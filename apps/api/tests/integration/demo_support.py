"""Helpers for the ``make seed-demo`` generator tests (#23, #195).

- :func:`migrate` and :func:`demo_env`: a fresh database at the Alembic head,
  and the environment of a ``demo.main`` run (each test module wraps them in
  its ``migrated_url`` and ``env_for`` fixtures).
- The expected summary lines of a first run, a rerun and a ``dev`` run.
"""

import pytest
from alembic import command

from tests.authz.csrf import origin_for
from tests.factories.identity import DEMO_ACCOUNT_GENERATORS
from tests.integration.conftest import run_sql
from tests.integration.test_migrations import alembic_config

FAMILIES_GENERATOR = "families.demo_families"
DEMO_FAMILIES_ROWS = 34

REFERENCE_LINES = [
    "identity.permissions: 25 inserted, 0 updated",
    "identity.roles: 7 inserted, 0 updated",
    "identity.role_permissions: 60 inserted, 0 updated",
    "catalog.education_levels: 4 inserted, 0 updated",
]
RERUN_REFERENCE_LINES = [
    "identity.permissions: 0 inserted, 0 updated",
    "identity.roles: 0 inserted, 0 updated",
    "identity.role_permissions: 0 inserted, 0 updated",
    "catalog.education_levels: 0 inserted, 0 updated",
]
ACCOUNT_GENERATORS = [generator.name for generator in DEMO_ACCOUNT_GENERATORS]
GENERATOR_NAMES = [*ACCOUNT_GENERATORS, FAMILIES_GENERATOR]

FIRST_RUN_LINES = [
    *REFERENCE_LINES,
    *(f"{name}: 1 rows" for name in ACCOUNT_GENERATORS),
    f"{FAMILIES_GENERATOR}: {DEMO_FAMILIES_ROWS} rows",
]
RERUN_LINES = [
    *RERUN_REFERENCE_LINES,
    *(f"{name}: 0 rows" for name in GENERATOR_NAMES),
]
DEV_LINES = [
    *REFERENCE_LINES,
    *(f"{name}: skipped (APP_ENV=dev; local only)" for name in GENERATOR_NAMES),
]


def migrate(url: str) -> str:
    command.upgrade(alembic_config(url), "head")
    return url


def demo_env(monkeypatch: pytest.MonkeyPatch, app_env: str, dsn: str) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("APP_ORIGIN", origin_for(app_env))
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("DATABASE_DIRECT_URL", dsn)


def count(url: str, table: str) -> int:
    """``SELECT count(*)`` of ``table`` (a fixed table name from the test)."""
    return int(run_sql(url, f"SELECT count(*) FROM {table}")[0][0])  # noqa: S608


class UntouchedSession:
    """Fails the test if the generator uses the session."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the session was used ({name})")
