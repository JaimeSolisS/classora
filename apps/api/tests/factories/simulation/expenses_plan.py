"""The simulation's operating expenses (#553 HF-01e): pure, built by
:func:`build_expenses` (no clock, no database, no RNG); the same for both
profiles, in the run's language.

**Setup (Finance, at SIM_SETUP).** A sixth category, teaching materials
(the five seeded ones are renamed in English by HF-01, which clears their
system key, so every category is found by its name), nine vendors and five
expense templates, each ending on :data:`~.anchor.SIM_HORIZON` (E9): the
monthly rent, the monthly electricity and water, the weekly cleaning (paused
from 16 Oct to 30 Oct, so the weeks of 19 and 26 Oct have none), a monthly
software subscription and the annual liability insurance (one period, in
September).

**History.** The recurring-expense job step generates each period on its
first day (01:55, the job's daily tick). Finance pays every generated
expense on its due date (the working day before when that is a weekend or
a closure, the working day after when that would be before the expense),
and records 13 one-off expenses on the day they are incurred, each paid on
its due date. October's electricity bill comes in 235.00 higher (an
adjustment); the projector gets its shipping added (an adjustment), and its
transfer is reversed by the Owner and paid again; one water purchase is
captured twice and the duplicate voided. The furniture vendor, used once,
is deactivated on 31 Oct. Nothing is dated on or after the anchor: every
expense incurred before it is paid or voided.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal

from app.modules.finance.service import Frequency, next_after
from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_START,
    local_instant,
)
from tests.factories.simulation.payroll_plan import CLOSED, working_day
from tests.factories.simulation.plan import SimulationPlan
from tests.factories.simulation.texts import TextPool

type CategoryKey = Literal[
    "rent", "utilities", "supplies", "equipment", "maintenance", "teaching_materials"
]
type Method = Literal["cash", "transfer"]

TEACHING_MATERIALS: Final = "teaching_materials"
VENDOR_KEYS: Final = (
    "landlord",
    "power",
    "internet",
    "cleaning",
    "software",
    "insurer",
    "stationery",
    "hardware",
    "furniture",
)
DEACTIVATED_VENDOR: Final = "furniture"
DEACTIVATED_AT: Final = local_instant(date(2026, 10, 31), "18:00")
PAUSE_AT: Final = local_instant(date(2026, 10, 16), "18:00")
RESUME_AT: Final = local_instant(date(2026, 10, 30), "18:00")
JOB_TIME: Final = "01:55"
"""The ``finance:generate_recurring_expenses`` job's daily tick (07:55
UTC)."""
PAY_TIME: Final = "12:00"
RECORD_TIME: Final = "09:30"
POWER_ADJUSTED: Final = date(2026, 10, 10)
POWER_ADJUSTMENT: Final = 235


def money(amount: int | Decimal) -> str:
    return f"{Decimal(amount):.2f}"


def open_day_before(day: date) -> date:
    while day.isoweekday() > 5 or day in CLOSED:
        day -= timedelta(days=1)
    return day


def pay_day(incurred_on: date, due_on: date) -> date:
    """The due date, or the working day before it, never before the
    expense (then the working day after)."""
    day = open_day_before(due_on)
    return day if day >= incurred_on else working_day(incurred_on)


@dataclass(frozen=True)
class VendorPlan:
    key: str
    name: str
    contact: str


@dataclass(frozen=True)
class Pay:
    amount: Decimal
    at: datetime
    method: Method
    reference: str | None = None


@dataclass(frozen=True)
class Reverse:
    """The Owner reverses the expense's ``index``-th payment."""

    index: int
    at: datetime
    reason: str


@dataclass(frozen=True)
class Adjust:
    amount: Decimal
    at: datetime
    reason: str


type MoneyStep = Pay | Reverse | Adjust


@dataclass(frozen=True)
class TemplatePlan:
    key: str
    name: str
    category: CategoryKey
    vendor: str
    amount: int
    frequency: Frequency
    starts_on: date
    due_offset_days: int
    method: Method
    paused: bool = False

    def periods(self) -> tuple[date, ...]:
        """The periods the job generates before the anchor (not those
        starting while it is paused)."""
        found: list[date] = []
        start = self.starts_on
        while start < SIM_ANCHOR and start <= SIM_HORIZON:
            if not (
                self.paused
                and PAUSE_AT.date() < start <= RESUME_AT.date() + timedelta(days=1)
            ):
                found.append(start)
            start = next_after(self.starts_on, self.frequency, start)
        return tuple(found)

    @property
    def ends_on(self) -> date:
        return SIM_HORIZON


