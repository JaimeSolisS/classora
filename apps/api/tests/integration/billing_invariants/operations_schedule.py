"""Schedule operation kinds (#103 D3): session cancellations (center, and
family change requests early or late), duration changes, reschedules,
closure skips and restores, then the reconciliation job; group and cohort
enrollments and plain M4 withdrawals (the #92 hooks), the enrollment
sweeper; standalone and group-linked tuition plans with pauses and the
tuition job; #101 withdrawal settlements of enrollments; a student moving to
another family.

Schedule changes are committed with raw SQL where the reconciliation job
only reads the committed result (the ``schedule_reconciliation_support``
pattern: #99 is a state-derived sweeper); enrollments, plans, pauses and
settlements go through their real workflow entry points.

Compound operations declare the effect of what their own transaction wrote
(:func:`effect_of_transaction`: the ledger rows whose ``created_at`` is the
transaction's ``now()``).
"""

import random
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.money import Money
from app.modules.billing.tuition import period_start
from app.modules.scheduling.schemas import GroupEnrollmentCreate, GroupWithdrawal
from app.workflows import (
    cohort_enrollment,
    enrollment_charges,
    group_enrollment,
    schedule_reconciliation,
    tuition_charges,
    tuition_plans,
)
from app.workflows.cohort_schemas import CohortEnrollmentCreate, CohortWithdrawal
from app.workflows.tuition_plan_schemas import TuitionPauseCreate, TuitionPlanCreate
from tests.factories.scheduling import persist_tutor_user
from tests.integration.billing_invariants import operations as ops
from tests.integration.billing_invariants import operations_packages as pkg
from tests.integration.billing_invariants.harness import (
    Ctx,
    Op,
    Outcome,
    coin,
)
from tests.integration.billing_invariants.operations import State
from tests.integration.billing_invariants.world import FamilyInfo

ENROLL_REFUSALS = frozenset(
    {
        "already_enrolled",
        "scheduling_conflict",
        "group_ended",
        "cohort_ended",
        "enrollment_not_active",
        "version_conflict",
        "enrollment_not_found",
    }
)
TUITION_REFUSALS = frozenset(
    {
        "tuition_plan_price_invalid",
        "tuition_plan_enrollment_invalid",
        "tuition_plan_starts_on_invalid",
        "tuition_plan_ends_on_invalid",
        "tuition_pause_invalid",
        "tuition_plan_overlap",
        "tuition_plan_enrollment_fee_charged",
        "tuition_plan_sessions_charged",
        "tuition_plan_ended",
        "tuition_pause_overlap",
        "student_has_no_family",
        "version_conflict",
    }
)
EFFECT_SQL = (
    f"SELECT account, SUM(amount) AS total FROM ({ops._NEW_ROWS}) r"  # noqa: S608 - constant SQL
    " WHERE created_at = :t GROUP BY account"
)


async def transaction_now(session: AsyncSession) -> datetime:
    value: datetime = await session.scalar(text("SELECT now()"))
    return value


async def effect_of_transaction(ctx: Ctx, at: datetime) -> dict[uuid.UUID, Money]:
    rows = await ctx.rows(EFFECT_SQL, t=at)
    return {row.account: Money(row.total) for row in rows}


def _account_of(ctx: Ctx, student_label: str) -> uuid.UUID | None:
    info = ctx.world.family_of(student_label)
    return None if info is None else info.account


def _compound(
    kind: str,
    label: str,
    account: uuid.UUID | None,
    work: Callable[[Ctx, Any], Any],
    refusals: frozenset[str],
) -> Op:
    """An operation whose delta is what its own transaction wrote. Any
    account may move (a student may have changed family), so a refusal
    must leave every account unchanged; ``account`` is only documentation
    of the family it targets."""
    del account

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        async with ctx.uow(engine) as uow:
            at = await transaction_now(uow.session)
            note = await work(ctx, uow)
        deltas = await effect_of_transaction(ctx, at)
        return Outcome(True, deltas=deltas, note=note or "")

    return Op(kind, label, _run, refusals=refusals)


# --- Sessions and reconciliation -----------------------------------------------------


async def our_sessions(ctx: Ctx, states: tuple[str, ...]) -> list[Any]:
    """Individual sessions this sequence created, and the world's group
    sessions, in ``states``, by label."""
    rows = await ctx.rows(
        "SELECT id, state, kind FROM class_sessions WHERE state = ANY(:s)",
        s=list(states),
    )
    return sorted(
        (row for row in rows if row.id in ctx.labels), key=lambda row: ctx.label(row.id)
    )


