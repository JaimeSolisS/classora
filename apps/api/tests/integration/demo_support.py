"""Helpers for the ``make seed-demo`` generator tests (#23, #195, #299).

- :func:`migrate` and :func:`demo_env`: a fresh database at the Alembic head,
  and the environment of a ``demo.main`` run (each test module wraps them in
  its ``migrated_url`` and ``env_for`` fixtures).
- The expected summary lines of a first run, a rerun and a ``dev`` run.
- The #299 tables and their counts after a first run (:func:`demo_table_counts`).
"""

from datetime import UTC, datetime

import pytest
from alembic import command

from tests.authz.csrf import origin_for
from tests.factories.identity import DEMO_ACCOUNT_GENERATORS
from tests.integration.conftest import run_sql
from tests.integration.test_migrations import alembic_config

FAMILIES_GENERATOR = "families.demo_families"
DEMO_FAMILIES_ROWS = 34
CATALOG_GENERATOR = "catalog.demo_catalog"
DEMO_CATALOG_ROWS = 24
TUTOR_GENERATOR = "staff.demo_tutor"
DEMO_TUTOR_ROWS = 21
"""#299 C5: 1 profile + 4 qualifications + 4 approval requests + 3
notifications + 1 settings row + 6 windows + 1 exception + 1 preferred
classroom."""
SCHEDULE_GENERATOR = "scheduling.demo_schedule"
DEMO_SCHEDULE_ROWS = 111
"""#299 D8: see :data:`DEMO_TABLE_COUNTS` (the schedule's share of
``approval_requests`` is 1 and of ``notifications`` 9)."""

DEMO_TABLE_COUNTS = {
    "subjects": 8,
    "classrooms": 3,
    "classroom_availability_windows": 11,
    "academic_periods": 1,
    "closures": 1,
    "tutor_profiles": 1,
    "tutor_qualifications": 4,
    "tutor_availability_settings": 1,
    "tutor_availability_windows": 6,
    "tutor_availability_exceptions": 1,
    "tutor_preferred_classrooms": 1,
    # 4 completed + U1-U5 + 8 series occurrences + 8 group occurrences.
    "class_sessions": 25,
    # One student each, except the group's three.
    "class_session_participants": 4 + 5 + 8 + 8 * 3,
    # The D5 series and the group's series.
    "class_series": 2,
    "class_series_participants": 1 + 3,
    "class_groups": 1,
    "group_enrollments": 3,
    # U1-U4, the series and the group.
    "assignment_offers": 6,
    # U1-U4, the series' 7 occurrences off the closure, the group's 8.
    "assignment_offer_sessions": 4 + 7 + 8,
    "class_session_reassignment_flags": 0,
    # 4 qualifications + the U4 reassignment item.
    "approval_requests": 5,
    # 3 qualification approvals + 6 offers received + U4 declined to the
    # Owner, the Administrator and the Coordinator.
    "notifications": 3 + 6 + 3,
}
"""Every table the #299 generators count, after a first run on a fresh
database (only these generators write to them)."""

FROZEN_NOW = datetime(2026, 11, 4, 17, 0, tzinfo=UTC)
"""A Wednesday, 11:00 in Mexico City (#299 E2), after the privacy notice's
version 1 (2026-10-18) that the demo families' consent needs."""

REFERENCE_LINES = [
    "identity.permissions: 28 inserted, 0 updated",
    "identity.roles: 7 inserted, 0 updated",
    "identity.role_permissions: 69 inserted, 0 updated",
    "catalog.education_levels: 4 inserted, 0 updated",
]
RERUN_REFERENCE_LINES = [
    "identity.permissions: 0 inserted, 0 updated",
    "identity.roles: 0 inserted, 0 updated",
    "identity.role_permissions: 0 inserted, 0 updated",
    "catalog.education_levels: 0 inserted, 0 updated",
]
ACCOUNT_GENERATORS = [generator.name for generator in DEMO_ACCOUNT_GENERATORS]
GENERATOR_NAMES = [
    *ACCOUNT_GENERATORS,
    FAMILIES_GENERATOR,
    CATALOG_GENERATOR,
    TUTOR_GENERATOR,
    SCHEDULE_GENERATOR,
]

FIRST_RUN_LINES = [
    *REFERENCE_LINES,
    *(f"{name}: 1 rows" for name in ACCOUNT_GENERATORS),
    f"{FAMILIES_GENERATOR}: {DEMO_FAMILIES_ROWS} rows",
    f"{CATALOG_GENERATOR}: {DEMO_CATALOG_ROWS} rows",
    f"{TUTOR_GENERATOR}: {DEMO_TUTOR_ROWS} rows",
    f"{SCHEDULE_GENERATOR}: {DEMO_SCHEDULE_ROWS} rows",
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


def demo_table_counts(url: str) -> dict[str, int]:
    """The row count of every table in :data:`DEMO_TABLE_COUNTS`."""
    return {table: count(url, table) for table in DEMO_TABLE_COUNTS}
