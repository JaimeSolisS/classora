"""Gap-free receipt numbers under concurrency (#98 B1-B6; E13, I10; the
#88/#96 two-connection pattern): every payment runs
``billing.service.record_payment`` in its own Unit of Work and connection.

- B3: N = 8 payments on different accounts plus two on one account, all at
  once: all commit and their numbers are one contiguous range after the
  series value read before (tests share the database);
- B4: a payment that takes number k and then rolls back gives k back: the
  payment waiting on the series row gets k, the next one k + 1, and
  nothing of the rolled back one remains;
- B5: the year is the center-local year of the issue instant
  (America/Mexico_City), each year with its own series row;
- B6: the series row is locked only after the account row: a payment
  waiting for its account holds no series lock, so another account's
  payment goes through meanwhile;
- B1: past 999999 → ``NumberSeriesExhaustedError`` and nothing is written.

The clock is frozen at noon on 2031-03-10 in the center's zone unless a
test moves it."""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.numbering import NumberSeriesExhaustedError
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from tests.conftest import FrozenClock, frozen_clock
from tests.factories import billing as factories
from tests.integration import payment_support as ps
from tests.integration import receipt_support as rs
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow

YEAR = ps.TODAY.year
HOLD_SECONDS = 0.5

Step = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def actor(module_seeded_url: str, frozen: FrozenClock) -> uuid.UUID:
    return in_uow(module_seeded_url, factories.staff_user_id)


def _numbers(recorded: list[Any]) -> list[int]:
    return sorted(int(r.receipt_number[-6:]) for r in recorded)


async def _in_own_uow(url: str, actor: uuid.UUID, step: Step) -> Any:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine), actor) as uow:
            return await step(uow.session)
    finally:
        await engine.dispose()


def _pay(account: uuid.UUID, amount: str = "100.00") -> Step:
    return lambda session: rs.record_in(session, rs.draft(account, amount))


@pytest.mark.parametrize("attempt", range(3))
def test_concurrent_payments_get_one_contiguous_range(
    module_seeded_url, actor, attempt
):
    url = module_seeded_url
    accounts = [rs.new_account(url)[1] for _ in range(8)]
    shared = rs.new_account(url)[1]
    before = rs.series_value(url, YEAR)

    async def _all() -> list[Any]:
        steps = [_pay(a) for a in accounts] + [_pay(shared), _pay(shared, "50.00")]
        return await asyncio.gather(*(_in_own_uow(url, actor, s) for s in steps))

    recorded = asyncio.run(_all())

    assert _numbers(recorded) == list(range(before + 1, before + 11))
    assert rs.series_value(url, YEAR) == before + 10
    assert {r.receipt_number[:7] for r in recorded} == {f"R-{YEAR}-"}
    for account in [*accounts, shared]:
        rows = rs.receipts_of(url, account)
        assert len(rows) == (2 if account == shared else 1)
    numbers = {
        row.number for a in [*accounts, shared] for row in rs.receipts_of(url, a)
    }
    assert numbers == {r.receipt_number for r in recorded}


class _Injected(Exception):
    """The failure injected after ``issue_receipt`` (B4)."""


@pytest.mark.parametrize("attempt", range(3))
def test_a_rolled_back_payment_gives_its_number_back(module_seeded_url, actor, attempt):
    url = module_seeded_url
    _, account_a, _ = rs.new_account(url)
    _, account_b, _ = rs.new_account(url)
    _, account_c, _ = rs.new_account(url)
    k = rs.series_value(url, YEAR) + 1
    holding = asyncio.Event()
    taken: list[str] = []

    async def run_a() -> None:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine), actor) as uow:
                recorded = await rs.record_in(uow.session, rs.draft(account_a))
                taken.append(recorded.receipt_number)
                holding.set()
                await asyncio.sleep(HOLD_SECONDS)
                raise _Injected()
        finally:
            holding.set()
            await engine.dispose()

    async def run_b() -> Any:
        await holding.wait()
        return await _in_own_uow(url, actor, _pay(account_b))

    async def _both() -> Any:
        a, b = await asyncio.gather(run_a(), run_b(), return_exceptions=True)
        assert isinstance(a, _Injected), a
        return b

    b = asyncio.run(_both())
    c = rs.record(url, actor, rs.draft(account_c))

    assert taken == [rs.receipt_number_text(YEAR, k)]
    assert b.receipt_number == rs.receipt_number_text(YEAR, k)
    assert c.receipt_number == rs.receipt_number_text(YEAR, k + 1)
    assert rs.receipts_of(url, account_a) == []
    assert ps.payments_of(url, account_a) == []
    assert rs.series_value(url, YEAR) == k + 1


