"""Financial reports: queries and assembly (#117; spec §8; ADR-0006 E7,
ADR-0017 E12, E18; ADR-0002 D3).

Read-only: no lock, no write, no audit event, nothing logged. Every derived
value comes from its owner's public seam (``billing.service.charge_figures``,
``unreversed_payment``, ``receivable_totals``; ``payroll.service.paid_sum``,
``unreversed_payment``; ``finance.service.expense_figures``, ``unreversed``;
``scheduling.service.owner_schedules``); this module only joins other
modules' **models** (ADR-0002 D3) and dates the figures.

**Accrual** (live from current nets, #117 E3): each charge's net by the
rules of :mod:`~app.modules.reporting.recognition`; finalized payroll lines
(session-sourced by ``service_date``/``class_kind``, salary, pay items and
adjustments on the period's ``ends_on``, E8); expenses by ``incurred_on`` at
their current net. **Cash**: unreversed payments by ``received_on`` minus
refunds by ``refunded_on`` (E6), unreversed payroll payments and expense
payments by ``paid_on``. **Balances** (E9): as of the center's today.

Each report runs a fixed number of statements whatever the row counts (F1):
the zone, the charges, the package movements, the cohort schedules (at most
three), payroll, expenses, and the cash or balance sums.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Date,
    Numeric,
    case,
    cast,
    func,
    literal,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.money import Money, MoneyType, money_sum
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.billing.models import (
    Package,
    PackageCreditTransaction,
    Payment,
    Refund,
    ServicePrice,
    TuitionPlan,
)
from app.modules.courses.models import CohortEnrollment
from app.modules.finance import service as finance
from app.modules.finance.models import (
    Expense,
    ExpenseCategory,
    ExpensePayment,
    Vendor,
)
from app.modules.finance.schemas import ExpenseCategoryRef, VendorRef
from app.modules.payroll import service as payroll
from app.modules.payroll.models import (
    PayrollAdjustment,
    PayrollLine,
    PayrollPayment,
    PayrollPeriod,
)
from app.modules.reporting.recognition import (
    CLASS_ROWS,
    SESSION_LINE_KINDS,
    ClassType,
    Consumption,
    DeferredBucket,
    Occurrence,
    Share,
    cost_class,
    course_shares,
    dated_shares,
    deferred_bucket,
    package_shares,
    revenue_class,
)
from app.modules.reporting.schemas import (
    Balances,
    CategoryAmount,
    ClassRow,
    DeferredRevenue,
    OperatingExpenses,
    Payables,
    Profitability,
    Receivables,
    ReportBasis,
    VendorAmount,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import ClassSession

FINALIZED: Final = "finalized"
VOID: Final = "void"
OVERDUE: Final = "overdue"


# --- Pure assembly ---------------------------------------------------------------


@dataclass(frozen=True)
class RevenueEntry:
    """A recognized (or deferred, ``on is None`` or after today) part of a
    charge's net."""

    on: date | None
    class_type: ClassType
    bucket: DeferredBucket
    amount: Money
    cohort_id: uuid.UUID | None = None
    """The cohort the part is attributed to (#126 K2): a ``cohort_enrollment``
    charge's enrollment cohort, else the charge's class session's cohort."""


@dataclass(frozen=True)
class CostEntry:
    """A finalized payroll line, dated and classed (ADR-0017 E18, #117 E8)."""

    on: date
    class_type: ClassType
    amount: Money
    cohort_id: uuid.UUID | None = None
    """The cohort the line is attributed to (#126 K2): its session's cohort,
    else its adjustment's session's, else its origin line's session's."""


@dataclass(frozen=True)
class ExpenseGroup:
    """The sum of a range's expenses for one (category, vendor) pair."""

    category: ExpenseCategoryRef
    vendor: VendorRef | None
    amount: Money
    month: date | None = None
    """The first of the month the sum belongs to (#126 K1), when grouped by
    month."""


def revenue_entries(
    class_type: ClassType,
    bucket: DeferredBucket,
    shares: Iterable[Share],
    cohort_id: uuid.UUID | None = None,
) -> list[RevenueEntry]:
    return [RevenueEntry(s.on, class_type, bucket, s.amount, cohort_id) for s in shares]


def _within(on: date | None, start: date, end: date) -> bool:
    return on is not None and start <= on <= end


