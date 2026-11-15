"""Logging payroll payments and reversing them (#112; spec §7; ADR-0017 E12,
E13; data-model §11.10, §11.11, §13, §15, P7).

Money paid to a tutor outside the platform (a transfer, cash) is logged against
a ``finalized`` period, with the date, the amount, the method, a reference and an
optional note: the reference and the note are the proof, there is no file
(owner decision 2026-10-21). Partial payments are allowed; over-payment and
duplicates are refused; a mistake is reversed, never edited. This is a workflow
because it composes ``payroll``, ``billing`` (the payment methods, through
``billing.service`` only, ADR-0002), ``administration`` and ``identity``.

**Recording** (:func:`record_payment`), after the route's checks (permission,
``422 text_invalid``, key, body): the period ``FOR UPDATE``
(:func:`~app.modules.payroll.lines.lock_period`; ``404``) → ``403
payroll_own_compensation`` on the caller's own period, an Owner excepted (#112
E5) → ``422 payment_method_unavailable`` → ``422 paid_on_in_future`` (after the
center's today) → ``422 paid_on_before_period`` (before ``starts_on``, #112 E4)
→ ``409 payroll_period_not_finalized`` → ``409 payroll_payment_duplicate`` → ``409
payroll_payment_exceeds_balance`` → the row. The tutor is known only from the
period, so the ``404`` comes first (#112 D1).

**Reversing** (:func:`reverse_payment`), after the route's checks (permission,
reauthentication, ``422 text_invalid``, key, body): the payment's period, read
with no lock (``404`` for no payment) → that period ``FOR UPDATE`` → ``409
payroll_payment_already_reversed`` (the UNIQUE is the backstop, mapped in a
savepoint) → ``403 payroll_own_compensation``, an Owner excepted → the row. The
payment then counts for nothing: not the paid state, the cap or the duplicate
rule (#112 E2).

**The lock (B3):** the idempotency key is claimed first, then the period row is
the only lock, and every rule reads after it. A finalized row allows ``FOR
UPDATE`` (#110 K1), and its ``total_amount`` never changes, so two payments, or a
payment and a reversal, of one period run one after the other. Taking no other
lock, this can't deadlock with ``payroll:post_adjustments`` (#469).

**What counts:** payments without a reversal. ``paid`` is their sum: ``paid +
amount > total_amount`` is refused with ``details.remaining``, so exactly the
remainder is accepted (P7). A finalized ``0.00`` period is ``paid`` with no
payment, and refuses any (#112 E8).

**No pay in side channels (#112 E6):** the responses, and so the stored
idempotent response, hold ids and the paid state; the audit rows mask
``amount``, ``reference``, ``note`` and ``reason``; there is no named event,
notification or log line.

**Who reads (#112 E7):** :func:`list_payments`, the period's record policy of
#110 E8 (:func:`~app.modules.payroll.permissions.can_read_period`): a
``payroll.view`` holder every period, a delegated Administrator ``prepared`` and
``finalized`` ones; any other is the same ``404``. A tutor's own read is #113.
"""

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError
from app.modules.administration import service as administration
from app.modules.administration.schemas import AccountName
from app.modules.billing import service as billing
from app.modules.identity import service as identity
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    PayrollPaymentCreate,
    PayrollPaymentRecorded,
    PayrollPaymentReversalCreate,
    PayrollPaymentReversed,
)
from app.workflows.payroll_workflow_schemas import (
    PayrollPaymentItem,
    PayrollPaymentReversalItem,
)

FINALIZED: Final = "finalized"
METHOD_FIELD: Final = "method_code"


