"""The M7-demo payroll plan (#499 "The plan", E1): a pure function of the
center's today on #299's ``P(k)``, checked for #353's 18 days (#299 E1)
against #299's past sessions, #353's classes and M6-demo's H4. The rules are
constants here (the #299, #353 and M6-demo tests'), so the test does not
depend on the generators' own values."""

from datetime import date, datetime, time, timedelta

import pytest

from tests.factories.demo_academics import DemoAcademicsPlan, plan_demo_academics
from tests.factories.demo_billing import DemoBillingPlan, plan_demo_billing
from tests.factories.demo_payroll import DemoPayrollPlan, plan_demo_payroll
from tests.factories.demo_schedule import (
    AULA_1,
    MATH_SECONDARY,
    DemoSchedulePlan,
    past_weekdays,
    plan_demo_schedule,
)
from tests.unit.test_demo_academics_plan import Slot, past_slots
from tests.unit.test_demo_schedule_plan import (
    OPERATING_HOURS,
    REQUIRED_BREAK,
    ROOM_WINDOWS,
    TODAYS,
    TUTOR_WINDOWS,
)

BOOKING_NOTICE = timedelta(hours=24)
"""The default booking notice."""
RATE_PER_MINUTE = 4
"""240.00 an hour (G3)."""
BONUS = 150
DEDUCTION = 80
MANUAL = 120
PP2_PLUS_PP3 = 300

type Plans = tuple[
    DemoSchedulePlan, DemoAcademicsPlan, DemoBillingPlan, DemoPayrollPlan
]


