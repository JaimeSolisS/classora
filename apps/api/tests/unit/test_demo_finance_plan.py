"""The M7-demo-b plan (#501 "The plan", F1): a pure function of the center's
today, checked for #353's 18 days (#299 E1), ``FROZEN_NOW``'s Wednesday and
an ``A31`` more than 31 days back. The rules (the status rule, the template
window, the period grid) are restated here, so the test doesn't depend on the
generators' own values."""

import calendar
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.factories.demo_billing import plan_demo_billing
from tests.factories.demo_finance import (
    FEE_AMOUNT,
    TL_NAME,
    TR_NAME,
    TV_NAME,
    Adjust,
    DemoFinancePlan,
    Pay,
    PlannedExpense,
    Reverse,
    latest_31st,
    plan_demo_finance,
)
from tests.factories.demo_payroll import plan_demo_payroll
from tests.unit.test_demo_schedule_plan import TODAYS

D = Decimal
STARTS_ON_BACK_DAYS = 31
"""#116 E5: ``starts_on`` at most 31 days before the center's today."""
STARTS_ON_AHEAD_DAYS = 366
EXTRA_TODAYS = [
    date(2026, 11, 4),  # FROZEN_NOW's Wednesday
    date(2026, 5, 15),  # A31 = 03-31, 45 days back
    date(2026, 10, 30),  # A31 = 08-31, 60 days back
]
ALL_TODAYS = [*TODAYS, *EXTRA_TODAYS]


@pytest.fixture(params=ALL_TODAYS, ids=lambda d: f"{d:%a-%Y-%m-%d}")
def plan(request: pytest.FixtureRequest) -> DemoFinancePlan:
    return plan_demo_finance(request.param)


def _grid(starts_on: date, frequency: str, k: int) -> date:
    """Period ``k`` of a template (#116 B: the day clamped, always from
    ``starts_on``)."""
    if frequency == "weekly":
        return starts_on + timedelta(days=7 * k)
    months = k if frequency == "monthly" else 12 * k
    index = starts_on.month - 1 + months
    year, month = starts_on.year + index // 12, index % 12 + 1
    return date(year, month, min(starts_on.day, calendar.monthrange(year, month)[1]))


def _status(expense: PlannedExpense, today: date) -> str:
    """data-model §13.2, restated."""
    net = D(expense.amount) + sum(
        (D(s.amount) for s in expense.steps if isinstance(s, Adjust)), D(0)
    )
    reversed_ = {s.index for s in expense.steps if isinstance(s, Reverse)}
    paid = sum(
        (D(p.amount) for i, p in enumerate(expense.payments) if i not in reversed_),
        D(0),
    )
    if net == 0:
        return "void"
    if net - paid <= 0:
        return "paid"
    if expense.due_on < today:
        return "overdue"
    if paid > 0:
        return "partially_paid"
    return "unpaid"


def test_the_dates(plan):
    t, f = plan.today, plan.floor
    assert f == plan_demo_payroll(t).floor  # reused, not re-derived
    assert timedelta(days=17) <= t - f <= timedelta(days=23)
    assert f + timedelta(days=17) <= t  # X4's second payment
    assert f + timedelta(days=14) < t  # TL's third week is overdue
    assert f + timedelta(days=9) < t  # X2 is overdue
    assert f + timedelta(days=30) > t  # TA is unpaid
    assert plan.month_start == t.replace(day=1)
    assert plan.anchor_31.day == 31
    assert plan.anchor_31 <= t
    # No 31st between A31 and T.
    day = plan.anchor_31 + timedelta(days=1)
    while day <= t:
        assert day.day != 31
        day += timedelta(days=1)
    # T0 = P(13) - 2 days, before the billing floor B = P(13) (G5).
    assert plan_demo_billing(t).floor - plan.created_on == timedelta(days=2)
    assert plan.fee_amount == FEE_AMOUNT


def test_the_templates(plan):
    t = plan.today
    assert [x.key for x in plan.templates] == ["TR", "TI", "TL", "TV", "TA"]
    created = [x.created_on for x in plan.creation_order]
    assert created == sorted(created)
    for template in plan.templates:
        # Created at 12:00 the day before it starts: before T 00:00, and
        # starts_on inside #116 E5's window on that day.
        assert template.created_on < t
        today_then = template.created_on
        assert (
            today_then - timedelta(days=STARTS_ON_BACK_DAYS)
            <= template.starts_on
            <= today_then + timedelta(days=STARTS_ON_AHEAD_DAYS)
        )
        assert template.ends_on is None or template.ends_on >= template.starts_on
        # The job's catch-up: every period from starts_on up to T (and the
        # end), on the grid.
        expected = []
        k = 0
        while (start := _grid(template.starts_on, template.frequency, k)) <= t and (
            template.ends_on is None or start <= template.ends_on
        ):
            expected.append(start)
            k += 1
        assert list(template.periods) == expected, template.key
        assert template.vendor != "V4"
        assert template.category != "furniture"
    assert len(plan.template("TR").periods) == 1
    assert 1 <= len(plan.template("TI").periods) <= 2
    tl = plan.template("TL")
    assert len(tl.periods) in (3, 4)
    assert tl.due_on(tl.periods[2]) < t  # overdue
    tv = plan.template("TV")
    assert len(tv.periods) == 2
    assert tv.ends_on is not None
    assert tv.ends_on < tv.periods[1] + timedelta(days=7)  # then ended
    assert [p.paid_on for p in plan.tv_paid] == [tv.due_on(p) for p in tv.periods]
    assert all(p.paid_on <= t for p in plan.tv_paid)
    assert plan.tl_paid.paid_on == tl.periods[0]
    assert D(plan.tl_void.amount) == -D(tl.amount)
    ta = plan.template("TA")
    assert len(ta.periods) == 1
    assert ta.due_on(ta.periods[0]) > t  # unpaid
    assert plan.generated == sum(len(x.periods) for x in plan.templates)


