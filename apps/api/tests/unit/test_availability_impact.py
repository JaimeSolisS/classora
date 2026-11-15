"""The pure rules of M4-18 (#63 I2): which sessions a change affects (B1),
the request priority (C2), the open reassignment flag (E1), and the
revision's copy of the CHECKs."""

import importlib
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.modules.scheduling.reassignment_rules import is_flag_open
from app.modules.staff.availability import (
    DatedException,
    TutorAvailabilityRules,
    WeeklyWindow,
)
from app.modules.staff.availability_impact import change_priority, is_affected
from app.modules.staff.models import CHANGE_REQUEST_CHECKS

pytestmark = pytest.mark.unit

MX = "America/Mexico_City"
NOW = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)
MONDAY = date(2031, 1, 6)
MONDAY_8_20 = WeeklyWindow(1, time(8), time(20))


def at(day: date, hhmm: str, zone: str = MX) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=ZoneInfo(zone))


def rules(
    *windows: WeeklyWindow, exceptions: tuple[DatedException, ...] = ()
) -> TutorAvailabilityRules:
    return TutorAvailabilityRules(windows=tuple(windows), exceptions=exceptions)


def affected(
    start: datetime,
    current: TutorAvailabilityRules,
    proposed: TutorAvailabilityRules,
    *,
    state: str = "confirmed",
    minutes: int = 60,
    zone: str = MX,
) -> bool:
    return is_affected(
        state=state,
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
        now=NOW,
        zone=zone,
        current=current,
        proposed=proposed,
    )


CURRENT = rules(MONDAY_8_20)
WITHOUT_16_18 = rules(
    WeeklyWindow(1, time(8), time(16)), WeeklyWindow(1, time(18), time(20))
)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("offered", True),
        ("confirmed", True),
        ("unassigned", False),
        ("completed", False),
        ("cancelled", False),
        ("skipped_closure", False),
        ("conflict", False),
    ],
)
def test_only_offered_and_confirmed_sessions_are_affected(state, expected):
    assert (
        affected(at(MONDAY, "16:00"), CURRENT, WITHOUT_16_18, state=state) is expected
    )


def test_past_and_started_sessions_are_never_affected():
    started = NOW - timedelta(minutes=30)
    assert not affected(started, rules(WeeklyWindow(3, time(0), time(23, 59))), rules())
    assert not affected(at(date(2030, 12, 30), "16:00"), CURRENT, WITHOUT_16_18)
    # Starting exactly now counts as started.
    assert not is_affected(
        state="confirmed",
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        now=NOW,
        zone="UTC",
        current=rules(WeeklyWindow(3, time(0), time(23, 59))),
        proposed=rules(),
    )


def test_a_session_outside_the_current_rules_is_never_affected():
    # Booked with a tutor_unavailable override (#49): Tuesday has no window.
    tuesday = MONDAY + timedelta(days=1)
    assert not affected(at(tuesday, "10:00"), CURRENT, rules())


def test_partial_overlap_is_affected_and_touching_is_not():
    assert affected(at(MONDAY, "15:30"), CURRENT, WITHOUT_16_18)
    assert affected(at(MONDAY, "17:30"), CURRENT, WITHOUT_16_18)
    assert not affected(at(MONDAY, "15:00"), CURRENT, WITHOUT_16_18)  # ends 16:00
    assert not affected(at(MONDAY, "18:00"), CURRENT, WITHOUT_16_18)  # starts 18:00


def test_removing_and_re_adding_the_same_time_affects_nothing():
    split = rules(
        WeeklyWindow(1, time(8), time(17)), WeeklyWindow(1, time(17), time(20))
    )
    assert not affected(at(MONDAY, "16:30"), CURRENT, split)


