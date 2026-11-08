"""The simulation clock and the fixed timeline (#543 C1, E2 (a))."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core import clock as app_clock
from tests.factories.demo_clock import DemoPlanError
from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_NOW,
    SIM_SETUP,
    SIM_START,
    history_days,
    local_instant,
    sim_clock,
)


def test_the_fixed_timeline():
    assert SIM_START == date(2026, 9, 1) and SIM_START.isoweekday() == 2
    assert SIM_ANCHOR == date(2026, 11, 19) and SIM_ANCHOR.isoweekday() == 4
    assert SIM_HORIZON == date(2026, 12, 5) and SIM_HORIZON.isoweekday() == 6
    assert SIM_NOW == datetime(2026, 11, 19, 14, 0, tzinfo=UTC)
    assert SIM_SETUP == datetime(2026, 8, 24, 16, 0, tzinfo=UTC)
    days = list(history_days())
    assert days[0] == SIM_START and days[-1] == date(2026, 11, 18)
    assert len(days) == 79


def test_local_instant_uses_the_center_zone():
    assert local_instant(date(2026, 9, 1), "09:30") == datetime(
        2026, 9, 1, 15, 30, tzinfo=UTC
    )


def test_it_moves_the_clock_forward_and_back(clock):
    start = clock.now()
    later = start + timedelta(days=40)
    earlier = start - timedelta(days=40)

    with sim_clock(later) as fixed:
        assert fixed == later
        assert app_clock.now() == later
    with sim_clock(earlier):
        assert app_clock.now() == earlier
    assert app_clock.now() == start


def test_a_naive_instant_is_refused():
    with pytest.raises(DemoPlanError):
        with sim_clock(datetime(2026, 11, 19, 8, 0)):
            pass  # pragma: no cover


def test_the_previous_source_comes_back_when_the_block_raises():
    saved = app_clock._source

    with pytest.raises(RuntimeError):
        with sim_clock(SIM_NOW):
            raise RuntimeError()

    assert app_clock._source is saved


def test_under_the_clock_fixture_the_frozen_source_comes_back(clock):
    frozen = app_clock._source

    with sim_clock(SIM_NOW):
        with sim_clock(SIM_SETUP):
            assert app_clock.now() == SIM_SETUP
        assert app_clock.now() == SIM_NOW

    assert app_clock._source is frozen
    assert app_clock._source is not app_clock.system_now
