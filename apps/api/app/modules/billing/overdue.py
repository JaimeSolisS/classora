"""Overdue accounts with aging buckets (#97 E1, E2; N7 (a); spec §6
"track pending, partially paid and overdue amounts").

A charge **counts** when its open amount (``net − valid allocations``) is
above zero and ``today − effective due date ≥ min_days`` (``today`` is the
center's local date; ``min_days`` ≥ 1, so a counted charge is ``overdue``).
Per account with at least one counted charge: the counted open total and
count, the oldest effective due date and ``days_overdue = today − it``, the
aging buckets of the counted open amounts by days past due (``1–30``,
``31–60``, ``61–90``, ``> 90``, inclusive as named; Σ buckets =
``overdue_total``), the account's outstanding amount, account credit and
net balance (credit is **shown, never netted**: it isn't allocated until
staff do it, #96), and the latest ``charge_reminders.sent_at`` of its
charges.

Order: ``days_overdue`` desc, ``overdue_total`` desc, family name, account
id. The families come from the caller as an unexecuted ``SELECT id, name,
is_active`` (``families.service.family_status_columns``, already filtered
by family status), so this module never reads the families' tables
itself. One statement per page with the filtered totals (window
aggregates), plus one for the totals when the page is past the end. No
lock.
"""

from datetime import date
from typing import Any, Final

from sqlalchemy import Integer, Select, and_, func, literal, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money, MoneyType, money_sum
from app.core.pagination import OffsetParams
from app.modules.billing.balances import (
    _reversed,
    _valid_allocated,
    effective_due_date_column,
    payment_remainder_column,
)
from app.modules.billing.models import (
    BillingAccount,
    Charge,
    ChargeAdjustment,
    ChargeReminder,
    Payment,
    PaymentAllocation,
)
from app.modules.billing.schemas import (
    AgingBuckets,
    OverdueAccountPage,
    OverdueAccountRow,
    OverdueTotals,
)

BUCKETS: Final = (
    ("days_1_30", 1, 30),
    ("days_31_60", 31, 60),
    ("days_61_90", 61, 90),
    ("days_over_90", 91, None),
)
"""The aging buckets (#97 N7): name, first and last day (``None``: no
end)."""


def _open_charges(today: date) -> Any:
    """Every charge with its account, open amount and days past its
    effective due date, as a subquery."""
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .scalar_subquery()
    )
    allocated = _valid_allocated(PaymentAllocation.charge_id == Charge.id)
    open_amount = type_coerce(Charge.amount + adjustments - allocated, MoneyType())
    days = type_coerce(literal(today) - effective_due_date_column(), Integer())
    return select(
        Charge.billing_account_id.label("account_id"),
        open_amount.label("open"),
        effective_due_date_column().label("due_date"),
        days.label("days"),
    ).subquery("open_charges")


def _sum_where(expression: Any, condition: Any) -> Any:
    """``coalesce(sum(expression) FILTER (WHERE condition), 0)`` as Money."""
    return func.coalesce(
        func.sum(expression).filter(condition),
        literal(Money.zero(), MoneyType()),
        type_=MoneyType(),
    )


def _bucket(charges: Any, counted: Any, first: int, last: int | None) -> Any:
    within = charges.c.days >= first
    if last is not None:
        within = and_(within, charges.c.days <= last)
    return _sum_where(charges.c.open, and_(counted, within))


