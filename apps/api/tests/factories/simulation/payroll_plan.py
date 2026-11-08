"""The simulation's payroll plan (#553 HF-01e): pure, built by
:func:`build_payroll` from the people plan and HF-01b's timetable, so the
same arguments give the same plan on any day (no clock, no database, no
RNG: every choice follows from the timetable).

**Setup (Finance, at SIM_SETUP).** Payroll starts on 31 Aug 2026 (a Monday,
also the biweekly anchor). Two new session pay rule versions from 31 Aug
pay a ``late`` family cancellation and a ``no_show`` at 50 % (the makeup
rule stays 100 %). Two pay item types: a punctuality bonus and a lost
materials deduction.

**Terms and rates.** The two [one] individual-only tutors with the fewest
held classes (no correction, no late review, not the joiner) are salaried,
paid **monthly** from 1 Sep, with no rate (the salary covers their work).
Every other tutor is paid **biweekly** from 31 Aug (the joiner from the day
they join): an ``individual`` rate (``per_hour``, or ``per_session`` for two
[one] of them), a ``group`` ``per_hour`` rate when they teach a group, and a
``course`` rate when they teach a course session (``per_course`` for a
cohort's own tutor, ``per_session`` otherwise). The rates exist for every
kind of work a tutor does, so no work is ever unresolved.

**Periods.** The ended ones (before :data:`~.anchor.SIM_ANCHOR`) are
prepared by Finance on the first working day after they end at 12:00,
finalized by the Owner on the next working day at 10:00 and paid by Finance
on the working day after that at 11:00 (the amount is the period's total,
read when paying; a 0.00 period needs no payment). The fortnight of
9-22 Nov and November are open at the anchor. The payroll-period job step
opens the periods; the adjustment job step runs every evening.

**Work after lock** (HF-01b's timetable, :func:`~.timetable.fortnight_end`):
the two late group reviews become ``late_approval`` adjustments and the
correction made after its fortnight an ``hours_correction``.

**Extras.** A recurring punctuality bonus for two [one] tutors from 1 Oct,
a one-time materials deduction on 20 Oct, a manual adjustment for the
science fair, and the joiner's pay advance (a negative manual adjustment)
larger than their first period's pay, which Finance carries forward into
the next fortnight (``carry_forward``) before preparing it. One payment is
reversed by the Owner the next morning and paid again that afternoon.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal

from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_SETUP,
    SIM_START,
    ZONE,
    local_instant,
)
from tests.factories.simulation.plan import CLOSURES, SimulationPlan, TutorPlan
from tests.factories.simulation.texts import TextPool
from tests.factories.simulation.timetable import (
    PAY_FORTNIGHTS_FROM,
    ClassPlan,
    CorrectAttendance,
    CorrectCloseout,
    DecideChange,
    ReviewCloseout,
    Timetable,
    fortnight_end,
)

type Frequency = Literal["biweekly", "monthly"]
type ClassKind = Literal["individual", "group", "course"]
type RateModel = Literal["per_hour", "per_session", "per_course"]

PAYROLL_STARTS_ON: Final = PAY_FORTNIGHTS_FROM
"""The payroll start floor and the biweekly anchor (a Monday)."""
MONTHLY_FROM: Final = SIM_START
"""Monthly terms start on 1 Sep, so September is the first whole month (a
31 Aug start would make a one-day August period)."""
RULES_FROM: Final = PAYROLL_STARTS_ON
RULE_PERCENT: Final = 50
"""The new ``late_cancellation`` and ``no_show`` versions pay 50 %."""
MAKEUP_PERCENT: Final = 100
"""The seeded makeup rule, unchanged."""
SALARIES: Final = (1900, 2200)
"""The salaried tutors' monthly salaries (HF-01g E2: 120-220 MXN per average
held hour)."""
INDIVIDUAL_HOURLY: Final = (150, 160, 170, 180)
INDIVIDUAL_SESSION: Final = 190
GROUP_HOURLY: Final = 230
COURSE_RATE: Final = 4000
"""A cohort tutor's ``per_course`` rate (split over the cohort's sessions)."""
COURSE_SESSION: Final = 250
BONUS_AMOUNT: Final = 200
DEDUCTION_AMOUNT: Final = 180
EVENT_AMOUNT: Final = 350
BONUS_FROM: Final = date(2026, 10, 1)
BONUS_AT: Final = local_instant(date(2026, 9, 30), "16:00")
DEDUCTION_ON: Final = date(2026, 10, 20)
DEDUCTION_AT: Final = local_instant(DEDUCTION_ON, "17:00")
EVENT_AT: Final = local_instant(date(2026, 10, 26), "09:00")
ADVANCE_DELAY: Final = timedelta(days=1)
"""The advance is recorded the day after the joiner's terms, once the job
has opened their first period."""
CARRY_TIME: Final = "11:30"
PREPARE_TIME: Final = "12:00"
FINALIZE_TIME: Final = "10:00"
PAY_TIME: Final = "11:00"
REVERSE_TIME: Final = "10:00"
REPAY_TIME: Final = "17:00"
OPEN_STEP_TIME: Final = "01:50"
"""The ``payroll:open_periods`` job's daily tick (07:50 UTC)."""
ADJUSTMENT_STEP_TIME: Final = "23:45"
"""The evening run of ``payroll:post_adjustments`` on a day with work after
lock (every 15 minutes in the worker; once that evening is enough here, and
B5 checks nothing is left)."""
SALARIED: Final[dict[str, int]] = {"full": 2, "small": 1}
SESSION_PRICED: Final[dict[str, int]] = {"full": 2, "small": 1}
BONUSES: Final[dict[str, int]] = {"full": 2, "small": 1}
CLOSED: Final = frozenset(day for _, day in CLOSURES)


