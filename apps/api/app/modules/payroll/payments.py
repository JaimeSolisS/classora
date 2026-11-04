"""Payroll payments and their reversals (#112; spec §7; ADR-0017 E12, E13;
data-model §11.10, §11.11, §13, §15, P7).

A payment is money paid to a tutor outside the platform, logged against a
``finalized`` period. :mod:`app.workflows.payroll_payments` decides, under the
period lock (:func:`~app.modules.payroll.lines.lock_period`); this module reads
and writes the two tables.

**What counts (#112 E2):** a payment **without a reversal**. It counts toward
the period's paid total (:func:`paid_total`, :func:`paid_sum`), the cap (P7) and
the duplicate rule (:func:`duplicate_of`). A reversed payment frees its method
and reference, so "reverse the wrong amount, re-enter it with the same transfer
number" works.

**Duplicates (#112 E3):** the same ``method_code`` and the same reference,
trimmed and compared case-insensitively.

**Paid state (data-model §13):** :func:`paid_state`; a ``0.00`` total is
``paid`` with no payment (#112 E8).

Every function runs in the caller's Unit of Work and never commits. Inserts go
through the ORM, so the audit row comes with the Unit of Work's actor, with
``amount``, ``reference``, ``note`` and ``reason`` masked.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import (
    ColumnElement,
    Numeric,
    ScalarSelect,
    cast,
    exists,
    func,
    literal,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.payroll.models import (
    PAYMENT_REVERSAL_KEY,
    PayrollPayment,
    PayrollPaymentReversal,
)
from app.modules.payroll.schemas import PaidState

UNPAID: Final = "unpaid"
PARTIALLY_PAID: Final = "partially_paid"
PAID: Final = "paid"


def paid_state(total: Money, paid: Money) -> PaidState:
    """``unpaid``, ``partially_paid`` or ``paid`` (data-model §13). A ``0.00``
    total is ``paid`` (#112 E8)."""
    if paid >= total:
        return PAID
    if paid.is_zero():
        return UNPAID
    return PARTIALLY_PAID


def _unreversed() -> ColumnElement[bool]:
    return ~exists().where(
        PayrollPaymentReversal.payroll_payment_id == PayrollPayment.id
    )


def paid_sum(period_id: Any) -> ScalarSelect[Any]:
    """The sum of the period's payments without a reversal, ``0`` with none: a
    scalar subquery, correlated on ``period_id`` (a column), so a list reads it
    in its own statement (#112 D2)."""
    return (
        select(func.coalesce(func.sum(cast(PayrollPayment.amount, Numeric)), 0))
        .where(PayrollPayment.payroll_period_id == period_id, _unreversed())
        .scalar_subquery()
    )


def last_paid_on(period_id: Any) -> ScalarSelect[Any]:
    """The latest ``paid_on`` of the period's payments without a reversal,
    ``NULL`` with none: a scalar subquery correlated on ``period_id``, like
    :func:`paid_sum` (#113 C2)."""
    return (
        select(func.max(PayrollPayment.paid_on))
        .where(PayrollPayment.payroll_period_id == period_id, _unreversed())
        .scalar_subquery()
    )


async def paid_total(session: AsyncSession, period_id: uuid.UUID) -> Money:
    """:func:`paid_sum` of one period. One statement; under the period lock it
    can't change."""
    value = await session.scalar(select(paid_sum(period_id)))
    return Money.zero() if value is None else Money(value)


async def duplicate_of(
    session: AsyncSession, period_id: uuid.UUID, method_code: str, reference: str
) -> uuid.UUID | None:
    """A payment without a reversal of this period with this method and this
    reference, trimmed and ignoring case (#112 E2, E3), or ``None``. One
    statement."""
    return await session.scalar(
        select(PayrollPayment.id)
        .where(
            PayrollPayment.payroll_period_id == period_id,
            PayrollPayment.method_code == method_code,
            # Both sides through PostgreSQL's lower(), so the database's case
            # rules decide; the stored reference and the body are trimmed.
            func.lower(PayrollPayment.reference) == func.lower(literal(reference)),
            _unreversed(),
        )
        .order_by(PayrollPayment.created_at, PayrollPayment.id)
        .limit(1)
    )


@dataclass(frozen=True)
class PaymentDraft:
    """A payment before it is written (#112 B2)."""

    payroll_period_id: uuid.UUID
    paid_on: date
    amount: Money
    method_code: str
    reference: str
    note: str | None
    recorded_by_user_id: uuid.UUID


async def record_payment(session: AsyncSession, draft: PaymentDraft) -> uuid.UUID:
    """The payment row; the caller holds the period ``FOR UPDATE`` and checked
    every rule. One statement and the audit row."""
    payment = PayrollPayment(
        id=uuid.uuid7(),
        payroll_period_id=draft.payroll_period_id,
        paid_on=draft.paid_on,
        amount=draft.amount,
        method_code=draft.method_code,
        reference=draft.reference,
        note=draft.note,
        recorded_by_user_id=draft.recorded_by_user_id,
    )
    session.add(payment)
    await session.flush()
    return payment.id


async def payment_period_id(
    session: AsyncSession, payment_id: uuid.UUID
) -> uuid.UUID | None:
    """The payment's period, or ``None`` for no payment. One statement, no lock:
    a payment never moves, so the caller locks this period next."""
    return await session.scalar(
        select(PayrollPayment.payroll_period_id).where(PayrollPayment.id == payment_id)
    )


async def is_reversed(session: AsyncSession, payment_id: uuid.UUID) -> bool:
    """Whether the payment has its reversal. One statement; under the period
    lock it can't change."""
    found = await session.scalar(
        select(PayrollPaymentReversal.id).where(
            PayrollPaymentReversal.payroll_payment_id == payment_id
        )
    )
    return found is not None


async def reverse_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    *,
    reason: str,
    reversed_by_user_id: uuid.UUID,
) -> uuid.UUID | None:
    """The reversal row in a savepoint, or ``None`` when the payment already has
    one (the UNIQUE, the backstop of :func:`is_reversed`: never a ``500``). One
    statement and the audit row, with the savepoint."""
    reversal = PayrollPaymentReversal(
        id=uuid.uuid7(),
        payroll_payment_id=payment_id,
        reason=reason,
        reversed_by_user_id=reversed_by_user_id,
    )
    try:
        async with session.begin_nested():
            session.add(reversal)
    except IntegrityError as exc:
        if PAYMENT_REVERSAL_KEY in str(exc.orig):
            return None
        raise
    return reversal.id


