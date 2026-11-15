"""The M7 demo payroll data, part 1 (M7-demo #499): :func:`plan_demo_payroll`
and the ``payroll.demo_setup``, ``payroll.demo_periods`` and
``payroll.demo_corrections`` generators, after M6-demo-b's three
(:mod:`tests.factories.demo_billing_money`).

**The plan (#499 "The plan")** is a pure function of ``T``, the center's
today, on #299's ``P(k)`` (the k-th Monday-Friday strictly before ``T``,
nearest first): local dates and ``HH:MM`` times only (ADR-0004).

- the payroll floor ``F`` = the Monday on or before ``P(13)``; weekly periods
  from ``F``: three ended weeks for ``T`` on Monday to Wednesday, two for
  Thursday to Sunday (E4), then the current week, which stays ``open``;
- two new ``demo.tutor`` classes with S (E6), Matemáticas (Secundaria),
  ``Aula 1``, 16:00-17:00, created at ``T0`` = ``P(13) - 2`` days 12:00 (as
  #353's): L1 on ``P(12)`` (S present, 60 minutes; its closeout stays
  pending until after ``W(n-1)`` is finalized, so its approval becomes a
  ``late_approval`` in ``Wn``) and N1 on ``P(10)`` (S absent: a ``no_show``
  line). The attendance and the closeout are recorded at the end + 15
  minutes, N1's approval at the end + 2 hours;
- the payment amounts and references (PP1 pays ``W1`` whole, PP2 200.00 and
  PP3 100.00 on ``W2``, PP3 reversed), all ``paid_on`` = ``T``.

**Every write goes through the route workflow or the job step (G1, E2).**
``demo.finance`` sets payroll up, prepares, posts the manual adjustment and
records the payments; ``demo.owner`` finalizes and reverses; ``demo.tutor``
records L1 and N1; ``demo.coordinator`` approves them and corrects H2
(each account's :func:`~tests.factories.identity.demo_principal`, G2; the
real-clock writes also run under
:func:`~tests.factories.demo_clock.seed_actor` with that account). The jobs'
per-unit steps run with no actor in the seed's single Unit of Work:
``payroll_periods.open_tutor_periods``, ``payroll_adjustments.
select_candidates`` + ``post_one``, and M6-demo's session-charge and reminder
steps for L1 and N1 (E8; engineer D3). Only L1's and N1's creation,
recording and N1's approval run under
:func:`~tests.factories.demo_clock.seed_clock`; everything else runs on the
real clock (E7). Those seed-clock steps carry no audit actor
(``audit_events.actor_user_id`` is NULL), as #353's H1-H3 don't: they run
without :func:`~tests.factories.demo_clock.seed_actor`, and the domain
columns name the users (the offer accepted by ``demo.tutor``, the approval's
``approved_by`` ``demo.coordinator``; M7-demo-b #501 E4). No ``persist``, no
direct insert; payslips are issued by ``finalize`` and rendered only on
download, so the seed needs no Pango.

**Idempotent; stale, hand-configured or E2E databases are skipped (G8).**
Each generator writes all its rows or none and returns ``0`` when its
marker exists (the "Bono de puntualidad" type; a finalized ``demo.tutor``
period; a payment on a ``demo.tutor`` period). Otherwise a #299 schedule
from an earlier day gives the stale skip; payroll set up by hand (a floor,
terms, a rate or a pay item type) gives ``skipped (payroll already set up;
reset to add it)``; ``--without-billing`` gives ``skipped
(--without-billing)`` without touching the session. **The payroll floor
locks once a period exists (#106 E6)**, so a re-seed needs a clean database.
Fixed ``es_MX`` text, no Faker.
"""

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.money import Money
from app.modules.academics.models import (
    SessionCloseout,
    SessionCloseoutApproval,
    SessionCloseoutCorrection,
)
from app.modules.academics.schemas import (
    CloseoutApprove,
    CloseoutCorrection,
    CloseoutSummaryInput,
)
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.billing import service as billing
from app.modules.billing.models import Charge, ChargeDiscount, ChargeReminder
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
    PayrollSettings,
    Payslip,
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
)
from app.modules.payroll.settings import get_payroll_settings, update_payroll_settings
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_schemas import AttendanceMark
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSession,
    ClassSessionAttendance,
    ClassSessionParticipant,
)
from app.modules.scheduling.schemas import ClassSessionCreate
from app.workflows import (
    payroll_adjustments,
    payroll_compensation,
    payroll_manual_adjustments,
    payroll_pay_items,
    payroll_payments,
    payroll_periods,
    payroll_workflow,
    session_closeout,
    session_closeout_review,
)
from app.workflows.session_closeout_schemas import SessionCloseoutSubmit
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_academics import (
    DEMO_COORDINATOR_USERNAME,
    NEXT_STEPS,
    PERFORMANCE,
    HeldClass,
    classroom_id,
    held_session_id,
    past_cohort_id,
    plan_demo_academics,
)
from tests.factories.demo_billing import (
    DEMO_FINANCE_USERNAME,
    _early_skip,
    _settings_app_env,
    billing_skipped,
    post_session_charges,
)
from tests.factories.demo_billing_money import send_charge_reminders
from tests.factories.demo_changes import (
    STALE_SCHEDULE,
    demo_subject_ids,
    no_warnings,
    schedule_from_today,
)
from tests.factories.demo_clock import DemoPlanError, seed_actor, seed_clock
from tests.factories.demo_schedule import (
    AULA_1,
    MATH_SECONDARY,
    StudentKey,
    demo_students,
    local_instant,
    past_weekdays,
)
from tests.factories.identity import (
    DEMO_OWNER_USERNAME,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR2_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_principal,
    demo_user_id,
)

