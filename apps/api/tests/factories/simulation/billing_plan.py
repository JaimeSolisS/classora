"""The simulation's billing plan (#552 HF-01d): pure, built by
:func:`build_billing` from the people plan and HF-01b's timetable, so the
same arguments give the same plan on any day (no clock, no database). Its
own RNG stream (``Random(seed + RNG_OFFSET)``) leaves the earlier plans
unchanged.

**Setup (Finance, at SIM_SETUP).** Billing starts on 1 Sep. Catalog prices:
one individual ``per_session`` item per subject and class length the
timetable uses (amounts by level), a monthly ``per_period`` item per
subject the tuition plans pin, a flat fee per class group and per course;
one price change effective 1 Oct (a second version). Discounts: the sibling
discount, two scholarships, a September-only promotion on packages and an
upcoming one from 1 Dec. Four package products.

**Billing modes (A2).** Each student has one mode at a time, changing only
on the 1st of a month:

- ``plan`` (about half): a monthly tuition plan; their individual classes
  are covered by a ``0.00`` custom price per item (the services post a
  ``void`` charge for each), so only the monthly charge is paid. One plan is
  paused for October.
- ``package`` (about a fifth): a 8- or 12-class package (any subject,
  valid 120 days, so it covers every class through the horizon) bought on
  31 Aug and renewed when it is used up; one student adds an intensive
  10-class package (30 days) on 1 Oct that lapses with units left, one
  buys a 4-class package for a subject they never start (5 Oct) and
  withdraws it on 23 Oct (account credit), and one cancels their package on
  31 Oct (refund) and moves to a plan from 1 Nov.
- ``per_class`` (the rest, and the students who join after 1 Sep): every
  class charged at the catalog price (two students with a custom price).

**Money in.** Each family pays on its own days, through the payment
service, every open charge due up to a cutoff (the module reads the
amounts): weekly payers on Mondays (the coming week's classes), monthly
payers between the 1st and the 7th (the whole month), two families every
other Monday (late by up to 13 days), three [one] monthly families stop
paying on 1 Nov (overdue at the anchor, with reminders: each has a
guardian login), two [one] pay ahead and keep account credit. One transfer
bounces (reversed two days later by the Owner) and is paid again in cash
the next day. Package purchases are paid at once. Nothing is paid on or
after the anchor.

**Corrections.** Two manual charges (materials, a practice exam fee), one
manual adjustment, two due-date changes, the package cancellation with its
refund and the package withdrawal.
"""

import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_SETUP_DAY,
    SIM_START,
    local_instant,
)
from tests.factories.simulation.plan import (
    CLOSURES,
    ELEMENTARY,
    HIGH_SCHOOL,
    MIDDLE_SCHOOL,
    UNIVERSITY,
    SimulationPlan,
)
from tests.factories.simulation.texts import TextPool
from tests.factories.simulation.timetable import FINISHED_COURSE, ClassPlan

RNG_OFFSET: Final = 552_000

type Mode = Literal["plan", "package", "per_class"]
type Method = Literal["cash", "transfer", "other"]

BILLING_STARTS_ON: Final = SIM_START
PRICE_CHANGE_ON: Final = date(2026, 10, 1)
UPCOMING_FROM: Final = date(2026, 12, 1)
PROMOTION: Final = (date(2026, 9, 1), date(2026, 9, 30))
SETUP_DAY: Final = SIM_SETUP_DAY
OPENING_DAY: Final = date(2026, 8, 31)
"""The plans and the first packages are set up the day before classes."""
OPENING_AT: Final = local_instant(OPENING_DAY, "18:00")
TUITION_DUE_OFFSET: Final = 9
"""A monthly charge is due on the 10th (period start + 9 days)."""
CANCELLED_ON: Final = date(2026, 10, 31)
MODE_CHANGE_ON: Final = date(2026, 11, 1)
PLAN_CHANGE_AT: Final = local_instant(date(2026, 10, 30), "12:00")
INTENSIVE_AT: Final = local_instant(date(2026, 10, 1), "12:00")
EXTRA_AT: Final = local_instant(date(2026, 10, 5), "12:00")
WITHDRAWN_AT: Final = local_instant(date(2026, 10, 23), "12:00")
CANCEL_AT: Final = local_instant(CANCELLED_ON, "14:30")
PAUSE_AT: Final = local_instant(date(2026, 9, 25), "12:00")
PAUSE: Final = (date(2026, 10, 1), date(2026, 10, 31))
OVERDUE_FROM: Final = date(2026, 11, 1)
"""An overdue monthly family pays nothing from this day (November's
tuition stays open)."""
OVERDUE_WEEKLY_FROM: Final = date(2026, 11, 9)
"""An overdue weekly family (the ``small`` fallback) stops a week later."""
LAST_PAY_DAY: Final = SIM_ANCHOR - timedelta(days=1)