@dataclass(frozen=True)
class PaymentRow:
    """One payment of a period with its reversal, for the read (#112 D1). Ids,
    not names: the workflow names them."""

    id: uuid.UUID
    paid_on: date
    amount: Money
    method_code: str
    reference: str
    note: str | None
    recorded_by_user_id: uuid.UUID
    created_at: datetime
    reversal_reason: str | None
    reversed_by_user_id: uuid.UUID | None
    reversed_at: datetime | None


async def list_payments(
    session: AsyncSession, period_id: uuid.UUID
) -> list[PaymentRow]:
    """The period's payments, reversed ones included, newest ``paid_on`` first,
    then ``created_at`` and ``id``, newest first too. One statement, no lock."""
    rows = await session.execute(
        select(
            PayrollPayment.id,
            PayrollPayment.paid_on,
            PayrollPayment.amount,
            PayrollPayment.method_code,
            PayrollPayment.reference,
            PayrollPayment.note,
            PayrollPayment.recorded_by_user_id,
            PayrollPayment.created_at,
            PayrollPaymentReversal.reason.label("reversal_reason"),
            PayrollPaymentReversal.reversed_by_user_id,
            PayrollPaymentReversal.created_at.label("reversed_at"),
        )
        .outerjoin(
            PayrollPaymentReversal,
            PayrollPaymentReversal.payroll_payment_id == PayrollPayment.id,
        )
        .where(PayrollPayment.payroll_period_id == period_id)
        .order_by(
            PayrollPayment.paid_on.desc(),
            PayrollPayment.created_at.desc(),
            PayrollPayment.id.desc(),
        )
    )
    return [PaymentRow(**row._mapping) for row in rows]