def test_time_off_over_several_days_and_hours():
    off = DatedException("unavailable", MONDAY, MONDAY + timedelta(days=8))
    with_off = rules(MONDAY_8_20, exceptions=(off,))
    assert affected(at(MONDAY, "10:00"), CURRENT, with_off)
    assert affected(at(MONDAY + timedelta(weeks=1), "10:00"), CURRENT, with_off)
    assert not affected(at(MONDAY + timedelta(weeks=2), "10:00"), CURRENT, with_off)
    hours = DatedException("unavailable", MONDAY, MONDAY, time(9), time(10))
    with_hours = rules(MONDAY_8_20, exceptions=(hours,))
    assert affected(at(MONDAY, "09:30"), CURRENT, with_hours)
    assert not affected(at(MONDAY, "10:00"), CURRENT, with_hours)


def test_deleting_extra_hours_affects_the_sessions_they_covered():
    tuesday = MONDAY + timedelta(days=1)
    extra = DatedException("available", tuesday, tuesday, time(10), time(12))
    with_extra = rules(MONDAY_8_20, exceptions=(extra,))
    assert affected(at(tuesday, "10:00"), with_extra, CURRENT)
    assert not affected(at(tuesday, "12:00"), with_extra, CURRENT)


def test_center_closures_are_not_part_of_the_rule():
    # The rules carry no closures: a session on a closed date is judged by
    # the tutor's windows and exceptions only.
    christmas = date(2031, 12, 22)  # a Monday
    assert affected(at(christmas, "16:00"), CURRENT, WITHOUT_16_18)


def test_sessions_are_split_into_local_pieces():
    # America/Tijuana falls back on 2031-11-02: 09:00Z is 01:00 PST, the
    # repeated hour's second pass; springs forward on 2031-03-09.
    sunday_1_2 = rules(WeeklyWindow(7, time(1), time(2)))
    sunday_0_4 = rules(WeeklyWindow(7, time(0), time(4)))
    fall_back = datetime(2031, 11, 2, 9, 0, tzinfo=UTC)
    assert not affected(fall_back, sunday_0_4, sunday_1_2, zone="America/Tijuana")
    assert affected(
        fall_back,
        sunday_0_4,
        rules(WeeklyWindow(7, time(2), time(4))),
        zone="America/Tijuana",
    )
    # 01:30 PST to 03:30 PDT: one real hour, one local piece 01:30-03:30.
    spring = datetime(2031, 3, 9, 9, 30, tzinfo=UTC)
    assert affected(spring, sunday_0_4, sunday_1_2, zone="America/Tijuana")
    assert not affected(
        spring,
        sunday_0_4,
        rules(WeeklyWindow(7, time(1), time(3, 30))),
        zone="America/Tijuana",
    )


def test_priority_is_high_within_24_hours():
    assert change_priority([NOW + timedelta(hours=24)], NOW) == "high"
    assert change_priority([NOW + timedelta(hours=24, minutes=1)], NOW) == "normal"
    assert (
        change_priority([NOW + timedelta(days=3), NOW + timedelta(hours=2)], NOW)
        == "high"
    )
    assert change_priority([], NOW) == "normal"


TUTOR = uuid.uuid4()
OTHER = uuid.uuid4()
LATER = NOW + timedelta(days=1)


@pytest.mark.parametrize(
    ("state", "session_tutor", "starts_at", "expected"),
    [
        ("confirmed", TUTOR, LATER, True),
        ("offered", TUTOR, LATER, True),
        ("unassigned", None, LATER, True),
        ("offered", OTHER, LATER, False),
        ("confirmed", OTHER, LATER, False),
        ("cancelled", TUTOR, LATER, False),
        ("completed", TUTOR, LATER, False),
        ("conflict", None, LATER, False),
        ("skipped_closure", None, LATER, False),
        ("confirmed", TUTOR, NOW, False),
        ("unassigned", None, NOW - timedelta(hours=1), False),
    ],
)
def test_the_open_flag_rule(state, session_tutor, starts_at, expected):
    assert (
        is_flag_open(
            flag_tutor_user_id=TUTOR,
            state=state,
            session_tutor_user_id=session_tutor,
            starts_at=starts_at,
            now=NOW,
        )
        is expected
    )


def test_the_revision_copies_the_change_request_checks():
    revision = importlib.import_module(
        "migrations.versions.0045_availability_change_requests"
    )
    assert revision.REQUEST_CHECKS == CHANGE_REQUEST_CHECKS