def working_day(day: date) -> date:
    """``day`` or the next Monday-Friday that isn't a closure."""
    while day.isoweekday() > 5 or day in CLOSED:
        day += timedelta(days=1)
    return day


def money(amount: int | Decimal) -> str:
    return f"{Decimal(amount):.2f}"


@dataclass(frozen=True)
class TermsPlan:
    tutor: str
    effective_from: date
    frequency: Frequency
    salary: int | None
    at: datetime


@dataclass(frozen=True)
class RatePlan:
    tutor: str
    class_kind: ClassKind
    model: RateModel
    rate: int
    effective_from: date
    at: datetime


@dataclass(frozen=True)
class ItemPlan:
    tutor: str
    kind: Literal["bonus", "deduction"]
    description: str
    amount: int
    recurrence: Literal["recurring", "one_time"]
    effective_from: date
    at: datetime


@dataclass(frozen=True)
class PeriodPlan:
    """One tutor's period. An ended one has its three working days."""

    tutor: str
    starts_on: date
    ends_on: date
    frequency: Frequency
    prepare_at: datetime | None = None
    finalize_at: datetime | None = None
    pay_at: datetime | None = None

    @property
    def ended(self) -> bool:
        return self.ends_on < SIM_ANCHOR


@dataclass(frozen=True)
class ManualPlan:
    key: Literal["advance", "event"]
    tutor: str
    amount: Decimal
    reason: str
    at: datetime


@dataclass(frozen=True)
class CarryPlan:
    tutor: str
    period_start: date
    at: datetime
    estimated_pay: Decimal
    """The plan's estimate of the period's pay before the advance."""


@dataclass(frozen=True)
class ReversalPlan:
    tutor: str
    period_start: date
    paid_at: datetime
    reversed_at: datetime
    repaid_at: datetime
    reason: str


@dataclass(frozen=True)
class Payroll:
    salaried: tuple[str, ...]
    joiner: str | None
    terms: tuple[TermsPlan, ...]
    rates: tuple[RatePlan, ...]
    item_types: dict[str, str]
    """``bonus`` / ``deduction`` → the type's name."""
    items: tuple[ItemPlan, ...]
    periods: tuple[PeriodPlan, ...]
    """Every period up to the one holding the anchor, by tutor and date."""
    manuals: tuple[ManualPlan, ...]
    carry: CarryPlan | None
    reversal: ReversalPlan
    open_days: tuple[date, ...]
    """The days the payroll-period job step has a period to open."""
    adjustment_days: tuple[date, ...]
    """The days with work after lock (an approval, a correction or a paying
    cancellation decided after its period's finalize): the evening
    adjustment job step runs on them (and at the anchor, which finds
    nothing left)."""

    def tutor_periods(self, tutor: str) -> list[PeriodPlan]:
        return [p for p in self.periods if p.tutor == tutor]

    @property
    def ended(self) -> tuple[PeriodPlan, ...]:
        return tuple(p for p in self.periods if p.ended)

    def rate(self, tutor: str, kind: str) -> RatePlan | None:
        return next(
            (r for r in self.rates if r.tutor == tutor and r.class_kind == kind), None
        )


