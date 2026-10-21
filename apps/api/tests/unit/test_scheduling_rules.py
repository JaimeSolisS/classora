"""Scheduling pure rules (#47 B4): state transitions, session time bounds and
effective capacity (ADR-0005 D2, D6, D7). No database."""

from datetime import UTC, datetime, timedelta
from itertools import product
from zoneinfo import ZoneInfo

import pytest

from app.modules.scheduling import service as scheduling
from app.modules.scheduling.service import (
    ALLOWED_TRANSITIONS,
    FINAL_STATES,
    INITIAL_STATES,
    ROOM_OCCUPYING_STATES,
    STUDENT_OCCUPYING_STATES,
    TUTOR_OCCUPYING_STATES,
    InvalidSessionTimesError,
    SessionKind,
    SessionState,
    can_transition,
    capacity_violations,
    effective_capacity,
    scheduled_minutes,
    validate_session_bounds,
)

S = SessionState

# ADR-0005 D2 plus D6, written out pair by pair (not derived from the table).
ALLOWED_PAIRS = {
    (S.UNASSIGNED, S.OFFERED),
    (S.UNASSIGNED, S.CANCELLED),
    (S.UNASSIGNED, S.SKIPPED_CLOSURE),
    (S.OFFERED, S.CONFIRMED),
    (S.OFFERED, S.UNASSIGNED),
    (S.OFFERED, S.CANCELLED),
    (S.OFFERED, S.SKIPPED_CLOSURE),
    (S.CONFIRMED, S.COMPLETED),
    (S.CONFIRMED, S.CANCELLED),
    (S.CONFIRMED, S.SKIPPED_CLOSURE),
    (S.CONFLICT, S.UNASSIGNED),
    (S.CONFLICT, S.OFFERED),
    (S.CONFLICT, S.CONFIRMED),
    (S.CONFLICT, S.CANCELLED),
    (S.SKIPPED_CLOSURE, S.UNASSIGNED),
    (S.SKIPPED_CLOSURE, S.OFFERED),
    (S.SKIPPED_CLOSURE, S.CONFIRMED),
    (S.SKIPPED_CLOSURE, S.CONFLICT),
}


# --- B1: states -----------------------------------------------------------------------


def test_the_states_and_kinds():
    assert {state.value for state in S} == {
        "unassigned",
        "offered",
        "confirmed",
        "completed",
        "cancelled",
        "skipped_closure",
        "conflict",
    }
    assert {kind.value for kind in SessionKind} == {"individual", "group", "course"}


def test_the_occupying_final_and_initial_sets():
    assert TUTOR_OCCUPYING_STATES == {S.OFFERED, S.CONFIRMED, S.COMPLETED}
    assert ROOM_OCCUPYING_STATES == {S.UNASSIGNED, S.OFFERED, S.CONFIRMED, S.COMPLETED}
    assert STUDENT_OCCUPYING_STATES == ROOM_OCCUPYING_STATES
    assert FINAL_STATES == {S.COMPLETED, S.CANCELLED}
    assert INITIAL_STATES == {
        S.UNASSIGNED,
        S.OFFERED,
        S.CONFIRMED,
        S.CONFLICT,
        S.SKIPPED_CLOSURE,
    }


@pytest.mark.parametrize(("source", "target"), list(product(S, S)))
def test_every_pair_of_states(source, target):
    allowed = (source, target) in ALLOWED_PAIRS

    assert can_transition(source, target) is allowed
    assert can_transition(source.value, target.value) is allowed
    assert (target in ALLOWED_TRANSITIONS[source]) is allowed


def test_the_table_covers_every_state_and_final_states_have_no_exits():
    assert set(ALLOWED_TRANSITIONS) == set(S)
    for state in FINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset()


@pytest.mark.parametrize(("source", "target"), [("draft", "offered"), ("offered", "x")])
def test_unknown_states_never_transition(source, target):
    assert can_transition(source, target) is False


# --- B2: time bounds ------------------------------------------------------------------

MX = "America/Mexico_City"
TIJUANA = "America/Tijuana"


def local(zone: str, *args: int, fold: int = 0) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo(zone), fold=fold)  # type: ignore[misc]


def reason_of(starts_at: datetime, ends_at: datetime, zone: str = MX) -> str:
    with pytest.raises(InvalidSessionTimesError) as caught:
        validate_session_bounds(starts_at, ends_at, zone)
    return caught.value.reason


START = local(MX, 2026, 3, 2, 10, 0)


@pytest.mark.parametrize("minutes", [15, 60, 480])
def test_15_to_480_minutes_pass(minutes):
    end = START + timedelta(minutes=minutes)

    validate_session_bounds(START, end, MX)
    assert scheduled_minutes(START, end) == minutes


@pytest.mark.parametrize("minutes", [14, 481])
def test_14_and_481_minutes_fail(minutes):
    assert (
        reason_of(START, START + timedelta(minutes=minutes)) == "duration_out_of_range"
    )


@pytest.mark.parametrize(
    "delta", [timedelta(seconds=30), timedelta(microseconds=1)], ids=["s", "us"]
)
def test_seconds_and_microseconds_fail(delta):
    end = START + timedelta(hours=1)

    assert reason_of(START + delta, end) == "not_whole_minutes"
    assert reason_of(START, end + delta) == "not_whole_minutes"