@dataclass(frozen=True)
class ExpensePlan:
    """A one-off expense, or (``template``) a generated one's money steps."""

    key: str
    description: str
    category: CategoryKey
    vendor: str | None
    amount: Decimal
    incurred_on: date
    due_on: date
    steps: tuple[MoneyStep, ...]
    template: str | None = None
    receipt_reference: str | None = None

    @property
    def recorded_at(self) -> datetime:
        return local_instant(self.incurred_on, RECORD_TIME)

    @property
    def net(self) -> Decimal:
        return self.amount + sum(
            (s.amount for s in self.steps if isinstance(s, Adjust)), Decimal(0)
        )

    @property
    def voided(self) -> bool:
        return self.net == 0


@dataclass(frozen=True)
class Expenses:
    category_name: str
    """The sixth category's name."""
    vendors: tuple[VendorPlan, ...]
    templates: tuple[TemplatePlan, ...]
    generated: tuple[ExpensePlan, ...]
    """The expenses the job generates, with Finance's money steps."""
    one_offs: tuple[ExpensePlan, ...]

    def template(self, key: str) -> TemplatePlan:
        return next(t for t in self.templates if t.key == key)

    @property
    def expenses(self) -> tuple[ExpensePlan, ...]:
        return (*self.generated, *self.one_offs)


def _templates(texts: TextPool) -> tuple[TemplatePlan, ...]:
    names = texts.mapping("expense_templates")
    return (
        TemplatePlan(
            "rent", names["rent"], "rent", "landlord", 9500, "monthly", SIM_START, 4,
            "transfer",
        ),
        TemplatePlan(
            "power", names["power"], "utilities", "power", 1450, "monthly",
            date(2026, 9, 10), 8, "transfer",
        ),
        TemplatePlan(
            "cleaning", names["cleaning"], "maintenance", "cleaning", 650, "weekly",
            date(2026, 9, 7), 0, "cash", paused=True,
        ),
        TemplatePlan(
            "software", names["software"], "utilities", "software", 890, "monthly",
            date(2026, 9, 5), 0, "transfer",
        ),
        TemplatePlan(
            "insurance", names["insurance"], "utilities", "insurer", 6800, "annual",
            date(2026, 9, 15), 15, "transfer",
        ),
    )  # fmt: skip


def _reference(prefix: str, day: date) -> str:
    return f"{prefix}-{day.strftime('%y%m%d')}"


def _generated(
    templates: tuple[TemplatePlan, ...], texts: TextPool
) -> tuple[ExpensePlan, ...]:
    found: list[ExpensePlan] = []
    for template in templates:
        for period in template.periods():
            due = period + timedelta(days=template.due_offset_days)
            amount = Decimal(template.amount)
            steps: list[MoneyStep] = []
            if template.key == "power" and period == POWER_ADJUSTED:
                steps.append(
                    Adjust(
                        Decimal(POWER_ADJUSTMENT),
                        local_instant(working_day(period), "10:00"),
                        texts.mapping("expense_adjustment_reasons")["power"],
                    )
                )
                amount += POWER_ADJUSTMENT
            day = pay_day(period, due)
            steps.append(
                Pay(
                    amount,
                    local_instant(day, PAY_TIME),
                    template.method,
                    None
                    if template.method == "cash"
                    else _reference(template.key[:3].upper(), day),
                )
            )
            found.append(
                ExpensePlan(
                    f"{template.key}:{period.isoformat()}",
                    template.name,
                    template.category,
                    template.vendor,
                    Decimal(template.amount),
                    period,
                    due,
                    tuple(steps),
                    template=template.key,
                )
            )
    return tuple(found)


def _one_off(
    key: str,
    text: str,
    category: CategoryKey,
    vendor: str | None,
    amount: int,
    incurred_on: date,
    due_on: date,
    method: Method,
    texts: TextPool,
    receipt: str | None = None,
    extra: tuple[MoneyStep, ...] = (),
    paid: bool = True,
) -> ExpensePlan:
    descriptions = texts.mapping("expense_descriptions")
    adjusted = Decimal(amount) + sum(
        (s.amount for s in extra if isinstance(s, Adjust)), Decimal(0)
    )
    day = pay_day(incurred_on, due_on)
    pay = Pay(
        adjusted,
        local_instant(day, PAY_TIME),
        method,
        None if method == "cash" else _reference("GTO", day) + f"-{key[:3].upper()}",
    )
    return ExpensePlan(
        key,
        descriptions[text],
        category,
        vendor,
        Decimal(amount),
        incurred_on,
        due_on,
        (*extra, pay) if paid else extra,
        receipt_reference=receipt,
    )