PAYROLL_CONFIGURED: Final = "payroll already set up; reset to add it"
"""The skip reason when payroll was set up by hand before the first run
(G8)."""

# --- Fixed es_MX texts and amounts (A5) -----------------------------------------------

BONUS_TYPE: Final = "Bono de puntualidad"
DEDUCTION_TYPE: Final = "Descuento por material"
BONUS_ITEM: Final = "Bono semanal por puntualidad"
DEDUCTION_ITEM: Final = "Material didáctico extraviado"
MANUAL_REASON: Final = "Apoyo en evento escolar"
CORRECTION_REASON: Final = "El tutor cubrió la hora completa"
REVERSAL_REASON: Final = "Pago registrado dos veces"
PP1_REFERENCE: Final = "NOM-DEMO-0001"
PP2_REFERENCE: Final = "NOM-DEMO-0002"
PP3_REFERENCE: Final = "NOM-DEMO-0003"
L1_TOPICS: Final = "Repaso de ecuaciones de primer grado"
N1_TOPICS: Final = "El alumno no se presentó a la clase."

INDIVIDUAL_RATE: Final = "240.00"
"""``demo.tutor``'s ``individual`` ``per_hour`` rate (G3)."""
GROUP_RATE: Final = "300.00"
"""``demo.tutor``'s ``group`` ``per_session`` rate (G3)."""
SALARY: Final = "2000.00"
"""``demo.tutor2``'s weekly salary (G3)."""
COURSE_RATE: Final = "4800.00"
"""``demo.tutor2``'s ``course`` ``per_course`` rate (G3)."""
BONUS_AMOUNT: Final = "150.00"
DEDUCTION_AMOUNT: Final = "80.00"
MANUAL_AMOUNT: Final = "120.00"
PP2_AMOUNT: Final = "200.00"
PP3_AMOUNT: Final = "100.00"
CORRECTED_MINUTES: Final = 60
"""H2's minutes after the correction (55 before, G6)."""
PAY_FREQUENCY: Final = "weekly"
CLASS_START: Final = "16:00"
CLASS_END: Final = "17:00"


# --- The plan (pure) ------------------------------------------------------------------


