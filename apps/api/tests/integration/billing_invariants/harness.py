"""Seeds, the sequence runner and the failure report of the billing
invariant suite (#103 B, D5–D7; U3, U5).

**Seeds** (:func:`seed_params`): each randomized test is parametrized over
seeds (``seed=<n>`` ids). The defaults are a fixed list in each test
module; ``BILLING_INVARIANT_SEEDS="5,9"`` replaces them,
``BILLING_INVARIANT_EXTRA_SEED=123`` adds one (CI sets it to the run id on
push and manual runs) and ``BILLING_INVARIANT_STEPS=<n>`` truncates every
sequence to ``n`` steps (:func:`step_count`). An invalid value is a
``pytest.UsageError``. All randomness of a sequence comes from one
``random.Random(seed)``; nothing chooses with ``uuid4`` or the time.

**The runner** (:func:`run_sequence`): per step the scenario's generator
picks one operation, or about one step in eight a **burst** of 2–3
operations on the same account run at once on separate engines. Around
each step:

- every operation either is accepted or raises one of its declared refusal
  codes; anything else fails (:class:`SequenceFailure`);
- **conservation:** per account, the change of the ``billing.service`` net
  balance = Σ the declared deltas of the accepted operations;
- **refused ⇒ unchanged:** a refused single operation leaves the account's
  ledger row counts and net balance as they were;
- the whole check catalogue (:mod:`.checks`), with the step checks of the
  kinds that ran.

A failure message holds the scenario, seed, step, the failing check and its
rows, the last 25 operations and the reproduce command.
"""

import asyncio
import os
import random
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Any, Protocol

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.money import Money
from app.core.uow import UnitOfWork
from tests.conftest import FrozenClock
from tests.integration.billing_invariants import checks
from tests.integration.billing_invariants.world import ZONE, World
from tests.integration.conftest import settings_for, short_lived_engine

SEEDS_ENV = "BILLING_INVARIANT_SEEDS"
STEPS_ENV = "BILLING_INVARIANT_STEPS"
EXTRA_SEED_ENV = "BILLING_INVARIANT_EXTRA_SEED"
TRACE_TAIL = 25
BURST_ODDS = 8
"""About one step in this many is a concurrent burst (D7)."""


# --- Seeds and steps (B1, B4) --------------------------------------------------------


def _non_negative(name: str, raw: str) -> int:
    item = raw.strip()
    if not item.isdigit():
        raise pytest.UsageError(
            f"{name} must be non-negative integers separated by commas; got {raw!r}"
        )
    return int(item)


def seeds(defaults: Sequence[int], env: dict[str, str] | None = None) -> list[int]:
    """The seeds to run: :data:`SEEDS_ENV` (replaces ``defaults``) plus
    :data:`EXTRA_SEED_ENV` (added)."""
    source = os.environ if env is None else env
    chosen = list(defaults)
    raw = source.get(SEEDS_ENV)
    if raw is not None:
        chosen = [_non_negative(SEEDS_ENV, item) for item in raw.split(",")]
    extra = source.get(EXTRA_SEED_ENV)
    if extra is not None and extra != "":
        value = _non_negative(EXTRA_SEED_ENV, extra)
        if value not in chosen:
            chosen.append(value)
    return chosen


def seed_params(defaults: Sequence[int]) -> list[Any]:
    """``pytest.param`` per seed with the id ``seed=<n>``."""
    return [pytest.param(seed, id=f"seed={seed}") for seed in seeds(defaults)]


def step_count(default: int, env: dict[str, str] | None = None) -> int:
    """:data:`STEPS_ENV` when set (≥ 1), else ``default``."""
    source = os.environ if env is None else env
    raw = source.get(STEPS_ENV)
    if raw is None or raw == "":
        return default
    value = _non_negative(STEPS_ENV, raw)
    if value < 1:
        raise pytest.UsageError(f"{STEPS_ENV} must be at least 1; got {raw!r}")
    return value


def reproduce(seed: int, step: int) -> str:
    return f"reproduce: make test-billing-invariants SEEDS={seed} STEPS={step}"


# --- Context, operations and outcomes ------------------------------------------------