def class_rows(
    revenue: Iterable[RevenueEntry],
    costs: Iterable[CostEntry],
    start: date,
    end: date,
) -> list[ClassRow]:
    """The four accrual rows over ``[start, end]``, in fixed order, zeros
    included."""
    income: defaultdict[str, list[Money]] = defaultdict(list)
    spent: defaultdict[str, list[Money]] = defaultdict(list)
    for item in revenue:
        if _within(item.on, start, end):
            income[item.class_type].append(item.amount)
    for cost in costs:
        if _within(cost.on, start, end):
            spent[cost.class_type].append(cost.amount)
    rows = []
    for class_type in CLASS_ROWS:
        earned = Money.total(income[class_type])
        paid = Money.total(spent[class_type])
        rows.append(
            ClassRow(
                class_type=class_type,
                revenue=earned,
                direct_tutor_cost=paid,
                gross_profit=earned - paid,
            )
        )
    return rows


def operating_expenses(groups: Iterable[ExpenseGroup]) -> OperatingExpenses:
    """Totals by category and by vendor (``None``: no vendor); only non-zero
    rows, by amount descending, then id (#117 E10)."""
    by_category: dict[uuid.UUID, tuple[ExpenseCategoryRef, list[Money]]] = {}
    by_vendor: dict[uuid.UUID | None, tuple[VendorRef | None, list[Money]]] = {}
    amounts = []
    for group in groups:
        amounts.append(group.amount)
        by_category.setdefault(group.category.id, (group.category, []))[1].append(
            group.amount
        )
        vendor_id = group.vendor.id if group.vendor is not None else None
        by_vendor.setdefault(vendor_id, (group.vendor, []))[1].append(group.amount)
    categories = [
        CategoryAmount(category=ref, amount=Money.total(parts))
        for ref, parts in by_category.values()
    ]
    vendors = [
        VendorAmount(vendor=ref, amount=Money.total(parts))
        for ref, parts in by_vendor.values()
    ]
    categories = [c for c in categories if not c.amount.is_zero()]
    vendors = [v for v in vendors if not v.amount.is_zero()]
    categories.sort(key=lambda c: (-c.amount.amount, str(c.category.id)))
    vendors.sort(
        key=lambda v: (-v.amount.amount, "" if v.vendor is None else str(v.vendor.id))
    )
    return OperatingExpenses(
        total=Money.total(amounts), by_category=categories, by_vendor=vendors
    )


def profitability(
    *,
    basis: ReportBasis,
    start: date,
    end: date,
    today: date,
    rows: list[ClassRow],
    revenue_total: Money,
    cost_total: Money,
    expenses: OperatingExpenses,
) -> Profitability:
    gross = revenue_total - cost_total
    return Profitability.model_validate(
        {
            "basis": basis,
            "from": start,
            "to": end,
            "today": today,
            "class_rows": rows,
            "revenue_total": revenue_total,
            "direct_tutor_cost_total": cost_total,
            "gross_profit": gross,
            "operating_expenses": expenses,
            "net_result": gross - expenses.total,
        }
    )


def accrual_profitability(
    *,
    start: date,
    end: date,
    today: date,
    revenue: Sequence[RevenueEntry],
    costs: Sequence[CostEntry],
    expenses: Iterable[ExpenseGroup],
) -> Profitability:
    """The accrual report from dated entries (pure)."""
    rows = class_rows(revenue, costs, start, end)
    return profitability(
        basis="accrual",
        start=start,
        end=end,
        today=today,
        rows=rows,
        revenue_total=Money.total(r.revenue for r in rows),
        cost_total=Money.total(r.direct_tutor_cost for r in rows),
        expenses=operating_expenses(expenses),
    )


def deferred_revenue(revenue: Iterable[RevenueEntry], today: date) -> DeferredRevenue:
    """Every part not recognized by ``today``: undated or dated after it."""
    buckets: defaultdict[str, list[Money]] = defaultdict(list)
    for item in revenue:
        if item.on is None or item.on > today:
            buckets[item.bucket].append(item.amount)
    packages = Money.total(buckets["packages"])
    courses = Money.total(buckets["courses"])
    sessions = Money.total(buckets["scheduled_sessions"])
    other = Money.total(buckets["other"])
    return DeferredRevenue(
        total=packages + courses + sessions + other,
        packages=packages,
        courses=courses,
        scheduled_sessions=sessions,
        other=other,
    )


# --- Queries ---------------------------------------------------------------------


