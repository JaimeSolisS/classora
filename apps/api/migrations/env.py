"""Alembic environment: online migrations with the async engine (asyncpg).

The database URL comes from ``get_settings()`` (``DATABASE_DIRECT_URL``), never
from ``DATABASE_URL`` or ``alembic.ini``. Alembic connects directly to
PostgreSQL, bypassing PgBouncer (ADR-0003), with its own short-lived engine
(``NullPool``), not the API's.

The target metadata comes from ``app.core.model_registry``, the one place that
imports every model module. Revision IDs are sequential four-digit numbers in
one linear history (see ``alembic.ini``).
"""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection

from app.core.config import ConfigurationError, get_settings
from app.core.db import async_direct_database_url, create_direct_engine
from app.core.model_registry import target_metadata

config = context.config

if config.config_file_name is not None and config.attributes.get(
    "configure_logging", True
):
    fileConfig(config.config_file_name)


def _database_url() -> str:
    # A URL passed in by tests (config.attributes) wins over the settings.
    url = config.attributes.get("database_url")
    if url is not None:
        return str(url)
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        # Same short message as the API: no traceback, no secret.
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None
    return async_direct_database_url(settings).render_as_string(hide_password=False)


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_direct_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported.")

asyncio.run(_run_async_migrations())
