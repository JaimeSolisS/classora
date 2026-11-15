"""The M6-demo billing plan (G4, E1): a pure function of the center's today
on #299's ``P(k)``, checked with #299's past sessions and #353's classes for
the 18 days of #299 E1. The rules are constants here (the #299 and #353
tests'), so the test does not depend on the generators' own values."""

from datetime import date, datetime, time, timedelta

import pytest

from tests.factories.demo_academics import DemoAcademicsPlan, plan_demo_academics
from tests.factories.demo_billing import (
    PK1_DAYS,
    PK2_DAYS,
    DemoBillingPlan,
    first_of_next_month,
    plan_demo_billing,
)
from tests.factories.demo_schedule import (
    AULA_1,
    ENGLISH_SECONDARY,
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
"""The default booking notice (#299 D(k): more than 24 hours)."""

type Plans = tuple[DemoSchedulePlan, DemoAcademicsPlan, DemoBillingPlan]


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


@pytest.fixture(params=TODAYS, ids=lambda d: f"{d:%a-%Y-%m-%d}")
def plans(request: pytest.FixtureRequest) -> Plans:
    today = request.param
    return (
        plan_demo_schedule(today),
        plan_demo_academics(today),
        plan_demo_billing(today),
    )


def test_the_floor_is_the_first_day_of_the_finished_cohort(plans):
    _, academics, plan = plans
    p = past_weekdays(plan.today, 13)
    assert plan.floor == p[12] == academics.cohort[0].day
    # Every #353 class is on or after the floor.
    for c in (*academics.held, *academics.cohort):
        assert c.day >= plan.floor


def test_the_sibling_package_lapses_whole(plans):
    schedule, academics, plan = plans
    p = past_weekdays(plan.today, 13)
    assert plan.ps_sold_on == p[12]
    assert plan.ps_expires_on == plan.ps_sold_on + timedelta(days=PK2_DAYS) == p[7]
    assert plan.ps_expires_on < plan.today
    sib_days = [s.day for s in schedule.past if s.student == "Sib"]
    sib_days += [h.day for h in academics.held if h.student == "Sib"]
    assert sib_days
    for day in sib_days:
        assert not plan.ps_sold_on <= day <= plan.ps_expires_on, day


def test_the_english_package_covers_only_h4(plans):
    schedule, academics, plan = plans
    p = past_weekdays(plan.today, 13)
    assert plan.pe_sold_on == p[2]
    assert plan.pe_expires_on == plan.pe_sold_on + timedelta(days=PK1_DAYS)
    assert plan.pe_expires_on > plan.today
    # S's earlier Inglés classes (#299's P4, #353's H3) are before the sale,
    # so they are charged; H4 is the only one inside the window.
    english = [
        s.day
        for s in schedule.past
        if s.student == "S" and s.subject == ENGLISH_SECONDARY
    ]
    english += [
        h.day
        for h in academics.held
        if h.student == "S" and h.subject == ENGLISH_SECONDARY
    ]
    assert len(english) == 2
    assert all(day < plan.pe_sold_on for day in english)
    h4 = plan.h4
    assert (h4.student, h4.subject) == ("S", ENGLISH_SECONDARY)
    assert plan.pe_sold_on < h4.day < plan.today
    assert h4.day == p[1]


def test_h4_is_in_a_free_slot(plans):
    # G5 fallback: H4 overlaps no student, tutor or room of the past demo
    # classes, keeps demo.tutor's break and fits the hours and windows.
    schedule, academics, plan = plans
    h4 = plan.h4
    slot: Slot = (h4.day, h4.start, h4.end, AULA_1, frozenset({"S"}), "tutor")
    slots = past_slots(schedule, academics)
    for other in slots:
        if _overlap(slot, other):
            assert not (slot[4] & other[4]), ("student", other)
            assert slot[5] != other[5], ("tutor", other)
            assert slot[3] != other[3], ("room", other)
    same_day = sorted(
        (s for s in [*slots, slot] if s[0] == h4.day and s[5] == "tutor"),
        key=lambda s: _t(s[1]),
    )
    for before, after in zip(same_day, same_day[1:], strict=False):
        assert _minutes(after[1]) - _minutes(before[2]) >= REQUIRED_BREAK
    weekday = h4.day.isoweekday()
    assert weekday <= 5
    assert _inside(OPERATING_HOURS.get(weekday), h4.start, h4.end)
    assert _inside(TUTOR_WINDOWS.get(weekday), h4.start, h4.end)
    assert ROOM_WINDOWS[AULA_1] is None  # Aula 1: no windows
    assert h4.day != schedule.closure_day
    assert h4.actual_minutes == _minutes(h4.end) - _minutes(h4.start)


def test_the_seed_instants_are_weekdays_before_today_in_order(plans):
    _, _, plan = plans
    instants = [(day, at) for _, day, at in plan.instants]
    assert [name for name, _, _ in plan.instants] == [
        "sell_ps",
        "sell_pe",
        "create_h4",
        "hold_h4",
        "approve_h4",
    ]
    local = [_local(day, at) for day, at in instants]
    assert local == sorted(local)
    assert len(set(local)) == len(local)
    for day, _ in instants:
        assert day.isoweekday() <= 5
    assert local[-1] < _local(plan.today, "00:00")
    # H4 is created with more than the booking notice and closed out after
    # it ends.
    h4 = plan.h4
    created = _local(plan.h4_created_on, plan.h4_created_at)
    assert created + BOOKING_NOTICE < _local(h4.day, h4.start)
    assert _local(h4.day, h4.end) < _local(h4.day, h4.closeout_at)
    assert _local(plan.pe_sold_on, plan.sale_at) < created


def test_the_upcoming_price_is_after_today(plans):
    _, _, plan = plans
    assert plan.pr1_next_from > plan.today
    assert plan.pr1_next_from.day == 1
    assert plan.pr1_next_from - plan.today <= timedelta(days=31)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 4), date(2026, 12, 1)),
        (date(2026, 12, 30), date(2027, 1, 1)),
        (date(2027, 1, 31), date(2027, 2, 1)),
    ],
)
def test_first_of_next_month(today, expected):
    assert first_of_next_month(today) == expected


def test_the_frozen_day_dates():
    # E: T = Wednesday 2026-11-04.
    plan = plan_demo_billing(date(2026, 11, 4))
    assert plan.floor == date(2026, 10, 16)
    assert plan.ps_expires_on == date(2026, 10, 23)
    assert plan.pe_sold_on == date(2026, 10, 30)
    assert plan.pe_expires_on == date(2026, 12, 29)
    assert plan.h4.day == date(2026, 11, 2)
    assert plan.pr1_next_from == date(2026, 12, 1)
