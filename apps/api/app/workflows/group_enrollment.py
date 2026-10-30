"""Group enrollment changes: the billing hook of ongoing tutoring groups (#56
E2, E3; architecture §4.2, ISSUE-J1; ADR-0002).

The group routes (:mod:`app.workflows.groups_router`) call these functions,
never ``scheduling.service`` directly for a change. Each runs in the
request's one Unit of Work:

- :func:`create_group` → ``scheduling.service.create_group`` (the group, its
  series, the initial enrollments and occurrences), then
  :func:`apply_enrollment_change` for each initial student (``enrolled``);
- :func:`enroll_student` → ``scheduling.service.enroll_in_group``, then
  :func:`apply_enrollment_change`;
- :func:`withdraw_student` → ``scheduling.service.withdraw_from_group``, then
  :func:`apply_enrollment_change`.

Each returns the frozen :class:`GroupEnrollmentChange` (``enrollment_id``,
``group_id``, ``student_id``, ``change`` ``enrolled`` | ``withdrawn``,
``effective_at``, ``actor_user_id``).

**The charges (#92 M6-07).** :func:`apply_enrollment_change` runs the
billing steps **in the same Unit of Work**, after the M4 locks (students →
zone → group → series → sessions), so an unexpected failure there rolls
back the enrollment, its participants and its audit events together (J10;
the expected skips write nothing and raise nothing):

- ``enrolled``: the J4 restorations of the session charges the student's
  earlier withdrawals credited, then the flat fee of the period when the
  group is fee-priced (``billing_starts_on`` ``FOR SHARE`` → the
  classification → the account row ``FOR UPDATE``);
- ``withdrawn``: the J4 ``enrollment_removal`` credits of the charged
  sessions the student just left. A flat group fee is never adjusted here
  (only #101).

**Withdrawal settlements (#101).** ``app.workflows.withdrawals`` calls
:func:`withdraw_student` for an active enrollment in its own Unit of Work
(the hook's J4 credits are then shown in its preview through
``enrollment_charges.removal_credits``, never posted twice); for an
enrollment already withdrawn it takes the same locks through
``scheduling.service.lock_group_enrollment_for_settlement``.

The rules are in :mod:`app.workflows.enrollment_charges`; the periods a
hook skips (billing off, no family) are found by
``billing:post_enrollment_charges`` (ISSUE-J1). There is no event bus and
no job is enqueued here.

**Rules the steps rely on** (``scheduling.groups``): the change takes effect
at ``effective_at`` and touches only the group's occurrences that start
after it in ``unassigned``, ``offered``, ``confirmed``, ``conflict`` or
``skipped_closure`` (past, started, ``completed`` and ``cancelled`` ones
keep their participants); the lock order is students → zone → group →
series → sessions.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import (
    GroupCreate,
    GroupEnrollmentCreate,
    GroupWithdrawal,
)
from app.modules.scheduling.service import GroupEnrollmentChange
from app.workflows import enrollment_charges

__all__ = [
    "GroupEnrollmentChange",
    "apply_enrollment_change",
    "create_group",
    "enroll_student",
    "withdraw_student",
]


async def apply_enrollment_change(
    session: AsyncSession, change: GroupEnrollmentChange
) -> None:
    """The billing steps of one enrollment change (#92 C2; see the module
    docstring), in the caller's Unit of Work."""
    if change.change == "withdrawn":
        await enrollment_charges.credit_removed_sessions(
            session,
            group_id=change.group_id,
            student_id=change.student_id,
            enrollment_id=change.enrollment_id,
            effective_at=change.effective_at,
        )
        return
    await enrollment_charges.restore_rejoined_sessions(
        session,
        group_id=change.group_id,
        student_id=change.student_id,
        enrollment_id=change.enrollment_id,
        effective_at=change.effective_at,
    )
    period = await enrollment_charges.group_period(session, change.enrollment_id)
    await enrollment_charges.charge_period(session, period)


async def create_group(
    session: AsyncSession,
    body: GroupCreate,
    *,
    horizon_weeks: int,
    actor_id: uuid.UUID,
) -> uuid.UUID:
    """Create a group (``POST /api/v1/groups``) and run the step for each
    initial enrollment. Returns the group id."""
    created = await scheduling.create_group(
        session, body, horizon_weeks=horizon_weeks, actor_id=actor_id
    )
    for change in created.changes:
        await apply_enrollment_change(session, change)
    return created.group_id


async def enroll_student(
    session: AsyncSession,
    group_id: uuid.UUID,
    body: GroupEnrollmentCreate,
    *,
    actor_id: uuid.UUID,
) -> GroupEnrollmentChange:
    """Enroll a student (``POST /api/v1/groups/{id}/enrollments``)."""
    change = await scheduling.enroll_in_group(
        session, group_id, body, actor_id=actor_id
    )
    await apply_enrollment_change(session, change)
    return change


async def withdraw_student(
    session: AsyncSession,
    group_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    body: GroupWithdrawal,
    *,
    actor_id: uuid.UUID,
) -> GroupEnrollmentChange:
    """Withdraw a student (``POST
    /api/v1/groups/{id}/enrollments/{enrollment_id}/withdraw``)."""
    change = await scheduling.withdraw_from_group(
        session, group_id, enrollment_id, body, actor_id=actor_id
    )
    await apply_enrollment_change(session, change)
    return change
