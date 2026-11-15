"""Billing figures for the financial reports (#117 A3; ADR-0006 E7).

Thin public wrappers over the ledger's own expressions, so the reporting
module never re-implements ``net``, ``open`` or "unreversed" (PM-NOTES #115
MUST):

- :func:`charge_figures`: every charge with its ``net`` (``amount + Σ
  adjustments``, the ``ledger._net_column`` rule) and the latest adjustment's
  ``posted_at``, as a subquery (not executed).
- :func:`unreversed_payment`: the condition "this ``payments`` row has no
  reversal" (``balances._reversed``), the only payments the cash basis counts.
- :func:`receivable_totals`: outstanding, overdue (by the effective due date,
  the ``overdue._open_charges`` rule), account credit
  (``payment_remainder_column``) and the accounts with something outstanding,
  over every account. It equals Σ :func:`account_balance` over every account.

All are reads: no lock, no write.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Subquery, and_, func, literal, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money, MoneyType, money_sum
from app.modules.billing.balances import (
    _reversed,
    _valid_allocated,
    effective_due_date_column,
    payment_remainder_column,
)
from app.modules.billing.ledger import _net_column
from app.modules.billing.models import (
    Charge,
    ChargeAdjustment,
    Payment,
    PaymentAllocation,
)


def charge_figures() -> Subquery:
    """``SELECT charges.*, net, last_adjusted_at FROM charges`` as a subquery
    (not executed). ``net`` is the charge's current net (the ledger's rule);
    ``last_adjusted_at`` the newest adjustment's ``posted_at`` (``NULL`` with
    none)."""
    last_adjusted = (
        select(func.max(ChargeAdjustment.posted_at))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .correlate(Charge)
        .scalar_subquery()
    )
    return select(
        Charge,
        _net_column(),
        last_adjusted.label("last_adjusted_at"),
    ).subquery("charge_figures")


def unreversed_payment(payment: Any = Payment) -> Any:
    """``NOT EXISTS`` a reversal of the ``payments`` row (the condition behind
    the account credit; #117 cash basis)."""
    return ~_reversed(payment)


@dataclass(frozen=True)
class ReceivableTotals:
    """The receivables of every account (#117 E9): Σ open charges, of which
    overdue, the account credit (shown, never netted; #97) and how many
    accounts have something outstanding."""

    outstanding: Money
    overdue: Money
    account_credit: Money
    accounts_with_outstanding: int


async def receivable_totals(session: AsyncSession, today: date) -> ReceivableTotals:
    """:class:`ReceivableTotals` as of ``today`` (the center's). Two
    statements, no lock."""
    zero = literal(Money.zero(), MoneyType())
    allocated = _valid_allocated(PaymentAllocation.charge_id == Charge.id)
    per_charge = select(
        Charge.billing_account_id.label("account_id"),
        type_coerce(_net_column() - allocated, MoneyType()).label("open"),
        effective_due_date_column().label("due_date"),
    ).subquery("open_charges")
    per_account = (
        select(
            per_charge.c.account_id,
            money_sum(per_charge.c.open).label("outstanding"),
            func.coalesce(
                func.sum(per_charge.c.open).filter(
                    and_(per_charge.c.open > zero, per_charge.c.due_date < today)
                ),
                zero,
                type_=MoneyType(),
            ).label("overdue"),
        )
        .group_by(per_charge.c.account_id)
        .subquery("per_account")
    )
    row = (
        await session.execute(
            select(
                money_sum(per_account.c.outstanding).label("outstanding"),
                money_sum(per_account.c.overdue).label("overdue"),
                func.count().filter(per_account.c.outstanding > zero).label("accounts"),
            )
        )
    ).one()
    credit = await session.scalar(
        select(money_sum(payment_remainder_column())).where(unreversed_payment())
    )
    return ReceivableTotals(
        outstanding=_money(row.outstanding),
        overdue=_money(row.overdue),
        account_credit=_money(credit),
        accounts_with_outstanding=int(row.accounts or 0),
    )


def _money(value: Any) -> Money:
    return Money.zero() if value is None else Money(value)
