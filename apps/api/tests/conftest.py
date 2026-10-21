"""Shared test setup.

Applies the ``unit`` / ``integration`` marker from each test's directory. Every
test must live under ``tests/unit/`` or ``tests/integration/``. A test anywhere
else is a collection error, so it cannot escape both ``-m`` selections.

Every test also starts with Factory Boy and Faker reseeded to
``FACTORY_SEED``, so generated data never depends on test order.

The ``clock`` fixture freezes ``app.core.clock.now`` for time travel (#25).

``--shard=K/N`` (``tests/sharding.py``) splits the selected files for CI.

Also isolates every test from the developer's shell: the variables that
``Settings`` reads are removed and the cached settings are cleared, so tests
set exactly the configuration they need.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import clock as app_clock
from app.core.config import env_var_names, get_settings
from app.modules.families import service as families_service
from app.modules.identity.service import register_student_login_gate
from app.wiring import register_approval_wiring
from tests.authz.csrf import TEST_APP_ORIGIN
from tests.factories.base import reseed

# --shard=K/N for the sharded CI integration jobs (#217).
pytest_plugins = ["tests.sharding"]

FACTORY_SEED = 20261017

TESTS_ROOT = Path(__file__).parent
MARKER_BY_DIRECTORY = {
    TESTS_ROOT / "unit": pytest.mark.unit,
    TESTS_ROOT / "integration": pytest.mark.integration,
}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    misplaced: list[str] = []
    for item in items:
        path = item.path
        for directory, marker in MARKER_BY_DIRECTORY.items():
            if path.is_relative_to(directory):
                item.add_marker(marker)
                break
        else:
            misplaced.append(item.nodeid)
    if misplaced:
        raise pytest.UsageError(
            "Tests must live in tests/unit/ or tests/integration/. Misplaced: "
            + ", ".join(misplaced)
        )


@pytest.fixture(autouse=True)
def isolated_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    names = env_var_names()
    # Settings matches names case-insensitively, so drop lowercase spellings too.
    for key in list(os.environ):
        if key.upper() in names:
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def student_login_gate() -> Iterator[None]:
    """Register the families student-login gate for every test (#35 B1), as
    the composition roots do: identity calls made outside an app (``in_uow``)
    pass the real gate too. ``no_student_login_gate`` unregisters it."""
    register_student_login_gate(families_service.STUDENT_LOGIN_GATE)
    yield
    register_student_login_gate(families_service.STUDENT_LOGIN_GATE)


@pytest.fixture(autouse=True)
def approval_wiring() -> None:
    """Register the approval types and the account-reference resolver for
    every test (#40 D3), as the composition roots do: a registration submitted
    outside an app (``in_uow``) raises its approval request too. Tests that
    register a fake type unregister it themselves."""
    register_approval_wiring()


@pytest.fixture
def no_student_login_gate(student_login_gate: None) -> Iterator[None]:
    """No gate registered for this test: activating a student fails closed."""
    register_student_login_gate(None)
    yield


@pytest.fixture(autouse=True)
def reseeded_factories() -> None:
    reseed(FACTORY_SEED)


class FrozenClock:
    """A stopped :mod:`app.core.clock` that tests move by hand (#25 A2)."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("the clock needs a timezone-aware datetime")
        self._now = value.astimezone(UTC)

    def advance(self, delta: timedelta) -> datetime:
        self._now += delta
        return self._now


CLOCK_START = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


@contextmanager
def frozen_clock(start: datetime = CLOCK_START) -> Iterator[FrozenClock]:
    """Replace ``app.core.clock._source`` until the block ends."""
    frozen = FrozenClock(start)
    previous = app_clock._source
    app_clock._source = frozen
    try:
        yield frozen
    finally:
        app_clock._source = previous


@pytest.fixture
def clock() -> Iterator[FrozenClock]:
    """Freeze ``app.core.clock.now`` at :data:`CLOCK_START`.

    Replaces the module attribute ``app.core.clock._source``, so code running
    in another thread (an app inside ``TestClient``) sees the same time.
    ``set(dt)`` and ``advance(timedelta)`` move it; the real clock is restored
    after the test. Never ``sleep`` to make time pass.
    """
    with frozen_clock() as frozen:
        yield frozen


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A minimal valid environment so ``create_app()`` passes its startup check.

    The DSNs are placeholders; tests that need a database override
    ``get_settings`` with their own ``Settings``.
    """
    placeholder = "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder"
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", placeholder)
    monkeypatch.setenv("DATABASE_DIRECT_URL", placeholder)
    monkeypatch.setenv("APP_ORIGIN", TEST_APP_ORIGIN)