@dataclass
class Ctx:
    """What operations run against: one world copy."""

    url: str
    world: World
    clock: FrozenClock
    engine: AsyncEngine
    labels: dict[uuid.UUID, str] = field(default_factory=dict)
    counters: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self.labels.update(self.world.labels)

    @property
    def settings(self) -> Settings:
        return settings_for(self.url)

    @property
    def today(self) -> date:
        return self.clock.now().astimezone(ZONE).date()

    def new_label(self, prefix: str, row_id: uuid.UUID) -> str:
        self.counters[prefix] += 1
        label = f"{prefix}{self.counters[prefix]:03d}"
        self.labels[row_id] = label
        return label

    def label(self, row_id: Any) -> str:
        if row_id is None:
            return "-"
        return self.labels.get(uuid.UUID(str(row_id)), f"?{str(row_id)[-6:]}")

    @asynccontextmanager
    async def uow(self, engine: AsyncEngine | None = None) -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(
            create_sessionmaker(engine or self.engine), self.world.owner.id
        ) as uow:
            yield uow

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        async with create_sessionmaker(self.engine)() as session:
            yield session

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.read() as session:
            return list((await session.execute(text(sql), params)).all())


@dataclass
class Outcome:
    accepted: bool
    code: str | None = None
    deltas: dict[uuid.UUID, Money] = field(default_factory=dict)
    """Declared net-balance change per account when accepted."""
    note: str = ""
    problems: list[str] = field(default_factory=list)
    """Step-level findings the operation itself detected (a job's failed
    items, a reminder for a settled charge…)."""

    def describe(self) -> str:
        if self.accepted:
            return "accepted" + (f" {self.note}" if self.note else "")
        return f"refused {self.code}"


type Execute = Callable[[Ctx, AsyncEngine], Awaitable[Outcome]]


@dataclass
class Op:
    kind: str
    label: str
    """Kind-specific inputs by stable labels (part of the trace)."""
    execute: Execute
    account: uuid.UUID | None = None
    """The one account the operation may change; ``None``: any (jobs)."""
    refusals: frozenset[str] = frozenset()
    """Codes of :class:`AppError` the operation may be refused with."""


def accepted(account: uuid.UUID, delta: Money, note: str = "") -> Outcome:
    return Outcome(True, deltas={account: delta}, note=note)


class Generator(Protocol):
    async def choose(self, rng: random.Random, ctx: Ctx) -> Op: ...

    async def burst(self, rng: random.Random, ctx: Ctx) -> list[Op]: ...


@dataclass(frozen=True)
class TraceEntry:
    step: int
    kind: str
    label: str
    outcome: str
    burst: bool = False

    def line(self) -> str:
        mark = "∥ " if self.burst else ""
        return f"  {self.step:>3} {mark}{self.kind} {self.label} → {self.outcome}"


@dataclass
class Trace:
    scenario: str
    seed: int
    entries: list[TraceEntry] = field(default_factory=list)
    accepted: Counter[str] = field(default_factory=Counter)
    refused: Counter[str] = field(default_factory=Counter)

    def add(self, step: int, op: Op, outcome: Outcome, *, burst: bool) -> None:
        self.entries.append(TraceEntry(step, op.kind, op.label, outcome.describe(), burst))
        (self.accepted if outcome.accepted else self.refused)[op.kind] += 1

    def tail(self) -> str:
        return "\n".join(entry.line() for entry in self.entries[-TRACE_TAIL:])

    def signature(self) -> list[tuple[int, str, str, str]]:
        return [(e.step, e.kind, e.label, e.outcome) for e in self.entries]


class SequenceFailure(AssertionError):
    pass


def failure(trace: Trace, step: int, what: str) -> SequenceFailure:
    return SequenceFailure(
        f"billing invariant failure\nscenario: {trace.scenario}\nseed: {trace.seed}"
        f"\nstep: {step}\n{what}\nlast {TRACE_TAIL} operations:\n{trace.tail()}"
        f"\n{reproduce(trace.seed, step)}"
    )


# --- Steps ---------------------------------------------------------------------------