def test_the_year_is_the_center_local_year(module_seeded_url, actor, frozen):
    url = module_seeded_url
    [settings] = run_sql(url, "SELECT time_zone FROM business_settings")
    assert settings.time_zone == "America/Mexico_City"
    assert rs.series_value(url, 2027) == 0  # this module never used 2027
    _, account, _ = rs.new_account(url)
    old_2026 = rs.series_value(url, 2026)
    try:
        # 2026-12-31 23:30 local is 2027-01-01 05:30 UTC.
        frozen.set(datetime(2026, 12, 31, 23, 30, tzinfo=ps.ZONE))
        late = rs.record(url, actor, rs.draft(account))
        frozen.set(datetime(2027, 1, 1, 0, 10, tzinfo=ps.ZONE))
        early = rs.record(url, actor, rs.draft(account))
    finally:
        frozen.set(ps.NOW)

    assert late.receipt_number == rs.receipt_number_text(2026, old_2026 + 1)
    assert early.receipt_number == "R-2027-000001"
    rows = run_sql(
        url,
        "SELECT year, last_value FROM number_series WHERE code = 'R'"
        " AND year IN (2026, 2027) ORDER BY year",
    )
    assert [(r.year, r.last_value) for r in rows] == [(2026, old_2026 + 1), (2027, 1)]
    [row] = run_sql(
        url,
        "SELECT issued_at FROM receipts WHERE number = :n",
        {"n": late.receipt_number},
    )
    assert row.issued_at.year == 2027  # the instant is already 2027 in UTC


@pytest.mark.parametrize("attempt", range(3))
def test_the_series_lock_comes_after_the_account_lock(
    module_seeded_url, actor, attempt
):
    url = module_seeded_url
    _, account_x, _ = rs.new_account(url)
    _, account_y, _ = rs.new_account(url)
    holding = asyncio.Event()
    ended: dict[str, float] = {}

    async def run_a() -> None:
        # A holds account X (as a payment on X would) for a while.
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine), actor) as uow:
                await billing.lock_billing_account(uow.session, account_x)
                holding.set()
                await asyncio.sleep(HOLD_SECONDS * 2)
            ended["a"] = time.monotonic()
        finally:
            holding.set()
            await engine.dispose()

    async def run(name: str, account: uuid.UUID, delay: float) -> Any:
        await holding.wait()
        await asyncio.sleep(delay)
        result = await _in_own_uow(url, actor, _pay(account))
        ended[name] = time.monotonic()
        return result

    async def _all() -> tuple[Any, ...]:
        return tuple(
            await asyncio.gather(
                run_a(), run("b", account_x, 0.0), run("c", account_y, 0.2)
            )
        )

    _, b, c = asyncio.run(_all())

    # B waits for X without holding the series, so C (account Y) commits
    # while A still holds X; B commits after A, with the next number.
    assert ended["c"] < ended["a"] < ended["b"]
    assert int(b.receipt_number[-6:]) == int(c.receipt_number[-6:]) + 1


def test_past_999999_the_payment_fails_and_nothing_is_written(
    module_seeded_url, actor, frozen
):
    url = module_seeded_url
    run_sql(
        url,
        "INSERT INTO number_series (code, year, last_value) VALUES ('R', 2040, 999999)",
    )
    _, account, _ = rs.new_account(url)
    try:
        frozen.set(datetime(2040, 6, 1, 12, 0, tzinfo=ps.ZONE))
        with pytest.raises(NumberSeriesExhaustedError):
            rs.record(url, actor, rs.draft(account))
    finally:
        frozen.set(ps.NOW)

    assert ps.payments_of(url, account) == []
    assert rs.receipts_of(url, account) == []
    assert rs.series_value(url, 2040) == 999999
