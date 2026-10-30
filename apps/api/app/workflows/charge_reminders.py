"""In-app payment reminders to families (#97 F2-F7; N1-N4 (a); spec §1
"in-app reminders to families", §6, §9; ADR-0003, ADR-0006 E11, ADR-0015;
``_docs/data-model.md`` §3.27, §6).

**Stages** (:mod:`app.modules.billing.reminders`): ``due_soon`` (D−3..D),
``overdue`` (D+1..D+13), ``overdue_followup`` (D+14 and later), with D the
charge's **effective** due date and "today" the center's local date at the
run's ``now``. A charge gets only the stage of the window it is in now, at
most once per ``(charge_id, stage)``: no catch-up of a missed window, and
no re-send after a due-date change.

**Selection** (F3): keyset pages of 200 accounts (by id, until exhausted)
with a charge whose open amount is above zero, whose stage today is not
``None`` and that has no ``charge_reminders`` row for it.

**Per account, its own Unit of Work** (``actor_id=None``; F4):

1. ``billing_settings`` ``FOR SHARE``; billing off (``billing_starts_on IS
   NULL``) stops the run (``billing_off``), so turning billing off mid-run
   stops it;
2. the account row ``FOR SHARE`` (``billing.service.
   share_lock_billing_account``): it waits for the payments, adjustments,
   due-date changes and postings that hold it ``FOR UPDATE``;
3. the account's charges re-read under that lock: open > 0, a stage today,
   and no row yet for it; none → ``nothing_due``;
4. the recipients (``families.service.family_billing_recipients``: the
   logins of the active guardian profiles in the **account's** family,
   N3); none → ``no_recipient``, nothing written (the charge is
   reconsidered on the next run while it is still in a window);
5. ``INSERT … ON CONFLICT (charge_id, stage) DO NOTHING RETURNING``
   (``charge_reminders`` is not audited);
6. per stage of the rows inserted **now** and per recipient, one
   ``billing.payment_reminder`` notification ``{family_id, kind: <stage>,
   count, open_total, due_on}`` (N2: count, the open total and the
   earliest effective due date of those charges; no names, no per-charge
   amounts);
7. commit: the rows and the notifications exist together or not at all.

A failing account is logged with its id and the error class, rolled back,
and never blocks the others; the next run retries it. Inactive families
and students are still reminded while money is open; a charge stays with
its account when a student changes family, so the account's family is
told.

**Preferences** (F7, N3): none are applied here. Spec §9's in-app
notification preferences are #119 (M8-02), which should treat
``billing.payment_reminder`` as optional and apply the preference in this
module.

**Counts** (one info log line, counts only): ``sent_charges`` (rows
inserted), ``notifications``, ``no_recipient``, ``nothing_due``,
``failed``, ``billing_off`` (1 when the run stopped because billing is
off).

**Run by hand** (:func:`run_send_charge_reminders` builds its own direct
engine and disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.charge_reminders import run_send_charge_reminders as run; \\
    print(asyncio.run(run(get_settings())))"
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import clock
from app.core.config import Settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.families import service as families
from app.workflows.charge_pricing import local_date

logger = logging.getLogger(__name__)

PAYMENT_REMINDER: Final = "billing.payment_reminder"


class PaymentReminderParams(BaseModel):
    """``billing.payment_reminder`` (#97 F6, N2): one stage of one account's
    charges reminded in a run. ``open_total`` is a money string;
    ``due_on`` the earliest effective due date among them."""

    model_config = ConfigDict(extra="forbid")

    family_id: uuid.UUID
    kind: Literal["due_soon", "overdue", "overdue_followup"]
    count: int = Field(ge=1)
    open_total: Money
    due_on: date


def register_reminder_notifications() -> None:
    """Register ``billing.payment_reminder`` (again: a no-op); ``app.wiring``
    and the job call it. An ordinary type: #119 may let users turn it
    off."""
    administration.register_notification_type(PAYMENT_REMINDER, PaymentReminderParams)


@dataclass(frozen=True)
class ChargeReminderRunResult:
    """The counts of one run (also its log line)."""

    sent_charges: int = 0
    notifications: int = 0
    no_recipient: int = 0
    nothing_due: int = 0
    failed: int = 0
    billing_off: int = 0


type AccountOutcome = Literal["sent", "no_recipient", "nothing_due", "billing_off"]


@dataclass(frozen=True)
class AccountRun:
    """What one account's Unit of Work did."""

    outcome: AccountOutcome
    sent_charges: int = 0
    notifications: int = 0


async def remind_account(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> AccountRun:
    """Steps 1–6 of the module docstring for one account, in the caller's
    Unit of Work."""
    if await billing.billing_starts_on(session, lock=True) is None:
        return AccountRun("billing_off")
    family_id = await billing.share_lock_billing_account(session, account_id)
    if family_id is None:  # pragma: no cover - accounts are never deleted
        return AccountRun("nothing_due")
    due = await billing.charges_to_remind(session, account_id, today=today)
    if not due:
        return AccountRun("nothing_due")
    recipients = (await families.family_billing_recipients(session, [family_id])).get(
        family_id, []
    )
    if not recipients:
        return AccountRun("no_recipient")
    inserted = await billing.record_reminders(session, due, sent_at=clock.now())
    by_stage: defaultdict[str, list[billing.ReminderCharge]] = defaultdict(list)
    for charge in inserted:
        by_stage[charge.stage].append(charge)
    notifications = 0
    for stage in billing.REMINDER_STAGES:
        charges = by_stage.get(stage)
        if not charges:
            continue
        params = PaymentReminderParams(
            family_id=family_id,
            kind=stage,
            count=len(charges),
            open_total=Money.total(c.open for c in charges),
            due_on=min(c.due_date for c in charges),
        )
        for user_id in recipients:
            await administration.create_notification(
                session,
                recipient_user_id=user_id,
                notification_type=PAYMENT_REMINDER,
                params=params,
            )
            notifications += 1
    return AccountRun("sent", sent_charges=len(inserted), notifications=notifications)


async def run_send_charge_reminders(
    settings: Settings, *, now: datetime | None = None
) -> ChargeReminderRunResult:
    """One run (module docstring). ``now`` (default
    :func:`app.core.clock.now`) gives the center's local ``today``."""
    register_reminder_notifications()
    engine = create_worker_engine(settings)
    counts = dict.fromkeys(
        (
            "sent_charges",
            "notifications",
            "no_recipient",
            "nothing_due",
            "failed",
            "billing_off",
        ),
        0,
    )
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            zone = await administration.get_center_time_zone(uow.session)
            off = await billing.billing_starts_on(uow.session) is None
        if off:
            counts["billing_off"] = 1
        else:
            today = local_date(now or clock.now(), zone)
            await _run_pages(sessionmaker, today=today, counts=counts)
    finally:
        await engine.dispose()
    result = ChargeReminderRunResult(**counts)
    logger.info(
        "Sent charge reminders",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result


async def _run_pages(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    today: date,
    counts: dict[str, int],
) -> None:
    after: uuid.UUID | None = None
    while True:
        async with UnitOfWork(sessionmaker) as uow:
            page = await billing.reminder_candidate_accounts(
                uow.session, today=today, after=after
            )
        if not page:
            return
        after = page[-1]
        for account_id in page:
            outcome = await _remind(sessionmaker, account_id, today=today)
            if outcome is None:
                counts["failed"] += 1
                continue
            if outcome.outcome == "billing_off":
                counts["billing_off"] = 1
                return
            if outcome.outcome != "sent":
                counts[outcome.outcome] += 1
            counts["sent_charges"] += outcome.sent_charges
            counts["notifications"] += outcome.notifications
        if len(page) < billing.ACCOUNT_PAGE:
            return


async def _remind(
    sessionmaker: async_sessionmaker[AsyncSession],
    account_id: uuid.UUID,
    *,
    today: date,
) -> AccountRun | None:
    """One account in its own Unit of Work; ``None`` when it failed (rolled
    back, logged, retried by the next run)."""
    try:
        async with UnitOfWork(sessionmaker, actor_id=None) as uow:
            return await remind_account(uow.session, account_id, today=today)
    except Exception as exc:  # noqa: BLE001 - one account never blocks the rest
        logger.error(
            "Sending charge reminders failed",
            extra={"billing_account_id": str(account_id), "error": type(exc).__name__},
        )
        return None
