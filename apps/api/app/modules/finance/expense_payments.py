"""Expense payments and their reversals (#115 D, E; ADR-0017 E12, E16;
data-model §12.4, §12.5, §13 X1, §15).

Money paid to a vendor outside the platform, partial payments included. A
mistake is reversed, never edited; a reversed payment counts for nothing: not
``paid``, the cap or the duplicate rule (#115 E2, E5).

**Recording** (:func:`record_payment`), after the route's checks (permission,
``422 text_invalid``, key, body): the expense ``FOR UPDATE``
(:func:`~app.modules.finance.expenses.lock_expense`; ``404``) → ``409
expense_void`` (#115 E4) → ``422 payment_method_unavailable`` (through
``billing.service``) → ``422 paid_on_in_future`` (the center's today; no lower
bound, #115 E2) → ``409 expense_payment_duplicate`` (with a reference only) →
``409 expense_payment_exceeds_open`` (``paid + amount > net``, X1) → the row.

**Reversing** (:func:`reverse_payment`), after the route's checks (both
permissions, reauthentication, ``422 text_invalid``, key, body): the payment's
expense, read with no lock (``404``) → that expense ``FOR UPDATE`` → ``409
expense_payment_already_reversed`` (the UNIQUE is the backstop, mapped in a
savepoint) → the row.

The expense row is the only business lock (data-model §15), taken after the
idempotency key, so payments, reversals and adjustments of one expense run one
after the other and every rule reads after it. Every function runs in the
caller's Unit of Work and never commits; inserts go through the ORM, so the
audit rows mask ``amount``, ``reference``, ``note`` and ``reason`` (#115 E7).
There is no named event, notification or log line.
"""

import uuid
from typing import Final

from sqlalchemy import func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.finance.errors import (
    ExpensePaymentAlreadyReversedError,
    ExpensePaymentDuplicateError,
    ExpensePaymentExceedsOpenError,
    ExpenseVoidError,
    PaidOnInFutureError,
)
from app.modules.finance.expenses import (
    expense_status,
    lock_expense,
    settled,
    unreversed,
)
from app.modules.finance.models import (
    EXPENSE_PAYMENT_REVERSAL_KEY,
    ExpensePayment,
    ExpensePaymentReversal,
)
from app.modules.finance.schemas import (
    ExpensePaymentCreate,
    ExpensePaymentRecorded,
    ExpensePaymentReversalCreate,
    ExpensePaymentReversed,
)

METHOD_FIELD: Final = "method_code"


async def duplicate_of(
    session: AsyncSession, expense_id: uuid.UUID, method_code: str, reference: str
) -> uuid.UUID | None:
    """A payment of this expense without a reversal with this method and this
    reference, ignoring case (both are trimmed), or ``None`` (#115 E5). One
    statement; under the expense lock it can't change."""
    return await session.scalar(
        select(ExpensePayment.id)
        .where(
            ExpensePayment.expense_id == expense_id,
            ExpensePayment.method_code == method_code,
            # PostgreSQL's lower() on both sides, so the database's case rules
            # decide (#112 D6).
            func.lower(ExpensePayment.reference) == func.lower(literal(reference)),
            unreversed(),
        )
        .order_by(ExpensePayment.created_at, ExpensePayment.id)
        .limit(1)
    )


async def insert_payment(
    session: AsyncSession,
    expense_id: uuid.UUID,
    body: ExpensePaymentCreate,
    recorded_by_user_id: uuid.UUID,
) -> uuid.UUID:
    """The payment row; the caller holds the expense ``FOR UPDATE`` and checked
    every rule."""
    payment = ExpensePayment(
        id=uuid.uuid7(),
        expense_id=expense_id,
        paid_on=body.paid_on,
        amount=body.amount,
        method_code=body.method_code,
        reference=body.reference,
        note=body.note,
        recorded_by_user_id=recorded_by_user_id,
    )
    session.add(payment)
    await session.flush()
    return payment.id


