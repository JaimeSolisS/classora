"""One extra session of a group or a course cohort (#592; spec W1, §Class
types; ADR-0005 D1, D2, D7, D9).

``POST /api/v1/class-sessions`` with ``kind = "group"`` (``group_id``) or
``kind = "course"`` (``cohort_id``) creates **one** session that belongs to
the group or cohort but to no series (``series_id`` and ``original_start``
null, ``is_exception`` false). Its subject is the group's or the cohort's,
and its participants are the students **actively enrolled now**. The
individual create (:mod:`~app.modules.scheduling.booking`) is unchanged.

**Create** (:func:`create_owner_session`), in this order, after the route's
``401``/``403`` (an ``override`` without ``schedule.override`` included) and
the body's ``422``:

1. the group or cohort and its active enrollments, read unlocked: an unknown
   one is ``422 group_unknown`` on ``group_id`` (``cohort_unknown`` on
   ``cohort_id``);
2. ``lock_students`` for that roster (none when it is empty);
3. the center zone ``FOR SHARE``;
4. the group row (``group_lock.lock_group``) or the cohort row
   (``courses.service.lock_cohort``) ``FOR UPDATE``;
5. the active enrollments read again: a different set of students is ``409
   roster_changed`` (``details.group_id`` / ``details.cohort_id``). An
   enrollment holds its student's lock while it waits for the owner row, so
   the create can't take a new student's lock here without breaking the
   lock order; the client reads again and retries;
6. with a tutor, ``lock_tutor`` (#63 D5);
7. ``422`` for the bounds (``booking.validate_bounds``);
8. ``422`` for the references, collected in one response: the owner's
   subject (``subject_inactive`` on the owner field), the participants
   (``student_unknown`` / ``student_inactive`` on the owner field, once per
   code), the tutor and the classroom as for an individual session, and for
   a course ``session_outside_cohort_dates`` on ``starts_at`` when the
   session's local date is before the cohort's first or after its last
   session date (``course_series.cohort_schedules``);
9. ``respond_by`` as for an individual session (``respond_by_requires_tutor``,
   ``respond_by_invalid``);
10. no producing series (``series_participants.producing_condition`` at the
    local today): ``409 group_ended`` / ``409 cohort_ended``;
11. every conflict (``checks.collect_conflicts`` with every participant,
    ``center_closed`` checked, ``group_max=None``: the maximum was enforced
    or overridden by the enrollments, as for the nightly job, so
    ``group_capacity_exceeded`` never comes from here);
12. the override decision (``409 scheduling_conflict`` with every conflict,
    sorted, unless the ``override`` lists every one and all are soft), then
    with a tutor ``409 class_session_not_offerable`` when the start is not
    after now;
13. with a tutor, the workload warnings;
14. the session (``offered`` with a tutor, ``unassigned`` without one) and
    one participant per enrolled student (none for an empty roster); an
    ``EXCLUDE`` race is ``409 scheduling_conflict`` with that one conflict;
15. with overridden conflicts, the override row and its
    ``scheduling.session_override`` event;
16. with a tutor, the ``pending`` session-scope offer and the tutor's
    ``scheduling.offer_received`` notification.

**Lock order:** students → zone → group **or** cohort → tutor; the complete
order is in :mod:`~app.modules.scheduling.locks`. No writer waits for a group
or cohort row while it holds a tutor lock.

**Afterwards** the session is reached by everything that selects a group's or
cohort's sessions by ``group_id`` / ``cohort_id``: a later enrollment adds
its student, a withdrawal removes them (the #56 B3 rule,
:mod:`~app.modules.scheduling.series_participants`), and billing, payroll,
the reads and the calendar include it. The nightly materialization, a
closure added later and the series edits never touch it.

Audit: the automatic ``create`` events of every table written, plus the
override event. Runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import FieldError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.courses import service as courses
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.booking import (
    REFERENCE_MESSAGES,
    _detail,
    _override_item,
    _race_conflict,
    _respond_by,
    tutor_and_room_references,
    validate_bounds,
)
from app.modules.scheduling.checks import SlotCandidate, collect_conflicts
from app.modules.scheduling.course_series import cohort_schedules
from app.modules.scheduling.errors import (
    ClassSessionNotOfferableError,
    GroupEndedError,
    RosterChangedError,
    SchedulingConflictError,
    SessionOverlapError,
    SessionValidationError,
)
from app.modules.scheduling.group_lock import lock_group
from app.modules.scheduling.locks import lock_students, lock_tutor
from app.modules.scheduling.models import (
    ClassGroup,
    ClassSession,
    ClassSessionParticipant,
    GroupEnrollment,
)
from app.modules.scheduling.offer_records import insert_offer, notify_offer_received
from app.modules.scheduling.overrides import evaluate_override, record_session_override
from app.modules.scheduling.schemas import (
    ClassSessionCreated,
    CourseSessionCreate,
    GroupSessionCreate,
    PendingOffer,
    SessionClassroom,
    SessionOverrideItem,
    SessionParticipant,
    SessionSubject,
    SessionTutor,
)
from app.modules.scheduling.series_participants import SeriesOwner, producing_condition
from app.modules.scheduling.sessions import flush_class_sessions
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.times import scheduled_minutes
from app.modules.scheduling.workload_sessions import session_warnings

type OwnerSessionCreate = GroupSessionCreate | CourseSessionCreate
type OwnerField = Literal["group_id", "cohort_id"]


@dataclass(frozen=True)
class _Owner:
    """The group or cohort of the body."""

    series_owner: SeriesOwner
    field: OwnerField

    @classmethod
    def of(cls, body: OwnerSessionCreate) -> _Owner:
        if isinstance(body, GroupSessionCreate):
            return cls(SeriesOwner.group(body.group_id), "group_id")
        return cls(SeriesOwner.cohort(body.cohort_id), "cohort_id")

    @property
    def id(self) -> uuid.UUID:
        return self.series_owner.id

    @property
    def is_group(self) -> bool:
        return self.series_owner.kind == "group"


def _unknown(owner: _Owner) -> SessionValidationError:
    if owner.is_group:
        return SessionValidationError(
            [
                {
                    "path": ["body", "group_id"],
                    "code": "group_unknown",
                    "message": "Choose an existing group.",
                }
            ]
        )
    return SessionValidationError(
        [
            {
                "path": ["body", "cohort_id"],
                "code": "cohort_unknown",
                "message": "Choose an existing cohort.",
            }
        ]
    )


async def _roster(session: AsyncSession, owner: _Owner) -> frozenset[uuid.UUID]:
    """The students with an active enrollment in the owner (one statement)."""
    if owner.is_group:
        return frozenset(
            await session.scalars(
                select(GroupEnrollment.student_id).where(
                    GroupEnrollment.group_id == owner.id,
                    GroupEnrollment.withdrawn_at.is_(None),
                )
            )
        )
    return frozenset(await courses.active_cohort_enrollments(session, owner.id))


async def _exists(session: AsyncSession, owner: _Owner) -> bool:
    if owner.is_group:
        found = await session.scalar(select(exists().where(ClassGroup.id == owner.id)))
        return bool(found)
    return await courses.cohort_exists(session, owner.id)


async def _lock_owner(session: AsyncSession, owner: _Owner) -> uuid.UUID:
    """Step 4: the owner row ``FOR UPDATE``; its subject. Unknown (gone
    since step 1): the step 1 ``422``."""
    if owner.is_group:
        group = await lock_group(session, owner.id)
        if group is None:
            raise _unknown(owner)
        return group.subject_id
    cohort = await courses.lock_cohort(session, owner.id)
    if cohort is None:
        raise _unknown(owner)
    return cohort.subject_id


@dataclass(frozen=True)
class _References:
    subject: SessionSubject
    participants: list[SessionParticipant]
    tutor: SessionTutor | None
    classroom: SessionClassroom | None


async def _references(
    session: AsyncSession,
    body: OwnerSessionCreate,
    owner: _Owner,
    *,
    subject_id: uuid.UUID,
    roster: Collection[uuid.UUID],
    zone: str,
) -> _References:
    """Step 8: every invalid reference in one ``422``."""
    errors: list[FieldError] = []

    def reject(code: str) -> None:
        errors.append(
            {
                "path": ["body", owner.field],
                "code": code,
                "message": REFERENCE_MESSAGES[code],
            }
        )

    subject = await catalog.get_subject_for_reference(session, subject_id, lock=True)
    if subject is None:
        reject("subject_unknown")
    elif not (subject.is_active and subject.education_level_is_active):
        reject("subject_inactive")

    found = await families.student_references(session, roster)
    if any(student_id not in found for student_id in roster):
        reject("student_unknown")
    if any(not student.is_active for student in found.values()):
        reject("student_inactive")

    tutor, classroom = await tutor_and_room_references(
        session, body.tutor_user_id, body.classroom_id, errors
    )

    if isinstance(body, CourseSessionCreate):
        schedule = (
            await cohort_schedules(session, [body.cohort_id], now=clock.now())
        ).get(body.cohort_id)
        local_date = body.starts_at.astimezone(ZoneInfo(zone)).date()
        if schedule is not None and (
            (
                schedule.first_session_on is not None
                and local_date < schedule.first_session_on
            )
            or (
                schedule.last_session_on is not None
                and local_date > schedule.last_session_on
            )
        ):
            errors.append(
                {
                    "path": ["body", "starts_at"],
                    "code": "session_outside_cohort_dates",
                    "message": (
                        "The date must fall within the cohort's first and last"
                        " session dates."
                    ),
                }
            )

    if errors:
        raise SessionValidationError(errors)
    # Narrowing only: an unknown subject was rejected above.
    assert subject is not None  # noqa: S101
    return _References(
        subject=SessionSubject(
            id=subject.id,
            name=subject.name,
            education_level_name=subject.education_level_name,
            is_active=subject.is_active and subject.education_level_is_active,
        ),
        participants=[
            SessionParticipant(
                student_id=student.id,
                display_name=student.display_name,
                is_active=student.is_active,
            )
            for student in found.values()
        ],
        tutor=tutor,
        classroom=classroom,
    )


async def _ensure_producing(session: AsyncSession, owner: _Owner, zone: str) -> None:
    """Step 10: ``409 group_ended`` / ``cohort_ended`` when no series of the
    owner may still produce sessions on or after the local today."""
    producing = await session.scalar(
        select(
            exists().where(
                owner.series_owner.series_condition(),
                producing_condition(clock.today_in(zone)),
            )
        )
    )
    if producing:
        return
    if owner.is_group:
        raise GroupEndedError()
    raise courses.CohortEndedError()


async def create_owner_session(
    session: AsyncSession, body: OwnerSessionCreate, *, actor_id: uuid.UUID
) -> ClassSessionCreated:
    """``POST /api/v1/class-sessions`` for ``kind`` ``group`` or ``course``:
    see the module docstring for the order. ``actor_id`` is the signed-in
    user (the override's actor and the offer's sender); the route has
    already checked ``schedule.override`` for an ``override``."""
    owner = _Owner.of(body)
    if not await _exists(session, owner):
        raise _unknown(owner)
    roster = await _roster(session, owner)
    await lock_students(session, roster)
    zone = await administration.get_center_time_zone(session, for_share=True)
    subject_id = await _lock_owner(session, owner)
    if await _roster(session, owner) != roster:
        raise RosterChangedError(owner.field, str(owner.id))
    if body.tutor_user_id is not None:
        # #63 D5: serialized with a change of this tutor's availability.
        await lock_tutor(session, body.tutor_user_id)
    validate_bounds(body, zone)
    refs = await _references(
        session, body, owner, subject_id=subject_id, roster=roster, zone=zone
    )
    offered_at = clock.now()
    respond_by = await _respond_by(session, body, offered_at)
    await _ensure_producing(session, owner, zone)
    conflicts = await collect_conflicts(
        session,
        SlotCandidate(
            kind=body.kind,
            student_ids=tuple(sorted(roster)),
            subject_id=subject_id,
            tutor_user_id=body.tutor_user_id,
            classroom_id=body.classroom_id,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            group_max=None,
        ),
        zone=zone,
        now=offered_at,
        check_center_closed=True,
    )
    decision = evaluate_override(conflicts, body.override)
    if not decision.allowed:
        raise SchedulingConflictError([conflict.as_wire() for conflict in conflicts])
    if body.tutor_user_id is not None and body.starts_at <= offered_at:
        # #52: an offer can't be answered before a session that has begun.
        raise ClassSessionNotOfferableError(started=True)
    warnings = (
        []
        if body.tutor_user_id is None
        else await session_warnings(
            session, body.tutor_user_id, body.starts_at, body.ends_at, zone
        )
    )

    state = (
        SessionState.UNASSIGNED if body.tutor_user_id is None else SessionState.OFFERED
    )
    row = ClassSession(
        kind=body.kind,
        state=state.value,
        subject_id=subject_id,
        tutor_user_id=body.tutor_user_id,
        classroom_id=body.classroom_id,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        scheduled_minutes=scheduled_minutes(body.starts_at, body.ends_at),
        group_id=owner.id if owner.is_group else None,
        cohort_id=None if owner.is_group else owner.id,
    )
    session.add(row)
    try:
        await flush_class_sessions(session)
    except SessionOverlapError as exc:
        raise SchedulingConflictError(
            [_race_conflict(exc.code, body).as_wire()]
        ) from None
    if roster:
        session.add_all(
            ClassSessionParticipant(class_session_id=row.id, student_id=student_id)
            for student_id in sorted(roster)
        )
        await flush_class_sessions(session)
    overrides: list[SessionOverrideItem] = []
    if decision.overridden:
        # Only an allowed override reaches here, so body.override is set.
        assert body.override is not None  # noqa: S101
        stored = await record_session_override(
            session,
            row.id,
            decision.overridden,
            body.override.reason,
            actor_id=actor_id,
        )
        actors = await identity.get_account_references(session, [actor_id])
        overrides.append(_override_item(stored, actors))
    pending_offer = None
    if refs.tutor is not None and respond_by is not None:
        offer = await insert_offer(
            session,
            tutor_user_id=refs.tutor.id,
            offered_by_user_id=actor_id,
            offered_at=offered_at,
            respond_by=respond_by,
            class_session_ids=[row.id],
        )
        await notify_offer_received(
            session, offer, subject_name=refs.subject.name, starts_at=row.starts_at
        )
        pending_offer = PendingOffer(
            id=offer.id,
            scope="session",
            tutor=refs.tutor,
            respond_by=offer.respond_by.astimezone(UTC),
        )
    detail = _detail(
        row,
        subject=refs.subject,
        tutor=refs.tutor,
        classroom=refs.classroom,
        participants=refs.participants,
        overrides=overrides,
        pending_offer=pending_offer,
    )
    return ClassSessionCreated(**detail.model_dump(), warnings=warnings)
