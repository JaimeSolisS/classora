"""The role-scoped schedules (#67 B, C; ADR-0005 D2, D9; spec §2, W1.4).

Read-only: no lock, no audit event. Both reads use the #57 range rules
(:func:`~app.modules.scheduling.calendar_read.validate_range`: offset
instants, ``to`` after ``from``, at most 45 days) and the one range query
(:func:`~app.modules.scheduling.calendar_read.in_range`, at most 1000 items
then ``truncated``) with their scope predicate:

- **tutor** (:func:`tutor_schedule`): the sessions whose **current** tutor
  is ``user_id`` (after a #55 substitution the original tutor no longer
  sees it) and that are tutor-visible (:func:`tutor_visible_condition`).
  At most 6 statements: the target's account (staff only), sessions,
  participants, students, subjects, classrooms.
- **family** (:func:`family_schedule`): the sessions the student takes part
  in (``class_session_participants``; group and course sessions too) that
  are family-visible (:func:`family_visible_condition`). At most 8
  statements: the facts (one per family role), the student (with the
  birthdate), the zone (only for the adult rule), sessions, subjects,
  tutors, classrooms.
- **family students** (:func:`family_students`): the whose-schedule choices
  of a family caller. At most 4 statements.

The SQL visibility predicates are built from the same sets as the pure
rules ``states.is_family_visible`` / ``is_tutor_visible`` (a drift test
compares them).
"""

import uuid
from datetime import UTC
from typing import Final, cast

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.families.service import StudentAccessFacts
from app.modules.identity import service as identity
from app.modules.scheduling.calendar_read import (
    ITEM_LIMIT,
    has_participant,
    in_range,
    validate_range,
)
from app.modules.scheduling.errors import StudentConsentRequiredError
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.modules.scheduling.permissions import (
    ScheduleAccess,
    can_view_tutor_schedule,
    is_schedule_staff,
    needs_age,
    student_schedule_access,
)
from app.modules.scheduling.requests_policy import (
    candidate_student_ids,
    has_family_role,
    is_own_student,
)
from app.modules.scheduling.schedule_schemas import (
    FamilySchedule,
    FamilyScheduleClassroom,
    FamilyScheduleItem,
    FamilyScheduleState,
    FamilyScheduleStudent,
    FamilyScheduleStudents,
    FamilyScheduleTutor,
    ScheduleRangeQuery,
    ScheduleSubject,
    TutorSchedule,
    TutorScheduleClassroom,
    TutorScheduleItem,
    TutorScheduleParticipant,
    TutorScheduleState,
)
from app.modules.scheduling.states import (
    FAMILY_VISIBLE_STATES,
    ONCE_CONFIRMED_VISIBLE_STATES,
    TUTOR_VISIBLE_STATES,
    SessionKind,
    SessionState,
)

TUTOR_ROLE: Final = "tutor"


class _NoAssignments:
    """The family schedule never uses tutor assignments (a tutor who only
    teaches the student gets ``404``)."""

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        return frozenset()


_NO_ASSIGNMENTS: Final = _NoAssignments()
_NO_FACTS: Final = StudentAccessFacts()


def _values(states: frozenset[SessionState]) -> list[str]:
    return sorted(state.value for state in states)


def visibility_condition(
    always: frozenset[SessionState], once_confirmed: frozenset[SessionState]
) -> ColumnElement[bool]:
    """``state IN always OR (state IN once_confirmed AND first_confirmed_at
    IS NOT NULL)``."""
    return or_(
        ClassSession.state.in_(_values(always)),
        and_(
            ClassSession.state.in_(_values(once_confirmed)),
            ClassSession.first_confirmed_at.is_not(None),
        ),
    )


def family_visible_condition() -> ColumnElement[bool]:
    """The SQL form of ``states.is_family_visible`` (#67 A3)."""
    return visibility_condition(FAMILY_VISIBLE_STATES, ONCE_CONFIRMED_VISIBLE_STATES)


def tutor_visible_condition() -> ColumnElement[bool]:
    """The SQL form of ``states.is_tutor_visible`` (#67 A3)."""
    return visibility_condition(TUTOR_VISIBLE_STATES, ONCE_CONFIRMED_VISIBLE_STATES)