PER_SESSION: Final[dict[str, int]] = {
    ELEMENTARY: 280,
    MIDDLE_SCHOOL: 300,
    HIGH_SCHOOL: 330,
    UNIVERSITY: 380,
}
"""The price of a 60-minute individual class by level (MXN); other lengths
pro rata, rounded to tens."""
MONTHLY: Final[dict[str, int]] = {
    ELEMENTARY: 1050,
    MIDDLE_SCHOOL: 1150,
    HIGH_SCHOOL: 1250,
    UNIVERSITY: 1450,
}
PRICE_CHANGE_STEP: Final = 30
GROUP_FEE: Final = 2400
COURSE_FEES: Final[tuple[int, ...]] = (3200, 2800)
CUSTOM_RATE: Final = Decimal("0.9")
MANUAL_AMOUNTS: Final[dict[str, int]] = {"materials": 350, "exam": 450}
ADJUSTMENT: Final = -50


@dataclass(frozen=True)
class ItemPlan:
    """One catalog item and its first version (from :data:`SETUP_DAY`)."""

    key: str
    class_type: Literal["individual", "group", "course"]
    pricing_model: Literal["per_session", "per_period", "full_course"]
    subject: str
    amount: int
    minutes: int | None = None
    group: str | None = None
    frequency: Literal["monthly"] | None = None


@dataclass(frozen=True)
class VersionPlan:
    item: str
    effective_from: date
    amount: int


@dataclass(frozen=True)
class CustomPricePlan:
    student: str
    item: str
    amount: int
    effective_from: date
    at: datetime


@dataclass(frozen=True)
class DiscountPlan:
    key: str
    kind: Literal["sibling", "scholarship", "promotion", "discount"]
    name: str
    percent: str
    charge_kinds: tuple[str, ...]
    starts_on: date | None = None
    expires_on: date | None = None
    students: tuple[str, ...] = ()

    @property
    def audience(self) -> str:
        return "assigned" if self.kind == "scholarship" else "all"


@dataclass(frozen=True)
class ProductPlan:
    key: str
    name: str
    quantity: int
    validity_days: int
    amount: int
    subject: str | None = None


@dataclass(frozen=True)
class TuitionPlanPlan:
    key: str
    student: str
    item: str
    starts_on: date
    at: datetime
    pause: tuple[date, date] | None = None


@dataclass(frozen=True)
class PackagePlan:
    """A package-mode student: the product bought on 31 Aug and renewed
    when it is used up, and the dated extras."""

    student: str
    product: str
    intensive: bool = False
    extra: str | None = None
    """A product for a subject the student never takes (withdrawn)."""
    cancelled: bool = False


@dataclass(frozen=True)
class PaymentPlan:
    """One payment day of a family: every open charge due up to ``cutoff``
    (``ahead``: rounded up and a month more, kept as account credit)."""

    family: str
    at: datetime
    method: Method
    cutoff: date
    ahead: bool = False


@dataclass(frozen=True)
class ManualChargePlan:
    key: str
    family: str
    student: str
    description: str
    amount: int
    service_date: date
    due_date: date
    at: datetime


@dataclass(frozen=True)
class AdjustmentPlan:
    charge: str
    amount: int
    reason: str
    at: datetime


@dataclass(frozen=True)
class DueDateChangePlan:
    family: str
    plan: str
    period_start: date
    due_date: date
    reason: str
    at: datetime


@dataclass(frozen=True)
class ReversalPlan:
    family: str
    payment_at: datetime
    at: datetime
    reason: str


