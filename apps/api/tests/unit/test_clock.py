"""The application clock and the ``clock`` fixture (#25 A1, A2)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import clock as app_clock
from tests.conftest import CLOCK_START, frozen_clock


def test_now_is_timezone_aware_utc():
    now = app_clock.now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)


def test_the_fixture_freezes_the_clock(clock):
    assert app_clock.now() == CLOCK_START
    assert app_clock.now() == CLOCK_START  # it does not move on its own


def test_set_and_advance(clock):
    clock.advance(timedelta(minutes=30))
    assert app_clock.now() == CLOCK_START + timedelta(minutes=30)

    later = datetime(2027, 1, 1, tzinfo=UTC)
    clock.set(later)
    assert app_clock.now() == later

    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2027, 1, 1))  # noqa: DTZ001 - the naive case under test


def time_app() -> FastAPI:
    app = FastAPI()

    @app.get("/now")
    async def now() -> str:
        return app_clock.now().isoformat()

    return app


def test_advance_is_seen_inside_a_request_handled_by_testclient(clock):
    # TestClient runs the app in another thread (an anyio portal).
    with TestClient(time_app()) as client:
        first = client.get("/now").json()
        clock.advance(timedelta(days=7, seconds=-1))
        second = client.get("/now").json()

    assert datetime.fromisoformat(first) == CLOCK_START
    assert datetime.fromisoformat(second) == CLOCK_START + timedelta(days=7, seconds=-1)


def test_the_real_clock_is_restored_after_a_test():
    # Runs after the tests above, which froze the clock.
    assert app_clock._source is app_clock.system_now
    assert abs(app_clock.now() - datetime.now(UTC)) < timedelta(seconds=5)


def test_the_fixture_restores_the_real_clock_even_on_error():
    # The fixture's own context manager, independent of test order.
    with pytest.raises(RuntimeError), frozen_clock() as frozen:
        frozen.advance(timedelta(hours=1))
        assert app_clock.now() == CLOCK_START + timedelta(hours=1)
        raise RuntimeError

    assert app_clock._source is app_clock.system_now
