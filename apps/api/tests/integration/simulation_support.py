"""Helpers for the simulated operations seed tests (#543 HF-01).

- :data:`FROZEN_NOW`: the tests' real-clock instant (after the anchor);
- the pinned module totals per profile, with their breakdowns;
- :func:`run_command`: one ``python -m tests.factories.simulation`` run on a
  database, in-process, with the environment it needs;
- :func:`table_counts` (every table's row count) and :func:`canonical_dump`
  (the rows this issue writes, without ids, database-set times, versions,
  hashes or token secrets, foreign keys replaced by natural keys; C4).
"""

import asyncio
import io
import json
import re
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from alembic import command
from sqlalchemy import text

from app.core.config import get_settings
from tests.authz.csrf import origin_for
from tests.conftest import frozen_clock
from tests.factories.simulation import SIMULATION_MODULES
from tests.factories.simulation import __main__ as simulation_command
from tests.factories.simulation.plan import ProfileName, SimulationPlan
from tests.factories.simulation.timeline import SimulationModule
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.test_migrations import alembic_config

FROZEN_NOW: Final = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
"""The real clock of the tests (E3 in testing-guidelines): a day after the
anchor, so a value taken from the real clock would differ from the plan's."""

SEED_LINES: Final = [
    "identity.permissions: 47 inserted, 0 updated",
    "identity.roles: 7 inserted, 0 updated",
    "identity.role_permissions: 118 inserted, 0 updated",
    "catalog.education_levels: 4 inserted, 0 updated",
]
RERUN_SEED_LINES: Final = [
    "identity.permissions: 0 inserted, 0 updated",
    "identity.roles: 0 inserted, 0 updated",
    "identity.role_permissions: 0 inserted, 0 updated",
    "catalog.education_levels: 0 inserted, 0 updated",
]

SMALL_PEOPLE_ROWS: Final = 156
"""``small``: 15 users (4 staff, 4 tutors, 3 guardian and 2 student logins,
the pending tutor and the pending registration), 14 user roles (not the
pending registration), 12 invitation tokens (not the Owner or the two
self-registrations; 11 redeemed), 13 preferences and 13 privacy acceptances
(the login accounts), 1 delegation (granted 18 Nov; the 12 Oct one was
revoked), 2 registration requests and their 2 approval requests, 8 families,
10 family guardians, 10 family students, 10 guardians, 10 students, 13
emergency contacts, 14 student-guardian links and 9 consents (the minors)."""
SMALL_CATALOG_ROWS: Final = 18
"""``small``: 6 subjects, 3 classrooms, 5 classroom windows, 1 academic
period and 3 closures."""
SMALL_STAFF_ROWS: Final = 90
"""``small``: 4 profiles, 17 qualifications (14 approved, 1 revoked, 1
rejected, 1 pending) with 17 approval requests and 17 decision notifications
(15 approved, 1 rejected, 1 revoked), 4 availability settings, 24 weekly
windows, 5 exceptions and 2 preferred classrooms."""
SMALL_SCHEDULE_ROWS: Final = 1607
"""``small`` (#550): 12 series (10 individual, 1 group, 1 tutor 12's) with
13 participants, 182 sessions with 210 participants, 30 offers covering 178
sessions, 2 substitutions, 2 reassignment flags, 1 group with 2
enrollments, 13 session requests, 15 change requests, 195 attendance marks
with 3 corrections, 151 closeouts with 153 approvals (two corrected) and 2
closeout corrections, 5 availability change requests, 2 time-off
exceptions, 189 approval requests and 247 notifications (override rows are
not counted: they depend on the real date, D5)."""
SMALL_COURSES_ROWS: Final = 140
"""``small``: 2 templates with 2 subjects, 7 modules, 4 assessments and 2
snapshots; 2 cohorts with 4 enrollments; their 2 series with 4
participants, 27 sessions with 54 participants, 2 series offers covering 26
sessions and 2 notifications."""
SMALL_ACADEMICS_ROWS: Final = 626
"""``small`` (#551): 34 notes (21 shared), 3 evaluation templates with 9
criteria, 34 evaluations with 105 scores, 10 learning plans with 20
objectives and 40 milestones, 49 assignments with 49 submissions, 3 report
schedules, 40 progress reports with their 40 approval requests and 190
notifications."""
SETTINGS_ROWS: Final = 1
"""The scheduling-policy version effective at ``SIM_SETUP`` (the business
settings, hours and renames update existing rows)."""