def monday_of(day: date) -> date:
    """The Monday on or before ``day``."""
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True)
class DemoPayrollPlan:
    """Every #499 date for one ``today`` (center-local)."""

    today: date
    floor: date
    """``F``: the Monday on or before ``P(13)`` (``payroll_starts_on``, the
    terms, rates and items' ``effective_from``)."""
    ended: tuple[date, ...]
    """The first days (Mondays) of ``W1 … Wn``, the ended weeks, oldest
    first."""
    current: date
    """The first day of the current week, which stays ``open``."""
    created_on: date
    """``T0``'s date: ``P(13) - 2`` days (L1 and N1 are created then)."""
    created_at: str
    l1: HeldClass
    """L1 (``P(12)``): recorded under the seed clock, approved later."""
    n1: HeldClass
    """N1 (``P(10)``): a no-show, approved at its end + 2 hours."""
    paid_on: date
    """Every payment's ``paid_on``: ``T``."""

    @property
    def n(self) -> int:
        return len(self.ended)

    def week_of(self, day: date) -> int | None:
        """``k`` when ``day`` is in ``Wk`` (1-based); ``0`` for the current
        week; ``None`` before ``F`` or after the current week."""
        if day < self.floor or day > self.current + timedelta(days=6):
            return None
        if day >= self.current:
            return 0
        return (day - self.floor).days // 7 + 1

    @property
    def instants(self) -> list[tuple[str, date, str]]:
        """Every seed instant ``(name, day, HH:MM)``, in applied order."""
        return [
            ("create", self.created_on, self.created_at),
            ("record_l1", self.l1.day, self.l1.closeout_at),
            ("record_n1", self.n1.day, self.n1.closeout_at),
            ("approve_n1", self.n1.day, self.n1.approve_at),
        ]


def _new_class(key: str, day: date, status: str, topics: str) -> HeldClass:
    return HeldClass(
        key=key,
        day=day,
        start=CLASS_START,
        end=CLASS_END,
        attendance=(("S", status),),  # type: ignore[arg-type]
        actual_minutes=60,
        topics=topics,
        student="S",
        subject=MATH_SECONDARY,
    )