def _one_offs(texts: TextPool) -> tuple[ExpensePlan, ...]:
    reasons = texts.mapping("expense_adjustment_reasons")
    d = date
    projector = _one_off(
        "projector",
        "projector",
        "equipment",
        "hardware",
        7900,
        d(2026, 10, 2),
        d(2026, 10, 9),
        "transfer",
        texts,
        "FAC-4417",
        extra=(
            Adjust(
                Decimal(450),
                local_instant(d(2026, 10, 3), "10:00"),
                reasons["shipping"],
            ),
        ),
    )
    first_pay = projector.steps[-1]
    assert isinstance(first_pay, Pay)  # noqa: S101 - built above
    repay_on = d(2026, 10, 13)
    projector = ExpensePlan(
        projector.key,
        projector.description,
        projector.category,
        projector.vendor,
        projector.amount,
        projector.incurred_on,
        projector.due_on,
        (
            *projector.steps,
            Reverse(
                0,
                local_instant(d(2026, 10, 12), "10:00"),
                texts.choices("expense_reversal_reasons")[0],
            ),
            Pay(
                first_pay.amount,
                local_instant(repay_on, PAY_TIME),
                "transfer",
                _reference("GTO", repay_on) + "-PRO-2",
            ),
        ),
        receipt_reference=projector.receipt_reference,
    )
    water = d(2026, 11, 3)
    void = Adjust(
        Decimal(-360),
        local_instant(water + timedelta(days=1), "09:30"),
        reasons["void"],
    )
    return (
        _one_off("geometry", "geometry", TEACHING_MATERIALS, "stationery", 1860,
                 d(2026, 9, 3), d(2026, 9, 10), "transfer", texts, "A-1029"),
        _one_off("paper", "paper", "supplies", "stationery", 740, d(2026, 9, 8),
                 d(2026, 9, 8), "cash", texts),
        _one_off("air", "air", "maintenance", "hardware", 2350, d(2026, 9, 22),
                 d(2026, 9, 29), "transfer", texts, "FAC-4302"),
        projector,
        _one_off("readers", "readers", TEACHING_MATERIALS, "stationery", 2480,
                 d(2026, 10, 5), d(2026, 10, 15), "transfer", texts, "A-1187"),
        _one_off("cleaning_supplies", "cleaning_supplies", "supplies", None, 615,
                 d(2026, 10, 6), d(2026, 10, 6), "cash", texts),
        _one_off("whiteboard", "whiteboard", "equipment", "furniture", 3200,
                 d(2026, 10, 8), d(2026, 10, 22), "transfer", texts, "T-0563"),
        _one_off("paint", "paint", "maintenance", "hardware", 1980, d(2026, 10, 17),
                 d(2026, 10, 24), "cash", texts),
        _one_off("lab", "lab", TEACHING_MATERIALS, "stationery", 1325,
                 d(2026, 10, 21), d(2026, 10, 28), "transfer", texts, "A-1240"),
        _one_off("water", "water", "supplies", None, 360, water, water, "cash", texts),
        _one_off("water_twice", "water", "supplies", None, 360, water, water, "cash",
                 texts, paid=False, extra=(void,)),
        _one_off("lock", "lock", "maintenance", "hardware", 980, d(2026, 11, 9),
                 d(2026, 11, 12), "cash", texts),
        _one_off("meeting", "meeting", "supplies", None, 430, d(2026, 11, 13),
                 d(2026, 11, 13), "cash", texts),
    )  # fmt: skip


def build_expenses(plan: SimulationPlan) -> Expenses:
    """The expenses plan in ``plan``'s language (pure; module docstring)."""
    texts = TextPool(plan.language)
    vendors = texts.mapping("vendors")
    contacts = texts.mapping("vendor_contacts")
    templates = _templates(texts)
    return Expenses(
        category_name=texts.text("teaching_materials_category"),
        vendors=tuple(VendorPlan(k, vendors[k], contacts[k]) for k in VENDOR_KEYS),
        templates=templates,
        generated=_generated(templates, texts),
        one_offs=_one_offs(texts),
    )