_COUNTS = """
SELECT a.id AS account,
  (SELECT COUNT(*) FROM charges c WHERE c.billing_account_id = a.id) AS charges,
  (SELECT COUNT(*) FROM charge_adjustments c WHERE c.billing_account_id = a.id)
    AS adjustments,
  (SELECT COUNT(*) FROM payments c WHERE c.billing_account_id = a.id) AS payments,
  (SELECT COUNT(*) FROM payment_allocations c WHERE c.billing_account_id = a.id)
    AS allocations,
  (SELECT COUNT(*) FROM allocation_releases r JOIN payment_allocations pa ON pa.id =
    r.allocation_id WHERE pa.billing_account_id = a.id) AS releases,
  (SELECT COUNT(*) FROM payment_reversals c WHERE c.billing_account_id = a.id)
    AS reversals,
  (SELECT COUNT(*) FROM refunds c WHERE c.billing_account_id = a.id) AS refunds,
  (SELECT COUNT(*) FROM receipts c WHERE c.billing_account_id = a.id) AS receipts,
  (SELECT COUNT(*) FROM package_credit_transactions t JOIN packages k ON k.id =
    t.package_id WHERE k.billing_account_id = a.id) AS units,
  (SELECT COUNT(*) FROM withdrawals c WHERE c.billing_account_id = a.id)
    AS withdrawals,
  (SELECT COUNT(*) FROM charge_due_date_changes c WHERE c.billing_account_id = a.id)
    AS due_changes
FROM billing_accounts a
"""


@dataclass(frozen=True)
class Snapshot:
    nets: dict[uuid.UUID, Money]
    counts: dict[uuid.UUID, tuple[int, ...]]


async def snapshot(ctx: Ctx) -> Snapshot:
    async with ctx.read() as session:
        nets = await checks.net_balances(session)
        rows = (await session.execute(text(_COUNTS))).all()
    return Snapshot(nets, {row.account: tuple(row[1:]) for row in rows})


async def _execute(ctx: Ctx, op: Op, engine: AsyncEngine) -> Outcome | BaseException:
    try:
        return await op.execute(ctx, engine)
    except AppError as error:
        if error.code in op.refusals:
            return Outcome(False, code=error.code)
        return error
    except Exception as error:  # noqa: BLE001 - reported with the trace
        return error


async def run_step(ctx: Ctx, ops: Sequence[Op]) -> list[Outcome | BaseException]:
    """One operation on the shared engine, or a burst on separate engines."""
    if len(ops) == 1:
        return [await _execute(ctx, ops[0], ctx.engine)]
    engines = [short_lived_engine(ctx.url) for _ in ops]
    try:
        return list(
            await asyncio.gather(
                *(_execute(ctx, op, engine) for op, engine in zip(ops, engines, strict=True))
            )
        )
    finally:
        for engine in engines:
            await engine.dispose()


async def verify_step(
    ctx: Ctx,
    trace: Trace,
    step: int,
    ops: Sequence[Op],
    results: Sequence[Outcome | BaseException],
    before: Snapshot,
    catalogue: checks.Catalogue,
) -> None:
    """The outcome contract, conservation and the catalogue (module
    docstring); raises :class:`SequenceFailure`."""
    burst = len(ops) > 1
    outcomes: list[Outcome] = []
    for op, result in zip(ops, results, strict=True):
        if isinstance(result, BaseException):
            trace.entries.append(
                TraceEntry(step, op.kind, op.label, f"raised {result!r}", burst)
            )
            raise failure(
                trace,
                step,
                f"check: outcome contract (an undeclared error)\n    {result!r}",
            ) from result
        trace.add(step, op, result, burst=burst)
        outcomes.append(result)
    after = await snapshot(ctx)
    problems = [p for o in outcomes for p in o.problems]
    if problems:
        raise failure(trace, step, "check: step\n" + "\n".join(f"    {p}" for p in problems))
    for op, outcome in zip(ops, outcomes, strict=True):
        stray = [a for a in outcome.deltas if op.account is not None and a != op.account]
        if outcome.accepted and stray:
            raise failure(
                trace, step, f"check: outcome contract\n    {op.kind} declared {stray}"
            )
    sums: dict[uuid.UUID, Money] = {}
    for outcome in outcomes:
        if outcome.accepted:
            for account, delta in outcome.deltas.items():
                sums[account] = sums.get(account, Money.zero()) + delta
    broken = []
    for account, net in after.nets.items():
        moved = net - before.nets.get(account, Money.zero())
        declared = sums.get(account, Money.zero())
        if moved != declared:
            broken.append(
                f"    {ctx.label(account)}: net moved {moved}, declared {declared}"
            )
    if broken:
        raise failure(trace, step, "check: conservation\n" + "\n".join(broken))
    if not burst and not outcomes[0].accepted:
        op = ops[0]
        touched = [
            account
            for account in after.counts
            if (op.account is None or account == op.account)
            and (
                after.counts[account] != before.counts.get(account)
                or after.nets[account] != before.nets.get(account)
            )
        ]
        if touched:
            raise failure(
                trace,
                step,
                "check: refused ⇒ unchanged\n"
                + "\n".join(
                    f"    {ctx.label(a)}: {before.counts.get(a)} → {after.counts[a]}"
                    for a in touched
                ),
            )
    async with ctx.read() as session:
        found = await catalogue.run(session, today=ctx.today, after=[op.kind for op in ops])
    if found:
        raise failure(
            trace, step, "check: " + "\ncheck: ".join(v.describe() for v in found)
        )


