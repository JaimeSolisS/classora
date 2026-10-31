"""``make seed-demo`` / ``python -m tests.factories.demo [--seed N]
[--without-billing]``.

Generates synthetic ``es_MX`` demo data for local and DEV databases:

1. Load settings (a configuration error prints the short ``Configuration
   error`` message and exits 1).
2. **Production guard.** Continue only when ``APP_ENV`` is in
   :data:`ALLOWED_APP_ENVS` (an allow-list, so any other or future name is
   refused). Otherwise print the refusal and exit 2 **before** creating an
   engine, connecting, or seeding or calling Faker.
3. :func:`~tests.factories.base.reseed` with ``--seed`` (default
   :data:`DEFAULT_SEED`).
4. Register the app's approval and notification wiring
   (``app.wiring.register_approval_wiring``, as ``app.cli`` and ``app.worker``
   do; #299 A3), so approval items and notifications are created as in the
   API.
5. In **one** Unit of Work: check the Alembic head (as ``make seed``), apply the
   reference seeds, then run every generator in :data:`DEMO_GENERATORS` in
   order. Everything is committed, or nothing.
6. Print generator names and row counts (or ``skipped (<reason>)``), never
   generated values.

``--without-billing`` (M6-demo E3; ``e2e/scripts/run.sh`` passes it, ``make
seed-demo`` doesn't) makes the billing generators print ``skipped
(--without-billing)`` without touching the database, so billing stays off in
the E2E database.

The registered generators (#23, #195, #299, #303, #353, #354, M6-demo #413,
M6-demo-b #420) write nothing on a rerun (``0 rows``), even on a later day:
the #299, #303, #353, #354 and M6-demo data keep the dates of their first
run. A future
generator may add rows on a rerun unless its issue says otherwise. To start
over, reset with ``docker compose down -v``, then ``make migrate`` and ``make
seed-demo``.

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
from app.wiring import register_approval_wiring
from tests.factories import demo_billing, demo_billing_money
from tests.factories.base import GeneratorSkipped, reseed
from tests.factories.catalog import DemoCatalogGenerator
from tests.factories.demo_academics import (
    DemoEvaluationsGenerator,
    DemoHeldSessionsGenerator,
    DemoNotesGenerator,
)
from tests.factories.demo_changes import DemoChangesGenerator
from tests.factories.demo_courses import DemoCoursesGenerator
from tests.factories.demo_learning import (
    DemoHomeworkGenerator,
    DemoLearningPlansGenerator,
    DemoProgressReportsGenerator,
)
from tests.factories.demo_past_cohort import DemoPastCohortGenerator
from tests.factories.demo_schedule import DemoScheduleGenerator
from tests.factories.families import DemoFamiliesGenerator
from tests.factories.identity import DEMO_ACCOUNT_GENERATORS
from tests.factories.staff import DemoSecondTutorGenerator, DemoTutorGenerator

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
    DemoCatalogGenerator(),
    DemoTutorGenerator(),
    DemoScheduleGenerator(),
    DemoSecondTutorGenerator(),
    DemoCoursesGenerator(),
    DemoChangesGenerator(),
    DemoHeldSessionsGenerator(),
    DemoPastCohortGenerator(),
    DemoNotesGenerator(),
    DemoEvaluationsGenerator(),
    DemoLearningPlansGenerator(),
    DemoHomeworkGenerator(),
    DemoProgressReportsGenerator(),
    demo_billing.DemoPricingGenerator(),
    demo_billing.DemoPackagesGenerator(),
    demo_billing.DemoChargesGenerator(),
    demo_billing_money.DemoReconciliationGenerator(),
    demo_billing_money.DemoPaymentsGenerator(),
    demo_billing_money.DemoRemindersGenerator(),
)
"""Every demo generator, in order. Domain tasks append generators that use
their factories (``persist`` / ``persist_batch``; never commit).