@dataclass(frozen=True)
class Billing:
    items: tuple[ItemPlan, ...]
    versions: tuple[VersionPlan, ...]
    discounts: tuple[DiscountPlan, ...]
    products: tuple[ProductPlan, ...]
    custom_prices: tuple[CustomPricePlan, ...]
    modes: dict[str, tuple[tuple[date, Mode], ...]]
    """Each student's modes from the date they start (A2)."""
    tuition_plans: tuple[TuitionPlanPlan, ...]
    packages: tuple[PackagePlan, ...]
    payments: tuple[PaymentPlan, ...]
    manual_charges: tuple[ManualChargePlan, ...]
    adjustments: tuple[AdjustmentPlan, ...]
    due_date_changes: tuple[DueDateChangePlan, ...]
    reversal: ReversalPlan
    overdue_families: tuple[str, ...]
    ahead_families: tuple[str, ...]
    late_families: tuple[str, ...]
    reasons: dict[str, str]
    """The pool reasons of the cancellation, refund and withdrawal."""

    def mode_on(self, student: str, day: date) -> Mode:
        current: Mode = "per_class"
        for start, mode in self.modes[student]:
            if start <= day:
                current = mode
        return current

    def package(self, student: str) -> PackagePlan | None:
        return next((p for p in self.packages if p.student == student), None)


# --- Helpers --------------------------------------------------------------------------


def session_amount(level: str, minutes: int) -> int:
    """The catalog price of a class of ``minutes`` at ``level`` (tens)."""
    raw = Decimal(PER_SESSION[level] * minutes) / 60 / 10
    return int(raw.quantize(Decimal(1), rounding=ROUND_HALF_UP)) * 10


def custom_amount(amount: int) -> int:
    raw = Decimal(amount) * CUSTOM_RATE / 10
    return int(raw.quantize(Decimal(1), rounding=ROUND_HALF_UP)) * 10


def item_key(subject: str, minutes: int) -> str:
    return f"{subject}:{minutes}"


def month_starts() -> tuple[date, ...]:
    return (date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1))


def month_end(start: date) -> date:
    following = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return following - timedelta(days=1)


CLOSED: Final = frozenset(day for _, day in CLOSURES)


def open_day(day: date) -> date:
    """``day`` or the next day the center opens (not Sunday, no closure)."""
    while day.isoweekday() == 7 or day in CLOSED:
        day += timedelta(days=1)
    return day


def mondays() -> Iterable[date]:
    day = date(2026, 9, 7)
    while day < SIM_ANCHOR:
        yield day
        day += timedelta(days=7)


# --- The builder ----------------------------------------------------------------------


def _individual(plan: SimulationPlan) -> dict[str, list[ClassPlan]]:
    found: dict[str, list[ClassPlan]] = defaultdict(list)
    for c in plan.timetable.classes:
        if c.kind == "individual":
            found[c.students[0]].append(c)
    return found


