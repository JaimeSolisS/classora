"""The M7 demo data, part 2 (M7-demo-b #501): :func:`plan_demo_finance` and
the ``billing.demo_past_cohort_fee``, ``finance.demo_setup``,
``finance.demo_templates`` and ``finance.demo_expenses`` generators, after
M7-demo's three (:mod:`tests.factories.demo_payroll`).

**The plan (#501 "The plan")** is a pure function of ``T``, the center's
today, in local dates only (ADR-0004):

- ``F`` = :func:`~tests.factories.demo_payroll.plan_demo_payroll`'s floor
  (17-23 days before ``T``), ``M1`` = the 1st of ``T``'s month, ``A31`` = the
  latest 31st on or before ``T``, and ``T0``'s date (#353's
  ``plan_demo_academics(T).created_on``);
- PR7, the price of #353's finished cohort (1,800.00 from ``T0``'s date, the
  enrollments' local date, G5);
- the vendors V1-V4 (fictional names, ``.example`` contacts, G6);
- the templates TR, TI, TL, TV and TA, each created at ``starts_on - 1`` day
  12:00 (E2) with the periods it generates up to ``T`` (G7);
- the ordinary expenses X1-X9, every date, amount, reference and money step,
  and the status each has at any ``T`` (G8).

**Every write goes through the function the route calls, or the job step
(G2).** ``demo.finance`` makes every write
(:func:`~tests.factories.demo_clock.seed_actor` with its id, G3) except X5's
reversal, which ``demo.owner`` makes (only the Owner holds
``finance.refund.approve``). The job steps run with no actor in the seed's
single Unit of Work: M6-demo's enrollment-charge and reminder steps for the
cohort's fees (E3), and the recurring-expense job's loop
(``finance.service.due_template_ids`` + ``recurring_expenses.
generate_for_template``) with ``today = T`` (E2). Only the template creates
run under :func:`~tests.factories.demo_clock.seed_clock` (``starts_on`` must
be within 31 days of the center's today, #116 E5); everything else runs on
the real clock. Route-only checks are not run (``Idempotency-Key``,
reauthentication, the template route's job queueing), so no idempotency keys
or job rows are written. No ``persist``, no direct insert.

**Idempotent; stale, hand-configured or E2E databases are skipped (G9).**
Each generator writes all its rows or none and returns ``0`` when its marker
exists (a price scoped to #353's cohort; the vendor "Arrendadora Las
Jacarandas"; the template "Renta del local"; an expense not generated from a
template, once that vendor exists). Otherwise a #299 schedule from an
earlier day gives the stale skip; billing set up by hand gives the cohort
fee ``skipped (billing already set up; reset to add it)``; finance set up by
hand (a vendor, an expense, a template, a category without its key or an
inactive one) gives the three finance generators ``skipped (expenses already
set up; reset to add them)``; ``--without-billing`` gives ``skipped
(--without-billing)`` without touching the session. Fixed ``es_MX`` text, no Faker.
"""

import calendar
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.money import Money
from app.modules.administration import service as administration
from app.modules.administration.models import Notification
from app.modules.billing import service as billing
from app.modules.billing.models import (
    Charge,
    ChargeDiscount,
    ChargeReminder,
    PriceVersion,
    ServicePrice,
)
from app.modules.billing.schemas import ServicePriceCreate
from app.modules.courses.models import CohortEnrollment, CourseCohort
from app.modules.finance import service as finance
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
    ExpenseCategoryUpdate,
    ExpenseCreate,
    ExpensePaymentCreate,
    ExpensePaymentReversalCreate,
    ExpenseTemplateCreate,
    ExpenseTemplateUpdate,
    VendorCreate,
    VendorUpdate,
)
from app.workflows import price_catalog, recurring_expenses
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_academics import (
    COHORT_NAME,
    past_cohort_id,
    plan_demo_academics,
)
from tests.factories.demo_billing import (
    BILLING_CONFIGURED,
    DEMO_FINANCE_USERNAME,
    _early_skip,
    _pricing_ran,
    _settings_app_env,
    billing_skipped,
    post_enrollment_charges,
)
from tests.factories.demo_billing_money import send_charge_reminders
from tests.factories.demo_changes import (
    STALE_SCHEDULE,
    demo_subject_ids,
    schedule_from_today,
)
from tests.factories.demo_clock import seed_actor, seed_clock
from tests.factories.demo_payroll import plan_demo_payroll
from tests.factories.demo_schedule import (
    MATH_SECONDARY,
    StudentKey,
    demo_students,
    local_instant,
)
from tests.factories.identity import (
    DEMO_OWNER_USERNAME,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_principal,
    demo_user_id,
)