FULL_PEOPLE_ROWS: Final = 524
"""``full``: 36 users, 35 user roles, 33 tokens, 34 preferences, 34
acceptances, 1 delegation, 2 registration requests and 2 approval requests,
30 families, 42 family guardians, 40 family students, 42 guardians, 40
students, 55 emergency contacts, 62 student-guardian links and 36
consents."""
FULL_CATALOG_ROWS: Final = 26
"""``full``: 12 subjects, 5 classrooms, 5 classroom windows, 1 academic
period and 3 closures."""
FULL_STAFF_ROWS: Final = 227
"""``full``: 12 profiles, 42 qualifications with 42 approval requests and
42 notifications, 12 availability settings, 60 weekly windows, 7 exceptions
and 10 preferred classrooms."""

FULL_SCHEDULE_ROWS: Final = 8844
"""``full`` (#550): 78 series (72 individual slots, 3 groups, 3 tutor 12's)
with 90 participants, 1085 sessions with 1416 participants, 106 offers
covering 1043 sessions, 8 substitutions, 8 reassignment flags, 3 groups
with 15 enrollments, 13 session requests, 65 change requests, 1170
attendance marks with 3 corrections, 818 closeouts with 820 approvals and 2
closeout corrections, 11 availability change requests, 8 time-off
exceptions, 912 approval requests and 1170 notifications."""
FULL_COURSES_ROWS: Final = 241
"""``full``: 2 templates with 2 subjects, 7 modules, 4 assessments and 2
snapshots; 2 cohorts with 11 enrollments; their 2 series with 11
participants, 26 sessions with 142 participants, 2 series offers covering
26 sessions and 2 notifications."""

FULL_ACADEMICS_ROWS: Final = 2231
"""``full`` (#551): 182 notes (113 shared), 3 evaluation templates with 9
criteria, 130 evaluations with 413 scores, 28 learning plans with 56
objectives and 112 milestones, 214 assignments with 214 submissions, 3
report schedules, 95 progress reports with their 95 approval requests and
677 notifications."""

MODULE_ROWS: Final[dict[str, dict[str, int]]] = {
    "small": {
        "people": SMALL_PEOPLE_ROWS,
        "catalog": SMALL_CATALOG_ROWS,
        "staff": SMALL_STAFF_ROWS,
        "settings": SETTINGS_ROWS,
        "schedule": SMALL_SCHEDULE_ROWS,
        "courses": SMALL_COURSES_ROWS,
        "academics": SMALL_ACADEMICS_ROWS,
    },
    "full": {
        "people": FULL_PEOPLE_ROWS,
        "catalog": FULL_CATALOG_ROWS,
        "staff": FULL_STAFF_ROWS,
        "settings": SETTINGS_ROWS,
        "schedule": FULL_SCHEDULE_ROWS,
        "courses": FULL_COURSES_ROWS,
        "academics": FULL_ACADEMICS_ROWS,
    },
}


def first_run_lines(profile: ProfileName) -> list[str]:
    return [
        *SEED_LINES,
        *(f"{name}: {rows} rows" for name, rows in MODULE_ROWS[profile].items()),
    ]


