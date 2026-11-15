"""``finance`` (#553 HF-01e): the center's operating expenses, through the
finance services and the recurring-expense job's per-template step, after
``payroll``.

**Setup** (:data:`~.anchor.SIM_SETUP`, the Finance account): the teaching
materials category, the vendors and the five templates
(:mod:`.expenses_plan`), each ending on :data:`~.anchor.SIM_HORIZON` (E9).

**Each day with work**, under :func:`~.anchor.sim_clock` at each instant: 01:55 the
recurring-expense job step (``finance.due_template_ids`` +
``recurring_expenses.generate_for_template``,
:func:`~tests.factories.demo_finance.generate_recurring_expenses`), then the
dated actions: one-off expenses, payments, adjustments, the reversal (the
Owner), the cleaning template's pause and resume, and the vendor's
deactivation. ``at_anchor`` runs the job step at :data:`~.anchor.SIM_NOW`:
nothing is due (B5). The categories are found by name: in English HF-01
renamed the seeded ones, which clears their system key (#114).
"""

import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.authz import Principal
from app.core.errors import AppError
from app.core.money import Money
from app.modules.finance import service as expenses
from app.modules.finance.models import (
    Expense,
    ExpenseAdjustment,
    ExpenseCategory,
    ExpensePayment,
    ExpensePaymentReversal,
    ExpenseTemplate,
    Vendor,
)
from app.modules.finance.schemas import (
    ExpenseAdjustmentCreate,
    ExpenseCategoryCreate,
    ExpenseCreate,
    ExpensePaymentCreate,
    ExpensePaymentReversalCreate,
    ExpenseTemplateCreate,
    ExpenseTemplateUpdate,
    VendorCreate,
    VendorUpdate,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.demo_finance import generate_recurring_expenses
from tests.factories.simulation.anchor import (
    SIM_NOW,
    ZONE,
    history_days,
    local_instant,
    sim_clock,
)
from tests.factories.simulation.expenses_plan import (
    DEACTIVATED_AT,
    DEACTIVATED_VENDOR,
    JOB_TIME,
    PAUSE_AT,
    RESUME_AT,
    TEACHING_MATERIALS,
    Adjust,
    ExpensePlan,
    Pay,
    Reverse,
    money,
)
from tests.factories.simulation.people import principal
from tests.factories.simulation.schedule import SimulationStepError
from tests.factories.simulation.timeline import SimulationContext

FINANCE_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    ExpenseCategory,
    Vendor,
    ExpenseTemplate,
    Expense,
    ExpensePayment,
    ExpensePaymentReversal,
    ExpenseAdjustment,
)
"""The finance tables (the pause, the resume, the deactivation and the job's
``next_period_start`` are updates, not counted)."""

type Action = Callable[[AsyncSession, SimulationContext], Awaitable[None]]


def local_day(at: datetime) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


async def finance_user(session: AsyncSession, ctx: SimulationContext) -> Principal:
    return await principal(session, ctx, ctx.plan.finance)


async def owner_user(session: AsyncSession, ctx: SimulationContext) -> Principal:
    return await principal(session, ctx, ctx.plan.owner)


class FinanceModule:
    """HF-01e's ``finance`` module (module docstring)."""

    name = "finance"
    tables = FINANCE_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (await session.scalar(select(Vendor.id).limit(1))) is not None

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        """The days a template period starts (the job step generates it) or
        with a dated action."""
        plan = ctx.plan.expenses
        starts = {p for t in plan.templates for p in t.periods()}
        return {d for d in history_days() if d in starts or _actions(ctx, d)}

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        plan = ctx.plan.expenses
        me = await finance_user(session, ctx)
        names = ctx.texts.mapping("expense_categories")
        with seed_actor(session, me.user_id):
            await expenses.add_category(
                session, ExpenseCategoryCreate(name=plan.category_name)
            )
            for key, name in (*names.items(), (TEACHING_MATERIALS, plan.category_name)):
                found = await session.scalar(
                    select(ExpenseCategory.id).where(ExpenseCategory.name == name)
                )
                if found is None:
                    raise SimulationStepError(f"no expense category {name}")
                ctx.ids[f"category:{key}"] = found
            for vendor in plan.vendors:
                created = await expenses.add_vendor(
                    session, VendorCreate(name=vendor.name, contact=vendor.contact)
                )
                ctx.ids[f"vendor:{vendor.key}"] = created.id
            for template in plan.templates:
                item = await expenses.create_template(
                    session,
                    ExpenseTemplateCreate(
                        name=template.name,
                        category_id=ctx.id(f"category:{template.category}"),
                        vendor_id=ctx.id(f"vendor:{template.vendor}"),
                        amount=Money(money(template.amount)),
                        frequency=template.frequency,
                        starts_on=template.starts_on,
                        ends_on=template.ends_on,
                        due_offset_days=template.due_offset_days,
                    ),
                )
                ctx.ids[f"template:{template.key}"] = item.id

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        with sim_clock(local_instant(day, JOB_TIME)):
            await generate_recurring_expenses(session, day)
        for at, action in sorted(_actions(ctx, day), key=lambda item: item[0]):
            with sim_clock(at):
                try:
                    await action(session, ctx)
                except AppError as exc:
                    raise SimulationStepError(
                        f"finance {at}: {exc.code} {exc.errors or exc.details}"
                    ) from exc
        await session.flush()
        session.expunge_all()

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        # The job's step at SIM_NOW: nothing is due (B5).
        await generate_recurring_expenses(session, local_day(SIM_NOW))


