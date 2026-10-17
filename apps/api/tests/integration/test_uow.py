"""Unit of Work as an async context manager (B1), for jobs, seeds and scripts."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import create_engine, create_sessionmaker
from app.core.uow import UnitOfWork
from tests.integration.conftest import settings_for
from tests.integration.models import DeferredCode, Widget

pytestmark = pytest.mark.anyio


@pytest.fixture
async def sessionmaker(
    tables_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings_for(tables_url))
    try:
        yield create_sessionmaker(engine)
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()


async def widget_names(sessionmaker: async_sessionmaker[AsyncSession]) -> list[str]:
    async with sessionmaker() as session:
        return sorted((await session.scalars(select(Widget.name))).all())


async def test_commits_on_normal_exit(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        uow.session.add(Widget(name="kept"))

    assert await widget_names(sessionmaker) == ["kept"]


async def test_rolls_back_and_reraises_the_original_exception(sessionmaker):
    error = ValueError("original")
    rollbacks: list[object] = []

    with pytest.raises(ValueError) as raised:
        async with UnitOfWork(sessionmaker) as uow:
            # An explicit rollback, not only the implicit one when the
            # connection returns to the pool.
            event.listen(uow.session.sync_session, "after_rollback", rollbacks.append)
            uow.session.add(Widget(name="discarded"))
            await uow.session.flush()
            raise error

    assert raised.value is error
    assert len(rollbacks) == 1
    assert await widget_names(sessionmaker) == []


async def test_failed_commit_raises_and_persists_nothing(sessionmaker):
    with pytest.raises(Exception, match="uq_deferred_codes_code"):
        async with UnitOfWork(sessionmaker) as uow:
            uow.session.add_all([DeferredCode(code="a"), DeferredCode(code="a")])

    async with sessionmaker() as session:
        assert (await session.scalars(select(DeferredCode))).all() == []


async def test_session_is_released_after_exit(sessionmaker):
    uow = UnitOfWork(sessionmaker)
    async with uow:
        session = uow.session
        await session.execute(select(1))
        assert session.in_transaction()

    assert not session.in_transaction()
    with pytest.raises(RuntimeError):
        uow.session  # noqa: B018 - the property refuses outside 'async with'
    assert sessionmaker.kw["bind"].pool.checkedout() == 0
