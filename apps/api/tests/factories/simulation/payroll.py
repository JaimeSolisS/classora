"""``payroll`` (#553 HF-01e): the tutors' pay over the term, through the
payroll workflows and the payroll jobs' per-item steps, after ``billing``
(so each day's classes are held, approved and corrected first).

**Setup** (:data:`~.anchor.SIM_SETUP`, the Finance account): payroll on
from 31 Aug (also the biweekly anchor), the 50 % ``late`` and ``no_show``
rule versions, the two pay item types, and each tutor's terms and rates
(:mod:`.payroll_plan`; the joiner's an hour after they join).

**Each day**, under :func:`~.anchor.sim_clock` at each instant:

- 01:50 the payroll-period job step on the days a period starts
  (``payroll_periods.open_tutor_periods`` for every tutor with terms);
- the dated actions: the pay items, the manual adjustments, the
  carry-forward, ``prepare`` (Finance) and ``finalize`` (the Owner) of each
  ended period, its payment (Finance, the period's total), the reversal
  (the Owner) and the second payment;
- 23:45, on a day with work after lock, the adjustment job step
  (``select_candidates`` + ``post_one``,
  :func:`~tests.factories.demo_payroll.post_adjustments`): the late
  approvals and the correction HF-01b's timetable makes after lock.

Only the days with work run (``days``); statistics are refreshed after each
day the period job step runs, as ``schedule`` and ``billing`` do.

``at_anchor`` runs both job steps at :data:`~.anchor.SIM_NOW`: nothing is
left for them (B5). Payslips are issued by ``finalize`` in each tutor's
stored language and rendered only on download. Any unexpected answer fails
the run (one Unit of Work).
"""

import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime
from typing import Final

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core import clock
from app.core.audit import AuditEvent
from app.core.authz import Principal
from app.core.errors import AppError
from app.core.money import Money, Rate
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.payroll import service as payroll
from app.modules.payroll.models import (
    CompensationRate,
    PayItem,
    PayItemType,
    PayrollAdjustment,
    PayrollLine,
    PayrollPayment,
    PayrollPaymentReversal,
    PayrollPeriod,
    Payslip,
    SessionPayRule,
    TutorPayTerms,
)
from app.modules.payroll.schemas import (
    CompensationRateCreate,
    PayItemCreate,
    PayItemTypeCreate,
    PayrollAdjustmentCreate,
    PayrollPaymentCreate,
    PayrollPaymentReversalCreate,
    PayrollSettingsUpdate,
    PayTermsCreate,
    SessionPayRuleCreate,
)
from app.modules.payroll.settings import get_payroll_settings, update_payroll_settings
from app.workflows import (
    payroll_compensation,
    payroll_manual_adjustments,
    payroll_pay_items,
    payroll_payments,
    payroll_periods,
    payroll_workflow,
    session_pay_rules,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.demo_payroll import post_adjustments
from tests.factories.simulation.anchor import (
    SIM_NOW,
    SIM_SETUP,
    ZONE,
    history_days,
    local_instant,
    sim_clock,
)
from tests.factories.simulation.payroll_plan import (
    ADJUSTMENT_STEP_TIME,
    OPEN_STEP_TIME,
    PAYROLL_STARTS_ON,
    RULE_PERCENT,
    RULES_FROM,
    ItemPlan,
    ManualPlan,
    PeriodPlan,
    RatePlan,
    TermsPlan,
    money,
)
from tests.factories.simulation.people import principal, user_key
from tests.factories.simulation.schedule import SimulationStepError
from tests.factories.simulation.timeline import SimulationContext

PAYROLL_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    TutorPayTerms,
    CompensationRate,
    SessionPayRule,
    PayItemType,
    PayItem,
    PayrollPeriod,
    PayrollLine,
    PayrollAdjustment,
    PayrollPayment,
    PayrollPaymentReversal,
    Payslip,
    ApprovalRequest,
    Notification,
)
"""The payroll tables, and the period approval requests and the finalize
notifications this module's steps create (``number_series`` gains one row
for the payslips' year: not counted; the settings row is updated)."""

ANALYZED_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    *PAYROLL_TABLES,
    AuditEvent,
)
PAY_RULE_EVENTS: Final = ("late_cancellation", "no_show")

type Action = Callable[[AsyncSession, SimulationContext], Awaitable[None]]


def tutor_id(ctx: SimulationContext, key: str) -> uuid.UUID:
    return ctx.id(user_key(ctx.plan.tutor(key).account.username))


def local_day(at: datetime) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


async def finance(session: AsyncSession, ctx: SimulationContext) -> Principal:
    return await principal(session, ctx, ctx.plan.finance)


async def owner(session: AsyncSession, ctx: SimulationContext) -> Principal:
    return await principal(session, ctx, ctx.plan.owner)


async def period_id(
    session: AsyncSession, ctx: SimulationContext, tutor: str, starts_on: date
) -> uuid.UUID:
    found = await session.scalar(
        select(PayrollPeriod.id).where(
            PayrollPeriod.tutor_user_id == tutor_id(ctx, tutor),
            PayrollPeriod.starts_on == starts_on,
        )
    )
    if found is None:
        raise SimulationStepError(f"no payroll period {tutor} {starts_on}")
    return found


