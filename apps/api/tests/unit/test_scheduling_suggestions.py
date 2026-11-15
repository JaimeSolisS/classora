"""The pure slot search (#50 B3-B6, D2): the candidate grid, DST, the
in-memory conflict check per code, the ranking and the limit."""

import dataclasses
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.modules.administration.service import SchedulingPolicyValues
from app.modules.scheduling.suggestions import (
    Busy,
    Candidate,
    OperatingDay,
    SlotContext,
    candidate_slots,
    rank,
    search_slots,
    slot_conflicts,
)
from app.modules.scheduling.workload import WorkloadSession
from app.modules.staff.service import (
    DatedException,
    TeachingLimits,
    TutorAvailabilityRules,
    WeeklyWindow,
)

MX_ZONE = "America/Mexico_City"
MX = ZoneInfo(MX_ZONE)
TJ_ZONE = "America/Tijuana"
TJ = ZoneInfo(TJ_ZONE)
MONDAY = date(2031, 1, 6)
TUESDAY = MONDAY + timedelta(days=1)
SUNDAY = MONDAY + timedelta(days=6)
NOW = datetime(2030, 12, 1, 12, 0, tzinfo=UTC)

POLICY = SchedulingPolicyValues(
    id=uuid.uuid4(),
    effective_from=datetime(2000, 1, 1, tzinfo=UTC),
    booking_notice_hours=24,
    change_notice_hours=24,
    response_hours=24,
    makeup_after_on_time=True,
    makeup_after_late=False,
    makeup_after_no_show=False,
    makeup_window_days=30,
)
WEEKDAY_HOURS = tuple(OperatingDay(day, "08:00", "20:00") for day in range(1, 7))
ALL_DAY_RULES = TutorAvailabilityRules(
    windows=tuple(WeeklyWindow(day, time(0), time(23, 59)) for day in range(1, 8)),
    exceptions=(),
)


def at(day: date, hhmm: str, zone: ZoneInfo = MX) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=zone).astimezone(UTC)


def cand(day: date, hhmm: str, minutes: int = 60) -> Candidate:
    start = at(day, hhmm)
    return Candidate(start, start + timedelta(minutes=minutes))


def busy(day: date, hhmm: str, minutes: int = 60) -> Busy:
    start = at(day, hhmm)
    return Busy(uuid.uuid4(), start, start + timedelta(minutes=minutes))


def context(**changes: Any) -> SlotContext:
    """A context where everything passes: no sessions, a qualified tutor
    available all week, a room without windows, open 08:00-20:00 Mon-Sat."""
    base = SlotContext(
        zone=MX_ZONE,
        kind="individual",
        student_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        tutor_user_id=uuid.uuid4(),
        classroom_id=uuid.uuid4(),
        student_sessions=(),
        tutor_sessions=(),
        room_sessions=(),
        tutor_rules=ALL_DAY_RULES,
        tutor_qualified=True,
        limits=TeachingLimits(),
        room_windows=(),
        room_capacity=10,
        closed_dates=frozenset(),
        operating_hours=WEEKDAY_HOURS,
        policy=POLICY,
        now=NOW,
    )
    return dataclasses.replace(base, **changes)


def codes(candidate: Candidate, ctx: SlotContext) -> list[str]:
    return [c.code for c in slot_conflicts(candidate, ctx)]


def local(instant: datetime, zone: ZoneInfo = MX) -> str:
    return instant.astimezone(zone).strftime("%Y-%m-%d %H:%M")


# --- The grid -------------------------------------------------------------------------


def test_the_grid_is_every_quarter_hour_with_the_duration_kept():
    found = candidate_slots(MONDAY, MONDAY, MX_ZONE, 45, at(MONDAY, "10:07"))

    starts = [local(c.starts_at) for c in found]
    assert starts[:3] == ["2031-01-06 00:00", "2031-01-06 00:15", "2031-01-06 00:30"]
    assert {c.starts_at.astimezone(MX).minute for c in found} == {0, 15, 30, 45}
    assert all(c.ends_at - c.starts_at == timedelta(minutes=45) for c in found)
    # The last start keeps the session on the date: 23:15 ends at midnight.
    assert starts[-1] == "2031-01-06 23:15"
    assert len(found) == 94


def test_the_requested_start_is_never_a_candidate():
    found = candidate_slots(MONDAY, MONDAY, MX_ZONE, 60, at(MONDAY, "10:00"))

    assert at(MONDAY, "10:00") not in {c.starts_at for c in found}
    assert len(found) == 92  # 93 starts up to 23:00, minus the requested one


