"""Integration tests connect with the raised timeout (#661 AC3).

The production ``app.core.db.CONNECT_TIMEOUT_SECONDS`` stays 2.0 (pinned by
``tests/unit/test_db.py`` and ``tests/unit/test_health.py``); inside an
integration module the autouse ``integration_connect_timeout`` fixture of
``tests/integration/conftest.py`` raises it, for the module-scoped fixtures
too, and restores it at module end.
"""

from collections.abc import Iterator

import pytest

from app.core import db
from tests.integration.conftest import INTEGRATION_CONNECT_TIMEOUT_SECONDS


@pytest.fixture(scope="module")
def timeout_at_module_setup() -> Iterator[float]:
    """The timeout a module-scoped fixture (like ``module_seeded_url``) sees."""
    yield db.CONNECT_TIMEOUT_SECONDS


def test_the_engines_connect_with_the_integration_timeout() -> None:
    assert INTEGRATION_CONNECT_TIMEOUT_SECONDS == 30.0
    # The request engine and the direct engines (Alembic, the seeds).
    assert db.engine_options()["connect_args"]["timeout"] == 30.0
    assert db.engine_options(pgbouncer=False)["connect_args"]["timeout"] == 30.0


def test_module_scoped_fixtures_see_the_integration_timeout(
    timeout_at_module_setup: float,
) -> None:
    assert timeout_at_module_setup == 30.0