def plan_demo_payroll(today: date) -> DemoPayrollPlan:
    """The #499 plan for the center's ``today``."""
    p = past_weekdays(today, 13)
    floor = monday_of(p[12])
    current = monday_of(today)
    ended = tuple(
        floor + timedelta(weeks=k) for k in range((current - floor).days // 7)
    )
    return DemoPayrollPlan(
        today=today,
        floor=floor,
        ended=ended,
        current=current,
        created_on=p[12] - timedelta(days=2),
        created_at="12:00",
        l1=_new_class("L1", p[11], "present", L1_TOPICS),
        n1=_new_class("N1", p[9], "absent", N1_TOPICS),
        paid_on=today,
    )


# --- Shared by the three generators ---------------------------------------------------


async def _bonus_type(session: AsyncSession) -> uuid.UUID | None:
    """The setup marker (G8): the pay item type :data:`BONUS_TYPE`, any
    state."""
    found: uuid.UUID | None = await session.scalar(
        select(PayItemType.id).where(PayItemType.name == BONUS_TYPE).limit(1)
    )
    return found


async def payroll_configured_by_hand(session: AsyncSession) -> bool:
    """G8: payroll was set up without the demo (a floor, terms, a rate or a
    pay item type)."""
    if await session.scalar(select(PayrollSettings.payroll_starts_on)) is not None:
        return True
    for model in (TutorPayTerms, CompensationRate, PayItemType):
        if await session.scalar(select(model.id).limit(1)) is not None:
            return True
    return False


async def _setup_ran(session: AsyncSession, generator: str) -> bool:
    """For the later two generators: ``True`` when ``payroll.demo_setup``
    ran; ``False`` when payroll was set up by hand (skip); raises
    otherwise."""
    if await _bonus_type(session) is not None:
        return True
    if await payroll_configured_by_hand(session):
        return False
    raise DemoPrerequisiteMissingError(
        f"the payroll.demo_setup terms must exist before {generator}"
    )


async def _require_payment_methods(session: AsyncSession, generator: str) -> None:
    for code in ("cash", "transfer"):
        if not await billing.active_payment_method(session, code):
            raise DemoPrerequisiteMissingError(
                f"an active {code} payment method must exist before {generator}"
            )


async def _require_held_classes(
    session: AsyncSession,
    today: date,
    tutor_id: uuid.UUID,
    students: dict[StudentKey, uuid.UUID],
    generator: str,
) -> dict[str, uuid.UUID]:
    """H1-H3 (closed out) and #353's cohort, or the prerequisite error; the
    ids of H1-H3 by key."""
    zone = await administration.get_center_time_zone(session)
    found: dict[str, uuid.UUID] = {}
    for held in plan_demo_academics(today).held:
        assert held.student is not None  # noqa: S101 - H1-H3 are individual
        found[held.key] = await held_session_id(
            session,
            tutor_id=tutor_id,
            starts_at=local_instant(held.day, held.start, zone),
            student_id=students[held.student],
            generator=generator,
            closed_out=True,
        )
    await past_cohort_id(session, generator)
    return found


async def _tutor_periods(
    session: AsyncSession, tutor_id: uuid.UUID
) -> list[PayrollPeriod]:
    """The tutor's periods, oldest first (a read; ``populate_existing`` so the
    workflow's writes show)."""
    return list(
        await session.scalars(
            select(PayrollPeriod)
            .where(PayrollPeriod.tutor_user_id == tutor_id)
            .order_by(PayrollPeriod.starts_on)
            .execution_options(populate_existing=True)
        )
    )


async def post_adjustments(session: AsyncSession) -> Counter[str]:
    """The ``payroll:post_adjustments`` run (``payroll_adjustments.
    run_post_adjustments``) in the caller's Unit of Work (E2): the selection,
    then ``post_one`` per candidate in ``to_post`` order. Counts
    ``candidates`` and each outcome."""
    outcomes: Counter[str] = Counter()
    floor = await payroll.payroll_starts_on(session)
    if floor is None:
        return outcomes
    zone = await administration.get_center_time_zone(session)
    today = await administration.center_today(session)
    selection = await payroll_adjustments.select_candidates(
        session, floor=floor, zone=zone
    )
    outcomes["candidates"] = selection.candidates
    for candidate in selection.to_post:
        outcomes[
            await payroll_adjustments.post_one(
                session, candidate, floor=floor, zone=zone, today=today
            )
        ] += 1
    return outcomes


# --- payroll.demo_setup (B) -----------------------------------------------------------

DEMO_SETUP_TABLES = (TutorPayTerms, CompensationRate, PayItemType, PayItem)
"""The tables whose added rows ``payroll.demo_setup`` counts (the
``payroll_settings`` update is not counted, A2)."""


class DemoPayrollSetupGenerator:
    """``payroll.demo_setup`` (#499 B): ``demo.finance`` turns payroll on from
    ``F``; gives ``demo.tutor`` weekly terms, an ``individual`` ``per_hour``
    and a ``group`` ``per_session`` rate, a recurring punctuality bonus and a
    one-time deduction, and ``demo.tutor2`` weekly terms with a salary and a
    ``course`` ``per_course`` rate, all from ``F``.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once the type :data:`BONUS_TYPE` exists.
    Skipped when #299's schedule is from an earlier day or payroll was set
    up by hand; raises when a demo account, S, H1-H3 or #353's cohort or an
    active ``cash`` or ``transfer`` payment method is missing.
    """

    name = "payroll.demo_setup"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoPayrollPlan] = plan_demo_payroll,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance = await demo_principal(session, DEMO_FINANCE_USERNAME, self.name)
        await demo_principal(session, DEMO_OWNER_USERNAME, self.name)
        await demo_principal(session, DEMO_COORDINATOR_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        tutor2_id = await demo_user_id(session, DEMO_TUTOR2_USERNAME, self.name)
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if await _bonus_type(session) is not None:
            return 0
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if await payroll_configured_by_hand(session):
            return GeneratorSkipped(PAYROLL_CONFIGURED)
        today = await administration.center_today(session)
        await _require_held_classes(session, today, tutor_id, students, self.name)
        await _require_payment_methods(session, self.name)
        plan = self._plan(today)
        before = await count_rows(session, DEMO_SETUP_TABLES)

        with seed_actor(session, finance.user_id):
            # B2: the floor, through the PATCH's service with the row's version.
            current = await get_payroll_settings(session)
            await update_payroll_settings(
                session,
                PayrollSettingsUpdate(
                    version=current.version,
                    payroll_starts_on=plan.floor,
                    biweekly_anchor_on=current.biweekly_anchor_on,
                    salary_proration_basis=current.salary_proration_basis,
                ),
            )
            # B3: terms and rates.
            for tutor, salary in ((tutor_id, None), (tutor2_id, Money(SALARY))):
                await payroll_compensation.add_pay_terms(
                    session,
                    finance,
                    tutor,
                    PayTermsCreate(
                        effective_from=plan.floor,
                        pay_frequency=PAY_FREQUENCY,
                        salary_amount=salary,
                    ),
                )
            for tutor, kind, model, rate in (
                (tutor_id, "individual", "per_hour", INDIVIDUAL_RATE),
                (tutor_id, "group", "per_session", GROUP_RATE),
                (tutor2_id, "course", "per_course", COURSE_RATE),
            ):
                await payroll_compensation.add_compensation_rate(
                    session,
                    finance,
                    tutor,
                    CompensationRateCreate.model_validate(
                        {
                            "class_kind": kind,
                            "effective_from": plan.floor,
                            "rate_model": model,
                            "rate": rate,
                        }
                    ),
                )
            # B3: the two types and demo.tutor's items.
            bonus = await payroll.add_pay_item_type(
                session, PayItemTypeCreate(name=BONUS_TYPE, direction="bonus")
            )
            deduction = await payroll.add_pay_item_type(
                session, PayItemTypeCreate(name=DEDUCTION_TYPE, direction="deduction")
            )
            for type_id, description, amount, recurrence in (
                (bonus.id, BONUS_ITEM, BONUS_AMOUNT, "recurring"),
                (deduction.id, DEDUCTION_ITEM, DEDUCTION_AMOUNT, "one_time"),
            ):
                await payroll_pay_items.add_item(
                    session,
                    finance,
                    tutor_id,
                    PayItemCreate(
                        pay_item_type_id=type_id,
                        description=description,
                        amount=Money(amount),
                        recurrence=recurrence,  # type: ignore[arg-type]
                        effective_from=plan.floor,
                    ),
                )
        return await count_rows(session, DEMO_SETUP_TABLES) - before


# --- payroll.demo_periods (C) ---------------------------------------------------------

DEMO_PERIODS_TABLES = (
    ClassSession,
    ClassSessionParticipant,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSessionAttendance,
    SessionCloseout,
    SessionCloseoutApproval,
    ApprovalRequest,
    Notification,
    Charge,
    ChargeDiscount,
    ChargeReminder,
    PayrollPeriod,
    PayrollLine,
    PayrollAdjustment,
    Payslip,
)
"""The tables whose added rows ``payroll.demo_periods`` counts (C; the
period status updates are not counted, A2)."""


@dataclass
class PeriodSteps:
    """What the job steps of the last run did (pinned by the tests)."""

    charges: Counter[str]
    reminders: Counter[str]
    opened: int
    adjustments: Counter[str]


async def _finalized_period_of(session: AsyncSession, tutor_id: uuid.UUID) -> bool:
    """The periods marker (G8): a finalized period of ``demo.tutor``."""
    found = await session.scalar(
        select(PayrollPeriod.id)
        .where(PayrollPeriod.tutor_user_id == tutor_id)
        .where(PayrollPeriod.status == "finalized")
        .limit(1)
    )
    return found is not None


async def _record(
    session: AsyncSession,
    *,
    class_session_id: uuid.UUID,
    planned: HeldClass,
    students: dict[StudentKey, uuid.UUID],
    zone: str,
    tutor: Principal,
) -> int:
    """Under the seed clock at the class's end + 15 minutes: the tutor's
    attendance and closeout submit. Returns the closeout's version."""
    with seed_clock(local_instant(planned.day, planned.closeout_at, zone)):
        version = await session.scalar(
            select(ClassSession.version).where(ClassSession.id == class_session_id)
        )
        assert version is not None  # noqa: S101 - just created
        marked = await scheduling.record_attendance(
            session,
            actor=tutor,
            class_session_id=class_session_id,
            version=version,
            marks=[
                AttendanceMark(student_id=students[key], status=status)
                for key, status in planned.attendance
            ],
        )
        absent = all(status == "absent" for _, status in planned.attendance)
        submitted = await session_closeout.submit_session_closeout(
            session,
            principal=tutor,
            class_session_id=class_session_id,
            body=SessionCloseoutSubmit(
                version=marked.version,
                actual_minutes=planned.actual_minutes,
                summary=CloseoutSummaryInput(
                    topics=planned.topics,
                    performance=None if absent else PERFORMANCE,
                    next_steps=None if absent else NEXT_STEPS,
                ),
            ),
        )
    assert submitted.closeout is not None  # noqa: S101 - just submitted
    return submitted.closeout.version


async def _finalize(
    session: AsyncSession,
    period_id: uuid.UUID,
    *,
    finance: Principal,
    owner: Principal,
) -> None:
    """``prepare`` (``demo.finance``; it recalculates first), then
    ``finalize`` (``demo.owner``, with ``prepared_at`` from the prepare
    result)."""
    with seed_actor(session, finance.user_id):
        prepared = await payroll_workflow.prepare(session, finance, period_id)
    assert prepared.prepared_at is not None  # noqa: S101 - just prepared
    with seed_actor(session, owner.user_id):
        await payroll_workflow.finalize(
            session, owner, period_id, prepared_at=prepared.prepared_at
        )


class DemoPayrollPeriodsGenerator:
    """``payroll.demo_periods`` (#499 C): L1 and N1 (created by ``demo.owner``
    at ``T0``, accepted and recorded by ``demo.tutor``, N1 approved by
    ``demo.coordinator``, under the seed clock), their session charges, the
    periods opened by the job step, ``W1 … W(n-1)`` finalized oldest first
    (``demo.finance`` prepares, ``demo.owner`` finalizes), L1's late approval
    posted by the adjustment step and a free manual adjustment of +120.00,
    both into ``Wn``, then ``Wn`` finalized, for both tutors.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once a ``demo.tutor`` period is
    finalized. Skipped when #299's schedule is from an earlier day or payroll
    was set up by hand; raises when a demo account, S, H1-H3 or #353's
    cohort, ``Aula 1`` or setup's terms are missing. A refused step (a slot
    taken, unresolved work) raises and rolls the run back.
    """

    name = "payroll.demo_periods"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoPayrollPlan] = plan_demo_payroll,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.steps = PeriodSteps(
            charges=Counter(), reminders=Counter(), opened=0, adjustments=Counter()
        )
        self.created: dict[str, uuid.UUID] = {}
        """L1's and N1's class ids of the last run."""

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance = await demo_principal(session, DEMO_FINANCE_USERNAME, self.name)
        owner = await demo_principal(session, DEMO_OWNER_USERNAME, self.name)
        reviewer = await demo_principal(session, DEMO_COORDINATOR_USERNAME, self.name)
        tutor = await demo_principal(session, DEMO_TUTOR_USERNAME, self.name)
        tutor2_id = await demo_user_id(session, DEMO_TUTOR2_USERNAME, self.name)
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if await _finalized_period_of(session, tutor.user_id):
            return 0
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        if not await _setup_ran(session, self.name):
            return GeneratorSkipped(PAYROLL_CONFIGURED)
        today = await administration.center_today(session)
        await _require_held_classes(session, today, tutor.user_id, students, self.name)
        subjects = await demo_subject_ids(session, (MATH_SECONDARY,), self.name)
        aula_1 = await classroom_id(session, AULA_1, self.name)
        zone = await administration.get_center_time_zone(session)
        plan = self._plan(today)
        before = await count_rows(session, DEMO_PERIODS_TABLES)

        # G5.1: L1 and N1 created at T0 by the Owner; demo.tutor accepts.
        created: dict[str, uuid.UUID] = {}
        with seed_clock(local_instant(plan.created_on, plan.created_at, zone)):
            for planned in (plan.l1, plan.n1):
                result = await scheduling.create_class_session(
                    session,
                    ClassSessionCreate(
                        kind="individual",
                        student_id=students["S"],
                        subject_id=subjects[MATH_SECONDARY],
                        tutor_user_id=tutor.user_id,
                        classroom_id=aula_1,
                        starts_at=local_instant(planned.day, planned.start, zone),
                        ends_at=local_instant(planned.day, planned.end, zone),
                    ),
                    actor_id=owner.user_id,
                )
                no_warnings(planned.key, list(result.warnings))
                assert result.pending_offer is not None  # noqa: S101
                await scheduling.answer_offer(
                    session,
                    result.pending_offer.id,
                    caller_id=tutor.user_id,
                    accept=True,
                )
                created[planned.key] = result.id
        self.created = created

        # G5.2: L1 recorded (left pending), then N1 recorded and approved.
        l1_version = await _record(
            session,
            class_session_id=created["L1"],
            planned=plan.l1,
            students=students,
            zone=zone,
            tutor=tutor,
        )
        n1_version = await _record(
            session,
            class_session_id=created["N1"],
            planned=plan.n1,
            students=students,
            zone=zone,
            tutor=tutor,
        )
        with seed_clock(local_instant(plan.n1.day, plan.n1.approve_at, zone)):
            await session_closeout_review.approve_closeout(
                session,
                principal=reviewer,
                class_session_id=created["N1"],
                body=CloseoutApprove(version=n1_version),
            )

        # G5.3: the session-charge step (E8), no actor (as the job).
        charges = await post_session_charges(session)
        # The reminder step too, so the billing worker finds nothing to send
        # for them after the seed (engineer D3, E8's parity).
        reminders = await send_charge_reminders(session)
        # G5.4: the open-periods step, both tutors.
        opened = 0
        for tutor_id in (tutor.user_id, tutor2_id):
            opened += await payroll_periods.open_tutor_periods(
                session, tutor_id, today=today
            )
        periods = {
            tutor_id: await _tutor_periods(session, tutor_id)
            for tutor_id in (tutor.user_id, tutor2_id)
        }
        for found in periods.values():
            ended = [p.id for p in found if p.ends_on < today]
            if [p.starts_on for p in found] != [*plan.ended, plan.current] or len(
                ended
            ) != plan.n:
                raise DemoPlanError(f"the periods differ from the plan: {found}")
        # G5.5: W1 … W(n-1), oldest first, demo.tutor then demo.tutor2.
        for k in range(plan.n - 1):
            for tutor_id in (tutor.user_id, tutor2_id):
                await _finalize(
                    session, periods[tutor_id][k].id, finance=finance, owner=owner
                )
        # G5.6: L1 approved on the real clock; the adjustment step posts it.
        with seed_actor(session, reviewer.user_id):
            await session_closeout_review.approve_closeout(
                session,
                principal=reviewer,
                class_session_id=created["L1"],
                body=CloseoutApprove(version=l1_version),
            )
        adjustments = await post_adjustments(session)
        # G5.7: the free manual adjustment (Wn, the earliest open period).
        with seed_actor(session, finance.user_id):
            await payroll_manual_adjustments.post_manual(
                session,
                finance,
                tutor.user_id,
                PayrollAdjustmentCreate(
                    amount=Money(MANUAL_AMOUNT), reason=MANUAL_REASON
                ),
            )
        # G5.8: Wn, both tutors.
        for tutor_id in (tutor.user_id, tutor2_id):
            await _finalize(
                session, periods[tutor_id][plan.n - 1].id, finance=finance, owner=owner
            )
        self.steps = PeriodSteps(
            charges=charges,
            reminders=reminders,
            opened=opened,
            adjustments=adjustments,
        )
        return await count_rows(session, DEMO_PERIODS_TABLES) - before


