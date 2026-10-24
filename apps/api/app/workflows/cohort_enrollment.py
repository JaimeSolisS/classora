"""Cohort enrollment and withdrawal: the billing hook of course cohorts (#66
C, D, H; spec §1 "only administrators … enroll/withdraw students", §3
"additions/removals after a start date", §6; architecture §4.1, §4.2,
ISSUE-F1, ISSUE-J1; ADR-0002, ADR-0005 D1, D2, D6, D7, D9).

The enrollment period (``cohort_enrollments``) belongs to ``courses``; the
participants of the cohort's series and sessions to ``scheduling``. These
functions compose ``courses.service`` and ``scheduling.service`` in the
caller's **one Unit of Work** and never commit. The cohort routes
(:mod:`app.workflows.cohorts_router`) call them, never the services
directly.

**Enroll** (:func:`enroll_in_cohort`), after the route's ``401``/``403``
(``schedule.manage``), the body's ``422`` and the ``override``'s ``403``
(``schedule.override``):

1. ``scheduling.service.lock_students`` (the student);
2. the center zone ``FOR SHARE``;
3. ``courses.service.lock_cohort`` (``404 cohort_not_found``);
4. the cohort's producing series ``FOR UPDATE`` by id (none: ``409
   cohort_ended``: its last session has started or passed);
5. ``422 student_unknown`` / ``student_inactive`` on ``student_id``;
6. ``409 already_enrolled`` (``details.enrollment_id``) for an active
   enrollment of the student;
7. the conflicts (``scheduling.service.plan_series_participant_add``): the
   soft ``group_capacity_exceeded`` (the active enrollments plus one
   against ``max_students``, ``details.cohort_id``), then, per not-started
   occupying occurrence, ``student_overlap`` and the hard
   ``room_capacity_exceeded``; the #49 decision: a hard conflict, or a soft
   one the ``override`` doesn't list, is ``409 scheduling_conflict`` with
   the complete list;
8. the period row (``courses.service.insert_cohort_enrollment``; a
   ``uq_cohort_enrollments_active`` race is ``409 already_enrolled``),
   then ``add_series_participant``: the producing series and every
   not-started occurrence in ``unassigned``, ``offered``, ``confirmed``,
   ``conflict`` or ``skipped_closure`` (the #56 B3 rule); later
   materializations include the student. Occurrences past the horizon are
   checked when the nightly run creates them (a clash there becomes a
   ``conflict`` row and an inbox item);
9. with an accepted override, the ``class_session_overrides`` row
   (``cohort_enrollment_id``) and ``scheduling.enrollment_override``.

**Withdraw** (:func:`withdraw_from_cohort`): the enrollment's student is
read unlocked, then ``lock_students`` → zone ``FOR SHARE`` →
``lock_cohort`` (``404 cohort_not_found``) → the producing series ``FOR
UPDATE`` → the enrollment re-read ``FOR UPDATE`` (``404
enrollment_not_found``: unknown or another cohort's) → ``409
version_conflict`` → ``409 enrollment_not_active``; then the withdrawal
columns are set once and the student leaves the producing series and every
not-started occurrence in those states (audited ``delete`` events). Past,
started, ``completed`` and ``cancelled`` occurrences keep the student
(W1.5). A withdrawal never fails a check and changes no session state,
even when an occurrence is left without participants; after the cohort has
ended it only closes the period.

**Lock order (H1, #65 G1):** students → zone → cohort ``FOR UPDATE`` →
series ``FOR UPDATE`` → sessions by id (the complete order is in
``scheduling.locks``). The cohort row serializes the enrollments of a
cohort (the last seat) and its ``PATCH`` of the maximum. ``courses`` takes
no scheduling lock: the workflow takes the student lock first.

**The billing contract (E3).** Each function returns a frozen
:class:`CohortEnrollmentChange` (``enrollment_id``, ``cohort_id``,
``student_id``, ``change`` ``enrolled`` | ``withdrawn``, ``effective_at``,
``actor_user_id``) and runs :func:`apply_cohort_enrollment_change` (a
no-op today) in the same Unit of Work:

- **M6-07 (#92)** adds its charge step there, so a failure rolls back the
  period, its participants, its override and its audit events together.
  Its sweeper finds the periods without charges through
  ``courses.service.list_cohort_enrollment_periods`` (ISSUE-J1). Course
  prices are not prorated for closures (ADR-0005 D6); proration for a late
  enrollment is M6-07's rule.
- **M6-16 (#101)** calls :func:`withdraw_from_cohort` from its own
  ``withdraw_enrollment`` workflow, in the same transaction as the refund
  or credit: nothing here commits.

There is no event bus and no job is enqueued. Enrollments and withdrawals
take effect at the request instant (no future-dated changes).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import FieldError
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.courses import service as courses
from app.modules.families import service as families
from app.modules.scheduling import service as scheduling
from app.workflows.cohort_schemas import CohortEnrollmentCreate, CohortWithdrawal

__all__ = [
    "CohortEnrollmentChange",
    "apply_cohort_enrollment_change",
    "enroll_in_cohort",
    "withdraw_from_cohort",
]

STUDENT_MESSAGES: Final = {
    "student_unknown": "Choose an existing student.",
    "student_inactive": "The student is inactive.",
}
"""The #48 reference messages."""

ChangeKind = Literal["enrolled", "withdrawn"]


