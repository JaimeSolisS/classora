"""The M6 demo billing data, part 1 (M6-demo E1 (a)): :func:`plan_demo_billing`
and the ``billing.demo_pricing``, ``billing.demo_packages`` and
``billing.demo_charges`` generators.

**The plan (G4)** is a pure function of ``T``, the center's today, on #299's
``P(k)`` (the k-th Monday-Friday strictly before ``T``, nearest first, so
``P(k + 5) = P(k) - 7``): local dates and ``HH:MM`` times only.

- the billing floor ``B`` = ``P(13)``, the first day of #353's finished
  cohort, so every #353 and #354 class is billed;
- catalog prices from ``B`` (PR1 gets an upcoming version on the 1st of next
  month), a sibling discount (10 %) and a scholarship (25 %, for A), and two
  package products;
- PS: the 7-day Matemáticas (Primaria) package sold to Sib at ``P(13)``
  12:00, so it expires on ``P(8)`` with no Sib class in its window (H2 is on
  ``P(7)``) and lapses whole;
- PE: the 60-day Inglés package sold to S at ``P(3)`` 12:00 (after H3 on
  ``P(8)`` and #299's P4 on ``P(4)``, which stay charged), then a new class
  H4 (S, Inglés, ``demo.tutor``, ``Aula 1``, ``P(2)`` 17:30-18:30), created at
  ``P(3)`` 12:30, accepted, closed out by ``demo.tutor`` and approved by
  ``demo.coordinator``: the approval consumes one session, so 7 of 8 are
  left. (#299's legacy P4 can't be closed out: the tutor's attendance of a
  ``completed`` class without a closeout is locked, ``409
  attendance_locked``; the groomed G5 fallback, a new class in a slot the
  plan test proves free, is used.)
- TP1: a monthly tuition plan for F3's student from ``B``.

**Every write goes through the route workflow or the job step (E2 (a)).**
``demo.finance`` makes the billing writes (:func:`seed_actor`, G2, G3). The
charges come from the billing jobs' per-item steps run in the seed's single
Unit of Work, in each job's order (``session_charges.candidate_page`` +
``post_one``, ``enrollment_charges.candidate_page`` + ``post_one``,
``tuition_charges.post_plan``, ``package_expiry.pending_sessions`` +
``consume_pending`` + ``billing.service.expire_package``), so every rule,
lock and outcome is the job's and the run stays all or nothing. The sales and
H4 (create, closeout, approval) run under :func:`seed_clock`, one instant at
a time, in instant order (:attr:`DemoBillingPlan.seed_steps`); everything
else on the real clock. No ``persist``, no direct insert.

**Idempotent; stale, hand-configured or E2E databases are skipped (G6, G7).**
Each generator writes all its rows or none and returns ``0`` when its marker
exists. A #299 schedule from an earlier day gives ``skipped (demo schedule
from an earlier day; reset to add it)``; billing set up by hand before the
first run gives ``skipped (billing already set up; reset to add it)``;
``python -m tests.factories.demo --without-billing`` (``e2e/scripts/run.sh``)
gives ``skipped (--without-billing)`` without touching the session, so the
E2E database keeps billing off. Fixed ``es_MX`` text, no Faker.
"""

import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import get_settings
from app.core.money import Money
from app.modules.academics.models import SessionCloseout, SessionCloseoutApproval
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.billing import service as billing
from app.modules.billing.discounts import DiscountEligibility
from app.modules.billing.models import (
    BillingAccount,
    BillingSettings,
    Charge,
    ChargeDiscount,
    Discount,
    DiscountAssignment,
    Package,
    PackageCreditTransaction,
    PackageProduct,
    PriceVersion,
    ServicePrice,
    TuitionPlan,
)
from app.modules.billing.schemas import (
    BillingSettingsUpdate,
    PriceVersionCreate,
    ServicePriceCreate,
)
from app.modules.billing.settings import get_billing_settings, update_billing_settings
from app.modules.courses.models import CourseCohort
from app.modules.families.models import StudentGuardian
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassGroup,
    ClassSession,
    ClassSessionAttendance,
    ClassSessionParticipant,
)
from app.modules.scheduling.schemas import ClassSessionCreate
from app.workflows import (
    billing_discounts,
    enrollment_charges,
    package_expiry,
    packages,
    price_catalog,
    session_charges,
    tuition_charges,
    tuition_plans,
)
from app.workflows.charge_pricing import local_date
from app.workflows.package_schemas import PackageProductCreate, PackagePurchase
from app.workflows.tuition_plan_schemas import TuitionPlanCreate
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_academics import (
    DEMO_COORDINATOR_USERNAME,
    HeldClass,
    classroom_id,
    hold_class,
)
from tests.factories.demo_changes import (
    COHORT_NAME,
    STALE_SCHEDULE,
    demo_subject_ids,
    no_warnings,
    schedule_from_today,
)
from tests.factories.demo_clock import seed_actor, seed_clock
from tests.factories.demo_schedule import (
    AULA_1,
    CIENCIAS_SECONDARY,
    ENGLISH_SECONDARY,
    GROUP_NAME,
    MATH_PRIMARY,
    MATH_SECONDARY,
    StudentKey,
    SubjectKey,
    demo_students,
    local_instant,
    past_weekdays,
)
from tests.factories.identity import (
    DEMO_ACCOUNT_APP_ENVS,
    DEMO_OWNER_USERNAME,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_principal,
    demo_user_id,
)