def test_every_date_of_the_window():
    found = candidate_slots(
        MONDAY, MONDAY + timedelta(days=13), MX_ZONE, 60, at(MONDAY, "10:07")
    )

    assert {c.starts_at.astimezone(MX).date() for c in found} == {
        MONDAY + timedelta(days=n) for n in range(14)
    }


def test_the_tijuana_spring_gap_is_skipped():
    gap_day = date(2026, 3, 8)

    found = candidate_slots(gap_day, gap_day, TJ_ZONE, 60, at(gap_day, "12:07", TJ))

    starts = [local(c.starts_at, TJ) for c in found]
    assert "2026-03-08 01:45" in starts
    assert not any(s.startswith("2026-03-08 02:") for s in starts)
    assert "2026-03-08 03:00" in starts


def test_the_tijuana_fall_back_hour_gives_one_fold_0_instant():
    fall = date(2026, 11, 1)

    found = candidate_slots(fall, fall, TJ_ZONE, 30, at(fall, "12:07", TJ))

    one_thirty = [c for c in found if local(c.starts_at, TJ) == "2026-11-01 01:30"]
    assert len(one_thirty) == 1
    # fold=0: the first 01:30, still in daylight time (UTC-7).
    assert one_thirty[0].starts_at == datetime(2026, 11, 1, 8, 30, tzinfo=UTC)


def test_a_candidate_ending_at_midnight_is_never_inside_the_hours():
    ctx = context(
        tutor_user_id=None,
        operating_hours=tuple(OperatingDay(d, "00:00", "23:59") for d in range(1, 8)),
    )

    assert codes(cand(MONDAY, "23:00"), ctx) == ["outside_operating_hours"]
    assert codes(cand(MONDAY, "22:45"), ctx) == []


# --- One code at a time ---------------------------------------------------------------


def test_a_clean_context_passes():
    assert slot_conflicts(cand(MONDAY, "10:00"), context()) == []


def test_student_overlap_excludes_only_the_overlapping_candidates():
    other = busy(MONDAY, "10:00")
    ctx = context(student_sessions=(other,))

    assert codes(cand(MONDAY, "09:15"), ctx) == ["student_overlap"]
    assert codes(cand(MONDAY, "10:45"), ctx) == ["student_overlap"]
    assert codes(cand(MONDAY, "09:00"), ctx) == []
    assert codes(cand(MONDAY, "11:00"), ctx) == []
    [conflict] = slot_conflicts(cand(MONDAY, "10:30"), ctx)
    assert conflict.details == {
        "student_id": str(ctx.student_id),
        "class_session_ids": [str(other.class_session_id)],
    }


def test_tutor_overlap():
    start = at(MONDAY, "10:00")
    held = WorkloadSession(uuid.uuid4(), start, start + timedelta(hours=1), 60)
    ctx = context(tutor_sessions=(held,))

    assert codes(cand(MONDAY, "10:30"), ctx) == ["tutor_overlap"]
    assert codes(cand(MONDAY, "11:00"), ctx) == []
    # Without a tutor the tutor's sessions don't matter.
    assert (
        codes(cand(MONDAY, "10:30"), dataclasses.replace(ctx, tutor_user_id=None)) == []
    )


def test_room_overlap():
    ctx = context(room_sessions=(busy(MONDAY, "10:00"),))

    assert codes(cand(MONDAY, "10:30"), ctx) == ["room_overlap"]
    assert (
        codes(cand(MONDAY, "10:30"), dataclasses.replace(ctx, classroom_id=None)) == []
    )


def test_room_capacity_is_never_exceeded_by_an_individual_session():
    assert codes(cand(MONDAY, "10:00"), context(room_capacity=1)) == []


def test_tutor_unavailable_outside_the_weekly_windows():
    rules = TutorAvailabilityRules(
        windows=(WeeklyWindow(1, time(16), time(20)),), exceptions=()
    )
    ctx = context(tutor_rules=rules)

    assert codes(cand(MONDAY, "15:30"), ctx) == ["tutor_unavailable"]
    assert codes(cand(MONDAY, "16:00"), ctx) == []
    assert codes(cand(TUESDAY, "16:00"), ctx) == ["tutor_unavailable"]
    [conflict] = slot_conflicts(cand(MONDAY, "15:30"), ctx)
    assert conflict.details == {"reasons": ["outside_weekly_availability"]}


def test_tutor_unavailable_on_time_off():
    rules = dataclasses.replace(
        ALL_DAY_RULES, exceptions=(DatedException("unavailable", TUESDAY, TUESDAY),)
    )
    ctx = context(tutor_rules=rules)

    assert codes(cand(TUESDAY, "10:00"), ctx) == ["tutor_unavailable"]
    assert codes(cand(MONDAY, "10:00"), ctx) == []


