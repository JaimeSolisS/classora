"""The #303 demo plan (#303 G2, A4, E1): a pure function of the center's
today on top of #299's. #299's plan and #303's are checked together, with
the substitution moving ``D(8)`` to ``demo.tutor2`` and the ``D(7)`` cohort
session cancelled. The rules are constants here (the #299 test's), so the
test does not depend on the generators' own values."""

from datetime import date, time, timedelta

import pytest

from tests.factories.demo_changes import DemoChangesPlan, plan_demo_changes
from tests.factories.demo_schedule import (
    AULA_2,
    DemoSchedulePlan,
    plan_demo_schedule,
    upcoming_weekdays,
)
from tests.unit.test_demo_schedule_plan import (
    OPERATING_HOURS,
    ROOM_WINDOWS,
    TODAYS,
    TUTOR_WINDOWS,
)

TUTOR2_WINDOWS = {day: ("15:00", "20:00") for day in (1, 2, 3, 4, 5)}
"""``demo.tutor2``'s weekly windows (#303 B3)."""
QUALIFIED = {
    "Matemáticas (Secundaria)": {"tutor", "tutor2"},
    "Ciencias (Secundaria)": {"tutor2"},
}
"""The approved qualifications for the two subjects (#299 C3, #303 B2)."""
GRID_MINUTES = 30

type Slot = tuple[date, str, str, str | None, frozenset[str], str | None]
"""Day, start, end, classroom, students, tutor (``tutor``, ``tutor2`` or none)."""


