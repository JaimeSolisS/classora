"""Package scenario (#103 D2, D4–D7): purchases, covered sessions,
closeout approvals (consumption), the session sweeper, the expiry job with
the clock moving past ``expires_on``, cancellations and package
withdrawals (account credit and refund), mixed with payments, allocations,
reversals and refunds, with concurrent bursts. Every step runs the whole
catalogue (I8, I9, K1, K2, X2–X4, W3, W4 …); every sequence ends with the
portal check."""

import random
from collections import Counter
from collections.abc import Callable

import pytest

from app.core.money import Money
from tests.integration.billing_invariants import harness
from tests.integration.billing_invariants import operations as ops
from tests.integration.billing_invariants import operations_packages as pkg
from tests.integration.billing_invariants.harness import Ctx, Op, Trace
from tests.integration.billing_invariants.world import Template

DEFAULT_SEEDS = (11, 12, 13, 14)
DEFAULT_STEPS = 60

WEIGHTS = {
    "purchase_package": 7,
    "confirm_session": 10,
    "approve_closeout": 9,
    "run_sessions": 6,
    "run_expiry": 4,
    "advance_clock": 6,
    "cancel_package": 4,
    "withdraw_package": 4,
    "record_payment": 7,
    "allocate_credit": 3,
    "reverse_payment": 4,
    "refund_payment": 3,
    "pay_package": 5,
}

TRACES: list[Trace] = []


class PackageGenerator:
    async def choose(self, rng: random.Random, ctx: Ctx) -> Op:
        state = await ops.read_state(ctx)
        kind = ops.weighted(rng, WEIGHTS)
        built: Op | None
        if kind == "purchase_package":
            built = pkg.purchase(rng, ctx, state)
        elif kind == "confirm_session":
            built = ops.confirm_session(rng, ctx, state, subject="PKG")
        elif kind == "approve_closeout":
            built = pkg.approve_closeout(
                rng, ctx, state, sessions=await pkg._pending_sessions(ctx)
            )
        elif kind == "run_sessions":
            built = ops.run_sessions_op()
        elif kind == "run_expiry":
            built = pkg.run_expiry_op()
        elif kind == "advance_clock":
            built = harness.advance_op(rng, ctx)
        elif kind == "cancel_package":
            built = pkg.cancel(rng, ctx, state)
        elif kind == "withdraw_package":
            built = pkg.withdraw_package(rng, ctx, state)
        elif kind == "pay_package":
            built = pkg.pay_package(rng, ctx, state)
        elif kind == "allocate_credit":
            # Credit is rare here (cancellations, withdrawals): draw on it.
            with_credit = [
                info
                for _, info in sorted(ctx.world.families.items())
                if state.credit.get(info.account, Money.zero()).is_positive()
            ]
            built = ops.allocate_credit(rng, ctx, state, ops.pick(rng, with_credit))
        else:
            built = ops.LEDGER_KINDS[kind][1](rng, ctx, state)
        return built if built is not None else pkg.purchase(rng, ctx, state)

    async def burst(self, rng: random.Random, ctx: Ctx) -> list[Op]:
        """Same account: a cancellation, a withdrawal, a consumption of the
        package's student, a purchase or a payment, at once."""
        state = await ops.read_state(ctx)
        info = ops.family(rng, ctx)
        out: list[Op] = []
        for _ in range(rng.choice([2, 2, 3])):
            choice = rng.choice(["cancel", "withdraw", "approve", "pay", "purchase"])
            built: Op | None
            if choice == "cancel":
                built = pkg.cancel(rng, ctx, state, info)
            elif choice == "withdraw":
                built = pkg.withdraw_package(rng, ctx, state, info)
            elif choice == "approve":
                built = pkg.approve_closeout(
                    rng, ctx, state, sessions=await pkg._pending_sessions(ctx)
                )
            elif choice == "pay":
                built = ops.record_payment(rng, ctx, state, info)
            else:
                built = pkg.purchase(rng, ctx, state, info)
            out.append(
                built if built is not None else pkg.purchase(rng, ctx, state, info)
            )
        return out


@pytest.mark.parametrize("seed", harness.seed_params(DEFAULT_SEEDS))
def test_billing_invariant_packages_sequence(
    seed: int, world_template: Template, world_copy: Callable[[], str]
) -> None:
    trace = harness.run_in_world(
        world_copy(),
        world_template.world,
        PackageGenerator(),
        scenario="packages",
        seed=seed,
        steps=harness.step_count(DEFAULT_STEPS),
    )
    TRACES.append(trace)


REFUSABLE = frozenset({"cancel_package", "purchase_package"})


def test_billing_invariant_packages_generator_reaches_both_sides() -> None:
    """D5 over the default seeds (see the ledger module)."""
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
    kinds = (set(WEIGHTS) - {"withdraw_package"}) | {
        "withdraw_credit",
        "withdraw_refund",
    }
    assert {k for k in kinds if accepted[k] == 0} == set(), (accepted, refused)
    refusable = REFUSABLE | {"withdraw_credit"}
    assert {k for k in refusable if refused[k] == 0} == set(), (accepted, refused)