def cancel_session(
    rng: random.Random, ctx: Ctx, state: State, *, sessions: list[Any]
) -> Op | None:
    """A center cancellation, or an approved family cancellation request
    (on time or late, makeup or not), committed like the scheduling paths
    do (state ``cancelled``, ``version + 1``)."""
    target = ops.pick(rng, sessions)
    if target is None:
        return None
    by_family = coin(rng, 0.5)
    classification = rng.choice(["on_time", "late"])
    makeup = coin(rng, 0.3)

    async def _work(ctx: Ctx, uow: Any) -> str:
        s = uow.session
        participant = None
        if by_family:
            participant = await s.scalar(
                text(
                    "SELECT student_id FROM class_session_participants"
                    " WHERE class_session_id = :id ORDER BY student_id LIMIT 1"
                ),
                {"id": target.id},
            )
        if by_family and participant is not None:
            policy = await s.scalar(
                text(
                    "SELECT id FROM scheduling_policies ORDER BY effective_from LIMIT 1"
                )
            )
            times = (
                await s.execute(
                    text(
                        "SELECT starts_at, ends_at FROM class_sessions WHERE id = :id"
                    ),
                    {"id": target.id},
                )
            ).one()
            until = times.starts_at + timedelta(days=30)
            await s.execute(
                text(
                    "INSERT INTO session_change_requests (class_session_id, kind,"
                    " initiated_by, requested_by_user_id, student_id,"
                    " session_starts_at, session_ends_at, policy_id, classification,"
                    " makeup_allowed, makeup_until, status, exception,"
                    " decided_by_user_id, resolved_at, final_classification,"
                    " final_makeup_allowed, final_makeup_until, created_at)"
                    " VALUES (:s, 'cancellation', 'family', :u, :st, :start, :end, :p,"
                    " :c, :m, :until, 'approved', false, :u, :now, :c, :m, :until,"
                    " :now)"
                ),
                {
                    "s": target.id,
                    "u": ctx.world.owner.id,
                    "st": participant,
                    "start": times.starts_at,
                    "end": times.ends_at,
                    "p": policy,
                    "c": classification,
                    "m": makeup,
                    "until": until if makeup else None,
                    "now": ctx.clock.now(),
                },
            )
        await s.execute(
            text(
                "UPDATE class_sessions SET state = 'cancelled', cancelled_at = :at,"
                " cancellation_reason = :r, version = version + 1 WHERE id = :id"
            ),
            {"id": target.id, "at": ctx.clock.now(), "r": "Cancelada"},
        )
        return ""

    who = (
        f"family {classification}{' makeup' if makeup else ''}"
        if by_family
        else "center"
    )
    return _compound(
        "cancel_session", f"{ctx.label(target.id)} {who}", None, _work, frozenset()
    )


def _session_sql(kind: str, sql: str, describe: str) -> Callable[..., Op | None]:
    def build(
        rng: random.Random,
        ctx: Ctx,
        state: State,
        *,
        sessions: list[Any],
        **params: Any,
    ) -> Op | None:
        target = ops.pick(rng, sessions)
        if target is None:
            return None
        values = {name: choose(rng) for name, choose in params.items()}

        async def _work(ctx: Ctx, uow: Any) -> str:
            await uow.session.execute(text(sql), {"id": target.id, **values})
            return ""

        return _compound(
            kind,
            f"{ctx.label(target.id)} {describe.format(**values)}".strip(),
            None,
            _work,
            frozenset(),
        )

    return build


retime_session = _session_sql(
    "retime_session",
    "UPDATE class_sessions SET ends_at = starts_at + make_interval(mins => :m),"
    " scheduled_minutes = :m, version = version + 1 WHERE id = :id",
    "{m}min",
)
move_session = _session_sql(
    "move_session",
    "UPDATE class_sessions SET starts_at = starts_at + make_interval(days => :d),"
    " ends_at = ends_at + make_interval(days => :d), version = version + 1"
    " WHERE id = :id",
    "{d:+d}d",
)
skip_for_closure = _session_sql(
    "closure_skip",
    "UPDATE class_sessions SET state = 'skipped_closure', version = version + 1"
    " WHERE id = :id",
    "",
)
restore_from_closure = _session_sql(
    "closure_restore",
    "UPDATE class_sessions SET state = 'confirmed', version = version + 1"
    " WHERE id = :id",
    "",
)


def confirm_group_session(
    rng: random.Random, ctx: Ctx, state: State, *, sessions: list[Any]
) -> Op | None:
    """A group session gets its tutor (``confirmed``), so the session
    sweeper sees it."""
    target = ops.pick(rng, sessions)
    if target is None:
        return None

    async def _work(ctx: Ctx, uow: Any) -> str:
        tutor = await persist_tutor_user(uow.session)
        await uow.session.execute(
            text(
                "UPDATE class_sessions SET state = 'confirmed', tutor_user_id = :t,"
                " first_confirmed_at = COALESCE(first_confirmed_at, starts_at -"
                " interval '7 days'), version = version + 1 WHERE id = :id"
            ),
            {"id": target.id, "t": tutor},
        )
        return ""

    return _compound(
        "confirm_group_session", ctx.label(target.id), None, _work, frozenset()
    )