async def refresh_statistics(session: AsyncSession) -> None:
    """``ANALYZE`` the payroll tables (the run is one transaction, so
    autovacuum never sees its rows; as ``schedule`` and ``billing``)."""
    names = ", ".join(f'"{model.__tablename__}"' for model in ANALYZED_TABLES)
    await session.execute(text(f"ANALYZE {names}"))


async def open_periods(session: AsyncSession, today: date) -> int:
    """The ``payroll:open_periods`` run in the caller's Unit of Work: every
    tutor with terms, as the job reads them."""
    opened = 0
    for tutor in await payroll.tutors_with_terms(session):
        opened += await payroll_periods.open_tutor_periods(session, tutor, today=today)
    return opened


class PayrollModule:
    """HF-01e's ``payroll`` module (module docstring)."""

    name = "payroll"
    tables = PAYROLL_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (await session.scalar(select(PayItemType.id).limit(1))) is not None

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        plan = ctx.plan.payroll
        busy = {*plan.open_days, *plan.adjustment_days}
        return {d for d in history_days() if d in busy or _actions(ctx, d)}

    # -- setup --

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        plan = ctx.plan.payroll
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            current = await get_payroll_settings(session)
            await update_payroll_settings(
                session,
                PayrollSettingsUpdate(
                    version=current.version,
                    payroll_starts_on=PAYROLL_STARTS_ON,
                    biweekly_anchor_on=PAYROLL_STARTS_ON,
                    salary_proration_basis=current.salary_proration_basis,
                ),
            )
            overview = await session_pay_rules.get_overview(session)
            for event in PAY_RULE_EVENTS:
                versions = getattr(overview, event)
                latest = (versions.upcoming or [versions.current])[-1]
                await session_pay_rules.create_rule(
                    session,
                    SessionPayRuleCreate(
                        based_on_id=latest.id,
                        event=event,
                        effective_from=RULES_FROM,
                        mode="percent",
                        percent=Rate(str(RULE_PERCENT)),
                        amount=None,
                    ),
                )
            for kind, name in plan.item_types.items():
                created = await payroll.add_pay_item_type(
                    session,
                    PayItemTypeCreate(name=name, direction=kind),  # type: ignore[arg-type]
                )
                ctx.ids[f"pay_item_type:{kind}"] = created.id
        for terms in plan.terms:
            if terms.at == SIM_SETUP:
                await _add_terms(session, ctx, terms)
        for rate in plan.rates:
            if rate.at == SIM_SETUP:
                await _add_rate(session, ctx, rate)
        await refresh_statistics(session)

    # -- each day --

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        plan = ctx.plan.payroll
        if day in plan.open_days:
            with sim_clock(local_instant(day, OPEN_STEP_TIME)):
                await open_periods(session, day)
        for at, action in sorted(_actions(ctx, day), key=lambda item: item[0]):
            with sim_clock(at):
                try:
                    await action(session, ctx)
                except AppError as exc:
                    raise SimulationStepError(
                        f"payroll {at}: {exc.code} {exc.errors or exc.details}"
                    ) from exc
        if day in plan.adjustment_days:
            with sim_clock(local_instant(day, ADJUSTMENT_STEP_TIME)):
                await post_adjustments(session)
        await session.flush()
        session.expunge_all()
        if day in plan.open_days:
            await refresh_statistics(session)

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        # The jobs' steps at SIM_NOW: nothing is left for them (B5).
        await open_periods(session, local_day(SIM_NOW))
        await post_adjustments(session)


# --- The dated actions ----------------------------------------------------------------


def _actions(ctx: SimulationContext, day: date) -> list[tuple[datetime, Action]]:
    plan = ctx.plan.payroll
    found: list[tuple[datetime, Action]] = []

    def on(at: datetime | None) -> bool:
        return at is not None and at != SIM_SETUP and local_day(at) == day

    for terms in plan.terms:
        if on(terms.at):
            found.append((terms.at, _terms_action(terms)))
    for rate in plan.rates:
        if on(rate.at):
            found.append((rate.at, _rate_action(rate)))
    for item in plan.items:
        if on(item.at):
            found.append((item.at, _item_action(item)))
    for manual in plan.manuals:
        if on(manual.at):
            found.append((manual.at, _manual_action(manual)))
    if plan.carry is not None and on(plan.carry.at):
        found.append((plan.carry.at, _carry))
    for period in plan.ended:
        if on(period.prepare_at):
            found.append((period.prepare_at, _prepare_action(period)))  # type: ignore[arg-type]
        if on(period.finalize_at):
            found.append((period.finalize_at, _finalize_action(period)))  # type: ignore[arg-type]
        if on(period.pay_at):
            found.append((period.pay_at, _pay_action(period, "transfer", "")))  # type: ignore[arg-type]
    reversal = plan.reversal
    if on(reversal.reversed_at):
        found.append((reversal.reversed_at, _reverse))
    if on(reversal.repaid_at):
        period = next(
            p
            for p in plan.ended
            if p.tutor == reversal.tutor and p.starts_on == reversal.period_start
        )
        found.append((reversal.repaid_at, _pay_action(period, "cash", "-R")))
    return found