DEMO_FINANCE_USERNAME: Final = "demo.finance"
"""The account that makes every billing write (G2)."""

WITHOUT_BILLING: Final = "--without-billing"
"""The skip reason under ``python -m tests.factories.demo --without-billing``
(E3, G7)."""
BILLING_CONFIGURED: Final = "billing already set up; reset to add it"
"""The skip reason when billing was set up by hand before the first run
(G6)."""

# --- Fixed es_MX texts and amounts (A6) -----------------------------------------------

SIBLING_DISCOUNT = "Descuento por hermanos"
SCHOLARSHIP = "Beca de apoyo"
PK1_NAME = "Paquete de 8 clases de inglés"
PK2_NAME = "Paquete de 4 clases de matemáticas"
H4_TOPICS = "Lectura de textos informativos en inglés"

PR1_AMOUNT = "350.00"
PR1_NEXT_AMOUNT = "380.00"
PR2_AMOUNT = "380.00"
PR3_AMOUNT = "300.00"
PR4_AMOUNT = "180.00"
PR5_AMOUNT = "2400.00"
PR6_AMOUNT = "1600.00"
PK1_AMOUNT = "2800.00"
PK2_AMOUNT = "1100.00"
SIBLING_PERCENT = "10"
SCHOLARSHIP_PERCENT = "25"
PK1_SESSIONS = 8
PK1_DAYS = 60
PK2_SESSIONS = 4
PK2_DAYS = 7
SALE_AT = "12:00"
H4_CREATED_AT = "12:30"
H4_START = "17:30"
H4_END = "18:30"


# --- The --without-billing flag (E3, G7) ----------------------------------------------

_without_billing: ContextVar[bool] = ContextVar("demo_without_billing", default=False)


def billing_skipped() -> bool:
    """Whether this run was started with ``--without-billing``."""
    return _without_billing.get()


@contextmanager
def without_billing(flag: bool) -> Iterator[None]:
    """Run the block with the ``--without-billing`` flag set to ``flag``
    (``tests.factories.demo.main``); the previous value comes back on exit."""
    token = _without_billing.set(flag)
    try:
        yield
    finally:
        _without_billing.reset(token)


# --- The plan (pure) ------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedPrice:
    """One catalog price (B): its key, class type, model and subject, the
    scope or duration, and the first version's amount."""

    key: str
    class_type: str
    pricing_model: str
    subject: SubjectKey
    amount: str
    duration_minutes: int | None = None
    scope: str | None = None
    """``"group"`` (scoped to :data:`GROUP_NAME`) or ``"cohort"`` (scoped to
    #303's cohort), else ``None``."""
    period_frequency: str | None = None


PRICES: Final[tuple[PlannedPrice, ...]] = (
    PlannedPrice("PR1", "individual", "per_session", MATH_SECONDARY, PR1_AMOUNT, 60),
    PlannedPrice("PR2", "individual", "per_session", ENGLISH_SECONDARY, PR2_AMOUNT, 60),
    PlannedPrice("PR3", "individual", "per_session", MATH_PRIMARY, PR3_AMOUNT, 60),
    PlannedPrice(
        "PR4", "group", "per_session", MATH_SECONDARY, PR4_AMOUNT, scope="group"
    ),
    PlannedPrice(
        "PR5", "course", "full_course", MATH_SECONDARY, PR5_AMOUNT, scope="cohort"
    ),
    PlannedPrice(
        "PR6",
        "individual",
        "per_period",
        CIENCIAS_SECONDARY,
        PR6_AMOUNT,
        period_frequency="monthly",
    ),
)
"""PR1-PR6 (B): every price applies to any level."""


