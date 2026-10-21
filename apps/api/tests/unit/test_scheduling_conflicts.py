"""Scheduling conflict codes and pure rules (#48 B3, D5, D8; ADR-0005 D1)."""

import re
from datetime import time
from pathlib import Path
from typing import get_args

import pytest

from app.modules.scheduling.conflicts import (
    CODE_ORDER,
    HARD,
    SOFT,
    WARNING,
    Conflict,
    covers_operating_hours,
    severity_of,
    sort_conflicts,
    tutor_unavailable_reasons,
)
from app.modules.scheduling.schemas import SessionKindCode, SessionStateCode
from app.modules.scheduling.states import SessionKind, SessionState

pytestmark = pytest.mark.unit

ADR = (
    Path(__file__).resolve().parents[4]
    / "_docs/adr/0005-scheduling-invariants-overrides-and-recurrence.md"
)


def _adr_codes(row_label: str) -> list[str]:
    """The codes of the ADR-0005 invariants table rows labelled ``row_label``,
    in table order."""
    codes: list[str] = []
    for line in ADR.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| **{row_label}** |"):
            rules = line.split("|")[2]
            codes += re.findall(r"`([a-z_]+)`", rules)
    return codes


# --- B3: the sets match the ADR table (drift guard) ----------------------------------


def test_the_code_sets_match_the_adr_table():
    assert set(_adr_codes("Hard")) == HARD
    assert set(_adr_codes("Soft")) == SOFT
    assert set(_adr_codes("Warning")) == WARNING
    assert HARD == {
        "student_overlap",
        "tutor_overlap",
        "room_overlap",
        "room_capacity_exceeded",
    }
    assert SOFT == {
        "tutor_unavailable",
        "room_unavailable",
        "center_closed",
        "outside_operating_hours",
        "tutor_not_qualified",
        "group_capacity_exceeded",
        "notice_window",
    }
    assert WARNING == {
        "daily_limit_exceeded",
        "weekly_limit_exceeded",
        "break_too_short",
    }


def test_the_code_order_is_the_adr_table_order():
    assert list(CODE_ORDER) == _adr_codes("Hard") + _adr_codes("Soft")
    assert not HARD & SOFT and not (HARD | SOFT) & WARNING


def test_severity_follows_the_code():
    assert severity_of("tutor_overlap") == "hard"
    assert severity_of("notice_window") == "soft"
    assert Conflict("center_closed", {"date": "2026-12-25"}).as_wire() == {
        "code": "center_closed",
        "severity": "soft",
        "details": {"date": "2026-12-25"},
    }
    with pytest.raises(ValueError, match="not a conflict code"):
        Conflict("daily_limit_exceeded")
    with pytest.raises(ValueError, match="not a conflict code"):
        severity_of("whatever")


def test_sort_puts_hard_first_then_table_order_then_details():
    conflicts = [
        Conflict("notice_window", {"booking_notice_hours": 24}),
        Conflict("tutor_unavailable", {"reasons": ["time_off"]}),
        Conflict("room_overlap", {"classroom_id": "b", "class_session_ids": []}),
        Conflict("student_overlap", {"student_id": "s", "class_session_ids": []}),
        Conflict("room_overlap", {"classroom_id": "a", "class_session_ids": []}),
        Conflict("center_closed", {"date": "2026-12-25"}),
        Conflict("tutor_overlap", {"class_session_ids": []}),
    ]

    ordered = sort_conflicts(conflicts)

    assert [(c.code, c.details.get("classroom_id")) for c in ordered] == [
        ("tutor_overlap", None),
        ("room_overlap", "a"),
        ("room_overlap", "b"),
        ("student_overlap", None),
        ("tutor_unavailable", None),
        ("center_closed", None),
        ("notice_window", None),
    ]
    assert sort_conflicts(reversed(conflicts)) == ordered


# --- D8: operating hours -------------------------------------------------------------

HOURS = [(1, time(8), time(20)), (6, time(9), time(14))]


@pytest.mark.parametrize(
    ("weekday", "start", "end", "covered"),
    [
        (1, time(8), time(20), True),  # touching both bounds
        (1, time(7, 59), time(9), False),
        (1, time(19), time(20, 1), False),
        (6, time(13, 30), time(14, 30), False),  # Saturday afternoon
        (6, time(13), time(14), True),
        (7, time(10), time(11), False),  # Sunday: not listed, closed
        (1, time(19), time.max, False),  # a local end at midnight
    ],
)
def test_covers_operating_hours(weekday, start, end, covered):
    assert covers_operating_hours(HOURS, weekday, start, end) is covered


def test_a_day_open_until_23_59_still_never_covers_a_midnight_end():
    hours = [(2, time(0), time(23, 59))]

    assert covers_operating_hours(hours, 2, time(23), time(23, 59))
    assert not covers_operating_hours(hours, 2, time(23), time.max)


# --- D5: a closure is reported once --------------------------------------------------


@pytest.mark.parametrize(
    ("reasons", "kept"),
    [
        (("center_closed",), []),
        (("center_closed", "time_off"), ["time_off"]),
        (
            ("center_closed", "time_off", "outside_weekly_availability"),
            ["time_off", "outside_weekly_availability"],
        ),
        (("outside_weekly_availability",), ["outside_weekly_availability"]),
        ((), []),
    ],
)
def test_tutor_unavailable_drops_only_the_closure(reasons, kept):
    assert tutor_unavailable_reasons(reasons) == kept


# --- The wire literals follow the states ------------------------------------------


def test_the_detail_literals_match_the_kinds_and_states():
    assert set(get_args(SessionKindCode.__value__)) == {k.value for k in SessionKind}
    assert set(get_args(SessionStateCode.__value__)) == {s.value for s in SessionState}