def build_billing(plan: SimulationPlan, seed: int) -> Billing:
    """The billing plan for ``plan`` (pure; module docstring)."""
    # Deterministic synthetic data, not security: a seeded generator.
    rng = random.Random(seed + RNG_OFFSET)  # noqa: S311
    texts = TextPool(plan.language)
    timetable = plan.timetable
    levels = {s.key: s.level for s in plan.subjects}
    families = {f.key: f for f in plan.families}
    family_of = {s.key: f.key for f in plan.families for s in f.students}
    classes = _individual(plan)
    series_count: dict[str, int] = defaultdict(int)
    for series in timetable.series:
        if series.kind == "individual":
            series_count[series.students[0]] += 1

    # Items: every individual (subject, length) the timetable uses.
    pairs = sorted(
        {(c.subject, c.final.minutes) for cs in classes.values() for c in cs}
        | {(c.subject, c.placed.minutes) for cs in classes.values() for c in cs}
    )
    items: list[ItemPlan] = [
        ItemPlan(
            item_key(subject, minutes),
            "individual",
            "per_session",
            subject,
            session_amount(levels[subject], minutes),
            minutes=minutes,
        )
        for subject, minutes in pairs
    ]
    for group in timetable.groups:
        items.append(
            ItemPlan(
                f"group:{group.key}",
                "group",
                "full_course",
                group.series.subject,
                GROUP_FEE,
                group=group.key,
            )
        )
    for course, fee in zip(timetable.courses, COURSE_FEES, strict=False):
        items.append(
            ItemPlan(
                f"course:{course.key}", "course", "full_course", course.subject, fee
            )
        )

    # Modes (A2).
    resized = timetable.cls(timetable.resized).students[0]
    regular = [
        s.key
        for s in plan.students
        if families[family_of[s.key]].enrolls_at.date() < SIM_START and classes[s.key]
    ]
    shuffled = sorted(regular)
    rng.shuffle(shuffled)
    total = len(plan.students)
    n_package = round(total * 0.2)
    n_plan = round(total * 0.5)
    pool = [s for s in shuffled if s != resized]
    package_students = sorted(
        (s for s in pool if series_count[s] in (1, 2)),
        key=lambda s: (series_count[s] != 1, pool.index(s)),
    )[:n_package]
    rest = [s for s in pool if s not in package_students]
    plan_students = rest[:n_plan]
    modes: dict[str, tuple[tuple[date, Mode], ...]] = {}
    for s in plan.students:
        mode: Mode = (
            "package"
            if s.key in package_students
            else "plan"
            if s.key in plan_students
            else "per_class"
        )
        modes[s.key] = ((SIM_START, mode),)

    # Packages: [0] intensive top-up, [1] cancelled → plan, [2 or 0] extra.
    singles = [s for s in package_students if series_count[s] == 1]
    intensive = singles[0]
    cancelled = singles[1] if len(singles) > 1 else package_students[1]
    extra_student = next(
        (s for s in package_students if s not in (intensive, cancelled)), intensive
    )
    taken = {c.subject for c in classes[extra_student]}
    extra_level = levels[classes[extra_student][0].subject]
    extra_subject = next(
        (s.key for s in plan.subjects if s.level == extra_level and s.key not in taken),
        next(s.key for s in plan.subjects if s.key not in taken),
    )
    names = texts.mapping("billing_products")
    subject_names = texts.mapping("subjects")
    products = (
        ProductPlan("pack8", names["pack8"], 8, 120, 2240),
        ProductPlan("pack12", names["pack12"], 12, 120, 3240),
        ProductPlan("intensive", names["intensive"], 10, 30, 2600),
        ProductPlan(
            "subject4",
            names["subject4"].format(subject=subject_names[extra_subject]),
            4,
            60,
            1200,
            subject=extra_subject,
        ),
    )
    packages = tuple(
        PackagePlan(
            student=s,
            product="pack12" if series_count[s] == 2 or index % 3 == 2 else "pack8",
            intensive=s == intensive,
            extra="subject4" if s == extra_student else None,
            cancelled=s == cancelled,
        )
        for index, s in enumerate(package_students)
    )
    modes[cancelled] = ((SIM_START, "package"), (MODE_CHANGE_ON, "plan"))

    # Tuition plans: each plan student's first subject's monthly item.
    def monthly_item(student: str) -> str:
        subject = sorted(classes[student], key=lambda c: (c.placed.starts_at, c.key))[
            0
        ].subject
        key = f"monthly:{subject}"
        if all(i.key != key for i in items):
            items.append(
                ItemPlan(
                    key,
                    "individual",
                    "per_period",
                    subject,
                    MONTHLY[levels[subject]],
                    frequency="monthly",
                )
            )
        return key

    plan_families = sorted({family_of[s] for s in plan_students})
    monthly_families = [
        f
        for f in plan_families
        if all(modes[s.key][0][1] == "plan" for s in families[f].students)
    ]
    with_login = [
        f
        for f in monthly_families
        if any(g.account is not None for g in families[f].guardians)
    ]
    n_overdue = 3 if plan.profile == "full" else 1
    n_ahead = 2 if plan.profile == "full" else 1
    package_families = {family_of[s] for s in package_students}
    fallback = [
        f.key
        for f in plan.families
        if f.key not in with_login
        and f.key not in package_families
        and any(g.account is not None for g in f.guardians)
    ]
    overdue = tuple((with_login + fallback)[:n_overdue])
    ahead = tuple([f for f in monthly_families if f not in overdue][:n_ahead])
    others = [s for s in plan_students if family_of[s] not in overdue + ahead]
    paused = others[0]
    due_changed = [s for s in others[1:] if family_of[s] != family_of[paused]][:2]

    tuition: list[TuitionPlanPlan] = []
    for s in sorted(plan_students):
        tuition.append(
            TuitionPlanPlan(
                f"tp:{s}",
                s,
                monthly_item(s),
                SIM_START,
                OPENING_AT,
                pause=PAUSE if s == paused else None,
            )
        )
    tuition.append(
        TuitionPlanPlan(
            f"tp:{cancelled}",
            cancelled,
            monthly_item(cancelled),
            MODE_CHANGE_ON,
            PLAN_CHANGE_AT,
        )
    )

    # Custom prices: 0.00 on a plan student's items; 10 % off for two
    # per-class students.
    by_key = {i.key: i for i in items}
    custom: list[CustomPricePlan] = []
    for t in tuition:
        used = sorted(
            {
                item_key(c.subject, m)
                for c in classes[t.student]
                for m in (c.placed.minutes, c.final.minutes)
            }
        )
        setup_at = (
            local_instant(SETUP_DAY, "11:00") if t.starts_on == SIM_START else t.at
        )
        custom.extend(
            CustomPricePlan(t.student, key, 0, t.starts_on, setup_at) for key in used
        )
    per_class = sorted(
        s for s in regular if modes[s][0][1] == "per_class" and s != resized
    )
    for s in per_class[:2]:
        for key in sorted({item_key(c.subject, c.placed.minutes) for c in classes[s]}):
            custom.append(
                CustomPricePlan(
                    s,
                    key,
                    custom_amount(by_key[key].amount),
                    SIM_START,
                    local_instant(SETUP_DAY, "11:00"),
                )
            )

    # The price change: the per-session item with most per-class classes
    # from 1 Oct.
    counts: dict[str, int] = defaultdict(int)
    for s in plan.students:
        if modes[s.key][0][1] == "per_class":
            for c in classes[s.key]:
                if c.placed.day >= PRICE_CHANGE_ON:
                    counts[item_key(c.subject, c.placed.minutes)] += 1
    changed = max(sorted(counts), key=lambda k: counts[k])
    versions = (
        VersionPlan(
            changed, PRICE_CHANGE_ON, by_key[changed].amount + PRICE_CHANGE_STEP
        ),
    )

    discount_names = texts.mapping("billing_discounts")
    scholars = tuple(
        sorted(
            s
            for s in plan_students
            if s != paused and family_of[s] not in overdue + ahead
        )[:2]
    )
    discounts = (
        DiscountPlan(
            "sibling",
            "sibling",
            discount_names["sibling"],
            "10",
            ("session", "tuition_period"),
        ),
        DiscountPlan(
            "scholarship_a",
            "scholarship",
            discount_names["scholarship_a"],
            "30",
            ("tuition_period",),
            students=scholars[:1],
        ),
        DiscountPlan(
            "scholarship_b",
            "scholarship",
            discount_names["scholarship_b"],
            "50",
            ("tuition_period",),
            students=scholars[1:2],
        ),
        DiscountPlan(
            "promotion",
            "promotion",
            discount_names["promotion"],
            "15",
            ("package_purchase",),
            starts_on=PROMOTION[0],
            expires_on=PROMOTION[1],
        ),
        DiscountPlan(
            "upcoming",
            "discount",
            discount_names["upcoming"],
            "5",
            ("tuition_period",),
            starts_on=UPCOMING_FROM,
        ),
    )

    # Payments.
    def method() -> Method:
        return rng.choice(("cash", "cash", "transfer", "transfer", "other"))  # type: ignore[return-value]

    def at(day: date) -> datetime:
        return local_instant(
            day, f"{rng.choice((10, 11, 12, 13))}:{rng.choice(('00', '30'))}"
        )

    weekly_families = [f.key for f in plan.families if f.key not in monthly_families]
    late = tuple(
        sorted(
            (
                f
                for f in weekly_families
                if all(modes[s.key][0][1] == "per_class" for s in families[f].students)
            ),
            key=lambda f: f,
        )[-(2 if plan.profile == "full" else 1) :]
    )
    reversal_family = next(
        f
        for f in weekly_families
        if f not in late
        and f not in overdue
        and any(modes[s.key][0][1] == "per_class" for s in families[f].students)
    )
    payments: list[PaymentPlan] = []
    reversal: ReversalPlan | None = None
    due_change_families = {family_of[s] for s in due_changed}
    for f in plan.families:
        how = method()
        start = max(f.enrolls_at.date(), SIM_START)
        if f.key in monthly_families:
            for first in month_starts():
                if f.key in overdue and first >= OVERDUE_FROM:
                    continue
                day = open_day(first + timedelta(days=rng.randint(0, 6)))
                if f.key in due_change_families and first.month == 10:
                    day = date(2026, 10, 16)
                if day < start or day > LAST_PAY_DAY:
                    continue
                payments.append(
                    PaymentPlan(
                        f.key, at(day), how, month_end(first), ahead=f.key in ahead
                    )
                )
            continue
        days = [open_day(SIM_START), *(open_day(m) for m in mondays())]
        if f.key in late:
            days = days[::2]
        for day in days:
            if day < start or day > LAST_PAY_DAY:
                continue
            if f.key in overdue and day >= OVERDUE_WEEKLY_FROM:
                continue
            cutoff = day + timedelta(days=13 if f.key in late else 6)
            if (
                f.key == reversal_family
                and reversal is None
                and day >= date(2026, 10, 12)
            ):
                paid_at = local_instant(day, "11:00")
                payments.append(PaymentPlan(f.key, paid_at, "transfer", cutoff))
                bounced = local_instant(day + timedelta(days=2), "10:00")
                reversal = ReversalPlan(
                    f.key, paid_at, bounced, texts.choices("reversal_reasons")[0]
                )
                again = open_day(day + timedelta(days=3))
                payments.append(
                    PaymentPlan(f.key, local_instant(again, "12:30"), "cash", cutoff)
                )
                continue
            payments.append(PaymentPlan(f.key, at(day), how, cutoff))
    assert reversal is not None  # noqa: S101 - every profile has weekly payers
    payments.sort(key=lambda p: (p.at, p.family))

    # Corrections.
    charges = texts.mapping("manual_charges")
    materials_student = per_class[0]
    finished = next(c for c in timetable.cohorts if c.course == FINISHED_COURSE)
    exam_student = sorted(student for student, _ in finished.enrollments)[0]
    manual = (
        ManualChargePlan(
            "materials",
            family_of[materials_student],
            materials_student,
            charges["materials"],
            MANUAL_AMOUNTS["materials"],
            date(2026, 9, 14),
            date(2026, 9, 21),
            local_instant(date(2026, 9, 14), "12:00"),
        ),
        ManualChargePlan(
            "exam",
            family_of[exam_student],
            exam_student,
            charges["exam"],
            MANUAL_AMOUNTS["exam"],
            date(2026, 10, 19),
            date(2026, 10, 26),
            local_instant(date(2026, 10, 19), "12:00"),
        ),
    )
    adjustments = (
        AdjustmentPlan(
            "materials",
            ADJUSTMENT,
            texts.choices("adjustment_reasons")[0],
            local_instant(date(2026, 9, 17), "11:00"),
        ),
    )
    due_reasons = texts.choices("due_date_reasons")
    due_changes = tuple(
        DueDateChangePlan(
            family_of[s],
            f"tp:{s}",
            date(2026, 10, 1),
            date(2026, 10, 20),
            due_reasons[i % len(due_reasons)],
            local_instant(date(2026, 10, 6), "12:00"),
        )
        for i, s in enumerate(due_changed)
    )
    return Billing(
        items=tuple(items),
        versions=versions,
        discounts=discounts,
        products=products,
        custom_prices=tuple(custom),
        modes=modes,
        tuition_plans=tuple(tuition),
        packages=packages,
        payments=tuple(payments),
        manual_charges=manual,
        adjustments=adjustments,
        due_date_changes=due_changes,
        reversal=reversal,
        overdue_families=overdue,
        ahead_families=ahead,
        late_families=late,
        reasons={
            "cancellation": texts.choices("package_cancellation_reasons")[0],
            "refund": texts.choices("refund_reasons")[0],
            "withdrawal": texts.choices("withdrawal_reasons")[0],
        },
    )