def _t(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


def _plus(hhmm: str, minutes: int) -> str:
    start = _t(hhmm)
    total = start.hour * 60 + start.minute + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


def _inside(window: tuple[str, str] | None, start: str, end: str) -> bool:
    return (
        window is not None and _t(window[0]) <= _t(start) and _t(end) <= _t(window[1])
    )


def _overlap(a: Slot, b: Slot) -> bool:
    return a[0] == b[0] and _t(a[1]) < _t(b[2]) and _t(b[1]) < _t(a[2])


def combined_slots(schedule: DemoSchedulePlan, changes: DemoChangesPlan) -> list[Slot]:
    """Every occupying slot after both plans: #299's past, upcoming (U4 and
    U5 have no tutor), series and group occurrences (not the closure one,
    ``D(8)`` taught by ``demo.tutor2``), and the cohort's sessions but the
    cancelled one."""
    slots: list[Slot] = []
    for s in (*schedule.past, *schedule.upcoming):
        tutor = "tutor" if s.tutor and s.outcome not in {"decline"} else None
        slots.append(
            (s.day, s.start, s.end, s.classroom, frozenset({s.student}), tutor)
        )
    for rule in (schedule.series, schedule.group):
        for day in rule.dates():
            if day == schedule.closure_day:
                continue
            tutor = "tutor2" if day == changes.substitution_day else "tutor"
            slots.append(
                (
                    day,
                    rule.start,
                    rule.end,
                    rule.classroom,
                    frozenset(rule.students),
                    tutor,
                )
            )
    for day in changes.cohort_dates:
        if day != changes.cancelled_day:
            slots.append(
                (
                    day,
                    changes.cohort_start,
                    changes.cohort_end,
                    AULA_2,
                    frozenset({"S", "A"}),
                    "tutor2",
                )
            )
    return slots


def _free(
    slots: list[Slot], probe: Slot, *, student: str | None, tutor: str | None
) -> bool:
    for slot in slots:
        if not _overlap(slot, probe):
            continue
        if student is not None and student in slot[4]:
            return False
        if tutor is not None and slot[5] == tutor:
            return False
    return True


@pytest.fixture(params=TODAYS, ids=lambda d: f"{d:%a-%Y-%m-%d}")
def plans(request: pytest.FixtureRequest) -> tuple[DemoSchedulePlan, DemoChangesPlan]:
    return plan_demo_schedule(request.param), plan_demo_changes(request.param)


def test_no_student_tutor_or_room_overlaps(plans):
    slots = combined_slots(*plans)
    for i, a in enumerate(slots):
        for b in slots[i + 1 :]:
            if not _overlap(a, b):
                continue
            assert not (a[4] & b[4]), ("student", a, b)
            assert a[5] is None or a[5] != b[5], ("tutor", a, b)
            assert a[3] is None or a[3] != b[3], ("room", a, b)


def test_the_cohort_fits_the_hours_the_room_and_the_tutor(plans):
    schedule, changes = plans
    d = upcoming_weekdays(changes.today, 19)
    assert changes.cohort_dates == tuple(d[k - 1] for k in (2, 4, 7, 9, 12, 14, 17, 19))
    assert changes.cohort_starts_on == d[1]
    assert changes.cohort_end == _plus(changes.cohort_start, 90)
    assert len(changes.cohort_dates) == 8
    assert schedule.closure_day not in changes.cohort_dates
    assert schedule.closure_day.isoweekday() not in changes.cohort_weekdays
    assert changes.cancelled_day == d[6]
    assert changes.cancelled_day in changes.cohort_dates
    for day in changes.cohort_dates:
        weekday = day.isoweekday()
        assert weekday in changes.cohort_weekdays
        start, end = changes.cohort_start, changes.cohort_end
        assert _inside(OPERATING_HOURS.get(weekday), start, end), day
        windows = ROOM_WINDOWS[AULA_2]
        assert windows is not None
        assert _inside(windows.get(weekday), start, end), day
        assert _inside(TUTOR2_WINDOWS.get(weekday), start, end), day


def test_the_substitution_is_a_confirmed_series_date(plans):
    schedule, changes = plans
    dates = schedule.series.dates()
    assert changes.substitution_day in dates
    assert changes.substitution_day != schedule.closure_day
    assert changes.substitution_day == upcoming_weekdays(changes.today, 8)[7]
    assert (changes.substitution_start, changes.substitution_end) == (
        schedule.series.start,
        schedule.series.end,
    )
    weekday = changes.substitution_day.isoweekday()
    assert _inside(
        TUTOR2_WINDOWS.get(weekday),
        changes.substitution_start,
        changes.substitution_end,
    )


def test_the_session_request_slot_is_open(plans):
    schedule, changes = plans
    today = changes.today
    start = changes.request_start
    end = _plus(start, changes.request_minutes)
    assert _t(start).minute % GRID_MINUTES == 0
    assert (
        today + timedelta(days=2) <= changes.request_day <= today + timedelta(days=60)
    )
    weekday = changes.request_day.isoweekday()
    assert _inside(OPERATING_HOURS.get(weekday), start, end)
    probe: Slot = (changes.request_day, start, end, None, frozenset({"S"}), None)
    slots = combined_slots(schedule, changes)
    assert _free(slots, probe, student="S", tutor=None)
    # Only demo.tutor2 is qualified, and it is free and covered.
    assert QUALIFIED["Ciencias (Secundaria)"] == {"tutor2"}
    assert _free(slots, probe, student=None, tutor="tutor2")
    assert _inside(TUTOR2_WINDOWS.get(weekday), start, end)


def test_the_reschedule_slot_is_open(plans):
    schedule, changes = plans
    u1 = schedule.upcoming[0]
    assert (u1.key, u1.day, u1.start) == (
        "U1",
        changes.reschedule_day,
        changes.reschedule_from,
    )
    end = _plus(changes.reschedule_to, 60)
    probe: Slot = (
        changes.reschedule_day,
        changes.reschedule_to,
        end,
        None,
        frozenset(),
        None,
    )
    slots = [
        s
        for s in combined_slots(schedule, changes)
        if s[:3] != (u1.day, u1.start, u1.end)
    ]
    weekday = changes.reschedule_day.isoweekday()
    assert _inside(OPERATING_HOURS.get(weekday), changes.reschedule_to, end)
    assert _free(slots, probe, student="S", tutor=None)
    windows = {"tutor": TUTOR_WINDOWS, "tutor2": TUTOR2_WINDOWS}
    assert any(
        _free(slots, probe, student=None, tutor=tutor)
        and _inside(windows[tutor].get(weekday), changes.reschedule_to, end)
        and changes.reschedule_day != schedule.time_off_day
        for tutor in QUALIFIED["Matemáticas (Secundaria)"]
    )


def test_the_time_off_hits_a_group_occurrence(plans):
    schedule, changes = plans
    assert changes.time_off_day in schedule.group.dates()
    assert changes.time_off_day.isoweekday() == 6
    assert changes.time_off_day != schedule.time_off_day


def test_every_303_date_is_in_the_coming_four_weeks(plans):
    _, changes = plans
    first = changes.today + timedelta(days=2)
    last = changes.today + timedelta(days=28)
    days = [
        *changes.cohort_dates,
        changes.cohort_starts_on,
        changes.cancelled_day,
        changes.substitution_day,
        changes.request_day,
        changes.reschedule_day,
        changes.time_off_day,
    ]
    for day in days:
        assert first <= day <= last, day