# --- The dated actions ----------------------------------------------------------------


def _actions(ctx: SimulationContext, day: date) -> list[tuple[datetime, Action]]:
    plan = ctx.plan.expenses
    found: list[tuple[datetime, Action]] = []
    for expense in plan.one_offs:
        if expense.incurred_on == day:
            found.append((expense.recorded_at, _record(expense)))
    for expense in plan.expenses:
        for index, step in enumerate(expense.steps):
            if local_day(step.at) == day:
                found.append((step.at, _money_step(expense, index)))
    if local_day(PAUSE_AT) == day:
        found.append((PAUSE_AT, _pause(False)))
    if local_day(RESUME_AT) == day:
        found.append((RESUME_AT, _pause(True)))
    if local_day(DEACTIVATED_AT) == day:
        found.append((DEACTIVATED_AT, _deactivate))
    return found


async def expense_id(
    session: AsyncSession, ctx: SimulationContext, expense: ExpensePlan
) -> uuid.UUID:
    if expense.template is None:
        return ctx.id(f"expense:{expense.key}")
    found = await session.scalar(
        select(Expense.id).where(
            Expense.expense_template_id == ctx.id(f"template:{expense.template}"),
            Expense.period_start == expense.incurred_on,
        )
    )
    if found is None:
        raise SimulationStepError(f"{expense.key} was not generated")
    return found


def _record(expense: ExpensePlan) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await finance_user(session, ctx)
        with seed_actor(session, me.user_id):
            created = await expenses.create_expense(
                session,
                me.user_id,
                ExpenseCreate(
                    category_id=ctx.id(f"category:{expense.category}"),
                    vendor_id=None
                    if expense.vendor is None
                    else ctx.id(f"vendor:{expense.vendor}"),
                    description=expense.description,
                    amount=Money(money(expense.amount)),
                    incurred_on=expense.incurred_on,
                    due_on=expense.due_on,
                    receipt_reference=expense.receipt_reference,
                ),
            )
        ctx.ids[f"expense:{expense.key}"] = created.id

    return run


def _money_step(expense: ExpensePlan, index: int) -> Action:
    step = expense.steps[index]

    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        target = await expense_id(session, ctx, expense)
        if isinstance(step, Reverse):
            payment = ctx.id(f"expense_payment:{expense.key}:{step.index}")
            boss = await owner_user(session, ctx)
            with seed_actor(session, boss.user_id):
                await expenses.reverse_payment(
                    session,
                    boss.user_id,
                    payment,
                    ExpensePaymentReversalCreate(reason=step.reason),
                )
            return
        me = await finance_user(session, ctx)
        with seed_actor(session, me.user_id):
            if isinstance(step, Adjust):
                await expenses.post_adjustment(
                    session,
                    me.user_id,
                    target,
                    ExpenseAdjustmentCreate(
                        amount=Money(money(step.amount)), reason=step.reason
                    ),
                )
                return
            assert isinstance(step, Pay)  # noqa: S101 - the third kind
            paid = await expenses.record_payment(
                session,
                me.user_id,
                target,
                ExpensePaymentCreate(
                    paid_on=local_day(step.at),
                    amount=Money(money(step.amount)),
                    method_code=step.method,
                    reference=step.reference,
                ),
            )
        number = sum(1 for s in expense.steps[:index] if isinstance(s, Pay))
        ctx.ids[f"expense_payment:{expense.key}:{number}"] = paid.id

    return run


def _pause(active: bool) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        template = next(t for t in ctx.plan.expenses.templates if t.paused)
        template_id = ctx.id(f"template:{template.key}")
        me = await finance_user(session, ctx)
        with seed_actor(session, me.user_id):
            current = await expenses.get_template(session, template_id)
            await expenses.change_template(
                session,
                template_id,
                ExpenseTemplateUpdate(version=current.version, is_active=active),
            )

    return run


async def _deactivate(session: AsyncSession, ctx: SimulationContext) -> None:
    vendor_id = ctx.id(f"vendor:{DEACTIVATED_VENDOR}")
    version = await session.scalar(
        select(Vendor.version)
        .where(Vendor.id == vendor_id)
        .execution_options(populate_existing=True)
    )
    assert version is not None  # noqa: S101 - created at setup
    me = await finance_user(session, ctx)
    with seed_actor(session, me.user_id):
        await expenses.change_vendor(
            session, vendor_id, VendorUpdate(version=version, is_active=False)
        )
