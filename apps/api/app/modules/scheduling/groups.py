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
changes no session state, even when an occurrence is left empty. The
participant changes and the checks are the kind-neutral helpers of
:mod:`~app.modules.scheduling.series_participants` (#66 B1), shared with
cohort enrollment.

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
returns a :class:`GroupEnrollmentChange` for the workflow, where M6-07
(#92) posts the charges in the same Unit of Work; the periods it misses are
found by ``billing:post_enrollment_charges``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
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
from app.modules.scheduling.groups_rules import group_capacity_conflicts
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.materialize import materialize_series
from app.modules.scheduling.models import (
    GROUP_ENROLLMENT_ACTIVE_UNIQUE,
    GROUP_NAME_UNIQUE,
    SERIES_ASSIGNMENT_PENDING,
    ClassGroup,
    ClassSeries,
    ClassSeriesParticipant,
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
from app.modules.scheduling.series_participants import (
    SeriesOwner,
    add_series_participant,
    lock_producing_series,
    plan_series_participant_add,
    remove_series_participant,
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
    owner = SeriesOwner.group(group_id)
    await lock_students(session, [student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    group = await lock_group_or_404(session, group_id)
    now = clock.now()
    producing = await lock_producing_series(
        session, owner, now.astimezone(ZoneInfo(zone)).date()
    )
    if not producing:
        raise GroupEndedError()
    await _check_enrolled_student(session, student_id)
    active = await _active_enrollments(session, group.id)
    current = next((e for e in active if e.student_id == student_id), None)
    if current is not None:
        raise AlreadyEnrolledError(str(current.id))

    series_ids = [series.id for series in producing]
    conflicts = await plan_series_participant_add(
        session,
        series_ids=series_ids,
        student_id=student_id,
        max_students=group.max_students,
        active_count=len(active),
        now=now,
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
    await add_series_participant(
        session, series_ids=series_ids, student_id=student_id, now=now, owner=owner
    )
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
    owner = SeriesOwner.group(group_id)
    zone = await administration.get_center_time_zone(session, for_share=True)
    group = await lock_group_or_404(session, group_id)
    now = clock.now()
    producing = await lock_producing_series(
        session, owner, now.astimezone(ZoneInfo(zone)).date()
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

    await remove_series_participant(
        session,
        series_ids=[series.id for series in producing],
        student_id=student_id,
        now=now,
        owner=owner,
    )
    return GroupEnrollmentChange(
        enrollment_id=enrollment.id,
        group_id=group.id,
        student_id=student_id,
        change="withdrawn",
        effective_at=now,
        actor_user_id=actor_id,
    )


# --- Withdrawal settlement (#101 C2 step 3) ------------------------------------------


@dataclass(frozen=True)
class GroupEnrollmentState:
    """A group enrollment period's state for a withdrawal settlement (#101):
    its group, student, ``version`` and ``withdrawn_at`` (``None``:
    active)."""

    enrollment_id: uuid.UUID
    group_id: uuid.UUID
    student_id: uuid.UUID
    enrolled_at: datetime
    withdrawn_at: datetime | None
    version: int


def _state(enrollment: GroupEnrollment) -> GroupEnrollmentState:
    return GroupEnrollmentState(
        enrollment_id=enrollment.id,
        group_id=enrollment.group_id,
        student_id=enrollment.student_id,
        enrolled_at=enrollment.enrolled_at,
        withdrawn_at=enrollment.withdrawn_at,
        version=enrollment.version,
    )


async def group_enrollment_state(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> GroupEnrollmentState | None:
    """The period re-read without a lock (the withdrawal preview), or
    ``None`` when unknown. One statement."""
    enrollment = await session.scalar(
        select(GroupEnrollment)
        .where(GroupEnrollment.id == enrollment_id)
        .execution_options(populate_existing=True)
    )
    return None if enrollment is None else _state(enrollment)


async def lock_group_enrollment_for_settlement(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> GroupEnrollmentState | None:
    """The M4 withdrawal's lock sequence **without the change** (#101 C2
    step 3, S3): the enrollment's student read unlocked → student lock →
    zone ``FOR SHARE`` → group ``FOR UPDATE`` → producing series ``FOR
    UPDATE`` → the enrollment re-read ``FOR UPDATE``. ``None`` when the
    enrollment (or its group) is unknown. An active enrollment is then
    withdrawn through :func:`withdraw_from_group` in the same Unit of Work
    (it takes the same locks again, already held); an already withdrawn one
    is only settled. Never writes."""
    found = await session.get(GroupEnrollment, enrollment_id)
    if found is None:
        return None
    await lock_students(session, [found.student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    if await lock_group(session, found.group_id) is None:
        return None
    await lock_producing_series(
        session,
        SeriesOwner.group(found.group_id),
        clock.now().astimezone(ZoneInfo(zone)).date(),
    )
    enrollment = await session.scalar(
        select(GroupEnrollment)
        .where(GroupEnrollment.id == enrollment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return None if enrollment is None else _state(enrollment)
