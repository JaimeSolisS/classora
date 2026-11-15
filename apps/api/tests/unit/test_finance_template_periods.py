"""The period grid of recurring expense templates (#116 B; ADR-0017 E17)."""

import random
from datetime import date, timedelta

import pytest

from app.modules.finance.template_periods import (
    FREQUENCIES,
    Frequency,
    first_on_or_after,
    next_after,
    period_start,
)


def _periods(starts_on: date, frequency: Frequency, count: int) -> list[date]:
    return [period_start(starts_on, frequency, k) for k in range(count)]


# --- B1: anchored on starts_on, day clamped -----


def test_b1_monthly_from_the_31st_clamps_and_comes_back():
    assert _periods(date(2027, 1, 31), "monthly", 5) == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
        date(2027, 5, 31),
    ]


def test_b1_monthly_from_the_31st_in_a_leap_year():
    assert _periods(date(2028, 1, 31), "monthly", 3) == [
        date(2028, 1, 31),
        date(2028, 2, 29),
        date(2028, 3, 31),
    ]


def test_b1_monthly_from_the_30th():
    assert _periods(date(2027, 1, 30), "monthly", 3) == [
        date(2027, 1, 30),
        date(2027, 2, 28),
        date(2027, 3, 30),
    ]


def test_b1_annual_from_29_february():
    assert _periods(date(2028, 2, 29), "annual", 5) == [
        date(2028, 2, 29),
        date(2029, 2, 28),
        date(2030, 2, 28),
        date(2031, 2, 28),
        date(2032, 2, 29),
    ]


def test_b1_weekly_across_a_dst_change_and_a_year_end():
    # US DST ended on 2026-11-01; Mexico has no DST since 2022: dates only.
    assert _periods(date(2026, 10, 26), "weekly", 3) == [
        date(2026, 10, 26),
        date(2026, 11, 2),
        date(2026, 11, 9),
    ]
    assert _periods(date(2026, 12, 24), "weekly", 3) == [
        date(2026, 12, 24),
        date(2026, 12, 31),
        date(2027, 1, 7),
    ]


def test_b1_monthly_across_a_year_end():
    assert _periods(date(2026, 11, 15), "monthly", 3) == [
        date(2026, 11, 15),
        date(2026, 12, 15),
        date(2027, 1, 15),
    ]


def test_b1_a_negative_index_is_refused():
    with pytest.raises(ValueError, match="start at 0"):
        period_start(date(2027, 1, 1), "monthly", -1)


# --- B2: first_on_or_after and next_after -----


@pytest.mark.parametrize(
    ("starts_on", "frequency", "day", "expected"),
    [
        (date(2027, 1, 1), "monthly", date(2026, 12, 1), date(2027, 1, 1)),
        (date(2027, 1, 1), "monthly", date(2027, 1, 1), date(2027, 1, 1)),
        (date(2027, 1, 1), "monthly", date(2027, 4, 15), date(2027, 5, 1)),
        (date(2027, 1, 1), "monthly", date(2027, 5, 1), date(2027, 5, 1)),
        (date(2027, 1, 31), "monthly", date(2027, 2, 28), date(2027, 2, 28)),
        (date(2027, 1, 31), "monthly", date(2027, 3, 1), date(2027, 3, 31)),
        (date(2027, 1, 31), "monthly", date(2027, 2, 27), date(2027, 2, 28)),
        (date(2027, 1, 4), "weekly", date(2027, 1, 12), date(2027, 1, 18)),
        (date(2027, 1, 4), "weekly", date(2027, 1, 11), date(2027, 1, 11)),
        (date(2028, 2, 29), "annual", date(2029, 1, 1), date(2029, 2, 28)),
        (date(2028, 2, 29), "annual", date(2029, 3, 1), date(2030, 2, 28)),
    ],
)
def test_b2_first_on_or_after(starts_on, frequency, day, expected):
    assert first_on_or_after(starts_on, frequency, day) == expected


@pytest.mark.parametrize(
    ("starts_on", "frequency", "start", "expected"),
    [
        (date(2027, 1, 31), "monthly", date(2027, 1, 31), date(2027, 2, 28)),
        (date(2027, 1, 31), "monthly", date(2027, 2, 28), date(2027, 3, 31)),
        (date(2027, 1, 31), "monthly", date(2027, 3, 31), date(2027, 4, 30)),
        (date(2027, 1, 4), "weekly", date(2027, 1, 4), date(2027, 1, 11)),
        (date(2028, 2, 29), "annual", date(2029, 2, 28), date(2030, 2, 28)),
        (date(2028, 2, 29), "annual", date(2031, 2, 28), date(2032, 2, 29)),
    ],
)
def test_b2_next_after(starts_on, frequency, start, expected):
    assert next_after(starts_on, frequency, start) == expected


def test_b2_next_after_refuses_a_day_before_the_anchor():
    with pytest.raises(ValueError, match="before"):
        next_after(date(2027, 1, 1), "monthly", date(2026, 12, 31))


def _grid(starts_on: date, frequency: Frequency, until: date) -> list[date]:
    points: list[date] = []
    k = 0
    while (point := period_start(starts_on, frequency, k)) <= until:
        points.append(point)
        k += 1
    points.append(point)
    return points


def test_b2_property_first_on_or_after_is_the_next_grid_point():
    chooser = random.Random(116)  # noqa: S311 - a reproducible test sample
    for _ in range(400):
        frequency = chooser.choice(FREQUENCIES)
        starts_on = date(2026, 1, 1) + timedelta(days=chooser.randrange(0, 1500))
        day = starts_on + timedelta(days=chooser.randrange(-40, 1200))
        grid = _grid(starts_on, frequency, max(day, starts_on) + timedelta(days=800))

        found = first_on_or_after(starts_on, frequency, day)

        assert found in grid, (starts_on, frequency, day)
        assert found >= day
        assert found >= starts_on
        index = grid.index(found)
        if index > 0:
            assert grid[index - 1] < day, (starts_on, frequency, day)
        assert next_after(starts_on, frequency, found) == grid[index + 1]