def _t(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


def _minutes(hhmm: str) -> int:
    value = _t(hhmm)
    return value.hour * 60 + value.minute


def _local(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, _t(hhmm))


def _inside(window: tuple[str, str] | None, start: str, end: str) -> bool:
    return (
        window is not None and _t(window[0]) <= _t(start) and _t(end) <= _t(window[1])
    )


def _overlap(a: Slot, b: Slot) -> bool:
    return a[0] == b[0] and _t(a[1]) < _t(b[2]) and _t(b[1]) < _t(a[2])


def _weekdays_before(today: date) -> int:
    """``j``: the weekdays of ``T``'s week before ``T`` (E4)."""
    return min(today.weekday(), 5)


@pytest.fixture(params=TODAYS, ids=lambda d: f"{d:%a-%Y-%m-%d}")
def plans(request: pytest.FixtureRequest) -> Plans:
    today = request.param
    return (
        plan_demo_schedule(today),
        plan_demo_academics(today),
        plan_demo_billing(today),
        plan_demo_payroll(today),
    )


def test_the_floor_and_the_ended_weeks(plans):
    # E4: F is the Monday on or before P(13); three ended weeks Mon-Wed, two
    # Thu-Sun; then the current week.
    _, academics, _, plan = plans
    p = past_weekdays(plan.today, 13)
    assert plan.floor.isoweekday() == 1
    assert plan.floor <= p[12] < plan.floor + timedelta(days=7)
    assert plan.floor <= academics.cohort[0].day
    assert plan.n == (3 if plan.today.isoweekday() <= 3 else 2)
    assert plan.ended == tuple(plan.floor + timedelta(weeks=k) for k in range(plan.n))
    assert plan.current == plan.floor + timedelta(weeks=plan.n)
    assert plan.current <= plan.today < plan.current + timedelta(days=7)
    for monday in plan.ended:
        assert monday + timedelta(days=6) < plan.today
    j = _weekdays_before(plan.today)
    # Wn holds P(j+1)…P(j+5), W(n-1) P(j+6)…P(j+10).
    for k in range(j + 1, j + 6):
        assert plan.week_of(p[k - 1]) == plan.n
    for k in range(j + 6, min(j + 11, 14)):
        assert plan.week_of(p[k - 1]) == plan.n - 1
    for k in range(1, j + 1):
        assert plan.week_of(p[k - 1]) == 0


def test_the_late_approval_and_the_correction_land_as_planned(plans):
    # L1's week is finalized before Wn, so its approval lands in Wn; H2 is in
    # an ended week, so its correction is an hours_correction.
    _, academics, _, plan = plans
    l1_week = plan.week_of(plan.l1.day)
    assert l1_week is not None and 1 <= l1_week < plan.n
    h2 = next(h for h in academics.held if h.key == "H2")
    assert h2.actual_minutes == 55
    h2_week = plan.week_of(h2.day)
    assert h2_week is not None and h2_week >= 1
    n1_week = plan.week_of(plan.n1.day)
    assert n1_week is not None and n1_week >= 1
    # Every class used here is on or after the floor and billing's floor.
    billing_floor = past_weekdays(plan.today, 13)[12]
    for day in (plan.l1.day, plan.n1.day, *(h.day for h in academics.held)):
        assert day >= billing_floor >= plan.floor


def test_l1_and_n1_are_in_free_slots(plans):
    # E6: no student, tutor or room overlap with #299, #353 and M6-demo's H4;
    # demo.tutor's break, the hours and windows; not the cohort's weekdays;
    # before S's English package sale.
    schedule, academics, billing, plan = plans
    h4 = billing.h4
    slots = [
        *past_slots(schedule, academics),
        (h4.day, h4.start, h4.end, AULA_1, frozenset({"S"}), "tutor"),
    ]
    for planned in (plan.l1, plan.n1):
        assert (planned.student, planned.subject) == ("S", MATH_SECONDARY)
        slot: Slot = (
            planned.day,
            planned.start,
            planned.end,
            AULA_1,
            frozenset({"S"}),
            "tutor",
        )
        for other in slots:
            if _overlap(slot, other):
                assert not (slot[4] & other[4]), ("student", other)
                assert slot[5] != other[5], ("tutor", other)
                assert slot[3] != other[3], ("room", other)
        same_day = sorted(
            (s for s in [*slots, slot] if s[0] == planned.day and s[5] == "tutor"),
            key=lambda s: _t(s[1]),
        )
        for before, after in zip(same_day, same_day[1:], strict=False):
            assert _minutes(after[1]) - _minutes(before[2]) >= REQUIRED_BREAK
        weekday = planned.day.isoweekday()
        assert weekday <= 5
        assert weekday not in academics.cohort_weekdays
        assert _inside(OPERATING_HOURS.get(weekday), planned.start, planned.end)
        assert _inside(TUTOR_WINDOWS.get(weekday), planned.start, planned.end)
        assert ROOM_WINDOWS[AULA_1] is None
        assert planned.day != schedule.closure_day
        assert planned.day < billing.pe_sold_on
        slots.append(slot)
    assert plan.l1.attendance == (("S", "present"),)
    assert plan.n1.attendance == (("S", "absent"),)


def _tutor_week_totals(plans: Plans) -> dict[int, int]:
    """``demo.tutor``'s total of each ended week, in pesos, under G3 (A1:
    approved minutes at 240.00 an hour; N1 approved at 60 minutes)."""
    _, academics, billing, plan = plans
    totals = dict.fromkeys(range(1, plan.n + 1), BONUS)
    totals[1] -= DEDUCTION
    approved = [(h.day, h.actual_minutes) for h in academics.held]
    approved += [(billing.h4.day, billing.h4.actual_minutes)]
    approved += [(plan.n1.day, plan.n1.actual_minutes)]
    for day, minutes in approved:
        week = plan.week_of(day)
        if week:
            totals[week] += minutes * RATE_PER_MINUTE
    totals[plan.n] += plan.l1.actual_minutes * RATE_PER_MINUTE + MANUAL
    return totals


def test_the_payments_fit(plans):
    # W2's lines can hold PP2 + PP3, W1 is above zero, and no week is
    # negative (prepare would refuse it).
    totals = _tutor_week_totals(plans)
    assert totals[2] >= BONUS + 2 * 60 * RATE_PER_MINUTE > PP2_PLUS_PP3
    assert totals[1] >= BONUS - DEDUCTION > 0
    assert all(total >= 0 for total in totals.values())
    plan = plans[3]
    assert plan.paid_on == plan.today
    assert plan.paid_on >= plan.ended[1]


def test_the_seed_instants_are_weekdays_before_today_in_order(plans):
    _, academics, _, plan = plans
    assert [name for name, _, _ in plan.instants] == [
        "create",
        "record_l1",
        "record_n1",
        "approve_n1",
    ]
    local = [_local(day, at) for _, day, at in plan.instants]
    assert local == sorted(local)
    assert len(set(local)) == len(local)
    for _, day, _ in plan.instants[1:]:
        assert day.isoweekday() <= 5
    assert local[-1] < _local(plan.today, "00:00")
    # #353's T0; more than the booking notice before each class; recorded
    # after it ends.
    assert (plan.created_on, plan.created_at) == (
        academics.created_on,
        academics.created_at,
    )
    for planned in (plan.l1, plan.n1):
        assert local[0] + BOOKING_NOTICE < _local(planned.day, planned.start)
        assert _local(planned.day, planned.end) < _local(
            planned.day, planned.closeout_at
        )
        assert planned.actual_minutes == _minutes(planned.end) - _minutes(planned.start)


def test_the_plan_is_a_pure_function_of_today():
    today = date(2026, 11, 4)
    assert plan_demo_payroll(today) == plan_demo_payroll(today)


def test_the_frozen_day_dates():
    # #499 "Expected figures": T = Wednesday 2026-11-04.
    plan = plan_demo_payroll(date(2026, 11, 4))
    assert plan.floor == date(2026, 10, 12)
    assert plan.ended == (date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26))
    assert plan.current == date(2026, 11, 2)
    assert (plan.l1.day, plan.n1.day) == (date(2026, 10, 19), date(2026, 10, 21))
    assert (plan.created_on, plan.created_at) == (date(2026, 10, 14), "12:00")
    assert plan.paid_on == date(2026, 11, 4)


@pytest.mark.parametrize(
    ("today", "n"),
    [(date(2026, 11, 7), 2), (date(2026, 11, 8), 2), (date(2026, 11, 2), 3)],
    ids=["saturday", "sunday", "monday"],
)
def test_the_weekend_and_monday(today, n):
    # E3: two ended weeks on the weekend; three on a Monday.
    plan = plan_demo_payroll(today)
    assert plan.n == n
    assert plan.current == today - timedelta(days=today.weekday())
