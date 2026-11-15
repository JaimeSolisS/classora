"""The demo schedule plan (#299 G2, A5, D7, E1): a pure function of the
center's today. The scheduling rules are constants here, so the test does
not depend on the generator's own values."""

from collections import defaultdict
from datetime import date, time, timedelta

import pytest

from tests.factories.demo_schedule import (
    AULA_1,
    AULA_2,
    SALA,
    DemoSchedulePlan,
    PlannedRecurrence,
    plan_demo_schedule,
)

OPERATING_HOURS = {
    **{day: ("09:00", "20:00") for day in (1, 2, 3, 4, 5)},
    6: ("09:00", "14:00"),
}
"""The default business hours (Sunday closed)."""

TUTOR_WINDOWS = {
    **{day: ("15:00", "20:00") for day in (1, 2, 3, 4, 5)},
    6: ("09:00", "13:00"),
}
ROOM_WINDOWS: dict[str, dict[int, tuple[str, str]] | None] = {
    AULA_1: None,
    AULA_2: {day: ("15:00", "20:00") for day in (1, 2, 3, 4, 5)},
    SALA: {
        **{day: ("15:00", "20:00") for day in (1, 2, 3, 4, 5)},
        6: ("09:00", "14:00"),
    },
}
DAILY_LIMIT = 240
WEEKLY_LIMIT = 1200
REQUIRED_BREAK = 10

TODAYS = [
    *(date(2026, 3, 2) + timedelta(days=n) for n in range(14)),
    date(2026, 12, 30),
    date(2027, 7, 31),
    date(2027, 8, 1),
    date(2028, 2, 28),
]
"""14 consecutive days (every weekday twice), a year end, the last day of a
school year and the first of the next, and a leap February."""

type Slot = tuple[date, str, str, str | None, tuple[str, ...], bool]
"""Day, start, end, classroom, students, has a tutor."""


def _recurrence_slots(rule: PlannedRecurrence, skip: date) -> list[Slot]:
    return [
        (day, rule.start, rule.end, rule.classroom, rule.students, True)
        for day in rule.dates()
        if day != skip
    ]


def upcoming_slots(plan: DemoSchedulePlan) -> list[Slot]:
    """Every upcoming occupied slot (the closure occurrence excluded)."""
    return [
        *(
            (s.day, s.start, s.end, s.classroom, (s.student,), s.tutor)
            for s in plan.upcoming
        ),
        *_recurrence_slots(plan.series, plan.closure_day),
        *_recurrence_slots(plan.group, plan.closure_day),
    ]


def all_slots(plan: DemoSchedulePlan) -> list[Slot]:
    past = [
        (s.day, s.start, s.end, s.classroom, (s.student,), s.tutor) for s in plan.past
    ]
    return [*past, *upcoming_slots(plan)]


def _t(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


def _minutes(start: str, end: str) -> int:
    a, b = _t(start), _t(end)
    return (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)


def _inside(window: tuple[str, str] | None, start: str, end: str) -> bool:
    return (
        window is not None and _t(window[0]) <= _t(start) and _t(end) <= _t(window[1])
    )


def _overlap(a: Slot, b: Slot) -> bool:
    return a[0] == b[0] and _t(a[1]) < _t(b[2]) and _t(b[1]) < _t(a[2])


@pytest.fixture(params=TODAYS, ids=lambda d: f"{d:%a-%Y-%m-%d}")
def plan(request: pytest.FixtureRequest) -> DemoSchedulePlan:
    return plan_demo_schedule(request.param)


def test_the_closure_falls_in_the_coming_week(plan):
    today = plan.today
    assert today + timedelta(days=4) <= plan.closure_day <= today + timedelta(days=6)
    assert plan.closure_day < today + timedelta(days=13)
    assert plan.closure_day.isoweekday() <= 5


def test_upcoming_starts_are_at_least_two_days_ahead(plan):
    first = plan.today + timedelta(days=2)
    for day, *_ in upcoming_slots(plan):
        assert day >= first
    assert plan.series.starts_on >= first
    assert plan.group.starts_on >= first
    assert plan.group.starts_on.isoweekday() == 6
    assert plan.group.starts_on < first + timedelta(days=7)


def test_past_sessions_are_on_weekdays_before_today(plan):
    days = [s.day for s in plan.past]
    assert len(set(days)) == 4
    for day in days:
        assert day < plan.today
        assert day.isoweekday() <= 5


def test_the_series_skips_only_its_first_date_for_the_closure(plan):
    dates = plan.series.dates()
    assert len(dates) == 8
    assert dates[0] == plan.closure_day
    assert all(day.isoweekday() == plan.closure_day.isoweekday() for day in dates)
    assert plan.closure_day not in plan.group.dates()
    assert all(s.day != plan.closure_day for s in (*plan.past, *plan.upcoming))


def test_the_tutor_has_no_session_on_the_day_off(plan):
    assert plan.time_off_day > plan.today
    tutor_days = {day for day, *rest in all_slots(plan) if rest[-1]}
    assert plan.time_off_day not in tutor_days


def test_every_slot_is_inside_the_hours_and_the_windows(plan):
    for day, start, end, room, _students, tutor in all_slots(plan):
        weekday = day.isoweekday()
        assert _inside(OPERATING_HOURS.get(weekday), start, end), (day, start)
        if tutor:
            assert _inside(TUTOR_WINDOWS.get(weekday), start, end), (day, start)
        if room is not None:
            windows = ROOM_WINDOWS[room]
            if windows is not None:
                assert _inside(windows.get(weekday), start, end), (room, day, start)


def test_no_student_tutor_or_room_overlaps(plan):
    slots = all_slots(plan)
    for i, a in enumerate(slots):
        for b in slots[i + 1 :]:
            if not _overlap(a, b):
                continue
            assert not (set(a[4]) & set(b[4])), ("student", a, b)
            assert not (a[5] and b[5]), ("tutor", a, b)
            assert a[3] is None or a[3] != b[3], ("room", a, b)


def test_the_tutor_workload_stays_within_the_limits(plan):
    by_day: dict[date, list[Slot]] = defaultdict(list)
    for slot in all_slots(plan):
        if slot[5]:
            by_day[slot[0]].append(slot)
    by_week: dict[tuple[int, int], int] = defaultdict(int)
    for day, slots in by_day.items():
        slots.sort(key=lambda s: _t(s[1]))
        assert sum(_minutes(s[1], s[2]) for s in slots) <= DAILY_LIMIT, day
        for earlier, later in zip(slots, slots[1:], strict=False):
            assert _minutes(earlier[2], later[1]) >= REQUIRED_BREAK, (day, earlier)
        year, week, _ = day.isocalendar()
        by_week[(year, week)] += sum(_minutes(s[1], s[2]) for s in slots)
    assert all(total <= WEEKLY_LIMIT for total in by_week.values())


def test_the_school_year_contains_today(plan):
    year = plan.period_start_year
    assert date(year, 8, 1) <= plan.today <= date(year + 1, 7, 31)


def test_the_school_year_switches_on_1_august():
    assert plan_demo_schedule(date(2027, 7, 31)).period_start_year == 2026
    assert plan_demo_schedule(date(2027, 8, 1)).period_start_year == 2027