async def _subjects(
    session: AsyncSession, rows: list[ClassSession]
) -> dict[uuid.UUID, ScheduleSubject]:
    found = await catalog.subject_references(session, {row.subject_id for row in rows})
    return {
        subject_id: ScheduleSubject(
            id=ref.id, name=ref.name, education_level_name=ref.education_level_name
        )
        for subject_id, ref in found.items()
    }


async def _sessions(
    session: AsyncSession, query: ScheduleRangeQuery, *scope: ColumnElement[bool]
) -> tuple[list[ClassSession], bool]:
    found = list(
        await session.scalars(
            in_range(select(ClassSession), query.from_, query.to, *scope)
        )
    )
    return found[:ITEM_LIMIT], len(found) > ITEM_LIMIT


# --- B: the tutor's schedule ------------------------------------------------------


async def _ensure_tutor(
    session: AsyncSession, principal: Principal, tutor_user_id: uuid.UUID
) -> None:
    """``404`` unless the caller may see the schedule and ``tutor_user_id``
    is a user holding the ``tutor`` role (B1). The caller's own roles need
    no statement."""
    if not can_view_tutor_schedule(principal, tutor_user_id):
        raise RecordNotFoundError()
    if principal.user_id == tutor_user_id:
        roles = principal.roles
    else:
        account = await identity.get_account_reference(session, tutor_user_id)
        roles = account.roles if account is not None else frozenset()
    if TUTOR_ROLE not in roles:
        raise RecordNotFoundError()


async def tutor_schedule(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    query: ScheduleRangeQuery,
) -> TutorSchedule:
    """``GET /api/v1/tutors/{user_id}/schedule``: see the module docstring."""
    validate_range(query.from_, query.to)
    await _ensure_tutor(session, principal, tutor_user_id)
    rows, truncated = await _sessions(
        session,
        query,
        ClassSession.tutor_user_id == tutor_user_id,
        tutor_visible_condition(),
    )
    if not rows:
        return TutorSchedule(items=[], truncated=False)

    students_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for class_session_id, student_id in await session.execute(
        select(
            ClassSessionParticipant.class_session_id,
            ClassSessionParticipant.student_id,
        ).where(ClassSessionParticipant.class_session_id.in_([row.id for row in rows]))
    ):
        students_of.setdefault(class_session_id, []).append(student_id)
    students = await families.student_references(
        session, {sid for ids in students_of.values() for sid in ids}
    )
    subjects = await _subjects(session, rows)
    rooms = await catalog.classroom_references(
        session, {row.classroom_id for row in rows if row.classroom_id is not None}
    )

    items = []
    for row in rows:
        room = rooms.get(row.classroom_id) if row.classroom_id else None
        participants = sorted(
            (
                TutorScheduleParticipant(
                    student_id=students[sid].id,
                    display_name=students[sid].display_name,
                )
                for sid in students_of.get(row.id, [])
                if sid in students
            ),
            key=lambda p: (p.display_name.casefold(), str(p.student_id)),
        )
        items.append(
            TutorScheduleItem(
                id=row.id,
                kind=SessionKind(row.kind).value,
                state=cast(TutorScheduleState, row.state),
                starts_at=row.starts_at.astimezone(UTC),
                ends_at=row.ends_at.astimezone(UTC),
                subject=subjects[row.subject_id],
                classroom=(
                    None
                    if room is None
                    else TutorScheduleClassroom(id=room.id, name=room.name)
                ),
                participants=participants,
            )
        )
    return TutorSchedule(items=items, truncated=truncated)


# --- C: the family schedule -------------------------------------------------------


async def _facts(session: AsyncSession, principal: Principal) -> StudentAccessFacts:
    """The caller's links, loaded on every request (none without a family
    role)."""
    if not has_family_role(principal):
        return _NO_FACTS
    return await families.load_access_facts(session, principal, _NO_ASSIGNMENTS)


