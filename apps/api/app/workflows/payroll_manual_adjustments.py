"""A person's payroll adjustments (M7-08c; spec §3, §7, W3.5; ADR-0017 E9, E11,
E12 and its #111 and M7-08c notes; data-model §11.9, §13.2, §15).

A finalized period never changes. Finance corrects one by hand with a ``manual``
adjustment, and moves a negative total forward with a ``carry_forward`` pair.
Both land in ``open`` periods only, so the target's ``finalize`` (#110 duty
rules) is the second person for both (E4).

**``manual``** (:func:`post_manual`, E1), one of three shapes, each with a
reason the tutor reads on the payslip once it is finalized:

- **on a line** (``origin_line_id``): a line of this tutor in a ``finalized``
  period, any kind. Another tutor's line and no line are the same ``404``; a line
  of an ``open`` or ``prepared`` period is ``409 payroll_line_not_finalized``
  (it is rebuilt by a run).
- **on unresolved work** (``class_session_id``, E2): the session must be one of
  the tutor's current items with a blocking reason (``no_pay_terms``,
  ``no_rate``), classified again under the locks. Already named by an adjustment
  of the tutor: ``409 payroll_work_settled``; ``before_first_period``: ``409
  payroll_work_before_first_period`` (a one-time item pays it, #109 E4);
  anything else: ``409 payroll_work_not_unresolved``. Once written the work is
  **settled** (#111 E7): off the Unresolved list and ``blocking_work``, no line
  from a run (:func:`~app.workflows.payroll_calculation.compute_lines`), and the
  job never posts for it. The first-payment index (#111 A3) makes a race with
  the job's ``late_approval`` pay once: the loser of the index is ``409
  payroll_work_settled``.
- **free** (neither): an amount and a reason, for everything else, a leaver's
  stranded one-time item included, after the item is deleted (E5).

Order, after the route's checks (permission, reauthentication, key, body): the
own-pay rule (``403 payroll_own_compensation``, an Owner excepted) → the tutor
(``404``) → the advisory lock (#106 E10) → the tutor's not-finalized periods
``FOR UPDATE``, oldest first → the reference checks → the target, the earliest
``open`` period, opened as #111 opens one if there is none (``409
payroll_no_period`` for a tutor with no period at all, D2) → the row in a
savepoint → its ``adjustment`` line.

**``carry_forward``** (:func:`carry_forward`, E3): on an ``open``, ended period
whose total after a ``recalculate`` is negative, ``+X`` in that period (its
total becomes ``0.00``) and ``-X`` in the tutor's earliest ``open`` period
starting after it (opened if there is none), ``X`` the negated total. Order: the
advisory lock → the period read (``404``) → the tutor's not-finalized periods
``FOR UPDATE``, oldest first → the period re-read → ``409
payroll_period_not_open`` → ``409 payroll_period_not_ended`` → ``recalculate``
→ ``409 payroll_total_not_negative`` → the target → the two rows and their
lines. No reauthentication and own pay allowed, like ``prepare`` (E4): it is net
zero and touches only ``open`` periods.

Both take the advisory lock before any period row, as the terms writes and the
job's period opening do, and the tutor's periods oldest first, as a rate or pay
item write does, so none of them can deadlock another.

**No pay in side channels (E6):** the responses and the stored idempotent
response hold ids, the kind, the target and ``created_at``; the audit row masks
``amount``, ``rate`` and ``reason`` (#111 E9); no named event and no log line.
"""

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError
from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    PayrollAdjustmentCreate,
    PayrollAdjustmentPosted,
    PayrollCarryForward,
)
from app.workflows import payroll_calculation
from app.workflows.payroll_adjustments import lock_target

TUTOR: Final = "tutor"
OPEN: Final = "open"
FINALIZED: Final = "finalized"
BEFORE_FIRST_PERIOD: Final = "before_first_period"
MANUAL: Final = "manual"


