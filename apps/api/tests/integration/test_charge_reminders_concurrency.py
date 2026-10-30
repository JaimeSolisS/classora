"""The reminder job under concurrency (#97 I6; N4 (a); the #96 lock-test
pattern): transaction A runs its step and holds it; B starts once A has run
its step, and decides from what A committed.

- a payment holding the account ``FOR UPDATE`` that pays the charge in
  full: the reminder's ``FOR SHARE`` waits, then sends nothing;
- two reminder Units of Work on one account: the natural key decides, the
  second inserts and notifies nothing; two whole runs at once leave one row
  per key and one notification;
- a due-date change racing the reminder: the reminder uses the committed
  effective date.

Each case runs 5 times."""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.schemas import AllocationRequest, PaymentDraft
from app.workflows import charge_reminders
from tests.integration import due_date_support as dd
from tests.integration.conftest import settings_for, short_lived_engine

HOLD_SECONDS = 0.5
REPEATS = 5
TODAY = dd.TODAY

Step = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    dd.reset(module_seeded_url)


async def _hold_then(
    url: str, actor: uuid.UUID | None, first: Step, second: Step
) -> tuple[Any, Any, float, float]:
    """A runs ``first`` (as ``actor``) and holds its transaction for
    :data:`HOLD_SECONDS`; B (no actor, like the job) runs ``second`` once A
    has run its step. Returns both results, A's release time (taken inside
    its transaction, just before the commit) and when B's step returned
    (inside its transaction). B's step blocks on A's lock until A commits,
    so ``b_done >= a_releasing`` holds exactly when B waited for A; both
    marks are taken before either commit, so they never race the commit."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    holding = asyncio.Event()
    ended: dict[str, float] = {}

    async def run_a() -> Any:
        try:
            async with UnitOfWork(create_sessionmaker(engine_a), actor) as uow:
                result = await first(uow.session)
                holding.set()
                await asyncio.sleep(HOLD_SECONDS)
                ended["a"] = time.monotonic()
            return result
        finally:
            holding.set()

    async def run_b() -> Any:
        await holding.wait()
        async with UnitOfWork(create_sessionmaker(engine_b)) as uow:
            result = await second(uow.session)
            ended["b"] = time.monotonic()
        return result

    try:
        a, b = await asyncio.gather(run_a(), run_b())
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    return a, b, ended["a"], ended["b"]


def _remind(account: uuid.UUID) -> Step:
    async def _run(session: AsyncSession) -> charge_reminders.AccountRun:
        return await charge_reminders.remind_account(session, account, today=TODAY)

    return _run


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_a_payment_in_full_wins_over_the_reminder(module_seeded_url, repeat):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, "300.00")

    async def pay(session: AsyncSession) -> Any:
        return await billing.record_payment(
            session,
            PaymentDraft(
                billing_account_id=w.account,
                amount=Money("300.00"),
                received_on=TODAY,
                payment_method_code="cash",
                allocation=AllocationRequest(mode="oldest_first"),
            ),
        )

    _, reminded, ended_a, ended_b = asyncio.run(
        _hold_then(url, w.actor, pay, _remind(w.account))
    )

    assert ended_b >= ended_a  # the share lock waited for the payment
    assert reminded.outcome == "nothing_due"
    assert dd.reminders(url, charge) == []
    assert dd.notifications(url, w.guardians) == []


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_two_reminders_on_one_account_send_once(module_seeded_url, repeat):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student)

    first, second, ended_a, ended_b = asyncio.run(
        _hold_then(url, None, _remind(w.account), _remind(w.account))
    )

    assert (first.sent_charges, first.notifications) == (1, 1)
    assert ended_b >= ended_a  # the second waited on the natural key
    assert (second.sent_charges, second.notifications) == (0, 0)
    assert len(dd.reminders(url, charge)) == 1
    assert len(dd.notifications(url, w.guardians)) == 1


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_two_whole_runs_at_once_send_one_set(module_seeded_url, repeat):
    url = module_seeded_url
    worlds = [dd.world(url) for _ in range(3)]
    charges = [dd.charge(url, w.student) for w in worlds]
    when = dd.local(TODAY)

    async def both() -> list[charge_reminders.ChargeReminderRunResult]:
        settings = settings_for(url)
        return list(
            await asyncio.gather(
                charge_reminders.run_send_charge_reminders(settings, now=when),
                charge_reminders.run_send_charge_reminders(settings, now=when),
            )
        )

    results = asyncio.run(both())

    assert sum(r.sent_charges for r in results) == 3
    assert sum(r.notifications for r in results) == 3
    assert all(len(dd.reminders(url, c)) == 1 for c in charges)
    for w in worlds:
        assert len(dd.notifications(url, w.guardians)) == 1


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_a_racing_due_date_change_is_seen(module_seeded_url, repeat):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=TODAY + dd.days(30))
    moved = TODAY - dd.days(2)

    async def change(session: AsyncSession) -> Any:
        return await billing.record_due_date_change(
            session, w.account, charge, due_date=moved, reason="Acuerdo", today=TODAY
        )

    _, reminded, ended_a, ended_b = asyncio.run(
        _hold_then(url, w.actor, change, _remind(w.account))
    )

    assert ended_b >= ended_a
    assert reminded.outcome == "sent"
    assert dd.reminders(url, charge) == [(charge, "overdue", moved)]
    [(_, params)] = dd.notifications(url, w.guardians)
    assert (params["kind"], params["due_on"]) == ("overdue", moved.isoformat())
