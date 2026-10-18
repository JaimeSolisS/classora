"""Async SQLAlchemy engines and session factory (spec §10, architecture §4.2).

There is one request :class:`~sqlalchemy.ext.asyncio.AsyncEngine` per
process. The FastAPI lifespan (``app.main``) creates it with
:func:`create_engine` and disposes it at shutdown. Creating an engine never
opens a connection, so the API starts even when PostgreSQL is down.

Two connection paths (ADR-0003, ``_docs/adr/0003-database-connection-topology.md``):

- **Pooled** (``Settings.database_url``, through PgBouncer in transaction
  mode): the request engine. asyncpg's statement cache is off and every
  prepared statement gets a unique name, so statements never collide on a
  server connection that PgBouncer shares between clients.
- **Direct** (``Settings.database_direct_url``, straight to PostgreSQL):
  :func:`create_direct_engine`, for Alembic, the SSE ``LISTEN`` connection and
  the worker.

Settings keep plain ``postgresql://`` DSNs; this module derives the
``postgresql+asyncpg`` driver URLs. ``asyncpg`` is used only as the SQLAlchemy
driver and is never imported by application code.

SQL echo is off and SQL statements and parameters are never logged by default;
``hide_parameters`` also keeps parameter values out of error messages.
The isolation level is PostgreSQL's default (``READ COMMITTED``).
"""

import uuid
from typing import Any

from fastapi import Request
from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Maps every model (and registers the audit flush hooks of app.core.audit)
# before any session from this module is used.
import app.core.model_registry  # noqa: F401
from app.core.config import Settings

ASYNC_DRIVER = "postgresql+asyncpg"

# asyncpg connect timeout, so a down or unreachable database fails fast.
CONNECT_TIMEOUT_SECONDS = 2.0


def _async_url(dsn: SecretStr) -> URL:
    return make_url(dsn.get_secret_value()).set(drivername=ASYNC_DRIVER)


def async_database_url(settings: Settings) -> URL:
    """The pooled ``postgresql+asyncpg`` URL (``Settings.database_url``).

    The returned ``URL`` hides its password in ``str()`` and ``repr()``.
    """
    return _async_url(settings.database_url)


def async_direct_database_url(settings: Settings) -> URL:
    """The direct ``postgresql+asyncpg`` URL (``Settings.database_direct_url``).

    The returned ``URL`` hides its password in ``str()`` and ``repr()``.
    """
    return _async_url(settings.database_direct_url)


def prepared_statement_name() -> str:
    """A unique prepared-statement name (never reused across connections)."""
    return f"__asyncpg_{uuid.uuid4()}__"


def engine_options(*, pgbouncer: bool = True) -> dict[str, Any]:
    """Keyword arguments for :func:`create_async_engine`.

    This is the single place for engine and driver settings. With
    ``pgbouncer=True`` (the request engine) it adds the ADR-0003 settings for
    transaction pooling: no statement cache and unique statement names.
    """
    connect_args: dict[str, Any] = {"timeout": CONNECT_TIMEOUT_SECONDS}
    if pgbouncer:
        connect_args |= {
            # SQLAlchemy's asyncpg dialect cache of prepared statements.
            "prepared_statement_cache_size": 0,
            # asyncpg's own statement cache.
            "statement_cache_size": 0,
            "prepared_statement_name_func": prepared_statement_name,
        }
    return {
        "echo": False,
        # Parameter values never appear in logs or exception messages.
        "hide_parameters": True,
        # Discards connections broken by a database or PgBouncer restart before use.
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the process's request engine (pooled URL). Does not connect."""
    return create_async_engine(async_database_url(settings), **engine_options())


def create_direct_engine(url: URL | str, **kwargs: Any) -> AsyncEngine:
    """Create an engine that connects directly to PostgreSQL. Does not connect.

    ``url`` is normally :func:`async_direct_database_url`. Per ADR-0003 this is
    for Alembic, the SSE ``LISTEN`` connection (M3-06) and the worker container
    (M1-16), which must not go through PgBouncer. Request traffic uses
    :func:`create_engine`. ``kwargs`` are extra :func:`create_async_engine`
    arguments (for example ``poolclass``).
    """
    return create_async_engine(url, **engine_options(pgbouncer=False), **kwargs)


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
