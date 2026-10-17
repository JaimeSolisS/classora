"""``persist`` / ``persist_batch`` against PostgreSQL (M1-17 C1, E2)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import create_sessionmaker
from tests.factories.base import persist, persist_batch
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.models import SampleProfile
from tests.integration.samples import SampleProfileFactory


async def count(dsn: str) -> int:
    rows = await run_sql_async(dsn, "SELECT count(*) FROM sample_profiles")
    return int(rows[0][0])


@pytest.mark.anyio
async def test_persist_flushes_without_committing(tables_url):
    engine = short_lived_engine(tables_url)
    try:
        async with create_sessionmaker(engine)() as session:
            profile = await persist(session, SampleProfileFactory, full_name="Ana")

            assert profile.id.version == 7
            assert profile.created_at.utcoffset() == timedelta(0)
            assert profile.updated_at.utcoffset() == timedelta(0)
            assert profile.full_name == "Ana"
            in_session = await session.scalar(select(func.count(SampleProfile.id)))
            assert in_session == 1
            assert session.in_transaction()
            await session.rollback()
    finally:
        await engine.dispose()

    assert await count(tables_url) == 0


@pytest.mark.anyio
async def test_persist_batch_flushes_without_committing(tables_url):
    engine = short_lived_engine(tables_url)
    try:
        async with create_sessionmaker(engine)() as session:
            profiles = await persist_batch(
                session, SampleProfileFactory, 5, amount=Decimal("1250.00")
            )

            assert len({profile.id for profile in profiles}) == 5
            assert all(profile.id.version == 7 for profile in profiles)
            assert all(profile.created_at.tzinfo for profile in profiles)
            stored = await session.scalars(select(SampleProfile.amount))
            assert list(stored) == [Decimal("1250.00")] * 5
            await session.rollback()
    finally:
        await engine.dispose()

    assert await count(tables_url) == 0


@pytest.mark.anyio
async def test_adr_0004_values_round_trip(tables_url):
    engine = short_lived_engine(tables_url)
    try:
        async with create_sessionmaker(engine)() as session:
            built = await persist(session, SampleProfileFactory)
            values = (built.amount, built.starts_on, built.occurred_at)
            session.expunge_all()
            stored = await session.get_one(SampleProfile, built.id)

            assert (stored.amount, stored.starts_on, stored.occurred_at) == values
            assert stored.amount.as_tuple().exponent == -2
            await session.rollback()
    finally:
        await engine.dispose()