async def _ensure_student_schedule(
    session: AsyncSession, principal: Principal, student_id: uuid.UUID
) -> None:
    """``404`` (hidden or unknown) or ``403 student_consent_required`` (a
    linked guardian of an adult without the consent) unless the caller may
    see the student's schedule (C1). The pure policy
    (``permissions.student_schedule_access``) decides; an unknown id is the
    same ``404``."""
    if is_schedule_staff(principal):
        if not await families.student_references(session, [student_id]):
            raise RecordNotFoundError()
        return
    facts = await _facts(session, principal)
    student = (await families.student_age_references(session, [student_id])).get(
        student_id
    )
    if student is None:
        raise RecordNotFoundError()
    minor = (
        families.is_minor(
            student.birthdate,
            clock.today_in(await administration.get_center_time_zone(session)),
        )
        if needs_age(principal, student_id, facts)
        else None
    )
    access = student_schedule_access(principal, student_id, facts, is_minor=minor)
    if access is ScheduleAccess.CONSENT_REQUIRED:
        raise StudentConsentRequiredError()
    if access is not ScheduleAccess.ALLOWED:
        raise RecordNotFoundError()


async def family_schedule(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    query: ScheduleRangeQuery,
) -> FamilySchedule:
    """``GET /api/v1/students/{student_id}/schedule``: see the module
    docstring."""
    validate_range(query.from_, query.to)
    await _ensure_student_schedule(session, principal, student_id)
    rows, truncated = await _sessions(
        session, query, has_participant(student_id), family_visible_condition()
    )
    if not rows:
        return FamilySchedule(student_id=student_id, items=[], truncated=False)

    subjects = await _subjects(session, rows)
    tutors = await identity.get_account_references(
        session, {row.tutor_user_id for row in rows if row.tutor_user_id is not None}
    )
    rooms = await catalog.classroom_references(
        session, {row.classroom_id for row in rows if row.classroom_id is not None}
    )
    items = []
    for row in rows:
        tutor = tutors.get(row.tutor_user_id) if row.tutor_user_id else None
        room = rooms.get(row.classroom_id) if row.classroom_id else None
        items.append(
            FamilyScheduleItem(
                id=row.id,
                kind=SessionKind(row.kind).value,
                state=cast(FamilyScheduleState, row.state),
                starts_at=row.starts_at.astimezone(UTC),
                ends_at=row.ends_at.astimezone(UTC),
                subject=subjects[row.subject_id],
                tutor=(
                    None
                    if tutor is None
                    else FamilyScheduleTutor(display_name=tutor.display_name)
                ),
                classroom=(
                    None if room is None else FamilyScheduleClassroom(name=room.name)
                ),
            )
        )
    return FamilySchedule(student_id=student_id, items=items, truncated=truncated)


async def family_students(
    session: AsyncSession, principal: Principal
) -> FamilyScheduleStudents:
    """``GET /api/v1/schedule/family-students`` (C4): the caller's own
    profile (``student`` role) and the students linked to their active
    guardian profile (``guardian`` role), by display name. A caller who is
    also a tutor or staff gets only these, never assigned or all students;
    a caller with neither role gets none (no statement)."""
    facts = await _facts(session, principal)
    wanted = candidate_student_ids(principal, facts)
    if not wanted:
        return FamilyScheduleStudents(items=[])
    refs = await families.student_age_references(session, wanted)
    if not refs:  # pragma: no cover - a link or a login implies the row
        return FamilyScheduleStudents(items=[])
    today = clock.today_in(await administration.get_center_time_zone(session))
    items = []
    for student_id, ref in refs.items():
        own = is_own_student(principal, student_id, facts)
        minor = families.is_minor(ref.birthdate, today)
        allowed = own or families.guardian_has_academic_access(
            principal, student_id, is_minor=minor, facts=facts
        )
        items.append(
            FamilyScheduleStudent(
                student_id=student_id,
                display_name=ref.display_name,
                relation="self" if own else "guardian",
                is_minor=minor,
                schedule_access="allowed" if allowed else "consent_required",
            )
        )
    items.sort(key=lambda item: (item.display_name.casefold(), str(item.student_id)))
    return FamilyScheduleStudents(items=items)