FINANCE_CONFIGURED: Final = "expenses already set up; reset to add them"
"""The skip reason of the three finance generators when expenses were set
up by hand before the first run (G9)."""

# --- Fixed es_MX texts and amounts (A4) -----------------------------------------------

FEE_AMOUNT: Final = "1800.00"
"""PR7: #353's cohort, ``course`` / ``full_course`` (G5)."""
COHORT_ENROLLMENTS: Final = 3
"""#353's three active enrollments (S, A and B)."""

STATIONERY: Final = "Papelería y material didáctico"
"""The seeded ``supplies`` category's new name (its key is cleared, #114
E3)."""
FURNITURE: Final = "Mobiliario"
"""The custom category, deactivated after X9."""
SEEDED_KEYS: Final = ("rent", "utilities", "supplies", "equipment", "maintenance")
"""The five categories migration 0083 seeds, with their keys."""

type CategoryKey = Literal[
    "rent", "utilities", "equipment", "maintenance", "stationery", "furniture"
]
type VendorKey = Literal["V1", "V2", "V3", "V4"]


@dataclass(frozen=True)
class PlannedVendor:
    key: VendorKey
    name: str
    contact: str


VENDORS: Final[tuple[PlannedVendor, ...]] = (
    PlannedVendor(
        "V1",
        "Arrendadora Las Jacarandas",
        "Lic. Rosa Méndez, cobranza@jacarandas.example",
    ),
    PlannedVendor("V2", "Papelería La Pluma Azul", "Ventas, ventas@plumaazul.example"),
    PlannedVendor("V3", "Red Colibrí Internet", "Soporte, soporte@redcolibri.example"),
    PlannedVendor(
        "V4", "Mobiliario Escolar Tlacuache", "Pedidos, pedidos@tlacuache.example"
    ),
)
"""V1-V4 (G6). V4 is used only by X9, then deactivated."""
SETUP_MARKER: Final = VENDORS[0].name

TR_NAME: Final = "Renta del local"
TI_NAME: Final = "Internet y telefonía"
TL_NAME: Final = "Limpieza semanal"
TV_NAME: Final = "Impresión de volantes de inscripción"
TA_NAME: Final = "Mantenimiento anual de climas"
TL_VOID_REASON: Final = "Semana sin servicio de limpieza"
TEMPLATE_CREATED_AT: Final = "12:00"

X5_REVERSAL_REASON: Final = "La transferencia fue rechazada por el banco"
X6_ADJUSTMENT_REASON: Final = "Cargo de envío no incluido en la nota"
X7_ADJUSTMENT_REASON: Final = "Proveedor equivocado; se capturó de nuevo"

type Method = Literal["cash", "transfer"]
type ExpenseStatus = Literal["unpaid", "overdue", "partially_paid", "paid", "void"]


# --- The plan (pure) ------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedPayment:
    amount: str
    paid_on: date
    method: Method
    reference: str | None = None


@dataclass(frozen=True)
class Pay:
    """Record a payment (``demo.finance``)."""

    payment: PlannedPayment


@dataclass(frozen=True)
class Reverse:
    """Reverse the expense's ``index``-th payment (``demo.owner``)."""

    index: int
    reason: str


@dataclass(frozen=True)
class Adjust:
    """Post an adjustment (``demo.finance``)."""

    amount: str
    reason: str


type MoneyStep = Pay | Reverse | Adjust


@dataclass(frozen=True)
class PlannedTemplate:
    key: str
    name: str
    category: CategoryKey
    vendor: VendorKey | None
    amount: str
    frequency: finance.Frequency
    starts_on: date
    ends_on: date | None
    due_offset_days: int
    periods: tuple[date, ...]
    """The periods the job generates up to ``T`` (and ``ends_on``)."""

    @property
    def created_on(self) -> date:
        """E2: created the day before ``starts_on``, at
        :data:`TEMPLATE_CREATED_AT` (seed clock)."""
        return self.starts_on - timedelta(days=1)

    def due_on(self, period: date) -> date:
        return period + timedelta(days=self.due_offset_days)


@dataclass(frozen=True)
class PlannedExpense:
    key: str
    description: str
    category: CategoryKey
    vendor: VendorKey | None
    amount: str
    incurred_on: date
    due_on: date
    status: ExpenseStatus
    """The status at any ``T`` (G8)."""
    receipt_reference: str | None = None
    steps: tuple[MoneyStep, ...] = ()

    @property
    def payments(self) -> tuple[PlannedPayment, ...]:
        return tuple(step.payment for step in self.steps if isinstance(step, Pay))


