"""Base columns (C2), optimistic version (C3, C4) on test-only models."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import create_engine, create_sessionmaker
from app.core.models import StaleVersionError, ensure_version
from app.core.uow import UnitOfWork
from tests.integration.conftest import settings_for
from tests.integration.models import VersionedWidget, Widget

pytestmark = pytest.mark.anyio


@pytest.fixture
async def sessionmaker(
    tables_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings_for(tables_url))
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def test_orm_insert_gets_uuid7_and_aware_timestamps(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        widget = Widget(name="orm")
        uow.session.add(widget)

    assert isinstance(widget.id, uuid.UUID)
    assert widget.id.version == 7
    assert widget.created_at.tzinfo is not None
    assert widget.updated_at.tzinfo is not None
    assert widget.created_at == widget.updated_at


async def test_raw_sql_insert_gets_uuid7_from_the_server(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        row = (
            await uow.session.execute(
                text(
                    "INSERT INTO widgets (name) VALUES ('raw') "
                    "RETURNING id, created_at, updated_at"
                )
            )
        ).one()

    assert row.id.version == 7
    assert row.created_at.tzinfo is not None
    assert row.updated_at == row.created_at


async def test_update_in_a_later_transaction_moves_only_updated_at(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        widget = Widget(name="before")
        uow.session.add(widget)
    created_at = widget.created_at
    await asyncio.sleep(0.01)

    async with UnitOfWork(sessionmaker) as uow:
        loaded = await uow.session.get_one(Widget, widget.id)
        loaded.name = "after"

    # Read back by the update (eager defaults): no lazy load needed.
    assert loaded.created_at == created_at
    assert loaded.updated_at > loaded.created_at
    async with sessionmaker() as session:
        fresh = await session.get_one(Widget, widget.id)
    assert fresh.created_at == created_at
    assert fresh.updated_at == loaded.updated_at


def test_models_without_the_mixin_have_no_version_column():
    assert "version" not in Widget.__table__.c
    assert "version" in VersionedWidget.__table__.c


async def test_version_starts_at_1_and_increments_by_one(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        widget = VersionedWidget(name="v")
        uow.session.add(widget)
    assert widget.version == 1

    for expected in (2, 3):
        async with UnitOfWork(sessionmaker) as uow:
            loaded = await uow.session.get_one(VersionedWidget, widget.id)
            loaded.name = f"v{expected}"
        assert loaded.version == expected


async def test_raw_sql_insert_starts_at_version_1(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        version = await uow.session.scalar(
            text(
                "INSERT INTO versioned_widgets (name) VALUES ('raw') RETURNING version"
            )
        )
    assert version == 1


async def test_concurrent_update_raises_stale_version_error(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        widget = VersionedWidget(name="original")
        uow.session.add(widget)

    with pytest.raises(StaleVersionError):
        async with UnitOfWork(sessionmaker) as second:
            stale = await second.session.get_one(VersionedWidget, widget.id)
            assert stale.version == 1
            second.session.add(Widget(name="side effect"))

            async with UnitOfWork(sessionmaker) as first:
                current = await first.session.get_one(VersionedWidget, widget.id)
                current.name = "first wins"
            assert current.version == 2

            stale.name = "second loses"

    async with sessionmaker() as session:
        row = await session.get_one(VersionedWidget, widget.id)
    assert (row.name, row.version) == ("first wins", 2)
    # The second transaction was rolled back as a whole.
    async with sessionmaker() as session:
        assert (await session.scalars(select(Widget))).all() == []


async def test_stale_flush_inside_the_unit_of_work_is_translated(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        widget = VersionedWidget(name="original")
        uow.session.add(widget)

    with pytest.raises(StaleVersionError):
        async with UnitOfWork(sessionmaker) as second:
            stale = await second.session.get_one(VersionedWidget, widget.id)
            async with UnitOfWork(sessionmaker) as first:
                (await first.session.get_one(VersionedWidget, widget.id)).name = "x"
            stale.name = "y"
            await second.session.flush()  # StaleDataError here, inside the block


async def test_client_supplied_stale_version_changes_nothing(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        widget = VersionedWidget(name="original")
        uow.session.add(widget)

    with pytest.raises(StaleVersionError):
        async with UnitOfWork(sessionmaker) as uow:
            loaded = await uow.session.get_one(VersionedWidget, widget.id)
            ensure_version(loaded, expected=0)
            loaded.name = "never written"

    async with sessionmaker() as session:
        row = await session.get_one(VersionedWidget, widget.id)
    assert (row.name, row.version) == ("original", 1)