def _aggregate(
    families: Select[*tuple[Any, ...]], *, today: date, min_days: int
) -> Any:
    """One row per account with counted charges (the module docstring)."""
    charges = _open_charges(today)
    zero = literal(Money.zero(), MoneyType())
    counted = and_(charges.c.open > zero, charges.c.days >= min_days)
    family = families.subquery("families")
    per_account = (
        select(
            charges.c.account_id,
            _sum_where(charges.c.open, counted).label("overdue_total"),
            func.count().filter(counted).label("overdue_charges"),
            func.min(charges.c.due_date).filter(counted).label("oldest_due_date"),
            *(
                _bucket(charges, counted, first, last).label(name)
                for name, first, last in BUCKETS
            ),
            money_sum(charges.c.open).label("outstanding"),
        )
        .group_by(charges.c.account_id)
        .having(func.count().filter(counted) > 0)
        .subquery("per_account")
    )
    credit = (
        select(money_sum(payment_remainder_column()))
        .where(Payment.billing_account_id == per_account.c.account_id, ~_reversed())
        .scalar_subquery()
    )
    reminded = (
        select(func.max(ChargeReminder.sent_at))
        .join(Charge, Charge.id == ChargeReminder.charge_id)
        .where(Charge.billing_account_id == per_account.c.account_id)
        .scalar_subquery()
    )
    days_overdue = type_coerce(
        literal(today) - per_account.c.oldest_due_date, Integer()
    )
    return select(
        per_account,
        BillingAccount.family_id,
        family.c.name.label("family_name"),
        family.c.is_active.label("family_is_active"),
        days_overdue.label("days_overdue"),
        type_coerce(credit, MoneyType()).label("account_credit"),
        reminded.label("last_reminded_at"),
    ).select_from(
        per_account.join(
            BillingAccount, BillingAccount.id == per_account.c.account_id
        ).join(family, family.c.id == BillingAccount.family_id)
    )


def _buckets(row: Any, prefix: str = "") -> AgingBuckets:
    return AgingBuckets(
        **{name: Money(getattr(row, f"{prefix}{name}")) for name, _, _ in BUCKETS}
    )


async def overdue_accounts(
    session: AsyncSession,
    *,
    families: Select[*tuple[Any, ...]],
    today: date,
    min_days: int,
    page: OffsetParams,
) -> OverdueAccountPage:
    """A page of the accounts with overdue charges and the totals over the
    filtered set (the module docstring)."""
    rows_of = _aggregate(families, today=today, min_days=min_days).subquery("rows")
    totals = [
        func.count().over().label("total_accounts"),
        func.sum(rows_of.c.overdue_total).over().label("total_overdue"),
        *(
            func.sum(getattr(rows_of.c, name)).over().label(f"total_{name}")
            for name, _, _ in BUCKETS
        ),
    ]
    rows = (
        await session.execute(
            select(rows_of, *totals)
            .order_by(
                rows_of.c.days_overdue.desc(),
                rows_of.c.overdue_total.desc(),
                rows_of.c.family_name,
                rows_of.c.account_id,
            )
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    if rows:
        first = rows[0]
        summary = OverdueTotals(
            accounts=int(first.total_accounts),
            overdue_total=Money(first.total_overdue),
            buckets=_buckets(first, "total_"),
        )
    else:
        summary = await _totals(session, rows_of) if page.offset else _no_totals()
    items = tuple(
        OverdueAccountRow(
            account_id=row.account_id,
            family_id=row.family_id,
            family_name=row.family_name,
            family_is_active=row.family_is_active,
            overdue_total=Money(row.overdue_total),
            overdue_charges=int(row.overdue_charges),
            oldest_due_date=row.oldest_due_date,
            days_overdue=int(row.days_overdue),
            buckets=_buckets(row),
            outstanding=Money(row.outstanding),
            account_credit=Money(row.account_credit),
            net_balance=Money(row.outstanding) - Money(row.account_credit),
            last_reminded_at=row.last_reminded_at,
        )
        for row in rows
    )
    return OverdueAccountPage(items=items, totals=summary)


def _no_totals() -> OverdueTotals:
    zero = Money.zero()
    return OverdueTotals(
        accounts=0,
        overdue_total=zero,
        buckets=AgingBuckets(
            days_1_30=zero, days_31_60=zero, days_61_90=zero, days_over_90=zero
        ),
    )


async def _totals(session: AsyncSession, rows_of: Any) -> OverdueTotals:
    row = (
        await session.execute(
            select(
                func.count().label("total_accounts"),
                money_sum(rows_of.c.overdue_total).label("total_overdue"),
                *(
                    money_sum(getattr(rows_of.c, name)).label(f"total_{name}")
                    for name, _, _ in BUCKETS
                ),
            )
        )
    ).one()
    return OverdueTotals(
        accounts=int(row.total_accounts),
        overdue_total=Money(row.total_overdue),
        buckets=_buckets(row, "total_"),
    )