@dataclass(frozen=True)
class DemoFinancePlan:
    """Every #501 date and amount for one ``today`` (center-local)."""

    today: date
    floor: date
    """``F``: the payroll floor (17-23 days before ``T``)."""
    month_start: date
    """``M1``: the 1st of ``T``'s month."""
    anchor_31: date
    """``A31``: the latest 31st on or before ``T``."""
    created_on: date
    """``T0``'s date (#353's cohort enrollments): PR7's ``effective_from``."""
    fee_amount: str
    templates: tuple[PlannedTemplate, ...]
    """TR, TI, TL, TV, TA (G7 order)."""
    expenses: tuple[PlannedExpense, ...]
    """X1-X9, recorded in this order."""
    tl_paid: PlannedPayment
    """TL's first period, paid in cash on ``F``."""
    tl_void: Adjust
    """TL's second period, voided."""
    tv_paid: tuple[PlannedPayment, ...] = field(default=())
    """TV's two periods, paid in cash on their due dates."""
    vendors: tuple[PlannedVendor, ...] = VENDORS

    def template(self, key: str) -> PlannedTemplate:
        [found] = [t for t in self.templates if t.key == key]
        return found

    def expense(self, key: str) -> PlannedExpense:
        [found] = [x for x in self.expenses if x.key == key]
        return found

    @property
    def creation_order(self) -> list[PlannedTemplate]:
        """The templates in creation-instant order (G7)."""
        return sorted(self.templates, key=lambda t: t.created_on)

    @property
    def generated(self) -> int:
        return sum(len(t.periods) for t in self.templates)


def latest_31st(today: date) -> date:
    """``A31``: the latest 31st on or before ``today``."""
    year, month = today.year, today.month
    while True:
        if calendar.monthrange(year, month)[1] == 31 and date(year, month, 31) <= today:
            return date(year, month, 31)
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)


def template_periods(
    starts_on: date, frequency: finance.Frequency, ends_on: date | None, today: date
) -> tuple[date, ...]:
    """The periods from ``starts_on`` that start on or before ``today`` and
    ``ends_on`` (the job's catch-up, #116 D4)."""
    found: list[date] = []
    start = starts_on
    while start <= today and (ends_on is None or start <= ends_on):
        found.append(start)
        start = finance.next_after(starts_on, frequency, start)
    return tuple(found)