async def run_sequence(
    ctx: Ctx,
    generator: Generator,
    *,
    scenario: str,
    seed: int,
    steps: int,
    catalogue: checks.Catalogue | None = None,
) -> Trace:
    """``steps`` steps of ``generator`` (module docstring)."""
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    trace = Trace(scenario, seed)
    catalogue = catalogue or checks.Catalogue()
    async with ctx.read() as session:
        found = await catalogue.run(session, today=ctx.today)
    if found:
        raise failure(trace, 0, "check: " + "\ncheck: ".join(v.describe() for v in found))
    for step in range(1, steps + 1):
        if rng.randrange(BURST_ODDS) == 0:
            ops = await generator.burst(rng, ctx)
        else:
            ops = [await generator.choose(rng, ctx)]
        before = await snapshot(ctx)
        results = await run_step(ctx, ops)
        await verify_step(ctx, trace, step, ops, results, before, catalogue)
    return trace


def advance_op(rng: random.Random, ctx: Ctx, *, max_days: int = 20) -> Op:
    """Move the clock 0–``max_days`` days and some hours (center-local
    midnights crossed or not)."""
    days = rng.randrange(0, max_days + 1)
    hours = rng.randrange(0, 24)

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        ctx.clock.advance(timedelta(days=days, hours=hours))
        return Outcome(True, note=f"today={ctx.today.isoformat()}")

    return Op("advance_clock", f"+{days}d{hours}h", _run)


def coin(rng: random.Random, odds: float) -> bool:
    return rng.random() < odds


def boundary_amount(
    rng: random.Random, exact: Sequence[Money], *, low: Money, high: Money
) -> tuple[Money, bool]:
    """An amount: a boundary of the current state (each of ``exact`` and
    ``exact + 0.01``, and ``0.01``) four times in ten, else uniform in
    ``[low, high]``. Returns the amount and whether it was a boundary."""
    cents = Money("0.01")
    if exact and coin(rng, 0.4):
        base = rng.choice(list(exact) + [cents])
        value = base if base == cents or coin(rng, 0.5) else base + cents
        return (value if value.is_positive() else cents), True
    span = int((high.amount - low.amount) * 100)
    cents_up = rng.randrange(0, max(span, 0) + 1)
    return low + Money(Decimal(cents_up) / 100), False


def run_in_world(
    url: str,
    world: World,
    generator: Generator,
    *,
    scenario: str,
    seed: int,
    steps: int,
    portal: bool = True,
) -> Trace:
    """One sequence on the world copy ``url`` under a clock frozen at the
    world's :data:`~.world.NOW`, then (``portal``) the D4 portal check."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.db import async_database_url
    from tests.conftest import frozen_clock
    from tests.integration.billing_invariants import routes
    from tests.integration.billing_invariants.world import NOW

    with frozen_clock(NOW.astimezone(UTC)) as clock:

        async def _main() -> Trace:
            engine = create_async_engine(
                async_database_url(settings_for(url)), pool_size=3, max_overflow=2
            )
            ctx = Ctx(url, world, clock, engine)
            try:
                return await run_sequence(
                    ctx, generator, scenario=scenario, seed=seed, steps=steps
                )
            finally:
                await engine.dispose()

        trace = asyncio.run(_main())
        if portal:
            with routes.auth_client(url) as client:
                problems = routes.portal_mismatches(client, world)
            if problems:
                raise failure(trace, steps, "check: portal (D4)\n    " + "\n    ".join(problems))
    return trace