def _local_date(instant: datetime, zone: ZoneInfo) -> date:
    return instant.astimezone(zone).date()


def _money(value: Any) -> Money:
    return Money.zero() if value is None else Money(value)


async def _charge_revenue(session: AsyncSession, zone: ZoneInfo) -> list[RevenueEntry]:
    """Every charge's net, dated and classed (#117 E3-E7). Statements: the
    charges, the package movements, and at most three for the cohort
    schedules."""
    charges = billing.charge_figures()
    rows = (
        await session.execute(
            select(
                charges.c.id,
                charges.c.kind,
                charges.c.amount,
                charges.c.net,
                charges.c.service_date,
                charges.c.package_id,
                charges.c.last_adjusted_at,
                ClassSession.starts_at.label("session_starts_at"),
                ClassSession.kind.label("session_kind"),
                ClassSession.cohort_id.label("session_cohort_id"),
                ServicePrice.class_type.label("tuition_class_type"),
                Package.units_granted,
                Package.expires_on,
                CohortEnrollment.cohort_id,
                CohortEnrollment.enrolled_at,
                CohortEnrollment.withdrawn_at,
            )
            .outerjoin(ClassSession, ClassSession.id == charges.c.class_session_id)
            .outerjoin(TuitionPlan, TuitionPlan.id == charges.c.tuition_plan_id)
            .outerjoin(ServicePrice, ServicePrice.id == TuitionPlan.service_price_id)
            .outerjoin(Package, Package.id == charges.c.package_id)
            .outerjoin(
                CohortEnrollment,
                CohortEnrollment.id == charges.c.cohort_enrollment_id,
            )
            .order_by(charges.c.id)
        )
    ).all()
    packages = await _package_movements(session, zone)
    cohorts = {
        r.cohort_id for r in rows if r.kind == "cohort_enrollment" and r.cohort_id
    }
    schedules = await scheduling.owner_schedules(
        session, [scheduling.SeriesOwner.cohort(c) for c in sorted(cohorts)]
    )
    entries: list[RevenueEntry] = []
    for row in rows:
        net = Money(row.net)
        class_type = revenue_class(
            row.kind,
            session_kind=row.session_kind,
            tuition_class_type=row.tuition_class_type,
        )
        bucket = deferred_bucket(row.kind)
        if row.kind == "package_purchase" and row.package_id is not None:
            movements = packages.get(row.package_id, _Movements())
            closed_on = movements.closed_on
            if row.last_adjusted_at is not None:
                closed_on = _local_date(row.last_adjusted_at, zone)
            shares = package_shares(
                amount=Money(row.amount),
                net=net,
                units_granted=row.units_granted,
                consumptions=movements.consumptions,
                expired_on=row.expires_on if movements.expired else None,
                closed_on=closed_on,
            )
        elif row.kind == "cohort_enrollment" and row.cohort_id is not None:
            schedule = schedules[scheduling.SeriesOwner.cohort(row.cohort_id)]
            shares = course_shares(
                net=net,
                occurrences=[
                    Occurrence(start, _local_date(start, zone))
                    for start in schedule.starts
                ],
                enrolled_at=row.enrolled_at,
                withdrawn_at=row.withdrawn_at,
                service_date=row.service_date,
            )
        else:
            session_on = (
                _local_date(row.session_starts_at, zone)
                if row.session_starts_at is not None
                else None
            )
            shares = dated_shares(
                kind=row.kind,
                net=net,
                service_date=row.service_date,
                session_on=session_on,
            )
        cohort_id = (
            row.cohort_id if row.kind == "cohort_enrollment" else row.session_cohort_id
        )
        entries.extend(revenue_entries(class_type, bucket, shares, cohort_id))
    return entries


@dataclass
class _Movements:
    consumptions: list[Consumption] = field(default_factory=list)
    expired: bool = False
    closed_on: date | None = None