# --- Who does what --------------------------------------------------------------------


def _work(timetable: Timetable) -> dict[str, list[ClassPlan]]:
    """Each tutor's held classes, and the classes of a paying (``late``)
    family cancellation, by the tutor who had them."""
    late = {
        r.cls
        for r in timetable.change_requests
        if r.classification == "late" and r.decision == "approved"
    }
    found: dict[str, list[ClassPlan]] = {}
    for c in timetable.classes:
        if c.final.tutor is None:
            continue
        if c.status == "completed" or (c.status == "cancelled" and c.key in late):
            found.setdefault(c.final.tutor, []).append(c)
    return found


def _salaried(plan: SimulationPlan, work: dict[str, list[ClassPlan]]) -> list[str]:
    timetable = plan.timetable
    # The work after lock must be paid by a rate (a salary covers it).
    busy = {timetable.cls(k).final.tutor for k in timetable.late_reviews} | {
        timetable.cls(e.cls).final.tutor for e in timetable.closeout_corrections
    }
    candidates = [
        t
        for t in plan.tutors
        if t.joins_on <= SIM_START
        and t.key not in busy
        and {c.kind for c in work.get(t.key, [])} <= {"individual"}
    ]
    candidates.sort(key=lambda t: (len(work.get(t.key, [])), t.key))
    return sorted(t.key for t in candidates[: SALARIED[plan.profile]])


def _rates(
    plan: SimulationPlan,
    work: dict[str, list[ClassPlan]],
    salaried: list[str],
) -> list[RatePlan]:
    timetable = plan.timetable
    corrected = {
        timetable.cls(e.cls).final.tutor for e in timetable.closeout_corrections
    }
    cohort_tutors = {c.series.tutor for c in timetable.cohorts}
    paid = [t for t in plan.tutors if t.key not in salaried]
    per_session = [
        t.key
        for t in paid
        if t.key not in corrected
        and t.key not in cohort_tutors
        and t.joins_on <= SIM_START
    ][: SESSION_PRICED[plan.profile]]
    rates: list[RatePlan] = []
    for index, tutor in enumerate(paid):
        start, at = _start(tutor)
        kinds = {c.kind for c in work.get(tutor.key, [])} | {"individual"}
        if tutor.key in cohort_tutors:
            kinds.add("course")
        if tutor.key in per_session:
            rates.append(
                RatePlan(
                    tutor.key,
                    "individual",
                    "per_session",
                    INDIVIDUAL_SESSION,
                    start,
                    at,
                )
            )
        else:
            hourly = INDIVIDUAL_HOURLY[index % len(INDIVIDUAL_HOURLY)]
            rates.append(
                RatePlan(tutor.key, "individual", "per_hour", hourly, start, at)
            )
        if "group" in kinds:
            rates.append(
                RatePlan(tutor.key, "group", "per_hour", GROUP_HOURLY, start, at)
            )
        if "course" in kinds:
            if tutor.key in cohort_tutors:
                rates.append(
                    RatePlan(tutor.key, "course", "per_course", COURSE_RATE, start, at)
                )
            else:
                rates.append(
                    RatePlan(
                        tutor.key, "course", "per_session", COURSE_SESSION, start, at
                    )
                )
    return rates


def _start(tutor: TutorPlan) -> tuple[date, datetime]:
    """When a tutor's pay starts and when Finance records it: at setup from
    the payroll start, or an hour after a later tutor joins."""
    if tutor.joins_on <= SIM_START:
        return PAYROLL_STARTS_ON, SIM_SETUP
    return tutor.joins_on, tutor.joins_at + timedelta(hours=1)


# --- Periods --------------------------------------------------------------------------


def _month_end(day: date) -> date:
    following = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
    return following - timedelta(days=1)