def migrate(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


@contextmanager
def simulation_env(url: str) -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("APP_ENV", "local")
        patch.setenv("APP_ORIGIN", origin_for("local"))
        patch.setenv("DATABASE_URL", url)
        patch.setenv("DATABASE_DIRECT_URL", url)
        get_settings.cache_clear()  # loaded once per process otherwise
        try:
            yield
        finally:
            get_settings.cache_clear()


def run_command(
    url: str,
    language: str,
    *,
    profile: ProfileName = "small",
    now: datetime = FROZEN_NOW,
    modules: Sequence[SimulationModule] = SIMULATION_MODULES,
) -> tuple[int, list[str], str]:
    """One run with the real clock frozen at ``now``; returns the exit code,
    the output lines and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with (
        simulation_env(url),
        frozen_clock(now),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        code = simulation_command.main(
            ["--language", language], profile=profile, modules=modules
        )
    return code, out.getvalue().splitlines(), err.getvalue()


def table_counts(url: str) -> dict[str, int]:
    """Every public table's row count (and the business settings version)."""
    names = [
        row[0]
        for row in run_sql(
            url,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            " ORDER BY tablename",
        )
    ]
    counts = {
        name: run_sql(url, f'SELECT count(*) FROM "{name}"')[0][0]  # noqa: S608
        for name in names
    }
    counts["business_settings.version"] = run_sql(
        url, "SELECT version FROM business_settings"
    )[0][0]
    return counts


# --- The canonical dump (C4) -------------------------------------------------------

DUMP_TABLES: Final = (
    "users",
    "user_roles",
    "account_tokens",
    "user_preferences",
    "privacy_notice_acceptances",
    "user_delegations",
    "registration_requests",
    "approval_requests",
    "notifications",
    "families",
    "family_guardians",
    "family_students",
    "guardians",
    "students",
    "student_emergency_contacts",
    "student_guardians",
    "consent_records",
    "subjects",
    "classrooms",
    "classroom_availability_windows",
    "academic_periods",
    "closures",
    "tutor_profiles",
    "tutor_qualifications",
    "tutor_availability_settings",
    "tutor_availability_windows",
    "tutor_availability_exceptions",
    "tutor_preferred_classrooms",
    "business_settings",
    "operating_hours",
    "scheduling_policies",
    "education_levels",
    "expense_categories",
    # HF-01b (#550): the schedule and the courses.
    "class_series",
    "class_series_participants",
    "class_sessions",
    "class_session_participants",
    "class_session_overrides",
    "assignment_offers",
    "assignment_offer_sessions",
    "class_session_substitutions",
    "class_session_reassignment_flags",
    "class_groups",
    "group_enrollments",
    "session_requests",
    "session_change_requests",
    "class_session_attendance",
    "class_session_attendance_corrections",
    "session_closeouts",
    "session_closeout_approvals",
    "session_closeout_corrections",
    "tutor_availability_change_requests",
    "course_templates",
    "course_template_subjects",
    "course_template_modules",
    "course_template_assessments",
    "course_template_snapshots",
    "course_cohorts",
    "cohort_enrollments",
    # HF-01c (#551): academics.
    "session_notes",
    "evaluation_templates",
    "evaluation_template_criteria",
    "evaluations",
    "evaluation_scores",
    "learning_plans",
    "learning_plan_objectives",
    "learning_plan_milestones",
    "assignments",
    "assignment_submissions",
    "progress_report_schedules",
    "progress_reports",
)
"""The tables HF-01, HF-01b and HF-01c write."""

REFERENCE_TABLES: Final = (
    "roles",
    "permissions",
    "privacy_notice_versions",
    "payment_methods",
)
"""Tables only referenced (their rows resolve foreign keys)."""

LEFT_OUT: Final = frozenset(
    {"id", "created_at", "updated_at", "version", "password_hash", "token_hash"}
)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


async def _load(url: str) -> dict[str, list[dict[str, Any]]]:
    engine = short_lived_engine(url)
    rows: dict[str, list[dict[str, Any]]] = {}
    try:
        async with engine.connect() as conn:
            for table in (*DUMP_TABLES, *REFERENCE_TABLES):
                result = await conn.execute(text(f'SELECT * FROM "{table}"'))  # noqa: S608
                rows[table] = [dict(row._mapping) for row in result]
    finally:
        await engine.dispose()
    return rows


def canonical_dump(url: str) -> dict[str, list[str]]:
    """Every row of :data:`DUMP_TABLES`, canonical (C4): sorted, without
    :data:`LEFT_OUT` columns, every id (column or JSON value) replaced by
    the canonical form of the row it names."""
    tables = asyncio.run(_load(url))
    by_id: dict[uuid.UUID, dict[str, Any]] = {
        row["id"]: row for rows in tables.values() for row in rows if "id" in row
    }
    cache: dict[uuid.UUID, str] = {}

    def resolve(value: uuid.UUID, depth: int) -> str:
        if value in cache:
            return cache[value]
        row = by_id.get(value)
        if row is None or depth > 6:
            return "<unknown>"
        label = canon_row(row, depth + 1)
        cache[value] = label
        return label

    def canon(value: Any, depth: int) -> Any:
        if isinstance(value, uuid.UUID):
            return resolve(value, depth)
        if isinstance(value, str) and _UUID.match(value):
            return resolve(uuid.UUID(value), depth)
        if isinstance(value, dict):
            # Keys can be ids too (a closeout approval's attendance by student).
            return dict(
                sorted(
                    (
                        resolve(uuid.UUID(k), depth) if _UUID.match(k) else k,
                        canon(v, depth),
                    )
                    for k, v in value.items()
                )
            )
        if isinstance(value, list):
            return [canon(v, depth) for v in value]
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat()
        return value if isinstance(value, int | float | bool | None) else str(value)

    def canon_row(row: dict[str, Any], depth: int) -> str:
        kept = {
            key: canon(value, depth)
            for key, value in sorted(row.items())
            if key not in LEFT_OUT
        }
        return json.dumps(kept, sort_keys=True, ensure_ascii=False)

    return {
        table: sorted(canon_row(row, 0) for row in tables[table])
        for table in DUMP_TABLES
    }


# --- HF-01b free text (B10) -------------------------------------------------------

FREE_TEXT = (
    ("session_change_requests", "note"),
    ("session_change_requests", "decision_reason"),
    ("session_requests", "note"),
    ("session_requests", "rejection_reason"),
    ("assignment_offers", "decline_note"),
    ("class_session_substitutions", "reason"),
    ("class_sessions", "cancellation_reason"),
    ("class_session_attendance_corrections", "reason"),
    ("session_closeouts", "topics"),
    ("session_closeouts", "performance"),
    ("session_closeouts", "homework"),
    ("session_closeouts", "next_steps"),
    ("session_closeouts", "review_reason"),
    ("session_closeout_corrections", "reason"),
    ("tutor_availability_change_requests", "rejection_reason"),
    ("tutor_availability_exceptions", "note"),
    ("class_groups", "name"),
    ("course_templates", "name"),
    ("course_templates", "description"),
    ("course_template_modules", "title"),
    ("course_template_modules", "materials"),
    ("course_template_assessments", "title"),
    ("course_cohorts", "name"),
    ("class_session_overrides", "reason"),
)


def free_text(url: str) -> list[tuple[str, str]]:
    """Every distinct value of :data:`FREE_TEXT`, with its column."""
    found = []
    for table, column in FREE_TEXT:
        for (value,) in run_sql(
            url,
            f"SELECT DISTINCT {column} FROM {table}"  # noqa: S608
            f" WHERE {column} IS NOT NULL",
        ):
            found.append((f"{table}.{column}", value))
    return found


SERVICE_CANCELLATION_REASONS: Final = frozenset({"family_request", "tutor_request"})
"""The cancellation reasons an approved request stores (codes, not text)."""


# --- HF-01c free text (B6) --------------------------------------------------------

ACADEMIC_FREE_TEXT: Final = (
    ("session_notes", "body"),
    ("evaluation_templates", "name"),
    ("evaluation_templates", "description"),
    ("evaluation_template_criteria", "title"),
    ("evaluations", "template_name"),
    ("evaluations", "feedback"),
    ("evaluations", "course_assessment_title"),
    ("evaluation_scores", "criterion_title"),
    ("evaluation_scores", "comment"),
    ("learning_plans", "title"),
    ("learning_plans", "description"),
    ("learning_plan_objectives", "title"),
    ("learning_plan_milestones", "title"),
    ("learning_plan_milestones", "activities"),
    ("assignments", "title"),
    ("assignments", "instructions"),
    ("assignment_submissions", "text"),
    ("assignment_submissions", "feedback"),
    ("progress_reports", "summary"),
    ("progress_reports", "strengths"),
    ("progress_reports", "areas_for_improvement"),
    ("progress_reports", "next_steps"),
    ("progress_reports", "changes_requested_reason"),
)


def academic_free_text(url: str) -> list[tuple[str, str]]:
    """Every distinct value of :data:`ACADEMIC_FREE_TEXT`, with its column."""
    found = []
    for table, column in ACADEMIC_FREE_TEXT:
        for (value,) in run_sql(
            url,
            f"SELECT DISTINCT {column} FROM {table}"  # noqa: S608
            f" WHERE {column} IS NOT NULL",
        ):
            found.append((f"{table}.{column}", value))
    return found


def composed_texts(plan: SimulationPlan) -> set[str]:
    """The academic texts the plan composes from pool fragments (note
    bodies, plan and homework titles, a discontinued plan's description);
    every other academic text is a pool value as is."""
    work = plan.academics
    return {
        *(n.body for n in work.notes),
        *(p.title for p in work.plans),
        *(f"{p.description} {p.reason}" for p in work.plans if p.reason),
        *(h.title for h in work.homework),
    }
