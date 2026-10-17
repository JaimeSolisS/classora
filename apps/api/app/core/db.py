"""Async SQLAlchemy engine and session factory (spec §10, architecture §4.2).

There is one :class:`~sqlalchemy.ext.asyncio.AsyncEngine` per process. The
FastAPI lifespan (``app.main``) creates it with :func:`create_engine` and
disposes it at shutdown; jobs and scripts create their own the same way.
Creating the engine never opens a connection, so the API starts even when
PostgreSQL is down.

``Settings.database_url`` stays a plain ``postgresql://`` DSN; this module
derives the ``postgresql+asyncpg`` driver URL. ``asyncpg`` is used only as the
SQLAlchemy driver and is never imported by application code.

SQL echo is off and SQL statements and parameters are never logged by default;
``hide_parameters`` also keeps parameter values out of error messages.
The isolation level is PostgreSQL's default (``READ COMMITTED``).
"""

from typing import Any

from fastapi import Request
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

ASYNC_DRIVER = "postgresql+asyncpg"

# asyncpg connect timeout, so a down or unreachable database fails fast.
CONNECT_TIMEOUT_SECONDS = 2.0


def async_database_url(settings: Settings) -> URL:
    """The ``postgresql+asyncpg`` URL for ``Settings.database_url``.

    The returned ``URL`` hides its password in ``str()`` and ``repr()``.
    """
    return make_url(settings.database_url.get_secret_value()).set(
        drivername=ASYNC_DRIVER
    )


def engine_options() -> dict[str, Any]:
    """Keyword arguments for :func:`create_async_engine`.

    This is the single place for engine and driver settings. M1-08 adds the
    PgBouncer ones here (statement cache off, unique prepared-statement names)
    and the pooled/direct URL choice.
    """
    return {
        "echo": False,
        # Parameter values never appear in logs or exception messages.
        "hide_parameters": True,
        # Discards connections broken by a database restart before use.
        "pool_pre_ping": True,
        "connect_args": {"timeout": CONNECT_TIMEOUT_SECONDS},
    }


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the process engine. Does not connect."""
    return create_async_engine(async_database_url(settings), **engine_options())


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to ``engine``.

    ``expire_on_commit=False`` keeps loaded attributes readable after commit
    (lazy loads are not possible with ``AsyncSession``).
    """
    return async_sessionmaker(engine, expire_on_commit=False)


def get_engine(request: Request) -> AsyncEngine:
    """FastAPI dependency: the engine created by the app lifespan."""
    engine: AsyncEngine = request.app.state.engine
    return engine


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """FastAPI dependency: the session factory created by the app lifespan."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    return sessionmaker
