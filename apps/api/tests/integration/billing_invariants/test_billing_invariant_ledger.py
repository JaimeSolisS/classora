"""Ledger scenario (#103 D1, D4–D7): randomized sequences of the money
operations, the session sweeper, due-date changes, reminders, clock moves
and billing switched off and on, with concurrent bursts, on a fresh copy of
the world per seed. Every step runs the whole check catalogue; every
sequence ends with the portal check.

Reproduce a failure with the line it prints, e.g. ``make
test-billing-invariants SEEDS=3 STEPS=17`` (same commit)."""

import random
from collections import Counter
from collections.abc import Callable

import pytest

from tests.integration.billing_invariants import harness
from tests.integration.billing_invariants import operations as ops
from tests.integration.billing_invariants.generators import JOB_WEIGHTS, LedgerGenerator
from tests.integration.billing_invariants.harness import Ctx, Op, Trace
from tests.integration.billing_invariants.world import Template

DEFAULT_SEEDS = (1, 2, 3, 4)
DEFAULT_STEPS = 60

TRACES: list[Trace] = []


@pytest.mark.parametrize("seed", harness.seed_params(DEFAULT_SEEDS))
def test_billing_invariant_ledger_sequence(
    seed: int, world_template: Template, world_copy: Callable[[], str]
) -> None:
    trace = harness.run_in_world(
        world_copy(),
        world_template.world,
        LedgerGenerator(),
        scenario="ledger",
        seed=seed,
        steps=harness.step_count(DEFAULT_STEPS),
    )
    TRACES.append(trace)


REFUSABLE = frozenset(
    {
        "manual_charge",
        "manual_adjustment",
        "record_payment",
        "allocate_credit",
        "reverse_payment",
        "refund_payment",
        "due_date_change",
    }
)


def test_billing_invariant_ledger_generator_reaches_both_sides() -> None:
    """D5: over the default seeds every kind was accepted at least once and
    every kind with a refusal path was refused at least once."""
    ran = {trace.seed for trace in TRACES}
    if (
        not set(DEFAULT_SEEDS) <= ran
        or harness.step_count(DEFAULT_STEPS) != DEFAULT_STEPS
    ):
        pytest.skip("needs the default seeds and steps in this run")
    accepted: Counter[str] = Counter()
    refused: Counter[str] = Counter()
    for trace in TRACES:
        accepted.update(trace.accepted)
        refused.update(trace.refused)
    kinds = set(ops.LEDGER_KINDS) | set(JOB_WEIGHTS)
    assert {k for k in kinds if accepted[k] == 0} == set(), (accepted, refused)
    assert {k for k in REFUSABLE if refused[k] == 0} == set(), (accepted, refused)


ROUTE_SEED = 7
ROUTE_STEPS = 30
ROUTE_KINDS = (*ops.MONEY_KINDS, "due_date_change")


class RouteGenerator(LedgerGenerator):
    """D8: the D1 money operations and due-date changes only (the clock
    stays, so the reauthentications stay recent)."""

    async def choose(self, rng: random.Random, ctx: Ctx) -> Op:
        state = await ops.read_state(ctx)
        kind = ops.weighted(rng, {k: ops.LEDGER_KINDS[k][0] for k in ROUTE_KINDS})
        built = ops.LEDGER_KINDS[kind][1](rng, ctx, state)
        return built if built is not None else ops.manual_charge(rng, ctx, state)


def test_billing_invariant_ledger_through_the_routes(
    world_template: Template, world_copy: Callable[[], str]
) -> None:
    """D8: one seed of the money operations through the real routes, with
    the same catalogue and outcome contract (a 4xx code must be one of the
    operation's declared refusals)."""
    trace = harness.run_in_world(
        world_copy(),
        world_template.world,
        RouteGenerator(),
        scenario="ledger-routes",
        seed=ROUTE_SEED,
        steps=harness.step_count(ROUTE_STEPS),
        through_routes=True,
    )
    assert sum(trace.accepted.values()) >= 10, trace.accepted
    assert sum(trace.refused.values()) >= 1, trace.refused