async def _package_movements(
    session: AsyncSession, zone: ZoneInfo
) -> dict[uuid.UUID, _Movements]:
    """The non-grant unit movements of every package: consumptions with the
    session's current start, whether it expired, and the local date of its
    cancellation or withdrawal units row. One statement."""
    found: dict[uuid.UUID, _Movements] = {}
    rows = await session.execute(
        select(
            PackageCreditTransaction.id,
            PackageCreditTransaction.package_id,
            PackageCreditTransaction.kind,
            PackageCreditTransaction.units,
            PackageCreditTransaction.created_at,
            ClassSession.starts_at,
        )
        .outerjoin(
            ClassSession, ClassSession.id == PackageCreditTransaction.class_session_id
        )
        .where(PackageCreditTransaction.kind != "grant")
        .order_by(PackageCreditTransaction.id)
    )
    for row in rows:
        movements = found.setdefault(row.package_id, _Movements())
        if row.kind == "consume" and row.starts_at is not None:
            movements.consumptions.append(
                Consumption(
                    starts_at=row.starts_at,
                    on=_local_date(row.starts_at, zone),
                    units=-row.units,
                    order_key=str(row.id),
                )
            )
        elif row.kind == "expire":
            movements.expired = True
        elif row.kind in ("reversal", "refund"):
            movements.closed_on = _local_date(row.created_at, zone)
    return found


def _cost_date() -> Any:
    return case(
        (
            PayrollLine.kind.in_(tuple(SESSION_LINE_KINDS)),
            func.coalesce(PayrollLine.service_date, PayrollPeriod.ends_on),
        ),
        else_=PayrollPeriod.ends_on,
    )


async def _cost_entries(
    session: AsyncSession, start: date | None = None, end: date | None = None
) -> list[CostEntry]:
    """The lines of finalized periods dated within ``[start, end]`` (all when
    not given), each with its cohort (#126 K2, outer joins on ids: one row per
    line). Never ``payslips`` (PM-NOTES #113). One statement."""
    origin = aliased(PayrollLine)
    line_session = aliased(ClassSession)
    origin_session = aliased(ClassSession)
    on = _cost_date().label("on")
    query = (
        select(
            on,
            PayrollLine.kind,
            PayrollLine.amount,
            PayrollLine.class_kind,
            PayrollAdjustment.kind.label("adjustment_kind"),
            PayrollAdjustment.class_kind.label("adjustment_class_kind"),
            origin.kind.label("origin_kind"),
            origin.class_kind.label("origin_class_kind"),
            ClassSession.kind.label("session_kind"),
            func.coalesce(
                line_session.cohort_id, ClassSession.cohort_id, origin_session.cohort_id
            ).label("cohort_id"),
        )
        .join(PayrollPeriod, PayrollPeriod.id == PayrollLine.payroll_period_id)
        .outerjoin(
            PayrollAdjustment, PayrollAdjustment.id == PayrollLine.payroll_adjustment_id
        )
        .outerjoin(origin, origin.id == PayrollAdjustment.origin_line_id)
        .outerjoin(ClassSession, ClassSession.id == PayrollAdjustment.class_session_id)
        .outerjoin(line_session, line_session.id == PayrollLine.class_session_id)
        .outerjoin(origin_session, origin_session.id == origin.class_session_id)
        .where(PayrollPeriod.status == FINALIZED)
    )
    if start is not None and end is not None:
        query = query.where(_cost_date().between(start, end))
    rows = await session.execute(query.order_by(PayrollLine.id))
    return [
        CostEntry(
            on=row.on,
            class_type=cost_class(
                line_kind=row.kind,
                line_class_kind=row.class_kind,
                adjustment_kind=row.adjustment_kind,
                adjustment_class_kind=row.adjustment_class_kind,
                origin_line_kind=row.origin_kind,
                origin_class_kind=row.origin_class_kind,
                adjustment_session_kind=row.session_kind,
            ),
            amount=Money(row.amount),
            cohort_id=row.cohort_id,
        )
        for row in rows
    ]


def _expense_refs(row: Any) -> tuple[ExpenseCategoryRef, VendorRef | None]:
    category = ExpenseCategoryRef(
        id=row.category_id,
        name=row.category_name,
        system_key=row.category_system_key,
        is_active=row.category_is_active,
    )
    vendor = (
        VendorRef(
            id=row.vendor_id, name=row.vendor_name, is_active=row.vendor_is_active
        )
        if row.vendor_id is not None
        else None
    )
    return category, vendor


def _label_columns() -> tuple[Any, ...]:
    return (
        ExpenseCategory.id.label("category_id"),
        ExpenseCategory.name.label("category_name"),
        ExpenseCategory.system_key.label("category_system_key"),
        ExpenseCategory.is_active.label("category_is_active"),
        Vendor.id.label("vendor_id"),
        Vendor.name.label("vendor_name"),
        Vendor.is_active.label("vendor_is_active"),
    )