def test_the_expenses(plan):
    t, f = plan.today, plan.floor
    assert [x.key for x in plan.expenses] == [f"X{k}" for k in range(1, 10)]
    for expense in plan.expenses:
        assert f <= expense.incurred_on <= t, expense.key
        assert expense.due_on >= expense.incurred_on, expense.key
        assert _status(expense, t) == expense.status, expense.key
        open_ = D(expense.amount)
        live: list[tuple[str, str | None]] = []
        payments = list(expense.payments)
        index = 0
        for step in expense.steps:
            if isinstance(step, Pay):
                payment = step.payment
                assert expense.incurred_on <= payment.paid_on <= t, expense.key
                assert payment.method in ("cash", "transfer")
                assert D(payment.amount) <= open_, expense.key  # never refused
                if payment.reference is not None:
                    assert (payment.method, payment.reference) not in live
                live.append((payment.method, payment.reference))
                open_ -= D(payment.amount)
                index += 1
            elif isinstance(step, Reverse):
                reversed_ = payments[step.index]
                live.remove((reversed_.method, reversed_.reference))
                open_ += D(reversed_.amount)
            else:
                assert D(step.amount) != 0
                assert D(step.amount) >= -open_
                open_ += D(step.amount)
    assert {x.status for x in plan.expenses} == {
        "unpaid",
        "overdue",
        "partially_paid",
        "paid",
        "void",
    }
    # "Mobiliario" and V4: X9 only.
    assert [x.key for x in plan.expenses if x.category == "furniture"] == ["X9"]
    assert [x.key for x in plan.expenses if x.vendor == "V4"] == ["X9"]


def test_the_frozen_wednesday():
    plan = plan_demo_finance(date(2026, 11, 4))

    assert plan.floor == date(2026, 10, 12)
    assert plan.month_start == date(2026, 11, 1)
    assert plan.anchor_31 == date(2026, 10, 31)
    assert plan.created_on == date(2026, 10, 14)
    assert [(x.key, x.created_on) for x in plan.creation_order] == [
        ("TL", date(2026, 10, 11)),
        ("TV", date(2026, 10, 11)),
        ("TA", date(2026, 10, 11)),
        ("TI", date(2026, 10, 30)),
        ("TR", date(2026, 10, 31)),
    ]
    assert {x.key: x.periods for x in plan.templates} == {
        "TR": (date(2026, 11, 1),),
        "TI": (date(2026, 10, 31),),
        "TL": tuple(date(2026, 10, 12) + timedelta(weeks=k) for k in range(4)),
        "TV": (date(2026, 10, 12), date(2026, 10, 19)),
        "TA": (date(2026, 10, 12),),
    }
    assert plan.generated == 9
    assert plan.template("TV").ends_on == date(2026, 10, 25)
    assert (plan.template("TR").name, plan.template("TL").name) == (TR_NAME, TL_NAME)
    assert plan.template("TV").name == TV_NAME
    x4 = plan.expense("X4")
    assert [(p.paid_on, p.amount) for p in x4.payments] == [
        (date(2026, 10, 15), "2500.00"),
        (date(2026, 10, 29), "1700.00"),
    ]
    assert [p.paid_on for p in plan.expense("X5").payments] == [
        date(2026, 10, 20),
        date(2026, 10, 22),
    ]
    assert plan.expense("X1").due_on == date(2026, 11, 19)


def test_the_weekend_has_three_cleaning_weeks():
    for today in (date(2026, 11, 7), date(2026, 11, 8)):
        plan = plan_demo_finance(today)
        assert plan.floor == date(2026, 10, 19)
        assert len(plan.template("TL").periods) == 3
        assert plan.generated == 8


def test_an_anchor_31_more_than_31_days_back():
    plan = plan_demo_finance(date(2026, 5, 15))

    assert plan.anchor_31 == date(2026, 3, 31)
    ti = plan.template("TI")
    assert ti.created_on == date(2026, 3, 30)
    assert ti.periods == (date(2026, 3, 31), date(2026, 4, 30))


def test_a_today_that_is_a_31st():
    today = date(2027, 7, 31)
    plan = plan_demo_finance(today)

    assert plan.anchor_31 == today
    ti = plan.template("TI")
    assert ti.periods == (today,)
    assert ti.created_on == date(2027, 7, 30)
    assert plan.month_start == date(2027, 7, 1)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 4), date(2026, 10, 31)),
        (date(2026, 10, 31), date(2026, 10, 31)),
        (date(2026, 3, 30), date(2026, 1, 31)),
        (date(2026, 1, 15), date(2025, 12, 31)),
        (date(2026, 8, 1), date(2026, 7, 31)),
    ],
)
def test_latest_31st(today, expected):
    assert latest_31st(today) == expected