async def _check_line(
    session: AsyncSession, tutor_user_id: uuid.UUID, line_id: uuid.UUID
) -> None:
    status = await payroll.line_period_status(session, line_id, tutor_user_id)
    if status is None:
        raise RecordNotFoundError()
    if status != FINALIZED:
        raise payroll.PayrollLineNotFinalizedError()


async def _check_work(
    session: AsyncSession, tutor_user_id: uuid.UUID, class_session_id: uuid.UUID
) -> None:
    """A4, under the caller's locks."""
    if await payroll.settled_by_tutor(session, tutor_user_id, class_session_id):
        raise payroll.PayrollWorkSettledError()
    items = await payroll_calculation.unresolved_work(session, tutor_user_id, None)
    item = next(
        (item for item in items if item.class_session_id == class_session_id), None
    )
    if item is None:
        raise payroll.PayrollWorkNotUnresolvedError()
    if item.reason == BEFORE_FIRST_PERIOD:
        raise payroll.PayrollWorkBeforeFirstPeriodError()
    if not item.blocking:  # pragma: no cover - every other reason blocks
        raise payroll.PayrollWorkNotUnresolvedError()


async def post_manual(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    body: PayrollAdjustmentCreate,
) -> PayrollAdjustmentPosted:
    """``POST /api/v1/payroll/tutors/{id}/adjustments``, in the order of the
    module docstring."""
    if not payroll.can_write_compensation(principal, tutor_user_id):
        raise payroll.PayrollOwnCompensationError()
    if await identity.get_role_member(session, TUTOR, tutor_user_id) is None:
        raise RecordNotFoundError()
    await payroll.lock_payroll_periods(session)
    await payroll.lock_unfinalized_periods_of_tutor(session, tutor_user_id)
    if body.origin_line_id is not None:
        await _check_line(session, tutor_user_id, body.origin_line_id)
    if body.class_session_id is not None:
        await _check_work(session, tutor_user_id, body.class_session_id)
    target = await lock_target(
        session, tutor_user_id, today=await administration.center_today(session)
    )
    posted = await payroll.post_manual_adjustment(
        session,
        payroll.ManualDraft(
            tutor_user_id=tutor_user_id,
            amount=body.amount,
            reason=body.reason,
            created_by_user_id=principal.user_id,
            origin_line_id=body.origin_line_id,
            class_session_id=body.class_session_id,
        ),
        target.id,
    )
    if posted is None:
        # The job's late_approval won the first-payment index meanwhile.
        raise payroll.PayrollWorkSettledError()
    return PayrollAdjustmentPosted(
        id=posted.id,
        kind=MANUAL,
        tutor_user_id=tutor_user_id,
        target_period_id=posted.target_period_id,
        created_at=posted.created_at,
    )


async def carry_forward(
    session: AsyncSession, principal: Principal, period_id: uuid.UUID
) -> PayrollCarryForward:
    """``POST /api/v1/payroll/periods/{id}/carry-forward``, in the order of the
    module docstring."""
    await payroll.lock_payroll_periods(session)
    unlocked = await payroll.period_row(session, period_id)
    await payroll.lock_unfinalized_periods_of_tutor(session, unlocked.tutor_user_id)
    period = await payroll.lock_period(session, period_id)
    if period.status != OPEN:
        raise payroll.PayrollPeriodNotOpenError(period.status)
    today = await administration.center_today(session)
    if period.ends_on >= today:
        raise payroll.PayrollPeriodNotEndedError(period.ends_on)
    calculated = await payroll_calculation.recalculate(session, period_id)
    if not calculated.total.is_negative():
        raise payroll.PayrollTotalNotNegativeError()
    target = await lock_target(
        session, period.tutor_user_id, today=today, after=period.starts_on
    )
    positive, negative = await payroll.post_carry_forward(
        session,
        tutor_user_id=period.tutor_user_id,
        period_id=period_id,
        target_period_id=target.id,
        amount=-calculated.total,
        created_by_user_id=principal.user_id,
    )
    return PayrollCarryForward(
        period_id=period_id,
        target_period_id=target.id,
        adjustment_ids=[positive.id, negative.id],
    )