- ``identity.demo_owner`` (#23): the demo Owner, ``APP_ENV=local`` only.
- ``identity.demo_admin``, ``identity.demo_coordinator``,
  ``identity.demo_finance``, ``identity.demo_tutor``, ``identity.demo_student``
  and ``identity.demo_guardian`` (#195): one demo account per other role,
  ``APP_ENV=local`` only.
- ``identity.demo_tutor2`` (#303): a second tutor account, the cohort's tutor
  and the substitute, ``APP_ENV=local`` only.
- ``families.demo_families`` (#195): three demo families, the first with the
  ``demo.student`` and ``demo.guardian`` logins, ``APP_ENV=local`` only.
- ``catalog.demo_catalog`` (#299): subjects for the four reference levels,
  three classrooms, the current school year and one holiday,
  ``APP_ENV=local`` only.
- ``staff.demo_tutor`` (#299): ``demo.tutor``'s profile, qualifications
  (three approved, one pending), availability, a day off, limits and a
  preferred classroom, ``APP_ENV=local`` only.
- ``scheduling.demo_schedule`` (#299): completed and upcoming sessions, a
  weekly series and a group around the center's today, through the
  scheduling services, ``APP_ENV=local`` only.
- ``staff.demo_tutor2`` (#303): ``demo.tutor2``'s two approved
  qualifications and weekly availability, ``APP_ENV=local`` only.
- ``courses.demo_courses`` (#303): a course template and a cohort taught by
  ``demo.tutor2`` with two students enrolled, ``APP_ENV=local`` only.
- ``scheduling.demo_changes`` (#303): an accepted substitution, a cancelled
  cohort class, ``demo.guardian``'s pending session and reschedule requests
  and ``demo.tutor``'s pending time off, ``APP_ENV=local`` only. The course
  and change generators skip a database whose #299 schedule is from an
  earlier day (reset to add them).
- ``scheduling.demo_held_sessions`` (#353): ``demo.tutor``'s three past
  classes, each with attendance and a closeout approved by
  ``demo.coordinator``, through the services under the seed clock,
  ``APP_ENV=local`` only.
- ``courses.demo_past_cohort`` (#353): a finished six-class cohort taught by
  ``demo.tutor2`` with three students enrolled, every class closed out and
  approved, ``APP_ENV=local`` only.
- ``academics.demo_notes`` (#353): two notes shared with ``demo.guardian``'s
  family and one internal note, ``APP_ENV=local`` only.
- ``academics.demo_evaluations`` (#353): two evaluation templates,
  ``demo.tutor``'s evaluations (one still a draft) and the finished cohort's
  course evaluations, so its completion shows ``completed`` and
  ``not_completed``, ``APP_ENV=local`` only. The four #353 generators also
  skip a database whose #299 schedule is from an earlier day.
- ``academics.demo_learning_plans`` (#354): ``demo.tutor``'s learning plans
  for ``demo.student``, one active with an overdue milestone and one
  completed, through the services under the seed clock, ``APP_ENV=local``
  only.
- ``academics.demo_homework`` (#354): ``demo.tutor``'s homework for
  ``demo.student``, graded (Inglés only), handed in and assigned,
  ``APP_ENV=local`` only.
- ``academics.demo_progress_reports`` (#354): ``demo.tutor``'s progress
  reports about ``demo.student`` (approved for the family, waiting for
  review and a draft) and ``demo.coordinator``'s weekly report schedule,
  ``APP_ENV=local`` only. The three #354 generators also skip a database
  whose #299 schedule is from an earlier day.
- ``billing.demo_pricing`` (M6-demo #413): ``demo.finance`` turns billing on
  from about three weeks back and creates catalog prices (one with a change
  next month), a sibling discount, a scholarship and two package products,
  ``APP_ENV=local`` only.
- ``billing.demo_packages`` (M6-demo #413): an English package for
  ``demo.student`` with one session used (a new class closed out and approved)
  and an expired package for the sibling, under the seed clock,
  ``APP_ENV=local`` only.
- ``billing.demo_charges`` (M6-demo #413): a monthly tuition plan, then every
  charge the billing jobs would post (their per-item steps in this Unit of
  Work), ``APP_ENV=local`` only.
- ``billing.demo_reconciliation`` (M6-demo-b #420): ``demo.owner`` cancels
  one of ``demo.student``'s upcoming series classes as the center, then the
  reconciliation step credits its charge, ``APP_ENV=local`` only.
- ``billing.demo_payments`` (M6-demo-b #420): two payments with receipts for
  ``demo.guardian``'s family (one paying the oldest charges, one kept as
  account credit), and for the second family a due-date offset and a manual
  charge whose payment ``demo.owner`` reversed, ``APP_ENV=local`` only.
- ``billing.demo_reminders`` (M6-demo-b #420): a due date moved with a
  reason, then the payment reminders the reminder job would send (to
  ``demo.guardian``), ``APP_ENV=local`` only. The six billing generators
  also skip a database whose #299 schedule is from an earlier day or whose
  billing was set up by hand, and under ``--without-billing``."""


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
    """Register the app wiring, reseed, then reference seeds and generators in
    one Unit of Work."""
    register_approval_wiring()  # #299 A3; registering again changes nothing
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
    parser.add_argument(
        "--without-billing",
        action="store_true",
        help="skip the billing demo data (e2e/scripts/run.sh passes it, so the"
        " E2E database keeps billing off)",
    )
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
        with demo_billing.without_billing(args.without_billing):
            result = asyncio.run(_run(engine, args.seed, seeds, generators))
    except DatabaseNotAtHeadError as exc:
        print(exc, file=sys.stderr)
        return 1
    for line in summary(result):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
