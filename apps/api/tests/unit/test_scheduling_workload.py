"""The pure workload warnings (#50 A, D1): daily and weekly limits, the
required break, the ISO week and the local date in the center zone."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.modules.scheduling.workload import (
    BREAK_MAX,
    WorkloadSession,
    WorkloadWarning,
    iso_week_start,
    sort_warnings,
    workload_range,
    workload_warnings,
)
from app.modules.staff.service import TeachingLimits

MX_ZONE = "America/Mexico_City"
MX = ZoneInfo(MX_ZONE)
TJ_ZONE = "America/Tijuana"
TJ = ZoneInfo(TJ_ZONE)
MONDAY = date(2031, 1, 6)


def at(day: date, hhmm: str, zone: ZoneInfo = MX) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=zone)


def held(
    day: date, hhmm: str, minutes: int = 60, zone: ZoneInfo = MX
) -> WorkloadSession:
    start = at(day, hhmm, zone)
    return WorkloadSession(
        uuid.uuid4(), start, start + timedelta(minutes=minutes), minutes
    )


def slot(day: date, hhmm: str, minutes: int = 60, zone: ZoneInfo = MX):
    start = at(day, hhmm, zone)
    return (start, start + timedelta(minutes=minutes))


def codes(warnings: list[WorkloadWarning]) -> list[str]:
    return [w.code for w in warnings]


# --- No limits, no tutor sessions ---------------------------------------------------


def test_no_limits_set_gives_nothing():
    sessions = [held(MONDAY, h) for h in ("08:00", "09:00", "11:00")]

    assert (
        workload_warnings(TeachingLimits(), sessions, slot(MONDAY, "10:00"), MX_ZONE)
        == []
    )


def test_a_warning_code_must_be_known():
    with pytest.raises(ValueError):
        WorkloadWarning("tutor_overlap")


# --- Daily --------------------------------------------------------------------------


def test_daily_exactly_at_the_limit_does_not_warn():
    limits = TeachingLimits(daily_limit_minutes=120)

    assert (
        workload_warnings(
            limits, [held(MONDAY, "08:00")], slot(MONDAY, "12:00"), MX_ZONE
        )
        == []
    )


def test_daily_one_minute_over_warns_with_the_details():
    limits = TeachingLimits(daily_limit_minutes=119)

    [warning] = workload_warnings(
        limits, [held(MONDAY, "08:00")], slot(MONDAY, "12:00"), MX_ZONE
    )

    assert warning.as_wire() == {
        "code": "daily_limit_exceeded",
        "details": {
            "date": "2031-01-06",
            "limit_minutes": 119,
            "scheduled_minutes": 120,
        },
    }


def test_daily_counts_only_the_candidates_local_date():
    limits = TeachingLimits(daily_limit_minutes=60)
    # 23:00-23:59 on Sunday and 00:00 on Tuesday are other local dates.
    others = [
        held(MONDAY - timedelta(days=1), "23:00", 59),
        held(MONDAY + timedelta(days=1), "00:00"),
    ]

    assert workload_warnings(limits, others, slot(MONDAY, "10:00"), MX_ZONE) == []


def test_the_candidate_alone_can_exceed_the_daily_limit():
    limits = TeachingLimits(daily_limit_minutes=45)

    assert codes(workload_warnings(limits, [], slot(MONDAY, "10:00"), MX_ZONE)) == [
        "daily_limit_exceeded"
    ]


# --- Weekly (ISO week) ----------------------------------------------------------------


def test_iso_week_start_is_the_monday():
    assert iso_week_start(MONDAY) == MONDAY
    assert iso_week_start(MONDAY + timedelta(days=6)) == MONDAY
    assert iso_week_start(MONDAY - timedelta(days=1)) == MONDAY - timedelta(days=7)


def test_a_sunday_and_a_monday_fall_in_different_iso_weeks():
    limits = TeachingLimits(weekly_limit_minutes=60)
    sunday_before = MONDAY - timedelta(days=1)

    # Sunday's session is in the previous week: the Monday candidate is fine.
    assert (
        workload_warnings(
            limits, [held(sunday_before, "10:00")], slot(MONDAY, "10:00"), MX_ZONE
        )
        == []
    )
    # The Sunday at the end of the same week counts.
    [warning] = workload_warnings(
        limits,
        [held(MONDAY + timedelta(days=6), "10:00")],
        slot(MONDAY, "10:00"),
        MX_ZONE,
    )
    assert warning.as_wire() == {
        "code": "weekly_limit_exceeded",
        "details": {
            "week_start": "2031-01-06",
            "limit_minutes": 60,
            "scheduled_minutes": 120,
        },
    }


def test_weekly_exactly_at_the_limit_does_not_warn():
    limits = TeachingLimits(weekly_limit_minutes=180)
    week = [held(MONDAY + timedelta(days=n), "10:00") for n in (1, 2)]

    assert workload_warnings(limits, week, slot(MONDAY, "10:00"), MX_ZONE) == []


# --- Break ----------------------------------------------------------------------------


def test_a_gap_equal_to_the_break_is_fine_and_one_minute_less_warns():
    limits = TeachingLimits(required_break_minutes=15)
    before = held(MONDAY, "08:45")  # ends 09:45

    assert workload_warnings(limits, [before], slot(MONDAY, "10:00"), MX_ZONE) == []
    [warning] = workload_warnings(limits, [before], slot(MONDAY, "09:59"), MX_ZONE)
    assert warning.as_wire() == {
        "code": "break_too_short",
        "details": {
            "class_session_id": str(before.class_session_id),
            "gap_minutes": 14,
            "required_break_minutes": 15,
        },
    }


def test_back_to_back_warns_when_a_break_is_required():
    limits = TeachingLimits(required_break_minutes=1)

    [warning] = workload_warnings(
        limits, [held(MONDAY, "09:00")], slot(MONDAY, "10:00"), MX_ZONE
    )

    assert warning.details["gap_minutes"] == 0


@pytest.mark.parametrize("required", [0, None])
def test_a_break_of_zero_or_null_never_warns(required):
    limits = TeachingLimits(required_break_minutes=required)

    assert (
        workload_warnings(
            limits,
            [held(MONDAY, "09:00"), held(MONDAY, "11:00")],
            slot(MONDAY, "10:00"),
            MX_ZONE,
        )
        == []
    )


def test_both_neighbours_warn_the_earlier_first():
    limits = TeachingLimits(required_break_minutes=30)
    before, after = held(MONDAY, "08:50"), held(MONDAY, "11:10")
    far = held(MONDAY, "07:00", 30)  # an earlier one, not the neighbour

    warnings = workload_warnings(
        limits, [after, far, before], slot(MONDAY, "10:00"), MX_ZONE
    )

    assert [
        (w.code, w.details["class_session_id"], w.details["gap_minutes"])
        for w in warnings
    ] == [
        ("break_too_short", str(before.class_session_id), 10),
        ("break_too_short", str(after.class_session_id), 10),
    ]


def test_the_gap_counts_across_midnight():
    limits = TeachingLimits(required_break_minutes=60)
    late = held(MONDAY - timedelta(days=1), "23:00", 45)  # ends 23:45 Sunday

    [warning] = workload_warnings(limits, [late], slot(MONDAY, "00:15"), MX_ZONE)

    assert warning.details["gap_minutes"] == 30


# --- The tuples, the order ------------------------------------------------------------


def test_plain_tuples_are_accepted():
    limits = TeachingLimits(daily_limit_minutes=60)
    start = at(MONDAY, "08:00")
    row = (uuid.uuid4(), start, start + timedelta(hours=1), 60)

    assert codes(workload_warnings(limits, [row], slot(MONDAY, "10:00"), MX_ZONE)) == [
        "daily_limit_exceeded"
    ]


def test_all_three_in_the_adr_order():
    limits = TeachingLimits(
        daily_limit_minutes=60, weekly_limit_minutes=60, required_break_minutes=15
    )

    warnings = workload_warnings(
        limits,
        [held(MONDAY, "11:00"), held(MONDAY, "09:00")],
        slot(MONDAY, "10:00"),
        MX_ZONE,
    )

    assert codes(warnings) == [
        "daily_limit_exceeded",
        "weekly_limit_exceeded",
        "break_too_short",
        "break_too_short",
    ]


def test_sort_warnings_is_by_code_and_keeps_the_neighbour_order():
    first = WorkloadWarning("break_too_short", {"class_session_id": "b"})
    second = WorkloadWarning("break_too_short", {"class_session_id": "a"})
    weekly = WorkloadWarning("weekly_limit_exceeded")
    daily = WorkloadWarning("daily_limit_exceeded")

    assert sort_warnings([first, weekly, second, daily]) == [
        daily,
        weekly,
        first,
        second,
    ]


# --- The local date in a DST zone ------------------------------------------------


def test_tijuana_dst_days_count_on_their_local_date():
    # 2026-03-08 (spring forward) and 2026-11-01 (fall back) in Tijuana.
    limits = TeachingLimits(daily_limit_minutes=60)
    spring = date(2026, 3, 8)
    # 23:00-23:59 local on the 7th is 07:00Z on the 8th: not the 8th locally.
    the_seventh = held(spring - timedelta(days=1), "23:00", 59, TJ)
    assert the_seventh.starts_at.astimezone(UTC).date() == spring
    assert (
        workload_warnings(limits, [the_seventh], slot(spring, "10:00", 60, TJ), TJ_ZONE)
        == []
    )

    fall = date(2026, 11, 1)
    # 01:30 local exists twice; fold=0 is the first. Both count for Nov 1.
    early = held(fall, "00:30", 30, TJ)
    [warning] = workload_warnings(limits, [early], slot(fall, "10:00", 60, TJ), TJ_ZONE)
    assert warning.details == {
        "date": "2026-11-01",
        "limit_minutes": 60,
        "scheduled_minutes": 90,
    }


def test_the_workload_range_covers_whole_iso_weeks_and_the_break():
    low, high = workload_range(
        MONDAY + timedelta(days=2), MONDAY + timedelta(days=8), MX_ZONE
    )

    assert low == at(MONDAY, "00:00") - timedelta(minutes=BREAK_MAX)
    assert high == at(MONDAY + timedelta(days=14), "00:00") + timedelta(
        minutes=BREAK_MAX
    )