def _month_of(column: Any) -> Any:
    """The first of ``column``'s month (a date column, SQL)."""
    return cast(func.date_trunc("month", column), Date).label("month")


def _expense_groups(rows: Iterable[Any], by_month: bool) -> list[ExpenseGroup]:
    return [
        ExpenseGroup(
            *_expense_refs(row),
            amount=Money(row.amount),
            month=row.month if by_month else None,
        )
        for row in rows
    ]


async def _accrual_expenses(
    session: AsyncSession,
    start: date,
    end: date,
    today: date,
    *,
    by_month: bool = False,
) -> list[ExpenseGroup]:
    """Expenses incurred in the range at their current net (void: ``0.00``),
    by category and vendor with their current rows, and by the month of
    ``incurred_on`` when ``by_month`` (#126 K1). One statement."""
    figures = finance.expense_figures(today)
    labels = _label_columns()
    keys = (*labels, _month_of(figures.c.incurred_on)) if by_month else labels
    rows = await session.execute(
        select(*keys, money_sum(figures.c.net).label("amount"))
        .select_from(figures)
        .join(ExpenseCategory, ExpenseCategory.id == figures.c.category_id)
        .outerjoin(Vendor, Vendor.id == figures.c.vendor_id)
        .where(figures.c.incurred_on.between(start, end))
        .group_by(*keys)
    )
    return _expense_groups(rows, by_month)


async def _cash_expenses(
    session: AsyncSession, start: date, end: date, *, by_month: bool = False
) -> list[ExpenseGroup]:
    """Unreversed expense payments paid in the range, by the expense's
    category and vendor, and by the month of ``paid_on`` when ``by_month``
    (#126 K1). One statement."""
    labels = _label_columns()
    keys = (*labels, _month_of(ExpensePayment.paid_on)) if by_month else labels
    rows = await session.execute(
        select(*keys, money_sum(ExpensePayment.amount).label("amount"))
        .select_from(ExpensePayment)
        .join(Expense, Expense.id == ExpensePayment.expense_id)
        .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .outerjoin(Vendor, Vendor.id == Expense.vendor_id)
        .where(finance.unreversed(), ExpensePayment.paid_on.between(start, end))
        .group_by(*keys)
    )
    return _expense_groups(rows, by_month)


async def _cash_sums(
    session: AsyncSession, start: date, end: date
) -> tuple[Money, Money]:
    """(cash revenue, payroll paid) over the range: unreversed payments by
    ``received_on`` minus refunds by ``refunded_on`` (E6); unreversed payroll
    payments by ``paid_on``. One statement."""
    received = (
        select(money_sum(Payment.amount))
        .where(billing.unreversed_payment(), Payment.received_on.between(start, end))
        .scalar_subquery()
    )
    refunded = (
        select(money_sum(Refund.amount))
        .where(Refund.refunded_on.between(start, end))
        .scalar_subquery()
    )
    payroll_paid = (
        select(money_sum(PayrollPayment.amount))
        .where(payroll.unreversed_payment(), PayrollPayment.paid_on.between(start, end))
        .scalar_subquery()
    )
    row = (
        await session.execute(
            select(
                received.label("received"),
                refunded.label("refunded"),
                payroll_paid.label("payroll_paid"),
            )
        )
    ).one()
    return _money(row.received) - _money(row.refunded), _money(row.payroll_paid)


@dataclass(frozen=True)
class CashMonth:
    """One month's cash sums (#126 K1): payments received minus refunds paid
    out, and payroll paid."""

    month: date
    revenue: Money
    payroll_paid: Money


async def _cash_sums_by_month(
    session: AsyncSession, start: date, end: date
) -> dict[date, CashMonth]:
    """:func:`_cash_sums` by month (the first of each month that has any
    row): unreversed payments by ``received_on``, refunds by
    ``refunded_on``, unreversed payroll payments by ``paid_on``, the same
    predicates. One statement (``UNION ALL`` grouped by source and month)."""
    sources = (
        ("received", Payment.received_on, Payment.amount, billing.unreversed_payment()),
        ("refunded", Refund.refunded_on, Refund.amount, None),
        (
            "payroll_paid",
            PayrollPayment.paid_on,
            PayrollPayment.amount,
            payroll.unreversed_payment(),
        ),
    )
    parts = []
    for source, on, amount, condition in sources:
        month = cast(func.date_trunc("month", on), Date)
        query = select(
            literal(source).label("source"),
            month.label("month"),
            money_sum(amount).label("amount"),
        ).where(on.between(start, end))
        if condition is not None:
            query = query.where(condition)
        parts.append(query.group_by(month))
    sums: defaultdict[date, dict[str, Money]] = defaultdict(dict)
    for row in await session.execute(union_all(*parts)):
        sums[row.month][row.source] = _money(row.amount)
    zero = Money.zero()
    return {
        month: CashMonth(
            month=month,
            revenue=found.get("received", zero) - found.get("refunded", zero),
            payroll_paid=found.get("payroll_paid", zero),
        )
        for month, found in sums.items()
    }


