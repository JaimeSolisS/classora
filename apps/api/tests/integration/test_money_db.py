"""``MoneyType``, ``money_sum`` and audit rows on PostgreSQL (M6-02 B5, E1-E3).

The model is the test-only :class:`~tests.integration.models.MoneyThing`. It is
``Audited``, so the database is migrated to head (``audit_events`` exists)
and the test-only tables are added.
"""

import asyncio
import random
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import create_sessionmaker
from app.core.money import MONEY_MAX, Money, money_sum
from app.core.uow import UnitOfWork
from tests.audit.assertions import audit_events_for
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.models import MoneyThing, TestBase

pytestmark = pytest.mark.anyio


@pytest.fixture
def money_url(head_url: str) -> str:
    """At head, plus the test-only tables (``money_things``)."""

    async def _create() -> None:
        engine = short_lived_engine(head_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(TestBase.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_create())
    return head_url


@pytest.fixture
async def sessionmaker(
    money_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = short_lived_engine(money_url)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def add_things(sessionmaker, *rows: tuple[Money, Money | None]) -> list:
    async with UnitOfWork(sessionmaker) as uow:
        things = [MoneyThing(amount=amount, refund=refund) for amount, refund in rows]
        uow.session.add_all(things)
        await uow.session.flush()
        return [thing.id for thing in things]


# --- E2: round trip, filters, sums, column type --------------------------------


ROUND_TRIP = [
    (Money(MONEY_MAX), Money("0.01")),
    (-Money(MONEY_MAX), None),
    (Money.zero(), Money("-0.01")),
    (Money("1250.00"), None),
    (Money("-3.50"), Money("12.34")),
]


async def test_round_trip_keeps_money(sessionmaker):
    ids = await add_things(sessionmaker, *ROUND_TRIP)

    async with sessionmaker() as session:
        for thing_id, (amount, refund) in zip(ids, ROUND_TRIP, strict=True):
            thing = await session.get_one(MoneyThing, thing_id)
            assert type(thing.amount) is Money
            assert thing.amount == amount
            assert thing.refund == refund
            assert thing.refund is None or type(thing.refund) is Money


async def test_binding_a_decimal_fails_on_flush(sessionmaker):
    # SQLAlchemy wraps the bind processor's TypeError in a StatementError.
    with pytest.raises(StatementError) as caught:  # noqa: PT012
        async with UnitOfWork(sessionmaker) as uow:
            uow.session.add(MoneyThing(amount=Decimal("1.00")))  # type: ignore[arg-type]
            await uow.session.flush()
    assert isinstance(caught.value.orig, TypeError)
    assert "binds Money only" in str(caught.value.orig)
    async with sessionmaker() as session:
        assert (
            await session.scalar(select(money_sum(MoneyThing.amount))) == Money.zero()
        )


async def test_filters_compare_money(sessionmaker):
    await add_things(
        sessionmaker,
        (Money("4.99"), None),
        (Money("5.00"), None),
        (Money("5.01"), None),
        (Money("1250.00"), None),
    )

    async with sessionmaker() as session:
        found = await session.scalars(
            select(MoneyThing.amount)
            .where(MoneyThing.amount > Money("5.00"))
            .order_by(MoneyThing.amount)
        )
        assert list(found) == [Money("5.01"), Money("1250.00")]


async def test_money_sum_returns_money(sessionmaker):
    async with sessionmaker() as session:
        empty = await session.scalar(select(money_sum(MoneyThing.amount)))
        assert type(empty) is Money
        assert empty == Money.zero()

    await add_things(
        sessionmaker,
        (Money("1250.00"), Money("10.00")),
        (Money("-0.50"), None),
        (Money("0.05"), Money("0.01")),
    )

    async with sessionmaker() as session:
        total = await session.scalar(select(money_sum(MoneyThing.amount)))
        assert type(total) is Money
        assert total == Money("1249.55")
        refunds = await session.scalar(select(money_sum(MoneyThing.refund)))
        assert refunds == Money("10.01")
        none = await session.scalar(
            select(money_sum(MoneyThing.amount)).where(
                MoneyThing.amount > Money("2000")
            )
        )
        assert type(none) is Money
        assert none == Money.zero()
        net = await session.scalar(
            select(money_sum(MoneyThing.amount - MoneyThing.refund))
        )
        assert type(net) is Money
        assert net == Money("1240.04")  # a NULL refund drops its row (SQL)


async def test_column_type_is_numeric_14_2(money_url):
    rows = await run_sql_async(
        money_url,
        "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable"
        " FROM information_schema.columns"
        " WHERE table_name = 'money_things' AND column_name IN ('amount', 'refund')"
        " ORDER BY column_name",
    )
    assert [tuple(row) for row in rows] == [
        ("amount", "numeric", 14, 2, "NO"),
        ("refund", "numeric", 14, 2, "YES"),
    ]


# --- B5: agreement with PostgreSQL round() ------------------------------------


ROUNDING_INPUTS = ["2.345", "-2.345", "2.344999", "0.005", "-0.005", "-0.004", "7"]


async def test_rounded_agrees_with_postgresql_round(money_url):
    rng = random.Random(87)  # noqa: S311 - seeded test data
    limit = 9_999_999_999_999_949  # |x| <= 999999999999.9949: rounds into range
    values = ROUNDING_INPUTS + [
        str(Decimal(rng.randint(-limit, limit)).scaleb(-4)) for _ in range(200)
    ]
    rows = await run_sql_async(
        money_url,
        "SELECT v, round(v::numeric, 2)::text"
        " FROM unnest(CAST(:values AS text[])) WITH ORDINALITY AS t(v, n) ORDER BY n",
        {"values": values},
    )
    assert len(rows) == len(values) == 207
    for value, rounded in rows:
        assert str(Money.rounded(Decimal(value))) == rounded, value


# --- E3: audit rows -----------------------------------------------------------


async def test_audit_rows_carry_money_strings(sessionmaker):
    [thing_id] = await add_things(sessionmaker, (Money("1250.00"), None))

    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(MoneyThing, thing_id)
        thing.amount = Money("-3.5")
        thing.refund = Money("0.10")

    async with sessionmaker() as session:
        [created] = await audit_events_for(session, action="create")
        [updated] = await audit_events_for(session, action="update")
    assert (created.record_type, created.record_id) == ("money_things", thing_id)
    assert created.changes == {
        "amount": {"old": None, "new": "1250.00"},
        "refund": {"old": None, "new": None},
    }
    assert updated.changes == {
        "amount": {"old": "1250.00", "new": "-3.50"},
        "refund": {"old": None, "new": "0.10"},
    }


async def test_an_equal_money_value_writes_no_update_event(sessionmaker):
    [thing_id] = await add_things(sessionmaker, (Money("1250.00"), None))

    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(MoneyThing, thing_id)
        thing.amount = Money("1250")

    async with sessionmaker() as session:
        assert await audit_events_for(session, action="update") == []
