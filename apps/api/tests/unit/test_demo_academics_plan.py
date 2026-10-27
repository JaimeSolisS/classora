"""The #353 demo plan (#353 G2, F1): a pure function of the center's today
on #299's ``P(k)``. It is checked together with #299's past sessions for the
18 days of #299 E1. The rules are constants here (the #299 and #303 tests'),
so the test does not depend on the generators' own values."""

from datetime import date, datetime, time, timedelta

import pytest

from tests.factories.demo_academics import DemoAcademicsPlan, plan_demo_academics
from tests.factories.demo_schedule import (
    AULA_1,
    AULA_2,
    DemoSchedulePlan,
    past_weekdays,
    plan_demo_schedule,
)
from tests.unit.test_demo_changes_plan import TUTOR2_WINDOWS
from tests.unit.test_demo_schedule_plan import (
    OPERATING_HOURS,
    REQUIRED_BREAK,
    ROOM_WINDOWS,
    TODAYS,
    TUTOR_WINDOWS,
)

ASSIGNMENT_LOOKBACK_DAYS = 30
"""#67: a tutor's students include those of classes in the last 30 days."""
E2E_SEARCH_DAYS = 7
"""``tests.factories.e2e_attendance.SEARCH_DAYS``."""

type Slot = tuple[date, str, str, str | None, frozenset[str], str | None]
"""Day, start, end, classroom, students, tutor (``tutor`` or ``tutor2``)."""


def _t(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


def _minutes(hhmm: str) -> int:
    value = _t(hhmm)
    return value.hour * 60 + value.minute


def _inside(window: tuple[str, str] | None, start: str, end: str) -> bool:
    return (
        window is not None and _t(window[0]) <= _t(start) and _t(end) <= _t(window[1])
    )


def _overlap(a: Slot, b: Slot) -> bool:
    return a[0] == b[0] and _t(a[1]) < _t(b[2]) and _t(b[1]) < _t(a[2])


def past_slots(schedule: DemoSchedulePlan, plan: DemoAcademicsPlan) -> list[Slot]:
    """#299's four past sessions, H1-H3 and the cohort's six classes."""
    slots: list[Slot] = [
        (s.day, s.start, s.end, s.classroom, frozenset({s.student}), "tutor")
        for s in schedule.past
    ]
    for h in plan.held:
        assert h.student is not None
        slots.append((h.day, h.start, h.end, AULA_1, frozenset({h.student}), "tutor"))
    for c in plan.cohort:
        students = frozenset(key for key, _ in c.attendance)
        slots.append((c.day, c.start, c.end, AULA_2, students, "tutor2"))
    return slots


def _local(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, _t(hhmm))


@pytest.fixture(params=TODAYS, ids=lambda d: f"{d:%a-%Y-%m-%d}")
def plans(request: pytest.FixtureRequest) -> tuple[DemoSchedulePlan, DemoAcademicsPlan]:
    return plan_demo_schedule(request.param), plan_demo_academics(request.param)


def test_no_student_tutor_or_room_overlaps_and_the_tutors_get_breaks(plans):
    slots = past_slots(*plans)
    for i, a in enumerate(slots):
        for b in slots[i + 1 :]:
            if not _overlap(a, b):
                continue
            assert not (a[4] & b[4]), ("student", a, b)
            assert a[5] != b[5], ("tutor", a, b)
            assert a[3] is None or a[3] != b[3], ("room", a, b)
    for tutor in ("tutor", "tutor2"):
        by_day: dict[date, list[Slot]] = {}
        for slot in slots:
            if slot[5] == tutor:
                by_day.setdefault(slot[0], []).append(slot)
        for day, items in by_day.items():
            items.sort(key=lambda s: _t(s[1]))
            for before, after in zip(items, items[1:], strict=False):
                gap = _minutes(after[1]) - _minutes(before[2])
                assert gap >= REQUIRED_BREAK, (tutor, day, before, after)


def test_every_class_fits_the_hours_the_rooms_and_the_tutors(plans):
    schedule, plan = plans
    for h in plan.held:
        weekday = h.day.isoweekday()
        assert weekday <= 5, h
        assert _inside(OPERATING_HOURS.get(weekday), h.start, h.end), h
        assert ROOM_WINDOWS[AULA_1] is None  # Aula 1: no windows
        assert _inside(TUTOR_WINDOWS.get(weekday), h.start, h.end), h
        assert h.day != schedule.closure_day
    windows = ROOM_WINDOWS[AULA_2]
    assert windows is not None
    for c in plan.cohort:
        weekday = c.day.isoweekday()
        assert weekday <= 5, c
        assert _inside(OPERATING_HOURS.get(weekday), c.start, c.end), c
        assert _inside(windows.get(weekday), c.start, c.end), c
        assert _inside(TUTOR2_WINDOWS.get(weekday), c.start, c.end), c
        assert c.day != schedule.closure_day


def test_the_cohort_has_six_dates_on_two_weekdays(plans):
    _, plan = plans
    p = past_weekdays(plan.today, 13)
    days = [c.day for c in plan.cohort]
    assert days == [p[k - 1] for k in (13, 11, 8, 6, 3, 1)]
    assert days == sorted(days)
    assert plan.cohort_starts_on == p[12] == days[0]
    assert len(plan.cohort_weekdays) == 2
    assert {d.isoweekday() for d in days} == set(plan.cohort_weekdays)
    # The rule's dates from the start: exactly these six (the derived count).
    rule: list[date] = []
    day = plan.cohort_starts_on
    while len(rule) < 6:
        if day.isoweekday() in plan.cohort_weekdays:
            rule.append(day)
        day += timedelta(days=1)
    assert rule == days
    assert [(c.start, c.end) for c in plan.cohort] == [("18:00", "19:30")] * 6
    assert (plan.cohort_start, plan.cohort_end) == ("18:00", "19:30")


def test_the_held_classes_are_more_than_a_week_old(plans):
    _, plan = plans
    p = past_weekdays(plan.today, 8)
    assert [h.day for h in plan.held] == [p[5], p[6], p[7]]
    for h in plan.held:
        assert h.day <= plan.today - timedelta(days=E2E_SEARCH_DAYS + 1), h


def test_the_seed_clock_instants_are_ordered_and_past(plans):
    _, plan = plans
    t0 = _local(plan.created_on, plan.created_at)
    midnight = _local(plan.today, "00:00")
    for classes in (plan.held, plan.cohort):
        instants = [t0]
        for c in sorted(classes, key=lambda c: c.day):
            assert _local(c.day, c.end) < _local(c.day, c.closeout_at)
            instants += [_local(c.day, c.closeout_at), _local(c.day, c.approve_at)]
        assert instants == sorted(instants)
        assert len(set(instants)) == len(instants)
        assert instants[-1] < midnight
    first = min(_local(c.day, c.start) for c in (*plan.held, *plan.cohort))
    assert t0 + timedelta(hours=24) < first


def test_the_cohort_ends_within_the_assignment_lookback(plans):
    _, plan = plans
    last = plan.cohort[-1]
    assert _local(plan.today, "00:00") - _local(last.day, last.end) < timedelta(
        days=ASSIGNMENT_LOOKBACK_DAYS
    )


def test_the_attendance_gives_the_completion_rates(plans):
    _, plan = plans
    attended = {"S": 0, "A": 0, "B": 0}
    for c in plan.cohort:
        for key, status in c.attendance:
            attended[key] += status in {"present", "late"}
    assert attended == {"S": 6, "A": 5, "B": 4}
    assert [h.attendance for h in plan.held] == [
        (("S", "present"),),
        (("Sib", "late"),),
        (("S", "present"),),
    ]
