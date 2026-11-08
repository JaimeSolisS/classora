"""The simulation's chronological driver (#543 E10 (a)) and its clean-database
check (E3 (a)).

A module (:class:`SimulationModule`) has a ``name``, the ``tables`` whose
rows it counts, a ``marker`` (its rows exist), and three steps:

- ``setup(session, ctx)`` at :data:`~.anchor.SIM_SETUP`;
- ``on_day(session, ctx, day)`` for each day of ``days(ctx)`` from
  :data:`~.anchor.SIM_START` to the day before :data:`~.anchor.SIM_ANCHOR`
  (the days it has work on; other days are no-ops it skips);
- ``at_anchor(session, ctx)`` at :data:`~.anchor.SIM_NOW`, for the future
  and pending writes.

:func:`run_simulation` runs every module's ``setup``, then for each day every
module's ``on_day`` in module order, then every ``at_anchor``, so each write
sees the world as it was on that simulated day. Each step runs under
:func:`~.anchor.sim_clock` (``setup`` at ``SIM_SETUP``, ``on_day`` at the
day's 08:00, ``at_anchor`` at ``SIM_NOW``); a step moves the clock to the
instants it chooses. A module's row total is the sum, over its steps, of the
rows after minus the rows before in its ``tables`` (``count_rows``).

Everything runs in **one** Unit of Work after ``ensure_at_head`` and the
reference seeds: all or nothing. The clean-database check runs inside it,
before any module:

1. this language's marker (business settings ``center_name`` is this
   language's center name **and** its Owner username exists): a rerun; each
   module prints ``0 rows`` (marker present) or ``skipped (seeded by an
   earlier version; reset to add it)``, and nothing is written;
2. the other language's marker: :class:`SimulationRefusedError` (exit 3);
3. any row in ``users``, ``students``, ``class_sessions``, ``charges``,
   ``expenses`` or ``payroll_periods``, or billing or payroll turned on:
   :class:`SimulationRefusedError` (exit 3). Nothing is ever truncated.

The password hashes are computed once per distinct password for the run
(:func:`cached_password_hashes`; Argon2id costs about 19 MiB and tens of
milliseconds per hash).
"""

import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import Final, Protocol

import faker
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.administration.settings_models import BusinessSettings
from app.modules.billing.models import BillingSettings, Charge
from app.modules.families import service as families_service
from app.modules.families.models import Student
from app.modules.finance.models import Expense
from app.modules.identity import service as identity_service
from app.modules.identity.models import User
from app.modules.identity.passwords import hash_password
from app.modules.payroll.models import PayrollPeriod, PayrollSettings
from app.modules.scheduling.models import ClassSession
from app.seeds.registry import REFERENCE_SEEDS, ReferenceSeed
from app.seeds.runner import apply_seeds, ensure_at_head
from app.seeds.upsert import UpsertResult
from app.wiring import register_approval_wiring
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.simulation.anchor import (
    DAY_INSTANT,
    SIM_NOW,
    SIM_SETUP,
    history_days,
    local_instant,
    sim_clock,
)
from tests.factories.simulation.plan import (
    STAFF_USERNAMES,
    ProfileName,
    SimulationPlan,
    make_faker,
    plan_simulation,
)
from tests.factories.simulation.texts import Language, TextPool, center_name

SEEDED_EARLIER: Final = "seeded by an earlier version; reset to add it"

RESET_STEPS: Final = (
    "Reset: native stack: make db-drop NAME=<db> && make db-create NAME=<db>,"
    " set DATABASE_URL and DATABASE_DIRECT_URL to it and run make api-native"
    " (migrates); Compose: docker compose down -v, make dev, make migrate."
    " Then run the target again."
)
NOT_EMPTY_MESSAGE: Final = (
    "The database is not empty (for example demo data or a bootstrap Owner);"
    " seed-data needs a freshly migrated database"
)
OTHER_LANGUAGE_MESSAGE: Final = (
    "The database holds the {other} simulation; use a separate database"
    ' (README "Simulated operations data")'
)
LANGUAGE_NAMES: Final[dict[Language, str]] = {"es": "Spanish", "en": "English"}

NOT_EMPTY_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    User,
    Student,
    ClassSession,
    Charge,
    Expense,
    PayrollPeriod,
)


class SimulationRefusedError(Exception):
    """The database isn't clean (E3): the command exits 3 and writes nothing."""


@dataclass
class SimulationContext:
    """What every module step gets: the run's language, profile and plan,
    a seeded Faker instance, the text pool and an id registry (plan keys →
    database ids)."""

    language: Language
    profile: ProfileName
    plan: SimulationPlan
    faker: faker.Faker
    texts: TextPool
    ids: dict[str, uuid.UUID] = field(default_factory=dict)
    principals: dict[uuid.UUID, Principal] = field(default_factory=dict)

    def id(self, key: str) -> uuid.UUID:
        return self.ids[key]