def plan_demo_finance(today: date) -> DemoFinancePlan:
    """The #501 plan for the center's ``today``."""
    f = plan_demo_payroll(today).floor
    m1 = today.replace(day=1)
    a31 = latest_31st(today)

    def template(
        key: str,
        name: str,
        category: CategoryKey,
        vendor: VendorKey | None,
        amount: str,
        frequency: finance.Frequency,
        starts_on: date,
        due_offset_days: int,
        ends_on: date | None = None,
    ) -> PlannedTemplate:
        return PlannedTemplate(
            key=key,
            name=name,
            category=category,
            vendor=vendor,
            amount=amount,
            frequency=frequency,
            starts_on=starts_on,
            ends_on=ends_on,
            due_offset_days=due_offset_days,
            periods=template_periods(starts_on, frequency, ends_on, today),
        )

    def day(n: int) -> date:
        return f + timedelta(days=n)

    tv = template("TV", TV_NAME, "stationery", "V2", "300.00", "weekly", f, 7, day(13))
    expenses = (
        PlannedExpense(
            "X1", "Cuadernos y plumones para el trimestre", "stationery", "V2",
            "850.00", today - timedelta(days=1), today + timedelta(days=15),
            "unpaid",
        ),
        PlannedExpense(
            "X2", "Reparación de la chapa de la entrada", "maintenance", None,
            "650.00", day(2), day(9), "overdue",
        ),
        PlannedExpense(
            "X3", "Impresora multifuncional", "equipment", "V2", "3800.00",
            today - timedelta(days=3), today + timedelta(days=20),
            "partially_paid", "FAC-DEMO-0003",
            (Pay(PlannedPayment("1500.00", today - timedelta(days=3), "transfer",
                                "PLA-DEMO-0001")),),
        ),
        PlannedExpense(
            "X4", "Proyector y pantalla para el aula 1", "equipment", None,
            "4200.00", day(3), day(17), "paid", "FAC-DEMO-0004",
            (
                Pay(PlannedPayment("2500.00", day(3), "transfer", "PRY-DEMO-0001")),
                Pay(PlannedPayment("1700.00", day(17), "transfer", "PRY-DEMO-0002")),
            ),
        ),
        PlannedExpense(
            "X5", "Instalación de punto de acceso WiFi", "equipment", "V3",
            "1200.00", day(1), day(8), "paid", None,
            (
                Pay(PlannedPayment("1200.00", day(8), "transfer", "COL-DEMO-0001")),
                Reverse(0, X5_REVERSAL_REASON),
                Pay(PlannedPayment("1200.00", day(10), "transfer", "COL-DEMO-0001")),
            ),
        ),
        PlannedExpense(
            "X6", "Material para la feria de ciencias", "stationery", "V2",
            "900.00", day(7), day(14), "paid", None,
            (
                Adjust("150.00", X6_ADJUSTMENT_REASON),
                Pay(PlannedPayment("1050.00", day(14), "cash")),
            ),
        ),
        PlannedExpense(
            "X7", "Garrafones de agua purificada", "utilities", "V3", "360.00",
            day(9), day(9), "void", None,
            (Adjust("-360.00", X7_ADJUSTMENT_REASON),),
        ),
        PlannedExpense(
            "X8", "Garrafones de agua purificada", "utilities", None, "360.00",
            day(9), day(9), "paid", None,
            (Pay(PlannedPayment("360.00", day(9), "cash")),),
        ),
        PlannedExpense(
            "X9", "Sillas apilables para el aula 2", "furniture", "V4", "2400.00",
            day(4), day(4), "paid", "FAC-DEMO-0009",
            (Pay(PlannedPayment("2400.00", day(4), "transfer", "TLA-DEMO-0001")),),
        ),
    )  # fmt: skip
    return DemoFinancePlan(
        today=today,
        floor=f,
        month_start=m1,
        anchor_31=a31,
        created_on=plan_demo_academics(today).created_on,
        fee_amount=FEE_AMOUNT,
        templates=(
            template("TR", TR_NAME, "rent", "V1", "6500.00", "monthly", m1, 4),
            template("TI", TI_NAME, "utilities", "V3", "650.00", "monthly", a31, 5),
            template("TL", TL_NAME, "maintenance", None, "450.00", "weekly", f, 0),
            tv,
            template("TA", TA_NAME, "maintenance", None, "3600.00", "annual", f, 30),
        ),
        expenses=expenses,
        tl_paid=PlannedPayment("450.00", f, "cash"),
        tl_void=Adjust("-450.00", TL_VOID_REASON),
        tv_paid=tuple(
            PlannedPayment(tv.amount, tv.due_on(period), "cash")
            for period in tv.periods
        ),
    )


# --- Shared by the four generators ----------------------------------------------------


async def _stale(session: AsyncSession, generator: str) -> bool:
    """#299's schedule is from an earlier day (the stale check, G9.3)."""
    tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, generator)
    student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, generator)
    students: dict[StudentKey, uuid.UUID] = await demo_students(
        session, student_user_id, generator
    )
    return not await schedule_from_today(session, tutor_id, students["S"], generator)


async def _vendor_marker(session: AsyncSession) -> bool:
    """The setup marker (G9.2): the vendor :data:`SETUP_MARKER`, any state."""
    found = await session.scalar(
        select(Vendor.id).where(Vendor.name == SETUP_MARKER).limit(1)
    )
    return found is not None


async def finance_configured_by_hand(session: AsyncSession) -> bool:
    """G9.4: expenses were set up without the demo (a vendor, an expense, a
    template, a category without its key or an inactive category)."""
    for model in (Vendor, Expense, ExpenseTemplate):
        if await session.scalar(select(model.id).limit(1)) is not None:
            return True
    found = await session.scalar(
        select(ExpenseCategory.id)
        .where(
            or_(
                ExpenseCategory.system_key.is_(None),
                ExpenseCategory.is_active.is_(False),
            )
        )
        .limit(1)
    )
    return found is not None


async def _require_payment_methods(session: AsyncSession, generator: str) -> None:
    for code in ("cash", "transfer"):
        if not await billing.active_payment_method(session, code):
            raise DemoPrerequisiteMissingError(
                f"an active {code} payment method must exist before {generator}"
            )


async def _categories(
    session: AsyncSession, generator: str
) -> dict[CategoryKey, uuid.UUID]:
    """The categories the templates and expenses use: the four keyed ones,
    the renamed ``supplies`` row and "Mobiliario" (G9.5)."""
    found: dict[CategoryKey, uuid.UUID] = {}
    keyed: tuple[CategoryKey, ...] = ("rent", "utilities", "equipment", "maintenance")
    for key in keyed:
        row = await session.scalar(
            select(ExpenseCategory.id).where(ExpenseCategory.system_key == key)
        )
        if row is None:
            raise DemoPrerequisiteMissingError(
                f"the seeded {key} category must exist before {generator}"
            )
        found[key] = row
    named: tuple[tuple[CategoryKey, str], ...] = (
        ("stationery", STATIONERY),
        ("furniture", FURNITURE),
    )
    for named_key, name in named:
        row = await session.scalar(
            select(ExpenseCategory.id).where(ExpenseCategory.name == name)
        )
        if row is None:
            raise DemoPrerequisiteMissingError(
                f"the finance.demo_setup category {name} must exist before {generator}"
            )
        found[named_key] = row
    return found


