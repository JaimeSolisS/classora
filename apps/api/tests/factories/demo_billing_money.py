"""The M6 demo billing data, part 2 (M6-demo-b #420): :func:`plan_demo_billing_money`
and the ``billing.demo_reconciliation``, ``billing.demo_payments`` and
``billing.demo_reminders`` generators, after part 1's three
(:mod:`tests.factories.demo_billing`).

**The plan (H4)** is a pure function of ``T``, the center's today, on #299's
``P(k)``/``D(k)`` and ``plan_demo_schedule(T).series.dates()``; local dates
only (ADR-0004):

- CX: the weekly series occurrence ``series.dates()[2]`` = ``D(13)`` (S,
  Matemáticas (Secundaria), ``demo.tutor``): ``dates()[0]`` is the closure
  and ``dates()[1]`` #303's substitution. ``demo.owner`` cancels it as the
  center, then the reconciliation step credits its charge;
- F2's due-date offset (5 days); PY1 (1,500.00 cash at ``P(3)``,
  oldest-first) and PY2 (500.00 transfer at ``P(1)``, kept as account
  credit) on F1; MC, F2's manual charge for A (250.00, service ``P(3)``, due
  ``P(1)``, so F2 is overdue: the #413 QA AC), paid by PY3 (250.00 transfer
  at ``T``, explicit) and reversed by ``demo.owner``, so MC is open again;
- DC: F1's oldest charge still open after PY1 gets the due date ``T + 2``
  with a reason; then the reminder step reminds every candidate account.

**Every write goes through the route workflow or the job step (H1, H2).**
``demo.owner`` cancels CX and reverses PY3; ``demo.finance`` makes the other
billing writes (:func:`~tests.factories.demo_clock.seed_actor`, H3). The
job steps (``schedule_reconciliation.candidate_page`` + ``reconcile_one``;
``billing.service.reminder_candidate_accounts`` +
``charge_reminders.remind_account``) run with no actor in the seed's single
Unit of Work, as part 1's do. Everything runs on the real clock. No
``persist``, no direct insert; receipts are issued by ``record_payment`` and
rendered only on download, so the seed needs no Pango.

**Idempotent; stale, hand-configured or E2E databases are skipped (H5).**
Each generator writes all its rows or none and returns ``0`` when its marker
exists (CX cancelled; a payment on F1's or F2's account; a due-date change
on F1's account). Otherwise a #299 schedule from an earlier day gives the
stale skip; part 1's charges missing because billing was set up by hand
gives ``skipped (billing already set up; reset to add it)``; ``--without-
billing`` gives ``skipped (--without-billing)`` without touching the
session. Fixed ``es_MX`` text, no Faker.
"""

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.money import Money
from app.modules.administration import service as administration
from app.modules.administration.models import Notification
from app.modules.billing import service as billing
from app.modules.billing.models import (
    AllocationRelease,
    BillingAccount,
    Charge,
    ChargeAdjustment,
    ChargeDueDateChange,
    ChargeReminder,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    Receipt,
    SessionCancellationDecision,
    TuitionPlan,
)
from app.modules.billing.schemas import (
    AllocationLineBody,
    PaymentAllocationBody,
    PaymentCreate,
)
from app.modules.families import service as families
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import ClassSession, ClassSessionParticipant
from app.workflows import (
    billing_accounts,
    billing_corrections,
    billing_payments,
    charge_reminders,
    schedule_reconciliation,
)
from app.workflows.charge_pricing import local_date
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_billing import (
    BILLING_CONFIGURED,
    DEMO_FINANCE_USERNAME,
    _early_skip,
    _settings_app_env,
    billing_skipped,
    billing_students,
    configured_by_hand,
)
from tests.factories.demo_changes import STALE_SCHEDULE, schedule_from_today
from tests.factories.demo_clock import seed_actor
from tests.factories.demo_schedule import (
    local_instant,
    past_weekdays,
    plan_demo_schedule,
)
from tests.factories.identity import (
    DEMO_OWNER_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_user_id,
)

# --- Fixed es_MX texts and amounts (A6) -----------------------------------------------