@dataclass(frozen=True)
class SeedStep:
    """One step of ``billing.demo_packages`` under the seed clock."""

    action: str
    """``sell_ps``, ``sell_pe``, ``create_h4`` or ``hold_h4`` (the closeout;
    :func:`~tests.factories.demo_academics.hold_class` also runs the approval
    at its own instant, ``approve_h4`` in :attr:`DemoBillingPlan.instants`)."""
    day: date
    at: str


@dataclass(frozen=True)
class DemoBillingPlan:
    """Every M6-demo date for one ``today``."""

    today: date
    floor: date
    """``B`` = ``P(13)``: ``billing_starts_on``, every first price version and
    product version, and TP1's ``starts_on``."""
    pr1_next_from: date
    """PR1's upcoming version: the 1st of next month (always after ``T``)."""
    ps_sold_on: date
    """PS (PK2 to Sib): ``P(13)``."""
    pe_sold_on: date
    """PE (PK1 to S): ``P(3)``."""
    h4: HeldClass
    """The package-covered class H4 (``P(2)``) and what its closeout
    records."""
    h4_created_on: date
    """H4 is created (and its offer accepted) at ``P(3)`` 12:30."""
    sale_at: str = SALE_AT
    h4_created_at: str = H4_CREATED_AT
    seed_steps: tuple[SeedStep, ...] = field(default=())
    """The seed-clock steps, in instant order."""

    @property
    def instants(self) -> list[tuple[str, date, str]]:
        """Every seed instant ``(name, day, HH:MM)``, in applied order (the
        H4 approval included)."""
        found = [(step.action, step.day, step.at) for step in self.seed_steps]
        return [*found, ("approve_h4", self.h4.day, self.h4.approve_at)]

    @property
    def ps_expires_on(self) -> date:
        return self.ps_sold_on + timedelta(days=PK2_DAYS)

    @property
    def pe_expires_on(self) -> date:
        return self.pe_sold_on + timedelta(days=PK1_DAYS)


def first_of_next_month(today: date) -> date:
    return date(today.year + today.month // 12, today.month % 12 + 1, 1)


def plan_demo_billing(today: date) -> DemoBillingPlan:
    """The M6-demo plan for the center's ``today`` (G4)."""
    p = past_weekdays(today, 13)
    h4 = HeldClass(
        key="H4",
        day=p[1],
        start=H4_START,
        end=H4_END,
        attendance=(("S", "present"),),
        actual_minutes=60,
        topics=H4_TOPICS,
        student="S",
        subject=ENGLISH_SECONDARY,
    )
    steps = (
        SeedStep("sell_ps", p[12], SALE_AT),
        SeedStep("sell_pe", p[2], SALE_AT),
        SeedStep("create_h4", p[2], H4_CREATED_AT),
        SeedStep("hold_h4", h4.day, h4.closeout_at),
    )
    return DemoBillingPlan(
        today=today,
        floor=p[12],
        pr1_next_from=first_of_next_month(today),
        ps_sold_on=p[12],
        pe_sold_on=p[2],
        h4=h4,
        h4_created_on=p[2],
        seed_steps=steps,
    )


# --- Shared by the three generators ---------------------------------------------------


def _settings_app_env() -> str:
    return get_settings().app_env


async def billing_students(
    session: AsyncSession, generator: str
) -> dict[str, uuid.UUID]:
    """S, Sib, A, B and F3's adult student (the one with a ``grandparent``
    guardian, #195 B), looked up, never created."""
    student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, generator)
    students: dict[str, uuid.UUID] = dict(
        await demo_students(session, student_user_id, generator)
    )
    adults = list(
        await session.scalars(
            select(StudentGuardian.student_id).where(
                StudentGuardian.relationship == "grandparent"
            )
        )
    )
    if len(adults) != 1:
        raise DemoPrerequisiteMissingError(
            f"the families.demo_families students must exist before {generator}"
        )
    students["F3"] = adults[0]
    return students


