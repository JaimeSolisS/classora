"""Due dates: the family offset and changes to a posted charge's due date
(#97 C, D; N5, N6; ADR-0006 E11; data-model §3.1, §3.11).

**Family offset** (:func:`update_due_offset`, N6 (a)):
``billing_accounts.due_offset_days`` (0–60) is added to a charge's due-date
base when it is posted (``post_charge`` reads it under the account lock),
so a change applies only to charges posted **afterwards**; posted due dates
never move. A family without an account yet sends ``version: null`` and the
account is created first; a non-null version for it, or ``null`` for a
family that has an account, is ``409 version_conflict``. The account row is
taken ``FOR UPDATE`` (the lock ``post_charge`` takes, so a racing posting
sees the old or the new offset, never a torn value), then its version is
checked; sending the current value writes nothing.

**Due-date changes** (:func:`record_due_date_change`, N5 (a)): the account
row ``FOR UPDATE`` (a change reorders #96's oldest-first allocation and
flips statuses) → the charge's balance and effective due date re-read under
it → ``paid``/``void`` → ``409 charge_settled`` → the same date as the
effective one → ``409 due_date_unchanged`` → one append-only, audited
``charge_due_date_changes`` row with ``previous_due_date`` (the effective
date before it) and the Unit of Work's actor. Any date from 2000-01-01 to
2099-12-31 is allowed, earlier ones included (correcting a mistake).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import current_actor_id
from app.core.models import StaleVersionError, ensure_version
from app.modules.billing.balances import charge_balances, charge_status
from app.modules.billing.errors import ChargeSettledError, DueDateUnchangedError
from app.modules.billing.ledger import ensure_billing_account, lock_billing_account
from app.modules.billing.models import BillingAccount, Charge, ChargeDueDateChange
from app.modules.billing.schemas import (
    AccountSettings,
    DueDateChangeRow,
    RecordedDueDateChange,
)

SETTLED = ("paid", "void")


async def update_due_offset(
    session: AsyncSession,
    family_id: uuid.UUID,
    *,
    version: int | None,
    due_offset_days: int,
) -> AccountSettings:
    """Set the family's due-date offset (#97 C1; the module docstring). The
    family must exist (the caller checks it)."""
    existing = await session.scalar(
        select(BillingAccount.id).where(BillingAccount.family_id == family_id)
    )
    if (existing is None) != (version is None):
        raise StaleVersionError()
    account_id = (
        existing
        if existing is not None
        else await ensure_billing_account(session, family_id)
    )
    account = await lock_billing_account(session, account_id)
    if version is not None:
        ensure_version(account, version)
    if account.due_offset_days != due_offset_days:
        account.due_offset_days = due_offset_days
        await session.flush()
    return AccountSettings(
        family_id=family_id,
        account_id=account.id,
        due_offset_days=account.due_offset_days,
        version=account.version,
    )


async def charge_account_of_family(
    session: AsyncSession, charge_id: uuid.UUID, family_id: uuid.UUID
) -> uuid.UUID | None:
    """The account id of the charge when it is on the family's account,
    else ``None`` (an unknown family included). One statement, no lock."""
    found: uuid.UUID | None = await session.scalar(
        select(Charge.billing_account_id)
        .join(BillingAccount, BillingAccount.id == Charge.billing_account_id)
        .where(Charge.id == charge_id, BillingAccount.family_id == family_id)
    )
    return found


def _row(change: ChargeDueDateChange) -> DueDateChangeRow:
    return DueDateChangeRow(
        id=change.id,
        charge_id=change.charge_id,
        previous_due_date=change.previous_due_date,
        due_date=change.due_date,
        reason=change.reason,
        changed_by_user_id=change.changed_by_user_id,
        created_at=change.created_at,
    )


async def record_due_date_change(
    session: AsyncSession,
    account_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    due_date: date,
    reason: str,
    today: date,
) -> RecordedDueDateChange:
    """Change the effective due date of a charge of the account (#97 D1;
    the module docstring order). ``today`` is the center's local date (for
    the statuses). Needs the Unit of Work's actor; a charge not on the
    account is a :class:`ValueError` (the caller answers ``404`` first)."""
    actor = current_actor_id(session)
    if actor is None:
        raise ValueError("a due-date change needs an actor")
    await lock_billing_account(session, account_id)
    found = await charge_balances(session, account_id, charge_id=charge_id, today=today)
    if not found:
        raise ValueError("unknown charge on this account")
    [charge] = found
    if charge.status in SETTLED:
        raise ChargeSettledError()
    if due_date == charge.effective_due_date:
        raise DueDateUnchangedError(due_date)
    change = ChargeDueDateChange(
        charge_id=charge.charge_id,
        billing_account_id=account_id,
        previous_due_date=charge.effective_due_date,
        due_date=due_date,
        reason=reason,
        changed_by_user_id=actor,
        created_at=clock.now(),
    )
    session.add(change)
    await session.flush()
    return RecordedDueDateChange(
        change=_row(change),
        status=charge_status(charge.net, charge.allocated, due_date, today),
    )


async def list_due_date_changes(
    session: AsyncSession, charge_id: uuid.UUID
) -> list[DueDateChangeRow]:
    """Every due-date change of the charge, oldest first (``created_at``,
    ``id``). One statement, no lock."""
    rows = await session.scalars(
        select(ChargeDueDateChange)
        .where(ChargeDueDateChange.charge_id == charge_id)
        .order_by(ChargeDueDateChange.created_at, ChargeDueDateChange.id)
    )
    return [_row(row) for row in rows]
