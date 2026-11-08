"""``make seed-data-spanish`` / ``make seed-data-english``:
``python -m tests.factories.simulation --language {es,en} [--seed N]``.

1. **Local-only guard (E5).** ``APP_ENV`` is read from the environment
   first: any value but ``local`` (``dev``, ``prod`` or a value the settings
   would reject) prints ``Refusing to generate simulated data: APP_ENV=<value>
   (local only)`` and exits 2 before any engine, connection or Faker call.
2. Load the settings (a configuration error exits 1).
3. :func:`~.timeline.run_simulation` in one Unit of Work: the Alembic head
   (exit 1 when not at head, as ``make seed-demo``), the reference seeds,
   the clean-database check (exit 3 with the reason and the reset steps;
   nothing written), then every module.
4. Print the reference-seed lines, then ``<module>: <n> rows`` (or
   ``skipped (<reason>)``) per module; generated values are never printed.

Like ``make seed``, it connects directly to PostgreSQL
(``DATABASE_DIRECT_URL``, ADR-0003).
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from typing import Final

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import ConfigurationError, get_settings
from app.seeds.registry import REFERENCE_SEEDS, ReferenceSeed
from app.seeds.runner import (
    DatabaseNotAtHeadError,
    EngineFactory,
    create_seed_engine,
    seed_summary,
)
from tests.factories.base import GeneratorSkipped
from tests.factories.simulation.plan import ProfileName
from tests.factories.simulation.texts import LANGUAGES, Language
from tests.factories.simulation.timeline import (
    SimulationModule,
    SimulationRefusedError,
    SimulationResult,
    run_simulation,
)

ALLOWED_APP_ENV: Final = "local"
DEFAULT_SEED: Final = 1
DESCRIPTION: Final = (
    "Generate the simulated operations data of a tutoring center (1 Sep to"
    " 5 Dec 2026, history to 18 Nov) in Spanish (es) or English (en)."
    " Local only (APP_ENV=local). It needs a freshly migrated database (the"
    " reference seeds and nothing else); a rerun in the same language changes"
    " nothing."
)


def refusal(app_env: str) -> str | None:
    """The refusal line for ``app_env``, or ``None`` when it is ``local``."""
    if app_env == ALLOWED_APP_ENV:
        return None
    return f"Refusing to generate simulated data: APP_ENV={app_env} (local only)"


def summary(result: SimulationResult) -> list[str]:
    lines = seed_summary(result.seeds)
    for name, rows in result.modules:
        if isinstance(rows, GeneratorSkipped):
            lines.append(f"{name}: skipped ({rows.reason})")
        else:
            lines.append(f"{name}: {rows} rows")
    return lines


async def _run(
    engine: AsyncEngine,
    language: Language,
    profile: ProfileName,
    seed: int,
    seeds: Sequence[ReferenceSeed],
    modules: Sequence[SimulationModule] | None,
) -> SimulationResult:
    try:
        return await run_simulation(
            engine,
            language=language,
            profile=profile,
            seed=seed,
            seeds=seeds,
            modules=modules,
        )
    finally:
        await engine.dispose()


def main(
    argv: Sequence[str] | None = None,
    *,
    profile: ProfileName = "full",
    seeds: Sequence[ReferenceSeed] = REFERENCE_SEEDS,
    modules: Sequence[SimulationModule] | None = None,
    engine_factory: EngineFactory = create_seed_engine,
) -> int:
    """Run the command; returns the process exit code. ``profile`` and
    ``modules`` are for tests (the make targets run the full profile)."""
    parser = argparse.ArgumentParser(
        prog="python -m tests.factories.simulation", description=DESCRIPTION
    )
    parser.add_argument(
        "--language",
        required=True,
        choices=LANGUAGES,
        help="es: Spanish (es_MX names and texts); en: English (en_US)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    raw_env = os.environ.get("APP_ENV")
    if raw_env is not None and raw_env != "":
        message = refusal(raw_env)
        if message is not None:
            print(message, file=sys.stderr)
            return 2

    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(exc, file=sys.stderr)
        return 1
    message = refusal(settings.app_env)
    if message is not None:  # pragma: no cover - the environment check above
        print(message, file=sys.stderr)
        return 2

    engine = engine_factory(settings)
    try:
        result = asyncio.run(
            _run(engine, args.language, profile, args.seed, seeds, modules)
        )
    except DatabaseNotAtHeadError as exc:
        print(exc, file=sys.stderr)
        return 1
    except SimulationRefusedError as exc:
        print(exc, file=sys.stderr)
        return 3
    for line in summary(result):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