class SimulationModule(Protocol):
    """One area of the simulation (see the module docstring)."""

    @property
    def name(self) -> str: ...

    @property
    def tables(self) -> Sequence[type[DeclarativeBase]]: ...

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool: ...

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None: ...

    def days(self, ctx: SimulationContext) -> Iterable[date]: ...

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None: ...

    async def at_anchor(
        self, session: AsyncSession, ctx: SimulationContext
    ) -> None: ...


@dataclass
class SimulationResult:
    seeds: list[tuple[str, UpsertResult]] = field(default_factory=list)
    modules: list[tuple[str, int | GeneratorSkipped]] = field(default_factory=list)


@contextmanager
def cached_password_hashes() -> Iterator[None]:
    """Within the block, the identity service hashes each distinct password
    once (the same Argon2id hash is stored for every account with that
    development password). Restores the real function on exit."""
    saved = identity_service.hash_password
    identity_service.hash_password = cache(hash_password)  # type: ignore[assignment]
    try:
        yield
    finally:
        identity_service.hash_password = saved  # type: ignore[assignment]


async def has_marker(session: AsyncSession, language: Language) -> bool:
    """The simulation marker of ``language``: its center name and its Owner."""
    name = await session.scalar(select(BusinessSettings.center_name))
    if name != center_name(language):
        return False
    owner = STAFF_USERNAMES[language]["owner"]
    return bool(await session.scalar(select(exists().where(User.username == owner))))


async def is_rerun(session: AsyncSession, language: Language) -> bool:
    """The clean-database check (E3, module docstring): ``True`` for a rerun
    in ``language``, ``False`` for a clean database; otherwise raises
    :class:`SimulationRefusedError`."""
    if await has_marker(session, language):
        return True
    other: Language = "en" if language == "es" else "es"
    if await has_marker(session, other):
        raise SimulationRefusedError(
            OTHER_LANGUAGE_MESSAGE.format(other=LANGUAGE_NAMES[other])
        )
    for model in NOT_EMPTY_TABLES:
        if await session.scalar(select(exists().select_from(model))):
            raise SimulationRefusedError(f"{NOT_EMPTY_MESSAGE}. {RESET_STEPS}")
    billing = await session.scalar(select(BillingSettings.billing_starts_on))
    payroll = await session.scalar(select(PayrollSettings.payroll_starts_on))
    if billing is not None or payroll is not None:
        raise SimulationRefusedError(f"{NOT_EMPTY_MESSAGE}. {RESET_STEPS}")
    return False


async def _counted(
    session: AsyncSession,
    module: SimulationModule,
    step: Callable[[], object],
) -> int:
    before = await count_rows(session, module.tables)
    await step()  # type: ignore[misc]
    return await count_rows(session, module.tables) - before


async def run_simulation(
    engine: AsyncEngine,
    *,
    language: Language,
    profile: ProfileName = "full",
    seed: int = 1,
    seeds: Sequence[ReferenceSeed] = REFERENCE_SEEDS,
    modules: Sequence[SimulationModule] | None = None,
) -> SimulationResult:
    """Register the app wiring, then reference seeds, the clean check and
    every module's steps in one Unit of Work (see the module docstring)."""
    if modules is None:
        from tests.factories.simulation import SIMULATION_MODULES

        modules = SIMULATION_MODULES
    register_approval_wiring()  # as demo.run_demo; registering again is a no-op
    # As app.main, app.cli and app.worker: the identity module's student
    # activation (an invitation redeemed) needs the families gate.
    identity_service.register_student_login_gate(families_service.STUDENT_LOGIN_GATE)
    plan = plan_simulation(language, profile, seed)
    ctx = SimulationContext(
        language=language,
        profile=profile,
        plan=plan,
        faker=make_faker(language, seed + 1),
        texts=TextPool(language),
    )
    result = SimulationResult()
    async with UnitOfWork(create_sessionmaker(engine)) as uow:
        session = uow.session
        await ensure_at_head(session)
        result.seeds = await apply_seeds(session, seeds)
        if await is_rerun(session, language):
            for module in modules:
                present = await module.marker(session, ctx)
                result.modules.append(
                    (module.name, 0 if present else GeneratorSkipped(SEEDED_EARLIER))
                )
            return result

        totals = {module.name: 0 for module in modules}
        module_days = {module.name: set(module.days(ctx)) for module in modules}
        with cached_password_hashes():
            for module in modules:
                with sim_clock(SIM_SETUP):
                    totals[module.name] += await _counted(
                        session, module, lambda m=module: m.setup(session, ctx)
                    )
            for day in history_days():
                for module in modules:
                    if day not in module_days[module.name]:
                        continue
                    with sim_clock(local_instant(day, DAY_INSTANT)):
                        totals[module.name] += await _counted(
                            session,
                            module,
                            lambda m=module, d=day: m.on_day(session, ctx, d),
                        )
            for module in modules:
                with sim_clock(SIM_NOW):
                    totals[module.name] += await _counted(
                        session, module, lambda m=module: m.at_anchor(session, ctx)
                    )
        result.modules = [(module.name, totals[module.name]) for module in modules]
    return result