async def _vendors(session: AsyncSession, generator: str) -> dict[VendorKey, uuid.UUID]:
    """V1-V4 (G9.5)."""
    found: dict[VendorKey, uuid.UUID] = {}
    for vendor in VENDORS:
        row = await session.scalar(select(Vendor.id).where(Vendor.name == vendor.name))
        if row is None:
            raise DemoPrerequisiteMissingError(
                f"the finance.demo_setup vendor {vendor.key} must exist before"
                f" {generator}"
            )
        found[vendor.key] = row
    return found


@dataclass
class _Context:
    """What the later two finance generators look up first."""

    finance: Principal
    owner: Principal
    categories: dict[CategoryKey, uuid.UUID]
    vendors: dict[VendorKey, uuid.UUID]


async def _finance_checks(
    session: AsyncSession,
    generator: str,
    marker: Callable[[AsyncSession], Awaitable[bool]],
) -> int | GeneratorSkipped | None:
    """G9.2-G9.4 for the later two finance generators: ``0`` when the marker
    exists, a skip, or ``None`` to go on."""
    if await marker(session):
        return 0
    if await _stale(session, generator):
        return GeneratorSkipped(STALE_SCHEDULE)
    if not await _vendor_marker(session):
        return GeneratorSkipped(FINANCE_CONFIGURED)
    return None


async def _context(session: AsyncSession, generator: str) -> _Context:
    finance_principal = await demo_principal(session, DEMO_FINANCE_USERNAME, generator)
    owner = await demo_principal(session, DEMO_OWNER_USERNAME, generator)
    return _Context(
        finance=finance_principal,
        owner=owner,
        categories={},
        vendors={},
    )


async def _record_payment(
    session: AsyncSession,
    user: Principal,
    expense_id: uuid.UUID,
    payment: PlannedPayment,
) -> uuid.UUID:
    with seed_actor(session, user.user_id):
        recorded = await finance.record_payment(
            session,
            user.user_id,
            expense_id,
            ExpensePaymentCreate(
                paid_on=payment.paid_on,
                amount=Money(payment.amount),
                method_code=payment.method,
                reference=payment.reference,
            ),
        )
    return recorded.id


async def _adjust(
    session: AsyncSession, user: Principal, expense_id: uuid.UUID, step: Adjust
) -> None:
    with seed_actor(session, user.user_id):
        await finance.post_adjustment(
            session,
            user.user_id,
            expense_id,
            ExpenseAdjustmentCreate(amount=Money(step.amount), reason=step.reason),
        )


# --- billing.demo_past_cohort_fee (B) -------------------------------------------------

DEMO_PAST_COHORT_FEE_TABLES = (
    ServicePrice,
    PriceVersion,
    Charge,
    ChargeDiscount,
    ChargeReminder,
    Notification,
)
"""The tables whose added rows ``billing.demo_past_cohort_fee`` counts (B)."""


@dataclass
class FeeSteps:
    """What the job steps of the last run did (pinned by the tests)."""

    enrollment: Counter[str] = field(default_factory=Counter)
    reminders: Counter[str] = field(default_factory=Counter)


async def _cohort_priced(session: AsyncSession, cohort_id: uuid.UUID) -> bool:
    """The cohort-fee marker (G9.2): a price scoped to #353's cohort."""
    found = await session.scalar(
        select(ServicePrice.id).where(ServicePrice.cohort_id == cohort_id).limit(1)
    )
    return found is not None