async def _sibling_discount(session: AsyncSession) -> uuid.UUID | None:
    """The pricing marker (G6): the discount :data:`SIBLING_DISCOUNT`, any
    state."""
    found: uuid.UUID | None = await session.scalar(
        select(Discount.id).where(Discount.name == SIBLING_DISCOUNT).limit(1)
    )
    return found


async def configured_by_hand(session: AsyncSession) -> bool:
    """G6: billing was set up without the demo (a floor, a catalog or package
    item, or a discount)."""
    floor = await session.scalar(select(BillingSettings.billing_starts_on))
    if floor is not None:
        return True
    for model in (ServicePrice, Discount, PackageProduct):
        if await session.scalar(select(model.id).limit(1)) is not None:
            return True
    return False


async def _pricing_ran(session: AsyncSession, generator: str) -> bool:
    """For the later two generators: ``True`` when ``billing.demo_pricing``
    ran; ``False`` when billing was set up by hand (skip, G6); raises
    otherwise."""
    if await _sibling_discount(session) is not None:
        return True
    if await configured_by_hand(session):
        return False
    raise DemoPrerequisiteMissingError(
        f"the billing.demo_pricing prices must exist before {generator}"
    )


def _early_skip(
    app_env: Callable[[], str], skipped: Callable[[], bool]
) -> GeneratorSkipped | None:
    env = app_env()
    if env not in DEMO_ACCOUNT_APP_ENVS:
        return GeneratorSkipped(f"APP_ENV={env}; local only")
    if skipped():
        return GeneratorSkipped(WITHOUT_BILLING)
    return None


async def _named_id(
    session: AsyncSession, query: object, what: str, generator: str
) -> uuid.UUID:
    found: uuid.UUID | None = await session.scalar(query)  # type: ignore[call-overload]
    if found is None:
        raise DemoPrerequisiteMissingError(f"{what} must exist before {generator}")
    return found


# --- billing.demo_pricing (B) ---------------------------------------------------------

DEMO_PRICING_TABLES = (
    ServicePrice,
    PriceVersion,
    Discount,
    DiscountAssignment,
    PackageProduct,
)
"""The tables whose added rows ``billing.demo_pricing`` counts (the
``billing_settings`` update is not counted, A2)."""


class DemoPricingGenerator:
    """``billing.demo_pricing`` (M6-demo B): ``demo.finance`` turns billing on
    from ``B``, creates PR1-PR6 (PR1 with an upcoming version), the sibling
    discount and A's scholarship, and the two package products.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once the discount
    :data:`SIBLING_DISCOUNT` exists. Skipped when #299's schedule is from an
    earlier day or billing was set up by hand; raises when a demo account, a
    #195 student, a demo subject, the group or #303's cohort is missing.
    """

    name = "billing.demo_pricing"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoBillingPlan] = plan_demo_billing,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance_id = await demo_user_id(session, DEMO_FINANCE_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        students = await billing_students(session, self.name)
        if await _sibling_discount(session) is not None:
            return 0
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if await configured_by_hand(session):
            return GeneratorSkipped(BILLING_CONFIGURED)
        subjects = await demo_subject_ids(
            session,
            (MATH_SECONDARY, ENGLISH_SECONDARY, MATH_PRIMARY, CIENCIAS_SECONDARY),
            self.name,
        )
        scopes = {
            "group": await _named_id(
                session,
                select(ClassGroup.id).where(ClassGroup.name == GROUP_NAME),
                f"the group {GROUP_NAME}",
                self.name,
            ),
            "cohort": await _named_id(
                session,
                select(CourseCohort.id).where(CourseCohort.name == COHORT_NAME),
                f"the cohort {COHORT_NAME}",
                self.name,
            ),
        }
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_PRICING_TABLES)

        with seed_actor(session, finance_id):
            current = await get_billing_settings(session)
            await update_billing_settings(
                session,
                BillingSettingsUpdate(
                    version=current.version, billing_starts_on=plan.floor
                ),
            )
            for planned in PRICES:
                detail = await price_catalog.create_service_price(
                    session,
                    ServicePriceCreate(
                        class_type=planned.class_type,  # type: ignore[arg-type]
                        pricing_model=planned.pricing_model,  # type: ignore[arg-type]
                        subject_id=subjects[planned.subject],
                        group_id=scopes["group"] if planned.scope == "group" else None,
                        cohort_id=scopes["cohort"]
                        if planned.scope == "cohort"
                        else None,
                        duration_minutes=planned.duration_minutes,
                        period_frequency=planned.period_frequency,  # type: ignore[arg-type]
                        effective_from=plan.floor,
                        amount=Money(planned.amount),
                    ),
                )
                if planned.key == "PR1":
                    await price_catalog.add_price_version(
                        session,
                        detail.id,
                        PriceVersionCreate(
                            effective_from=plan.pr1_next_from,
                            amount=Money(PR1_NEXT_AMOUNT),
                        ),
                    )
            await billing_discounts.create_discount(
                session,
                billing.DiscountCreate(
                    kind="sibling",
                    audience="all",
                    name=SIBLING_DISCOUNT,
                    percent=SIBLING_PERCENT,
                    eligibility=DiscountEligibility(min_family_students=2),
                ),
            )
            scholarship = await billing_discounts.create_discount(
                session,
                billing.DiscountCreate(
                    kind="scholarship",
                    audience="assigned",
                    name=SCHOLARSHIP,
                    percent=SCHOLARSHIP_PERCENT,
                ),
            )
            await billing_discounts.add_assignment(
                session,
                scholarship.id,
                billing.DiscountAssignmentCreate(student_id=students["A"]),
            )
            for name, sessions, subject, days, amount in (
                (PK1_NAME, PK1_SESSIONS, ENGLISH_SECONDARY, PK1_DAYS, PK1_AMOUNT),
                (PK2_NAME, PK2_SESSIONS, MATH_PRIMARY, PK2_DAYS, PK2_AMOUNT),
            ):
                await packages.create_package_product(
                    session,
                    PackageProductCreate(
                        name=name,
                        unit="sessions",
                        quantity=sessions,
                        validity_days=days,
                        subject_id=subjects[subject],
                        amount=Money(amount),
                        effective_from=plan.floor,
                    ),
                )
        return await count_rows(session, DEMO_PRICING_TABLES) - before


