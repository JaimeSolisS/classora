"""``billing`` (#552 HF-01d): the term's billing through the billing
workflows and the billing jobs' per-item steps, after ``academics`` (the
last module, so each day's classes are held, cancelled and approved
first).

**Setup.** At :data:`~.anchor.SIM_SETUP` the Finance account turns billing
on from 1 Sep (so every HF-01b day hook, from the first class on, sees
billing on: package consumption at closeout approval, the fees of later
enrollments), creates the catalog prices (with the 1 Oct price change),
the discounts, the package products and the custom prices. On 31 Aug at
18:00 it creates the tuition plans and sells the first packages (each paid
at once); at 23:30 the job steps post every charge the jobs would post
(the enrollment fees, every confirmed individual class through the
horizon: ADR-0006 E1 charges a confirmed class).

**Each day** (the plan, :mod:`.billing_plan`), under
:func:`~.anchor.sim_clock` at each instant:

- 07:30 the tuition step on the 1st (``tuition_charges.post_plan`` per
  plan); 07:40 the package expiry step;
- the dated actions: payments (every open charge due up to the plan's
  cutoff, account credit first; receipts are issued by the payment
  service), manual charges, the adjustment, due-date changes, the bounced
  transfer (reversed by the Owner), the package sales, the package
  withdrawal, the package cancellation and its refund (the Owner), the
  pause and the plan change;
- 15:00 the reminder step;
- 23:15 package renewals (a package used up that day is renewed and paid);
- 23:30 the enrollment-charge, session-charge and reconciliation steps
  (cancellation decisions, repricings, closure credits as the services
  decide).

The job steps run in-process with no actor (E12), so at
:data:`~.anchor.SIM_NOW` (:meth:`BillingModule.at_anchor` runs them once
more) the jobs have nothing to post. No payment, receipt, refund or
reversal is dated on or after the anchor. Any unexpected answer fails the
run (one Unit of Work).
"""

import uuid
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Final, cast

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.audit import AuditEvent
from app.core.authz import Principal
from app.core.errors import AppError
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.administration.models import Notification
from app.modules.billing import service as billing
from app.modules.billing.discounts import DiscountEligibility
from app.modules.billing.models import (
    AllocationRelease,
    BillingAccount,
    Charge,
    ChargeAdjustment,
    ChargeDiscount,
    ChargeDueDateChange,
    ChargeDurationRepricing,
    ChargeReminder,
    CustomPrice,
    Discount,
    DiscountAssignment,
    Package,
    PackageCreditTransaction,
    PackageProduct,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    PriceVersion,
    Receipt,
    Refund,
    ServicePrice,
    SessionCancellationDecision,
    TuitionPlan,
    TuitionPlanPause,
    Withdrawal,
)
from app.modules.billing.schemas import (
    AllocationLineBody,
    BillingSettingsUpdate,
    CreditAllocationBody,
    CreditAllocationCreate,
    PaymentAllocationBody,
    PaymentCreate,
    PriceVersionCreate,
    ServicePriceCreate,
)
from app.modules.billing.settings import get_billing_settings, update_billing_settings
from app.modules.scheduling.models import GroupEnrollment
from app.workflows import (
    billing_accounts,
    billing_corrections,
    billing_discounts,
    billing_payments,
    custom_prices,
    packages,
    price_catalog,
    session_charges,
    tuition_charges,
    tuition_plans,
    withdrawals,
)
from app.workflows.package_schemas import PackageProductCreate, PackagePurchase
from app.workflows.tuition_plan_schemas import TuitionPauseCreate, TuitionPlanCreate
from app.workflows.withdrawal_schemas import (
    WithdrawalCreditConfirm,
    WithdrawalSubjectBody,
)
from tests.factories.demo_billing import (
    expire_due_packages,
    post_enrollment_charges,
)
from tests.factories.demo_billing_money import (
    reconcile_session_charges,
    send_charge_reminders,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import (
    SIM_NOW,
    history_days,
    local_instant,
    sim_clock,
)
from tests.factories.simulation.billing_plan import (
    BILLING_STARTS_ON,
    CANCEL_AT,
    EXTRA_AT,
    INTENSIVE_AT,
    OPENING_AT,
    OPENING_DAY,
    PAUSE_AT,
    SETUP_DAY,
    TUITION_DUE_OFFSET,
    WITHDRAWN_AT,
    Billing,
    ManualChargePlan,
    PaymentPlan,
)
from tests.factories.simulation.people import principal
from tests.factories.simulation.schedule import SimulationStepError, student_id
from tests.factories.simulation.timeline import SimulationContext

BILLING_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    BillingAccount,
    ServicePrice,
    PriceVersion,
    CustomPrice,
    Discount,
    DiscountAssignment,
    PackageProduct,
    Package,
    PackageCreditTransaction,
    TuitionPlan,
    TuitionPlanPause,
    Charge,
    ChargeDiscount,
    ChargeAdjustment,
    ChargeDurationRepricing,
    SessionCancellationDecision,
    ChargeDueDateChange,
    ChargeReminder,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    AllocationRelease,
    Refund,
    Receipt,
    Withdrawal,
    Notification,
)
"""The billing tables, and the reminder notifications this module's steps
create (``number_series`` gains one row per receipt year: not counted)."""