class DemoPastCohortFeeGenerator:
    """``billing.demo_past_cohort_fee`` (#501 B): ``demo.finance`` prices #353's
    finished cohort (PR7, 1,800.00 from ``T0``'s date); then M6-demo's
    enrollment-charge step posts its three fees and the reminder step sends
    the payment reminders they call for (E3), with no actor, as the jobs.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once a price is scoped to #353's cohort.
    Skipped when #299's schedule is from an earlier day or billing was set up
    by hand; raises when a demo account, #353's cohort with its three active
    enrollments or Matemáticas (Secundaria) is missing.
    """

    name = "billing.demo_past_cohort_fee"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoFinancePlan] = plan_demo_finance,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.steps = FeeSteps()

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance_principal = await demo_principal(
            session, DEMO_FINANCE_USERNAME, self.name
        )
        await demo_principal(session, DEMO_OWNER_USERNAME, self.name)
        found = await session.scalar(
            select(CourseCohort.id).where(CourseCohort.name == COHORT_NAME)
        )
        if found is not None and await _cohort_priced(session, found):
            return 0
        if await _stale(session, self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if not await _pricing_ran(session, self.name):
            return GeneratorSkipped(BILLING_CONFIGURED)
        cohort_id = await past_cohort_id(session, self.name)
        enrolled = len(
            list(
                await session.scalars(
                    select(CohortEnrollment.id).where(
                        CohortEnrollment.cohort_id == cohort_id,
                        CohortEnrollment.withdrawn_at.is_(None),
                    )
                )
            )
        )
        if enrolled != COHORT_ENROLLMENTS:
            raise DemoPrerequisiteMissingError(
                f"the courses.demo_past_cohort enrollments must exist before"
                f" {self.name}"
            )
        subjects = await demo_subject_ids(session, (MATH_SECONDARY,), self.name)
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_PAST_COHORT_FEE_TABLES)

        # G5: PR7, by demo.finance (the route's service).
        with seed_actor(session, finance_principal.user_id):
            await price_catalog.create_service_price(
                session,
                ServicePriceCreate(
                    class_type="course",
                    pricing_model="full_course",
                    subject_id=subjects[MATH_SECONDARY],
                    cohort_id=cohort_id,
                    effective_from=plan.created_on,
                    amount=Money(plan.fee_amount),
                ),
            )
        # E3: the enrollment-charge step, then the reminder step, no actor.
        steps = FeeSteps()
        steps.enrollment = await post_enrollment_charges(session)
        steps.reminders = await send_charge_reminders(session)
        self.steps = steps
        return await count_rows(session, DEMO_PAST_COHORT_FEE_TABLES) - before


# --- finance.demo_setup (C) -----------------------------------------------------------

DEMO_FINANCE_SETUP_TABLES = (ExpenseCategory, Vendor)
"""The tables whose added rows ``finance.demo_setup`` counts (the rename is
an update, not counted, A2)."""


class DemoFinanceSetupGenerator:
    """``finance.demo_setup`` (#501 C): ``demo.finance`` renames the seeded
    ``supplies`` category to :data:`STATIONERY` (its key is cleared), adds
    :data:`FURNITURE` and the vendors V1-V4.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once the vendor :data:`SETUP_MARKER`
    exists. Skipped when #299's schedule is from an earlier day or expenses
    were set up by hand; raises when a demo account or a seeded category
    with its key is missing.
    """

    name = "finance.demo_setup"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoFinancePlan] = plan_demo_finance,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        context = await _context(session, self.name)
        if await _vendor_marker(session):
            return 0
        if await _stale(session, self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if await finance_configured_by_hand(session):
            return GeneratorSkipped(FINANCE_CONFIGURED)
        seeded = {
            row.system_key: row
            for row in await session.scalars(
                select(ExpenseCategory).where(
                    ExpenseCategory.system_key.in_(SEEDED_KEYS)
                )
            )
        }
        missing = [key for key in SEEDED_KEYS if key not in seeded]
        if missing:
            raise DemoPrerequisiteMissingError(
                f"the seeded {missing[0]} category must exist before {self.name}"
            )
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_FINANCE_SETUP_TABLES)

        with seed_actor(session, context.finance.user_id):
            supplies = seeded["supplies"]
            await finance.change_category(
                session,
                supplies.id,
                ExpenseCategoryUpdate(version=supplies.version, name=STATIONERY),
            )
            await finance.add_category(session, ExpenseCategoryCreate(name=FURNITURE))
            for vendor in plan.vendors:
                await finance.add_vendor(
                    session, VendorCreate(name=vendor.name, contact=vendor.contact)
                )
        return await count_rows(session, DEMO_FINANCE_SETUP_TABLES) - before


# --- finance.demo_templates (D) -------------------------------------------------------

DEMO_FINANCE_TEMPLATES_TABLES = (
    ExpenseTemplate,
    Expense,
    ExpensePayment,
    ExpenseAdjustment,
)
"""The tables whose added rows ``finance.demo_templates`` counts (the pause
and ``next_period_start`` are updates, not counted, A2)."""


@dataclass
class GenerationSteps:
    """The recurring-expense loop's counts of the last run (pinned)."""

    templates: int = 0
    generated: int = 0
    already_generated: int = 0


