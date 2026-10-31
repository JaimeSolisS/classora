"""The demo seed clock (#353 G1, A4): ``seed_clock`` fixes ``clock.now()``
at a past instant and always restores the previous source. The demo actor
(M6-demo G3, A4): ``seed_actor`` binds the session's actor and always
restores the previous one."""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import clock as app_clock
from app.core.audit import ACTOR_INFO_KEY, bind_actor, current_actor_id
from tests.factories.demo_clock import DemoPlanError, seed_actor, seed_clock

NOW = datetime(2026, 11, 4, 17, 0, tzinfo=UTC)
PAST = datetime(2026, 10, 14, 12, 0, tzinfo=timezone(timedelta(hours=-6)))


class Boom(Exception):
    pass


def test_the_block_sees_the_instant_and_the_frozen_source_comes_back(clock):
    clock.set(NOW)
    source = app_clock._source

    with seed_clock(PAST) as fixed:
        assert fixed == PAST
        assert app_clock.now() == PAST
        assert app_clock.now().tzinfo == UTC

    assert app_clock._source is source
    assert app_clock.now() == NOW


def test_the_source_comes_back_after_an_exception(clock):
    clock.set(NOW)
    source = app_clock._source

    with pytest.raises(Boom), seed_clock(PAST):
        assert app_clock.now() == PAST
        raise Boom()

    assert app_clock._source is source
    assert app_clock.now() == NOW


def test_the_real_clock_comes_back_without_the_fixture():
    assert app_clock._source is app_clock.system_now

    with seed_clock(datetime(2020, 1, 1, tzinfo=UTC)):
        assert app_clock.now() == datetime(2020, 1, 1, tzinfo=UTC)

    assert app_clock._source is app_clock.system_now


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 10, 14, 12, 0),  # naive
        NOW,  # not before now
        NOW + timedelta(seconds=1),  # the future
    ],
    ids=["naive", "now", "future"],
)
def test_a_naive_or_future_instant_is_refused(clock, instant):
    clock.set(NOW)
    source = app_clock._source

    with pytest.raises(DemoPlanError), seed_clock(instant):
        pytest.fail("the block must not run")

    assert app_clock._source is source


def test_nested_use_restores_each_level(clock):
    clock.set(NOW)
    outer = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    inner = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

    with seed_clock(outer):
        with seed_clock(inner):
            assert app_clock.now() == inner
        assert app_clock.now() == outer
        # Inside the outer block, "now" is the outer instant: a later inner
        # one would move time forward.
        with pytest.raises(DemoPlanError), seed_clock(outer + timedelta(hours=1)):
            pytest.fail("the block must not run")
        assert app_clock.now() == outer

    assert app_clock.now() == NOW


# --- seed_actor (M6-demo G3) ----------------------------------------------------------

USER = uuid.UUID("00000000-0000-7000-8000-000000000001")
OTHER = uuid.UUID("00000000-0000-7000-8000-000000000002")


def _session() -> Any:
    """Only ``info`` is used (``app.core.audit.bind_actor``)."""
    return SimpleNamespace(info={})


def test_the_actor_is_bound_and_the_unit_of_work_actor_comes_back():
    session = _session()
    bind_actor(session, lambda: None)  # a Unit of Work without an actor
    source = session.info[ACTOR_INFO_KEY]

    with seed_actor(session, USER) as bound:
        assert bound == USER
        assert current_actor_id(session) == USER

    assert session.info[ACTOR_INFO_KEY] is source
    assert current_actor_id(session) is None


def test_the_actor_comes_back_after_an_exception():
    session = _session()
    bind_actor(session, lambda: OTHER)

    with pytest.raises(Boom), seed_actor(session, USER):
        assert current_actor_id(session) == USER
        raise Boom()

    assert current_actor_id(session) == OTHER


def test_no_previous_source_leaves_none_bound():
    session = _session()

    with seed_actor(session, USER):
        assert current_actor_id(session) == USER

    assert ACTOR_INFO_KEY not in session.info
    assert current_actor_id(session) is None


def test_nested_actors_restore_each_level():
    session = _session()

    with seed_actor(session, OTHER):
        with seed_actor(session, USER):
            assert current_actor_id(session) == USER
        assert current_actor_id(session) == OTHER

    assert current_actor_id(session) is None