# --- billing.demo_packages (C) --------------------------------------------------------

DEMO_PACKAGES_TABLES = (
    ClassSession,
    ClassSessionParticipant,
    AssignmentOffer,
    AssignmentOfferSession,
    BillingAccount,
    Package,
    PackageCreditTransaction,
    Charge,
    ChargeDiscount,
    ClassSessionAttendance,
    SessionCloseout,
    SessionCloseoutApproval,
    ApprovalRequest,
    Notification,
)
"""The tables whose added rows ``billing.demo_packages`` counts (C)."""


@dataclass(frozen=True)
class ExpiryCounts:
    """What the package expiry step did (the job's counts)."""

    packages: int = 0
    fallback_consumed: int = 0
    fallback_short: int = 0
    expired: int = 0
    already_expired: int = 0


async def expire_due_packages(session: AsyncSession) -> ExpiryCounts:
    """The ``billing:expire_packages`` loop (``package_expiry.
    run_expire_packages``) in the caller's Unit of Work: every due package,
    its pending sessions consumed, then ``expire_package`` (E2 (a))."""
    zone = await administration.get_center_time_zone(session)
    today = local_date(clock.now(), zone)
    counts: Counter[str] = Counter()
    after: uuid.UUID | None = None
    while True:
        page = await billing.due_packages(session, today=today, after=after)
        if not page:
            break
        after = page[-1].id
        for package in page:
            counts["packages"] += 1
            for item in await package_expiry.pending_sessions(
                session, package, zone=zone
            ):
                outcome = await package_expiry.consume_pending(
                    session, item.class_session_id, package.student_id, zone=zone
                )
                if outcome == "consumed":
                    counts["fallback_consumed"] += 1
                elif outcome == "short":
                    counts["fallback_short"] += 1
            expired = await billing.expire_package(session, package.id)
            if expired in ("expired", "already_expired"):
                counts[expired] += 1
        if len(page) < billing.DUE_PAGE:
            break
    return ExpiryCounts(**counts)


