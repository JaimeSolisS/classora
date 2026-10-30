"""The reminder log (``charge_reminders``; #97 F3, F4; N1, N4 (a);
data-model §3.27): what the reminder job reads and writes in the billing
tables. The pure stage rules are in :mod:`app.modules.billing.reminders`;
the job (recipients, notifications, Units of Work) is
``app.workflows.charge_reminders``.

- :func:`reminder_candidate_accounts`: a keyset page of the accounts with at
  least one charge whose open amount is above zero, whose stage today is
  not ``None`` and that has no ``charge_reminders`` row for that stage. One
  statement; no lock (the job re-reads under the account's share lock).
- :func:`charges_to_remind`: the same rule for one account, read **after**
  the caller took ``share_lock_billing_account``, so a payment or a
  due-date change committed meanwhile is seen. Two statements.
- :func:`record_reminders`: ``INSERT … ON CONFLICT (charge_id, stage) DO
  NOTHING RETURNING``, a Core insert (``charge_reminders`` is not audited),
  returning only the rows inserted now, so a concurrent run that inserted
  first wins and this one notifies nothing for them.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import Integer, case, literal, select, tuple_, type_coerce
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money, MoneyType, money_sum
from app.modules.billing.balances import (
    _valid_allocated,
    charge_balances,
    effective_due_date_column,
)
from app.modules.billing.models import (
    REMINDER_KEY,
    Charge,
    ChargeAdjustment,
    ChargeReminder,
    PaymentAllocation,
)
from app.modules.billing.reminders import (
    DUE_SOON,
    DUE_SOON_LEAD_DAYS,
    FOLLOWUP_AFTER_DAYS,
    OVERDUE,
    OVERDUE_FOLLOWUP,
    reminder_stage,
)
from app.modules.billing.schemas import ReminderCharge

ACCOUNT_PAGE: Final = 200
"""Accounts per selection page (#97 F3)."""


def _stage_column(days: Any) -> Any:
    """:func:`~app.modules.billing.reminders.reminder_stage` in SQL, from
    ``days = today − effective due date``."""
    return case(
        (days < -DUE_SOON_LEAD_DAYS, None),
        (days <= 0, DUE_SOON),
        (days < FOLLOWUP_AFTER_DAYS, OVERDUE),
        else_=OVERDUE_FOLLOWUP,
    )


async def reminder_candidate_accounts(
    session: AsyncSession,
    *,
    today: date,
    after: uuid.UUID | None = None,
    limit: int = ACCOUNT_PAGE,
) -> list[uuid.UUID]:
    """The next page of accounts to remind, by account id after ``after``
    (the module docstring). One statement, no lock."""
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .scalar_subquery()
    )
    allocated = _valid_allocated(PaymentAllocation.charge_id == Charge.id)
    days = type_coerce(literal(today) - effective_due_date_column(), Integer())
    charges = select(
        Charge.id.label("charge_id"),
        Charge.billing_account_id.label("account_id"),
        type_coerce(Charge.amount + adjustments - allocated, MoneyType()).label("open"),
        _stage_column(days).label("stage"),
    ).subquery("reminder_charges")
    sent = (
        select(ChargeReminder.id)
        .where(
            ChargeReminder.charge_id == charges.c.charge_id,
            ChargeReminder.stage == charges.c.stage,
        )
        .exists()
    )
    query = select(charges.c.account_id).where(
        charges.c.open > literal(Money.zero(), MoneyType()),
        charges.c.stage.is_not(None),
        ~sent,
    )
    if after is not None:
        query = query.where(charges.c.account_id > after)
    rows = await session.scalars(
        query.group_by(charges.c.account_id).order_by(charges.c.account_id).limit(limit)
    )
    return list(rows)


async def charges_to_remind(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> list[ReminderCharge]:
    """The account's charges with something open whose stage today is not
    yet recorded, by effective due date then id. Call it after
    ``share_lock_billing_account``. Two statements (one when nothing is
    open in a window)."""
    candidates = []
    for charge in await charge_balances(session, account_id, today=today):
        if not charge.open.is_positive():
            continue
        stage = reminder_stage(charge.effective_due_date, today)
        if stage is not None:
            candidates.append(
                ReminderCharge(
                    charge_id=charge.charge_id,
                    stage=stage,
                    due_date=charge.effective_due_date,
                    open=charge.open,
                )
            )
    if not candidates:
        return []
    found = await session.execute(
        select(ChargeReminder.charge_id, ChargeReminder.stage).where(
            tuple_(ChargeReminder.charge_id, ChargeReminder.stage).in_(
                [(c.charge_id, c.stage) for c in candidates]
            )
        )
    )
    sent = {(row.charge_id, row.stage) for row in found}
    return sorted(
        (c for c in candidates if (c.charge_id, c.stage) not in sent),
        key=lambda c: (c.due_date, c.charge_id),
    )


async def record_reminders(
    session: AsyncSession, charges: Sequence[ReminderCharge], *, sent_at: datetime
) -> list[ReminderCharge]:
    """Insert one ``charge_reminders`` row per charge and stage, skipping the
    keys another transaction recorded; returns the charges inserted now
    (none for no charges)."""
    if not charges:
        return []
    statement = (
        insert(ChargeReminder)
        .values(
            [
                {
                    "charge_id": c.charge_id,
                    "stage": c.stage,
                    "due_date": c.due_date,
                    "sent_at": sent_at,
                }
                for c in charges
            ]
        )
        .on_conflict_do_nothing(constraint=REMINDER_KEY)
        .returning(ChargeReminder.charge_id, ChargeReminder.stage)
    )
    inserted = {(row.charge_id, row.stage) for row in await session.execute(statement)}
    return [c for c in charges if (c.charge_id, c.stage) in inserted]
