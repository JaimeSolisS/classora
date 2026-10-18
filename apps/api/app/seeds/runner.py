"""``make seed`` / ``python -m app.seeds``: apply the reference seeds.

1. Load settings (a configuration error prints the short ``Configuration
   error`` message and exits 1).
2. In **one** Unit of Work: check that the database is at the Alembic head
   (otherwise exit 1 with a ``make migrate`` hint, having written nothing), then
   apply every seed in :data:`~app.seeds.registry.REFERENCE_SEEDS` in order.
   Any error rolls the whole run back.
3. Print one line per seed with its inserted and updated counts (and deleted,
   when non-zero), never values.

Runs in every ``APP_ENV``: reference data is not demo data.

Connection (ADR-0003): like Alembic, seeds are a one-off maintenance command,
not request traffic, so they connect directly to PostgreSQL through
``DATABASE_DIRECT_URL`` (``db:5432`` inside Compose), never through PgBouncer.
"""

import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import column, inspect, pool, select, table
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import ConfigurationError, Settings, get_settings
from app.core.db import (
    async_direct_database_url,
    create_direct_engine,
    create_sessionmaker,
)
from app.core.uow import UnitOfWork
from app.seeds.registry import REFERENCE_SEEDS, ReferenceSeed
from app.seeds.upsert import UpsertResult

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
# Alembic's default version table (``alembic.ini`` does not change it).
VERSION_TABLE = "alembic_version"

NOT_AT_HEAD_MESSAGE = (
    "The database is not at the latest migration: run `make migrate` first."
)
NO_SEEDS_MESSAGE = "No reference seeds are registered."

EngineFactory = Callable[[Settings], AsyncEngine]


class DatabaseNotAtHeadError(Exception):
    """The database is empty or has pending Alembic migrations."""

    def __init__(self) -> None:
        super().__init__(NOT_AT_HEAD_MESSAGE)


def create_seed_engine(settings: Settings) -> AsyncEngine:
    """A short-lived direct engine (ADR-0003). Does not connect."""
    return create_direct_engine(
        async_direct_database_url(settings), poolclass=pool.NullPool
    )


def alembic_heads() -> set[str]:
    """The head revision(s) of the migration scripts."""
    return set(ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads())


async def ensure_at_head(session: AsyncSession) -> None:
    """Raise :class:`DatabaseNotAtHeadError` unless the database is at head.

    Reads ``alembic_version`` only; never creates or changes anything.
    """

    def _current(connection: Connection) -> set[str]:
        if not inspect(connection).has_table(VERSION_TABLE):
            return set()
        return set(
            connection.scalars(
                select(column("version_num")).select_from(table(VERSION_TABLE))
            )
        )

    connection = await session.connection()
    if await connection.run_sync(_current) != alembic_heads():
        raise DatabaseNotAtHeadError()


async def apply_seeds(
    session: AsyncSession, seeds: Sequence[ReferenceSeed]
) -> list[tuple[str, UpsertResult]]:
    """Apply ``seeds`` in order in the caller's transaction (never commits)."""
    return [(seed.name, await seed.apply(session)) for seed in seeds]


def seed_summary(results: Sequence[tuple[str, UpsertResult]]) -> list[str]:
    """One line per seed: its name and counts (never row values).

    The deleted count appears only when it is non-zero.
    """
    if not results:
        return [NO_SEEDS_MESSAGE]
    lines = []
    for name, result in results:
        line = f"{name}: {result.inserted} inserted, {result.updated} updated"
        if result.deleted:
            line += f", {result.deleted} deleted"
        lines.append(line)
    return lines


async def run_reference_seeds(
    engine: AsyncEngine, seeds: Sequence[ReferenceSeed] = REFERENCE_SEEDS
) -> list[tuple[str, UpsertResult]]:
    """Check the head and apply ``seeds`` in one Unit of Work (all or nothing)."""
    async with UnitOfWork(create_sessionmaker(engine)) as uow:
        await ensure_at_head(uow.session)
        return await apply_seeds(uow.session, seeds)


async def _run(
    settings: Settings,
    seeds: Sequence[ReferenceSeed],
    engine_factory: EngineFactory,
) -> list[tuple[str, UpsertResult]]:
    engine = engine_factory(settings)
    try:
        return await run_reference_seeds(engine, seeds)
    finally:
        await engine.dispose()


def main(
    *,
    seeds: Sequence[ReferenceSeed] = REFERENCE_SEEDS,
    engine_factory: EngineFactory = create_seed_engine,
) -> int:
    """Run the command; returns the process exit code."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        # Short message, no traceback, no secret (M1-05).
        print(exc, file=sys.stderr)
        return 1
    try:
        results = asyncio.run(_run(settings, seeds, engine_factory))
    except DatabaseNotAtHeadError as exc:
        print(exc, file=sys.stderr)
        return 1
    for line in seed_summary(results):
        print(line)
    return 0