async def _add_terms(
    session: AsyncSession, ctx: SimulationContext, terms: TermsPlan
) -> None:
    me = await finance(session, ctx)
    with seed_actor(session, me.user_id):
        await payroll_compensation.add_pay_terms(
            session,
            me,
            tutor_id(ctx, terms.tutor),
            PayTermsCreate(
                effective_from=terms.effective_from,
                pay_frequency=terms.frequency,
                salary_amount=None
                if terms.salary is None
                else Money(money(terms.salary)),
            ),
        )


async def _add_rate(
    session: AsyncSession, ctx: SimulationContext, rate: RatePlan
) -> None:
    me = await finance(session, ctx)
    with seed_actor(session, me.user_id):
        await payroll_compensation.add_compensation_rate(
            session,
            me,
            tutor_id(ctx, rate.tutor),
            CompensationRateCreate.model_validate(
                {
                    "class_kind": rate.class_kind,
                    "effective_from": rate.effective_from,
                    "rate_model": rate.model,
                    "rate": money(rate.rate),
                }
            ),
        )


def _terms_action(terms: TermsPlan) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        await _add_terms(session, ctx, terms)

    return run


def _rate_action(rate: RatePlan) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        await _add_rate(session, ctx, rate)

    return run


def _item_action(item: ItemPlan) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            await payroll_pay_items.add_item(
                session,
                me,
                tutor_id(ctx, item.tutor),
                PayItemCreate(
                    pay_item_type_id=ctx.id(f"pay_item_type:{item.kind}"),
                    description=item.description,
                    amount=Money(money(item.amount)),
                    recurrence=item.recurrence,
                    effective_from=item.effective_from,
                ),
            )

    return run


def _manual_action(manual: ManualPlan) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            await payroll_manual_adjustments.post_manual(
                session,
                me,
                tutor_id(ctx, manual.tutor),
                PayrollAdjustmentCreate(
                    amount=Money(money(manual.amount)), reason=manual.reason
                ),
            )

    return run


async def _carry(session: AsyncSession, ctx: SimulationContext) -> None:
    carry = ctx.plan.payroll.carry
    assert carry is not None  # noqa: S101 - planned
    me = await finance(session, ctx)
    target = await period_id(session, ctx, carry.tutor, carry.period_start)
    with seed_actor(session, me.user_id):
        await payroll_manual_adjustments.carry_forward(session, me, target)


def _prepare_action(period: PeriodPlan) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await finance(session, ctx)
        target = await period_id(session, ctx, period.tutor, period.starts_on)
        with seed_actor(session, me.user_id):
            await payroll_workflow.prepare(session, me, target)

    return run


def _finalize_action(period: PeriodPlan) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        boss = await owner(session, ctx)
        target = await period_id(session, ctx, period.tutor, period.starts_on)
        prepared_at = await session.scalar(
            select(PayrollPeriod.prepared_at)
            .where(PayrollPeriod.id == target)
            .execution_options(populate_existing=True)
        )
        if prepared_at is None:
            raise SimulationStepError(f"{period.tutor} {period.starts_on} not prepared")
        with seed_actor(session, boss.user_id):
            await payroll_workflow.finalize(session, boss, target, prepared_at)

    return run


def _pay_action(period: PeriodPlan, method: str, suffix: str) -> Action:
    """Finance pays the period's whole total (read now: the amounts are the
    services'); a 0.00 period needs none."""

    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        target = await period_id(session, ctx, period.tutor, period.starts_on)
        total = await session.scalar(
            select(PayrollPeriod.total_amount)
            .where(PayrollPeriod.id == target)
            .execution_options(populate_existing=True)
        )
        if total is None:
            raise SimulationStepError(f"{period.tutor} {period.starts_on} not final")
        if not total.is_positive():
            return
        today = clock.now().astimezone(ZONE).date()
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            paid = await payroll_payments.record_payment(
                session,
                me,
                target,
                PayrollPaymentCreate(
                    paid_on=today,
                    amount=total,
                    method_code=method,
                    reference=(
                        f"NOM-{today.strftime('%y%m%d')}-{period.tutor.upper()}{suffix}"
                    ),
                ),
            )
        ctx.ids[f"payroll_payment:{period.tutor}:{period.starts_on}{suffix}"] = paid.id

    return run


async def _reverse(session: AsyncSession, ctx: SimulationContext) -> None:
    reversal = ctx.plan.payroll.reversal
    payment = ctx.ids.get(f"payroll_payment:{reversal.tutor}:{reversal.period_start}")
    if payment is None:
        raise SimulationStepError("the reversed payroll payment was not recorded")
    boss = await owner(session, ctx)
    with seed_actor(session, boss.user_id):
        await payroll_payments.reverse_payment(
            session,
            boss,
            payment,
            PayrollPaymentReversalCreate(reason=reversal.reason),
        )
