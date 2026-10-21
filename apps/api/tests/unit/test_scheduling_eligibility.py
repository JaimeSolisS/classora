"""The workload ranking rule and the local week/day bounds (#51 C, H1)."""

import itertools
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.scheduling import eligibility
from app.modules.scheduling.eligibility import (
    Candidate,
    local_day_bounds,
    local_week_bounds,
    rank_candidates,
)

pytestmark = pytest.mark.unit

TIJUANA = "America/Tijuana"
MEXICO_CITY = "America/Mexico_City"
ID_A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
ID_B = uuid.UUID("00000000-0000-4000-8000-00000000000b")


def candidate(
    name: str = "Ana",
    *,
    weekly: int = 0,
    daily: int = 0,
    prefers: bool = False,
    user_id: uuid.UUID | None = None,
) -> Candidate:
    return Candidate(
        user_id=user_id or uuid.uuid4(),
        display_name=name,
        weekly_minutes=weekly,
        daily_minutes=daily,
        prefers_classroom=prefers,
    )


def names(candidates: list[Candidate]) -> list[str]:
    return [c.display_name for c in rank_candidates(candidates)]


# --- C2: each tie level ---------------------------------------------------------------


def test_weekly_minutes_ascending_first():
    found = names(
        [
            candidate("Heavy", weekly=300, daily=0, prefers=True),
            candidate("Light", weekly=60, daily=60),
            candidate("Middle", weekly=120),
        ]
    )

    assert found == ["Light", "Middle", "Heavy"]


def test_equal_weekly_daily_decides():
    found = names(
        [
            candidate("A", weekly=120, daily=90, prefers=True),
            candidate("B", weekly=120, daily=30),
        ]
    )

    assert found == ["B", "A"]


def test_equal_minutes_preferred_room_first():
    found = names(
        [
            candidate("Ana", weekly=60, daily=60),
            candidate("Zoe", weekly=60, daily=60, prefers=True),
        ]
    )

    assert found == ["Zoe", "Ana"]


def test_then_case_folded_name():
    found = names([candidate("beto"), candidate("Ana"), candidate("Carla")])

    assert found == ["Ana", "beto", "Carla"]


def test_then_user_id():
    found = rank_candidates(
        [candidate("Ana", user_id=ID_B), candidate("ana", user_id=ID_A)]
    )

    assert [c.user_id for c in found] == [ID_A, ID_B]


def test_the_order_is_stable_for_any_input_order():
    pool = [
        candidate("Ana", weekly=60, user_id=ID_A),
        candidate("ana", weekly=60, user_id=ID_B),
        candidate("Beto", weekly=60, daily=30),
        candidate("Carla", weekly=30, prefers=True),
        candidate("Dora", weekly=60, prefers=True),
    ]
    expected = rank_candidates(pool)

    for permutation in itertools.permutations(pool):
        assert rank_candidates(permutation) == expected
    assert [c.display_name for c in expected] == ["Carla", "Dora", "Ana", "ana", "Beto"]


def test_the_rule_is_documented():
    doc = eligibility.__doc__ or ""

    assert "weekly_minutes" in doc
    assert "daily_minutes" in doc
    assert "ISO week" in doc


# --- C1: local week and day bounds ----------------------------------------------------


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_week_of_a_monday_and_of_a_sunday():
    monday = date(2031, 1, 6)
    sunday = date(2031, 1, 12)
    expected = (utc(2031, 1, 6, 6), utc(2031, 1, 13, 6))

    assert local_week_bounds(monday, MEXICO_CITY) == expected
    assert local_week_bounds(sunday, MEXICO_CITY) == expected


def test_day_bounds():
    assert local_day_bounds(date(2031, 1, 12), MEXICO_CITY) == (
        utc(2031, 1, 12, 6),
        utc(2031, 1, 13, 6),
    )


def test_tijuana_spring_forward_week_has_167_hours():
    # DST starts Sunday 2026-03-08 02:00 in America/Tijuana.
    start, end = local_week_bounds(date(2026, 3, 4), TIJUANA)

    assert start == utc(2026, 3, 2, 8)  # Monday 00:00 PST
    assert end == utc(2026, 3, 9, 7)  # Monday 00:00 PDT
    assert end - start == timedelta(hours=167)
    day_start, day_end = local_day_bounds(date(2026, 3, 8), TIJUANA)
    assert day_end - day_start == timedelta(hours=23)


def test_tijuana_fall_back_week_has_169_hours():
    # DST ends Sunday 2026-11-01 02:00 in America/Tijuana.
    start, end = local_week_bounds(date(2026, 11, 1), TIJUANA)

    assert start == utc(2026, 10, 26, 7)  # Monday 00:00 PDT
    assert end == utc(2026, 11, 2, 8)  # Monday 00:00 PST
    assert end - start == timedelta(hours=169)
    day_start, day_end = local_day_bounds(date(2026, 11, 1), TIJUANA)
    assert day_end - day_start == timedelta(hours=25)