async def record_payment(
    session: AsyncSession,
    user_id: uuid.UUID,
    expense_id: uuid.UUID,
    body: ExpensePaymentCreate,
) -> ExpensePaymentRecorded:
    """``POST /api/v1/finance/expenses/{id}/payments``, in the order of the
    module docstring."""
    expense = await lock_expense(session, expense_id)
    figures = await settled(session, expense)
    if figures.net.is_zero():
        raise ExpenseVoidError()
    if not await billing.active_payment_method(session, body.method_code):
        raise billing.PaymentMethodUnavailableError(METHOD_FIELD)
    today = await administration.center_today(session)
    if body.paid_on > today:
        raise PaidOnInFutureError()
    if body.reference is not None:
        duplicate = await duplicate_of(
            session, expense_id, body.method_code, body.reference
        )
        if duplicate is not None:
            raise ExpensePaymentDuplicateError(duplicate)
    # Decimal: paid + amount may be beyond the money range; that is refused too.
    if figures.paid.amount + body.amount.amount > figures.net.amount:
        raise ExpensePaymentExceedsOpenError(figures.open)
    payment_id = await insert_payment(session, expense_id, body, user_id)
    return ExpensePaymentRecorded(
        id=payment_id,
        expense_id=expense_id,
        status=expense_status(
            figures.net, figures.paid + body.amount, expense.due_on, today
        ),
    )


async def payment_expense_id(
    session: AsyncSession, payment_id: uuid.UUID
) -> uuid.UUID | None:
    """The payment's expense, or ``None`` for no payment. One statement, no
    lock: a payment never moves, so the caller locks this expense next."""
    return await session.scalar(
        select(ExpensePayment.expense_id).where(ExpensePayment.id == payment_id)
    )


async def is_reversed(session: AsyncSession, payment_id: uuid.UUID) -> bool:
    """Whether the payment has its reversal. One statement; under the expense
    lock it can't change."""
    found = await session.scalar(
        select(ExpensePaymentReversal.id).where(
            ExpensePaymentReversal.expense_payment_id == payment_id
        )
    )
    return found is not None


async def insert_reversal(
    session: AsyncSession,
    payment_id: uuid.UUID,
    *,
    reason: str,
    reversed_by_user_id: uuid.UUID,
) -> uuid.UUID | None:
    """The reversal row in a savepoint, or ``None`` when the payment already has
    one (the UNIQUE, the backstop of :func:`is_reversed`: never a ``500``)."""
    reversal = ExpensePaymentReversal(
        id=uuid.uuid7(),
        expense_payment_id=payment_id,
        reason=reason,
        reversed_by_user_id=reversed_by_user_id,
    )
    try:
        async with session.begin_nested():
            session.add(reversal)
    except IntegrityError as exc:
        if EXPENSE_PAYMENT_REVERSAL_KEY in str(exc.orig):
            return None
        raise
    return reversal.id


async def reverse_payment(
    session: AsyncSession,
    user_id: uuid.UUID,
    payment_id: uuid.UUID,
    body: ExpensePaymentReversalCreate,
) -> ExpensePaymentReversed:
    """``POST /api/v1/finance/expense-payments/{id}/reversal``, in the order of
    the module docstring."""
    expense_id = await payment_expense_id(session, payment_id)
    if expense_id is None:
        raise RecordNotFoundError()
    expense = await lock_expense(session, expense_id)
    if await is_reversed(session, payment_id):
        raise ExpensePaymentAlreadyReversedError()
    reversal_id = await insert_reversal(
        session, payment_id, reason=body.reason, reversed_by_user_id=user_id
    )
    if reversal_id is None:
        raise ExpensePaymentAlreadyReversedError()
    figures = await settled(session, expense)
    today = await administration.center_today(session)
    return ExpensePaymentReversed(
        id=reversal_id,
        expense_payment_id=payment_id,
        status=expense_status(figures.net, figures.paid, expense.due_on, today),
    )