def _periods(tutor: str, frequency: Frequency, start: date) -> list[PeriodPlan]:
    found: list[PeriodPlan] = []
    day = start
    while day <= SIM_ANCHOR:
        end = fortnight_end(day) if frequency == "biweekly" else _month_end(day)
        found.append(_dated(PeriodPlan(tutor, day, end, frequency)))
        day = end + timedelta(days=1)
    return found


def _dated(period: PeriodPlan) -> PeriodPlan:
    if not period.ended:
        return period
    prepare = working_day(period.ends_on + timedelta(days=1))
    finalize = working_day(prepare + timedelta(days=1))
    pay = working_day(finalize + timedelta(days=1))
    return PeriodPlan(
        period.tutor,
        period.starts_on,
        period.ends_on,
        period.frequency,
        local_instant(prepare, PREPARE_TIME),
        local_instant(finalize, FINALIZE_TIME),
        local_instant(pay, PAY_TIME),
    )


# --- The joiner's pay, estimated from the timetable -------------------------------


def estimated_pay(
    plan: SimulationPlan, rates: Iterable[RatePlan], tutor: str, start: date, end: date
) -> Decimal:
    """What the tutor's ordinary session lines of ``[start, end]`` come to
    (``per_hour`` and ``per_session`` rates; the 50 % ``no_show`` and
    ``late`` rules): the size of the joiner's advance."""
    timetable = plan.timetable
    by_kind = {r.class_kind: r for r in rates if r.tutor == tutor}
    total = Decimal(0)
    for c in _work(timetable).get(tutor, []):
        if not start <= c.final.day <= end:
            continue
        rate = by_kind[c.kind]
        if rate.model == "per_course":  # pragma: no cover - not the joiner's
            raise ValueError("the estimate covers per-hour and per-session rates")
        if rate.model == "per_hour":
            ordinary = Decimal(rate.rate) * c.final.minutes / 60
        else:
            ordinary = Decimal(rate.rate)
        absent = bool(c.attendance) and all(m == "absent" for _, m in c.attendance)
        if c.status == "cancelled" or absent:
            ordinary = ordinary * RULE_PERCENT / 100
        total += ordinary.quantize(Decimal("0.01"))
    return total


def _advance(
    plan: SimulationPlan, rates: list[RatePlan], joiner: TutorPlan, texts: TextPool
) -> tuple[ManualPlan, CarryPlan] | None:
    """An advance worth the first period's pay plus a third of the second's,
    rounded up to 50.00: the first period ends negative and the rest is
    carried into the second, which still ends positive."""
    periods = _periods(joiner.key, "biweekly", joiner.joins_on)
    first, second = periods[0], periods[1]
    pay1 = estimated_pay(plan, rates, joiner.key, first.starts_on, first.ends_on)
    pay2 = estimated_pay(plan, rates, joiner.key, second.starts_on, second.ends_on)
    if pay2 <= 0:
        return None
    raw = pay1 + pay2 / 3
    amount = (raw / 50).to_integral_value(rounding="ROUND_CEILING") * 50
    if amount <= pay1:
        amount += 50
    manual = ManualPlan(
        "advance",
        joiner.key,
        -Decimal(amount),
        texts.mapping("payroll_manual_reasons")["advance"],
        local_instant(joiner.joins_on + ADVANCE_DELAY, "11:00"),
    )
    carry = CarryPlan(
        joiner.key,
        first.starts_on,
        local_instant(working_day(first.ends_on + timedelta(days=1)), CARRY_TIME),
        pay1,
    )
    return manual, carry


# --- Work after lock -----------------------------------------------------------------


def _adjustment_days(
    plan: SimulationPlan, periods: list[PeriodPlan]
) -> tuple[date, ...]:
    timetable = plan.timetable
    requests = {r.key: r for r in timetable.change_requests}
    days: set[date] = set()
    for event in timetable.events:
        if isinstance(event, ReviewCloseout) and event.approve:
            key = event.cls
        elif isinstance(event, CorrectCloseout | CorrectAttendance):
            key = event.cls
        elif isinstance(event, DecideChange) and event.approve:
            request = requests[event.request]
            if request.classification != "late":
                continue
            key = request.cls
        else:
            continue
        c = timetable.cls(key)
        tutor = c.final.tutor
        if tutor is None:
            continue
        period = next(
            (
                p
                for p in periods
                if p.tutor == tutor and p.starts_on <= c.final.day <= p.ends_on
            ),
            None,
        )
        if period is None or period.finalize_at is None:
            continue
        if period.finalize_at < event.at < local_instant(SIM_ANCHOR, "00:00"):
            days.add(event.at.astimezone(ZONE).date())
    return tuple(sorted(days))


