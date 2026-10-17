"""Shared test setup.

Applies the ``unit`` / ``integration`` marker from each test's directory. Every
test must live under ``tests/unit/`` or ``tests/integration/``. A test anywhere
else is a collection error, so it cannot escape both ``-m`` selections.

Also isolates every test from the developer's shell: the variables that
``Settings`` reads are removed and the cached settings are cleared, so tests
set exactly the configuration they need.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import env_var_names, get_settings

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