class DemoPackagesGenerator:
    """``billing.demo_packages`` (M6-demo C): ``demo.finance`` sells PK2 to
    Sib at ``P(13)`` and PK1 to S at ``P(3)``; ``demo.owner`` creates H4,
    ``demo.tutor`` accepts it, records attendance and closes it out, and
    ``demo.coordinator`` approves it (the approval consumes one of S's
    sessions), under the seed clock; then the package expiry step expires
    Sib's package on the real clock.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once S or Sib has a package (any status).
    Skipped when #299's schedule is from an earlier day or billing was set up
    by hand; raises when a demo account, a #195 student, a demo subject,
    ``Aula 1`` or the demo products are missing. A refused step (H4's slot
    taken, a sale refused) raises and rolls the run back.
    """

    name = "billing.demo_packages"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoBillingPlan] = plan_demo_billing,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.expiry = ExpiryCounts()
        """The expiry step's counts of the last run (pinned by the tests)."""

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance_id = await demo_user_id(session, DEMO_FINANCE_USERNAME, self.name)
        tutor = await demo_principal(session, DEMO_TUTOR_USERNAME, self.name)
        reviewer = await demo_principal(session, DEMO_COORDINATOR_USERNAME, self.name)
        students = await billing_students(session, self.name)
        if await session.scalar(
            select(Package.id)
            .where(Package.student_id.in_((students["S"], students["Sib"])))
            .limit(1)
        ):
            return 0
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        if not await _pricing_ran(session, self.name):
            return GeneratorSkipped(BILLING_CONFIGURED)
        products = {
            name: await _named_id(
                session,
                select(PackageProduct.id).where(PackageProduct.name == name),
                f"the package product {name}",
                self.name,
            )
            for name in (PK1_NAME, PK2_NAME)
        }
        zone = await administration.get_center_time_zone(session)
        plan = self._plan(await administration.center_today(session))
        owner_id = await demo_user_id(session, DEMO_OWNER_USERNAME, self.name)
        english = (await demo_subject_ids(session, (ENGLISH_SECONDARY,), self.name))[
            ENGLISH_SECONDARY
        ]
        aula_1 = await classroom_id(session, AULA_1, self.name)
        h4_students: dict[StudentKey, uuid.UUID] = {"S": students["S"]}
        h4_id: uuid.UUID | None = None
        before = await count_rows(session, DEMO_PACKAGES_TABLES)

        # C2: each step at its seed instant, in instant order.
        for step in plan.seed_steps:
            if step.action in ("sell_ps", "sell_pe"):
                student, product = (
                    ("Sib", PK2_NAME) if step.action == "sell_ps" else ("S", PK1_NAME)
                )
                with (
                    seed_clock(local_instant(step.day, step.at, zone)),
                    seed_actor(session, finance_id),
                ):
                    await packages.purchase_package(
                        session,
                        PackagePurchase(
                            student_id=students[student],
                            package_product_id=products[product],
                        ),
                    )
            elif step.action == "create_h4":
                with seed_clock(local_instant(step.day, step.at, zone)):
                    created = await scheduling.create_class_session(
                        session,
                        ClassSessionCreate(
                            kind="individual",
                            student_id=students["S"],
                            subject_id=english,
                            tutor_user_id=tutor.user_id,
                            classroom_id=aula_1,
                            starts_at=local_instant(plan.h4.day, plan.h4.start, zone),
                            ends_at=local_instant(plan.h4.day, plan.h4.end, zone),
                        ),
                        actor_id=owner_id,
                    )
                    no_warnings(plan.h4.key, list(created.warnings))
                    assert created.pending_offer is not None  # noqa: S101
                    await scheduling.answer_offer(
                        session,
                        created.pending_offer.id,
                        caller_id=tutor.user_id,
                        accept=True,
                    )
                h4_id = created.id
            elif step.action == "hold_h4":
                assert h4_id is not None  # noqa: S101 - created_h4 comes first
                # The closeout, then the approval at its own instant.
                await hold_class(
                    session,
                    class_session_id=h4_id,
                    planned=plan.h4,
                    students=h4_students,
                    zone=zone,
                    tutor=tutor,
                    reviewer=reviewer,
                )
        # C3: the expiry job's step on the real clock (no actor, as the job).
        self.expiry = await expire_due_packages(session)
        return await count_rows(session, DEMO_PACKAGES_TABLES) - before


# --- billing.demo_charges (D) ---------------------------------------------------------

DEMO_CHARGES_TABLES = (BillingAccount, TuitionPlan, Charge, ChargeDiscount)
"""The tables whose added rows ``billing.demo_charges`` counts (D)."""