@dataclass(frozen=True)
class CohortEnrollmentChange:
    """One cohort enrollment change (#66 D3): what M6-07's charge step and
    M6-16's withdrawal need. ``effective_at`` is the period's
    ``enrolled_at`` or its ``withdrawn_at``."""

    enrollment_id: uuid.UUID
    cohort_id: uuid.UUID
    student_id: uuid.UUID
    change: ChangeKind
    effective_at: datetime
    actor_user_id: uuid.UUID


async def apply_cohort_enrollment_change(
    session: AsyncSession, change: CohortEnrollmentChange
) -> None:
    """The cross-module steps of one change (none yet). M6-07 (#92) adds the
    charge (or proration) of the period here, in the same Unit of Work."""
    del session, change


async def _check_student(session: AsyncSession, student_id: uuid.UUID) -> None:
    student = (await families.student_references(session, [student_id])).get(student_id)
    code = None
    if student is None:
        code = "student_unknown"
    elif not student.is_active:
        code = "student_inactive"
    if code is not None:
        error: FieldError = {
            "path": ["body", "student_id"],
            "code": code,
            "message": STUDENT_MESSAGES[code],
        }
        raise scheduling.SessionValidationError([error])


async def _lock_cohort(session: AsyncSession, cohort_id: uuid.UUID) -> int:
    """``lock_cohort``; ``404 cohort_not_found``; the cohort's maximum."""
    cohort = await courses.lock_cohort(session, cohort_id)
    if cohort is None:
        raise courses.CohortNotFoundError()
    return cohort.max_students


async def enroll_in_cohort(
    uow: UnitOfWork,
    cohort_id: uuid.UUID,
    body: CohortEnrollmentCreate,
    *,
    actor_id: uuid.UUID,
) -> CohortEnrollmentChange:
    """``POST /api/v1/cohorts/{id}/enrollments``: see the module docstring.
    Never commits."""
    session = uow.session
    student_id = body.student_id
    owner = scheduling.SeriesOwner.cohort(cohort_id)
    await scheduling.lock_students(session, [student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    max_students = await _lock_cohort(session, cohort_id)
    now = clock.now()
    producing = await scheduling.lock_producing_series(
        session, owner, clock.today_in(zone)
    )
    if not producing:
        raise courses.CohortEndedError()
    await _check_student(session, student_id)
    active = await courses.active_cohort_enrollments(session, cohort_id)
    if student_id in active:
        raise scheduling.AlreadyEnrolledError(str(active[student_id]))

    series_ids = [series.id for series in producing]
    conflicts = await scheduling.plan_series_participant_add(
        session,
        series_ids=series_ids,
        student_id=student_id,
        max_students=max_students,
        active_count=len(active),
        now=now,
        owner=owner,
        zone=zone,
    )
    decision = scheduling.evaluate_override(conflicts, body.override)
    if not decision.allowed:
        raise scheduling.SchedulingConflictError([c.as_wire() for c in conflicts])

    try:
        enrollment_id = await courses.insert_cohort_enrollment(
            session,
            cohort_id=cohort_id,
            student_id=student_id,
            enrolled_at=now,
            actor_id=actor_id,
        )
    except courses.CohortEnrollmentActiveExists:
        raise scheduling.AlreadyEnrolledError(None) from None
    await scheduling.add_series_participant(
        session, series_ids=series_ids, student_id=student_id, now=now, owner=owner
    )
    if decision.overridden:
        assert body.override is not None  # noqa: S101 - overridden needs one
        await scheduling.record_enrollment_override(
            session,
            cohort_enrollment_id=enrollment_id,
            conflicts=decision.overridden,
            reason=body.override.reason,
            actor_id=actor_id,
        )
    change = CohortEnrollmentChange(
        enrollment_id=enrollment_id,
        cohort_id=cohort_id,
        student_id=student_id,
        change="enrolled",
        effective_at=now,
        actor_user_id=actor_id,
    )
    await apply_cohort_enrollment_change(session, change)
    return change


async def withdraw_from_cohort(
    uow: UnitOfWork,
    cohort_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    body: CohortWithdrawal,
    *,
    actor_id: uuid.UUID,
) -> CohortEnrollmentChange:
    """``POST /api/v1/cohorts/{id}/enrollments/{enrollment_id}/withdraw``:
    see the module docstring. Never commits (M6-16 calls it inside its own
    Unit of Work)."""
    session = uow.session
    owner = scheduling.SeriesOwner.cohort(cohort_id)
    found = await courses.find_cohort_enrollment(session, enrollment_id)
    if found is not None and found.cohort_id == cohort_id:
        await scheduling.lock_students(session, [found.student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)
    await _lock_cohort(session, cohort_id)
    now = clock.now()
    producing = await scheduling.lock_producing_series(
        session, owner, clock.today_in(zone)
    )
    locked = await courses.lock_cohort_enrollment(session, enrollment_id)
    if locked is None or locked.cohort_id != cohort_id:
        raise scheduling.EnrollmentNotFoundError()
    try:
        withdrawn = await courses.withdraw_cohort_enrollment(
            session,
            enrollment_id,
            version=body.version,
            withdrawn_at=now,
            actor_id=actor_id,
            reason=body.reason,
        )
    except courses.CohortEnrollmentNotActive:
        raise scheduling.EnrollmentNotActiveError() from None
    await scheduling.remove_series_participant(
        session,
        series_ids=[series.id for series in producing],
        student_id=withdrawn.student_id,
        now=now,
        owner=owner,
    )
    change = CohortEnrollmentChange(
        enrollment_id=withdrawn.id,
        cohort_id=cohort_id,
        student_id=withdrawn.student_id,
        change="withdrawn",
        effective_at=now,
        actor_user_id=actor_id,
    )
    await apply_cohort_enrollment_change(session, change)
    return change