def run_reconcile_op() -> Op:
    async def _runner(ctx: Ctx) -> Any:
        return await schedule_reconciliation.run_reconcile_session_charges(
            ctx.settings, now=ctx.clock.now()
        )

    async def _extra(ctx: Ctx, result: Any, rows: list[Any]) -> list[str]:
        """#99: nothing the run wrote is for a non-participant."""
        ids = [r.id for r in rows if r.row == "adjustment"]
        stray = await ctx.rows(
            "SELECT a.id, c.class_session_id, c.student_id FROM charge_adjustments a"
            " JOIN charges c ON c.id = a.charge_id WHERE a.id = ANY(:ids) AND NOT"
            " EXISTS (SELECT 1 FROM class_session_participants p WHERE"
            " p.class_session_id = c.class_session_id AND p.student_id = c.student_id)",
            ids=ids,
        )
        return [
            f"S4: reconcile adjusted {ctx.label(r.class_session_id)}"
            f"/{ctx.label(r.student_id)},"
            " not a participant"
            for r in stray
        ]

    return ops.job_op("run_reconcile", _runner, extra=_extra)


def run_enrollments_op() -> Op:
    async def _runner(ctx: Ctx) -> Any:
        return await enrollment_charges.run_post_enrollment_charges(
            ctx.settings, now=ctx.clock.now()
        )

    return ops.job_op("run_enrollments", _runner, posted=lambda r: r.posted)


def run_tuition_op() -> Op:
    async def _runner(ctx: Ctx) -> Any:
        return await tuition_charges.run_post_tuition_charges(
            ctx.settings, now=ctx.clock.now()
        )

    return ops.job_op("run_tuition", _runner, posted=lambda r: r.posted)


# --- Enrollments ---------------------------------------------------------------------


async def enrollments(ctx: Ctx, kind: str) -> list[Any]:
    """Our group or cohort enrollments (labelled), active ones first."""
    table = "group_enrollments" if kind == "group" else "cohort_enrollments"
    owner = "group_id" if kind == "group" else "cohort_id"
    rows = await ctx.rows(
        f"SELECT id, {owner} AS owner, student_id, version, withdrawn_at"  # noqa: S608
        f" FROM {table}"
    )
    return sorted(
        (row for row in rows if row.id in ctx.labels), key=lambda row: ctx.label(row.id)
    )


def _student(rng: random.Random, ctx: Ctx) -> tuple[str, uuid.UUID]:
    label = rng.choice(ctx.world.billed_students + (["X1"] if coin(rng, 0.1) else []))
    return label, ctx.world.students[label]


def enroll(rng: random.Random, ctx: Ctx, state: State) -> Op:
    kind = rng.choice(["G1", "G2", "CO1"])
    student_label, student = _student(rng, ctx)

    async def _work(ctx: Ctx, uow: Any) -> str:
        if kind == "CO1":
            change: Any = await cohort_enrollment.enroll_in_cohort(
                uow,
                ctx.world.cohort,
                CohortEnrollmentCreate(student_id=student),
                actor_id=ctx.world.owner.id,
            )
            return ctx.new_label("CE", change.enrollment_id)
        change = await group_enrollment.enroll_student(
            uow.session,
            ctx.world.groups[kind],
            GroupEnrollmentCreate(student_id=student),
            actor_id=ctx.world.owner.id,
        )
        return ctx.new_label("GE", change.enrollment_id)

    return _compound(
        "enroll",
        f"{kind}/{student_label}",
        _account_of(ctx, student_label),
        _work,
        ENROLL_REFUSALS,
    )


def withdraw_m4(
    rng: random.Random, ctx: Ctx, state: State, *, rows: list[Any], kind: str
) -> Op | None:
    """The plain M4 withdrawal (the #92 removal credits run in its hook)."""
    target = ops.pick(rng, rows)
    if target is None:
        return None
    student_label = ctx.label(target.student_id)

    async def _work(ctx: Ctx, uow: Any) -> str:
        if kind == "cohort":
            await cohort_enrollment.withdraw_from_cohort(
                uow,
                target.owner,
                target.id,
                CohortWithdrawal(version=target.version, reason="Baja"),
                actor_id=ctx.world.owner.id,
            )
        else:
            await group_enrollment.withdraw_student(
                uow.session,
                target.owner,
                target.id,
                GroupWithdrawal(version=target.version, reason="Baja"),
                actor_id=ctx.world.owner.id,
            )
        return ""

    return _compound(
        "withdraw_m4",
        f"{ctx.label(target.id)}",
        _account_of(ctx, student_label),
        _work,
        ENROLL_REFUSALS,
    )


