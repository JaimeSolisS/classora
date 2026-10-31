"""Schedule scenario (#103 D3, D4–D7): charged individual and group
sessions cancelled (center, family early or late), retimed, rescheduled,
skipped for a closure and restored, then reconciled; group and cohort
enrollments, M4 withdrawals and re-enrollments with the enrollment
sweeper; standalone and group-linked tuition plans with pauses and the
tuition job; #101 settlements (account credit and refund); a student
changing family; payments and manual credits mixed in, with concurrent
bursts. Every step runs the whole catalogue (S1–S4 and the reconcile step
check, T1, T2, J5, W2–W5 …); every sequence ends with the portal check."""

import random
from collections import Counter
from collections.abc import Callable

import pytest

from tests.integration.billing_invariants import harness
from tests.integration.billing_invariants import operations as ops
from tests.integration.billing_invariants import operations_schedule as sch
from tests.integration.billing_invariants.harness import Ctx, Op, Trace
from tests.integration.billing_invariants.world import Template

DEFAULT_SEEDS = (21, 22, 23, 24)
DEFAULT_STEPS = 60

WEIGHTS = {
    "confirm_session": 8,
    "confirm_group_session": 5,
    "run_sessions": 6,
    "cancel_session": 5,
    "retime_session": 4,
    "move_session": 3,
    "closure_skip": 3,
    "closure_restore": 3,
    "run_reconcile": 7,
    "enroll": 6,
    "withdraw_m4": 3,
    "settle_enrollment": 3,
    "run_enrollments": 4,
    "create_plan": 5,
    "pause_plan": 7,
    "run_tuition": 4,
    "advance_clock": 5,
    "record_payment": 6,
    "manual_credit": 3,
    "move_family": 1,
}

CHARGED = ("confirmed", "completed")

TRACES: list[Trace] = []


class ScheduleGenerator:
    def __init__(self) -> None:
        self.moved = False

    async def choose(self, rng: random.Random, ctx: Ctx) -> Op:
        state = await ops.read_state(ctx)
        kind = ops.weighted(rng, WEIGHTS)
        built: Op | None = None
        if kind == "confirm_session":
            built = ops.confirm_session(rng, ctx, state)
        elif kind == "confirm_group_session":
            built = sch.confirm_group_session(
                rng, ctx, state, sessions=await sch.our_sessions(ctx, ("unassigned",))
            )
        elif kind == "run_sessions":
            built = ops.run_sessions_op()
        elif kind == "cancel_session":
            built = sch.cancel_session(
                rng, ctx, state, sessions=await sch.our_sessions(ctx, CHARGED)
            )
        elif kind == "retime_session":
            built = sch.retime_session(
                rng,
                ctx,
                state,
                sessions=await sch.our_sessions(ctx, CHARGED),
                m=lambda r: r.choice([30, 45, 60, 90]),
            )
        elif kind == "move_session":
            built = sch.move_session(
                rng,
                ctx,
                state,
                sessions=await sch.our_sessions(ctx, CHARGED),
                d=lambda r: r.choice([-2, -1, 1, 2, 3]),
            )
        elif kind == "closure_skip":
            built = sch.skip_for_closure(
                rng, ctx, state, sessions=await sch.our_sessions(ctx, CHARGED)
            )
        elif kind == "closure_restore":
            built = sch.restore_from_closure(
                rng, ctx, state, sessions=await sch.our_sessions(ctx, ("skipped_closure",))
            )
        elif kind == "run_reconcile":
            built = sch.run_reconcile_op()
        elif kind == "enroll":
            built = sch.enroll(rng, ctx, state)
        elif kind in ("withdraw_m4", "settle_enrollment"):
            which = rng.choice(["group", "cohort"])
            rows = await sch.enrollments(ctx, which)
            active = [r for r in rows if r.withdrawn_at is None]
            pool = active if active and ops.coin(rng, 0.85) else rows
            if kind == "withdraw_m4":
                built = sch.withdraw_m4(rng, ctx, state, rows=pool, kind=which)
            else:
                built = sch.settle_enrollment(rng, ctx, state, rows=pool, kind=which)
        elif kind == "run_enrollments":
            built = sch.run_enrollments_op()
        elif kind == "create_plan":
            linked = [
                r
                for r in await sch.enrollments(ctx, "group")
                if r.owner == ctx.world.groups["G2"] and r.withdrawn_at is None
            ]
            built = sch.create_plan(rng, ctx, state, linked=linked)
        elif kind == "pause_plan":
            built = sch.pause_plan(rng, ctx, state, plans=await sch.plans(ctx))
        elif kind == "run_tuition":
            built = sch.run_tuition_op()
        elif kind == "advance_clock":
            built = harness.advance_op(rng, ctx, max_days=12)
        elif kind == "record_payment":
            built = ops.record_payment(rng, ctx, state)
        elif kind == "manual_credit":
            built = sch.manual_credit(rng, ctx, state)
        elif kind == "move_family" and not self.moved:
            self.moved = True
            built = sch.move_family(rng, ctx, state)
        return built if built is not None else ops.confirm_session(rng, ctx, state)

    async def burst(self, rng: random.Random, ctx: Ctx) -> list[Op]:
        """Same account: payments, manual credits and a settlement at
        once."""
        state = await ops.read_state(ctx)
        info = ops.family(rng, ctx)
        out: list[Op] = []
        for _ in range(rng.choice([2, 2, 3])):
            choice = rng.choice(["pay", "credit", "allocate"])
            if choice == "pay":
                built = ops.record_payment(rng, ctx, state, info)
            elif choice == "credit":
                built = sch.manual_credit(rng, ctx, state, info)
            else:
                built = ops.allocate_credit(rng, ctx, state, info)
            out.append(built if built is not None else ops.record_payment(rng, ctx, state, info))
        return out


@pytest.mark.parametrize("seed", harness.seed_params(DEFAULT_SEEDS))
def test_billing_invariant_schedule_sequence(
    seed: int, world_template: Template, world_copy: Callable[[], str]
) -> None:
    trace = harness.run_in_world(
        world_copy(),
        world_template.world,
        ScheduleGenerator(),
        scenario="schedule",
        seed=seed,
        steps=harness.step_count(DEFAULT_STEPS),
    )
    TRACES.append(trace)


REFUSABLE = frozenset({"enroll", "create_plan"})


def test_billing_invariant_schedule_generator_reaches_both_sides() -> None:
    """D5 over the default seeds (see the ledger module)."""
    ran = {trace.seed for trace in TRACES}
    if not set(DEFAULT_SEEDS) <= ran or harness.step_count(DEFAULT_STEPS) != DEFAULT_STEPS:
        pytest.skip("needs the default seeds and steps in this run")
    accepted: Counter[str] = Counter()
    refused: Counter[str] = Counter()
    for trace in TRACES:
        accepted.update(trace.accepted)
        refused.update(trace.refused)
    kinds = (set(WEIGHTS) - {"settle_enrollment", "manual_credit"}) | {
        "withdraw_credit",
        "manual_adjustment",
    }
    assert {k for k in kinds if accepted[k] == 0} == set(), (accepted, refused)
    assert {k for k in REFUSABLE if refused[k] == 0} == set(), (accepted, refused)