ANALYZED_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    *BILLING_TABLES,
    AuditEvent,
)
ANALYZE_EVERY_DAYS: Final = 7

MAIN_PRODUCTS: Final = ("pack8", "pack12")


def money(amount: int) -> Money:
    return Money(f"{amount}.00")


def family_id(ctx: SimulationContext, family: str) -> uuid.UUID:
    return ctx.id(f"family:{family}")


async def finance(session: AsyncSession, ctx: SimulationContext) -> Principal:
    return await principal(session, ctx, ctx.plan.finance)


async def owner(session: AsyncSession, ctx: SimulationContext) -> Principal:
    return await principal(session, ctx, ctx.plan.owner)


async def account_id(
    session: AsyncSession, ctx: SimulationContext, family: str
) -> uuid.UUID:
    found = await billing.account_of_family(session, family_id(ctx, family))
    if found is None:
        raise SimulationStepError(f"{family} has no billing account")
    return found.id


def local_today(at: datetime) -> date:
    from tests.factories.simulation.anchor import ZONE

    result: date = at.astimezone(ZONE).date()
    return result


async def refresh_statistics(session: AsyncSession) -> None:
    """``ANALYZE`` the billing tables (as ``schedule``: the run is one
    transaction, so autovacuum never sees its rows)."""
    names = ", ".join(f'"{model.__tablename__}"' for model in ANALYZED_TABLES)
    await session.execute(text(f"ANALYZE {names}"))


# --- The session-charge step ----------------------------------------------------------

STABLE: Final = ("enrollment_priced", "tuition_priced")
_settled: dict[tuple[uuid.UUID, uuid.UUID], str] = {}
"""Keys the session step left uncharged in an earlier run (D-perf): a
fee- or tuition-priced group key stays so, and a package-covered future
key stays so while its package lasts (renewed the evening it runs out)."""


def forget_package_coverage() -> None:
    """A package was cancelled, withdrawn or expired: its covered keys are
    read again at the next evening."""
    for key in [k for k, v in _settled.items() if v == "package_covered"]:
        del _settled[key]


async def post_new_session_charges(session: AsyncSession, day: date) -> None:
    """The ``billing:post_session_charges`` loop (``session_charges.
    candidate_page`` + ``post_one``, as :func:`~tests.factories.demo_billing.
    post_session_charges`), skipping the keys an earlier evening left
    uncharged for a reason that can't have changed (:data:`_settled`): the
    job re-reads every one each run, which would cost minutes here. At the
    anchor it runs once more (:meth:`BillingModule.at_anchor`) and posts
    nothing; the B5 test runs the job's own loop there."""
    floor = await billing.billing_starts_on(session)
    if floor is None:
        return
    zone = await administration.get_center_time_zone(session)
    after: session_charges.Position | None = None
    while True:
        page, after = await session_charges.candidate_page(
            session, floor=floor, zone=zone, after=after
        )
        for candidate in page:
            known = _settled.get(candidate.key)
            if known in STABLE or (
                known == "package_covered" and candidate.service_date > day
            ):
                continue
            outcome = await session_charges.post_one(
                session, candidate.class_session_id, candidate.student_id, zone=zone
            )
            if outcome in (*STABLE, "package_covered"):
                _settled[candidate.key] = outcome
        if after is None:
            return