def settle_enrollment(
    rng: random.Random, ctx: Ctx, state: State, *, rows: list[Any], kind: str
) -> Op | None:
    """A #101 withdrawal settlement of an enrollment (account credit or
    refund)."""
    target = ops.pick(rng, rows)
    if target is None:
        return None
    account = _account_of(ctx, ctx.label(target.student_id))
    if account is None:
        return None
    return pkg.withdraw(
        rng,
        ctx,
        state,
        kind=f"{kind}_enrollment",
        subject=target.id,
        subject_label=ctx.label(target.id),
        account=account,
    )


# --- Tuition plans -------------------------------------------------------------------


def create_plan(
    rng: random.Random, ctx: Ctx, state: State, *, linked: list[Any]
) -> Op | None:
    """A standalone monthly plan of ``TUI``, or one linked to a ``G2``
    enrollment (the group's ``per_period`` item)."""
    starts = ctx.today + timedelta(days=rng.randrange(-15, 10))
    target = ops.pick(rng, linked) if coin(rng, 0.4) else None
    if target is not None:
        student_label = ctx.label(target.student_id)
        body = TuitionPlanCreate(
            student_id=target.student_id,
            service_price_id=ctx.world.group_tuition_item,
            group_enrollment_id=target.id,
            starts_on=starts,
            due_offset_days=rng.randrange(0, 10),
        )
        what = f"linked {ctx.label(target.id)}"
    else:
        student_label, student = _student(rng, ctx)
        body = TuitionPlanCreate(
            student_id=student,
            service_price_id=ctx.world.tuition_item,
            starts_on=starts,
            due_offset_days=rng.randrange(0, 10),
        )
        what = f"TUI/{student_label}"

    async def _work(ctx: Ctx, uow: Any) -> str:
        detail, _ = await tuition_plans.create_tuition_plan(uow.session, body)
        return ctx.new_label("TP", detail.id)

    return _compound(
        "create_plan",
        f"{what} from {starts}",
        _account_of(ctx, student_label),
        _work,
        TUITION_REFUSALS,
    )


def pause_plan(
    rng: random.Random, ctx: Ctx, state: State, *, plans: list[Any]
) -> Op | None:
    target = ops.pick(rng, plans)
    if target is None:
        return None
    # A pause starts on a period start and ends on a period end (#93 K4):
    # periods from the current one on, open-ended three times in ten.
    current = max(
        0,
        (ctx.today.year - target.starts_on.year) * 12
        + ctx.today.month
        - target.starts_on.month,
    )
    k = current + rng.randrange(0, 3)
    starts = period_start(target.starts_on, target.frequency, k)
    ends = (
        None
        if coin(rng, 0.3)
        else period_start(
            target.starts_on, target.frequency, k + 1 + rng.randrange(0, 2)
        )
        - timedelta(days=1)
    )

    async def _work(ctx: Ctx, uow: Any) -> str:
        version = await uow.session.scalar(
            text("SELECT version FROM tuition_plans WHERE id = :id"), {"id": target.id}
        )
        await tuition_plans.add_tuition_pause(
            uow.session,
            target.id,
            TuitionPauseCreate(version=version, starts_on=starts, ends_on=ends),
        )
        return ""

    return _compound(
        "pause_plan",
        f"{ctx.label(target.id)} {starts}–{ends or '…'}",
        None,
        _work,
        TUITION_REFUSALS,
    )


async def plans(ctx: Ctx) -> list[Any]:
    rows = await ctx.rows("SELECT id, starts_on, frequency FROM tuition_plans")
    return sorted(
        (r for r in rows if r.id in ctx.labels), key=lambda r: ctx.label(r.id)
    )


def move_family(rng: random.Random, ctx: Ctx, state: State) -> Op:
    """A student leaves their family for a new one (raw rows, as a fixture
    may): their plans report ``family_changed`` and post nothing (#93 K9);
    manual charges on the old family are refused."""
    student_label = rng.choice(ctx.world.billed_students)
    student = ctx.world.students[student_label]

    async def _work(ctx: Ctx, uow: Any) -> str:
        from tests.factories.families import FamilyFactory

        s = uow.session
        await s.execute(
            text("DELETE FROM family_students WHERE student_id = :s"), {"s": student}
        )
        family = FamilyFactory.build()
        s.add(family)
        await s.flush()
        await s.execute(
            text("INSERT INTO family_students (family_id, student_id) VALUES (:f, :s)"),
            {"f": family.id, "s": student},
        )
        ctx.labels[family.id] = f"new-family-of-{student_label}"
        return ""

    return _compound("move_family", student_label, None, _work, frozenset())


def manual_credit(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op | None:
    """A manual credit (negative adjustment) only."""
    for _ in range(4):
        built = ops.manual_adjustment(rng, ctx, state, info)
        if built is None or "-" in built.label.split()[-1]:
            return built
    return None
