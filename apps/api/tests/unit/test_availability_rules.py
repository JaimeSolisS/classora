"""Tutor availability rules (#45 B1-B3), bodies (C2, C3) and edit policy:
pure functions, no I/O.

The time-zone expectations were derived with ``zoneinfo`` (the IANA data of
the ``tzdata`` package), not by hand: for example ``America/Mexico_City``
moved from UTC-6 to UTC-5 at 2022-04-03 08:00Z (02:00 local became 03:00)
and back at 2022-10-30 07:00Z (02:00 CDT became 01:00 CST), and has had no
DST since; ``America/Tijuana`` changes at 2026-03-08 10:00Z and 2026-11-01
09:00Z; ``Pacific/Kiritimati`` is UTC+14.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.authz import Principal
from app.modules.identity import permissions as identity
from app.modules.staff.availability import (
    END_OF_DAY,
    AvailabilityResult,
    DatedException,
    WeeklyWindow,
    evaluate,
    merge_ranges,
    merge_results,
    to_local_intervals,
    uncovered_ranges,
)
from app.modules.staff.permissions import can_edit_availability
from app.modules.staff.schemas import (
    AvailabilityExceptionCreate,
    WeeklyAvailabilityUpdate,
)

MONDAY = date(2026, 10, 19)
TUESDAY = MONDAY + timedelta(days=1)


def t(value: str) -> time:
    return time.fromisoformat(value)


def window(weekday: int, start: str, end: str) -> WeeklyWindow:
    return WeeklyWindow(weekday, t(start), t(end))


def off(
    starts_on: date,
    ends_on: date | None = None,
    start: str | None = None,
    end: str | None = None,
) -> DatedException:
    return DatedException(
        "unavailable",
        starts_on,
        ends_on or starts_on,
        t(start) if start else None,
        t(end) if end else None,
    )


def extra(starts_on: date, start: str, end: str) -> DatedException:
    return DatedException("available", starts_on, starts_on, t(start), t(end))


def check(
    windows: list[WeeklyWindow],
    start: str,
    end: str,
    *,
    exceptions: list[DatedException] | None = None,
    closed: bool = False,
    on: date = MONDAY,
) -> AvailabilityResult:
    return evaluate(windows, exceptions or [], closed, on, t(start), t(end))


AVAILABLE = AvailabilityResult(True, ())
OUTSIDE = AvailabilityResult(False, ("outside_weekly_availability",))
TIME_OFF = AvailabilityResult(False, ("time_off",))

AFTERNOON = [window(1, "16:00", "20:00")]


# --- B1 evaluate -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("17:00", "18:00", AVAILABLE),  # inside a window
        ("16:00", "20:00", AVAILABLE),  # exactly the window
        ("16:00", "20:01", OUTSIDE),  # one minute over
        ("15:59", "17:00", OUTSIDE),  # one minute early
        ("20:00", "21:00", OUTSIDE),  # after it (touching)
    ],
)
def test_a_single_window(start, end, expected):
    assert check(AFTERNOON, start, end) == expected


def test_touching_windows_join_and_a_gap_does_not():
    touching = [window(1, "09:00", "12:00"), window(1, "12:00", "14:00")]
    gap = [window(1, "09:00", "12:00"), window(1, "12:30", "14:00")]

    assert check(touching, "11:00", "13:00") == AVAILABLE
    assert check(touching, "09:00", "14:00") == AVAILABLE
    assert check(gap, "11:00", "13:00") == OUTSIDE
    assert check(gap, "12:30", "13:00") == AVAILABLE


def test_only_the_dates_weekday_counts():
    assert check(AFTERNOON, "17:00", "18:00", on=TUESDAY) == OUTSIDE
    assert check([window(2, "16:00", "20:00")], "17:00", "18:00", on=TUESDAY) == (
        AVAILABLE
    )


def test_a_tutor_without_windows_is_never_available():
    assert check([], "09:00", "10:00") == OUTSIDE


def test_whole_day_time_off_blocks_the_day():
    exceptions = [off(MONDAY - timedelta(days=2), MONDAY + timedelta(days=2))]

    assert check(AFTERNOON, "17:00", "18:00", exceptions=exceptions) == TIME_OFF
    # Outside the range: not blocked.
    later = MONDAY + timedelta(days=7)
    assert check(AFTERNOON, "17:00", "18:00", exceptions=exceptions, on=later) == (
        AVAILABLE
    )


def test_partial_time_off_blocks_only_overlapping_hours():
    exceptions = [off(MONDAY, start="17:00", end="18:00")]

    assert check(AFTERNOON, "17:30", "19:00", exceptions=exceptions) == TIME_OFF
    assert check(AFTERNOON, "16:00", "17:30", exceptions=exceptions) == TIME_OFF
    # Adjacent ranges don't overlap.
    assert check(AFTERNOON, "18:00", "19:00", exceptions=exceptions) == AVAILABLE
    assert check(AFTERNOON, "16:00", "17:00", exceptions=exceptions) == AVAILABLE


def test_adjacent_time_off_does_not_block():
    windows = [window(1, "09:00", "18:00")]
    exceptions = [off(MONDAY, start="12:00", end="13:00")]

    assert check(windows, "13:00", "14:00", exceptions=exceptions) == AVAILABLE
    assert check(windows, "11:00", "12:00", exceptions=exceptions) == AVAILABLE
    assert check(windows, "12:59", "14:00", exceptions=exceptions) == TIME_OFF


def test_extra_availability_on_a_day_without_windows():
    exceptions = [extra(TUESDAY, "10:00", "12:00")]

    assert check(AFTERNOON, "10:00", "12:00", exceptions=exceptions, on=TUESDAY) == (
        AVAILABLE
    )
    assert check(AFTERNOON, "11:00", "12:30", exceptions=exceptions, on=TUESDAY) == (
        OUTSIDE
    )
    # Only on its dates.
    assert check(AFTERNOON, "10:00", "12:00", exceptions=exceptions) == OUTSIDE


def test_extra_availability_joins_the_weekly_windows():
    exceptions = [extra(MONDAY, "14:00", "16:00")]

    assert check(AFTERNOON, "15:00", "17:00", exceptions=exceptions) == AVAILABLE


def test_unavailable_wins_over_available_on_the_same_hours():
    exceptions = [
        extra(MONDAY, "10:00", "12:00"),
        off(MONDAY, start="10:00", end="12:00"),
    ]

    assert check([], "10:00", "11:00", exceptions=exceptions) == TIME_OFF
    whole_day = [extra(MONDAY, "10:00", "12:00"), off(MONDAY)]
    assert check([], "10:00", "11:00", exceptions=whole_day) == TIME_OFF


def test_a_closure_wins_over_everything_and_every_reason_is_returned():
    assert check(AFTERNOON, "17:00", "18:00", closed=True) == AvailabilityResult(
        False, ("center_closed",)
    )
    assert check(
        AFTERNOON, "17:00", "18:00", closed=True, exceptions=[off(MONDAY)]
    ) == AvailabilityResult(False, ("center_closed", "time_off"))
    assert check(
        [], "09:00", "10:00", closed=True, exceptions=[off(MONDAY)]
    ) == AvailabilityResult(
        False, ("center_closed", "time_off", "outside_weekly_availability")
    )


@pytest.mark.parametrize(("start", "end"), [("10:00", "10:00"), ("11:00", "10:00")])
def test_an_empty_or_reversed_interval_raises(start, end):
    with pytest.raises(ValueError, match="start must be before end"):
        check(AFTERNOON, start, end)


def test_merge_ranges_joins_touching_and_overlapping_ranges():
    assert merge_ranges(
        [(t("12:00"), t("14:00")), (t("09:00"), t("12:00")), (t("15:00"), t("16:00"))]
    ) == [(t("09:00"), t("14:00")), (t("15:00"), t("16:00"))]


def test_merge_results_keeps_the_reason_order_without_duplicates():
    merged = merge_results(
        [
            AvailabilityResult(False, ("outside_weekly_availability",)),
            AVAILABLE,
            AvailabilityResult(False, ("center_closed", "outside_weekly_availability")),
        ]
    )

    assert merged == AvailabilityResult(
        False, ("center_closed", "outside_weekly_availability")
    )
    assert merge_results([AVAILABLE, AVAILABLE]) == AVAILABLE


# --- B2 to_local_intervals ---------------------------------------------------------


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def test_an_interval_across_local_midnight_gives_two_pieces():
    assert to_local_intervals(
        utc("2026-10-19T05:30"), utc("2026-10-19T07:00"), "America/Mexico_City"
    ) == [
        (date(2026, 10, 18), t("23:30"), END_OF_DAY),
        (date(2026, 10, 19), t("00:00"), t("01:00")),
    ]


def test_an_interval_ending_at_local_midnight_ends_at_end_of_day():
    assert to_local_intervals(
        utc("2026-10-19T05:00"), utc("2026-10-19T06:00"), "America/Mexico_City"
    ) == [(date(2026, 10, 18), t("23:00"), END_OF_DAY)]


def test_dst_start_in_mexico_city_keeps_one_piece_across_the_gap():
    # 2022-04-03: 02:00 local didn't exist (clocks went to 03:00).
    starts_at, ends_at = utc("2022-04-03T07:30"), utc("2022-04-03T08:30")
    zone = ZoneInfo("America/Mexico_City")
    assert starts_at.astimezone(zone).utcoffset() == timedelta(hours=-6)
    assert ends_at.astimezone(zone).utcoffset() == timedelta(hours=-5)

    assert to_local_intervals(starts_at, ends_at, "America/Mexico_City") == [
        (date(2022, 4, 3), t("01:30"), t("03:30"))
    ]
    assert ends_at - starts_at == timedelta(hours=1)


def test_dst_end_in_mexico_city_checks_the_repeated_hour_twice():
    # 2022-10-30: 01:00-02:00 local happened twice (CDT, then CST).
    starts_at, ends_at = utc("2022-10-30T06:00"), utc("2022-10-30T08:00")
    assert starts_at.astimezone(ZoneInfo("America/Mexico_City")).hour == 1
    assert ends_at.astimezone(ZoneInfo("America/Mexico_City")).hour == 2

    assert to_local_intervals(starts_at, ends_at, "America/Mexico_City") == [
        (date(2022, 10, 30), t("01:00"), t("02:00")),
        (date(2022, 10, 30), t("01:00"), t("02:00")),
    ]
    assert to_local_intervals(
        utc("2022-10-30T06:15"), utc("2022-10-30T07:45"), "America/Mexico_City"
    ) == [
        (date(2022, 10, 30), t("01:15"), t("02:00")),
        (date(2022, 10, 30), t("01:00"), t("01:45")),
    ]


def test_tijuana_dst_start_and_end_2026():
    assert to_local_intervals(
        utc("2026-03-08T09:30"), utc("2026-03-08T10:30"), "America/Tijuana"
    ) == [(date(2026, 3, 8), t("01:30"), t("03:30"))]
    assert to_local_intervals(
        utc("2026-11-01T08:30"), utc("2026-11-01T10:00"), "America/Tijuana"
    ) == [
        (date(2026, 11, 1), t("01:30"), t("02:00")),
        (date(2026, 11, 1), t("01:00"), t("02:00")),
    ]


def test_kiritimati_shifts_the_local_date():
    # 09:00Z on Monday is 23:00 Monday at UTC+14; 10:30Z is Tuesday 00:30.
    assert to_local_intervals(
        utc("2026-10-19T09:00"), utc("2026-10-19T10:30"), "Pacific/Kiritimati"
    ) == [
        (MONDAY, t("23:00"), END_OF_DAY),
        (TUESDAY, t("00:00"), t("00:30")),
    ]
    assert to_local_intervals(
        utc("2026-10-19T11:00"), utc("2026-10-19T12:00"), "Pacific/Kiritimati"
    ) == [(TUESDAY, t("01:00"), t("02:00"))]


def test_any_aware_offset_is_accepted_and_long_intervals_split_per_day():
    six_hours_west = timezone(timedelta(hours=-6))
    assert to_local_intervals(
        datetime(2026, 10, 19, 9, tzinfo=six_hours_west),
        datetime(2026, 10, 21, 10, tzinfo=six_hours_west),
        "UTC",
    ) == [
        (MONDAY, t("15:00"), END_OF_DAY),
        (TUESDAY, t("00:00"), END_OF_DAY),
        (date(2026, 10, 21), t("00:00"), t("16:00")),
    ]


def test_naive_or_reversed_instants_raise():
    with pytest.raises(ValueError, match="timezone-aware"):
        to_local_intervals(
            datetime(2026, 10, 19, 9), utc("2026-10-19T10:00"), "America/Mexico_City"
        )
    with pytest.raises(ValueError, match="before"):
        to_local_intervals(
            utc("2026-10-19T10:00"), utc("2026-10-19T10:00"), "America/Mexico_City"
        )


def test_the_same_weekly_window_gives_the_same_answer_across_a_dst_change():
    # Tijuana: Monday 16:00-17:00 local is 23:00Z-00:00Z before 2026-11-01
    # (UTC-7) and 00:00Z-01:00Z after it (UTC-8); a Monday 16:00-20:00
    # window covers both.
    windows = [window(1, "16:00", "20:00")]
    zone = "America/Tijuana"
    before = to_local_intervals(utc("2026-10-26T23:00"), utc("2026-10-27T00:00"), zone)
    after = to_local_intervals(utc("2026-11-03T00:00"), utc("2026-11-03T01:00"), zone)

    assert before == [(date(2026, 10, 26), t("16:00"), t("17:00"))]
    assert after == [(date(2026, 11, 2), t("16:00"), t("17:00"))]
    for day, start, end in (*before, *after):
        assert evaluate(windows, [], False, day, start, end) == AVAILABLE


def test_a_piece_to_midnight_is_never_covered_by_a_window():
    # Windows end by 23:59, so "24:00" stays outside (no interval across
    # midnight is available in #45).
    windows = [window(1, "20:00", "23:59")]
    assert evaluate(windows, [], False, MONDAY, t("23:00"), END_OF_DAY) == OUTSIDE


# --- What a weekly change takes away -------------------------------------------------


def test_uncovered_ranges_is_the_time_the_change_removes():
    old = [window(1, "16:00", "20:00"), window(3, "16:00", "18:00")]

    assert uncovered_ranges(old, old) == []
    # Growing a window or adding one removes nothing.
    assert uncovered_ranges(old, [window(1, "15:00", "21:00"), *old[1:]]) == []
    # Splitting a window into touching ones removes nothing.
    split = [window(1, "16:00", "18:00"), window(1, "18:00", "20:00"), old[1]]
    assert uncovered_ranges(old, split) == []
    # Shrinking and dropping.
    assert uncovered_ranges(old, [window(1, "17:00", "19:00")]) == [
        window(1, "16:00", "17:00"),
        window(1, "19:00", "20:00"),
        window(3, "16:00", "18:00"),
    ]


# --- C2, C3 bodies -------------------------------------------------------------------


def test_the_weekly_body_reports_overlaps_on_the_later_item():
    with pytest.raises(ValidationError) as exc:
        WeeklyAvailabilityUpdate.model_validate(
            {
                "version": 0,
                "windows": [
                    {"weekday": 1, "starts_at": "16:00", "ends_at": "20:00"},
                    {"weekday": 1, "starts_at": "19:00", "ends_at": "21:00"},
                ],
            }
        )
    [error] = exc.value.errors()
    assert error["type"] == "window_overlap"
    assert error["loc"] == ("windows", 1, "starts_at")


def test_the_weekly_body_takes_at_most_42_windows_and_touching_ones():
    def windows(count: int) -> list[dict[str, object]]:
        return [
            {
                "weekday": 1 + index // 6,
                "starts_at": f"{8 + 2 * (index % 6):02d}:00",
                "ends_at": f"{10 + 2 * (index % 6):02d}:00",
            }
            for index in range(count)
        ]

    body = WeeklyAvailabilityUpdate.model_validate(
        {"version": 3, "windows": windows(42)}
    )
    assert len(body.windows) == 42
    with pytest.raises(ValidationError, match="at most 42"):
        WeeklyAvailabilityUpdate.model_validate(
            {"version": 3, "windows": [*windows(42), windows(1)[0] | {"weekday": 7}]}
        )


@pytest.mark.parametrize(
    "body",
    [
        {"version": -1, "windows": []},
        {"windows": []},
        {"version": 0},
        {"version": 0, "windows": [], "extra": 1},
        {
            "version": 0,
            "windows": [{"weekday": 8, "starts_at": "09:00", "ends_at": "10:00"}],
        },
        {
            "version": 0,
            "windows": [{"weekday": 1, "starts_at": "9:00", "ends_at": "10:00"}],
        },
        {
            "version": 0,
            "windows": [{"weekday": 1, "starts_at": "10:00", "ends_at": "10:00"}],
        },
    ],
)
def test_invalid_weekly_bodies(body):
    with pytest.raises(ValidationError):
        WeeklyAvailabilityUpdate.model_validate(body)


def exception_body(**overrides: object) -> dict[str, object]:
    return {
        "kind": "unavailable",
        "starts_on": "2026-12-21",
        "ends_on": "2026-12-31",
        **overrides,
    }


def test_valid_exception_bodies():
    whole_day = AvailabilityExceptionCreate.model_validate(
        exception_body(note="  Vacaciones  ")
    )
    assert (whole_day.starts_at, whole_day.ends_at) == (None, None)
    assert whole_day.note == "Vacaciones"
    hours = AvailabilityExceptionCreate.model_validate(
        exception_body(kind="available", starts_at="10:00", ends_at="12:00", note=" ")
    )
    assert hours.note is None
    longest = AvailabilityExceptionCreate.model_validate(
        exception_body(starts_on="2027-01-01", ends_on="2028-01-02")
    )
    assert (longest.ends_on - longest.starts_on).days == 366


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"kind": "available"}, "starts_at"),
        ({"starts_at": "10:00"}, "ends_at"),
        ({"ends_at": "10:00"}, "ends_at"),
        ({"starts_at": "10:00", "ends_at": "10:00"}, "ends_at"),
        ({"starts_at": "11:00", "ends_at": "10:00"}, "ends_at"),
        ({"ends_on": "2026-12-20"}, "ends_on"),
        ({"starts_on": "2027-01-01", "ends_on": "2028-01-03"}, "ends_on"),
        ({"starts_on": "2026-12-21T00:00"}, "starts_on"),
        ({"kind": "busy"}, "kind"),
        ({"note": "x" * 201}, "note"),
        ({"starts_at": "24:00", "ends_at": "10:00"}, "starts_at"),
        ({"version": 1}, "version"),
    ],
)
def test_invalid_exception_bodies(overrides, field):
    with pytest.raises(ValidationError) as exc:
        AvailabilityExceptionCreate.model_validate(exception_body(**overrides))
    assert field in {error["loc"][0] for error in exc.value.errors()}


# --- Edit policy ---------------------------------------------------------------------

SELF = uuid.UUID("00000000-0000-4000-8000-00000000000a")
OTHER = uuid.UUID("00000000-0000-4000-8000-00000000000b")


def principal(*roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= identity.ROLE_PERMISSIONS[role]
    return Principal(
        user_id=SELF, roles=frozenset(roles), permissions=frozenset(permissions)
    )


@pytest.mark.parametrize(
    ("roles", "self_", "other"),
    [
        (("owner",), True, True),
        (("administrator",), True, True),
        (("academic_coordinator",), True, False),  # self: only if also a tutor
        (("tutor",), True, False),
        (("academic_coordinator", "tutor"), True, False),
        (("finance_staff",), False, False),
        (("guardian",), False, False),
        (("student",), False, False),
    ],
)
def test_only_the_tutor_or_tutor_manage_edit_availability(roles, self_, other):
    caller = principal(*roles)

    assert can_edit_availability(caller, SELF) is self_
    assert can_edit_availability(caller, OTHER) is other