# --- payroll.demo_corrections (D) -----------------------------------------------------

DEMO_CORRECTIONS_TABLES = (
    SessionCloseoutApproval,
    SessionCloseoutCorrection,
    Notification,
    PayrollAdjustment,
    PayrollLine,
    PayrollPayment,
    PayrollPaymentReversal,
)
"""The tables whose added rows ``payroll.demo_corrections`` counts (D)."""


@dataclass(frozen=True)
class RecordedPayroll:
    """The ids ``payroll.demo_corrections`` wrote in its last run (read by the
    tests)."""

    pp1: uuid.UUID
    pp2: uuid.UUID
    pp3: uuid.UUID


async def _paid_on_tutor(session: AsyncSession, tutor_id: uuid.UUID) -> bool:
    """The corrections marker (G8): a payment, reversed or not, on a period
    of ``demo.tutor``."""
    found = await session.scalar(
        select(PayrollPayment.id)
        .join(PayrollPeriod, PayrollPeriod.id == PayrollPayment.payroll_period_id)
        .where(PayrollPeriod.tutor_user_id == tutor_id)
        .limit(1)
    )
    return found is not None


async def _h2_closeout(
    session: AsyncSession, h2_id: uuid.UUID, generator: str
) -> SessionCloseout:
    """H2's approved, never corrected closeout, or the prerequisite error."""
    closeout = await session.scalar(
        select(SessionCloseout)
        .where(SessionCloseout.class_session_id == h2_id)
        .execution_options(populate_existing=True)
    )
    if closeout is None or closeout.status != "approved":
        raise DemoPrerequisiteMissingError(
            f"H2's approved closeout must exist before {generator}"
        )
    corrected = await session.scalar(
        select(SessionCloseoutCorrection.id)
        .where(SessionCloseoutCorrection.closeout_id == closeout.id)
        .limit(1)
    )
    if corrected is not None:
        raise DemoPrerequisiteMissingError(
            f"H2 must not be corrected before {generator}"
        )
    return closeout


