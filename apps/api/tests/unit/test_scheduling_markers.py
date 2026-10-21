"""The calendar markers (#57 A5, E3): codes, order and the pure rules."""

from datetime import UTC, date, datetime, timedelta
from typing import get_args

import pytest

from app.modules.scheduling import markers
from app.modules.scheduling.schemas import MarkerCode
from app.modules.scheduling.states import SessionState

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
DAY = date(2026, 11, 2)


def test_the_codes_match_the_wire_literal_in_order():
    assert markers.MARKER_ORDER == get_args(MarkerCode.__value__)
    assert markers.MARKER_ORDER == ("center_closed", "tutor_not_qualified")
    assert (markers.CENTER_CLOSED, markers.TUTOR_NOT_QUALIFIED) == markers.MARKER_ORDER


def test_the_marked_state_sets():
    assert {s.value for s in markers.CLOSURE_MARKED_STATES} == {
        "unassigned",
        "offered",
        "confirmed",
    }
    assert {s.value for s in markers.QUALIFICATION_MARKED_STATES} == {
        "offered",
        "confirmed",
    }


@pytest.mark.parametrize("state", list(SessionState))
def test_the_closure_marker_by_state(state):
    expected = state.value in {"unassigned", "offered", "confirmed"}

    assert markers.gets_closure_marker(state.value, DAY, frozenset({DAY})) is expected
    assert markers.gets_closure_marker(state.value, DAY, frozenset()) is False


@pytest.mark.parametrize("state", list(SessionState))
def test_the_qualification_check_by_state(state):
    expected = state.value in {"offered", "confirmed"}

    assert markers.needs_qualification_check(state.value, True, NOW, NOW) is expected


def test_the_qualification_check_needs_a_tutor_and_a_future_start():
    later = NOW + timedelta(minutes=1)
    earlier = NOW - timedelta(minutes=1)

    assert markers.needs_qualification_check("confirmed", True, later, NOW)
    assert markers.needs_qualification_check("confirmed", True, NOW, NOW)
    assert not markers.needs_qualification_check("confirmed", True, earlier, NOW)
    assert not markers.needs_qualification_check("confirmed", False, later, NOW)


@pytest.mark.parametrize(
    ("closed", "not_qualified", "expected"),
    [
        (False, False, []),
        (True, False, ["center_closed"]),
        (False, True, ["tutor_not_qualified"]),
        (True, True, ["center_closed", "tutor_not_qualified"]),
    ],
)
def test_markers_come_in_the_fixed_order(closed, not_qualified, expected):
    assert markers.markers_of(closed=closed, not_qualified=not_qualified) == expected
