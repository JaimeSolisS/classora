"""Unit of Work: one transaction per request or job (architecture §4.2).

Outside FastAPI (jobs, seeds, scripts)::

    async with UnitOfWork(sessionmaker) as uow:
        uow.session.add(thing)

In a route or dependency, ask for :data:`UnitOfWorkDep`; every dependency and
service in the request gets the same Unit of Work and session.

On a normal exit the Unit of Work commits; on any exception it rolls back and
re-raises. In both cases the session is closed and its connection returns to
the pool. Only the Unit of Work commits: services and repositories never call
``commit()``. SQLAlchemy's ``StaleDataError`` (a versioned update that matched
no row) is raised as :class:`~app.core.models.StaleVersionError`.
"""

import logging
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Annotated, Self

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.core.db import get_sessionmaker
from app.core.models import StaleVersionError

logger = logging.getLogger(__name__)


class UnitOfWork:
    """One ``AsyncSession`` and its transaction."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active; use 'async with'.")
        return self._session

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("UnitOfWork is already active.")
        self._session = self._sessionmaker()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self.session
        try:
            if exc is None:
                await self._commit(session)
            else:
                await self._rollback(session)
                if isinstance(exc, StaleDataError):
                    raise StaleVersionError() from exc
        finally:
            self._session = None
            await session.close()

    async def _commit(self, session: AsyncSession) -> None:
        try:
            await session.commit()
        except BaseException as exc:
            await self._rollback(session)
            if isinstance(exc, StaleDataError):
                raise StaleVersionError() from exc
            raise

    @staticmethod
    async def _rollback(session: AsyncSession) -> None:
        try:
            await session.rollback()
        except Exception:
            # Never hide the original error; close() still releases the connection.
            logger.warning("rollback failed", exc_info=True)


async def get_uow(
    request: Request,
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
) -> AsyncIterator[UnitOfWork]:
    """FastAPI dependency; use it through :data:`UnitOfWorkDep`.

    It must run with ``scope="function"``: the commit then happens when the
    route function returns and before the response is sent, so a failed commit
    becomes an error response, never a ``2xx``.
    """
    if getattr(request.state, "uow_active", False):
        # Two Unit of Work instances in one request would split its transaction
        # (for example a Depends(get_uow) without scope="function").
        raise RuntimeError("Use UnitOfWorkDep: a request has only one UnitOfWork.")
    request.state.uow_active = True
    async with UnitOfWork(sessionmaker) as uow:
        yield uow


UnitOfWorkDep = Annotated[UnitOfWork, Depends(get_uow, scope="function")]