async def record_payment(
    session: AsyncSession,
    principal: Principal,
    period_id: uuid.UUID,
    body: PayrollPaymentCreate,
) -> PayrollPaymentRecorded:
    """``POST /api/v1/payroll/periods/{id}/payments``, in the order of the module
    docstring."""
    period = await payroll.lock_period(session, period_id)
    if not payroll.can_write_compensation(principal, period.tutor_user_id):
        raise payroll.PayrollOwnCompensationError()
    if not await billing.active_payment_method(session, body.method_code):
        raise billing.PaymentMethodUnavailableError(METHOD_FIELD)
    if body.paid_on > await administration.center_today(session):
        raise payroll.PaidOnInFutureError()
    if body.paid_on < period.starts_on:
        raise payroll.PaidOnBeforePeriodError()
    if period.status != FINALIZED or period.total_amount is None:
        raise payroll.PayrollPeriodNotFinalizedError(period.status)
    duplicate = await payroll.duplicate_of(
        session, period_id, body.method_code, body.reference
    )
    if duplicate is not None:
        raise payroll.PayrollPaymentDuplicateError(duplicate)
    total = period.total_amount
    paid = await payroll.paid_total(session, period_id)
    # Decimal: paid + amount may be beyond the money range; that is refused too.
    if paid.amount + body.amount.amount > total.amount:
        raise payroll.PayrollPaymentExceedsBalanceError(total - paid)
    payment_id = await payroll.record_payment(
        session,
        payroll.PaymentDraft(
            payroll_period_id=period_id,
            paid_on=body.paid_on,
            amount=body.amount,
            method_code=body.method_code,
            reference=body.reference,
            note=body.note,
            recorded_by_user_id=principal.user_id,
        ),
    )
    return PayrollPaymentRecorded(
        id=payment_id,
        payroll_period_id=period_id,
        paid_state=payroll.paid_state(total, paid + body.amount),
    )


async def reverse_payment(
    session: AsyncSession,
    principal: Principal,
    payment_id: uuid.UUID,
    body: PayrollPaymentReversalCreate,
) -> PayrollPaymentReversed:
    """``POST /api/v1/payroll/payments/{id}/reversal``, in the order of the
    module docstring."""
    period_id = await payroll.payment_period_id(session, payment_id)
    if period_id is None:
        raise RecordNotFoundError()
    period = await payroll.lock_period(session, period_id)
    if await payroll.is_reversed(session, payment_id):
        raise payroll.PayrollPaymentAlreadyReversedError()
    if not payroll.can_write_compensation(principal, period.tutor_user_id):
        raise payroll.PayrollOwnCompensationError()
    reversal_id = await payroll.reverse_payment(
        session,
        payment_id,
        reason=body.reason,
        reversed_by_user_id=principal.user_id,
    )
    if reversal_id is None:  # pragma: no cover - the period lock comes first
        raise payroll.PayrollPaymentAlreadyReversedError()
    if period.total_amount is None:  # pragma: no cover - payments need finalized
        raise payroll.PayrollPeriodNotFinalizedError(period.status)
    paid = await payroll.paid_total(session, period_id)
    return PayrollPaymentReversed(
        id=reversal_id,
        payroll_payment_id=payment_id,
        paid_state=payroll.paid_state(period.total_amount, paid),
    )


def _reversal(
    row: payroll.PaymentRow, names: dict[uuid.UUID, str]
) -> PayrollPaymentReversalItem | None:
    if (
        row.reversal_reason is None
        or row.reversed_by_user_id is None
        or row.reversed_at is None
    ):
        return None
    return PayrollPaymentReversalItem(
        reason=row.reversal_reason,
        reversed_by=AccountName(
            id=row.reversed_by_user_id, display_name=names[row.reversed_by_user_id]
        ),
        created_at=row.reversed_at,
    )


async def list_payments(
    session: AsyncSession, principal: Principal, period_id: uuid.UUID
) -> list[PayrollPaymentItem]:
    """``GET /api/v1/payroll/periods/{id}/payments``: the record policy first, so
    a hidden period is the same ``404`` as a missing one. Three statements, no
    lock."""
    period = await payroll.period_row(session, period_id)
    if not payroll.can_read_period(principal, period.status):
        raise RecordNotFoundError()
    rows = await payroll.list_payments(session, period_id)
    accounts = await identity.get_account_references(
        session,
        {row.recorded_by_user_id for row in rows}
        | {row.reversed_by_user_id for row in rows if row.reversed_by_user_id},
    )
    names = {user_id: account.display_name for user_id, account in accounts.items()}
    return [
        PayrollPaymentItem(
            id=row.id,
            paid_on=row.paid_on,
            amount=row.amount,
            method_code=row.method_code,
            reference=row.reference,
            note=row.note,
            recorded_by=AccountName(
                id=row.recorded_by_user_id,
                display_name=names[row.recorded_by_user_id],
            ),
            created_at=row.created_at,
            reversal=_reversal(row, names),
        )
        for row in rows
    ]