# --- Money in -------------------------------------------------------------------------


async def pay(
    session: AsyncSession, ctx: SimulationContext, payment: PaymentPlan
) -> uuid.UUID | None:
    """Every open charge due up to the cutoff, account credit first; a
    pay-ahead family rounds up and adds about a month. Nothing open: no
    payment. Returns the payment id."""
    today = local_today(payment.at)
    family = family_id(ctx, payment.family)
    account = await billing.account_of_family(session, family)
    if account is None:
        return None
    balances = await billing.charge_balances(session, account.id, today=today)
    due = Money.total(
        b.open
        for b in balances
        if b.open.is_positive() and b.effective_due_date <= payment.cutoff
    )
    if not due.is_positive():
        return None
    credit = await billing.account_credit(session, account.id)
    if credit.is_positive():
        used = min(credit, due)
        await billing_payments.allocate_credit(
            session,
            family,
            CreditAllocationCreate(
                allocation=CreditAllocationBody(mode="oldest_first"), amount=used
            ),
        )
        due = due - used
        if not due.is_positive():
            return None
    amount = due
    if payment.ahead:
        whole = int(due.amount) // 500 + 2
        amount = money(whole * 500)
    recorded = await billing_payments.record_payment(
        session,
        family,
        PaymentCreate(
            amount=amount,
            received_on=today,
            payment_method_code=payment.method,
            reference=_reference(payment),
            allocation=PaymentAllocationBody(mode="oldest_first"),
        ),
    )
    return recorded.payment.id


def _reference(payment: PaymentPlan) -> str | None:
    if payment.method != "transfer":
        return None
    return f"TRF-{local_today(payment.at).strftime('%y%m%d')}-{payment.family.upper()}"


async def sell(
    session: AsyncSession,
    ctx: SimulationContext,
    student: str,
    product: str,
    at: datetime,
) -> uuid.UUID:
    """Sell ``product`` to ``student`` and record its payment at once."""
    with sim_clock(at):
        detail = await packages.purchase_package(
            session,
            PackagePurchase(
                student_id=student_id(ctx, student),
                package_product_id=ctx.id(f"product:{product}"),
            ),
        )
    charge_id = detail.purchase_charge_id
    assert charge_id is not None  # noqa: S101 - posted with the package
    family = detail.family.id
    account = await billing.account_of_family(session, family)
    assert account is not None  # noqa: S101 - the sale created it
    [balance] = await billing.charge_balances(
        session, account.id, charge_id=charge_id, today=local_today(at)
    )
    with sim_clock(at + timedelta(minutes=5)):
        if balance.open.is_positive():
            await billing_payments.record_payment(
                session,
                family,
                PaymentCreate(
                    amount=balance.open,
                    received_on=local_today(at),
                    payment_method_code="cash",
                    allocation=PaymentAllocationBody(
                        mode="explicit",
                        lines=[
                            AllocationLineBody(charge_id=charge_id, amount=balance.open)
                        ],
                    ),
                ),
            )
    ctx.ids[f"package:{student}:{product}:{local_today(at).isoformat()}"] = detail.id
    return detail.id


async def remaining_main(
    session: AsyncSession, ctx: SimulationContext, student: str, day: date
) -> int:
    """The units left on ``student``'s 8- or 12-class packages that are
    still valid after ``day`` (no ``expire`` row)."""
    products = [ctx.id(f"product:{p}") for p in MAIN_PRODUCTS]
    units = (
        select(func.coalesce(func.sum(PackageCreditTransaction.units), 0))
        .where(PackageCreditTransaction.package_id == Package.id)
        .scalar_subquery()
    )
    expired = (
        select(PackageCreditTransaction.id)
        .where(
            PackageCreditTransaction.package_id == Package.id,
            PackageCreditTransaction.kind == "expire",
        )
        .exists()
    )
    found = await session.scalar(
        select(func.coalesce(func.sum(units), 0)).where(
            Package.student_id == student_id(ctx, student),
            Package.package_product_id.in_(products),
            Package.expires_on > day,
            ~expired,
        )
    )
    return int(found or 0)