def test_naive_datetimes_fail():
    naive = datetime(2026, 3, 2, 10, 0)

    assert reason_of(naive, naive + timedelta(hours=1)) == "naive_datetime"
    assert reason_of(START, naive + timedelta(hours=1)) == "naive_datetime"


@pytest.mark.parametrize("minutes", [0, -60])
def test_an_end_not_after_the_start_fails(minutes):
    assert reason_of(START, START + timedelta(minutes=minutes)) == "ends_before_start"


def test_an_end_at_the_next_local_midnight_passes():
    validate_session_bounds(
        local(MX, 2026, 3, 2, 23, 0), local(MX, 2026, 3, 3, 0, 0), MX
    )


def test_a_session_across_local_midnight_fails():
    assert (
        reason_of(local(MX, 2026, 3, 2, 23, 30), local(MX, 2026, 3, 3, 0, 30))
        == "crosses_local_date"
    )


def test_tijuana_spring_forward_counts_real_minutes():
    # 2026-03-08 02:00 PST jumps to 03:00 PDT: 01:30-03:30 local is 60 minutes.
    start = local(TIJUANA, 2026, 3, 8, 1, 30)
    end = local(TIJUANA, 2026, 3, 8, 3, 30)

    validate_session_bounds(start, end, TIJUANA)
    assert scheduled_minutes(start, end) == 60
    # Python subtracts datetimes sharing a tzinfo in wall-clock time (2 h).
    assert end - start == timedelta(hours=2)


def test_tijuana_fall_back_counts_the_repeated_hour():
    # 2026-11-01 02:00 PDT goes back to 01:00 PST: 00:30-02:30 local is 180 minutes.
    start = local(TIJUANA, 2026, 11, 1, 0, 30)
    end = local(TIJUANA, 2026, 11, 1, 2, 30)

    validate_session_bounds(start, end, TIJUANA)
    assert scheduled_minutes(start, end) == 180
    # The first and the second 01:30 are an hour apart.
    first = local(TIJUANA, 2026, 11, 1, 1, 30, fold=0)
    second = local(TIJUANA, 2026, 11, 1, 1, 30, fold=1)
    assert scheduled_minutes(first, second.astimezone(UTC)) == 60


def test_kiritimati_uses_the_local_date_not_the_utc_date():
    zone = "Pacific/Kiritimati"  # UTC+14
    # 2026-03-02 09:00-10:00 local is 2026-03-01 19:00-20:00 UTC: same local date.
    start = local(zone, 2026, 3, 2, 9, 0).astimezone(UTC)
    end = local(zone, 2026, 3, 2, 10, 0).astimezone(UTC)
    assert start.date() != local(zone, 2026, 3, 2, 9, 0).date()

    validate_session_bounds(start, end, zone)
    # 2026-03-02 09:00-11:00 UTC is 23:00-01:00 local: it crosses the local date,
    # although both UTC instants share one date.
    utc_start = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    assert (
        reason_of(utc_start, utc_start + timedelta(hours=2), zone)
        == "crosses_local_date"
    )


# --- B3: capacity ---------------------------------------------------------------------


def test_an_individual_session_takes_one_student_and_has_no_capacity_codes():
    kind = SessionKind.INDIVIDUAL

    assert effective_capacity(kind, group_max=8, room_capacity=20) == 1
    assert effective_capacity(kind, group_max=None, room_capacity=None) == 1
    assert capacity_violations(kind, 3, group_max=None, room_capacity=1) == ()


@pytest.mark.parametrize("kind", [SessionKind.GROUP, SessionKind.COURSE])
@pytest.mark.parametrize(
    ("group_max", "room_capacity", "expected"),
    [
        (10, 6, 6),  # room smaller than the group
        (8, 8, 8),  # equal
        (6, 10, 6),  # room larger
        (6, None, 6),  # no room
        (None, 12, 12),  # no group maximum
        (None, None, None),  # neither: no limit
    ],
)
def test_effective_capacity_is_the_smallest_limit(
    kind, group_max, room_capacity, expected
):
    assert (
        effective_capacity(kind, group_max=group_max, room_capacity=room_capacity)
        == expected
    )


@pytest.mark.parametrize(
    ("count", "group_max", "room_capacity", "expected"),
    [
        (6, 10, 6, ()),
        (7, 10, 6, ("room_capacity_exceeded",)),
        (8, 8, 8, ()),
        (9, 8, 8, ("room_capacity_exceeded",)),
        (6, 6, 10, ()),
        (7, 6, 10, ("group_capacity_exceeded",)),
        (10, 6, 10, ("group_capacity_exceeded",)),
        (11, 6, 10, ("room_capacity_exceeded",)),
        (6, 6, None, ()),
        (7, 6, None, ("group_capacity_exceeded",)),
        (12, None, 12, ()),
        (13, None, 12, ("room_capacity_exceeded",)),
        (500, None, None, ()),
    ],
)
def test_capacity_violations(count, group_max, room_capacity, expected):
    assert (
        capacity_violations(
            SessionKind.GROUP, count, group_max=group_max, room_capacity=room_capacity
        )
        == expected
    )
    assert scheduling.ROOM_CAPACITY_EXCEEDED == "room_capacity_exceeded"
    assert scheduling.GROUP_CAPACITY_EXCEEDED == "group_capacity_exceeded"
