"""Packages under concurrency (#94 G8, D2, D3, E3; I8, I9; the #276/#91
F13 lock-test pattern): transaction A runs its step, which takes the billing
account ``FOR UPDATE``, and holds it; B starts once A holds the lock and
waits for A's commit, then decides from what A committed.

- an approval's consumption holding the account vs the sweeper's step for
  the same key (its coverage read made empty, as if it classified before
  the purchase): B is ``package_covered``, no charge;
- the reverse: the sweeper's charge holding vs the approval: ``charged``,
  no consumption;
- two approvals competing for the last minutes: one ``consumed``, one
  ``short``;
- an approval vs the expiry of the same package: the expired remainder is
  computed after the consumption (Σ units = 0);
- a purchase vs an approval for the same family: no deadlock, and the
  approval consumes the new package.

Each case runs 3 times."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing_service
from app.modules.scheduling import service as scheduling
from app.workflows import packages, session_charges
from app.workflows.package_schemas import PackagePurchase
from tests.conftest import frozen_clock
from tests.integration import package_support as ps
from tests.integration.conftest import short_lived_engine
from tests.integration.lifecycle_support import in_uow

DAY = date(2031, 3, 10)
SOLD = date(2031, 3, 1)
FLOOR = date(2031, 1, 1)
HOLD_SECONDS = 0.5
REPEATS = 3
ZONE = "America/Mexico_City"

Step = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    ps.reset(module_seeded_url)


async def _hold_then(url: str, first: Step, second: Step) -> tuple[Any, Any]:
    """A runs ``first`` and holds its transaction; B runs ``second`` once A
    has run its step. Returns both results."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    holding = asyncio.Event()

    async def run_a() -> Any:
        try:
            async with UnitOfWork(create_sessionmaker(engine_a)) as uow:
                result = await first(uow.session)
                holding.set()
                await asyncio.sleep(HOLD_SECONDS)
                return result
        finally:
            holding.set()

    async def run_b() -> Any:
        await holding.wait()
        async with UnitOfWork(create_sessionmaker(engine_b)) as uow:
            return await second(uow.session)

    try:
        a, b = await asyncio.gather(run_a(), run_b())
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    return a, b


def _consume(class_session: uuid.UUID, student: uuid.UUID) -> Step:
    async def _run(session: AsyncSession) -> str:
        locked = await scheduling.lock_session_for_billing(session, class_session)
        assert locked is not None
        result = await billing_service.consume_session_credit(
            session,
            billing_service.SessionCreditClaim(
                class_session_id=locked.id,
                student_id=student,
                subject_id=locked.subject_id,
                service_date=locked.starts_at.astimezone(ps.ZONE).date(),
                scheduled_minutes=locked.scheduled_minutes,
            ),
        )
        return result.outcome

    return _run


def _sweep_step(class_session: uuid.UUID, student: uuid.UUID) -> Step:
    async def _run(session: AsyncSession) -> str:
        return await session_charges.post_one(
            session, class_session, student, zone=ZONE
        )

    return _run


def _no_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty(session: AsyncSession, items: Any) -> set[Any]:
        del session, items
        return set()

    monkeypatch.setattr(billing_service, "package_covered_keys", empty)


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_consumption_holding_the_account_wins_over_the_sweeper(
    module_seeded_url, monkeypatch, attempt
):
    url = module_seeded_url
    w = ps.world(url)
    ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)
    ps.set_floor(url, FLOOR)
    _no_coverage(monkeypatch)

    consumed, swept = asyncio.run(
        _hold_then(url, _consume(session, w.student), _sweep_step(session, w.student))
    )

    assert (consumed, swept) == ("consumed", "package_covered")
    assert ps.session_charges(url, session) == []
    assert len(ps.consumes(url, session)) == 1


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_charge_holding_the_account_wins_over_the_approval(
    module_seeded_url, monkeypatch, attempt
):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url), SOLD)
    session = ps.session_for(url, w, DAY)
    ps.set_floor(url, FLOOR)
    _no_coverage(monkeypatch)

    swept, consumed = asyncio.run(
        _hold_then(url, _sweep_step(session, w.student), _consume(session, w.student))
    )

    assert (swept, consumed) == ("posted", "charged")
    assert len(ps.session_charges(url, session)) == 1
    assert ps.consumes(url, session) == []
    assert ps.remaining(url, package) == 10


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_approvals_compete_for_the_last_minutes(module_seeded_url, attempt):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(
        url, w.student, ps.product(url, unit="minutes", quantity=90), SOLD
    )
    first = ps.session_for(url, w, DAY)
    second = ps.session_for(url, w, date(2031, 3, 11))

    a, b = asyncio.run(
        _hold_then(url, _consume(first, w.student), _consume(second, w.student))
    )

    assert (a, b) == ("consumed", "short")
    assert ps.remaining(url, package) == 30
    assert ps.consumes(url, second) == []


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_an_expiry_waits_for_a_consumption(module_seeded_url, attempt):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(url, w.student, ps.product(url, quantity=5), SOLD)
    session = ps.session_for(url, w, DAY)

    async def _expire(session_: AsyncSession) -> str:
        return await billing_service.expire_package(session_, package)

    consumed, expired = asyncio.run(
        _hold_then(url, _consume(session, w.student), _expire)
    )

    assert (consumed, expired) == ("consumed", "expired")
    kinds = [(t.kind, t.units) for t in ps.transactions(url, package)]
    assert kinds == [("grant", 5), ("consume", -1), ("expire", -4)]
    assert ps.remaining(url, package) == 0


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_purchase_and_an_approval_dont_deadlock(module_seeded_url, attempt):
    url = module_seeded_url
    w = ps.world(url)
    in_uow(url, lambda s: billing_service.ensure_billing_account(s, w.family))
    product = ps.product(url)
    session = ps.session_for(url, w, DAY)

    async def _purchase(session_: AsyncSession) -> uuid.UUID:
        detail = await packages.purchase_package(
            session_, PackagePurchase(student_id=w.student, package_product_id=product)
        )
        return detail.id

    with frozen_clock(ps.local(SOLD, 12)):
        package, consumed = asyncio.run(
            asyncio.wait_for(
                _hold_then(url, _purchase, _consume(session, w.student)), timeout=20
            )
        )

    assert consumed == "consumed"
    [row] = ps.consumes(url, session)
    assert row.package_id == package