# --- The plan -------------------------------------------------------------------------


def build_payroll(plan: SimulationPlan) -> Payroll:
    """The payroll plan for ``plan`` (pure; module docstring)."""
    texts = TextPool(plan.language)
    timetable = plan.timetable
    work = _work(timetable)
    salaried = _salaried(plan, work)
    joiners = [t for t in plan.tutors if t.joins_on > SIM_START]
    joiner = joiners[0] if joiners else None
    terms: list[TermsPlan] = []
    periods: list[PeriodPlan] = []
    for tutor in plan.tutors:
        start, at = _start(tutor)
        if tutor.key in salaried:
            salary = SALARIES[salaried.index(tutor.key)]
            terms.append(TermsPlan(tutor.key, MONTHLY_FROM, "monthly", salary, at))
            periods += _periods(tutor.key, "monthly", MONTHLY_FROM)
        else:
            terms.append(TermsPlan(tutor.key, start, "biweekly", None, at))
            periods += _periods(tutor.key, "biweekly", start)
    rates = _rates(plan, work, salaried)
    paid = [
        t.key
        for t in plan.tutors
        if t.key not in salaried and (joiner is None or t.key != joiner.key)
    ]
    group_tutors = [k for k in paid if any(c.kind == "group" for c in work.get(k, []))]
    bonus_tutors = paid[: BONUSES[plan.profile]]
    deduction_tutor = paid[len(bonus_tutors)]
    names = texts.mapping("pay_item_descriptions")
    items = [
        ItemPlan(
            tutor,
            "bonus",
            names["bonus"],
            BONUS_AMOUNT,
            "recurring",
            BONUS_FROM,
            BONUS_AT,
        )
        for tutor in bonus_tutors
    ]
    items.append(
        ItemPlan(
            deduction_tutor,
            "deduction",
            names["deduction"],
            DEDUCTION_AMOUNT,
            "one_time",
            DEDUCTION_ON,
            DEDUCTION_AT,
        )
    )
    reasons = texts.mapping("payroll_manual_reasons")
    manuals = [
        ManualPlan(
            "event",
            (group_tutors or paid)[0],
            Decimal(EVENT_AMOUNT),
            reasons["event"],
            EVENT_AT,
        )
    ]
    carry = None
    if joiner is not None:
        advance = _advance(plan, rates, joiner, texts)
        if advance is not None:
            manuals.append(advance[0])
            carry = advance[1]
    reversed_period = [p for p in periods if p.tutor == paid[0] and p.ended][2]
    assert reversed_period.pay_at is not None  # noqa: S101 - an ended period
    reverse_day = working_day(reversed_period.pay_at.date() + timedelta(days=1))
    reversal = ReversalPlan(
        paid[0],
        reversed_period.starts_on,
        reversed_period.pay_at,
        local_instant(reverse_day, REVERSE_TIME),
        local_instant(reverse_day, REPAY_TIME),
        texts.choices("payroll_reversal_reasons")[0],
    )
    open_days = sorted(
        {p.starts_on for p in periods if p.starts_on >= SIM_START}
        | {SIM_START}
        | ({joiner.joins_on + ADVANCE_DELAY} if joiner is not None else set())
    )
    return Payroll(
        salaried=tuple(salaried),
        joiner=None if joiner is None else joiner.key,
        terms=tuple(terms),
        rates=tuple(rates),
        item_types=dict(texts.mapping("pay_item_types")),
        items=tuple(items),
        periods=tuple(sorted(periods, key=lambda p: (p.tutor, p.starts_on))),
        manuals=tuple(sorted(manuals, key=lambda m: m.at)),
        carry=carry,
        reversal=reversal,
        open_days=tuple(d for d in open_days if d < SIM_ANCHOR),
        adjustment_days=_adjustment_days(plan, periods),
    )
