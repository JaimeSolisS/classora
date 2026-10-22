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

**The M6-07 contract.** M6-07 adds its charge step to
:func:`apply_enrollment_change`, **in the same Unit of Work**: a failure
there rolls back the enrollment, its participants and its audit events
together. Its sweeper finds the enrollment periods without charges in
``group_enrollments`` (one row per period, write-once withdrawal, never
deleted) through ``scheduling.service.list_group_enrollment_periods``
(ISSUE-J1: work enqueued after commit is found again by a sweeper). There is
no event bus and no job is enqueued here: today the step does nothing.

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
    """The cross-module steps of one enrollment change (none yet). M6-07
    adds the charge (or proration) of the period here."""
    del session, change


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