CX_REASON: Final = "Cierre del aula por mantenimiento"
MC_DESCRIPTION: Final = "Material didáctico"
RV_REASON: Final = "La transferencia fue rechazada por el banco"
DC_REASON: Final = "Prórroga acordada con la familia"
PY2_REFERENCE: Final = "TRF-DEMO-0001"
PY3_REFERENCE: Final = "TRF-DEMO-0002"

F2_OFFSET_DAYS: Final = 5
PY1_AMOUNT: Final = "1500.00"
PY2_AMOUNT: Final = "500.00"
MC_AMOUNT: Final = "250.00"
PY3_AMOUNT: Final = MC_AMOUNT
DC_DAYS_AHEAD: Final = 2


# --- The plan (pure, H4) --------------------------------------------------------------


@dataclass(frozen=True)
class DemoBillingMoneyPlan:
    """Every M6-demo-b date for one ``today`` (center-local)."""

    today: date
    cx_day: date
    """CX: ``series.dates()[2]`` = ``D(13)``."""
    cx_start: str
    """CX's start (the series' ``HH:MM``)."""
    py1_received_on: date
    """PY1: ``P(3)``."""
    py2_received_on: date
    """PY2: ``P(1)``."""
    mc_service_date: date
    """MC: ``P(3)``."""
    mc_due_date: date
    """MC: ``P(1)``, always before ``T``, so F2 is overdue (the #413 QA AC)."""
    py3_received_on: date
    """PY3: ``T``."""
    dc_due_date: date
    """DC's new due date: ``T + 2`` (the ``due_soon`` window on ``T``)."""


def plan_demo_billing_money(today: date) -> DemoBillingMoneyPlan:
    """The M6-demo-b plan for the center's ``today`` (H4)."""
    p = past_weekdays(today, 3)
    series = plan_demo_schedule(today).series
    return DemoBillingMoneyPlan(
        today=today,
        cx_day=series.dates()[2],
        cx_start=series.start,
        py1_received_on=p[2],
        py2_received_on=p[0],
        mc_service_date=p[2],
        mc_due_date=p[0],
        py3_received_on=today,
        dc_due_date=today + timedelta(days=DC_DAYS_AHEAD),
    )


# --- Shared by the three generators ---------------------------------------------------


async def _part1_ran(
    session: AsyncSession, students: dict[str, uuid.UUID], generator: str
) -> bool:
    """``True`` when part 1's charges ran (its marker: a tuition plan of F3's
    student); ``False`` when billing was set up by hand (skip, H5); raises
    otherwise."""
    if await session.scalar(
        select(TuitionPlan.id).where(TuitionPlan.student_id == students["F3"]).limit(1)
    ):
        return True
    if await configured_by_hand(session):
        return False
    raise DemoPrerequisiteMissingError(
        f"the billing.demo_charges charges must exist before {generator}"
    )


async def _family_id(
    session: AsyncSession, student_id: uuid.UUID, what: str, generator: str
) -> uuid.UUID:
    family = await families.family_of_student(session, student_id)
    if family is None:
        raise DemoPrerequisiteMissingError(f"{what} must exist before {generator}")
    return family.id


async def _account_id(session: AsyncSession, family_id: uuid.UUID) -> uuid.UUID | None:
    found: uuid.UUID | None = await session.scalar(
        select(BillingAccount.id).where(BillingAccount.family_id == family_id)
    )
    return found


async def _cx(
    session: AsyncSession,
    plan: DemoBillingMoneyPlan,
    s_id: uuid.UUID,
    generator: str,
) -> ClassSession:
    """S's session at CX's instant (the series occurrence)."""
    zone = await administration.get_center_time_zone(session)
    found = await session.scalar(
        select(ClassSession)
        .join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
        .where(
            ClassSession.starts_at == local_instant(plan.cx_day, plan.cx_start, zone)
        )
        .where(ClassSessionParticipant.student_id == s_id)
    )
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"the demo series occurrence of {plan.cx_day} must exist before {generator}"
        )
    return found


async def _series_cancellation(session: AsyncSession, s_id: uuid.UUID) -> bool:
    """The reconciliation marker (H5): a cancelled occurrence of S's weekly
    ``individual`` series (#299 D5; CX, or one cancelled by hand). It doesn't
    depend on today, so a rerun on a later day finds it."""
    found = await session.scalar(
        select(ClassSession.id)
        .join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
        .where(ClassSession.series_id.is_not(None))
        .where(ClassSession.kind == "individual")
        .where(ClassSession.state == "cancelled")
        .where(ClassSessionParticipant.student_id == s_id)
        .limit(1)
    )
    return found is not None


