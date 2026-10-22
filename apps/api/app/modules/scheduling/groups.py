"""Ongoing tutoring groups: create, rename, enroll and withdraw (#56 C-F;
spec §1, §3, §6, W1.5; ADR-0005 D1, D2, D7, D9).

A group (``class_groups``) has a name, a subject (its level follows) and a
maximum size; its tutor, room and weekly rule live on its ``group`` series
(``class_series.group_id``), whose occurrences are the group's sessions
(``kind = 'group'``, materialized by the #53 code with every enrolled
student as a participant). The routes call the workflow
(``app.workflows.group_enrollment``), which calls these functions.

**Capacity (ADR-0005 D7, ISSUE-SC3).** Effective capacity = ``min(max_students,
room capacity)``. The group maximum is checked against the **active
enrollments** (soft ``group_capacity_exceeded``, overridable with
``schedule.override`` and a reason); the room capacity against each future
occupying occurrence's participant count (hard ``room_capacity_exceeded``,
never overridable), because one occurrence may use another room. The
nightly job never re-checks the maximum.

**Mid-term changes (B3, W1.5).** An enrollment or a withdrawal takes effect
at the request instant: it changes the group's occurrences that start after
now in ``unassigned``, ``offered``, ``confirmed``, ``conflict`` or
``skipped_closure`` (:mod:`~app.modules.scheduling.groups_rules`), plus the
participants of every **producing** series of the group
(``last_occurrence_on`` null or on/after the local today) so later
materializations include (or leave out) the student. Past, started,
``completed`` and ``cancelled`` occurrences keep their participants. Only
the occupying ones (``unassigned``, ``offered``, ``confirmed``) are checked
(student overlap, room capacity). A withdrawal never fails a check and
changes no session state, even when an occurrence is left empty.

**Lock order (F1)**, every writer: ``lock_students`` → the zone ``FOR
SHARE`` → **the group row ``FOR UPDATE``** → the producing series ``FOR
UPDATE`` by id → the affected occurrences ``FOR UPDATE`` by id
(:mod:`~app.modules.scheduling.group_lock`). The group row serializes the
enrollments of a group (the last seat), the partial unique index
``uq_group_enrollments_active`` backs up a duplicate. Every enrollment
change bumps the ``version`` of the series and occurrences whose
participants it changes (their version covers their participants), so a
series edit or ``PATCH`` working from an older read gets ``409
version_conflict``.

**The billing hook (E2/E3).** ``group_enrollments`` holds one row per
enrollment period (write-once withdrawal, never deleted). Each change
returns a :class:`GroupEnrollmentChange` for the workflow, where M6-07 adds
its charge step in the same Unit of Work; its sweeper reads the periods
with :func:`list_group_enrollment_periods`.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.booking import REFERENCE_MESSAGES
from app.modules.scheduling.checks import student_overlap_ids
from app.modules.scheduling.conflicts import STUDENT_OVERLAP, Conflict, sort_conflicts
from app.modules.scheduling.errors import (
    AlreadyEnrolledError,
    EnrollmentNotActiveError,
    EnrollmentNotFoundError,
    GroupEndedError,
    GroupNameTakenError,
    GroupNotFoundError,
    SchedulingConflictError,
    SessionValidationError,
)
from app.modules.scheduling.group_lock import lock_group
from app.modules.scheduling.groups_rules import (
    AFFECTED_STATES,
    GroupOccurrence,
    affected_occurrences,
    group_capacity_conflicts,
    is_checked,
    room_capacity_conflicts,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.materialize import materialize_series
from app.modules.scheduling.models import (
    GROUP_ENROLLMENT_ACTIVE_UNIQUE,
    GROUP_NAME_UNIQUE,
    SERIES_ASSIGNMENT_PENDING,
    ClassGroup,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
    GroupEnrollment,
)
from app.modules.scheduling.overrides import (
    evaluate_override,
    record_enrollment_override,
    record_series_overrides,
)
from app.modules.scheduling.recurrence import build_rrule, last_occurrence_on
from app.modules.scheduling.schemas import (
    GroupCreate,
    GroupEnrollmentCreate,
    GroupUpdate,
    GroupWithdrawal,
)
from app.modules.scheduling.series import check_series_rule
from app.modules.scheduling.series_offers import (
    offer_new_series,
    validate_create_respond_by,
)
from app.modules.scheduling.states import SessionKind

TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"

ChangeKind = Literal["enrolled", "withdrawn"]


@dataclass(frozen=True)
class GroupEnrollmentChange:
    """One enrollment change (#56 E3): what M6-07's charge step needs.
    ``effective_at`` is the enrollment's ``enrolled_at`` or its
    ``withdrawn_at``."""

    enrollment_id: uuid.UUID
    group_id: uuid.UUID
    student_id: uuid.UUID
    change: ChangeKind
    effective_at: datetime
    actor_user_id: uuid.UUID


@dataclass(frozen=True)
class GroupCreation:
    """A created group and the ``enrolled`` change of each initial student."""

    group_id: uuid.UUID
    changes: tuple[GroupEnrollmentChange, ...]


@dataclass(frozen=True)
class GroupEnrollmentPeriod:
    """One enrollment period for the M6-07 sweeper (#56 E3).
    ``changed_at`` is its latest change (``withdrawn_at``, else
    ``enrolled_at``)."""

    enrollment_id: uuid.UUID
    group_id: uuid.UUID
    student_id: uuid.UUID
    enrolled_at: datetime
    enrolled_by_user_id: uuid.UUID
    withdrawn_at: datetime | None
    withdrawn_by_user_id: uuid.UUID | None
    changed_at: datetime


# --- Shared steps --------------------------------------------------------------------


def _error(path: list[str | int], code: str, message: str) -> FieldError:
    return {"path": path, "code": code, "message": message}


def _reference_error(path: list[str | int], code: str) -> FieldError:
    return _error(path, code, REFERENCE_MESSAGES[code])


async def _check_students(
    session: AsyncSession, student_ids: Sequence[uuid.UUID], path: list[str | int]
) -> list[FieldError]:
    found = await families.student_references(session, student_ids)
    errors: list[FieldError] = []
    for index, student_id in enumerate(student_ids):
        at = [*path, index] if len(path) > 1 and path[-1] == "student_ids" else path
        student = found.get(student_id)
        if student is None:
            errors.append(_reference_error(at, "student_unknown"))
        elif not student.is_active:
            errors.append(_reference_error(at, "student_inactive"))
    return errors


async def _check_create_references(session: AsyncSession, body: GroupCreate) -> None:
    """D2 step 5: every invalid reference in one ``422`` (the #48 codes; the
    students with their index)."""
    errors = await _check_students(session, body.student_ids, ["body", "student_ids"])
    subject = await catalog.get_subject_for_reference(
        session, body.subject_id, lock=True
    )
    if subject is None:
        errors.append(_reference_error(["body", "subject_id"], "subject_unknown"))
    elif not (subject.is_active and subject.education_level_is_active):
        errors.append(_reference_error(["body", "subject_id"], "subject_inactive"))
    if body.tutor_user_id is not None:
        tutor = await identity.get_role_member(session, TUTOR_ROLE, body.tutor_user_id)
        if tutor is None:
            errors.append(_reference_error(["body", "tutor_user_id"], "tutor_unknown"))
        elif tutor.state != ACTIVE:
            errors.append(_reference_error(["body", "tutor_user_id"], "tutor_inactive"))
    if body.classroom_id is not None:
        room = (await catalog.classroom_references(session, [body.classroom_id])).get(
            body.classroom_id
        )
        if room is None:
            errors.append(
                _reference_error(["body", "classroom_id"], "classroom_unknown")
            )
        elif not room.is_active:
            errors.append(
                _reference_error(["body", "classroom_id"], "classroom_inactive")
            )
    if errors:
        raise SessionValidationError(errors)


async def _ensure_name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    query = select(ClassGroup.id).where(func.lower(ClassGroup.name) == name.lower())
    if except_id is not None:
        query = query.where(ClassGroup.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise GroupNameTakenError()


async def _flush_group(session: AsyncSession) -> None:
    """Flush; a concurrent request that took the name after the pre-check is
    ``409 group_name_taken``."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if GROUP_NAME_UNIQUE in str(exc.orig):
            raise GroupNameTakenError() from None
        raise


async def lock_group_or_404(session: AsyncSession, group_id: uuid.UUID) -> ClassGroup:
    """The group row ``FOR UPDATE``; ``404 group_not_found`` when unknown."""
    group = await lock_group(session, group_id)
    if group is None:
        raise GroupNotFoundError()
    return group


def producing_condition(today: date) -> ColumnElement[bool]:
    """A series that may still produce occurrences on or after ``today``."""
    return or_(
        ClassSeries.last_occurrence_on.is_(None),
        ClassSeries.last_occurrence_on >= today,
    )


async def _lock_producing_series(
    session: AsyncSession, group_id: uuid.UUID, today: date
) -> list[ClassSeries]:
    return list(
        await session.scalars(
            select(ClassSeries)
            .where(ClassSeries.group_id == group_id, producing_condition(today))
            .order_by(ClassSeries.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


async def _lock_affected_rows(
    session: AsyncSession, group_id: uuid.UUID, now: datetime
) -> list[ClassSession]:
    """The group's B3 occurrences ``FOR UPDATE``, by id (then ordered by
    start)."""
    candidates = list(
        await session.scalars(
            select(ClassSession)
            .where(
                ClassSession.group_id == group_id,
                ClassSession.starts_at > now,
                ClassSession.state.in_(sorted(AFFECTED_STATES)),
            )
            .order_by(ClassSession.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    rows = [row for row in candidates if row.starts_at > now]
    kept = {o.class_session_id for o in affected_occurrences(_facts(rows), now)}
    return sorted(
        (row for row in rows if row.id in kept), key=lambda r: (r.starts_at, r.id)
    )


def _facts(
    rows: Sequence[ClassSession],
    zone: str = "UTC",
    capacities: dict[uuid.UUID, int] | None = None,
) -> list[GroupOccurrence]:
    tz = ZoneInfo(zone)
    caps = capacities or {}
    return [
        GroupOccurrence(
            class_session_id=row.id,
            state=row.state,
            starts_at=row.starts_at,
            local_date=row.starts_at.astimezone(tz).date(),
            classroom_id=row.classroom_id,
            room_capacity=(
                None if row.classroom_id is None else caps.get(row.classroom_id)
            ),
        )
        for row in rows
    ]


async def _participants_of(
    session: AsyncSession, row_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    found: dict[uuid.UUID, set[uuid.UUID]] = {row_id: set() for row_id in row_ids}
    if not row_ids:
        return found
    for session_id, student_id in await session.execute(
        select(
            ClassSessionParticipant.class_session_id,
            ClassSessionParticipant.student_id,
        ).where(ClassSessionParticipant.class_session_id.in_(row_ids))
    ):
        found[session_id].add(student_id)
    return found


async def _active_enrollments(
    session: AsyncSession, group_id: uuid.UUID
) -> list[GroupEnrollment]:
    return list(
        await session.scalars(
            select(GroupEnrollment).where(
                GroupEnrollment.group_id == group_id,
                GroupEnrollment.withdrawn_at.is_(None),
            )
        )
    )


async def _bump(session: AsyncSession, records: Sequence[object]) -> None:
    """One versioned ``UPDATE`` per record (only ``updated_at`` and
    ``version`` change, which the audit leaves out): their version covers
    their participants."""
    for record in records:
        record.updated_at = func.now()  # type: ignore[attr-defined]
    await session.flush()
    for record in records:
        await session.refresh(record, ["updated_at"])


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


# --- D. Create -----------------------------------------------------------------------


async def create_group(
    session: AsyncSession,
    body: GroupCreate,
    *,
    horizon_weeks: int,
    actor_id: uuid.UUID,
) -> GroupCreation:
    """``POST /api/v1/groups`` (D2): the #53 series create with ``kind =
    'group'``, every initial student as a participant and an enrollment.
    After the route's ``401``/``403`` and the body's ``422``: student locks
    → zone ``FOR SHARE`` → ``422`` recurrence / ``starts_on_in_past`` /
    ``crosses_local_date`` → ``422`` references → ``409 group_name_taken``
    → the group-level conflict and every occurrence's conflicts in one
    ``409 scheduling_conflict`` (group level first) unless the ``override``
    covers them → insert → the overrides → the series offer with a
    tutor."""
    student_ids = list(body.student_ids)
    await lock_students(session, student_ids)
    zone = await administration.get_center_time_zone(session, for_share=True)
    fields = check_series_rule(body, zone)
    await _check_create_references(session, body)
    await _ensure_name_free(session, body.name)
    now = clock.now()

    group = ClassGroup(
        id=uuid.uuid7(),
        name=body.name,
        subject_id=body.subject_id,
        max_students=body.max_students,
    )
    group_conflicts = group_capacity_conflicts(
        len(student_ids), max_students=body.max_students, group_id=group.id
    )
    session.add(group)
    await _flush_group(session)
    series = ClassSeries(
        kind=SessionKind.GROUP.value,
        group_id=group.id,
        subject_id=body.subject_id,
        tutor_user_id=body.tutor_user_id,
        classroom_id=body.classroom_id,
        starts_on=body.starts_on,
        start_time=body.start_time,
        duration_minutes=body.duration_minutes,
        time_zone=zone,
        weekdays=sorted(fields.weekdays),
        interval=fields.interval,
        until_on=fields.until_on,
        count=fields.count,
        rrule=build_rrule(fields),
        last_occurrence_on=last_occurrence_on(fields, body.starts_on),
        assignment_status=(
            None if body.tutor_user_id is None else SERIES_ASSIGNMENT_PENDING
        ),
        assignment_from=None if body.tutor_user_id is None else body.starts_on,
    )
    if body.tutor_user_id is not None:
        await validate_create_respond_by(
            session, series, offered_at=now, requested=None
        )
    session.add(series)
    await session.flush()
    enrollments = [
        GroupEnrollment(
            id=uuid.uuid7(),
            group_id=group.id,
            student_id=student_id,
            enrolled_at=now,
            enrolled_by_user_id=actor_id,
        )
        for student_id in student_ids
    ]
    session.add_all(
        ClassSeriesParticipant(class_series_id=series.id, student_id=student_id)
        for student_id in student_ids
    )
    session.add_all(enrollments)
    await session.flush()

    counts = await materialize_series(
        session,
        series.id,
        now=now,
        horizon_weeks=horizon_weeks,
        mode="create",
        override=body.override,
        group_conflicts=group_conflicts,
        group_codes=None if body.override is None else body.override.codes,
    )
    if body.override is not None:
        if counts.override is not None:
            await record_series_overrides(
                session,
                class_series_id=series.id,
                decision=counts.override,
                reason=body.override.reason,
                write="create",
                actor_id=actor_id,
            )
        if counts.group_override is not None and counts.group_override.overridden:
            # The students above the maximum, in the body's order.
            for enrollment in enrollments[body.max_students :]:
                await record_enrollment_override(
                    session,
                    group_enrollment_id=enrollment.id,
                    conflicts=counts.group_override.overridden,
                    reason=body.override.reason,
                    actor_id=actor_id,
                )
    if body.tutor_user_id is not None:
        await offer_new_series(
            session,
            series,
            offered_at=now,
            requested_respond_by=None,
            actor_id=actor_id,
        )
    return GroupCreation(
        group_id=group.id,
        changes=tuple(
            GroupEnrollmentChange(
                enrollment_id=enrollment.id,
                group_id=group.id,
                student_id=enrollment.student_id,
                change="enrolled",
                effective_at=now,
                actor_user_id=actor_id,
            )
            for enrollment in enrollments
        ),
    )


# --- E4. PATCH -----------------------------------------------------------------------


async def update_group(
    session: AsyncSession, group_id: uuid.UUID, body: GroupUpdate
) -> None:
    """``PATCH /api/v1/groups/{id}`` (E4): group ``FOR UPDATE`` → ``404`` →
    ``409 version_conflict`` → ``422 max_students_below_enrolled`` → ``409
    group_name_taken``. Raising the maximum above the room capacity is
    allowed (the effective capacity stays the room's)."""
    group = await lock_group_or_404(session, group_id)
    ensure_version(group, body.version)
    if body.max_students is not None and body.max_students != group.max_students:
        active = len(await _active_enrollments(session, group.id))
        if body.max_students < active:
            raise SessionValidationError(
                [
                    _error(
                        ["body", "max_students"],
                        "max_students_below_enrolled",
                        f"The group has {active} enrolled students.",
                    )
                ]
            )
        group.max_students = body.max_students
    if body.name is not None and body.name != group.name:
        if body.name.lower() != group.name.lower():
            await _ensure_name_free(session, body.name, except_id=group.id)
        group.name = body.name
    await _flush_group(session)


# --- E1. Enroll ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Plan:
    rows: list[ClassSession]
    facts: dict[uuid.UUID, GroupOccurrence]
    participants: dict[uuid.UUID, set[uuid.UUID]]


async def _plan(
    session: AsyncSession, group_id: uuid.UUID, now: datetime, zone: str
) -> _Plan:
    rows = await _lock_affected_rows(session, group_id, now)
    rooms = {row.classroom_id for row in rows if row.classroom_id is not None}
    capacities = {
        room_id: room.capacity
        for room_id, room in (
            await catalog.classroom_capacities(session, rooms)
        ).items()
    }
    facts = {fact.class_session_id: fact for fact in _facts(rows, zone, capacities)}
    participants = await _participants_of(session, [row.id for row in rows])
    return _Plan(rows=rows, facts=facts, participants=participants)


async def _enrollment_conflicts(
    session: AsyncSession,
    group: ClassGroup,
    plan: _Plan,
    *,
    student_id: uuid.UUID,
    active_count: int,
    zone: str,
) -> list[Conflict]:
    """E1 step 9: the group-level conflict first, then each checked
    occurrence's (by start, then the rule order)."""
    conflicts = group_capacity_conflicts(
        active_count + 1, max_students=group.max_students, group_id=group.id
    )
    for row in plan.rows:
        if not is_checked(row.state) or student_id in plan.participants[row.id]:
            continue
        fact = plan.facts[row.id]
        found: list[Conflict] = []
        overlapping = await student_overlap_ids(
            session,
            student_id,
            row.starts_at,
            row.ends_at,
            zone=zone,
            exclude_session_ids=frozenset({row.id}),
        )
        if overlapping:
            found.append(
                Conflict(
                    STUDENT_OVERLAP,
                    {
                        "student_id": str(student_id),
                        "class_session_ids": overlapping,
                        "occurrence_start": _iso_z(row.starts_at),
                        "date": fact.local_date.isoformat(),
                    },
                )
            )
        found += room_capacity_conflicts([(fact, len(plan.participants[row.id]) + 1)])
        conflicts += sort_conflicts(found)
    return conflicts


async def _check_enrolled_student(session: AsyncSession, student_id: uuid.UUID) -> None:
    errors = await _check_students(session, [student_id], ["body", "student_id"])
    if errors:
        raise SessionValidationError(errors)


async def enroll_in_group(
    session: AsyncSession,
    group_id: uuid.UUID,
    body: GroupEnrollmentCreate,
    *,
    actor_id: uuid.UUID,
) -> GroupEnrollmentChange:
    """``POST /api/v1/groups/{id}/enrollments`` (E1). After the route's
    ``403``s and the body's ``422``: student lock → zone ``FOR SHARE`` →
    group ``FOR UPDATE`` (``404 group_not_found``) → producing series ``FOR
    UPDATE`` (none: ``409 group_ended``) → ``422`` student → ``409
    already_enrolled`` → the conflicts (``409 scheduling_conflict`` unless
    an ``override`` covers every one; hard ones never) → the enrollment, the
    series participants and the B3 occurrences' participants → the
    override row and ``scheduling.enrollment_override``."""
    student_id = body.student_id
    await lock_students(session, [student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    group = await lock_group_or_404(session, group_id)
    now = clock.now()
    producing = await _lock_producing_series(
        session, group.id, now.astimezone(ZoneInfo(zone)).date()
    )
    if not producing:
        raise GroupEndedError()
    await _check_enrolled_student(session, student_id)
    active = await _active_enrollments(session, group.id)
    current = next((e for e in active if e.student_id == student_id), None)
    if current is not None:
        raise AlreadyEnrolledError(str(current.id))

    plan = await _plan(session, group.id, now, zone)
    conflicts = await _enrollment_conflicts(
        session, group, plan, student_id=student_id, active_count=len(active), zone=zone
    )
    decision = evaluate_override(conflicts, body.override)
    if not decision.allowed:
        raise SchedulingConflictError([c.as_wire() for c in conflicts])

    enrollment = GroupEnrollment(
        id=uuid.uuid7(),
        group_id=group.id,
        student_id=student_id,
        enrolled_at=now,
        enrolled_by_user_id=actor_id,
    )
    session.add(enrollment)
    try:
        await session.flush()
    except IntegrityError as exc:
        if GROUP_ENROLLMENT_ACTIVE_UNIQUE in str(exc.orig):
            raise AlreadyEnrolledError(None) from None
        raise
    session.add_all(
        ClassSeriesParticipant(class_series_id=series.id, student_id=student_id)
        for series in producing
    )
    joined = [row for row in plan.rows if student_id not in plan.participants[row.id]]
    session.add_all(
        ClassSessionParticipant(class_session_id=row.id, student_id=student_id)
        for row in joined
    )
    await session.flush()
    await _bump(session, [*producing, *joined])
    if decision.overridden:
        assert body.override is not None  # noqa: S101 - overridden needs one
        await record_enrollment_override(
            session,
            group_enrollment_id=enrollment.id,
            conflicts=decision.overridden,
            reason=body.override.reason,
            actor_id=actor_id,
        )
    return GroupEnrollmentChange(
        enrollment_id=enrollment.id,
        group_id=group.id,
        student_id=student_id,
        change="enrolled",
        effective_at=now,
        actor_user_id=actor_id,
    )


# --- E2. Withdraw --------------------------------------------------------------------


async def withdraw_from_group(
    session: AsyncSession,
    group_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    body: GroupWithdrawal,
    *,
    actor_id: uuid.UUID,
) -> GroupEnrollmentChange:
    """``POST /api/v1/groups/{id}/enrollments/{enrollment_id}/withdraw``
    (E2): the enrollment's student is read unlocked, then student lock →
    zone ``FOR SHARE`` → group ``FOR UPDATE`` (``404 group_not_found``) →
    producing series ``FOR UPDATE`` → the enrollment re-read ``FOR UPDATE``
    (``404 enrollment_not_found``) → ``409 version_conflict`` → ``409
    enrollment_not_active``. Then the withdrawal columns are set once, and
    the student leaves the producing series and the B3 occurrences (audited
    ``delete`` events). Never a conflict; no session changes state."""
    found = await session.get(GroupEnrollment, enrollment_id)
    if found is not None and found.group_id == group_id:
        await lock_students(session, [found.student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    group = await lock_group_or_404(session, group_id)
    now = clock.now()
    producing = await _lock_producing_series(
        session, group.id, now.astimezone(ZoneInfo(zone)).date()
    )
    enrollment = await session.scalar(
        select(GroupEnrollment)
        .where(GroupEnrollment.id == enrollment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if enrollment is None or enrollment.group_id != group.id:
        raise EnrollmentNotFoundError()
    ensure_version(enrollment, body.version)
    if enrollment.withdrawn_at is not None:
        raise EnrollmentNotActiveError()
    student_id = enrollment.student_id
    enrollment.withdrawn_at = now
    enrollment.withdrawn_by_user_id = actor_id
    enrollment.withdrawal_reason = body.reason
    await session.flush()

    series_ids = [series.id for series in producing]
    left_series: list[ClassSeries] = []
    if series_ids:
        members = list(
            await session.scalars(
                select(ClassSeriesParticipant).where(
                    ClassSeriesParticipant.class_series_id.in_(series_ids),
                    ClassSeriesParticipant.student_id == student_id,
                )
            )
        )
        by_id = {series.id: series for series in producing}
        left_series = [by_id[member.class_series_id] for member in members]
        for member in members:
            await session.delete(member)
    rows = await _lock_affected_rows(session, group.id, now)
    left_rows: list[ClassSession] = []
    if rows:
        by_row = {row.id: row for row in rows}
        participants = list(
            await session.scalars(
                select(ClassSessionParticipant).where(
                    ClassSessionParticipant.class_session_id.in_(list(by_row)),
                    ClassSessionParticipant.student_id == student_id,
                )
            )
        )
        left_rows = [by_row[p.class_session_id] for p in participants]
        for participant in participants:
            await session.delete(participant)
    await session.flush()
    await _bump(session, [*left_series, *left_rows])
    return GroupEnrollmentChange(
        enrollment_id=enrollment.id,
        group_id=group.id,
        student_id=student_id,
        change="withdrawn",
        effective_at=now,
        actor_user_id=actor_id,
    )


# --- E3. The sweeper's reader --------------------------------------------------------


async def list_group_enrollment_periods(
    session: AsyncSession, *, changed_since: datetime, limit: int
) -> list[GroupEnrollmentPeriod]:
    """The enrollment periods whose ``enrolled_at`` or ``withdrawn_at`` is at
    or after ``changed_since``, by their latest change then id, at most
    ``limit`` (one statement): the M6-07 sweeper's input (#56 E3)."""
    changed_at = func.coalesce(
        GroupEnrollment.withdrawn_at, GroupEnrollment.enrolled_at
    )
    rows = await session.execute(
        select(GroupEnrollment, changed_at.label("changed_at"))
        .where(
            or_(
                GroupEnrollment.enrolled_at >= changed_since,
                GroupEnrollment.withdrawn_at >= changed_since,
            )
        )
        .order_by(changed_at, GroupEnrollment.id)
        .limit(limit)
    )
    return [
        GroupEnrollmentPeriod(
            enrollment_id=row.id,
            group_id=row.group_id,
            student_id=row.student_id,
            enrolled_at=row.enrolled_at,
            enrolled_by_user_id=row.enrolled_by_user_id,
            withdrawn_at=row.withdrawn_at,
            withdrawn_by_user_id=row.withdrawn_by_user_id,
            changed_at=at,
        )
        for row, at in rows
    ]