class DemoPayrollCorrectionsGenerator:
    """``payroll.demo_corrections`` (#499 D): ``demo.coordinator`` corrects
    H2's minutes from 55 to 60, and the adjustment step posts the
    ``hours_correction`` into ``demo.tutor``'s current week; ``demo.finance``
    pays ``W1`` whole (PP1) and 200.00 and 100.00 of ``W2`` (PP2, PP3);
    ``demo.owner`` reverses PP3. ``demo.tutor2`` stays unpaid.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once a ``demo.tutor`` period has a
    payment. Skipped when #299's schedule is from an earlier day or payroll
    was set up by hand; raises when a demo account, S, H1-H3, #353's cohort,
    H2's approved closeout (or H2 already corrected), the finalized periods
    or a payment method is missing. A refusal (a payment over the balance)
    raises and rolls the run back.
    """

    name = "payroll.demo_corrections"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoPayrollPlan] = plan_demo_payroll,
        skipped: Callable[[], bool] = billing_skipped,
        pp2_amount: str = PP2_AMOUNT,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self._pp2_amount = pp2_amount
        self.adjustments: Counter[str] = Counter()
        """The adjustment step's counts of the last run (pinned)."""
        self.recorded: RecordedPayroll | None = None

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance = await demo_principal(session, DEMO_FINANCE_USERNAME, self.name)
        owner = await demo_principal(session, DEMO_OWNER_USERNAME, self.name)
        reviewer = await demo_principal(session, DEMO_COORDINATOR_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        await demo_user_id(session, DEMO_TUTOR2_USERNAME, self.name)
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if await _paid_on_tutor(session, tutor_id):
            return 0
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if not await _setup_ran(session, self.name):
            return GeneratorSkipped(PAYROLL_CONFIGURED)
        today = await administration.center_today(session)
        held = await _require_held_classes(
            session, today, tutor_id, students, self.name
        )
        h2 = await _h2_closeout(session, held["H2"], self.name)
        await _require_payment_methods(session, self.name)
        plan = self._plan(today)
        finalized = [
            p
            for p in await _tutor_periods(session, tutor_id)
            if p.status == "finalized"
        ]
        if len(finalized) < 2:
            raise DemoPrerequisiteMissingError(
                "the payroll.demo_periods finalized weeks must exist before"
                f" {self.name}"
            )
        w1, w2 = finalized[0], finalized[1]
        assert w1.total_amount is not None  # noqa: S101 - finalized
        before = await count_rows(session, DEMO_CORRECTIONS_TABLES)

        # G6.1: H2 corrected (a new approved revision), then the job step.
        with seed_actor(session, reviewer.user_id):
            await session_closeout_review.correct_closeout(
                session,
                principal=reviewer,
                class_session_id=held["H2"],
                body=CloseoutCorrection(
                    version=h2.version,
                    actual_minutes=CORRECTED_MINUTES,
                    reason=CORRECTION_REASON,
                ),
            )
        self.adjustments = await post_adjustments(session)
        # G6.2: PP1-PP3 by demo.finance.
        recorded: list[uuid.UUID] = []
        with seed_actor(session, finance.user_id):
            for period, amount, method, reference in (
                (w1, w1.total_amount, "transfer", PP1_REFERENCE),
                (w2, Money(self._pp2_amount), "transfer", PP2_REFERENCE),
                (w2, Money(PP3_AMOUNT), "cash", PP3_REFERENCE),
            ):
                paid = await payroll_payments.record_payment(
                    session,
                    finance,
                    period.id,
                    PayrollPaymentCreate(
                        paid_on=plan.paid_on,
                        amount=amount,
                        method_code=method,
                        reference=reference,
                    ),
                )
                recorded.append(paid.id)
        # G6.3: PP3 reversed by demo.owner.
        with seed_actor(session, owner.user_id):
            await payroll_payments.reverse_payment(
                session,
                owner,
                recorded[2],
                PayrollPaymentReversalCreate(reason=REVERSAL_REASON),
            )
        self.recorded = RecordedPayroll(*recorded)
        return await count_rows(session, DEMO_CORRECTIONS_TABLES) - before
