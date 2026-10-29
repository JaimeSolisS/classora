"""The tuition period grid and a period's state (#93 C1, C2, H1; K3-K6)."""

import itertools
from datetime import date, timedelta

import pytest

from app.modules.billing.tuition import (
    Period,
    PlanTerms,
    is_period_end,
    is_period_start,
    period_at,
    period_containing,
    period_index,
    period_start,
    periods_between,
    plan_period_state,
)


def test_weekly_and_biweekly_periods_are_7_and_14_days_from_the_start():
    start = date(2027, 1, 6)

    assert period_start(start, "weekly", 0) == start
    assert period_start(start, "weekly", 3) == date(2027, 1, 27)
    assert period_at(start, "weekly", 1) == Period(date(2027, 1, 13), date(2027, 1, 19))
    assert period_start(start, "biweekly", 2) == date(2027, 2, 3)
    assert period_at(start, "biweekly", 0) == Period(start, date(2027, 1, 19))


def test_monthly_periods_from_the_31st_clamp_and_come_back():
    start = date(2027, 1, 31)

    assert [period_start(start, "monthly", k) for k in range(4)] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]
    assert period_at(start, "monthly", 0) == Period(start, date(2027, 2, 27))
    assert period_at(start, "monthly", 1) == Period(
        date(2027, 2, 28), date(2027, 3, 30)
    )


def test_monthly_from_a_leap_day():
    start = date(2028, 2, 29)

    assert period_start(start, "monthly", 1) == date(2028, 3, 29)
    assert period_start(start, "monthly", 12) == date(2029, 2, 28)
    assert period_start(start, "monthly", 13) == date(2029, 3, 29)


def test_monthly_from_the_30th_is_computed_from_the_start_not_the_previous():
    start = date(2027, 1, 30)

    assert period_start(start, "monthly", 1) == date(2027, 2, 28)
    # Not 28 Mar (which chaining from the previous period would give).
    assert period_start(start, "monthly", 2) == date(2027, 3, 30)


def test_negative_indexes_are_refused():
    with pytest.raises(ValueError, match="start at 0"):
        period_start(date(2027, 1, 1), "weekly", -1)


@pytest.mark.parametrize("frequency", ["weekly", "biweekly", "monthly"])
def test_the_period_containing_its_first_and_last_day(frequency):
    start = date(2027, 1, 31)
    for k in range(30):
        period = period_at(start, frequency, k)
        assert period_containing(start, frequency, period.start) == period
        assert period_containing(start, frequency, period.end) == period
        assert period_index(start, frequency, period.end) == k
        assert is_period_start(start, frequency, period.start)
        assert is_period_end(start, frequency, period.end)
        assert not is_period_end(start, frequency, period.start) or (
            period.start == period.end
        )
    assert period_containing(start, frequency, start - timedelta(days=1)) is None
    assert not is_period_start(start, frequency, start - timedelta(days=1))


def test_is_period_end_on_a_mid_period_day():
    start = date(2027, 1, 15)

    assert is_period_end(start, "monthly", date(2027, 2, 14))
    assert not is_period_end(start, "monthly", date(2027, 2, 15))
    assert not is_period_end(start, "monthly", date(2027, 2, 10))
    assert is_period_end(start, "weekly", date(2027, 1, 21))
    assert not is_period_end(start, "weekly", date(2027, 1, 22))


@pytest.mark.parametrize(
    ("frequency", "counts"),
    [("weekly", (52, 53)), ("biweekly", (26, 27)), ("monthly", (12, 12))],
)
def test_periods_between_across_a_year(frequency, counts):
    start = date(2027, 1, 1)
    found = periods_between(start, frequency, start, date(2027, 12, 31))

    assert len(found) in counts
    assert found[0].start == start
    assert all(
        a.end + timedelta(days=1) == b.start for a, b in itertools.pairwise(found)
    )


def test_periods_between_takes_the_periods_whose_start_is_in_the_range():
    start = date(2027, 1, 15)

    assert periods_between(start, "monthly", date(2027, 1, 16), date(2027, 3, 15)) == [
        Period(date(2027, 2, 15), date(2027, 3, 14)),
        Period(date(2027, 3, 15), date(2027, 4, 14)),
    ]
    assert periods_between(start, "monthly", date(2026, 1, 1), date(2027, 1, 15)) == [
        Period(date(2027, 1, 15), date(2027, 2, 14))
    ]
    assert periods_between(start, "monthly", date(2027, 3, 1), date(2027, 2, 1)) == []
    assert periods_between(start, "weekly", date(2026, 1, 1), date(2027, 1, 14)) == []


PLAN = PlanTerms(starts_on=date(2027, 1, 1), frequency="monthly", ends_on=None)
MARCH = Period(date(2027, 3, 1), date(2027, 3, 31))


def state(**overrides):
    values = {
        "plan": PLAN,
        "pauses": [],
        "period": MARCH,
        "today": date(2027, 3, 1),
        "floor": date(2027, 1, 1),
        "enrollment": None,
    }
    values.update(overrides)
    return plan_period_state(
        values.pop("plan"), values.pop("pauses"), values.pop("period"), **values
    )


def test_a_period_starting_today_is_due():
    assert state() == "due"


def test_states_and_their_precedence():
    assert state(today=date(2027, 2, 28)) == "not_due"
    # not_due before below_floor, after_end, paused and enrollment_ended.
    assert (
        state(
            today=date(2027, 2, 28),
            floor=None,
            plan=PlanTerms(PLAN.starts_on, "monthly", date(2027, 1, 31)),
            pauses=[(date(2027, 3, 1), None)],
            enrollment=date(2027, 2, 1),
        )
        == "not_due"
    )
    assert state(floor=None) == "below_floor"
    assert state(floor=date(2027, 3, 2)) == "below_floor"
    assert state(floor=date(2027, 3, 1)) == "due"
    ended = PlanTerms(PLAN.starts_on, "monthly", date(2027, 2, 28))
    assert state(plan=ended) == "after_end"
    assert state(plan=ended, floor=None) == "below_floor"
    assert state(plan=ended, pauses=[(date(2027, 3, 1), None)]) == "after_end"
    assert state(pauses=[(date(2027, 2, 1), date(2027, 3, 31))]) == "paused"
    assert state(pauses=[(date(2027, 2, 1), date(2027, 2, 28))]) == "due"
    assert state(pauses=[(date(2027, 3, 1), None)]) == "paused"
    assert (
        state(pauses=[(date(2027, 3, 1), None)], enrollment=date(2027, 2, 1))
        == "paused"
    )
    assert state(enrollment=date(2027, 3, 1)) == "enrollment_ended"
    assert state(enrollment=date(2027, 2, 15)) == "enrollment_ended"
    assert state(enrollment=date(2027, 3, 2)) == "due"