async def _zone_and_today(session: AsyncSession) -> tuple[ZoneInfo, date]:
    zone = await administration.get_center_time_zone(session)
    today = await administration.center_today(session)
    return ZoneInfo(zone), today


async def center_today(session: AsyncSession) -> date:
    """The center's today (the range check reads it)."""
    return await administration.center_today(session)


async def profitability_report(
    session: AsyncSession, *, basis: ReportBasis, start: date, end: date
) -> Profitability:
    """``GET …/profitability`` once the range is valid."""
    zone, today = await _zone_and_today(session)
    if basis == "cash":
        revenue_total, cost_total = await _cash_sums(session, start, end)
        return profitability(
            basis="cash",
            start=start,
            end=end,
            today=today,
            rows=[],
            revenue_total=revenue_total,
            cost_total=cost_total,
            expenses=operating_expenses(await _cash_expenses(session, start, end)),
        )
    return accrual_profitability(
        start=start,
        end=end,
        today=today,
        revenue=await _charge_revenue(session, zone),
        costs=await _cost_entries(session, start, end),
        expenses=await _accrual_expenses(session, start, end, today),
    )


async def _payables(session: AsyncSession, today: date) -> Payables:
    """Open non-void expenses (``expense_figures``), and Σ (total − paid) over
    finalized periods paid less than their total (``paid_sum``). Two
    statements."""
    figures = finance.expense_figures(today)
    zero = literal(Money.zero(), MoneyType())
    expense_row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(figures.c.open).filter(figures.c.status != VOID),
                    zero,
                    type_=MoneyType(),
                ).label("open"),
                func.coalesce(
                    func.sum(figures.c.open).filter(figures.c.status == OVERDUE),
                    zero,
                    type_=MoneyType(),
                ).label("overdue"),
            )
        )
    ).one()
    total = cast(PayrollPeriod.total_amount, Numeric)
    unpaid = (total - payroll.paid_sum(PayrollPeriod.id)).label("unpaid")
    per_period = (
        select(unpaid)
        .where(
            PayrollPeriod.status == FINALIZED, PayrollPeriod.total_amount.is_not(None)
        )
        .subquery("per_period")
    )
    payroll_row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(per_period.c.unpaid).filter(per_period.c.unpaid > 0), 0
                ).label("unpaid"),
                func.count().filter(per_period.c.unpaid > 0).label("periods"),
            )
        )
    ).one()
    expenses_open = Money(expense_row.open)
    payroll_unpaid = Money(payroll_row.unpaid)
    return Payables(
        expenses_open=expenses_open,
        expenses_overdue=Money(expense_row.overdue),
        payroll_unpaid=payroll_unpaid,
        payroll_periods_unpaid=int(payroll_row.periods or 0),
        total=expenses_open + payroll_unpaid,
    )


async def balances_report(session: AsyncSession) -> Balances:
    """``GET …/balances``: receivables, payables and deferred revenue as of
    the center's today (E9)."""
    zone, today = await _zone_and_today(session)
    receivable = await billing.receivable_totals(session, today)
    payables = await _payables(session, today)
    revenue = await _charge_revenue(session, zone)
    return Balances(
        today=today,
        receivables=Receivables(
            outstanding=receivable.outstanding,
            overdue=receivable.overdue,
            account_credit=receivable.account_credit,
            accounts_with_outstanding=receivable.accounts_with_outstanding,
        ),
        payables=payables,
        deferred_revenue=deferred_revenue(revenue, today),
    )


__all__ = [
    "CostEntry",
    "ExpenseGroup",
    "RevenueEntry",
    "accrual_profitability",
    "balances_report",
    "center_today",
    "class_rows",
    "deferred_revenue",
    "operating_expenses",
    "profitability_report",
    "revenue_entries",
]