@dataclass
class ChargeSteps:
    """The outcome counts of each job step of the last run (D3, pinned by
    the tests)."""

    session: Counter[str] = field(default_factory=Counter)
    enrollment: Counter[str] = field(default_factory=Counter)
    tuition: Counter[str] = field(default_factory=Counter)


async def post_session_charges(session: AsyncSession) -> Counter[str]:
    """The ``billing:post_session_charges`` loop (``session_charges.
    run_post_session_charges``) in the caller's Unit of Work (E2 (a))."""
    outcomes: Counter[str] = Counter()
    floor = await billing.billing_starts_on(session)
    if floor is None:
        return outcomes
    zone = await administration.get_center_time_zone(session)
    after: session_charges.Position | None = None
    while True:
        page, after = await session_charges.candidate_page(
            session, floor=floor, zone=zone, after=after
        )
        for candidate in page:
            outcomes[
                await session_charges.post_one(
                    session,
                    candidate.class_session_id,
                    candidate.student_id,
                    zone=zone,
                )
            ] += 1
        if after is None:
            return outcomes


async def post_enrollment_charges(session: AsyncSession) -> Counter[str]:
    """The ``billing:post_enrollment_charges`` loop (``enrollment_charges.
    run_post_enrollment_charges``) in the caller's Unit of Work (E2 (a))."""
    outcomes: Counter[str] = Counter()
    floor = await billing.billing_starts_on(session)
    if floor is None:
        return outcomes
    zone = await administration.get_center_time_zone(session)
    for kind in enrollment_charges.KINDS:
        after: enrollment_charges.Position | None = None
        while True:
            page, after = await enrollment_charges.candidate_page(
                session, kind, floor=floor, zone=zone, after=after
            )
            for candidate in page:
                outcomes[
                    await enrollment_charges.post_one(
                        session, kind, candidate.period.enrollment_id
                    )
                ] += 1
            if after is None:
                break
    return outcomes


class DemoChargesGenerator:
    """``billing.demo_charges`` (M6-demo D): ``demo.finance`` creates TP1 (F3's
    student, PR6, monthly from ``B``); then the session, enrollment and
    tuition job steps post every charge the billing jobs would post, on the
    real clock.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once F3's student has a tuition plan (any
    status). Skipped when #299's schedule is from an earlier day or billing
    was set up by hand; raises when a demo account, a #195 student or PR6 is
    missing.
    """

    name = "billing.demo_charges"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoBillingPlan] = plan_demo_billing,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.steps = ChargeSteps()

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance_id = await demo_user_id(session, DEMO_FINANCE_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        students = await billing_students(session, self.name)
        if await session.scalar(
            select(TuitionPlan.id)
            .where(TuitionPlan.student_id == students["F3"])
            .limit(1)
        ):
            return 0
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if not await _pricing_ran(session, self.name):
            return GeneratorSkipped(BILLING_CONFIGURED)
        subjects = await demo_subject_ids(session, (CIENCIAS_SECONDARY,), self.name)
        pr6 = await _named_id(
            session,
            select(ServicePrice.id).where(
                ServicePrice.class_type == "individual",
                ServicePrice.pricing_model == "per_period",
                ServicePrice.subject_id == subjects[CIENCIAS_SECONDARY],
                ServicePrice.group_id.is_(None),
            ),
            "the billing.demo_pricing monthly price",
            self.name,
        )
        zone = await administration.get_center_time_zone(session)
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_CHARGES_TABLES)

        # D1: TP1, by demo.finance.
        with seed_actor(session, finance_id):
            created, _ = await tuition_plans.create_tuition_plan(
                session,
                TuitionPlanCreate(
                    student_id=students["F3"],
                    service_price_id=pr6,
                    starts_on=plan.floor,
                ),
            )
        # D2-D4: the job steps, no actor (as the jobs).
        steps = ChargeSteps()
        steps.session = await post_session_charges(session)
        steps.enrollment = await post_enrollment_charges(session)
        floor = await billing.billing_starts_on(session)
        assert floor is not None  # noqa: S101 - pricing set it
        steps.tuition = await tuition_charges.post_plan(
            session,
            created.id,
            zone=zone,
            today=local_date(clock.now(), zone),
            first=floor,
        )
        self.steps = steps
        return await count_rows(session, DEMO_CHARGES_TABLES) - before
