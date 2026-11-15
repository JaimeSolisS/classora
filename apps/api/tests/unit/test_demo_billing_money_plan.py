"""The M6-demo-b plan (H4, E1): a pure function of the center's today on
#299's ``P(k)``/``D(k)`` and the weekly series, checked for #353's 18 days
(#299 E1). The rules are constants here, so the test does not depend on the
generators' own values."""

from datetime import date, timedelta

import pytest

from app.modules.billing.reminders import reminder_stage
from tests.factories.demo_billing_money import (
    DemoBillingMoneyPlan,
    plan_demo_billing_money,
)
from tests.factories.demo_changes import plan_demo_changes
from tests.factories.demo_schedule import (
    past_weekdays,
    plan_demo_schedule,
    upcoming_weekdays,
)
from tests.unit.test_demo_schedule_plan import TODAYS

MANUAL_CHARGE_HORIZON = timedelta(days=366)
"""#100 R7: a manual charge's dates at most 366 days after today."""


@pytest.fixture(params=TODAYS, ids=lambda d: f"{d:%a-%Y-%m-%d}")
def plan(request: pytest.FixtureRequest) -> DemoBillingMoneyPlan:
    return plan_demo_billing_money(request.param)


def test_cx_is_the_first_ordinary_series_occurrence(plan):
    schedule = plan_demo_schedule(plan.today)
    d = upcoming_weekdays(plan.today, 13)
    assert plan.cx_day == schedule.series.dates()[2] == d[12]
    assert plan.cx_start == schedule.series.start
    # Upcoming, on the series weekday, neither the closure nor the
    # substitution.
    assert plan.cx_day > plan.today
    assert plan.cx_day.isoweekday() == schedule.series.weekday
    assert plan.cx_day != schedule.closure_day == d[2]
    assert plan.cx_day != plan_demo_changes(plan.today).substitution_day
    assert plan.cx_day in schedule.series.dates()


def test_payments_are_received_on_past_weekdays(plan):
    p = past_weekdays(plan.today, 3)
    assert (plan.py1_received_on, plan.py2_received_on) == (p[2], p[0])
    for day in (plan.py1_received_on, plan.py2_received_on):
        assert day < plan.today
        assert day.isoweekday() <= 5
    # received_on is never after the center's today.
    assert plan.py3_received_on == plan.today


def test_the_manual_charge_is_past_due(plan):
    # F2 is overdue after part 2 (the #413 QA AC).
    assert plan.mc_service_date <= plan.mc_due_date < plan.today
    assert reminder_stage(plan.mc_due_date, plan.today) == "overdue"
    for day in (plan.mc_service_date, plan.mc_due_date):
        assert day <= plan.today + MANUAL_CHARGE_HORIZON


def test_the_changed_due_date_is_due_soon(plan):
    assert plan.dc_due_date == plan.today + timedelta(days=2)
    assert reminder_stage(plan.dc_due_date, plan.today) == "due_soon"


def test_the_plan_is_a_pure_function_of_today():
    today = date(2026, 11, 4)
    assert plan_demo_billing_money(today) == plan_demo_billing_money(today)
    # The #299 frozen day (E tests).
    plan = plan_demo_billing_money(today)
    assert (
        plan.cx_day,
        plan.py1_received_on,
        plan.py2_received_on,
        plan.mc_service_date,
        plan.mc_due_date,
        plan.dc_due_date,
    ) == (
        date(2026, 11, 24),
        date(2026, 10, 30),
        date(2026, 11, 3),
        date(2026, 10, 30),
        date(2026, 11, 3),
        date(2026, 11, 6),
    )