async def main_package(
    session: AsyncSession, ctx: SimulationContext, student: str
) -> uuid.UUID:
    """The student's latest 8- or 12-class package."""
    products = [ctx.id(f"product:{p}") for p in MAIN_PRODUCTS]
    found = await session.scalar(
        select(Package.id)
        .where(
            Package.student_id == student_id(ctx, student),
            Package.package_product_id.in_(products),
        )
        .order_by(Package.purchased_on.desc(), Package.created_at.desc())
        .limit(1)
    )
    if found is None:
        raise SimulationStepError(f"{student} has no package")
    return found


# --- The module -----------------------------------------------------------------------


class BillingModule:
    """HF-01d's ``billing`` module (module docstring)."""

    name = "billing"
    tables = BILLING_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (await session.scalar(select(ServicePrice.id).limit(1))) is not None

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return history_days()

    # -- setup --

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        _settled.clear()
        plan = ctx.plan.billing
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            current = await get_billing_settings(session)
            await update_billing_settings(
                session,
                BillingSettingsUpdate(
                    version=current.version, billing_starts_on=BILLING_STARTS_ON
                ),
            )
            for item in plan.items:
                created = await price_catalog.create_service_price(
                    session,
                    ServicePriceCreate(
                        class_type=item.class_type,
                        pricing_model=item.pricing_model,
                        subject_id=ctx.id(f"subject:{item.subject}"),
                        group_id=None
                        if item.group is None
                        else ctx.id(f"group:{item.group}"),
                        duration_minutes=item.minutes,
                        period_frequency=item.frequency,
                        effective_from=SETUP_DAY,
                        amount=money(item.amount),
                    ),
                )
                ctx.ids[f"price:{item.key}"] = created.id
            for version in plan.versions:
                await price_catalog.add_price_version(
                    session,
                    ctx.id(f"price:{version.item}"),
                    PriceVersionCreate(
                        effective_from=version.effective_from,
                        amount=money(version.amount),
                    ),
                )
            for discount in plan.discounts:
                created_discount = await billing_discounts.create_discount(
                    session,
                    billing.DiscountCreate(
                        kind=discount.kind,
                        audience=discount.audience,  # type: ignore[arg-type]
                        name=discount.name,
                        percent=discount.percent,
                        starts_on=discount.starts_on,
                        expires_on=discount.expires_on,
                        eligibility=DiscountEligibility(
                            charge_kinds=list(discount.charge_kinds),  # type: ignore[arg-type]
                            min_family_students=2
                            if discount.kind == "sibling"
                            else None,
                        ),
                    ),
                )
                for student in discount.students:
                    await billing_discounts.add_assignment(
                        session,
                        created_discount.id,
                        billing.DiscountAssignmentCreate(
                            student_id=student_id(ctx, student)
                        ),
                    )
            for product in plan.products:
                created_product = await packages.create_package_product(
                    session,
                    PackageProductCreate(
                        name=product.name,
                        unit="sessions",
                        quantity=product.quantity,
                        validity_days=product.validity_days,
                        subject_id=None
                        if product.subject is None
                        else ctx.id(f"subject:{product.subject}"),
                        amount=money(product.amount),
                        effective_from=SETUP_DAY,
                    ),
                )
                ctx.ids[f"product:{product.key}"] = created_product.id
        await self._custom_prices(session, ctx, SETUP_DAY)
        # 31 Aug: the plans and the first packages, then the job steps.
        with sim_clock(OPENING_AT), seed_actor(session, me.user_id):
            for tuition in plan.tuition_plans:
                if tuition.at == OPENING_AT:
                    await self._create_plan(session, ctx, tuition.key)
        with seed_actor(session, me.user_id):
            for package in plan.packages:
                await sell(session, ctx, package.student, package.product, OPENING_AT)
        await self._evening(session, ctx, OPENING_DAY)
        await refresh_statistics(session)

    async def _custom_prices(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        me = await finance(session, ctx)
        for custom in ctx.plan.billing.custom_prices:
            if local_today(custom.at) != day:
                continue
            with sim_clock(custom.at), seed_actor(session, me.user_id):
                await custom_prices.create_custom_price(
                    session,
                    billing.CustomPriceCreate(
                        service_price_id=ctx.id(f"price:{custom.item}"),
                        student_id=student_id(ctx, custom.student),
                        effective_from=custom.effective_from,
                        amount=money(custom.amount),
                    ),
                )

    async def _create_plan(
        self, session: AsyncSession, ctx: SimulationContext, key: str
    ) -> None:
        tuition = next(t for t in ctx.plan.billing.tuition_plans if t.key == key)
        enrollment = None
        if tuition.group is not None:
            enrollment = await session.scalar(
                select(GroupEnrollment.id).where(
                    GroupEnrollment.group_id == ctx.id(f"group:{tuition.group}"),
                    GroupEnrollment.student_id == student_id(ctx, tuition.student),
                    GroupEnrollment.withdrawn_at.is_(None),
                )
            )
            if enrollment is None:
                raise SimulationStepError(f"{key}: no group enrollment")
        created, _ = await tuition_plans.create_tuition_plan(
            session,
            TuitionPlanCreate(
                student_id=student_id(ctx, tuition.student),
                service_price_id=ctx.id(f"price:{tuition.item}"),
                group_enrollment_id=enrollment,
                starts_on=tuition.starts_on,
                due_offset_days=TUITION_DUE_OFFSET,
            ),
        )
        ctx.ids[f"tuition:{key}"] = created.id

    # -- each day --

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        if day.day == 1:
            with sim_clock(local_instant(day, "07:30")):
                await self._tuition(session, ctx, day)
        with sim_clock(local_instant(day, "07:40")):
            if (await expire_due_packages(session)).expired:
                forget_package_coverage()
        for at, action in sorted(self._actions(ctx, day), key=lambda a: a[0]):
            with sim_clock(at):
                try:
                    await action(session, ctx)
                except AppError as exc:
                    raise SimulationStepError(
                        f"billing {at}: {exc.code} {exc.errors or exc.details}"
                    ) from exc
        with sim_clock(local_instant(day, "15:00")):
            await send_charge_reminders(session)
        await self._renew(session, ctx, day)
        await self._evening(session, ctx, day)
        await session.flush()
        session.expunge_all()
        if (day - BILLING_STARTS_ON).days % ANALYZE_EVERY_DAYS == 0:
            await refresh_statistics(session)

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        # The jobs' steps at SIM_NOW: nothing is left for them (B5).
        await self._tuition(session, ctx, local_today(SIM_NOW))
        await expire_due_packages(session)
        await send_charge_reminders(session)
        await post_enrollment_charges(session)
        await post_new_session_charges(session, local_today(SIM_NOW))
        await reconcile_session_charges(session)

    async def _tuition(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        zone = await administration.get_center_time_zone(session)
        for tuition in ctx.plan.billing.tuition_plans:
            plan_id = ctx.ids.get(f"tuition:{tuition.key}")
            if plan_id is None:
                continue
            await tuition_charges.post_plan(
                session, plan_id, zone=zone, today=day, first=BILLING_STARTS_ON
            )

    async def _evening(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        with sim_clock(local_instant(day, "23:30")):
            await post_enrollment_charges(session)
            await post_new_session_charges(session, day)
            await reconcile_session_charges(session)

    async def _renew(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        plan = ctx.plan.billing
        me = await finance(session, ctx)
        tomorrow = day + timedelta(days=1)
        for package in plan.packages:
            if plan.mode_on(package.student, tomorrow) != "package":
                continue
            if await remaining_main(session, ctx, package.student, day) > 0:
                continue
            with seed_actor(session, me.user_id):
                await sell(
                    session,
                    ctx,
                    package.student,
                    package.product,
                    local_instant(day, "23:15"),
                )

    def _actions(self, ctx: SimulationContext, day: date) -> list[tuple[datetime, Any]]:
        plan = ctx.plan.billing
        found: list[tuple[datetime, Any]] = []

        def on(at: datetime) -> bool:
            return local_today(at) == day

        for payment in plan.payments:
            if on(payment.at):
                found.append((payment.at, _pay_action(payment)))
        for manual in plan.manual_charges:
            if on(manual.at):
                found.append((manual.at, _manual_action(manual)))
        for adjustment in plan.adjustments:
            if on(adjustment.at):
                found.append((adjustment.at, _adjust_action(adjustment.charge, plan)))
        for change in plan.due_date_changes:
            if on(change.at):
                found.append(
                    (change.at, _due_action(plan.due_date_changes.index(change)))
                )
        if on(plan.reversal.at):
            found.append((plan.reversal.at, _reverse))
        if on(PAUSE_AT):
            found.append((PAUSE_AT, _pause))
        if on(INTENSIVE_AT):
            found.append((INTENSIVE_AT, _intensive))
        if on(EXTRA_AT):
            found.append((EXTRA_AT, _extra))
        if on(WITHDRAWN_AT):
            found.append((WITHDRAWN_AT, _withdraw))
        if on(CANCEL_AT):
            found.append((CANCEL_AT, _cancel))
        changes = [t for t in plan.tuition_plans if t.at != OPENING_AT and on(t.at)]
        if changes:
            found.append((changes[0].at, _plan_change(tuple(t.key for t in changes))))
        return found


# --- The dated actions ----------------------------------------------------------------


def _pay_action(payment: PaymentPlan) -> Any:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            paid = await pay(session, ctx, payment)
        if paid is not None:
            ctx.ids[f"payment:{payment.family}:{payment.at.isoformat()}"] = paid

    return run


def _manual_action(manual: ManualChargePlan) -> Any:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            posted = await billing_corrections.post_manual_charge(
                session,
                family_id(ctx, manual.family),
                billing.ManualChargeCreate(
                    student_id=student_id(ctx, manual.student),
                    description=manual.description,
                    amount=money(manual.amount),
                    service_date=manual.service_date,
                    due_date=manual.due_date,
                ),
            )
        ctx.ids[f"manual:{manual.key}"] = posted.charge.id

    return run


def _adjust_action(charge: str, plan: Billing) -> Any:
    adjustment = next(a for a in plan.adjustments if a.charge == charge)
    manual = next(m for m in plan.manual_charges if m.key == charge)

    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            await billing_corrections.post_manual_adjustment(
                session,
                family_id(ctx, manual.family),
                ctx.id(f"manual:{charge}"),
                billing.ManualAdjustmentCreate(
                    amount=Money(f"{adjustment.amount}.00"), reason=adjustment.reason
                ),
            )

    return run


def _due_action(index: int) -> Any:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        change = ctx.plan.billing.due_date_changes[index]
        charge = await session.scalar(
            select(Charge.id).where(
                Charge.tuition_plan_id == ctx.id(f"tuition:{change.plan}"),
                Charge.service_date == change.period_start,
            )
        )
        if charge is None:
            raise SimulationStepError(f"no tuition charge for {change.plan}")
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            await billing_accounts.record_due_date_change(
                session,
                family_id(ctx, change.family),
                charge,
                billing.DueDateChangeCreate(
                    due_date=change.due_date, reason=change.reason
                ),
            )

    return run


async def _reverse(session: AsyncSession, ctx: SimulationContext) -> None:
    reversal = ctx.plan.billing.reversal
    payment = ctx.ids.get(
        f"payment:{reversal.family}:{reversal.payment_at.isoformat()}"
    )
    if payment is None:
        raise SimulationStepError("the bounced transfer was not recorded")
    me = await owner(session, ctx)
    with seed_actor(session, me.user_id):
        await billing_corrections.reverse_payment(
            session,
            family_id(ctx, reversal.family),
            payment,
            billing.PaymentReversalCreate(reason=reversal.reason),
        )


async def _pause(session: AsyncSession, ctx: SimulationContext) -> None:
    """Every plan of the paused student (HF-01g: a plan per subject)."""
    me = await finance(session, ctx)
    for tuition in ctx.plan.billing.tuition_plans:
        if tuition.pause is None:
            continue
        plan_id = ctx.id(f"tuition:{tuition.key}")
        version = await session.scalar(
            select(TuitionPlan.version).where(TuitionPlan.id == plan_id)
        )
        assert version is not None  # noqa: S101 - created at setup
        with seed_actor(session, me.user_id):
            await tuition_plans.add_tuition_pause(
                session,
                plan_id,
                TuitionPauseCreate(
                    version=version,
                    starts_on=tuition.pause[0],
                    ends_on=tuition.pause[1],
                ),
            )


async def _intensive(session: AsyncSession, ctx: SimulationContext) -> None:
    package = next(p for p in ctx.plan.billing.packages if p.intensive)
    me = await finance(session, ctx)
    with seed_actor(session, me.user_id):
        await sell(session, ctx, package.student, "intensive", INTENSIVE_AT)


async def _extra(session: AsyncSession, ctx: SimulationContext) -> None:
    package = next(p for p in ctx.plan.billing.packages if p.extra is not None)
    assert package.extra is not None  # noqa: S101 - selected above
    me = await finance(session, ctx)
    with seed_actor(session, me.user_id):
        await sell(session, ctx, package.student, package.extra, EXTRA_AT)


async def _withdraw(session: AsyncSession, ctx: SimulationContext) -> None:
    """The package bought for a subject never started is withdrawn as
    account credit (#101; the package's ``refund`` units row)."""
    package = next(p for p in ctx.plan.billing.packages if p.extra is not None)
    key = (
        f"package:{package.student}:{package.extra}:{local_today(EXTRA_AT).isoformat()}"
    )
    subject_id = ctx.id(key)
    me = await finance(session, ctx)
    preview = await withdrawals.preview_withdrawal(
        session, "package", subject_id, now=WITHDRAWN_AT
    )
    with seed_actor(session, me.user_id):
        await withdrawals.confirm_withdrawal(
            cast(UnitOfWork, SimpleNamespace(session=session)),
            me,
            WithdrawalCreditConfirm(
                subject=WithdrawalSubjectBody(package_id=subject_id),
                reason=ctx.plan.billing.reasons["withdrawal"],
                fingerprint=preview.fingerprint,
            ),
            outcome="account_credit",
        )
    forget_package_coverage()


async def _cancel(session: AsyncSession, ctx: SimulationContext) -> None:
    """The family cancels the package (the Finance account) and the Owner
    refunds the freed cash."""
    plan = ctx.plan.billing
    package = next(p for p in plan.packages if p.cancelled)
    package_id = await main_package(session, ctx, package.student)
    me = await finance(session, ctx)
    with seed_actor(session, me.user_id):
        cancelled = await packages.cancel_package(
            session,
            package_id,
            billing.PackageCancellationCreate(reason=plan.reasons["cancellation"]),
        )
    forget_package_coverage()
    left = cancelled.credit
    family = next(
        f.key for f in ctx.plan.families for s in f.students if s.key == package.student
    )
    account = await account_id(session, ctx, family)
    boss = await owner(session, ctx)
    today = local_today(CANCEL_AT)
    with seed_actor(session, boss.user_id):
        for payment, remainder in await billing.payment_remainders(session, account):
            if not left.is_positive():
                break
            amount = min(remainder, left)
            left = left - amount
            await billing_corrections.refund_payment(
                session,
                family_id(ctx, family),
                payment,
                billing.RefundCreate(
                    amount=amount,
                    refunded_on=today,
                    payment_method_code="cash",
                    reason=plan.reasons["refund"],
                ),
            )


def _plan_change(keys: tuple[str, ...]) -> Any:
    """The package student's plans (one per subject) and their custom
    prices."""

    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        plans = [t for t in ctx.plan.billing.tuition_plans if t.key in keys]
        me = await finance(session, ctx)
        with seed_actor(session, me.user_id):
            for tuition in plans:
                await BillingModule()._create_plan(session, ctx, tuition.key)
        await BillingModule()._custom_prices(session, ctx, local_today(plans[0].at))

    return run