# --- billing.demo_reconciliation (B) --------------------------------------------------

DEMO_RECONCILIATION_TABLES = (SessionCancellationDecision, ChargeAdjustment)
"""The tables whose added rows ``billing.demo_reconciliation`` counts (A2)."""


async def reconcile_session_charges(session: AsyncSession) -> Counter[str]:
    """The ``billing:reconcile_session_charges`` loop (``schedule_reconciliation.
    run_reconcile_session_charges``) in the caller's Unit of Work (H2): every
    candidate, keyset pages until a short one."""
    events: Counter[str] = Counter()
    zone = await administration.get_center_time_zone(session)
    after: schedule_reconciliation.Key | None = None
    while True:
        page, after = await schedule_reconciliation.candidate_page(session, after=after)
        for class_session_id, student_id in page:
            events["candidates"] += 1
            events.update(
                await schedule_reconciliation.reconcile_one(
                    session, class_session_id, student_id, zone=zone
                )
            )
        if after is None:
            return events


class DemoReconciliationGenerator:
    """``billing.demo_reconciliation`` (M6-demo-b B): ``demo.owner`` cancels CX,
    S's series occurrence on ``D(13)``, as the center; then the
    reconciliation step over every candidate credits its charge
    (``cancellation_credited``).

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once CX is cancelled. Skipped when #299's
    schedule is from an earlier day or billing was set up by hand; raises
    when a demo account, a #195 student, part 1's charges, CX or its charge
    (net above zero) is missing.
    """

    name = "billing.demo_reconciliation"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoBillingMoneyPlan] = plan_demo_billing_money,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.events: Counter[str] = Counter()
        """The reconciliation step's events of the last run (pinned)."""

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        owner_id = await demo_user_id(session, DEMO_OWNER_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        students = await billing_students(session, self.name)
        if await _series_cancellation(session, students["S"]):
            return 0
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if not await _part1_ran(session, students, self.name):
            return GeneratorSkipped(BILLING_CONFIGURED)
        plan = self._plan(await administration.center_today(session))
        cx = await _cx(session, plan, students["S"], self.name)
        found = await billing.session_charge_of_key(session, cx.id, students["S"])
        charge = (
            None
            if found is None
            else await billing.reconciliation_charge(session, found[0])
        )
        if charge is None or not charge.net.is_positive():
            raise DemoPrerequisiteMissingError(
                f"the session charge of {plan.cx_day} must exist before {self.name}"
            )
        before = await count_rows(session, DEMO_RECONCILIATION_TABLES)

        # B2: the center cancels CX (as #303 D4), then the job step.
        with seed_actor(session, owner_id):
            await scheduling.cancel_class_session(
                session,
                cx.id,
                expected_version=cx.version,
                reason=CX_REASON,
                by_user_id=owner_id,
                initiated_by="center",
            )
        self.events = await reconcile_session_charges(session)
        return await count_rows(session, DEMO_RECONCILIATION_TABLES) - before


# --- billing.demo_payments (C) --------------------------------------------------------

DEMO_PAYMENTS_TABLES = (
    Payment,
    PaymentAllocation,
    Receipt,
    Charge,
    PaymentReversal,
    AllocationRelease,
)
"""The tables whose added rows ``billing.demo_payments`` counts (A2; the
offset is an update, not counted)."""


@dataclass(frozen=True)
class RecordedMoney:
    """The ids ``billing.demo_payments`` wrote in its last run (read by the
    tests)."""

    py1: uuid.UUID
    py2: uuid.UUID
    py3: uuid.UUID
    mc: uuid.UUID


async def _payment_on(session: AsyncSession, account_ids: list[uuid.UUID]) -> bool:
    """The payments marker (H5): any payment on these accounts."""
    if not account_ids:
        return False
    found = await session.scalar(
        select(Payment.id).where(Payment.billing_account_id.in_(account_ids)).limit(1)
    )
    return found is not None


class DemoPaymentsGenerator:
    """``billing.demo_payments`` (M6-demo-b C): ``demo.finance`` sets F2's
    due-date offset (5 days), records PY1 (1,500.00 cash, oldest-first) and
    PY2 (500.00 transfer, kept as account credit) on F1, posts MC (F2's
    manual charge for A, already past due) and pays it with PY3; then
    ``demo.owner`` reverses PY3, so MC is open again. Each payment gets its
    receipt from ``record_payment``.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once F1's or F2's account has a payment.
    Skipped when #299's schedule is from an earlier day or billing was set up
    by hand; raises when a demo account, a #195 student or part 1's charges
    are missing. A refusal (a future ``received_on``) raises and rolls the run
    back.
    """

    name = "billing.demo_payments"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoBillingMoneyPlan] = plan_demo_billing_money,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.recorded: RecordedMoney | None = None

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance_id = await demo_user_id(session, DEMO_FINANCE_USERNAME, self.name)
        owner_id = await demo_user_id(session, DEMO_OWNER_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        students = await billing_students(session, self.name)
        f1 = await _family_id(
            session, students["S"], "the first demo family", self.name
        )
        f2 = await _family_id(
            session, students["A"], "the second demo family", self.name
        )
        accounts = [
            account
            for account in (
                await _account_id(session, f1),
                await _account_id(session, f2),
            )
            if account is not None
        ]
        if await _payment_on(session, accounts):
            return 0
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if not await _part1_ran(session, students, self.name):
            return GeneratorSkipped(BILLING_CONFIGURED)
        plan = self._plan(await administration.center_today(session))
        f2_account = await session.scalar(
            select(BillingAccount).where(BillingAccount.family_id == f2)
        )
        before = await count_rows(session, DEMO_PAYMENTS_TABLES)

        with seed_actor(session, finance_id):
            # OF: F2's offset (version null for a family without an account).
            await billing_accounts.update_offset(
                session,
                f2,
                billing.BillingAccountUpdate(
                    version=None if f2_account is None else f2_account.version,
                    due_offset_days=F2_OFFSET_DAYS,
                ),
            )
            # PY1, PY2: F1's oldest charges, then account credit.
            py1 = await billing_payments.record_payment(
                session,
                f1,
                PaymentCreate(
                    amount=Money(PY1_AMOUNT),
                    received_on=plan.py1_received_on,
                    payment_method_code="cash",
                    allocation=PaymentAllocationBody(mode="oldest_first"),
                ),
            )
            py2 = await billing_payments.record_payment(
                session,
                f1,
                PaymentCreate(
                    amount=Money(PY2_AMOUNT),
                    received_on=plan.py2_received_on,
                    payment_method_code="transfer",
                    reference=PY2_REFERENCE,
                    allocation=PaymentAllocationBody(mode="none"),
                ),
            )
            # MC and PY3 on F2.
            mc = await billing_corrections.post_manual_charge(
                session,
                f2,
                billing.ManualChargeCreate(
                    student_id=students["A"],
                    description=MC_DESCRIPTION,
                    amount=Money(MC_AMOUNT),
                    service_date=plan.mc_service_date,
                    due_date=plan.mc_due_date,
                ),
            )
            py3 = await billing_payments.record_payment(
                session,
                f2,
                PaymentCreate(
                    amount=Money(PY3_AMOUNT),
                    received_on=plan.py3_received_on,
                    payment_method_code="transfer",
                    reference=PY3_REFERENCE,
                    allocation=PaymentAllocationBody(
                        mode="explicit",
                        lines=[
                            AllocationLineBody(
                                charge_id=mc.charge.id, amount=Money(PY3_AMOUNT)
                            )
                        ],
                    ),
                ),
            )
        # RV: reversals need finance.refund.approve (demo.owner, H3).
        with seed_actor(session, owner_id):
            await billing_corrections.reverse_payment(
                session,
                f2,
                py3.payment.id,
                billing.PaymentReversalCreate(reason=RV_REASON),
            )
        self.recorded = RecordedMoney(
            py1=py1.payment.id, py2=py2.payment.id, py3=py3.payment.id, mc=mc.charge.id
        )
        return await count_rows(session, DEMO_PAYMENTS_TABLES) - before


# --- billing.demo_reminders (D) -------------------------------------------------------

DEMO_REMINDERS_TABLES = (ChargeDueDateChange, ChargeReminder, Notification)
"""The tables whose added rows ``billing.demo_reminders`` counts (A2)."""


async def send_charge_reminders(session: AsyncSession) -> Counter[str]:
    """The ``billing:send_charge_reminders`` loop (``charge_reminders.
    run_send_charge_reminders``) in the caller's Unit of Work (H2): every
    candidate account on the center's today, keyset pages until a short
    one. Counts the job's outcomes, ``sent_charges`` and ``notifications``."""
    counts: Counter[str] = Counter()
    zone = await administration.get_center_time_zone(session)
    today = local_date(clock.now(), zone)
    after: uuid.UUID | None = None
    while True:
        page = await billing.reminder_candidate_accounts(
            session, today=today, after=after
        )
        if not page:
            return counts
        after = page[-1]
        for account_id in page:
            run = await charge_reminders.remind_account(
                session, account_id, today=today
            )
            counts[run.outcome] += 1
            counts["sent_charges"] += run.sent_charges
            counts["notifications"] += run.notifications
            if run.outcome == "billing_off":  # pragma: no cover - part 1 set it
                return counts
        if len(page) < billing.ACCOUNT_PAGE:
            return counts


async def oldest_open_charge(
    session: AsyncSession, account_id: uuid.UUID, today: date
) -> billing.ChargeBalance | None:
    """DC (D1): the account's oldest charge with open > 0, in #96's
    oldest-first order (effective due date, ``service_date``, ``posted_at``,
    ``id``)."""
    open_charges = [
        c
        for c in await billing.charge_balances(session, account_id, today=today)
        if c.open.is_positive()
    ]
    if not open_charges:
        return None
    return min(
        open_charges,
        key=lambda c: (c.effective_due_date, c.service_date, c.posted_at, c.charge_id),
    )


class DemoRemindersGenerator:
    """``billing.demo_reminders`` (M6-demo-b D): ``demo.finance`` gives DC,
    F1's oldest charge still open, the due date ``T + 2`` with a reason; then
    the reminder step reminds every candidate account on the center's today
    (only ``demo.guardian`` has a login, so F2 and F3 are ``no_recipient``).

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once a charge of F1's account has a
    due-date change. Skipped when #299's schedule is from an earlier day or
    billing was set up by hand; raises when a demo account, a #195 student,
    part 1's charges, F1's account or an open F1 charge is missing.
    """

    name = "billing.demo_reminders"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoBillingMoneyPlan] = plan_demo_billing_money,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped
        self.counts: Counter[str] = Counter()
        """The reminder step's counts of the last run (pinned)."""
        self.dc: uuid.UUID | None = None

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance_id = await demo_user_id(session, DEMO_FINANCE_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        students = await billing_students(session, self.name)
        f1 = await _family_id(
            session, students["S"], "the first demo family", self.name
        )
        f1_account = await _account_id(session, f1)
        if f1_account is not None and await session.scalar(
            select(ChargeDueDateChange.id)
            .where(ChargeDueDateChange.billing_account_id == f1_account)
            .limit(1)
        ):
            return 0
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if not await _part1_ran(session, students, self.name):
            return GeneratorSkipped(BILLING_CONFIGURED)
        if f1_account is None:
            raise DemoPrerequisiteMissingError(
                f"the first demo family's billing account must exist before {self.name}"
            )
        plan = self._plan(await administration.center_today(session))
        dc = await oldest_open_charge(session, f1_account, plan.today)
        if dc is None:
            raise DemoPrerequisiteMissingError(
                f"an open charge of the first demo family must exist before {self.name}"
            )
        before = await count_rows(session, DEMO_REMINDERS_TABLES)

        with seed_actor(session, finance_id):
            await billing_accounts.record_due_date_change(
                session,
                f1,
                dc.charge_id,
                billing.DueDateChangeCreate(
                    due_date=plan.dc_due_date, reason=DC_REASON
                ),
            )
        # The job step, no actor (as the job).
        self.counts = await send_charge_reminders(session)
        self.dc = dc.charge_id
        return await count_rows(session, DEMO_REMINDERS_TABLES) - before
