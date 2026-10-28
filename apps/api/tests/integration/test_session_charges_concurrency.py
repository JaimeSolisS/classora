"""The session charge step against concurrent scheduling changes (#91 F13;
the #276 lock-test pattern; data-model §6).

Connection A takes the session row ``FOR UPDATE`` (as every scheduling
writer that changes a state, time, duration or participants does) and holds
it; the per-candidate step in B (``post_one``, the session ``FOR SHARE``)
waits for A and posts from what A committed. And the other way round: B's
step holds the session ``FOR SHARE`` and a cancellation in A waits for B.
Each case runs 5 times.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.workflows import session_charges
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.session_charge_support import (
    FLOOR,
    PRICE_FROM,
    Item,
    add_price,
    charges_of,
    item,
    local,
    reset,
    set_floor,
)

DAY = FLOOR + timedelta(days=9)
HOLD_SECONDS = 0.5
REPEATS = 5
ZONE = "America/Mexico_City"

LOCK = "SELECT id FROM class_sessions WHERE id = :id FOR UPDATE"
CANCEL = (
    "UPDATE class_sessions SET state = 'cancelled', cancelled_at = now(),"
    " cancellation_reason = 'Cancelada por el centro', version = version + 1"
    " WHERE id = :id"
)
LENGTHEN = (
    "UPDATE class_sessions SET ends_at = starts_at + interval '90 minutes',"
    " scheduled_minutes = 90, version = version + 1 WHERE id = :id"
)


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    reset(module_seeded_url)


async def _post(url: str, it: Item) -> session_charges.ItemOutcome:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await session_charges.post_one(
                uow.session, it.session, it.student, zone=ZONE
            )
    finally:
        await engine.dispose()


async def _writer_first(
    url: str,
    it: Item,
    change: Callable[[AsyncConnection], Awaitable[None]],
    meanwhile: Callable[[], Awaitable[None]] | None = None,
) -> tuple[session_charges.ItemOutcome, float, float]:
    """A locks the session, runs ``change`` and holds; B's step starts once
    A holds the lock. Returns B's outcome, when A started its commit and
    when B finished (monotonic seconds)."""
    engine = short_lived_engine(url)
    holding = asyncio.Event()
    committed_at = 0.0

    async def run_a() -> None:
        nonlocal committed_at
        try:
            async with engine.begin() as conn:
                await conn.execute(text(LOCK), {"id": it.session})
                await change(conn)
                holding.set()
                await asyncio.sleep(HOLD_SECONDS)
                if meanwhile is not None:
                    await meanwhile()
                # Taken just before the commit (the block's exit): B can't
                # get the lock before it, so B always finishes later.
                committed_at = time.monotonic()
        finally:
            holding.set()

    async def run_b() -> tuple[session_charges.ItemOutcome, float]:
        await holding.wait()
        outcome = await _post(url, it)
        return outcome, time.monotonic()

    try:
        _, (outcome, finished_at) = await asyncio.gather(run_a(), run_b())
    finally:
        await engine.dispose()
    return outcome, committed_at, finished_at


async def _nothing(conn: AsyncConnection) -> None:
    del conn


def _change(sql: str, it: Item) -> Callable[[AsyncConnection], Awaitable[None]]:
    async def _run(conn: AsyncConnection) -> None:
        await conn.execute(text(sql), {"id": it.session})

    return _run


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_cancellation_holding_the_session_wins(module_seeded_url, attempt):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY))
    set_floor(url, FLOOR)

    outcome, committed_at, finished_at = asyncio.run(
        _writer_first(url, it, _change(CANCEL, it))
    )

    assert outcome == "not_billable"
    assert finished_at >= committed_at
    assert charges_of(url, it.session) == []


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_new_duration_holding_the_session_is_priced(module_seeded_url, attempt):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY), price="250.00")
    add_price(url, it.subject, [(PRICE_FROM, "375.00")], duration_minutes=90)
    set_floor(url, FLOOR)

    outcome, committed_at, finished_at = asyncio.run(
        _writer_first(url, it, _change(LENGTHEN, it))
    )

    assert outcome == "posted"
    assert finished_at >= committed_at
    [charge] = charges_of(url, it.session)
    assert charge.scheduled_minutes == 90
    assert charge.amount == Money("375.00").amount


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_billing_turned_off_while_waiting_posts_nothing(module_seeded_url, attempt):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY))
    set_floor(url, FLOOR)

    async def _turn_off() -> None:
        # Committed by another connection while B waits on the session.
        await asyncio.to_thread(set_floor, url, None)

    outcome, _, _ = asyncio.run(_writer_first(url, it, _nothing, _turn_off))

    assert outcome == "not_billable"
    assert charges_of(url, it.session) == []


async def _step_first(url: str, it: Item) -> tuple[float, float]:
    """B's step holds the session ``FOR SHARE`` (after posting, before its
    commit); A then cancels. Returns when B started its commit and when A's
    cancel finished."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    holding = asyncio.Event()
    committed_at = 0.0

    async def run_b() -> None:
        nonlocal committed_at
        try:
            async with UnitOfWork(create_sessionmaker(engine_b)) as uow:
                outcome = await session_charges.post_one(
                    uow.session, it.session, it.student, zone=ZONE
                )
                assert outcome == "posted"
                holding.set()
                await asyncio.sleep(HOLD_SECONDS)
                # Taken just before the commit (the block's exit): A's cancel
                # can't get the lock before it, so it always finishes later.
                committed_at = time.monotonic()
        finally:
            holding.set()

    async def run_a() -> float:
        await holding.wait()
        async with engine_a.begin() as conn:
            await conn.execute(text(LOCK), {"id": it.session})
            await conn.execute(text(CANCEL), {"id": it.session})
        return time.monotonic()

    try:
        _, cancelled_at = await asyncio.gather(run_b(), run_a())
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    return committed_at, cancelled_at


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_cancellation_waits_for_the_charge(module_seeded_url, attempt):
    url = module_seeded_url
    it = item(url, starts_at=local(DAY))
    set_floor(url, FLOOR)

    committed_at, cancelled_at = asyncio.run(_step_first(url, it))

    assert cancelled_at >= committed_at
    assert len(charges_of(url, it.session)) == 1
    [row] = run_sql(
        url, "SELECT state FROM class_sessions WHERE id = :id", {"id": it.session}
    )
    # The charge stands until #99 applies the cancellation rules.
    assert row.state == "cancelled"