async def generate_recurring_expenses(
    session: AsyncSession, today: date
) -> GenerationSteps:
    """The ``finance:generate_recurring_expenses`` loop
    (``recurring_expenses.run_generate_recurring_expenses``) in the caller's
    Unit of Work (E2): every due template, keyset pages by id."""
    steps = GenerationSteps()
    after: uuid.UUID | None = None
    while True:
        page = await finance.due_template_ids(session, today=today, after=after)
        for template_id in page:
            steps.templates += 1
            done = await recurring_expenses.generate_for_template(
                session, template_id, today=today
            )
            steps.generated += done.generated
            steps.already_generated += done.already_generated
        if len(page) < finance.TEMPLATE_CANDIDATE_PAGE:
            return steps
        after = page[-1]


async def _template_marker(session: AsyncSession) -> bool:
    """The templates marker (G9.2): the template :data:`TR_NAME`."""
    found = await session.scalar(
        select(ExpenseTemplate.id).where(ExpenseTemplate.name == TR_NAME).limit(1)
    )
    return found is not None


async def _period_expense(
    session: AsyncSession, template_id: uuid.UUID, period: date
) -> uuid.UUID:
    found = await session.scalar(
        select(Expense.id).where(
            Expense.expense_template_id == template_id,
            Expense.period_start == period,
        )
    )
    assert found is not None  # noqa: S101 - just generated
    return found


class DemoFinanceTemplatesGenerator:
    """``finance.demo_templates`` (#501 D): ``demo.finance`` creates TR, TI,
    TL, TV and TA, each at 12:00 the day before it starts (seed clock, E2);
    the recurring-expense job's loop generates every due period with ``today
    = T`` (no actor); ``demo.finance`` pays TL's first week and TV's two
    weeks, voids TL's second week and pauses TL.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once the template :data:`TR_NAME`
    exists. Skipped when #299's schedule is from an earlier day or
    ``finance.demo_setup`` didn't run; raises when a demo account, V1-V4,
    the categories or a payment method is missing.
    """

    name = "finance.demo_templates"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoFinancePlan] = plan_demo_finance,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.steps = GenerationSteps()
        self.created: dict[str, uuid.UUID] = {}
        """The templates' ids by key, of the last run."""

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        context = await _context(session, self.name)
        checked = await _finance_checks(session, self.name, _template_marker)
        if checked is not None:
            return checked
        context.categories = await _categories(session, self.name)
        context.vendors = await _vendors(session, self.name)
        await _require_payment_methods(session, self.name)
        zone = await administration.get_center_time_zone(session)
        today = await administration.center_today(session)
        plan = self._plan(today)
        before = await count_rows(session, DEMO_FINANCE_TEMPLATES_TABLES)
        user = context.finance

        # G7, E2: each template at 12:00 the day before it starts.
        created: dict[str, uuid.UUID] = {}
        for planned in plan.creation_order:
            instant = local_instant(planned.created_on, TEMPLATE_CREATED_AT, zone)
            with seed_clock(instant), seed_actor(session, user.user_id):
                item = await finance.create_template(
                    session,
                    ExpenseTemplateCreate(
                        name=planned.name,
                        category_id=context.categories[planned.category],
                        vendor_id=(
                            None
                            if planned.vendor is None
                            else context.vendors[planned.vendor]
                        ),
                        amount=Money(planned.amount),
                        frequency=planned.frequency,
                        starts_on=planned.starts_on,
                        ends_on=planned.ends_on,
                        due_offset_days=planned.due_offset_days,
                    ),
                )
            created[planned.key] = item.id
        self.created = created

        # E2: the job's loop on the real clock, today = T, no actor.
        self.steps = await generate_recurring_expenses(session, today)

        # TL: the first week paid, the second voided, then paused.
        tl, tv = plan.template("TL"), plan.template("TV")
        first = await _period_expense(session, created["TL"], tl.periods[0])
        await _record_payment(session, user, first, plan.tl_paid)
        second = await _period_expense(session, created["TL"], tl.periods[1])
        await _adjust(session, user, second, plan.tl_void)
        # TV: both weeks paid on their due dates (it has ended).
        for period, payment in zip(tv.periods, plan.tv_paid, strict=True):
            expense_id = await _period_expense(session, created["TV"], period)
            await _record_payment(session, user, expense_id, payment)
        with seed_actor(session, user.user_id):
            current = await finance.get_template(session, created["TL"])
            await finance.change_template(
                session,
                created["TL"],
                ExpenseTemplateUpdate(version=current.version, is_active=False),
            )
        return await count_rows(session, DEMO_FINANCE_TEMPLATES_TABLES) - before


