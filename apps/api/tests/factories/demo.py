"""``make seed-demo`` / ``python -m tests.factories.demo [--seed N]``.

Generates synthetic ``es_MX`` demo data for local and DEV databases:

1. Load settings (a configuration error prints the short ``Configuration
   error`` message and exits 1).
2. **Production guard.** Continue only when ``APP_ENV`` is in
   :data:`ALLOWED_APP_ENVS` (an allow-list, so any other or future name is
   refused). Otherwise print the refusal and exit 2 **before** creating an
   engine, connecting, or seeding or calling Faker.
3. :func:`~tests.factories.base.reseed` with ``--seed`` (default
   :data:`DEFAULT_SEED`).
4. In **one** Unit of Work: check the Alembic head (as ``make seed``), apply the
   reference seeds, then run every generator in :data:`DEMO_GENERATORS` in
   order. Everything is committed, or nothing.
5. Print generator names and row counts (or ``skipped (<reason>)``), never
   generated values.

The registered generators (#23, #195) write nothing on a rerun (``0 rows``).
A future generator may add rows on a rerun unless its issue says otherwise.
To start over, reset with ``docker compose down -v``, then ``make migrate``
and ``make seed-demo``.

Like ``make seed``, it connects directly to PostgreSQL (``DATABASE_DIRECT_URL``,
ADR-0003).
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import ConfigurationError, get_settings
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.seeds.registry import REFERENCE_SEEDS, ReferenceSeed
from app.seeds.runner import (
    DatabaseNotAtHeadError,
    EngineFactory,
    apply_seeds,
    create_seed_engine,
    ensure_at_head,
    seed_summary,
)
from app.seeds.upsert import UpsertResult
from tests.factories.base import GeneratorSkipped, reseed
from tests.factories.families import DemoFamiliesGenerator
from tests.factories.identity import DEMO_ACCOUNT_GENERATORS

ALLOWED_APP_ENVS = frozenset({"local", "dev"})
DEFAULT_SEED = 1
NO_GENERATORS_MESSAGE = "No demo generators are registered."


class DemoGenerator(Protocol):
    """Writes demo rows with domain factories; returns the number of rows, or
    :class:`~tests.factories.base.GeneratorSkipped` when it writes nothing on
    purpose."""

    @property
    def name(self) -> str: ...

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped: ...


DEMO_GENERATORS: tuple[DemoGenerator, ...] = (
    *DEMO_ACCOUNT_GENERATORS,
    DemoFamiliesGenerator(),
)
"""Every demo generator, in order. Domain tasks append generators that use
their factories (``persist`` / ``persist_batch``; never commit).

- ``identity.demo_owner`` (#23): the demo Owner, ``APP_ENV=local`` only.
- ``identity.demo_admin``, ``identity.demo_coordinator``,
  ``identity.demo_finance``, ``identity.demo_tutor``, ``identity.demo_student``
  and ``identity.demo_guardian`` (#195): one demo account per other role,
  ``APP_ENV=local`` only.
- ``families.demo_families`` (#195): three demo families, the first with the
  ``demo.student`` and ``demo.guardian`` logins, ``APP_ENV=local`` only."""


@dataclass
class DemoResult:
    seeds: list[tuple[str, UpsertResult]] = field(default_factory=list)
    generators: list[tuple[str, int | GeneratorSkipped]] = field(default_factory=list)


def refusal(app_env: str) -> str | None:
    """The refusal line for ``app_env``, or ``None`` when demo data is allowed."""
    if app_env in ALLOWED_APP_ENVS:
        return None
    return f"Refusing to generate demo data: APP_ENV={app_env}"


async def run_demo(
    engine: AsyncEngine,
    *,
    seed: int = DEFAULT_SEED,
    seeds: Sequence[ReferenceSeed] = REFERENCE_SEEDS,
    generators: Sequence[DemoGenerator] = DEMO_GENERATORS,
) -> DemoResult:
    """Reseed, then reference seeds and generators in one Unit of Work."""
    reseed(seed)
    result = DemoResult()
    async with UnitOfWork(create_sessionmaker(engine)) as uow:
        await ensure_at_head(uow.session)
        result.seeds = await apply_seeds(uow.session, seeds)
        for generator in generators:
            result.generators.append(
                (generator.name, await generator.generate(uow.session))
            )
    return result


def summary(result: DemoResult) -> list[str]:
    lines = seed_summary(result.seeds)
    if not result.generators:
        return [*lines, NO_GENERATORS_MESSAGE]
    return [*lines, *(_generator_line(name, rows) for name, rows in result.generators)]


def _generator_line(name: str, rows: int | GeneratorSkipped) -> str:
    if isinstance(rows, GeneratorSkipped):
        return f"{name}: skipped ({rows.reason})"
    return f"{name}: {rows} rows"


async def _run(
    engine: AsyncEngine,
    seed: int,
    seeds: Sequence[ReferenceSeed],
    generators: Sequence[DemoGenerator],
) -> DemoResult:
    try:
        return await run_demo(engine, seed=seed, seeds=seeds, generators=generators)
    finally:
        await engine.dispose()


def main(
    argv: Sequence[str] | None = None,
    *,
    seeds: Sequence[ReferenceSeed] = REFERENCE_SEEDS,
    generators: Sequence[DemoGenerator] = DEMO_GENERATORS,
    engine_factory: EngineFactory = create_seed_engine,
) -> int:
    """Run the command; returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m tests.factories.demo",
        description="Generate synthetic demo data (APP_ENV local or dev only).",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(exc, file=sys.stderr)
        return 1

    message = refusal(settings.app_env)
    if message is not None:
        print(message, file=sys.stderr)
        return 2

    engine = engine_factory(settings)
    try:
        result = asyncio.run(_run(engine, args.seed, seeds, generators))
    except DatabaseNotAtHeadError as exc:
        print(exc, file=sys.stderr)
        return 1
    for line in summary(result):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
