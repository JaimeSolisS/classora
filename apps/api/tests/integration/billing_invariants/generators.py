"""The ledger scenario's generator (#103 D1), shared by the ledger
sequences, the route-driven sequence (D8) and the determinism test (B2)."""

import random

from tests.integration.billing_invariants import harness
from tests.integration.billing_invariants import operations as ops
from tests.integration.billing_invariants.harness import Ctx, Op

JOB_WEIGHTS = {
    "run_sessions": 6,
    "run_reminders": 3,
    "advance_clock": 5,
    "toggle_billing": 1,
}


class LedgerGenerator:
    """D1: the ledger kinds of :mod:`.operations`, the jobs and the clock."""

    async def choose(self, rng: random.Random, ctx: Ctx) -> Op:
        state = await ops.read_state(ctx)
        table = {name: weight for name, (weight, _) in ops.LEDGER_KINDS.items()}
        table.update(JOB_WEIGHTS)
        kind = ops.weighted(rng, table)
        if kind == "run_sessions":
            return ops.run_sessions_op()
        if kind == "run_reminders":
            return ops.run_reminders_op()
        if kind == "advance_clock":
            return harness.advance_op(rng, ctx)
        if kind == "toggle_billing":
            return ops.toggle_billing(rng, ctx, state)
        built = ops.LEDGER_KINDS[kind][1](rng, ctx, state)
        return built if built is not None else ops.manual_charge(rng, ctx, state)

    async def burst(self, rng: random.Random, ctx: Ctx) -> list[Op]:
        state = await ops.read_state(ctx)
        info = ops.family(rng, ctx)
        out: list[Op] = []
        for _ in range(rng.choice([2, 2, 3])):
            kind = rng.choice(ops.MONEY_KINDS)
            built = ops.for_family(rng, ctx, state, ops.LEDGER_KINDS[kind][1], info)
            out.append(
                built if built is not None else ops.manual_charge(rng, ctx, state, info)
            )
        return out
