"""The pure group rules (#56 B4): the group maximum, the room capacity per
occurrence and the mid-term state filter."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.scheduling.groups_rules import (
    AFFECTED_STATES,
    CHECKED_STATES,
    GroupOccurrence,
    affected_occurrences,
    group_capacity_conflicts,
    is_affected,
    is_checked,
    room_capacity_conflicts,
)
from app.modules.scheduling.states import SessionState

pytestmark = pytest.mark.unit

NOW = datetime(2031, 1, 5, 18, 0, tzinfo=UTC)
GROUP = uuid.UUID("00000000-0000-4000-8000-000000000056")
ROOM = uuid.UUID("00000000-0000-4000-8000-000000000057")


def occurrence(
    *,
    state: str = "confirmed",
    starts_at: datetime = NOW + timedelta(days=1),
    room_capacity: int | None = 6,
) -> GroupOccurrence:
    return GroupOccurrence(
        class_session_id=uuid.uuid4(),
        state=state,
        starts_at=starts_at,
        local_date=date(2031, 1, 6),
        classroom_id=None if room_capacity is None else ROOM,
        room_capacity=room_capacity,
    )


# --- B1: the group maximum ------------------------------------------------------------


def test_at_the_maximum_passes_and_one_over_is_soft():
    assert group_capacity_conflicts(6, max_students=6, group_id=GROUP) == []

    [conflict] = group_capacity_conflicts(7, max_students=6, group_id=GROUP)

    assert conflict.code == "group_capacity_exceeded"
    assert conflict.severity == "soft"
    assert conflict.details == {
        "group_id": str(GROUP),
        "max_students": 6,
        "enrolled_count": 7,
    }


def test_an_empty_group_never_exceeds():
    assert group_capacity_conflicts(0, max_students=1, group_id=GROUP) == []


# --- B2: the room capacity ------------------------------------------------------------


def test_at_the_room_capacity_passes_and_one_over_is_hard():
    room = occurrence(room_capacity=6)

    assert room_capacity_conflicts([(room, 6)]) == []
    [conflict] = room_capacity_conflicts([(room, 7)])

    assert conflict.code == "room_capacity_exceeded"
    assert conflict.severity == "hard"
    assert conflict.details == {
        "class_session_id": str(room.class_session_id),
        "occurrence_start": "2031-01-06T18:00:00Z",
        "date": "2031-01-06",
        "classroom_id": str(ROOM),
        "room_capacity": 6,
        "participant_count": 7,
    }


def test_an_occurrence_without_a_room_has_no_room_limit():
    assert room_capacity_conflicts([(occurrence(room_capacity=None), 500)]) == []


@pytest.mark.parametrize(
    ("room_capacity", "count", "refused"),
    [(4, 5, True), (6, 6, False), (10, 7, False)],
    ids=["room_smaller_than_max", "room_equal", "room_larger"],
)
def test_the_room_limits_only_above_its_capacity(room_capacity, count, refused):
    found = room_capacity_conflicts([(occurrence(room_capacity=room_capacity), count)])

    assert bool(found) is refused


def test_each_occurrence_over_its_room_is_listed():
    small, large = occurrence(room_capacity=2), occurrence(room_capacity=10)

    found = room_capacity_conflicts([(small, 3), (large, 3), (small, 3)])

    assert [c.details["class_session_id"] for c in found] == [
        str(small.class_session_id)
    ] * 2


# --- B3: the mid-term state filter ----------------------------------------------------


@pytest.mark.parametrize("state", list(SessionState))
def test_the_affected_and_checked_states(state):
    later = NOW + timedelta(minutes=1)
    expected = state in {
        SessionState.UNASSIGNED,
        SessionState.OFFERED,
        SessionState.CONFIRMED,
        SessionState.CONFLICT,
        SessionState.SKIPPED_CLOSURE,
    }

    assert is_affected(state, later, NOW) is expected
    assert is_checked(state) is (
        state in {SessionState.UNASSIGNED, SessionState.OFFERED, SessionState.CONFIRMED}
    )


def test_the_sets():
    assert CHECKED_STATES < AFFECTED_STATES
    assert SessionState.COMPLETED not in AFFECTED_STATES
    assert SessionState.CANCELLED not in AFFECTED_STATES


def test_an_occurrence_starting_now_has_started():
    assert not is_affected("confirmed", NOW, NOW)
    assert not is_affected("confirmed", NOW - timedelta(minutes=1), NOW)
    assert is_affected("confirmed", NOW + timedelta(minutes=1), NOW)


def test_affected_occurrences_keeps_the_order_and_drops_the_rest():
    past = occurrence(starts_at=NOW - timedelta(days=1))
    started = occurrence(starts_at=NOW)
    done = occurrence(state="completed")
    cancelled = occurrence(state="cancelled")
    first = occurrence(state="conflict", starts_at=NOW + timedelta(days=1))
    second = occurrence(state="skipped_closure", starts_at=NOW + timedelta(days=2))
    third = occurrence(state="unassigned", starts_at=NOW + timedelta(days=3))

    kept = affected_occurrences(
        [past, started, first, done, second, cancelled, third], NOW
    )

    assert kept == [first, second, third]
