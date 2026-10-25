"""Helpers for the ``make seed-demo`` generator tests (#23, #195, #299, #303).

- :func:`migrate` and :func:`demo_env`: a fresh database at the Alembic head,
  and the environment of a ``demo.main`` run (each test module wraps them in
  its ``migrated_url`` and ``env_for`` fixtures).
- The expected summary lines of a first run, a rerun and a ``dev`` run.
- The #299 tables and their counts after a first run (:func:`demo_table_counts`),
  and the #303 rows in the same tables and its own (:data:`DEMO_303_TABLE_COUNTS`,
  :data:`ALL_TABLE_COUNTS`).
- Prefixes of the registered generators by name (:func:`generators_through`).
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from alembic import command

from tests.authz.csrf import origin_for
from tests.factories import demo
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

TUTOR2_GENERATOR = "staff.demo_tutor2"
DEMO_TUTOR2_ROWS = 12
"""#303 B4: 2 qualifications + 2 approval requests + 2 approval notifications
+ 1 settings row + 5 windows."""
COURSES_GENERATOR = "courses.demo_courses"
DEMO_COURSES_ROWS = 49
"""#303 C5: 1 template + 2 subjects + 3 modules + 2 assessments + 1 snapshot,
1 cohort, 2 enrollments, 1 series, 2 series participants, 8 sessions, 16
session participants, 1 offer, 8 offer sessions, 1 notification."""
CHANGES_GENERATOR = "scheduling.demo_changes"
DEMO_CHANGES_ROWS = 16
"""#303 D8: 1 substitution, 1 offer, 1 offer session, 1 session request, 1
change request, 1 availability change request, 3 approval requests and 7
notifications (see :data:`DEMO_303_TABLE_COUNTS`)."""

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
"""Every table the #299 generators count, after a first run of the
generators through ``scheduling.demo_schedule`` on a fresh database."""

DEMO_303_TABLE_COUNTS = {
    # staff.demo_tutor2: Matemáticas and Ciencias (Secundaria), approved.
    "tutor_qualifications": 2,
    "tutor_availability_settings": 1,
    "tutor_availability_windows": 5,
    # courses.demo_courses.
    "course_templates": 1,
    "course_template_subjects": 2,
    "course_template_modules": 3,
    "course_template_assessments": 2,
    "course_template_snapshots": 1,
    "course_cohorts": 1,
    "cohort_enrollments": 2,
    "class_series": 1,
    "class_series_participants": 2,
    "class_sessions": 8,
    "class_session_participants": 8 * 2,
    # The cohort's series offer and the substitution offer.
    "assignment_offers": 1 + 1,
    "assignment_offer_sessions": 8 + 1,
    # scheduling.demo_changes.
    "class_session_substitutions": 1,
    "session_requests": 1,
    "session_change_requests": 1,
    "tutor_availability_change_requests": 1,
    # 2 qualifications; the session request, the change request and the
    # availability change.
    "approval_requests": 2 + 3,
    # 2 qualification approvals and the cohort offer to demo.tutor2; the
    # substitution offer to demo.tutor2, tutor_substituted to demo.tutor,
    # session_tutor_changed to demo.student and demo.guardian, and
    # session_cancelled to demo.tutor2, demo.student and demo.guardian.
    "notifications": 2 + 1 + 1 + 1 + 2 + 3,
}
"""The rows the #303 generators add to each table they count, after a first
run on a fresh database (several tables are shared with #299)."""

ALL_TABLE_COUNTS = {
    table: DEMO_TABLE_COUNTS.get(table, 0) + DEMO_303_TABLE_COUNTS.get(table, 0)
    for table in {**DEMO_TABLE_COUNTS, **DEMO_303_TABLE_COUNTS}
}
"""Every table of :data:`DEMO_TABLE_COUNTS` and :data:`DEMO_303_TABLE_COUNTS`
after a first run of every generator."""

FROZEN_NOW = datetime(2026, 11, 4, 17, 0, tzinfo=UTC)
"""A Wednesday, 11:00 in Mexico City (#299 E2), after the privacy notice's
version 1 (2026-10-18) that the demo families' consent needs."""

REFERENCE_LINES = [
    "identity.permissions: 33 inserted, 0 updated",
    "identity.roles: 7 inserted, 0 updated",
    "identity.role_permissions: 84 inserted, 0 updated",
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
    TUTOR2_GENERATOR,
    COURSES_GENERATOR,
    CHANGES_GENERATOR,
]

FIRST_RUN_LINES = [
    *REFERENCE_LINES,
    *(f"{name}: 1 rows" for name in ACCOUNT_GENERATORS),
    f"{FAMILIES_GENERATOR}: {DEMO_FAMILIES_ROWS} rows",
    f"{CATALOG_GENERATOR}: {DEMO_CATALOG_ROWS} rows",
    f"{TUTOR_GENERATOR}: {DEMO_TUTOR_ROWS} rows",
    f"{SCHEDULE_GENERATOR}: {DEMO_SCHEDULE_ROWS} rows",
    f"{TUTOR2_GENERATOR}: {DEMO_TUTOR2_ROWS} rows",
    f"{COURSES_GENERATOR}: {DEMO_COURSES_ROWS} rows",
    f"{CHANGES_GENERATOR}: {DEMO_CHANGES_ROWS} rows",
]
RERUN_LINES = [
    *RERUN_REFERENCE_LINES,
    *(f"{name}: 0 rows" for name in GENERATOR_NAMES),
]
DEV_LINES = [
    *REFERENCE_LINES,
    *(f"{name}: skipped (APP_ENV=dev; local only)" for name in GENERATOR_NAMES),
]


NEW_303_GENERATORS = (
    "identity.demo_tutor2",
    TUTOR2_GENERATOR,
    COURSES_GENERATOR,
    CHANGES_GENERATOR,
)


def generators_through(name: str) -> list[demo.DemoGenerator]:
    """The registered generators up to and including ``name``."""
    names = [generator.name for generator in demo.DEMO_GENERATORS]
    return list(demo.DEMO_GENERATORS[: names.index(name) + 1])


def generators_before_303() -> list[demo.DemoGenerator]:
    """The #299 run: every registered generator except the four #303 ones."""
    return [g for g in demo.DEMO_GENERATORS if g.name not in NEW_303_GENERATORS]


def first_run_lines(generators: Sequence[demo.DemoGenerator]) -> list[str]:
    """The lines of :data:`FIRST_RUN_LINES` of a first run of ``generators``."""
    names = {generator.name for generator in generators}
    return [
        line
        for line in FIRST_RUN_LINES
        if line in REFERENCE_LINES or line.split(":")[0] in names
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


def demo_table_counts(
    url: str, tables: dict[str, int] = DEMO_TABLE_COUNTS
) -> dict[str, int]:
    """The row count of every table in ``tables`` (default
    :data:`DEMO_TABLE_COUNTS`)."""
    return {table: count(url, table) for table in tables}