# --- finance.demo_expenses (E) --------------------------------------------------------

DEMO_FINANCE_EXPENSES_TABLES = (
    Expense,
    ExpensePayment,
    ExpensePaymentReversal,
    ExpenseAdjustment,
)
"""The tables whose added rows ``finance.demo_expenses`` counts (the two
deactivations are updates, not counted, A2)."""


async def _ordinary_expense(session: AsyncSession) -> bool:
    """The expenses marker (G9.2): an expense not generated from a template,
    once ``finance.demo_setup`` ran (its vendor exists). Without setup's
    marker an ordinary expense was entered by hand, so the run goes on to the
    hand-configured skip (F6; engineer D1)."""
    found = await session.scalar(
        select(Expense.id).where(Expense.expense_template_id.is_(None)).limit(1)
    )
    return found is not None and await _vendor_marker(session)


@dataclass(frozen=True)
class RecordedExpenses:
    """The ids ``finance.demo_expenses`` wrote in its last run (read by the
    tests): each expense by key, and its payments in plan order."""

    expenses: dict[str, uuid.UUID]
    payments: dict[str, list[uuid.UUID]]


class DemoFinanceExpensesGenerator:
    """``finance.demo_expenses`` (#501 E): ``demo.finance`` records X1-X9 with
    their payments and adjustments (X5's first payment reversed by
    ``demo.owner``), then deactivates :data:`FURNITURE` and V4, which only X9
    uses.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once an expense not generated from a
    template exists after ``finance.demo_setup`` ran. Skipped when #299's
    schedule is from an earlier day or ``finance.demo_setup`` didn't run;
    raises when a demo account, V1-V4, the
    categories or a payment method is missing. A refusal (a payment above
    the open amount, an inactive category) raises and rolls the run back.
    """

    name = "finance.demo_expenses"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoFinancePlan] = plan_demo_finance,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.recorded: RecordedExpenses | None = None

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        context = await _context(session, self.name)
        checked = await _finance_checks(session, self.name, _ordinary_expense)
        if checked is not None:
            return checked
        context.categories = await _categories(session, self.name)
        context.vendors = await _vendors(session, self.name)
        await _require_payment_methods(session, self.name)
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_FINANCE_EXPENSES_TABLES)
        user = context.finance

        expenses: dict[str, uuid.UUID] = {}
        payments: dict[str, list[uuid.UUID]] = {}
        for planned in plan.expenses:
            with seed_actor(session, user.user_id):
                created = await finance.create_expense(
                    session,
                    user.user_id,
                    ExpenseCreate(
                        category_id=context.categories[planned.category],
                        vendor_id=(
                            None
                            if planned.vendor is None
                            else context.vendors[planned.vendor]
                        ),
                        description=planned.description,
                        amount=Money(planned.amount),
                        incurred_on=planned.incurred_on,
                        due_on=planned.due_on,
                        receipt_reference=planned.receipt_reference,
                    ),
                )
            expenses[planned.key] = created.id
            paid: list[uuid.UUID] = []
            for step in planned.steps:
                if isinstance(step, Pay):
                    paid.append(
                        await _record_payment(session, user, created.id, step.payment)
                    )
                elif isinstance(step, Reverse):
                    with seed_actor(session, context.owner.user_id):
                        await finance.reverse_payment(
                            session,
                            context.owner.user_id,
                            paid[step.index],
                            ExpensePaymentReversalCreate(reason=step.reason),
                        )
                else:
                    await _adjust(session, user, created.id, step)
            payments[planned.key] = paid
        # E2: "Mobiliario" and V4, after X9 (no live template uses them).
        await self._deactivate(session, context)
        self.recorded = RecordedExpenses(expenses=expenses, payments=payments)
        return await count_rows(session, DEMO_FINANCE_EXPENSES_TABLES) - before

    @staticmethod
    async def _deactivate(session: AsyncSession, context: _Context) -> None:
        with seed_actor(session, context.finance.user_id):
            category = await session.scalar(
                select(ExpenseCategory)
                .where(ExpenseCategory.id == context.categories["furniture"])
                .execution_options(populate_existing=True)
            )
            assert category is not None  # noqa: S101 - looked up above
            await finance.change_category(
                session,
                category.id,
                ExpenseCategoryUpdate(version=category.version, is_active=False),
            )
            vendor = await session.scalar(
                select(Vendor)
                .where(Vendor.id == context.vendors["V4"])
                .execution_options(populate_existing=True)
            )
            assert vendor is not None  # noqa: S101 - looked up above
            await finance.change_vendor(
                session,
                vendor.id,
                VendorUpdate(version=vendor.version, is_active=False),
            )