def test_room_unavailable_outside_its_windows():
    ctx = context(room_windows=((1, time(9), time(12)), (1, time(12), time(14))))

    assert codes(cand(MONDAY, "11:30"), ctx) == []  # touching windows join
    assert codes(cand(MONDAY, "13:30"), ctx) == ["room_unavailable"]
    assert codes(cand(TUESDAY, "10:00"), ctx) == ["room_unavailable"]
    assert (
        codes(cand(MONDAY, "13:30"), dataclasses.replace(ctx, classroom_id=None)) == []
    )


def test_center_closed_is_reported_once_and_not_as_a_tutor_reason():
    ctx = context(closed_dates=frozenset({TUESDAY}))

    assert codes(cand(TUESDAY, "10:00"), ctx) == ["center_closed"]
    assert codes(cand(MONDAY, "10:00"), ctx) == []


def test_outside_operating_hours_and_a_closed_weekday():
    ctx = context()

    assert codes(cand(MONDAY, "07:30"), ctx) == ["outside_operating_hours"]
    assert codes(cand(MONDAY, "19:00"), ctx) == []
    [conflict] = slot_conflicts(cand(SUNDAY, "10:00"), ctx)
    assert (conflict.code, conflict.details) == (
        "outside_operating_hours",
        {"date": SUNDAY.isoformat(), "opens_at": None, "closes_at": None},
    )


def test_tutor_not_qualified_is_listed_by_the_check():
    ctx = context(tutor_qualified=False)

    assert codes(cand(MONDAY, "10:00"), ctx) == ["tutor_not_qualified"]


def test_notice_window_exactly_the_notice_is_fine():
    now = at(MONDAY, "10:00") - timedelta(hours=24)
    ctx = context(now=now)

    assert codes(cand(MONDAY, "09:45"), ctx) == ["notice_window"]
    assert codes(cand(MONDAY, "10:00"), ctx) == []


def test_conflicts_are_sorted_hard_first():
    ctx = context(student_sessions=(busy(SUNDAY, "10:00"),), tutor_qualified=False)

    assert codes(cand(SUNDAY, "10:00"), ctx) == [
        "student_overlap",
        "outside_operating_hours",
        "tutor_not_qualified",
    ]


# --- Ranking, limit, warnings --------------------------------------------------------


def test_rank_is_by_distance_and_a_tie_goes_earlier():
    requested = at(MONDAY, "10:00")
    later, earlier, far = (
        cand(MONDAY, "10:30"),
        cand(MONDAY, "09:30"),
        cand(MONDAY, "08:00"),
    )

    assert rank([far, later, earlier], requested) == [earlier, later, far]


def test_search_returns_the_nearest_free_slots_up_to_the_limit():
    requested = at(MONDAY, "10:30")
    ctx = context(student_sessions=(busy(MONDAY, "10:00"),), tutor_user_id=None)
    found = candidate_slots(MONDAY, MONDAY + timedelta(days=6), MX_ZONE, 60, requested)

    slots = search_slots(ctx, found, requested, 3)

    assert [local(s.starts_at) for s in slots] == [
        "2031-01-06 11:00",
        "2031-01-06 11:15",
        "2031-01-06 11:30",
    ]
    assert all(s.warnings == () for s in slots)
    assert len(search_slots(ctx, found, requested, 1)) == 1


def test_tutor_not_qualified_short_circuits_to_nothing():
    ctx = context(tutor_qualified=False)
    found = candidate_slots(MONDAY, MONDAY, MX_ZONE, 60, at(MONDAY, "10:00"))

    assert search_slots(ctx, found, at(MONDAY, "10:00"), 5) == []


def test_each_slot_carries_its_warnings():
    start = at(MONDAY, "10:00")
    held = WorkloadSession(uuid.uuid4(), start, start + timedelta(hours=1), 60)
    ctx = context(tutor_sessions=(held,), limits=TeachingLimits(daily_limit_minutes=60))
    requested = at(MONDAY, "10:30")
    found = candidate_slots(MONDAY, TUESDAY, MX_ZONE, 60, requested)

    slots = search_slots(ctx, found, requested, 10)

    monday = [s for s in slots if s.starts_at.astimezone(MX).date() == MONDAY]
    assert monday and all(
        [w.code for w in s.warnings] == ["daily_limit_exceeded"] for s in monday
    )


@pytest.mark.parametrize("limit", [1, 5, 10])
def test_the_limit_caps_the_result(limit):
    requested = at(MONDAY, "10:00")
    found = candidate_slots(MONDAY, MONDAY, MX_ZONE, 60, requested)

    assert len(search_slots(context(), found, requested, limit)) == limit
