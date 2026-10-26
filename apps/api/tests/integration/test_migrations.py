"""Alembic history (M1-07 C5-C8, M1-16 B, M2-02 D4, M2-05 A3, M2-07 B2, B3,
M2-08 A1, A2, M2-09 A, M2-11 B, M2-13 A, M2-14 A, M2-15 A, M3-01 A, M3-02 A,
M3-03 A, M3-04 A, M3-05 A, M3-09 A, M4-02 A, M4-16 A, M4-19 B); each test uses a
fresh
database."""

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.db import async_direct_database_url
from app.core.model_registry import include_name, target_metadata
from app.modules.scheduling.models import CHANGE_REQUEST_CHECKS
from app.modules.staff.models import (
    CHANGE_REQUEST_CHECKS as AVAILABILITY_CHANGE_CHECKS,
)
from tests.authz.csrf import origin_for
from tests.integration.conftest import run_sql, settings_for, short_lived_engine

API_ROOT = Path(__file__).resolve().parents[2]


def alembic_config(dsn: str | None = None) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.attributes["configure_logging"] = False
    if dsn is not None:
        config.attributes["database_url"] = async_direct_database_url(
            settings_for(dsn)
        ).render_as_string(hide_password=False)
    return config


def version_nums(dsn: str) -> list[str]:
    return [
        row.version_num
        for row in run_sql(dsn, "SELECT version_num FROM alembic_version")
    ]


def metadata_diff(dsn: str) -> list[object]:
    def _compare(connection: Connection) -> list[object]:
        return list(
            compare_metadata(
                MigrationContext.configure(
                    connection, opts={"include_name": include_name}
                ),
                target_metadata,
            )
        )

    async def _run() -> list[object]:
        engine = short_lived_engine(dsn)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(_compare)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


HEAD = "0056"

IDENTITY_TABLES = [
    "permissions",
    "role_permissions",
    "roles",
    "user_delegations",
    "user_roles",
    "users",
]

AUTH_TABLES = ["auth_sessions"]

RATE_LIMIT_TABLES = ["rate_limit_counters"]
ACCOUNT_TOKEN_TABLES = ["account_tokens"]
REGISTRATION_TABLES = ["registration_requests"]
CATALOG_TABLES = ["education_levels", "subjects"]
FAMILY_TABLES = ["student_emergency_contacts", "students"]
GUARDIAN_TABLES = ["guardians", "student_guardians"]
GROUPING_TABLES = ["families", "family_guardians", "family_students"]
PRIVACY_TABLES = ["privacy_notice_acceptances", "privacy_notice_versions"]
CONSENT_TABLES = ["consent_records"]
PREFERENCE_TABLES = ["user_preferences"]
SETTINGS_TABLES = ["business_settings", "operating_hours"]
PERIOD_TABLES = ["academic_periods", "closures"]
CLASSROOM_TABLES = ["classroom_availability_windows", "classrooms"]
APPROVAL_TABLES = ["approval_requests"]
NOTIFICATION_TABLES = ["notifications"]
STAFF_TABLES = ["tutor_profiles"]
QUALIFICATION_TABLES = ["tutor_qualifications"]
AVAILABILITY_TABLES = [
    "tutor_availability_exceptions",
    "tutor_availability_settings",
    "tutor_availability_windows",
]
PREFERRED_ROOM_TABLES = ["tutor_preferred_classrooms"]
SESSION_TABLES = ["class_session_participants", "class_sessions"]
POLICY_TABLES = ["scheduling_policies"]
COURSE_TABLES = [
    "course_template_assessments",
    "course_template_modules",
    "course_template_snapshots",
    "course_template_subjects",
    "course_templates",
]
OVERRIDE_TABLES = ["class_session_overrides"]
OFFER_TABLES = ["assignment_offer_sessions", "assignment_offers"]
SERIES_TABLES = ["class_series", "class_series_participants"]
SUBSTITUTION_TABLES = ["class_session_substitutions"]
GROUP_TABLES = ["class_groups", "group_enrollments"]
SESSION_REQUEST_TABLES = ["session_requests"]
CHANGE_REQUEST_TABLES = ["session_change_requests"]
AVAILABILITY_CHANGE_TABLES = [
    "class_session_reassignment_flags",
    "tutor_availability_change_requests",
]
ATTENDANCE_TABLES = ["class_session_attendance", "class_session_attendance_corrections"]
CLOSEOUT_TABLES = ["session_closeouts"]
CLOSEOUT_REVIEW_TABLES = ["session_closeout_approvals", "session_closeout_corrections"]
NOTE_TABLES = ["session_notes"]
EVALUATION_TABLES = [
    "evaluation_templates",
    "evaluation_template_criteria",
    "evaluations",
    "evaluation_scores",
]
LEARNING_PLAN_TABLES = [
    "learning_plans",
    "learning_plan_objectives",
    "learning_plan_milestones",
]
ASSIGNMENT_TABLES = ["assignments", "assignment_submissions"]
CLOSEOUT_REVIEW_COLUMNS = {
    "last_submitted_at",
    "review_reason",
    "tutor_note",
    "current_approval_id",
}
"""The session_closeouts columns revision 0051 adds (#78 A1)."""
ON_BEHALF_COLUMNS = {"on_behalf_by_user_id", "on_behalf_reason"}
"""The session_closeouts columns revision 0052 adds (#301 A1)."""
OVERDUE_INDEX = "ix_class_sessions_confirmed_ends_at"
"""The class_sessions partial index revision 0052 adds (#301 A1)."""
SERIES_SESSION_COLUMNS = [
    "series_id",
    "original_start",
    "is_exception",
    "conflict_codes",
]
"""The class_sessions columns revision 0035 adds (#53 B4)."""
SERIES_OFFER_COLUMNS = {
    "assignment_offers": {"class_series_id", "from_date"},
    "class_series": {"assignment_status", "assignment_from"},
}
"""The columns revision 0036 adds (#233 A1, A2)."""
SERIES_OFFER_INDEXES = {
    "ix_assignment_offers_class_series_id",
    "uq_assignment_offers_pending_series",
}

PROCRASTINATE_TABLES = [
    "procrastinate_events",
    "procrastinate_jobs",
    "procrastinate_periodic_defers",
    "procrastinate_workers",
]

# Catalog objects named procrastinate%: relations (tables, indexes, sequences),
# functions and types (enums, composite and row types).
PROCRASTINATE_OBJECT_COUNTS = (
    "SELECT (SELECT count(*) FROM pg_class WHERE relname LIKE 'procrastinate%') AS rel,"
    " (SELECT count(*) FROM pg_proc WHERE proname LIKE 'procrastinate%') AS proc,"
    " (SELECT count(*) FROM pg_type WHERE typname LIKE 'procrastinate%') AS type,"
    " (SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'procrastinate%') AS trig"
)


def procrastinate_object_counts(dsn: str) -> tuple[int, int, int, int]:
    row = run_sql(dsn, PROCRASTINATE_OBJECT_COUNTS)[0]
    return (row.rel, row.proc, row.type, row.trig)


def test_history_is_linear_with_one_head():
    script = ScriptDirectory.from_config(alembic_config())

    assert script.get_heads() == [HEAD]
    baseline = script.get_revision("0001")
    assert baseline.down_revision is None
    assert Path(baseline.path).name == "0001_baseline.py"
    procrastinate = script.get_revision("0002")
    assert procrastinate.down_revision == "0001"
    assert Path(procrastinate.path).name == "0002_procrastinate_3_10_0.py"
    identity = script.get_revision("0003")
    assert identity.down_revision == "0002"
    assert Path(identity.path).name == "0003_identity_users_roles_permissions.py"
    audit = script.get_revision("0004")
    assert audit.down_revision == "0003"
    assert Path(audit.path).name == "0004_audit_events.py"
    auth_sessions = script.get_revision("0005")
    assert auth_sessions.down_revision == "0004"
    assert Path(auth_sessions.path).name == "0005_identity_auth_sessions.py"
    lifecycle = script.get_revision("0006")
    assert lifecycle.down_revision == "0005"
    assert Path(lifecycle.path).name == "0006_identity_session_lifecycle.py"
    reauth = script.get_revision("0007")
    assert reauth.down_revision == "0006"
    assert Path(reauth.path).name == "0007_identity_session_reauth.py"
    rate_limits = script.get_revision("0008")
    assert rate_limits.down_revision == "0007"
    assert Path(rate_limits.path).name == "0008_rate_limit_counters.py"
    account_tokens = script.get_revision("0009")
    assert account_tokens.down_revision == "0008"
    assert Path(account_tokens.path).name == "0009_identity_account_tokens.py"
    reset_tokens = script.get_revision("0010")
    assert reset_tokens.down_revision == "0009"
    assert Path(reset_tokens.path).name == "0010_identity_password_reset_tokens.py"
    registrations = script.get_revision("0011")
    assert registrations.down_revision == "0010"
    assert Path(registrations.path).name == "0011_identity_registration_requests.py"
    contact = script.get_revision("0012")
    assert contact.down_revision == "0011"
    assert Path(contact.path).name == "0012_identity_user_contact.py"
    catalog = script.get_revision("0013")
    assert catalog.down_revision == "0012"
    assert Path(catalog.path).name == "0013_catalog_education_levels_subjects.py"
    families = script.get_revision("0014")
    assert families.down_revision == "0013"
    assert Path(families.path).name == "0014_families_students.py"
    guardians = script.get_revision("0015")
    assert guardians.down_revision == "0014"
    assert Path(guardians.path).name == "0015_families_guardians.py"
    groupings = script.get_revision("0016")
    assert groupings.down_revision == "0015"
    assert Path(groupings.path).name == "0016_families_groupings.py"
    privacy = script.get_revision("0017")
    assert privacy.down_revision == "0016"
    assert Path(privacy.path).name == "0017_identity_privacy_notice.py"
    consent = script.get_revision("0018")
    assert consent.down_revision == "0017"
    assert Path(consent.path).name == "0018_families_consent.py"
    preferences = script.get_revision("0019")
    assert preferences.down_revision == "0018"
    assert Path(preferences.path).name == "0019_identity_user_preferences.py"
    settings = script.get_revision("0020")
    assert settings.down_revision == "0019"
    assert Path(settings.path).name == "0020_administration_business_settings.py"
    periods = script.get_revision("0021")
    assert periods.down_revision == "0020"
    assert Path(periods.path).name == "0021_catalog_academic_periods_closures.py"
    classrooms = script.get_revision("0022")
    assert classrooms.down_revision == "0021"
    assert Path(classrooms.path).name == "0022_catalog_classrooms.py"
    approvals = script.get_revision("0023")
    assert approvals.down_revision == "0022"
    assert Path(approvals.path).name == "0023_administration_approval_requests.py"
    notifications = script.get_revision("0024")
    assert notifications.down_revision == "0023"
    assert Path(notifications.path).name == "0024_administration_notifications.py"
    tutor_profiles = script.get_revision("0025")
    assert tutor_profiles.down_revision == "0024"
    assert Path(tutor_profiles.path).name == "0025_staff_tutor_profiles.py"
    availability = script.get_revision("0027")
    assert availability.down_revision == "0026"
    assert Path(availability.path).name == "0027_staff_tutor_availability.py"
    revocation = script.get_revision("0028")
    assert revocation.down_revision == "0027"
    assert Path(revocation.path).name == "0028_staff_qualification_revocation.py"
    limits = script.get_revision("0029")
    assert limits.down_revision == "0028"
    assert Path(limits.path).name == "0029_staff_tutor_limits_rooms.py"
    sessions = script.get_revision("0030")
    assert sessions.down_revision == "0029"
    assert Path(sessions.path).name == "0030_scheduling_class_sessions.py"
    policies = script.get_revision("0031")
    assert policies.down_revision == "0030"
    assert Path(policies.path).name == "0031_administration_scheduling_policies.py"
    courses = script.get_revision("0032")
    assert courses.down_revision == "0031"
    assert Path(courses.path).name == "0032_courses_course_templates.py"
    overrides = script.get_revision("0033")
    assert overrides.down_revision == "0032"
    assert Path(overrides.path).name == "0033_scheduling_session_overrides.py"
    offers = script.get_revision("0034")
    assert offers.down_revision == "0033"
    assert Path(offers.path).name == "0034_scheduling_assignment_offers.py"
    series = script.get_revision("0035")
    assert series.down_revision == "0034"
    assert Path(series.path).name == "0035_scheduling_class_series.py"
    series_offers = script.get_revision("0036")
    assert series_offers.down_revision == "0035"
    assert Path(series_offers.path).name == "0036_scheduling_series_offers.py"


def test_alembic_ini_has_no_database_url():
    ini = (API_ROOT / "alembic.ini").read_text()

    assert "sqlalchemy.url" not in ini
    assert "postgresql" not in ini


def test_upgrade_empty_database_to_head(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert version_nums(fresh_database_url) == [HEAD]
    # Beyond plpgsql, only the ISSUE-D6 name-search extensions (#33) and
    # btree_gist (#38 E1): usernames are normalized and CHECKed, not citext.
    extensions = run_sql(
        fresh_database_url, "SELECT extname FROM pg_extension ORDER BY extname"
    )
    assert [row.extname for row in extensions] == [
        "btree_gist",
        "pg_trgm",
        "plpgsql",
        "unaccent",
    ]
    tables = run_sql(
        fresh_database_url,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name",
    )
    assert [row.table_name for row in tables] == sorted(
        [
            "alembic_version",
            "audit_events",
            *ACCOUNT_TOKEN_TABLES,
            *APPROVAL_TABLES,
            *NOTIFICATION_TABLES,
            *STAFF_TABLES,
            *QUALIFICATION_TABLES,
            *AVAILABILITY_TABLES,
            *PREFERRED_ROOM_TABLES,
            *SESSION_TABLES,
            *POLICY_TABLES,
            *COURSE_TABLES,
            *COHORT_TABLES,
            *COHORT_ENROLLMENT_TABLES,
            *OVERRIDE_TABLES,
            *OFFER_TABLES,
            *SERIES_TABLES,
            *SUBSTITUTION_TABLES,
            *GROUP_TABLES,
            *SESSION_REQUEST_TABLES,
            *CHANGE_REQUEST_TABLES,
            *AVAILABILITY_CHANGE_TABLES,
            *ATTENDANCE_TABLES,
            *CLOSEOUT_TABLES,
            *CLOSEOUT_REVIEW_TABLES,
            *NOTE_TABLES,
            *EVALUATION_TABLES,
            *LEARNING_PLAN_TABLES,
            *ASSIGNMENT_TABLES,
            *AUTH_TABLES,
            *CATALOG_TABLES,
            *CLASSROOM_TABLES,
            *CONSENT_TABLES,
            *FAMILY_TABLES,
            *GUARDIAN_TABLES,
            *GROUPING_TABLES,
            *IDENTITY_TABLES,
            *PERIOD_TABLES,
            *PREFERENCE_TABLES,
            *PRIVACY_TABLES,
            *PROCRASTINATE_TABLES,
            *RATE_LIMIT_TABLES,
            *REGISTRATION_TABLES,
            *SETTINGS_TABLES,
        ]
    )


def public_tables(dsn: str) -> set[str]:
    rows = run_sql(
        dsn,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public'",
    )
    return {row.table_name for row in rows}


def test_downgrade_to_0002_drops_exactly_the_identity_tables(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0002")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0003")
    assert public_tables(fresh_database_url) - before == set(IDENTITY_TABLES)
    command.upgrade(config, "head")

    command.downgrade(config, "0002")

    assert version_nums(fresh_database_url) == ["0002"]
    assert public_tables(fresh_database_url) == before


AUDIT_OBJECTS = (
    "SELECT (SELECT count(*) FROM pg_proc"
    "        WHERE proname = 'audit_events_append_only') AS functions,"
    " (SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_events%') AS triggers"
)


def test_downgrade_to_0003_drops_exactly_the_audit_objects(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0003")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0004")
    assert public_tables(fresh_database_url) - before == {"audit_events"}
    assert tuple(run_sql(fresh_database_url, AUDIT_OBJECTS)[0]) == (1, 2)

    command.downgrade(config, "0003")

    assert version_nums(fresh_database_url) == ["0003"]
    assert public_tables(fresh_database_url) == before
    assert tuple(run_sql(fresh_database_url, AUDIT_OBJECTS)[0]) == (0, 0)
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_downgrade_to_0004_drops_exactly_auth_sessions(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0004")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0005")
    assert public_tables(fresh_database_url) - before == set(AUTH_TABLES)
    command.upgrade(config, "head")

    command.downgrade(config, "0004")

    assert version_nums(fresh_database_url) == ["0004"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def auth_session_columns(dsn: str) -> set[str]:
    return {
        row.column_name
        for row in run_sql(
            dsn,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'auth_sessions'",
        )
    }


def auth_session_constraints(dsn: str) -> set[str]:
    return {
        row.conname
        for row in run_sql(
            dsn,
            "SELECT conname FROM pg_constraint"
            " WHERE conrelid = 'auth_sessions'::regclass AND contype <> 'n'",
        )
    }


def test_downgrade_to_0005_drops_exactly_the_expiry_columns(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0005")
    columns = auth_session_columns(fresh_database_url)
    constraints = auth_session_constraints(fresh_database_url)
    tables = public_tables(fresh_database_url)
    command.upgrade(config, "0006")
    assert auth_session_columns(fresh_database_url) - columns == {
        "expires_at",
        "idle_timeout_seconds",
    }
    assert auth_session_constraints(fresh_database_url) - constraints == {
        "ck_auth_sessions_idle_timeout_seconds_positive"
    }

    command.downgrade(config, "0005")

    assert version_nums(fresh_database_url) == ["0005"]
    assert auth_session_columns(fresh_database_url) == columns
    assert auth_session_constraints(fresh_database_url) == constraints
    assert public_tables(fresh_database_url) == tables
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_upgrade_to_0006_backfills_existing_sessions_with_the_staff_limits(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0005")
    run_sql(
        fresh_database_url,
        "INSERT INTO users (username, display_name, state)"
        " VALUES ('old.session', 'Old Session', 'active')",
    )
    run_sql(
        fresh_database_url,
        "INSERT INTO auth_sessions (user_id, token_hash, created_at, last_seen_at)"
        " SELECT id, sha256('t'::bytea), '2026-10-01 08:00:00+00',"
        " '2026-10-01 09:00:00+00' FROM users",
    )

    command.upgrade(config, "0006")

    [row] = run_sql(
        fresh_database_url,
        "SELECT expires_at, idle_timeout_seconds, created_at, last_seen_at"
        " FROM auth_sessions",
    )
    assert row.expires_at - row.created_at == timedelta(hours=12)
    assert row.idle_timeout_seconds == 1800
    assert row.last_seen_at.isoformat() == "2026-10-01T09:00:00+00:00"


def test_downgrade_to_0006_drops_exactly_the_reauth_column(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0006")
    columns = auth_session_columns(fresh_database_url)
    constraints = auth_session_constraints(fresh_database_url)
    indexes = auth_session_indexes(fresh_database_url)
    tables = public_tables(fresh_database_url)
    command.upgrade(config, "head")
    assert auth_session_columns(fresh_database_url) - columns == {"reauthenticated_at"}
    assert auth_session_constraints(fresh_database_url) == constraints
    assert auth_session_indexes(fresh_database_url) == indexes

    command.downgrade(config, "0006")

    assert version_nums(fresh_database_url) == ["0006"]
    assert auth_session_columns(fresh_database_url) == columns
    assert auth_session_constraints(fresh_database_url) == constraints
    assert public_tables(fresh_database_url) == tables
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_upgrade_to_0007_leaves_existing_sessions_unmarked(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0006")
    run_sql(
        fresh_database_url,
        "INSERT INTO users (username, display_name, state)"
        " VALUES ('old.session', 'Old Session', 'active')",
    )
    run_sql(
        fresh_database_url,
        "INSERT INTO auth_sessions (user_id, token_hash, expires_at,"
        " idle_timeout_seconds) SELECT id, sha256('t'::bytea), now(), 1800"
        " FROM users",
    )

    command.upgrade(config, "0007")

    [row] = run_sql(fresh_database_url, "SELECT reauthenticated_at FROM auth_sessions")
    assert row.reauthenticated_at is None


def auth_session_indexes(dsn: str) -> set[str]:
    return {
        row.indexname
        for row in run_sql(
            dsn, "SELECT indexname FROM pg_indexes WHERE tablename = 'auth_sessions'"
        )
    }


def test_auth_sessions_columns_constraints_and_index(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    columns = run_sql(
        fresh_database_url,
        "SELECT column_name, data_type, is_nullable, column_default"
        " FROM information_schema.columns WHERE table_name = 'auth_sessions'",
    )
    by_name = {row.column_name: row for row in columns}
    assert set(by_name) == {
        "id",
        "created_at",
        "updated_at",
        "user_id",
        "token_hash",
        "last_seen_at",
        "revoked_at",
        "expires_at",
        "idle_timeout_seconds",
        "reauthenticated_at",
    }
    assert (by_name["token_hash"].data_type, by_name["token_hash"].is_nullable) == (
        "bytea",
        "NO",
    )
    assert by_name["user_id"].is_nullable == "NO"
    assert by_name["last_seen_at"].data_type == "timestamp with time zone"
    assert by_name["last_seen_at"].is_nullable == "NO"
    assert by_name["last_seen_at"].column_default == "now()"
    assert by_name["revoked_at"].data_type == "timestamp with time zone"
    assert by_name["revoked_at"].is_nullable == "YES"
    # #25: no defaults, both required.
    assert (
        by_name["expires_at"].data_type,
        by_name["expires_at"].is_nullable,
        by_name["expires_at"].column_default,
    ) == ("timestamp with time zone", "NO", None)
    assert (
        by_name["idle_timeout_seconds"].data_type,
        by_name["idle_timeout_seconds"].is_nullable,
        by_name["idle_timeout_seconds"].column_default,
    ) == ("integer", "NO", None)
    # #26: nullable, no default (NULL = never reauthenticated in this session).
    assert (
        by_name["reauthenticated_at"].data_type,
        by_name["reauthenticated_at"].is_nullable,
        by_name["reauthenticated_at"].column_default,
    ) == ("timestamp with time zone", "YES", None)
    constraints = run_sql(
        fresh_database_url,
        "SELECT conname, contype::text, pg_get_constraintdef(oid) AS def,"
        " confdeltype::text FROM pg_constraint"
        " WHERE conrelid = 'auth_sessions'::regclass AND contype <> 'n'"
        " ORDER BY conname",  # PostgreSQL 18 also lists NOT NULL ('n')
    )
    assert {(row.conname, row.contype) for row in constraints} == {
        ("ck_auth_sessions_token_hash_length", "c"),
        ("ck_auth_sessions_idle_timeout_seconds_positive", "c"),
        ("fk_auth_sessions_user_id_users", "f"),
        ("pk_auth_sessions", "p"),
        ("uq_auth_sessions_token_hash", "u"),
    }
    [fk] = [row for row in constraints if row.contype == "f"]
    assert fk.confdeltype == "r"  # RESTRICT, never CASCADE
    indexes = run_sql(
        fresh_database_url,
        "SELECT indexname FROM pg_indexes WHERE tablename = 'auth_sessions'",
    )
    assert "ix_auth_sessions_user_id" in {row.indexname for row in indexes}


def test_audit_events_has_the_three_keyset_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    rows = run_sql(
        fresh_database_url,
        "SELECT indexname, indexdef FROM pg_indexes"
        " WHERE tablename = 'audit_events' ORDER BY indexname",
    )

    assert {row.indexname: row.indexdef.split("USING btree ")[1] for row in rows} == {
        "ix_audit_events_actor": "(actor_user_id, occurred_at, id)",
        "ix_audit_events_occurred_at": "(occurred_at, id)",
        "ix_audit_events_record": "(record_type, record_id, occurred_at, id)",
        "pk_audit_events": "(id)",
    }


def test_identity_foreign_keys_never_cascade(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    tables = ", ".join(f"'{table}'" for table in IDENTITY_TABLES)
    rows = run_sql(
        fresh_database_url,
        # Table names are test constants, never user input.
        "SELECT conname, confdeltype::text, confupdtype::text FROM pg_constraint "  # noqa: S608
        f"WHERE contype = 'f' AND conrelid::regclass::text IN ({tables})",
    )
    # r = RESTRICT, a = NO ACTION; never c (CASCADE), n (SET NULL), d (SET DEFAULT).
    assert len(rows) == 7
    assert {(row.confdeltype, row.confupdtype) for row in rows} == {("r", "a")}
    assert {row.conname for row in rows} == {
        "fk_role_permissions_permission_id_permissions",
        "fk_role_permissions_role_id_roles",
        "fk_user_roles_user_id_users",
        "fk_user_roles_role_id_roles",
        "fk_user_delegations_user_id_users",
        "fk_user_delegations_permission_id_permissions",
        "fk_user_delegations_granted_by_user_id_users",
    }


def test_downgrade_base_then_upgrade_head(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")
    assert version_nums(fresh_database_url) == []

    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_downgrade_to_0001_removes_every_procrastinate_object(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "head")
    rel, proc, type_, trig = procrastinate_object_counts(fresh_database_url)
    assert rel > 0 and proc > 0 and type_ > 0 and trig > 0

    command.downgrade(config, "0001")

    assert version_nums(fresh_database_url) == ["0001"]
    assert procrastinate_object_counts(fresh_database_url) == (0, 0, 0, 0)
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_upgrade_is_a_no_op_at_head(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0001")
    command.upgrade(config, "head")
    counts = procrastinate_object_counts(fresh_database_url)

    command.upgrade(config, "head")

    assert version_nums(fresh_database_url) == [HEAD]
    assert procrastinate_object_counts(fresh_database_url) == counts


def test_metadata_matches_migrations(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert metadata_diff(fresh_database_url) == []


def test_env_reads_the_direct_url_from_settings(fresh_database_url, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_ORIGIN", origin_for("local"))
    # Closed port: migrating through DATABASE_URL would fail.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@127.0.0.1:1/unused")
    monkeypatch.setenv("DATABASE_DIRECT_URL", fresh_database_url)

    command.upgrade(alembic_config(), "head")

    assert version_nums(fresh_database_url) == [HEAD]


def test_offline_mode_is_refused(fresh_database_url):
    with pytest.raises(SystemExit):
        command.upgrade(alembic_config(fresh_database_url), "head", sql=True)


# --- 0008: rate_limit_counters (#27 A) ----------------------------------------------

RATE_LIMIT_INSERT = (
    "INSERT INTO rate_limit_counters"
    " (policy, key_hash, window_start, window_end, count)"
    " VALUES (:policy, :hash, :start, :end, :count)"
)
WINDOW_START = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 18, 12, 15, tzinfo=UTC)


def counter_row(**overrides: Any) -> dict[str, Any]:
    return {
        "policy": "login.ip",
        "hash": bytes(32),
        "start": WINDOW_START,
        "end": WINDOW_END,
        "count": 1,
        **overrides,
    }


def test_downgrade_to_0007_drops_exactly_the_rate_limit_table(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0007")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0008")
    assert public_tables(fresh_database_url) - before == set(RATE_LIMIT_TABLES)
    command.upgrade(config, "head")

    command.downgrade(config, "0007")

    assert version_nums(fresh_database_url) == ["0007"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_rate_limit_counters_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    columns = run_sql(
        fresh_database_url,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
        " WHERE table_name = 'rate_limit_counters'",
    )
    assert {(r.column_name, r.data_type, r.is_nullable) for r in columns} == {
        ("id", "uuid", "NO"),
        ("created_at", "timestamp with time zone", "NO"),
        ("updated_at", "timestamp with time zone", "NO"),
        ("policy", "text", "NO"),
        ("key_hash", "bytea", "NO"),
        ("window_start", "timestamp with time zone", "NO"),
        ("window_end", "timestamp with time zone", "NO"),
        ("count", "integer", "NO"),
    }
    constraints = run_sql(
        fresh_database_url,
        "SELECT conname, contype::text, pg_get_constraintdef(oid) AS definition"
        " FROM pg_constraint WHERE conrelid = 'rate_limit_counters'::regclass"
        " AND contype <> 'n' ORDER BY conname",
    )
    assert [(r.conname, r.contype) for r in constraints] == [
        ("ck_rate_limit_counters_count_not_negative", "c"),
        ("ck_rate_limit_counters_key_hash_length", "c"),
        ("ck_rate_limit_counters_window_order", "c"),
        ("pk_rate_limit_counters", "p"),
        ("uq_rate_limit_counters_policy_key_window", "u"),
    ]
    by_name = {r.conname: r.definition for r in constraints}
    assert by_name["uq_rate_limit_counters_policy_key_window"] == (
        "UNIQUE (policy, key_hash, window_start)"
    )
    indexes = run_sql(
        fresh_database_url,
        "SELECT indexname, indexdef FROM pg_indexes"
        " WHERE tablename = 'rate_limit_counters' ORDER BY indexname",
    )
    assert [r.indexname for r in indexes] == [
        "ix_rate_limit_counters_window_end",
        "pk_rate_limit_counters",
        "uq_rate_limit_counters_policy_key_window",
    ]
    assert "(window_end)" in indexes[0].indexdef
    foreign_keys = run_sql(
        fresh_database_url,
        "SELECT count(*) FROM pg_constraint"
        " WHERE conrelid = 'rate_limit_counters'::regclass AND contype = 'f'",
    )
    assert foreign_keys[0][0] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"hash": bytes(31)}, id="31-byte-key-hash"),
        pytest.param({"count": -1}, id="negative-count"),
        pytest.param({"end": WINDOW_START}, id="empty-window"),
    ],
)
def test_rate_limit_counters_reject_invalid_rows(fresh_database_url, overrides):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError):
        run_sql(fresh_database_url, RATE_LIMIT_INSERT, counter_row(**overrides))


def test_rate_limit_counters_reject_a_duplicate_window(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    run_sql(fresh_database_url, RATE_LIMIT_INSERT, counter_row())
    # Another policy, key or window is a different row.
    run_sql(fresh_database_url, RATE_LIMIT_INSERT, counter_row(policy="reauth.user"))
    run_sql(fresh_database_url, RATE_LIMIT_INSERT, counter_row(hash=b"\x01" * 32))
    run_sql(
        fresh_database_url,
        RATE_LIMIT_INSERT,
        counter_row(start=WINDOW_END, end=WINDOW_END + timedelta(minutes=15)),
    )

    with pytest.raises(IntegrityError):
        run_sql(fresh_database_url, RATE_LIMIT_INSERT, counter_row(count=7))


# --- 0009: account_tokens (#29 B) --------------------------------------------------


def test_downgrade_to_0008_drops_exactly_account_tokens(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0008")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0009")
    assert public_tables(fresh_database_url) - before == set(ACCOUNT_TOKEN_TABLES)

    command.downgrade(config, "0008")

    assert version_nums(fresh_database_url) == ["0008"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_account_tokens_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    columns = run_sql(
        fresh_database_url,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
        " WHERE table_name = 'account_tokens'",
    )
    assert {(r.column_name, r.data_type, r.is_nullable) for r in columns} == {
        ("id", "uuid", "NO"),
        ("created_at", "timestamp with time zone", "NO"),
        ("updated_at", "timestamp with time zone", "NO"),
        ("user_id", "uuid", "NO"),
        ("kind", "text", "NO"),
        ("token_hash", "bytea", "NO"),
        ("expires_at", "timestamp with time zone", "NO"),
        ("used_at", "timestamp with time zone", "YES"),
        ("revoked_at", "timestamp with time zone", "YES"),
        ("issued_by_user_id", "uuid", "YES"),
    }
    constraints = run_sql(
        fresh_database_url,
        "SELECT conname, contype::text, confdeltype::text"
        " FROM pg_constraint WHERE conrelid = 'account_tokens'::regclass"
        " AND contype <> 'n' ORDER BY conname",
    )
    assert [(r.conname, r.contype) for r in constraints] == [
        ("ck_account_tokens_kind_valid", "c"),
        ("ck_account_tokens_token_hash_length", "c"),
        ("ck_account_tokens_used_or_revoked", "c"),
        ("fk_account_tokens_issued_by_user_id_users", "f"),
        ("fk_account_tokens_user_id_users", "f"),
        ("pk_account_tokens", "p"),
        ("uq_account_tokens_token_hash", "u"),
    ]
    # ON DELETE RESTRICT: archived users keep their history.
    assert {r.confdeltype for r in constraints if r.contype == "f"} == {"r"}
    indexes = run_sql(
        fresh_database_url,
        "SELECT indexname, indexdef FROM pg_indexes"
        " WHERE tablename = 'account_tokens' ORDER BY indexname",
    )
    by_name = {r.indexname: r.indexdef for r in indexes}
    assert set(by_name) == {
        "ix_account_tokens_user_id",
        "pk_account_tokens",
        "uq_account_tokens_live_user_kind",
        "uq_account_tokens_token_hash",
    }
    live = by_name["uq_account_tokens_live_user_kind"]
    assert "UNIQUE INDEX" in live
    assert "(user_id, kind)" in live
    assert "WHERE ((used_at IS NULL) AND (revoked_at IS NULL))" in live


# --- 0010: password_reset account tokens (#30 A) -----------------------------------

KIND_CHECK = (
    "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint"
    " WHERE conname = 'ck_account_tokens_kind_valid'"
)
TOKEN_INSERT = (
    "INSERT INTO account_tokens (user_id, kind, token_hash, expires_at)"
    " VALUES (:user_id, :kind, :hash, now() + interval '1 day')"
)
USER_INSERT = (
    "INSERT INTO users (id, username, display_name, state)"
    " VALUES (:id, :username, 'Migration User', 'active')"
)


def test_0010_widens_the_kind_check_and_downgrade_restores_it(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0009")
    [before] = run_sql(fresh_database_url, KIND_CHECK)
    assert "'invitation'" in before.definition
    assert "password_reset" not in before.definition
    command.upgrade(config, "head")
    [after] = run_sql(fresh_database_url, KIND_CHECK)
    assert "'invitation'" in after.definition
    assert "'password_reset'" in after.definition
    user_id = uuid.uuid4()
    run_sql(fresh_database_url, USER_INSERT, {"id": user_id, "username": "m.user"})
    for kind, fill in (("invitation", b"\x01"), ("password_reset", b"\x02")):
        run_sql(
            fresh_database_url,
            TOKEN_INSERT,
            {"user_id": user_id, "kind": kind, "hash": fill * 32},
        )
    with pytest.raises(IntegrityError, match="kind_valid"):
        run_sql(
            fresh_database_url,
            TOKEN_INSERT,
            {"user_id": user_id, "kind": "other", "hash": b"\x03" * 32},
        )

    command.downgrade(config, "0009")

    assert version_nums(fresh_database_url) == ["0009"]
    [restored] = run_sql(fresh_database_url, KIND_CHECK)
    assert restored.definition == before.definition
    kinds = run_sql(fresh_database_url, "SELECT kind FROM account_tokens")
    assert [r.kind for r in kinds] == ["invitation"]
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


# --- 0011: registration_requests (#169 A) ------------------------------------------


def test_downgrade_to_0010_drops_exactly_registration_requests(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0010")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0011")
    assert public_tables(fresh_database_url) - before == set(REGISTRATION_TABLES)

    command.downgrade(config, "0010")

    assert version_nums(fresh_database_url) == ["0010"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_registration_requests_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    columns = run_sql(
        fresh_database_url,
        "SELECT column_name, data_type, is_nullable, column_default"
        " FROM information_schema.columns"
        " WHERE table_name = 'registration_requests'",
    )
    assert {(r.column_name, r.data_type, r.is_nullable) for r in columns} == {
        ("id", "uuid", "NO"),
        ("created_at", "timestamp with time zone", "NO"),
        ("updated_at", "timestamp with time zone", "NO"),
        ("user_id", "uuid", "NO"),
        ("requested_role", "text", "NO"),
        ("status", "text", "NO"),
        ("decided_at", "timestamp with time zone", "YES"),
        ("decided_by_user_id", "uuid", "YES"),
    }
    [status] = [r for r in columns if r.column_name == "status"]
    assert status.column_default == "'pending'::text"
    constraints = run_sql(
        fresh_database_url,
        "SELECT conname, contype::text, confdeltype::text"
        " FROM pg_constraint WHERE conrelid = 'registration_requests'::regclass"
        " AND contype <> 'n' ORDER BY conname",
    )
    assert [(r.conname, r.contype) for r in constraints] == [
        ("ck_registration_requests_decided_iff_not_pending", "c"),
        ("ck_registration_requests_requested_role_valid", "c"),
        ("ck_registration_requests_status_valid", "c"),
        ("fk_registration_requests_decided_by_user_id_users", "f"),
        ("fk_registration_requests_user_id_users", "f"),
        ("pk_registration_requests", "p"),
        ("uq_registration_requests_user_id", "u"),
    ]
    # ON DELETE RESTRICT: archived users keep their history.
    assert {r.confdeltype for r in constraints if r.contype == "f"} == {"r"}
    indexes = run_sql(
        fresh_database_url,
        "SELECT indexname, indexdef FROM pg_indexes"
        " WHERE tablename = 'registration_requests' ORDER BY indexname",
    )
    by_name = {r.indexname: r.indexdef for r in indexes}
    assert set(by_name) == {
        "ix_registration_requests_status_created_at_id",
        "pk_registration_requests",
        "uq_registration_requests_user_id",
    }
    assert (
        "(status, created_at, id)"
        in by_name["ix_registration_requests_status_created_at_id"]
    )


# --- 0012: users contact fields (#31 A) ---------------------------------------------

USERS_COLUMNS = (
    "SELECT column_name, data_type, is_nullable, character_maximum_length"
    " FROM information_schema.columns WHERE table_name = 'users'"
)
USERS_CHECKS = (
    "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint"
    " WHERE conrelid = 'users'::regclass AND contype = 'c' ORDER BY conname"
)


def test_0012_adds_the_contact_columns_and_downgrade_drops_exactly_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0011")
    columns_before = {r.column_name for r in run_sql(fresh_database_url, USERS_COLUMNS)}
    checks_before = run_sql(fresh_database_url, USERS_CHECKS)
    command.upgrade(config, "head")

    columns = {
        r.column_name: (r.data_type, r.is_nullable, r.character_maximum_length)
        for r in run_sql(fresh_database_url, USERS_COLUMNS)
    }
    assert set(columns) - columns_before == {"email", "phone"}
    assert columns["email"] == ("character varying", "YES", 254)
    assert columns["phone"] == ("character varying", "YES", 32)
    checks = {r.conname for r in run_sql(fresh_database_url, USERS_CHECKS)}
    assert checks - {r.conname for r in checks_before} == {
        "ck_users_email_format",
        "ck_users_phone_format",
    }
    # Not unique: family members may share contact fields.
    unique = run_sql(
        fresh_database_url,
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'users'"
        " AND (indexdef LIKE '%email%' OR indexdef LIKE '%phone%')",
    )
    assert unique == []

    command.downgrade(config, "0011")

    assert version_nums(fresh_database_url) == ["0011"]
    assert {
        r.column_name for r in run_sql(fresh_database_url, USERS_COLUMNS)
    } == columns_before
    assert run_sql(fresh_database_url, USERS_CHECKS) == checks_before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


# --- 0013: catalog education_levels and subjects (#32 A) ----------------------------


def test_downgrade_to_0012_drops_exactly_the_catalog_tables(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0012")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0013")
    assert public_tables(fresh_database_url) - before == set(CATALOG_TABLES)

    command.downgrade(config, "0012")

    assert version_nums(fresh_database_url) == ["0012"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def _columns(dsn: str, table: str) -> dict[str, tuple[Any, ...]]:
    rows = run_sql(
        dsn,
        "SELECT column_name, data_type, is_nullable, character_maximum_length,"
        " column_default FROM information_schema.columns WHERE table_name = :t",
        {"t": table},
    )
    return {
        r.column_name: (
            r.data_type,
            r.is_nullable,
            r.character_maximum_length,
            r.column_default,
        )
        for r in rows
    }


def _constraints(dsn: str, table: str) -> dict[str, str]:
    rows = run_sql(
        dsn,
        "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint"
        " WHERE conrelid = CAST(:t AS regclass) AND contype <> 'n'",
        {"t": table},
    )
    return {r.conname: r.definition for r in rows}


def _indexes(dsn: str, table: str) -> dict[str, str]:
    rows = run_sql(
        dsn,
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t",
        {"t": table},
    )
    return {r.indexname: r.indexdef for r in rows}


STANDARD_COLUMNS = {
    "id": ("uuid", "NO", None, "uuidv7()"),
    "created_at": ("timestamp with time zone", "NO", None, "now()"),
    "updated_at": ("timestamp with time zone", "NO", None, "now()"),
    "version": ("integer", "NO", None, "1"),
}


def test_education_levels_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "education_levels") == {
        **STANDARD_COLUMNS,
        "name": ("character varying", "NO", 100, None),
        "sort_order": ("smallint", "NO", None, None),
        "is_active": ("boolean", "NO", None, "true"),
        "seed_key": ("character varying", "YES", 40, None),
    }
    constraints = _constraints(fresh_database_url, "education_levels")
    assert set(constraints) == {
        "pk_education_levels",
        "uq_education_levels_seed_key",
        "ck_education_levels_name_trimmed",
        "ck_education_levels_name_not_blank",
        "ck_education_levels_sort_order_range",
        "ck_education_levels_seed_key_format",
    }
    assert "btrim" in constraints["ck_education_levels_name_trimmed"]
    assert "9999" in constraints["ck_education_levels_sort_order_range"]
    indexes = _indexes(fresh_database_url, "education_levels")
    assert set(indexes) == {
        "pk_education_levels",
        "uq_education_levels_seed_key",
        "uq_education_levels_name_ci",
    }
    assert "UNIQUE INDEX" in indexes["uq_education_levels_name_ci"]
    assert "lower((name)::text)" in indexes["uq_education_levels_name_ci"]


def test_subjects_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "subjects") == {
        **STANDARD_COLUMNS,
        "education_level_id": ("uuid", "NO", None, None),
        "name": ("character varying", "NO", 100, None),
        "is_active": ("boolean", "NO", None, "true"),
    }
    constraints = _constraints(fresh_database_url, "subjects")
    assert set(constraints) == {
        "pk_subjects",
        "fk_subjects_education_level_id_education_levels",
        "ck_subjects_name_trimmed",
        "ck_subjects_name_not_blank",
    }
    assert (
        "ON DELETE RESTRICT"
        in (constraints["fk_subjects_education_level_id_education_levels"])
    )
    indexes = _indexes(fresh_database_url, "subjects")
    assert set(indexes) == {
        "pk_subjects",
        "ix_subjects_education_level_id",
        "uq_subjects_level_name_ci",
    }
    assert "UNIQUE INDEX" in indexes["uq_subjects_level_name_ci"]
    assert (
        "(education_level_id, lower((name)::text))"
        in indexes["uq_subjects_level_name_ci"]
    )


# --- 0014: families students and emergency contacts (#33 A) -------------------------

EXTENSIONS = "SELECT extname FROM pg_extension ORDER BY extname"
F_UNACCENT = (
    "SELECT p.provolatile::text AS provolatile,"
    " p.proparallel::text AS proparallel, p.proisstrict,"
    " has_function_privilege('public', p.oid, 'EXECUTE') AS public_execute"
    " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
    " WHERE n.nspname = 'public' AND p.proname = 'f_unaccent'"
)


def test_0014_adds_the_extensions_function_and_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0013")
    before = public_tables(fresh_database_url)
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == ["plpgsql"]
    command.upgrade(config, "0014")

    assert public_tables(fresh_database_url) - before == set(FAMILY_TABLES)
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == [
        "pg_trgm",
        "plpgsql",
        "unaccent",
    ]
    [function] = run_sql(fresh_database_url, F_UNACCENT)
    # IMMUTABLE ('i'), PARALLEL SAFE ('s'), STRICT; EXECUTE stays granted to
    # PUBLIC, so a least-privilege runtime role (#165) can call it.
    assert (function.provolatile, function.proparallel, function.proisstrict) == (
        "i",
        "s",
        True,
    )
    assert function.public_execute is True

    command.downgrade(config, "0013")

    assert version_nums(fresh_database_url) == ["0013"]
    assert public_tables(fresh_database_url) == before
    assert run_sql(fresh_database_url, F_UNACCENT) == []
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == ["plpgsql"]
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_students_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "students") == {
        **STANDARD_COLUMNS,
        "given_names": ("character varying", "NO", 100, None),
        "family_names": ("character varying", "NO", 100, None),
        "birthdate": ("date", "NO", None, None),
        "email": ("character varying", "YES", 254, None),
        "phone": ("character varying", "YES", 32, None),
        "school_name": ("character varying", "YES", 200, None),
        "school_grade": ("character varying", "YES", 50, None),
        "education_level_id": ("uuid", "YES", None, None),
        "is_active": ("boolean", "NO", None, "true"),
        "user_id": ("uuid", "YES", None, None),  # #35
    }
    constraints = _constraints(fresh_database_url, "students")
    assert set(constraints) == {
        "pk_students",
        "fk_students_education_level_id_education_levels",
        "fk_students_user_id_users",
        "uq_students_user_id",
        "ck_students_given_names_trimmed",
        "ck_students_given_names_not_blank",
        "ck_students_family_names_trimmed",
        "ck_students_family_names_not_blank",
        "ck_students_birthdate_min",
        "ck_students_email_format",
        "ck_students_phone_format",
        "ck_students_school_name_trimmed",
        "ck_students_school_grade_trimmed",
    }
    assert (
        "ON DELETE RESTRICT"
        in constraints["fk_students_education_level_id_education_levels"]
    )
    assert "1900-01-01" in constraints["ck_students_birthdate_min"]
    assert "ON DELETE RESTRICT" in constraints["fk_students_user_id_users"]
    assert constraints["uq_students_user_id"] == "UNIQUE (user_id)"
    indexes = _indexes(fresh_database_url, "students")
    assert set(indexes) == {
        "pk_students",
        "ix_students_education_level_id",
        "ix_students_search_name_trgm",
        "uq_students_user_id",
    }
    search = indexes["ix_students_search_name_trgm"]
    assert "USING gin" in search
    assert "f_unaccent(lower" in search
    assert "gin_trgm_ops" in search


def test_student_emergency_contacts_columns_constraints_and_indexes(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    columns = _columns(fresh_database_url, "student_emergency_contacts")
    assert columns == {
        "id": ("uuid", "NO", None, "uuidv7()"),
        "created_at": ("timestamp with time zone", "NO", None, "now()"),
        "updated_at": ("timestamp with time zone", "NO", None, "now()"),
        "student_id": ("uuid", "NO", None, None),
        "name": ("character varying", "NO", 200, None),
        "relationship": ("character varying", "NO", 50, None),
        "phone": ("character varying", "NO", 32, None),
        "position": ("smallint", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, "student_emergency_contacts")
    assert set(constraints) == {
        "pk_student_emergency_contacts",
        "fk_student_emergency_contacts_student_id_students",
        "uq_student_emergency_contacts_student_id_position",
        "ck_student_emergency_contacts_name_trimmed",
        "ck_student_emergency_contacts_name_not_blank",
        "ck_student_emergency_contacts_relationship_trimmed",
        "ck_student_emergency_contacts_relationship_not_blank",
        "ck_student_emergency_contacts_phone_format",
        "ck_student_emergency_contacts_position_range",
    }
    assert (
        "ON DELETE RESTRICT"
        in constraints["fk_student_emergency_contacts_student_id_students"]
    )
    assert constraints["uq_student_emergency_contacts_student_id_position"] == (
        'UNIQUE (student_id, "position") DEFERRABLE INITIALLY DEFERRED'
    )
    assert set(_indexes(fresh_database_url, "student_emergency_contacts")) == {
        "pk_student_emergency_contacts",
        "uq_student_emergency_contacts_student_id_position",
        "ix_student_emergency_contacts_student_id",
    }


# --- 0015: guardian profiles and student links (#34 A) ------------------------------


def test_0015_adds_exactly_the_guardian_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0014")
    before = public_tables(fresh_database_url)
    extensions = [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)]
    command.upgrade(config, "0015")

    assert public_tables(fresh_database_url) - before == set(GUARDIAN_TABLES)
    # The extensions and f_unaccent are 0014's; this revision doesn't touch them.
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == extensions

    command.downgrade(config, "0014")

    assert version_nums(fresh_database_url) == ["0014"]
    assert public_tables(fresh_database_url) == before
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == extensions
    assert len(run_sql(fresh_database_url, F_UNACCENT)) == 1
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_guardians_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "guardians") == {
        **STANDARD_COLUMNS,
        "given_names": ("character varying", "NO", 100, None),
        "family_names": ("character varying", "NO", 100, None),
        "email": ("character varying", "YES", 254, None),
        "phone": ("character varying", "YES", 32, None),
        "user_id": ("uuid", "YES", None, None),
        "is_active": ("boolean", "NO", None, "true"),
    }
    constraints = _constraints(fresh_database_url, "guardians")
    assert set(constraints) == {
        "pk_guardians",
        "fk_guardians_user_id_users",
        "uq_guardians_user_id",
        "ck_guardians_given_names_trimmed",
        "ck_guardians_given_names_not_blank",
        "ck_guardians_family_names_trimmed",
        "ck_guardians_family_names_not_blank",
        "ck_guardians_email_format",
        "ck_guardians_phone_format",
    }
    assert "ON DELETE RESTRICT" in constraints["fk_guardians_user_id_users"]
    assert constraints["uq_guardians_user_id"] == "UNIQUE (user_id)"
    # The #31 patterns, the same as the students' CHECKs.
    students = _constraints(fresh_database_url, "students")
    for check in ("email_format", "phone_format", "given_names_not_blank"):
        assert constraints[f"ck_guardians_{check}"] == students[f"ck_students_{check}"]
    indexes = _indexes(fresh_database_url, "guardians")
    assert set(indexes) == {
        "pk_guardians",
        "uq_guardians_user_id",
        "ix_guardians_search_name_trgm",
    }
    search = indexes["ix_guardians_search_name_trgm"]
    assert "USING gin" in search
    assert "gin_trgm_ops" in search
    # The same expression as the students' index.
    expression = search.split("USING gin")[1]
    assert expression == indexes_students(fresh_database_url).split("USING gin")[1]


def indexes_students(dsn: str) -> str:
    return _indexes(dsn, "students")["ix_students_search_name_trgm"]


def test_student_guardians_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "student_guardians") == {
        "id": ("uuid", "NO", None, "uuidv7()"),
        "created_at": ("timestamp with time zone", "NO", None, "now()"),
        "updated_at": ("timestamp with time zone", "NO", None, "now()"),
        "student_id": ("uuid", "NO", None, None),
        "guardian_id": ("uuid", "NO", None, None),
        "relationship": ("text", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, "student_guardians")
    assert set(constraints) == {
        "pk_student_guardians",
        "fk_student_guardians_student_id_students",
        "fk_student_guardians_guardian_id_guardians",
        "uq_student_guardians_student_guardian",
        "ck_student_guardians_relationship_valid",
    }
    for fk in (
        "fk_student_guardians_student_id_students",
        "fk_student_guardians_guardian_id_guardians",
    ):
        assert "ON DELETE RESTRICT" in constraints[fk]
    assert constraints["uq_student_guardians_student_guardian"] == (
        "UNIQUE (student_id, guardian_id)"
    )
    for code in (
        "mother",
        "father",
        "grandparent",
        "stepparent",
        "sibling",
        "other_relative",
        "legal_guardian",
        "other",
    ):
        assert f"'{code}'" in constraints["ck_student_guardians_relationship_valid"]
    assert set(_indexes(fresh_database_url, "student_guardians")) == {
        "pk_student_guardians",
        "uq_student_guardians_student_guardian",
        "ix_student_guardians_guardian_id",
    }


# --- 0016: family groupings (#172 A) ------------------------------------------------


def test_0016_adds_exactly_the_family_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0015")
    before = public_tables(fresh_database_url)
    extensions = [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)]
    command.upgrade(config, "0016")

    assert public_tables(fresh_database_url) - before == set(GROUPING_TABLES)
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == extensions

    command.downgrade(config, "0015")

    assert version_nums(fresh_database_url) == ["0015"]
    assert public_tables(fresh_database_url) == before
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == extensions
    assert len(run_sql(fresh_database_url, F_UNACCENT)) == 1
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_families_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "families") == {
        **STANDARD_COLUMNS,
        "name": ("character varying", "NO", 200, None),
        "is_active": ("boolean", "NO", None, "true"),
    }
    constraints = _constraints(fresh_database_url, "families")
    assert set(constraints) == {
        "pk_families",
        "ck_families_name_trimmed",
        "ck_families_name_not_blank",
    }
    # The #33 A2 trim and not-blank CHECKs.
    students = _constraints(fresh_database_url, "students")
    assert constraints["ck_families_name_not_blank"] == students[
        "ck_students_given_names_not_blank"
    ].replace("given_names", "name")
    indexes = _indexes(fresh_database_url, "families")
    assert set(indexes) == {"pk_families", "ix_families_search_name_trgm"}
    search = indexes["ix_families_search_name_trgm"]
    assert "USING gin" in search
    assert "gin_trgm_ops" in search
    assert "f_unaccent(lower((name)::text))" in search  # as pg_indexes prints it


@pytest.mark.parametrize(
    ("table", "member", "target"),
    [
        ("family_students", "student_id", "students"),
        ("family_guardians", "guardian_id", "guardians"),
    ],
)
def test_family_membership_columns_constraints_and_indexes(
    fresh_database_url, table, member, target
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, table) == {
        "id": ("uuid", "NO", None, "uuidv7()"),
        "created_at": ("timestamp with time zone", "NO", None, "now()"),
        "updated_at": ("timestamp with time zone", "NO", None, "now()"),
        "family_id": ("uuid", "NO", None, None),
        member: ("uuid", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_family_id_families",
        f"fk_{table}_{member}_{target}",
        f"uq_{table}_{member}",
    }
    for fk in (f"fk_{table}_family_id_families", f"fk_{table}_{member}_{target}"):
        assert "ON DELETE RESTRICT" in constraints[fk]
    # One family per student and per guardian (#172 E2 (a)).
    assert constraints[f"uq_{table}_{member}"] == f"UNIQUE ({member})"
    assert set(_indexes(fresh_database_url, table)) == {
        f"pk_{table}",
        f"uq_{table}_{member}",
        f"ix_{table}_family_id",
    }


# --- 0017: privacy-notice versions and acceptances (#174 A) --------------------------

IMMUTABLE_TRIGGER = (
    "SELECT tgname FROM pg_trigger"
    " WHERE tgrelid = 'privacy_notice_versions'::regclass AND NOT tgisinternal"
)
IMMUTABLE_FUNCTION = (
    "SELECT proname FROM pg_proc WHERE proname = 'privacy_notice_versions_immutable'"
)


def test_0017_adds_exactly_the_privacy_objects_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0016")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0017")

    assert public_tables(fresh_database_url) - before == set(PRIVACY_TABLES)
    assert [r.tgname for r in run_sql(fresh_database_url, IMMUTABLE_TRIGGER)] == [
        "privacy_notice_versions_immutable"
    ]
    assert len(run_sql(fresh_database_url, IMMUTABLE_FUNCTION)) == 1

    command.downgrade(config, "0016")

    assert version_nums(fresh_database_url) == ["0016"]
    assert public_tables(fresh_database_url) == before
    assert run_sql(fresh_database_url, IMMUTABLE_FUNCTION) == []
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_privacy_notice_versions_columns_and_constraints(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "privacy_notice_versions") == {
        "id": ("uuid", "NO", None, "uuidv7()"),
        "created_at": ("timestamp with time zone", "NO", None, "now()"),
        "updated_at": ("timestamp with time zone", "NO", None, "now()"),
        # A publication number, not an optimistic-lock version (no default).
        "version": ("integer", "NO", None, None),
        "published_at": ("timestamp with time zone", "NO", None, None),
        "body_es": ("text", "NO", None, None),
        "body_en": ("text", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, "privacy_notice_versions")
    assert set(constraints) == {
        "pk_privacy_notice_versions",
        "uq_privacy_notice_versions_version",
        "ck_privacy_notice_versions_version_positive",
        "ck_privacy_notice_versions_body_es_not_blank",
        "ck_privacy_notice_versions_body_en_not_blank",
    }
    assert constraints["uq_privacy_notice_versions_version"] == "UNIQUE (version)"


def test_privacy_notice_acceptances_columns_constraints_and_indexes(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "privacy_notice_acceptances") == {
        "id": ("uuid", "NO", None, "uuidv7()"),
        "created_at": ("timestamp with time zone", "NO", None, "now()"),
        "updated_at": ("timestamp with time zone", "NO", None, "now()"),
        "user_id": ("uuid", "NO", None, None),
        "notice_version_id": ("uuid", "NO", None, None),
        "accepted_at": ("timestamp with time zone", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, "privacy_notice_acceptances")
    assert set(constraints) == {
        "pk_privacy_notice_acceptances",
        "fk_privacy_notice_acceptances_user_id_users",
        "fk_privacy_notice_acceptances_notice_version_id",
        "uq_privacy_notice_acceptances_user_id",
    }
    for fk in (
        "fk_privacy_notice_acceptances_user_id_users",
        "fk_privacy_notice_acceptances_notice_version_id",
    ):
        assert "ON DELETE RESTRICT" in constraints[fk]
    # The unique index starts with user_id, so it serves lookups by user.
    assert constraints["uq_privacy_notice_acceptances_user_id"] == (
        "UNIQUE (user_id, notice_version_id)"
    )
    assert set(_indexes(fresh_database_url, "privacy_notice_acceptances")) == {
        "pk_privacy_notice_acceptances",
        "uq_privacy_notice_acceptances_user_id",
    }


def test_version_1_is_published_with_both_draft_texts(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    rows = run_sql(
        fresh_database_url,
        "SELECT version, published_at, body_es, body_en FROM privacy_notice_versions",
    )

    assert len(rows) == 1
    [row] = rows
    assert row.version == 1
    assert row.published_at == datetime(2026, 10, 18, tzinfo=UTC)
    assert row.body_es.startswith("Borrador pendiente de revisión legal.\n\n")
    assert row.body_en.startswith("Draft pending legal review.\n\n")
    for body in (row.body_es, row.body_en):
        assert body == body.strip()
        assert "\n\n\n" not in body


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE privacy_notice_versions SET body_en = 'x'",
        "UPDATE privacy_notice_versions SET published_at = now()",
        "DELETE FROM privacy_notice_versions",
    ],
)
def test_published_versions_never_change(fresh_database_url, statement):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(DBAPIError, match="never change once published"):
        run_sql(fresh_database_url, statement)
    assert (
        len(run_sql(fresh_database_url, "SELECT 1 FROM privacy_notice_versions")) == 1
    )


INSERT_VERSION = (
    "INSERT INTO privacy_notice_versions (version, published_at, body_es, body_en)"
    " VALUES (:version, now(), :es, :en)"
)


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"version": 1, "es": "a", "en": "b"}, "uq_privacy_notice_versions_version"),
        ({"version": 0, "es": "a", "en": "b"}, "version_positive"),
        ({"version": -1, "es": "a", "en": "b"}, "version_positive"),
        ({"version": 2, "es": " \n\t", "en": "b"}, "body_es_not_blank"),
        ({"version": 2, "es": "a", "en": ""}, "body_en_not_blank"),
    ],
)
def test_invalid_versions_are_rejected(fresh_database_url, values, constraint):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match=constraint):
        run_sql(fresh_database_url, INSERT_VERSION, values)


def test_a_duplicate_acceptance_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    user_id = uuid.uuid7()
    run_sql(
        fresh_database_url,
        "INSERT INTO users (id, username, display_name, state)"
        " VALUES (:id, 'notice.reader', 'Notice Reader', 'active')",
        {"id": user_id},
    )
    accept = (
        "INSERT INTO privacy_notice_acceptances"
        " (user_id, notice_version_id, accepted_at)"
        " SELECT :user_id, id, now() FROM privacy_notice_versions WHERE version = 1"
    )
    run_sql(fresh_database_url, accept, {"user_id": user_id})

    with pytest.raises(IntegrityError, match="uq_privacy_notice_acceptances_user_id"):
        run_sql(fresh_database_url, accept, {"user_id": user_id})


# --- 0018: student logins and consent records (#35 A) --------------------------------


def test_0018_adds_exactly_the_consent_objects_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0017")
    before = public_tables(fresh_database_url)
    students_before = _columns(fresh_database_url, "students")
    command.upgrade(config, "0018")

    assert public_tables(fresh_database_url) - before == set(CONSENT_TABLES)
    assert set(_columns(fresh_database_url, "students")) - set(students_before) == {
        "user_id"
    }

    command.downgrade(config, "0017")

    assert version_nums(fresh_database_url) == ["0017"]
    assert public_tables(fresh_database_url) == before
    assert _columns(fresh_database_url, "students") == students_before
    assert "uq_students_user_id" not in _constraints(fresh_database_url, "students")
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_consent_records_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "consent_records") == {
        "id": ("uuid", "NO", None, "uuidv7()"),
        "created_at": ("timestamp with time zone", "NO", None, "now()"),
        "updated_at": ("timestamp with time zone", "NO", None, "now()"),
        "student_id": ("uuid", "NO", None, None),
        "guardian_id": ("uuid", "NO", None, None),
        "scope": ("text", "NO", None, None),
        "method": ("text", "NO", None, None),
        "notice_version_id": ("uuid", "NO", None, None),
        "granted_at": ("timestamp with time zone", "NO", None, None),
        "recorded_by_user_id": ("uuid", "NO", None, None),
        "revoked_at": ("timestamp with time zone", "YES", None, None),
        "revoked_by_user_id": ("uuid", "YES", None, None),
        "revocation_cause": ("text", "YES", None, None),
    }
    constraints = _constraints(fresh_database_url, "consent_records")
    fks = {
        "fk_consent_records_student_id_students",
        "fk_consent_records_guardian_id_guardians",
        "fk_consent_records_notice_version_id",
        "fk_consent_records_recorded_by_user_id_users",
        "fk_consent_records_revoked_by_user_id_users",
    }
    assert set(constraints) == {
        "pk_consent_records",
        *fks,
        "ck_consent_records_scope_valid",
        "ck_consent_records_method_valid",
        "ck_consent_records_revocation_cause_valid",
        "ck_consent_records_revocation_complete",
    }
    for fk in fks:
        assert "ON DELETE RESTRICT" in constraints[fk]
    assert (
        "privacy_notice_versions(id)"
        in constraints["fk_consent_records_notice_version_id"]
    )
    indexes = _indexes(fresh_database_url, "consent_records")
    assert set(indexes) == {
        "pk_consent_records",
        "ix_consent_records_student_id",
        "uq_consent_records_active",
    }
    active = indexes["uq_consent_records_active"]
    assert "UNIQUE INDEX" in active
    assert "(student_id, guardian_id, scope)" in active
    assert "WHERE (revoked_at IS NULL)" in active


def _consent_world(dsn: str) -> dict[str, uuid.UUID]:
    """A user, a student, a guardian (raw SQL) and the id of notice v1."""
    ids = {name: uuid.uuid7() for name in ("user", "other", "student", "guardian")}
    for name in ("user", "other"):
        run_sql(
            dsn,
            "INSERT INTO users (id, username, display_name, state)"
            " VALUES (:id, :username, 'Consent Tester', 'active')",
            {"id": ids[name], "username": f"consent.{name}"},
        )
    run_sql(
        dsn,
        "INSERT INTO students (id, given_names, family_names, birthdate)"
        " VALUES (:id, 'Ana', 'Prueba', DATE '2014-03-01')",
        {"id": ids["student"]},
    )
    run_sql(
        dsn,
        "INSERT INTO guardians (id, given_names, family_names)"
        " VALUES (:id, 'Laura', 'Prueba')",
        {"id": ids["guardian"]},
    )
    [row] = run_sql(dsn, "SELECT id FROM privacy_notice_versions WHERE version = 1")
    ids["notice"] = row.id
    return ids


INSERT_CONSENT = (
    "INSERT INTO consent_records (student_id, guardian_id, scope, method,"
    " notice_version_id, granted_at, recorded_by_user_id, revoked_at,"
    " revoked_by_user_id, revocation_cause)"
    " VALUES (:student, :guardian, :scope, :method, :notice, now(), :user,"
    " :revoked_at, :revoked_by, :cause)"
)


def _consent_values(
    ids: dict[str, uuid.UUID], **overrides: object
) -> dict[str, object]:
    values: dict[str, object] = {
        "student": ids["student"],
        "guardian": ids["guardian"],
        "scope": "student_login",
        "method": "paper",
        "notice": ids["notice"],
        "user": ids["user"],
        "revoked_at": None,
        "revoked_by": None,
        "cause": None,
    }
    values.update(overrides)
    return values


def test_a_second_student_with_the_same_login_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    ids = _consent_world(fresh_database_url)
    link = "UPDATE students SET user_id = :user WHERE id = :student"
    run_sql(fresh_database_url, link, {"user": ids["user"], "student": ids["student"]})
    other = uuid.uuid7()
    with pytest.raises(IntegrityError, match="uq_students_user_id"):
        run_sql(
            fresh_database_url,
            "INSERT INTO students (id, given_names, family_names, birthdate, user_id)"
            " VALUES (:id, 'Otro', 'Prueba', DATE '2010-01-01', :user)",
            {"id": other, "user": ids["user"]},
        )


def test_deleting_a_user_linked_to_a_student_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    ids = _consent_world(fresh_database_url)
    run_sql(
        fresh_database_url,
        "UPDATE students SET user_id = :user WHERE id = :student",
        {"user": ids["user"], "student": ids["student"]},
    )

    with pytest.raises(IntegrityError, match="fk_students_user_id_users"):
        run_sql(
            fresh_database_url, "DELETE FROM users WHERE id = :id", {"id": ids["user"]}
        )


def test_one_active_record_per_student_guardian_and_scope(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    ids = _consent_world(fresh_database_url)
    revoked = _consent_values(
        ids,
        revoked_at=datetime(2026, 1, 1, tzinfo=UTC),
        revoked_by=ids["user"],
        cause="revoked",
    )
    run_sql(fresh_database_url, INSERT_CONSENT, revoked)
    # A revoked one plus a new active one is accepted, and another scope too.
    run_sql(fresh_database_url, INSERT_CONSENT, _consent_values(ids))
    run_sql(
        fresh_database_url,
        INSERT_CONSENT,
        _consent_values(ids, scope="guardian_academic_access", method="in_app"),
    )

    with pytest.raises(IntegrityError, match="uq_consent_records_active"):
        run_sql(
            fresh_database_url, INSERT_CONSENT, _consent_values(ids, method="in_app")
        )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"scope": "everything"}, "ck_consent_records_scope_valid"),
        ({"method": "email"}, "ck_consent_records_method_valid"),
        (
            {
                "revoked_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_by": "user",
                "cause": "expired",
            },
            "ck_consent_records_revocation_cause_valid",
        ),
        ({"revoked_at": datetime(2026, 1, 1, tzinfo=UTC)}, "revocation_complete"),
        ({"revoked_by": "user"}, "revocation_complete"),
        ({"cause": "revoked"}, "revocation_complete"),
        (
            {"revoked_at": datetime(2026, 1, 1, tzinfo=UTC), "cause": "revoked"},
            "revocation_complete",
        ),
    ],
)
def test_invalid_consent_records_are_rejected(
    fresh_database_url, overrides, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    ids = _consent_world(fresh_database_url)
    if overrides.get("revoked_by") == "user":
        overrides = {**overrides, "revoked_by": ids["user"]}

    with pytest.raises(IntegrityError, match=constraint):
        run_sql(fresh_database_url, INSERT_CONSENT, _consent_values(ids, **overrides))


# --- 0019: user preferences (#36 A) --------------------------------------------------


def test_0019_adds_exactly_user_preferences_and_downgrade_drops_it(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0018")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0019")

    assert public_tables(fresh_database_url) - before == set(PREFERENCE_TABLES)

    command.downgrade(config, "0018")

    assert version_nums(fresh_database_url) == ["0018"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_user_preferences_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "user_preferences") == {
        "id": ("uuid", "NO", None, "uuidv7()"),
        "created_at": ("timestamp with time zone", "NO", None, "now()"),
        "updated_at": ("timestamp with time zone", "NO", None, "now()"),
        "user_id": ("uuid", "NO", None, None),
        "language": ("text", "YES", None, None),
        "theme": ("text", "YES", None, None),
    }
    constraints = _constraints(fresh_database_url, "user_preferences")
    assert set(constraints) == {
        "pk_user_preferences",
        "uq_user_preferences_user_id",
        "fk_user_preferences_user_id_users",
        "ck_user_preferences_language_valid",
        "ck_user_preferences_theme_valid",
    }
    assert "ON DELETE RESTRICT" in constraints["fk_user_preferences_user_id_users"]
    assert "'es'" in constraints["ck_user_preferences_language_valid"]
    assert "'en'" in constraints["ck_user_preferences_language_valid"]
    for theme in ("'light'", "'dark'", "'system'"):
        assert theme in constraints["ck_user_preferences_theme_valid"]
    assert set(_indexes(fresh_database_url, "user_preferences")) == {
        "pk_user_preferences",
        "uq_user_preferences_user_id",
    }


INSERT_PREFERENCES = (
    "INSERT INTO user_preferences (user_id, language, theme)"
    " VALUES (:user, :language, :theme)"
)


def _preferences_user(dsn: str) -> uuid.UUID:
    user_id = uuid.uuid7()
    run_sql(
        dsn,
        "INSERT INTO users (id, username, display_name, state)"
        " VALUES (:id, :username, 'Preferences Tester', 'active')",
        {"id": user_id, "username": f"prefs.{user_id.hex[-8:]}"},
    )
    return user_id


def test_user_preferences_accept_both_values_null(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    user = _preferences_user(fresh_database_url)

    run_sql(
        fresh_database_url,
        INSERT_PREFERENCES,
        {"user": user, "language": None, "theme": None},
    )

    [row] = run_sql(fresh_database_url, "SELECT language, theme FROM user_preferences")
    assert (row.language, row.theme) == (None, None)


@pytest.mark.parametrize(
    ("language", "theme", "constraint"),
    [
        ("fr", None, "ck_user_preferences_language_valid"),
        ("", None, "ck_user_preferences_language_valid"),
        ("EN", None, "ck_user_preferences_language_valid"),
        (None, "blue", "ck_user_preferences_theme_valid"),
    ],
)
def test_invalid_user_preferences_are_rejected(
    fresh_database_url, language, theme, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    user = _preferences_user(fresh_database_url)

    with pytest.raises(IntegrityError, match=constraint):
        run_sql(
            fresh_database_url,
            INSERT_PREFERENCES,
            {"user": user, "language": language, "theme": theme},
        )


def test_a_second_preferences_row_for_a_user_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    user = _preferences_user(fresh_database_url)
    values = {"user": user, "language": "es", "theme": None}
    run_sql(fresh_database_url, INSERT_PREFERENCES, values)

    with pytest.raises(IntegrityError, match="uq_user_preferences_user_id"):
        run_sql(fresh_database_url, INSERT_PREFERENCES, {**values, "theme": "dark"})


def test_deleting_a_user_with_preferences_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    user = _preferences_user(fresh_database_url)
    run_sql(
        fresh_database_url,
        INSERT_PREFERENCES,
        {"user": user, "language": "en", "theme": "dark"},
    )

    with pytest.raises(IntegrityError, match="fk_user_preferences_user_id_users"):
        run_sql(fresh_database_url, "DELETE FROM users WHERE id = :id", {"id": user})


# --- 0020: business settings and operating hours (#37 A) ------------------------------


def test_0020_adds_exactly_the_settings_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0019")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0020")

    assert public_tables(fresh_database_url) - before == set(SETTINGS_TABLES)

    command.downgrade(config, "0019")

    assert version_nums(fresh_database_url) == ["0019"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_business_settings_columns_and_constraints(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "business_settings") == {
        **STANDARD_COLUMNS,
        "singleton": ("boolean", "NO", None, "true"),
        "center_name": ("character varying", "NO", 100, None),
        "contact_phone": ("character varying", "YES", 32, None),
        "contact_email": ("character varying", "YES", 254, None),
        "address": ("character varying", "YES", 300, None),
        "time_zone": (
            "character varying",
            "NO",
            64,
            "'America/Mexico_City'::character varying",
        ),
    }
    constraints = _constraints(fresh_database_url, "business_settings")
    assert set(constraints) == {
        "pk_business_settings",
        "uq_business_settings_singleton",
        "ck_business_settings_singleton_true",
        "ck_business_settings_center_name_trimmed",
        "ck_business_settings_center_name_not_blank",
        "ck_business_settings_email_format",
        "ck_business_settings_phone_format",
        "ck_business_settings_address_trimmed",
    }
    assert set(_indexes(fresh_database_url, "business_settings")) == {
        "pk_business_settings",
        "uq_business_settings_singleton",
    }


def test_operating_hours_columns_and_constraints(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    columns = _columns(fresh_database_url, "operating_hours")
    assert columns == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "weekday": ("smallint", "NO", None, None),
        "opens_at": ("time without time zone", "NO", None, None),
        "closes_at": ("time without time zone", "NO", None, None),
    }
    assert set(_constraints(fresh_database_url, "operating_hours")) == {
        "pk_operating_hours",
        "uq_operating_hours_weekday",
        "ck_operating_hours_weekday_range",
        "ck_operating_hours_opens_before_closes",
        "ck_operating_hours_zero_seconds",
    }


def test_0020_inserts_the_settings_row_and_the_default_week(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    [row] = run_sql(
        fresh_database_url,
        "SELECT singleton, center_name, contact_phone, contact_email, address,"
        " time_zone, version FROM business_settings",
    )
    assert tuple(row) == (
        True,
        "Classora",
        None,
        None,
        None,
        "America/Mexico_City",
        1,
    )
    hours = run_sql(
        fresh_database_url,
        "SELECT weekday, opens_at, closes_at FROM operating_hours ORDER BY weekday",
    )
    assert [
        (h.weekday, h.opens_at.isoformat(), h.closes_at.isoformat()) for h in hours
    ] == [
        *((day, "09:00:00", "20:00:00") for day in range(1, 6)),
        (6, "09:00:00", "14:00:00"),
    ]
    # Raw inserts: no audit event.
    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM audit_events")
    assert count.n == 0


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO business_settings (center_name) VALUES ('Otro')",
            "uq_business_settings_singleton",
        ),
        (
            "INSERT INTO business_settings (singleton, center_name)"
            " VALUES (false, 'Otro')",
            "ck_business_settings_singleton_true",
        ),
        (
            "UPDATE business_settings SET singleton = false",
            "ck_business_settings_singleton_true",
        ),
        (
            "UPDATE business_settings SET center_name = '   '",
            "ck_business_settings_center_name",
        ),
        (
            "UPDATE business_settings SET center_name = ' Classora'",
            "ck_business_settings_center_name_trimmed",
        ),
        (
            "UPDATE business_settings SET contact_email = 'nope'",
            "ck_business_settings_email_format",
        ),
        (
            "UPDATE business_settings SET contact_phone = '12'",
            "ck_business_settings_phone_format",
        ),
        (
            "UPDATE business_settings SET address = 'Calle 1 '",
            "ck_business_settings_address_trimmed",
        ),
        (
            "INSERT INTO operating_hours (weekday, opens_at, closes_at)"
            " VALUES (0, '09:00', '10:00')",
            "ck_operating_hours_weekday_range",
        ),
        (
            "INSERT INTO operating_hours (weekday, opens_at, closes_at)"
            " VALUES (8, '09:00', '10:00')",
            "ck_operating_hours_weekday_range",
        ),
        (
            "UPDATE operating_hours SET closes_at = opens_at WHERE weekday = 1",
            "ck_operating_hours_opens_before_closes",
        ),
        (
            "UPDATE operating_hours SET opens_at = '21:00' WHERE weekday = 1",
            "ck_operating_hours_opens_before_closes",
        ),
        (
            "UPDATE operating_hours SET opens_at = '09:00:30' WHERE weekday = 1",
            "ck_operating_hours_zero_seconds",
        ),
        (
            "INSERT INTO operating_hours (weekday, opens_at, closes_at)"
            " VALUES (1, '07:00', '08:00')",
            "uq_operating_hours_weekday",
        ),
    ],
)
def test_invalid_settings_rows_are_rejected(fresh_database_url, statement, constraint):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match=constraint):
        run_sql(fresh_database_url, statement)


def test_valid_contact_values_and_a_sunday_are_accepted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    run_sql(
        fresh_database_url,
        "UPDATE business_settings SET contact_email = 'hola@centro.mx',"
        " contact_phone = '+52 (55) 1234-5678', address = 'Av. Reforma 1'",
    )
    run_sql(
        fresh_database_url,
        "INSERT INTO operating_hours (weekday, opens_at, closes_at)"
        " VALUES (7, '10:00', '13:00')",
    )


# --- 0021: btree_gist, academic periods and closures (#38 A) -------------------------


def test_0021_adds_btree_gist_and_the_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0020")
    before = public_tables(fresh_database_url)
    extensions_before = [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)]
    assert "btree_gist" not in extensions_before
    command.upgrade(config, "0021")

    assert public_tables(fresh_database_url) - before == set(PERIOD_TABLES)
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == sorted(
        [*extensions_before, "btree_gist"]
    )
    # No reference seed and no demo data (A5).
    [count] = run_sql(
        fresh_database_url,
        "SELECT (SELECT count(*) FROM academic_periods)"
        " + (SELECT count(*) FROM closures) AS n",
    )
    assert count.n == 0

    command.downgrade(config, "0020")

    assert version_nums(fresh_database_url) == ["0020"]
    assert public_tables(fresh_database_url) == before
    assert [
        r.extname for r in run_sql(fresh_database_url, EXTENSIONS)
    ] == extensions_before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


RANGE_COLUMNS = {
    **STANDARD_COLUMNS,
    "name": ("character varying", "NO", 100, None),
    "starts_on": ("date", "NO", None, None),
    "ends_on": ("date", "NO", None, None),
    "is_active": ("boolean", "NO", None, "true"),
}


def test_academic_periods_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "academic_periods") == {
        **RANGE_COLUMNS,
        "period_type": ("character varying", "NO", 20, None),
    }
    constraints = _constraints(fresh_database_url, "academic_periods")
    assert set(constraints) == {
        "pk_academic_periods",
        "ck_academic_periods_name_trimmed",
        "ck_academic_periods_name_not_blank",
        "ck_academic_periods_period_type_known",
        "ck_academic_periods_starts_before_ends",
        "ck_academic_periods_span_max",
        "ex_academic_periods_no_overlap",
    }
    assert "730" in constraints["ck_academic_periods_span_max"]
    exclusion = constraints["ex_academic_periods_no_overlap"]
    assert exclusion.startswith("EXCLUDE USING gist (period_type WITH =,")
    assert "daterange(starts_on, ends_on, '[]'::text) WITH &&" in exclusion
    assert exclusion.endswith("WHERE (is_active)")
    indexes = _indexes(fresh_database_url, "academic_periods")
    assert set(indexes) == {
        "pk_academic_periods",
        "uq_academic_periods_name_ci",
        "ex_academic_periods_no_overlap",
    }
    assert "UNIQUE INDEX" in indexes["uq_academic_periods_name_ci"]
    assert "lower((name)::text)" in indexes["uq_academic_periods_name_ci"]


def test_closures_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "closures") == {
        **RANGE_COLUMNS,
        "closure_type": ("character varying", "NO", 20, None),
    }
    constraints = _constraints(fresh_database_url, "closures")
    assert set(constraints) == {
        "pk_closures",
        "ck_closures_name_trimmed",
        "ck_closures_name_not_blank",
        "ck_closures_closure_type_known",
        "ck_closures_starts_before_ends",
        "ck_closures_span_max",
    }
    assert "366" in constraints["ck_closures_span_max"]
    indexes = _indexes(fresh_database_url, "closures")
    assert set(indexes) == {"pk_closures", "ix_closures_active_range"}
    assert (
        "USING gist (daterange(starts_on, ends_on, '[]'::text))"
        in (indexes["ix_closures_active_range"])
    )
    assert indexes["ix_closures_active_range"].endswith("WHERE is_active")


Statement = tuple[str, dict[str, Any]]


def _period(
    name: str, period_type: str, starts: str, ends: str, active: bool = True
) -> Statement:
    return (
        "INSERT INTO academic_periods (name, period_type, starts_on, ends_on,"
        " is_active) VALUES (:name, :type, :starts, :ends, :active)",
        {
            "name": name,
            "type": period_type,
            "starts": date.fromisoformat(starts),
            "ends": date.fromisoformat(ends),
            "active": active,
        },
    )


def _closure(name: str, closure_type: str, starts: str, ends: str) -> Statement:
    return (
        "INSERT INTO closures (name, closure_type, starts_on, ends_on)"
        " VALUES (:name, :type, :starts, :ends)",
        {
            "name": name,
            "type": closure_type,
            "starts": date.fromisoformat(starts),
            "ends": date.fromisoformat(ends),
        },
    )


def _sqlstate(error: DBAPIError) -> str | None:
    orig = error.orig
    return getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )


def test_two_overlapping_active_school_years_are_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    run_sql(
        fresh_database_url,
        *_period("2026-2027", "school_year", "2026-08-01", "2027-07-15"),
    )

    with pytest.raises(IntegrityError, match="ex_academic_periods_no_overlap") as exc:
        run_sql(
            fresh_database_url,
            *_period("Otro", "school_year", "2027-01-01", "2027-12-31"),
        )
    assert _sqlstate(exc.value) == "23P01"


def test_periods_of_other_types_inactive_rows_and_touching_ranges_are_accepted(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    run_sql(
        fresh_database_url,
        *_period("2026-2027", "school_year", "2026-08-01", "2027-07-15"),
    )

    # Another type over the same days.
    run_sql(
        fresh_database_url,
        *_period("Semestre 1", "semester", "2026-08-01", "2026-12-20"),
    )
    # An inactive school year over the same days.
    run_sql(
        fresh_database_url,
        *_period("Borrador", "school_year", "2026-09-01", "2027-06-30", active=False),
    )
    # The next school year starts the day after the first one ends.
    run_sql(
        fresh_database_url,
        *_period("2027-2028", "school_year", "2027-07-16", "2028-07-15"),
    )
    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM academic_periods")
    assert count.n == 4


def test_two_school_years_sharing_one_day_are_rejected(fresh_database_url):
    # Inclusive bounds: ends_on = D and starts_on = D share the day D.
    command.upgrade(alembic_config(fresh_database_url), "head")
    run_sql(
        fresh_database_url,
        *_period("2026-2027", "school_year", "2026-08-01", "2027-07-15"),
    )

    with pytest.raises(IntegrityError, match="ex_academic_periods_no_overlap"):
        run_sql(
            fresh_database_url,
            *_period("2027-2028", "school_year", "2027-07-15", "2028-07-14"),
        )


def test_reactivating_an_overlapping_period_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    run_sql(
        fresh_database_url,
        *_period("A", "term", "2026-01-01", "2026-03-31", active=False),
    )
    run_sql(fresh_database_url, *_period("B", "term", "2026-03-01", "2026-05-31"))

    with pytest.raises(IntegrityError, match="ex_academic_periods_no_overlap"):
        run_sql(fresh_database_url, "UPDATE academic_periods SET is_active = true")


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            _period("Al revés", "term", "2026-02-01", "2026-01-31"),
            "ck_academic_periods_starts_before_ends",
        ),
        (
            _period("Largo", "school_year", "2026-01-01", "2028-01-02"),
            "ck_academic_periods_span_max",
        ),
        (
            _period("Trimestre", "quarter", "2026-01-01", "2026-03-31"),
            "ck_academic_periods_period_type_known",
        ),
        (
            _period(" Espacio", "term", "2026-01-01", "2026-03-31"),
            "ck_academic_periods_name_trimmed",
        ),
        (
            _period("   ", "term", "2026-01-01", "2026-03-31"),
            "ck_academic_periods_name",
        ),
        (
            _closure("Al revés", "holiday", "2026-12-26", "2026-12-25"),
            "ck_closures_starts_before_ends",
        ),
        (
            _closure("Largo", "vacation", "2026-01-01", "2027-01-03"),
            "ck_closures_span_max",
        ),
        (
            _closure("Puente", "bridge", "2026-12-25", "2026-12-25"),
            "ck_closures_closure_type_known",
        ),
        (
            _closure("Navidad ", "holiday", "2026-12-25", "2026-12-25"),
            "ck_closures_name_trimmed",
        ),
    ],
)
def test_invalid_period_and_closure_rows_are_rejected(
    fresh_database_url, statement, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match=constraint):
        run_sql(fresh_database_url, *statement)


def test_period_names_are_unique_ignoring_case_and_inactive_rows_count(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    run_sql(
        fresh_database_url,
        *_period("Ciclo 2026", "school_year", "2026-08-01", "2027-07-15", active=False),
    )

    with pytest.raises(IntegrityError, match="uq_academic_periods_name_ci"):
        run_sql(
            fresh_database_url,
            *_period("CICLO 2026", "semester", "2026-08-01", "2026-12-20"),
        )


def test_overlapping_closures_with_one_name_and_the_longest_spans_are_accepted(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    run_sql(
        fresh_database_url,
        *_closure("Vacaciones", "vacation", "2026-12-20", "2027-01-06"),
    )
    run_sql(
        fresh_database_url, *_closure("Navidad", "holiday", "2026-12-25", "2026-12-25")
    )
    run_sql(
        fresh_database_url, *_closure("Navidad", "holiday", "2026-12-25", "2026-12-25")
    )
    run_sql(fresh_database_url, *_closure("Año", "closure", "2026-01-01", "2027-01-02"))
    run_sql(
        fresh_database_url,
        *_period("Dos años", "school_year", "2030-01-01", "2031-12-31"),
    )
    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM closures")
    assert count.n == 4


# --- 0022: classrooms and their weekly windows (#39 A) -------------------------------


def test_0022_adds_exactly_the_classroom_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0021")
    before = public_tables(fresh_database_url)
    extensions = [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)]
    command.upgrade(config, "0022")

    assert public_tables(fresh_database_url) - before == set(CLASSROOM_TABLES)
    # btree_gist comes from 0021; 0022 creates no extension (A1).
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == extensions
    # No seed or demo rooms (A4).
    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM classrooms")
    assert count.n == 0

    command.downgrade(config, "0021")

    assert version_nums(fresh_database_url) == ["0021"]
    assert public_tables(fresh_database_url) == before
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == extensions
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_classrooms_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert _columns(fresh_database_url, "classrooms") == {
        **STANDARD_COLUMNS,
        "name": ("character varying", "NO", 100, None),
        "capacity": ("smallint", "NO", None, None),
        "is_active": ("boolean", "NO", None, "true"),
    }
    constraints = _constraints(fresh_database_url, "classrooms")
    assert set(constraints) == {
        "pk_classrooms",
        "ck_classrooms_name_trimmed",
        "ck_classrooms_name_not_blank",
        "ck_classrooms_capacity_range",
    }
    assert "1" in constraints["ck_classrooms_capacity_range"]
    assert "500" in constraints["ck_classrooms_capacity_range"]
    indexes = _indexes(fresh_database_url, "classrooms")
    assert set(indexes) == {"pk_classrooms", "uq_classrooms_name_ci"}
    assert "UNIQUE INDEX" in indexes["uq_classrooms_name_ci"]
    assert "lower((name)::text)" in indexes["uq_classrooms_name_ci"]


def test_classroom_windows_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "classroom_availability_windows"

    assert _columns(fresh_database_url, table) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "classroom_id": ("uuid", "NO", None, None),
        "weekday": ("smallint", "NO", None, None),
        "starts_at": ("time without time zone", "NO", None, None),
        "ends_at": ("time without time zone", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_classroom_id_classrooms",
        f"ck_{table}_weekday_range",
        f"ck_{table}_starts_before_ends",
        f"ck_{table}_zero_seconds",
        "ex_classroom_windows_no_overlap",
    }
    assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_classroom_id_classrooms"]
    exclusion = constraints["ex_classroom_windows_no_overlap"]
    assert exclusion.startswith(
        "EXCLUDE USING gist (classroom_id WITH =, weekday WITH =, tsrange("
    )
    assert "'[)'::text) WITH &&)" in exclusion
    indexes = _indexes(fresh_database_url, table)
    assert set(indexes) == {
        f"pk_{table}",
        f"ix_{table}_classroom_id",
        "ex_classroom_windows_no_overlap",
    }


def _classroom(name: str = "Aula 1", capacity: int = 10) -> Statement:
    return (
        "INSERT INTO classrooms (name, capacity) VALUES (:name, :capacity)"
        " RETURNING id",
        {"name": name, "capacity": capacity},
    )


def _window(classroom_id: Any, weekday: int, starts: str, ends: str) -> Statement:
    return (
        "INSERT INTO classroom_availability_windows"
        " (classroom_id, weekday, starts_at, ends_at)"
        " VALUES (:room, :weekday, :starts, :ends)",
        {
            "room": classroom_id,
            "weekday": weekday,
            "starts": time.fromisoformat(starts),
            "ends": time.fromisoformat(ends),
        },
    )


@pytest.mark.parametrize("capacity", [0, -1, 501])
def test_a_classroom_capacity_outside_1_to_500_is_rejected(
    fresh_database_url, capacity
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match="ck_classrooms_capacity_range"):
        run_sql(fresh_database_url, *_classroom(capacity=capacity))


def test_capacities_1_and_500_are_accepted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    run_sql(fresh_database_url, *_classroom("Cubículo", 1))
    run_sql(fresh_database_url, *_classroom("Auditorio", 500))

    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM classrooms")
    assert count.n == 2


@pytest.mark.parametrize(
    ("name", "constraint"),
    [(" Aula", "ck_classrooms_name_trimmed"), ("   ", "ck_classrooms_name")],
)
def test_untrimmed_or_blank_classroom_names_are_rejected(
    fresh_database_url, name, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match=constraint):
        run_sql(fresh_database_url, *_classroom(name))


def test_classroom_names_are_unique_ignoring_case_and_inactive_rows_count(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    run_sql(
        fresh_database_url,
        "INSERT INTO classrooms (name, capacity, is_active)"
        " VALUES ('Aula Magna', 30, false)",
    )

    with pytest.raises(IntegrityError, match="uq_classrooms_name_ci"):
        run_sql(fresh_database_url, *_classroom("AULA MAGNA"))


def test_overlapping_windows_of_one_room_and_weekday_are_rejected(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    [room] = run_sql(fresh_database_url, *_classroom())
    run_sql(fresh_database_url, *_window(room.id, 1, "09:00", "12:00"))

    with pytest.raises(IntegrityError, match="ex_classroom_windows_no_overlap") as exc:
        run_sql(fresh_database_url, *_window(room.id, 1, "11:00", "13:00"))
    assert _sqlstate(exc.value) == "23P01"


def test_touching_windows_other_weekdays_and_other_rooms_are_accepted(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    [room] = run_sql(fresh_database_url, *_classroom("Aula 1"))
    [other] = run_sql(fresh_database_url, *_classroom("Aula 2"))

    run_sql(fresh_database_url, *_window(room.id, 1, "09:00", "12:00"))
    # Touching: one ends when the other starts.
    run_sql(fresh_database_url, *_window(room.id, 1, "12:00", "14:00"))
    # The same window on another weekday and on another room.
    run_sql(fresh_database_url, *_window(room.id, 2, "09:00", "12:00"))
    run_sql(fresh_database_url, *_window(other.id, 1, "09:00", "12:00"))

    [count] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM classroom_availability_windows"
    )
    assert count.n == 4


@pytest.mark.parametrize(
    ("weekday", "starts", "ends", "constraint"),
    [
        (1, "12:00", "12:00", "starts_before_ends"),
        (1, "13:00", "12:00", "starts_before_ends"),
        (0, "09:00", "12:00", "weekday_range"),
        (8, "09:00", "12:00", "weekday_range"),
        (1, "09:00:30", "12:00", "zero_seconds"),
        (1, "09:00", "12:00:00.5", "zero_seconds"),
    ],
)
def test_invalid_window_rows_are_rejected(
    fresh_database_url, weekday, starts, ends, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    [room] = run_sql(fresh_database_url, *_classroom())

    with pytest.raises(
        IntegrityError, match=f"ck_classroom_availability_windows_{constraint}"
    ):
        run_sql(fresh_database_url, *_window(room.id, weekday, starts, ends))


def test_a_classroom_with_windows_cannot_be_deleted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    [room] = run_sql(fresh_database_url, *_classroom())
    run_sql(fresh_database_url, *_window(room.id, 1, "09:00", "12:00"))

    with pytest.raises(
        IntegrityError, match="fk_classroom_availability_windows_classroom_id"
    ):
        run_sql(
            fresh_database_url,
            "DELETE FROM classrooms WHERE id = :id",
            {"id": room.id},
        )


# --- 0023: approval requests, backfilled from registrations (#40 A) -----------------


def test_0023_adds_exactly_approval_requests_and_downgrade_drops_it(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0022")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0023")

    assert public_tables(fresh_database_url) - before == set(APPROVAL_TABLES)

    command.downgrade(config, "0022")

    assert version_nums(fresh_database_url) == ["0022"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_approval_requests_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "approval_requests"

    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "request_type": ("character varying", "NO", 64, None),
        "subject_id": ("uuid", "NO", None, None),
        "requester_user_id": ("uuid", "YES", None, None),
        "assignee_user_id": ("uuid", "YES", None, None),
        "status": ("character varying", "NO", 20, "'pending'::character varying"),
        "priority": ("character varying", "NO", 10, "'normal'::character varying"),
        "resolved_at": ("timestamp with time zone", "YES", None, None),
        "resolved_by_user_id": ("uuid", "YES", None, None),
        "reason": ("character varying", "YES", 1000, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"uq_{table}_request_type",
        f"fk_{table}_requester_user_id_users",
        f"fk_{table}_assignee_user_id_users",
        f"fk_{table}_resolved_by_user_id_users",
        f"ck_{table}_request_type_format",
        f"ck_{table}_status_valid",
        f"ck_{table}_priority_valid",
        f"ck_{table}_resolved_iff_final",
        f"ck_{table}_resolver_unless_cancelled",
        f"ck_{table}_reason_for_changes",
    }
    assert constraints[f"uq_{table}_request_type"] == (
        "UNIQUE (request_type, subject_id)"
    )
    for column in ("requester_user_id", "assignee_user_id", "resolved_by_user_id"):
        assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_{column}_users"]
    indexes = _indexes(fresh_database_url, table)
    assert set(indexes) == {
        f"pk_{table}",
        f"uq_{table}_request_type",
        "ix_approval_requests_status_priority_created_at_id",
        "ix_approval_requests_assignee_user_id_status",
        "ix_approval_requests_requester_user_id",
    }
    assert (
        "(status, priority, created_at, id)"
        in indexes["ix_approval_requests_status_priority_created_at_id"]
    )
    assert (
        "(assignee_user_id, status)"
        in indexes["ix_approval_requests_assignee_user_id_status"]
    )


def _registration(dsn: str, status: str, decider: Any = None) -> Any:
    """A raw user and registration request (no audit); returns the request."""
    [user] = run_sql(
        dsn,
        "INSERT INTO users (username, display_name, state)"
        " VALUES (:username, 'Requester', :state) RETURNING id",
        {
            "username": f"requester.{uuid.uuid4().hex[:8]}",
            "state": {"pending": "pending", "approved": "active"}.get(
                status, "archived"
            ),
        },
    )
    decided = status != "pending"
    [request] = run_sql(
        dsn,
        "INSERT INTO registration_requests"
        " (user_id, requested_role, status, decided_at, decided_by_user_id,"
        " created_at)"
        " VALUES (:user_id, 'guardian', :status, :decided_at, :decider,"
        " :created_at)"
        " RETURNING id, user_id, status, decided_at, decided_by_user_id,"
        " created_at",
        {
            "user_id": user.id,
            "status": status,
            "decided_at": datetime(2026, 10, 2, 9, 30, tzinfo=UTC) if decided else None,
            "decider": decider if decided else None,
            "created_at": datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
        },
    )
    return request


def test_0023_backfills_one_approval_request_per_registration(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0022")
    [reviewer] = run_sql(
        fresh_database_url,
        "INSERT INTO users (username, display_name, state)"
        " VALUES ('reviewer', 'Reviewer', 'active') RETURNING id",
    )
    registrations = {
        status: _registration(fresh_database_url, status, reviewer.id)
        for status in ("pending", "approved", "rejected")
    }
    [audit_before] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM audit_events"
    )

    command.upgrade(config, "0023")

    rows = run_sql(
        fresh_database_url,
        "SELECT id, request_type, subject_id, requester_user_id, assignee_user_id,"
        " status, priority, resolved_at, resolved_by_user_id, reason, created_at,"
        " version FROM approval_requests",
    )
    assert len(rows) == 3
    by_subject = {row.subject_id: row for row in rows}
    for status, registration in registrations.items():
        row = by_subject[registration.id]
        assert row.id.version == 7
        assert (row.request_type, row.status, row.priority, row.version) == (
            "identity.registration",
            status,
            "normal",
            1,
        )
        assert row.requester_user_id == registration.user_id
        assert row.assignee_user_id is None
        assert row.reason is None
        assert row.resolved_at == registration.decided_at
        assert row.resolved_by_user_id == registration.decided_by_user_id
        assert row.created_at == registration.created_at
    assert by_subject[registrations["pending"].id].resolved_at is None
    assert by_subject[registrations["approved"].id].resolved_by_user_id == reviewer.id
    # The migration writes no audit row (A2).
    [audit_after] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM audit_events"
    )
    assert audit_after.n == audit_before.n


def _approval(**overrides: Any) -> Statement:
    values: dict[str, Any] = {
        "request_type": "identity.registration",
        "subject_id": uuid.uuid4(),
        "status": "pending",
        "resolved_at": None,
        "reason": None,
        **overrides,
    }
    return (
        "INSERT INTO approval_requests"
        " (request_type, subject_id, status, resolved_at, reason)"
        " VALUES (:request_type, :subject_id, :status, :resolved_at, :reason)",
        values,
    )


RESOLVED = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"request_type": "registration"}, "request_type_format"),
        ({"request_type": "Identity.Registration"}, "request_type_format"),
        ({"request_type": "identity.registration."}, "request_type_format"),
        ({"status": "approved"}, "resolved_iff_final"),
        ({"status": "pending", "resolved_at": RESOLVED}, "resolved_iff_final"),
        ({"status": "approved", "resolved_at": RESOLVED}, "resolver_unless_cancelled"),
        ({"status": "rejected", "resolved_at": RESOLVED}, "resolver_unless_cancelled"),
        ({"status": "changes_requested"}, "reason_for_changes"),
        ({"status": "done"}, "status_valid"),
    ],
)
def test_invalid_approval_requests_are_rejected(
    fresh_database_url, overrides, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match=f"ck_approval_requests_{constraint}"):
        run_sql(fresh_database_url, *_approval(**overrides))


def test_a_bad_priority_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match="ck_approval_requests_priority_valid"):
        run_sql(
            fresh_database_url,
            "INSERT INTO approval_requests (request_type, subject_id, priority)"
            " VALUES ('identity.registration', :id, 'urgent')",
            {"id": uuid.uuid4()},
        )


def test_a_second_request_for_one_type_and_subject_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    subject = uuid.uuid4()
    run_sql(fresh_database_url, *_approval(subject_id=subject))

    with pytest.raises(IntegrityError, match="uq_approval_requests_request_type"):
        run_sql(fresh_database_url, *_approval(subject_id=subject))
    # Another type may point at the same subject.
    run_sql(
        fresh_database_url, *_approval(subject_id=subject, request_type="test.sample")
    )


def test_valid_approval_rows_are_accepted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    # A system cancel has no resolver; changes requested carry a reason.
    run_sql(fresh_database_url, *_approval(status="cancelled", resolved_at=RESOLVED))
    run_sql(
        fresh_database_url,
        *_approval(status="changes_requested", reason="Add the birth certificate."),
    )

    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM approval_requests")
    assert count.n == 2


# --- 0024: notifications (#41 A) ---------------------------------------------------


def test_0024_adds_exactly_notifications_and_downgrade_drops_it(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0023")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0024")

    assert public_tables(fresh_database_url) - before == set(NOTIFICATION_TABLES)
    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM notifications")
    assert count.n == 0

    command.downgrade(config, "0023")

    assert version_nums(fresh_database_url) == ["0023"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_notifications_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "notifications"

    # Not versioned (A1): no version column.
    standard = {k: v for k, v in STANDARD_COLUMNS.items() if k != "version"}
    assert _columns(fresh_database_url, table) == {
        **standard,
        "recipient_user_id": ("uuid", "NO", None, None),
        "notification_type": ("character varying", "NO", 64, None),
        "params": ("jsonb", "NO", None, "'{}'::jsonb"),
        "read_at": ("timestamp with time zone", "YES", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_recipient_user_id_users",
        f"ck_{table}_notification_type_format",
        f"ck_{table}_params_object",
    }
    assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_recipient_user_id_users"]
    indexes = _indexes(fresh_database_url, table)
    assert set(indexes) == {
        f"pk_{table}",
        "ix_notifications_recipient_created",
        "ix_notifications_recipient_unread",
    }
    assert (
        "(recipient_user_id, created_at DESC, id DESC)"
        in indexes["ix_notifications_recipient_created"]
    )
    assert indexes["ix_notifications_recipient_unread"].endswith(
        "(recipient_user_id) WHERE (read_at IS NULL)"
    )


def _recipient(dsn: str) -> Any:
    [user] = run_sql(
        dsn,
        "INSERT INTO users (username, display_name, state)"
        " VALUES (:username, 'Recipient', 'active') RETURNING id",
        {"username": f"recipient.{uuid.uuid4().hex[:8]}"},
    )
    return user.id


@pytest.mark.parametrize(
    ("notification_type", "params", "constraint"),
    [
        ("dev", "{}", "notification_type_format"),
        ("Dev.Demo", "{}", "notification_type_format"),
        ("dev.demo.", "{}", "notification_type_format"),
        ("dev demo.x", "{}", "notification_type_format"),
        ("dev.demo", "[]", "params_object"),
        ("dev.demo", '"label"', "params_object"),
        ("dev.demo", "null", "params_object"),
        ("dev.demo", "1", "params_object"),
    ],
)
def test_invalid_notifications_are_rejected(
    fresh_database_url, notification_type, params, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    recipient = _recipient(fresh_database_url)

    with pytest.raises(IntegrityError, match=f"ck_notifications_{constraint}"):
        run_sql(
            fresh_database_url,
            "INSERT INTO notifications (recipient_user_id, notification_type, params)"
            " VALUES (:recipient, :type, CAST(:params AS jsonb))",
            {"recipient": recipient, "type": notification_type, "params": params},
        )


def test_a_valid_notification_gets_the_defaults_and_its_user_cannot_be_deleted(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    recipient = _recipient(fresh_database_url)

    [row] = run_sql(
        fresh_database_url,
        "INSERT INTO notifications (recipient_user_id, notification_type)"
        " VALUES (:recipient, 'staff.qualification_approved')"
        " RETURNING params, read_at",
        {"recipient": recipient},
    )

    assert (row.params, row.read_at) == ({}, None)
    with pytest.raises(IntegrityError, match="fk_notifications_recipient_user_id"):
        run_sql(
            fresh_database_url,
            "DELETE FROM users WHERE id = :id",
            {"id": recipient},
        )


# --- 0025: tutor profiles (#43 A) --------------------------------------------------


def test_0025_adds_exactly_tutor_profiles_and_downgrade_drops_it(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0024")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0025")

    assert public_tables(fresh_database_url) - before == set(STAFF_TABLES)
    # Lazy rows (A3): nothing is inserted or backfilled.
    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM tutor_profiles")
    assert count.n == 0

    command.downgrade(config, "0024")

    assert version_nums(fresh_database_url) == ["0024"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_tutor_profiles_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "tutor_profiles"

    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "user_id": ("uuid", "NO", None, None),
        "professional_experience": ("text", "YES", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_user_id_users",
        f"uq_{table}_user_id",
        f"ck_{table}_professional_experience_length",
        f"ck_{table}_professional_experience_trimmed",
    }
    assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_user_id_users"]
    assert constraints[f"uq_{table}_user_id"] == "UNIQUE (user_id)"


def _tutor_profile(dsn: str, user_id: Any, experience: str | None = None) -> Any:
    [row] = run_sql(
        dsn,
        "INSERT INTO tutor_profiles (user_id, professional_experience)"
        " VALUES (:user_id, :experience) RETURNING version",
        {"user_id": user_id, "experience": experience},
    )
    return row


def test_one_tutor_profile_per_user(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    user_id = _recipient(fresh_database_url)

    assert _tutor_profile(fresh_database_url, user_id, "Algebra.").version == 1
    with pytest.raises(IntegrityError, match="uq_tutor_profiles_user_id"):
        _tutor_profile(fresh_database_url, user_id)
    with pytest.raises(IntegrityError, match="fk_tutor_profiles_user_id_users"):
        run_sql(fresh_database_url, "DELETE FROM users WHERE id = :id", {"id": user_id})


@pytest.mark.parametrize(
    ("experience", "constraint"),
    [
        ("x" * 4001, "professional_experience_length"),
        (" Algebra.", "professional_experience_trimmed"),
        ("Algebra. ", "professional_experience_trimmed"),
    ],
)
def test_invalid_tutor_profiles_are_rejected(
    fresh_database_url, experience, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match=f"ck_tutor_profiles_{constraint}"):
        _tutor_profile(fresh_database_url, _recipient(fresh_database_url), experience)


def test_4000_characters_and_no_experience_are_accepted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    _tutor_profile(fresh_database_url, _recipient(fresh_database_url), "x" * 4000)
    _tutor_profile(fresh_database_url, _recipient(fresh_database_url), None)

    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM tutor_profiles")
    assert count.n == 2


# --- 0026: tutor qualifications (#44 A) ----------------------------------------------


def test_0026_adds_exactly_tutor_qualifications_and_downgrade_drops_it(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0025")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0026")

    assert public_tables(fresh_database_url) - before == set(QUALIFICATION_TABLES)
    [count] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM tutor_qualifications"
    )
    assert count.n == 0

    command.downgrade(config, "0025")

    assert version_nums(fresh_database_url) == ["0025"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_tutor_qualifications_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "tutor_qualifications"

    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "tutor_user_id": ("uuid", "NO", None, None),
        "subject_id": ("uuid", "NO", None, None),
        "status": ("character varying", "NO", 20, "'pending'::character varying"),
        "decided_at": ("timestamp with time zone", "YES", None, None),
        "decided_by_user_id": ("uuid", "YES", None, None),
        "rejection_reason": ("character varying", "YES", 1000, None),
        # 0028 (#198 A1).
        "revoked_at": ("timestamp with time zone", "YES", None, None),
        "revoked_by_user_id": ("uuid", "YES", None, None),
        "revocation_reason": ("character varying", "YES", 1000, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_tutor_user_id_users",
        f"fk_{table}_subject_id_subjects",
        f"fk_{table}_decided_by_user_id_users",
        f"fk_{table}_revoked_by_user_id_users",
        f"ck_{table}_status_valid",
        f"ck_{table}_decided_iff_final",
        f"ck_{table}_reason_iff_rejected",
        f"ck_{table}_revocation_iff_revoked",
    }
    for fk in (
        "tutor_user_id_users",
        "subject_id_subjects",
        "revoked_by_user_id_users",
    ):
        assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_{fk}"]
    indexes = _indexes(fresh_database_url, table)
    assert set(indexes) == {
        f"pk_{table}",
        f"ix_{table}_tutor_user_id",
        f"ix_{table}_subject_id",
        "uq_tutor_qualifications_active",
    }
    active = indexes["uq_tutor_qualifications_active"]
    assert active.startswith("CREATE UNIQUE INDEX")
    assert "(tutor_user_id, subject_id)" in active
    assert "'pending'" in active and "'approved'" in active
    assert "'rejected'" not in active and "'cancelled'" not in active
    # #198 A2: the index is not changed; revoked rows are outside it.
    assert "'revoked'" not in active


def _qualification_subject(dsn: str) -> Any:
    [level] = run_sql(
        dsn,
        "INSERT INTO education_levels (name, sort_order)"
        " VALUES (:name, 10) RETURNING id",
        {"name": f"Nivel {uuid.uuid4().hex[:8]}"},
    )
    [subject] = run_sql(
        dsn,
        "INSERT INTO subjects (education_level_id, name)"
        " VALUES (:level, 'Física') RETURNING id",
        {"level": level.id},
    )
    return subject.id


def _qualification(
    dsn: str,
    tutor_id: Any,
    subject_id: Any,
    status: str = "pending",
    *,
    decided: bool | None = None,
    reason: str | None = None,
) -> None:
    if decided is None:
        decided = status != "pending"
    run_sql(
        dsn,
        "INSERT INTO tutor_qualifications"
        " (tutor_user_id, subject_id, status, decided_at, rejection_reason)"
        " VALUES (:tutor, :subject, :status,"
        " CASE WHEN :decided THEN now() END, :reason)",
        {
            "tutor": tutor_id,
            "subject": subject_id,
            "status": status,
            "decided": decided,
            "reason": reason,
        },
    )


@pytest.mark.parametrize("second", ["pending", "approved"])
@pytest.mark.parametrize("first", ["pending", "approved"])
def test_two_active_qualifications_for_one_tutor_and_subject_are_rejected(
    fresh_database_url, first, second
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)
    subject = _qualification_subject(fresh_database_url)

    _qualification(fresh_database_url, tutor, subject, first)
    with pytest.raises(IntegrityError, match="uq_tutor_qualifications_active"):
        _qualification(fresh_database_url, tutor, subject, second)


def test_rejected_and_cancelled_rows_do_not_block_a_new_request(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)
    other_tutor = _recipient(fresh_database_url)
    subject = _qualification_subject(fresh_database_url)

    _qualification(fresh_database_url, tutor, subject, "rejected", reason="No.")
    _qualification(fresh_database_url, tutor, subject, "cancelled")
    _qualification(fresh_database_url, tutor, subject, "rejected", reason="Again.")
    _qualification(fresh_database_url, tutor, subject, "pending")
    _qualification(fresh_database_url, other_tutor, subject, "approved")

    [count] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM tutor_qualifications"
    )
    assert count.n == 5
    with pytest.raises(IntegrityError, match="fk_tutor_qualifications_subject_id"):
        run_sql(
            fresh_database_url, "DELETE FROM subjects WHERE id = :id", {"id": subject}
        )


@pytest.mark.parametrize(
    ("status", "decided", "reason", "constraint"),
    [
        ("rejected", True, None, "reason_iff_rejected"),
        ("approved", False, None, "decided_iff_final"),
        ("rejected", False, "No.", "decided_iff_final"),
        ("cancelled", False, None, "decided_iff_final"),
        ("pending", True, None, "decided_iff_final"),
        ("approved", True, "No.", "reason_iff_rejected"),
        ("expired", False, None, "status_valid"),
    ],
)
def test_invalid_tutor_qualifications_are_rejected(
    fresh_database_url, status, decided, reason, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match=f"ck_tutor_qualifications_{constraint}"):
        _qualification(
            fresh_database_url,
            _recipient(fresh_database_url),
            _qualification_subject(fresh_database_url),
            status,
            decided=decided,
            reason=reason,
        )


# --- 0027: tutor availability (#45 A) ------------------------------------------------


def test_0027_adds_exactly_the_availability_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0026")
    before = public_tables(fresh_database_url)
    extensions = [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)]
    command.upgrade(config, "0027")

    assert public_tables(fresh_database_url) - before == set(AVAILABILITY_TABLES)
    # btree_gist comes from 0021; 0027 creates no extension.
    assert [r.extname for r in run_sql(fresh_database_url, EXTENSIONS)] == extensions
    [count] = run_sql(
        fresh_database_url,
        "SELECT (SELECT count(*) FROM tutor_availability_settings)"
        " + (SELECT count(*) FROM tutor_availability_windows)"
        " + (SELECT count(*) FROM tutor_availability_exceptions) AS n",
    )
    assert count.n == 0

    command.downgrade(config, "0026")

    assert version_nums(fresh_database_url) == ["0026"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_tutor_availability_settings_columns_and_constraints(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "tutor_availability_settings"

    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "user_id": ("uuid", "NO", None, None),
        "daily_limit_minutes": ("smallint", "YES", None, None),
        "weekly_limit_minutes": ("smallint", "YES", None, None),
        "required_break_minutes": ("smallint", "YES", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_user_id_users",
        f"uq_{table}_user_id",
        f"ck_{table}_daily_limit_range",
        f"ck_{table}_weekly_limit_range",
        f"ck_{table}_weekly_not_below_daily",
        f"ck_{table}_required_break_range",
    }
    assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_user_id_users"]


def test_tutor_availability_windows_columns_constraints_and_indexes(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "tutor_availability_windows"

    assert _columns(fresh_database_url, table) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "tutor_user_id": ("uuid", "NO", None, None),
        "weekday": ("smallint", "NO", None, None),
        "starts_at": ("time without time zone", "NO", None, None),
        "ends_at": ("time without time zone", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_tutor_user_id_users",
        f"ck_{table}_weekday_range",
        f"ck_{table}_starts_before_ends",
        f"ck_{table}_zero_seconds",
        "ex_tutor_windows_no_overlap",
    }
    assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_tutor_user_id_users"]
    exclusion = constraints["ex_tutor_windows_no_overlap"]
    assert exclusion.startswith(
        "EXCLUDE USING gist (tutor_user_id WITH =, weekday WITH =, tsrange("
    )
    assert "'[)'::text) WITH &&)" in exclusion
    assert set(_indexes(fresh_database_url, table)) == {
        f"pk_{table}",
        f"ix_{table}_tutor_user_id",
        "ex_tutor_windows_no_overlap",
    }


def test_tutor_availability_exceptions_columns_constraints_and_indexes(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "tutor_availability_exceptions"

    assert _columns(fresh_database_url, table) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "tutor_user_id": ("uuid", "NO", None, None),
        "kind": ("character varying", "NO", 20, None),
        "starts_on": ("date", "NO", None, None),
        "ends_on": ("date", "NO", None, None),
        "starts_at": ("time without time zone", "YES", None, None),
        "ends_at": ("time without time zone", "YES", None, None),
        "note": ("character varying", "YES", 200, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_tutor_user_id_users",
        f"ck_{table}_kind_valid",
        f"ck_{table}_starts_before_ends",
        f"ck_{table}_span_max",
        f"ck_{table}_hours_valid",
        f"ck_{table}_zero_seconds",
        f"ck_{table}_available_has_hours",
        f"ck_{table}_note_trimmed",
    }
    assert "366" in constraints[f"ck_{table}_span_max"]
    assert set(_indexes(fresh_database_url, table)) == {
        f"pk_{table}",
        f"ix_{table}_tutor_user_id",
    }


def _tutor_window(tutor_id: Any, weekday: int, starts: str, ends: str) -> Statement:
    return (
        "INSERT INTO tutor_availability_windows"
        " (tutor_user_id, weekday, starts_at, ends_at)"
        " VALUES (:tutor, :weekday, :starts, :ends)",
        {
            "tutor": tutor_id,
            "weekday": weekday,
            "starts": time.fromisoformat(starts),
            "ends": time.fromisoformat(ends),
        },
    )


def _tutor_exception(
    tutor_id: Any,
    kind: str = "unavailable",
    starts_on: str = "2026-12-21",
    ends_on: str = "2026-12-31",
    starts: str | None = None,
    ends: str | None = None,
    note: str | None = None,
) -> Statement:
    return (
        "INSERT INTO tutor_availability_exceptions"
        " (tutor_user_id, kind, starts_on, ends_on, starts_at, ends_at, note)"
        " VALUES (:tutor, :kind, :starts_on, :ends_on, :starts, :ends, :note)",
        {
            "tutor": tutor_id,
            "kind": kind,
            "starts_on": date.fromisoformat(starts_on),
            "ends_on": date.fromisoformat(ends_on),
            "starts": time.fromisoformat(starts) if starts else None,
            "ends": time.fromisoformat(ends) if ends else None,
            "note": note,
        },
    )


def test_overlapping_tutor_windows_on_one_weekday_are_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)
    run_sql(fresh_database_url, *_tutor_window(tutor, 1, "16:00", "20:00"))

    with pytest.raises(IntegrityError, match="ex_tutor_windows_no_overlap") as exc:
        run_sql(fresh_database_url, *_tutor_window(tutor, 1, "19:00", "21:00"))
    assert _sqlstate(exc.value) == "23P01"


def test_touching_tutor_windows_other_weekdays_and_tutors_are_accepted(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)
    other = _recipient(fresh_database_url)

    run_sql(fresh_database_url, *_tutor_window(tutor, 1, "16:00", "18:00"))
    run_sql(fresh_database_url, *_tutor_window(tutor, 1, "18:00", "20:00"))
    run_sql(fresh_database_url, *_tutor_window(tutor, 3, "16:00", "20:00"))
    run_sql(fresh_database_url, *_tutor_window(other, 1, "16:00", "20:00"))

    [count] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM tutor_availability_windows"
    )
    assert count.n == 4
    with pytest.raises(IntegrityError, match="fk_tutor_availability_windows_tutor"):
        run_sql(fresh_database_url, "DELETE FROM users WHERE id = :id", {"id": tutor})


@pytest.mark.parametrize(
    ("weekday", "starts", "ends", "constraint"),
    [
        (1, "12:00", "12:00", "starts_before_ends"),
        (1, "13:00", "12:00", "starts_before_ends"),
        (0, "09:00", "12:00", "weekday_range"),
        (8, "09:00", "12:00", "weekday_range"),
        (1, "09:00:30", "12:00", "zero_seconds"),
    ],
)
def test_invalid_tutor_window_rows_are_rejected(
    fresh_database_url, weekday, starts, ends, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)

    with pytest.raises(
        IntegrityError, match=f"ck_tutor_availability_windows_{constraint}"
    ):
        run_sql(fresh_database_url, *_tutor_window(tutor, weekday, starts, ends))


def test_one_availability_settings_row_per_tutor(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)
    statement = "INSERT INTO tutor_availability_settings (user_id) VALUES (:id)"

    run_sql(fresh_database_url, statement, {"id": tutor})
    with pytest.raises(IntegrityError, match="uq_tutor_availability_settings_user_id"):
        run_sql(fresh_database_url, statement, {"id": tutor})


def test_valid_and_overlapping_exceptions_are_accepted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)

    run_sql(fresh_database_url, *_tutor_exception(tutor, note="Vacaciones"))
    run_sql(fresh_database_url, *_tutor_exception(tutor, starts="10:00", ends="12:00"))
    run_sql(
        fresh_database_url,
        *_tutor_exception(tutor, "available", starts="10:00", ends="12:00"),
    )
    run_sql(
        fresh_database_url,
        *_tutor_exception(tutor, starts_on="2027-01-01", ends_on="2028-01-02"),
    )

    [count] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM tutor_availability_exceptions"
    )
    assert count.n == 4


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"kind": "available"}, "available_has_hours"),
        ({"starts": "10:00"}, "hours_valid"),
        ({"ends": "10:00"}, "hours_valid"),
        ({"starts": "12:00", "ends": "10:00"}, "hours_valid"),
        ({"starts": "10:00", "ends": "10:00"}, "hours_valid"),
        ({"starts_on": "2026-12-31", "ends_on": "2026-12-21"}, "starts_before_ends"),
        ({"starts_on": "2027-01-01", "ends_on": "2028-01-03"}, "span_max"),
        ({"kind": "busy", "starts": "10:00", "ends": "12:00"}, "kind_valid"),
        ({"note": " Vacaciones"}, "note_trimmed"),
        ({"starts": "10:00:30", "ends": "12:00"}, "zero_seconds"),
    ],
)
def test_invalid_exception_rows_are_rejected(fresh_database_url, overrides, constraint):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)

    with pytest.raises(
        IntegrityError, match=f"ck_tutor_availability_exceptions_{constraint}"
    ):
        run_sql(fresh_database_url, *_tutor_exception(tutor, **overrides))


# --- 0028: revoked tutor qualifications (#198 A) --------------------------------------


def _revocable(dsn: str) -> tuple[Any, Any]:
    """A tutor with an approved qualification: (tutor id, qualification id)."""
    tutor = _recipient(dsn)
    subject = _qualification_subject(dsn)
    _qualification(dsn, tutor, subject, "approved")
    [row] = run_sql(
        dsn,
        "SELECT id FROM tutor_qualifications WHERE tutor_user_id = :tutor",
        {"tutor": tutor},
    )
    return tutor, row.id


REVOKE_SQL = (
    "UPDATE tutor_qualifications SET status = :status,"
    " decided_at = CASE WHEN :decided THEN decided_at END,"
    " revoked_at = CASE WHEN :revoked_at THEN now() END,"
    " revoked_by_user_id = :by, revocation_reason = :reason"
    " WHERE id = :id"
)


def _revoke(
    dsn: str,
    qualification_id: Any,
    by: Any,
    *,
    status: str = "revoked",
    decided: bool = True,
    revoked_at: bool = True,
    reason: str | None = "Outdated.",
) -> None:
    run_sql(
        dsn,
        REVOKE_SQL,
        {
            "status": status,
            "decided": decided,
            "revoked_at": revoked_at,
            "by": by,
            "reason": reason,
            "id": qualification_id,
        },
    )


def test_0028_adds_exactly_the_revocation_columns_and_downgrade_restores_0027(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    table = "tutor_qualifications"
    command.upgrade(config, "0027")
    columns = _columns(fresh_database_url, table)
    constraints = _constraints(fresh_database_url, table)
    indexes = _indexes(fresh_database_url, table)
    command.upgrade(config, "0028")

    assert set(_columns(fresh_database_url, table)) - set(columns) == {
        "revoked_at",
        "revoked_by_user_id",
        "revocation_reason",
    }
    assert set(_constraints(fresh_database_url, table)) - set(constraints) == {
        f"fk_{table}_revoked_by_user_id_users",
        f"ck_{table}_revocation_iff_revoked",
    }
    assert _indexes(fresh_database_url, table) == indexes

    command.downgrade(config, "0027")

    assert version_nums(fresh_database_url) == ["0027"]
    assert _columns(fresh_database_url, table) == columns
    assert _constraints(fresh_database_url, table) == constraints
    assert _indexes(fresh_database_url, table) == indexes
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_0028_downgrade_refuses_while_revoked_rows_exist(fresh_database_url):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "head")
    tutor, qualification = _revocable(fresh_database_url)
    _revoke(fresh_database_url, qualification, tutor)

    with pytest.raises(Exception, match="revoked rows"):
        command.downgrade(config, "0027")

    assert version_nums(fresh_database_url) == [HEAD]
    [row] = run_sql(
        fresh_database_url,
        "SELECT status FROM tutor_qualifications WHERE id = :id",
        {"id": qualification},
    )
    assert row.status == "revoked"


def test_a_revoked_row_keeps_its_decision_and_a_new_request_is_accepted(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor, qualification = _revocable(fresh_database_url)
    reviewer = _recipient(fresh_database_url)
    _revoke(fresh_database_url, qualification, reviewer)
    [row] = run_sql(
        fresh_database_url,
        "SELECT subject_id, decided_at, rejection_reason FROM tutor_qualifications"
        " WHERE id = :id",
        {"id": qualification},
    )
    assert row.decided_at is not None and row.rejection_reason is None

    # A2: a revoked row plus a new pending row for the same tutor and subject.
    _qualification(fresh_database_url, tutor, row.subject_id, "pending")

    [count] = run_sql(
        fresh_database_url,
        "SELECT count(*) AS n FROM tutor_qualifications"
        " WHERE tutor_user_id = :tutor AND subject_id = :subject",
        {"tutor": tutor, "subject": row.subject_id},
    )
    assert count.n == 2
    with pytest.raises(IntegrityError, match="fk_tutor_qualifications_revoked_by"):
        run_sql(
            fresh_database_url, "DELETE FROM users WHERE id = :id", {"id": reviewer}
        )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"reason": None}, "revocation_iff_revoked"),
        ({"revoked_at": False}, "revocation_iff_revoked"),
        ({"by": None}, "revocation_iff_revoked"),
        ({"status": "approved"}, "revocation_iff_revoked"),
        ({"status": "approved", "reason": None, "by": None}, "revocation_iff_revoked"),
        ({"decided": False}, "decided_iff_final"),
    ],
)
def test_invalid_revoked_tutor_qualifications_are_rejected(
    fresh_database_url, overrides, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor, qualification = _revocable(fresh_database_url)
    values: dict[str, Any] = {"by": tutor, **overrides}

    with pytest.raises(IntegrityError, match=f"ck_tutor_qualifications_{constraint}"):
        _revoke(fresh_database_url, qualification, **values)


# --- 0029: tutor limits and preferred classrooms (#197 A) ----------------------------


def test_0029_adds_the_limits_and_the_preferred_rooms_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0028")
    before = public_tables(fresh_database_url)
    settings_columns = set(_columns(fresh_database_url, "tutor_availability_settings"))
    tutor = _recipient(fresh_database_url)
    run_sql(
        fresh_database_url,
        "INSERT INTO tutor_availability_settings (user_id) VALUES (:id)",
        {"id": tutor},
    )

    command.upgrade(config, "0029")

    assert public_tables(fresh_database_url) - before == set(PREFERRED_ROOM_TABLES)
    # An existing settings row has no limits (NULL: no limit, no break).
    [row] = run_sql(
        fresh_database_url,
        "SELECT daily_limit_minutes, weekly_limit_minutes, required_break_minutes"
        " FROM tutor_availability_settings",
    )
    assert tuple(row) == (None, None, None)
    [count] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM tutor_preferred_classrooms"
    )
    assert count.n == 0

    command.downgrade(config, "0028")

    assert version_nums(fresh_database_url) == ["0028"]
    assert public_tables(fresh_database_url) == before
    assert (
        set(_columns(fresh_database_url, "tutor_availability_settings"))
        == settings_columns
    )
    assert set(_constraints(fresh_database_url, "tutor_availability_settings")) == {
        "pk_tutor_availability_settings",
        "fk_tutor_availability_settings_user_id_users",
        "uq_tutor_availability_settings_user_id",
    }
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_tutor_preferred_classrooms_columns_constraints_and_indexes(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "tutor_preferred_classrooms"

    assert _columns(fresh_database_url, table) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "tutor_user_id": ("uuid", "NO", None, None),
        "classroom_id": ("uuid", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_tutor_user_id_users",
        f"fk_{table}_classroom_id_classrooms",
        "uq_tutor_preferred_classrooms_tutor_classroom",
    }
    assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_tutor_user_id_users"]
    assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_classroom_id_classrooms"]
    assert constraints["uq_tutor_preferred_classrooms_tutor_classroom"] == (
        "UNIQUE (tutor_user_id, classroom_id)"
    )
    assert set(_indexes(fresh_database_url, table)) == {
        f"pk_{table}",
        f"ix_{table}_classroom_id",
        "uq_tutor_preferred_classrooms_tutor_classroom",
    }


def _limits(
    tutor_id: Any,
    daily: int | None = None,
    weekly: int | None = None,
    pause: int | None = None,
) -> Statement:
    return (
        "INSERT INTO tutor_availability_settings"
        " (user_id, daily_limit_minutes, weekly_limit_minutes,"
        " required_break_minutes) VALUES (:id, :daily, :weekly, :pause)",
        {"id": tutor_id, "daily": daily, "weekly": weekly, "pause": pause},
    )


@pytest.mark.parametrize(
    ("daily", "weekly", "pause"),
    [
        (None, None, None),
        (15, 15, 0),
        (1440, 10080, 240),
        (240, 240, None),
        (None, 15, None),
        (1440, None, None),
    ],
)
def test_valid_teaching_limits_are_accepted(fresh_database_url, daily, weekly, pause):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)

    run_sql(fresh_database_url, *_limits(tutor, daily, weekly, pause))

    [row] = run_sql(
        fresh_database_url,
        "SELECT daily_limit_minutes AS d, weekly_limit_minutes AS w,"
        " required_break_minutes AS b FROM tutor_availability_settings",
    )
    assert (row.d, row.w, row.b) == (daily, weekly, pause)


@pytest.mark.parametrize(
    ("daily", "weekly", "pause", "constraint"),
    [
        (14, None, None, "daily_limit_range"),
        (1441, None, None, "daily_limit_range"),
        (0, None, None, "daily_limit_range"),
        (None, 14, None, "weekly_limit_range"),
        (None, 10081, None, "weekly_limit_range"),
        (240, 239, None, "weekly_not_below_daily"),
        (None, None, -1, "required_break_range"),
        (None, None, 241, "required_break_range"),
    ],
)
def test_invalid_teaching_limits_are_rejected(
    fresh_database_url, daily, weekly, pause, constraint
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)

    with pytest.raises(
        IntegrityError, match=f"ck_tutor_availability_settings_{constraint}"
    ):
        run_sql(fresh_database_url, *_limits(tutor, daily, weekly, pause))


def _preferred(tutor_id: Any, classroom_id: Any) -> Statement:
    return (
        "INSERT INTO tutor_preferred_classrooms (tutor_user_id, classroom_id)"
        " VALUES (:tutor, :room)",
        {"tutor": tutor_id, "room": classroom_id},
    )


def test_a_preferred_classroom_is_unique_per_tutor_and_restricts_deletes(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    tutor = _recipient(fresh_database_url)
    other = _recipient(fresh_database_url)
    [room] = run_sql(fresh_database_url, *_classroom("Aula 1"))
    [second] = run_sql(fresh_database_url, *_classroom("Aula 2"))

    run_sql(fresh_database_url, *_preferred(tutor, room.id))
    run_sql(fresh_database_url, *_preferred(tutor, second.id))
    run_sql(fresh_database_url, *_preferred(other, room.id))

    with pytest.raises(
        IntegrityError, match="uq_tutor_preferred_classrooms_tutor_classroom"
    ) as exc:
        run_sql(fresh_database_url, *_preferred(tutor, room.id))
    assert _sqlstate(exc.value) == "23505"
    with pytest.raises(
        IntegrityError, match="fk_tutor_preferred_classrooms_classroom_id"
    ):
        run_sql(
            fresh_database_url, "DELETE FROM classrooms WHERE id = :id", {"id": room.id}
        )
    with pytest.raises(
        IntegrityError, match="fk_tutor_preferred_classrooms_tutor_user_id"
    ):
        run_sql(fresh_database_url, "DELETE FROM users WHERE id = :id", {"id": other})


# --- 0030: class sessions (#47 A) ------------------------------------------


def test_0030_adds_exactly_the_session_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0029")
    before = public_tables(fresh_database_url)
    [gist] = run_sql(
        fresh_database_url,
        "SELECT count(*) AS n FROM pg_extension WHERE extname = 'btree_gist'",
    )
    assert gist.n == 1

    command.upgrade(config, "0030")

    assert public_tables(fresh_database_url) - before == set(SESSION_TABLES)
    # Only the tables of later revisions (0031 scheduling_policies, 0032 the
    # course tables, 0033 class_session_overrides, 0034 the offer tables,
    # 0035 the series tables and the class_sessions occurrence columns) differ.
    assert [
        diff
        for diff in metadata_diff(fresh_database_url)
        if not _of_later_revision(
            diff,
            [
                *POLICY_TABLES,
                *COURSE_TABLES,
                *OVERRIDE_TABLES,
                *OFFER_TABLES,
                *SERIES_TABLES,
            ],
        )
    ] == []

    command.downgrade(config, "0029")

    assert version_nums(fresh_database_url) == ["0029"]
    assert public_tables(fresh_database_url) == before
    # 0030 never owned btree_gist (0021 did): it is still there.
    [gist] = run_sql(
        fresh_database_url,
        "SELECT count(*) AS n FROM pg_extension WHERE extname = 'btree_gist'",
    )
    assert gist.n == 1
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def _of_0036(diff: Any) -> bool:
    """A metadata difference that revision 0036 adds to existing tables (its
    columns, indexes and foreign key); every caller of
    :func:`_of_later_revision` is at an earlier revision."""
    if diff[0] == "add_column":
        return diff[3].name in SERIES_OFFER_COLUMNS.get(diff[2], set())
    if diff[0] == "add_index":
        return diff[1].name in SERIES_OFFER_INDEXES
    if diff[0] == "add_fk":
        return diff[1].name == "fk_assignment_offers_class_series_id_class_series"
    return False


LINEAGE_COLUMN = "split_from_series_id"
LINEAGE_INDEX = "ix_class_series_split_from_series_id"
LINEAGE_FK = "fk_class_series_split_from_series_id_class_series"
"""The objects revision 0038 adds to class_series (#54 A2)."""


def _of_0038(diff: Any) -> bool:
    """A metadata difference that revision 0038 adds (the series lineage
    column, its index and its foreign key)."""
    if diff[0] == "add_column":
        return diff[2] == "class_series" and diff[3].name == LINEAGE_COLUMN
    if diff[0] == "add_index":
        return diff[1].name == LINEAGE_INDEX
    if diff[0] == "add_fk":
        return diff[1].name == LINEAGE_FK
    return False


OVERRIDE_CONTEXT_COLUMN = "class_series_id"
OVERRIDE_CONTEXT_INDEX = "ix_class_session_overrides_class_series_id"
OVERRIDE_CONTEXT_FK = "fk_class_session_overrides_class_series_id_class_series"
"""The objects revision 0039 adds to class_session_overrides (#236 D1)."""


def _of_0039(diff: Any) -> bool:
    """A metadata difference that revision 0039 adds (the series override
    context column, its index and its foreign key)."""
    if diff[0] == "add_column":
        return (
            diff[2] == "class_session_overrides"
            and diff[3].name == OVERRIDE_CONTEXT_COLUMN
        )
    if diff[0] == "add_index":
        return diff[1].name == OVERRIDE_CONTEXT_INDEX
    if diff[0] == "add_fk":
        return diff[1].name == OVERRIDE_CONTEXT_FK
    return False


SUBSTITUTIONS_TABLE = "class_session_substitutions"
SUBSTITUTIONS_INDEX = "ix_class_session_substitutions_class_session_id"
"""The objects revision 0040 adds (#55 A1, A2)."""


def _of_0040(diff: Any) -> bool:
    """A metadata difference that revision 0040 adds (the offer ``kind``
    column and the substitutions table with its index)."""
    if diff[0] == "add_column":
        return diff[2] == "assignment_offers" and diff[3].name == "kind"
    if diff[0] == "add_table":
        return diff[1].name == SUBSTITUTIONS_TABLE
    if diff[0] == "add_index":
        return diff[1].name == SUBSTITUTIONS_INDEX
    return False


GROUP_COLUMNS = {
    ("class_series", "group_id"),
    ("class_sessions", "group_id"),
    ("class_session_overrides", "group_enrollment_id"),
}
GROUP_INDEXES = {
    "ix_class_series_group_id",
    "ix_class_sessions_group_id_starts_at",
    "ix_class_session_overrides_group_enrollment_id",
}
GROUP_FKS = {
    "fk_class_series_group_id_class_groups",
    "fk_class_sessions_group_id_class_groups",
    "fk_class_session_overrides_group_enrollment_id_group_en_59d0",
}
"""The objects revision 0041 adds to existing tables (#56 A3, A4, A6)."""


def _of_0041(diff: Any) -> bool:
    """A metadata difference that revision 0041 adds: the two group tables
    (and their indexes), the ``group_id`` / ``group_enrollment_id`` columns,
    their indexes and keys, and ``class_session_overrides.class_session_id``
    becoming nullable."""
    if isinstance(diff, list):
        return all(
            change[0] == "modify_nullable"
            and change[2] == "class_session_overrides"
            and change[3] == "class_session_id"
            for change in diff
        )
    if diff[0] == "add_table":
        return diff[1].name in GROUP_TABLES
    if diff[0] == "add_column":
        return (diff[2], diff[3].name) in GROUP_COLUMNS
    if diff[0] == "add_index":
        return diff[1].name in GROUP_INDEXES or diff[1].table.name in GROUP_TABLES
    if diff[0] == "add_fk":
        # The overrides key's name is shortened to 63 characters in the
        # database, not in the metadata.
        return diff[1].name in GROUP_FKS or str(diff[1].name).startswith(
            "fk_class_session_overrides_group_enrollment_id"
        )
    return False


def _of_0042(diff: Any) -> bool:
    """A metadata difference that revision 0042 adds: the
    ``session_requests`` table and its indexes (#59 A1)."""
    if diff[0] == "add_table":
        return diff[1].name in SESSION_REQUEST_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in SESSION_REQUEST_TABLES
    return False


SESSION_REQUEST_DECISION_COLUMNS = {"class_session_id", "rejection_reason"}
SESSION_REQUEST_DECISION_INDEX = "uq_session_requests_class_session_id"
SESSION_REQUEST_DECISION_FK = "fk_session_requests_class_session_id_class_sessions"
"""The objects revision 0043 adds to ``session_requests`` (#60 A1)."""


def _of_0043(diff: Any) -> bool:
    """A metadata difference that revision 0043 adds: the two decision
    columns of ``session_requests``, their unique index and the key."""
    if diff[0] == "add_column":
        return (
            diff[2] == "session_requests"
            and diff[3].name in SESSION_REQUEST_DECISION_COLUMNS
        )
    if diff[0] == "add_index":
        return diff[1].name == SESSION_REQUEST_DECISION_INDEX
    if diff[0] == "add_fk":
        return diff[1].name == SESSION_REQUEST_DECISION_FK
    return False


def _of_0044(diff: Any) -> bool:
    """A metadata difference that revision 0044 adds: the
    ``session_change_requests`` table and its indexes (#62 A1)."""
    if diff[0] == "add_table":
        return diff[1].name in CHANGE_REQUEST_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in CHANGE_REQUEST_TABLES
    return False


def _of_0045(diff: Any) -> bool:
    """A metadata difference that revision 0045 adds: the availability
    change requests and reassignment flags tables and their indexes (#63
    A1, A2)."""
    if diff[0] == "add_table":
        return diff[1].name in AVAILABILITY_CHANGE_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in AVAILABILITY_CHANGE_TABLES
    return False


COHORT_TABLES = ["course_cohorts"]
COHORT_COLUMNS = {("class_series", "cohort_id"), ("class_sessions", "cohort_id")}
COHORT_INDEXES = {
    "ix_class_series_cohort_id",
    "ix_class_sessions_cohort_id_starts_at",
}
COHORT_FKS = {
    "fk_class_series_cohort_id_course_cohorts",
    "fk_class_sessions_cohort_id_course_cohorts",
}
"""The objects revision 0046 adds (#65 A2-A4)."""


def _of_0046(diff: Any) -> bool:
    """A metadata difference that revision 0046 adds: the ``course_cohorts``
    table (and its indexes) and the ``cohort_id`` columns of the series and
    sessions, their indexes and keys."""
    if diff[0] == "add_table":
        return diff[1].name in COHORT_TABLES
    if diff[0] == "add_column":
        return (diff[2], diff[3].name) in COHORT_COLUMNS
    if diff[0] == "add_index":
        return diff[1].name in COHORT_INDEXES or diff[1].table.name in COHORT_TABLES
    if diff[0] == "add_fk":
        return diff[1].name in COHORT_FKS
    return False


COHORT_ENROLLMENT_TABLES = ["cohort_enrollments"]
COHORT_ENROLLMENT_COLUMN = ("class_session_overrides", "cohort_enrollment_id")
COHORT_ENROLLMENT_INDEX = "ix_class_session_overrides_cohort_enrollment_id"
COHORT_ENROLLMENT_FK = "fk_class_session_overrides_cohort_enrollment_id"
"""The objects revision 0047 adds (#66 A2, A3)."""


def _of_0047(diff: Any) -> bool:
    """A metadata difference that revision 0047 adds: the
    ``cohort_enrollments`` table (and its indexes) and
    ``class_session_overrides.cohort_enrollment_id``, its index and key."""
    if diff[0] == "add_table":
        return diff[1].name in COHORT_ENROLLMENT_TABLES
    if diff[0] == "add_column":
        return (diff[2], diff[3].name) == COHORT_ENROLLMENT_COLUMN
    if diff[0] == "add_index":
        return (
            diff[1].name == COHORT_ENROLLMENT_INDEX
            or diff[1].table.name in COHORT_ENROLLMENT_TABLES
        )
    if diff[0] == "add_fk":
        return diff[1].name == COHORT_ENROLLMENT_FK
    return False


FIRST_CONFIRMED_COLUMN = ("class_sessions", "first_confirmed_at")
"""The column revision 0048 adds (#67 A1)."""


def _of_0048(diff: Any) -> bool:
    """A metadata difference that revision 0048 adds:
    ``class_sessions.first_confirmed_at`` (its CHECK is not compared)."""
    if diff[0] == "add_column":
        return (diff[2], diff[3].name) == FIRST_CONFIRMED_COLUMN
    return False


def _of_0049(diff: Any) -> bool:
    """A metadata difference that revision 0049 adds: the two attendance
    tables (and their indexes)."""
    if diff[0] == "add_table":
        return diff[1].name in ATTENDANCE_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in ATTENDANCE_TABLES
    return False


def _of_0050(diff: Any) -> bool:
    """A metadata difference that revision 0050 adds: the closeout table
    (and its index)."""
    if diff[0] == "add_table":
        return diff[1].name in CLOSEOUT_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in CLOSEOUT_TABLES
    return False


def _of_0051(diff: Any) -> bool:
    """A metadata difference that revision 0051 adds: the two review tables
    (and their indexes), the session_closeouts columns and the current
    approval key (its CHECKs are not compared)."""
    if diff[0] == "add_table":
        return diff[1].name in CLOSEOUT_REVIEW_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in CLOSEOUT_REVIEW_TABLES
    if diff[0] == "add_column":
        return diff[2] == "session_closeouts" and diff[3].name in (
            CLOSEOUT_REVIEW_COLUMNS
        )
    if diff[0] == "add_fk":
        return diff[1].name == "fk_session_closeouts_current_approval_id"
    return False


def _of_0052(diff: Any) -> bool:
    """A metadata difference that revision 0052 adds: the two on-behalf
    columns of session_closeouts, their key (the CHECKs are not compared)
    and the overdue index on class_sessions."""
    if diff[0] == "add_column":
        return diff[2] == "session_closeouts" and diff[3].name in ON_BEHALF_COLUMNS
    if diff[0] == "add_fk":
        return diff[1].name == "fk_session_closeouts_on_behalf_by_user_id_users"
    if diff[0] == "add_index":
        return diff[1].name == OVERDUE_INDEX
    return False


def _of_0053(diff: Any) -> bool:
    """A metadata difference that revision 0053 adds: the session_notes
    table and its indexes (#79 A1)."""
    if diff[0] == "add_table":
        return diff[1].name in NOTE_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in NOTE_TABLES
    return False


def _of_0054(diff: Any) -> bool:
    """A metadata difference that revision 0054 adds: the evaluation tables
    and their indexes (#80 A1)."""
    if diff[0] == "add_table":
        return diff[1].name in EVALUATION_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in EVALUATION_TABLES
    return False


def _of_0055(diff: Any) -> bool:
    """A metadata difference that revision 0055 adds: the learning plan
    tables and their indexes (#81 A1)."""
    if diff[0] == "add_table":
        return diff[1].name in LEARNING_PLAN_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in LEARNING_PLAN_TABLES
    return False


def _of_0056(diff: Any) -> bool:
    """A metadata difference that revision 0056 adds: the homework
    assignment tables and their indexes (#82 A1)."""
    if diff[0] == "add_table":
        return diff[1].name in ASSIGNMENT_TABLES
    if diff[0] == "add_index":
        return diff[1].table.name in ASSIGNMENT_TABLES
    return False


def _of_0055_on(diff: Any) -> bool:
    """A metadata difference that revision 0055 or a later one adds."""
    return _of_0055(diff) or _of_0056(diff)


def _of_0054_on(diff: Any) -> bool:
    """A metadata difference that revision 0054 or a later one adds."""
    return _of_0054(diff) or _of_0055_on(diff)


def _of_0053_on(diff: Any) -> bool:
    """A metadata difference that revision 0053 or a later one adds."""
    return _of_0053(diff) or _of_0054_on(diff)


def _of_0052_on(diff: Any) -> bool:
    """A metadata difference that revision 0052 or a later one adds."""
    return _of_0052(diff) or _of_0053_on(diff)


def _of_0051_on(diff: Any) -> bool:
    """A metadata difference that revision 0051 or a later one adds."""
    return _of_0051(diff) or _of_0052_on(diff)


def _of_0050_on(diff: Any) -> bool:
    """A metadata difference that revision 0050 or a later one adds."""
    return _of_0050(diff) or _of_0051_on(diff)


def _of_0049_on(diff: Any) -> bool:
    """A metadata difference that revision 0049 or a later one adds."""
    return _of_0049(diff) or _of_0050_on(diff)


def _of_0048_on(diff: Any) -> bool:
    """A metadata difference that revision 0048 or a later one adds."""
    return _of_0048(diff) or _of_0049_on(diff)


def _of_later_revision(diff: Any, tables: list[str]) -> bool:
    """A metadata difference that is a table (or one of its indexes) a later
    revision adds; with the 0034 tables, also 0034's class_sessions columns,
    their foreign key and unique constraint; and 0036's and 0038's
    objects."""
    if (
        _of_0036(diff)
        or _of_0038(diff)
        or _of_0039(diff)
        or _of_0040(diff)
        or _of_0041(diff)
        or _of_0042(diff)
        or _of_0043(diff)
        or _of_0044(diff)
        or _of_0045(diff)
        or _of_0046(diff)
        or _of_0047(diff)
        or _of_0048_on(diff)
    ):
        return True
    if diff[0] == "add_table":
        return diff[1].name in tables
    if diff[0] == "add_index":
        return diff[1].table.name in tables
    if "class_series" not in tables:
        return False
    if diff[0] == "add_column":
        return diff[2] == "class_sessions" and diff[3].name in SERIES_SESSION_COLUMNS
    if diff[0] == "add_fk":
        return diff[1].name == "fk_class_sessions_series_id_class_series"
    if diff[0] == "add_constraint":
        return diff[1].name == "uq_class_sessions_series_original_start"
    return False


def test_class_sessions_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "class_sessions"

    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "kind": ("character varying", "NO", 20, None),
        "state": ("character varying", "NO", 20, None),
        "subject_id": ("uuid", "NO", None, None),
        "tutor_user_id": ("uuid", "YES", None, None),
        "classroom_id": ("uuid", "YES", None, None),
        "starts_at": ("timestamp with time zone", "NO", None, None),
        "ends_at": ("timestamp with time zone", "NO", None, None),
        "scheduled_minutes": ("smallint", "NO", None, None),
        "actual_minutes": ("smallint", "YES", None, None),
        "cancelled_at": ("timestamp with time zone", "YES", None, None),
        "cancellation_reason": ("character varying", "YES", 1000, None),
        # 0034 (#53 B4)
        "series_id": ("uuid", "YES", None, None),
        "original_start": ("timestamp with time zone", "YES", None, None),
        "is_exception": ("boolean", "NO", None, "false"),
        "conflict_codes": ("ARRAY", "NO", None, "'{}'::character varying[]"),
        # 0041 (#56 A4)
        "group_id": ("uuid", "YES", None, None),
        # 0046 (#65 A4)
        "cohort_id": ("uuid", "YES", None, None),
        # 0048 (#67 A1)
        "first_confirmed_at": ("timestamp with time zone", "YES", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    checks = {
        f"ck_{table}_{name}"
        for name in (
            "group_iff_kind",
            "cohort_iff_kind",
            "series_original_start_together",
            "exception_needs_series",
            "kind_valid",
            "state_valid",
            "starts_before_ends",
            "whole_minutes",
            "duration_range",
            "scheduled_minutes_match",
            "actual_minutes_range",
            "unassigned_has_no_tutor",
            "assigned_has_tutor",
            "cancellation_iff_cancelled",
            "cancellation_reason_trimmed",
            "confirmed_has_first_confirmed_at",
        )
    }
    foreign_keys = {
        f"fk_{table}_subject_id_subjects",
        f"fk_{table}_tutor_user_id_users",
        f"fk_{table}_classroom_id_classrooms",
        f"fk_{table}_series_id_class_series",
        f"fk_{table}_group_id_class_groups",
        f"fk_{table}_cohort_id_course_cohorts",
    }
    assert set(constraints) == {
        f"pk_{table}",
        *checks,
        *foreign_keys,
        "ex_class_sessions_tutor_overlap",
        "ex_class_sessions_room_overlap",
        "uq_class_sessions_series_original_start",
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(fresh_database_url, table)) == {
        f"pk_{table}",
        f"ix_{table}_subject_id",
        f"ix_{table}_tutor_user_id",
        f"ix_{table}_classroom_id",
        f"ix_{table}_starts_at",
        f"ix_{table}_group_id_starts_at",
        f"ix_{table}_cohort_id_starts_at",
        # 0052 (#301 A1)
        OVERDUE_INDEX,
        "ex_class_sessions_tutor_overlap",
        "ex_class_sessions_room_overlap",
        "uq_class_sessions_series_original_start",
    }


def test_class_session_participants_columns_constraints_and_indexes(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "class_session_participants"

    assert _columns(fresh_database_url, table) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "class_session_id": ("uuid", "NO", None, None),
        "student_id": ("uuid", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        f"pk_{table}",
        f"fk_{table}_class_session_id_class_sessions",
        f"fk_{table}_student_id_students",
        "uq_class_session_participants_session_student",
    }
    assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_student_id_students"]
    assert (
        "ON DELETE RESTRICT"
        in (constraints[f"fk_{table}_class_session_id_class_sessions"])
    )
    assert constraints["uq_class_session_participants_session_student"] == (
        "UNIQUE (class_session_id, student_id)"
    )
    assert set(_indexes(fresh_database_url, table)) == {
        f"pk_{table}",
        f"ix_{table}_student_id",
        "uq_class_session_participants_session_student",
    }


# --- 0031: scheduling policy versions (#61 A) -----------------------------------------

POLICY_SELECT = (
    "SELECT effective_from, booking_notice_hours, change_notice_hours,"
    " response_hours, makeup_after_on_time, makeup_after_late,"
    " makeup_after_no_show, makeup_window_days FROM scheduling_policies"
)
POLICY_INSERT = (
    "INSERT INTO scheduling_policies (effective_from, booking_notice_hours,"
    " change_notice_hours, response_hours, makeup_after_on_time,"
    " makeup_after_late, makeup_after_no_show, makeup_window_days)"
    " VALUES (:effective_from, :booking_notice_hours, :change_notice_hours,"
    " :response_hours, :makeup_after_on_time, :makeup_after_late,"
    " :makeup_after_no_show, :makeup_window_days)"
)


def _policy(**changes: Any) -> Statement:
    values: dict[str, Any] = {
        "effective_from": datetime(2030, 1, 1, tzinfo=UTC),
        "booking_notice_hours": 24,
        "change_notice_hours": 24,
        "response_hours": 48,
        "makeup_after_on_time": True,
        "makeup_after_late": False,
        "makeup_after_no_show": False,
        "makeup_window_days": 30,
        **changes,
    }
    return (POLICY_INSERT, values)


def test_0031_adds_exactly_the_policy_table_and_downgrade_drops_it(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0030")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0031")

    assert public_tables(fresh_database_url) - before == set(POLICY_TABLES)

    command.downgrade(config, "0030")

    assert version_nums(fresh_database_url) == ["0030"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_scheduling_policies_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "scheduling_policies"

    assert _columns(fresh_database_url, table) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "effective_from": ("timestamp with time zone", "NO", None, None),
        "booking_notice_hours": ("smallint", "NO", None, None),
        "change_notice_hours": ("smallint", "NO", None, None),
        "response_hours": ("smallint", "NO", None, None),
        "makeup_after_on_time": ("boolean", "NO", None, None),
        "makeup_after_late": ("boolean", "NO", None, None),
        "makeup_after_no_show": ("boolean", "NO", None, None),
        "makeup_window_days": ("smallint", "NO", None, None),
    }
    assert set(_constraints(fresh_database_url, table)) == {
        "pk_scheduling_policies",
        "uq_scheduling_policies_effective_from",
        "ck_scheduling_policies_booking_notice_hours_range",
        "ck_scheduling_policies_change_notice_hours_range",
        "ck_scheduling_policies_response_hours_range",
        "ck_scheduling_policies_makeup_window_days_range",
    }
    assert set(_indexes(fresh_database_url, table)) == {
        "pk_scheduling_policies",
        "uq_scheduling_policies_effective_from",
    }


def test_0031_inserts_version_1_with_the_defaults(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    [row] = run_sql(fresh_database_url, POLICY_SELECT)
    assert tuple(row) == (
        datetime(2000, 1, 1, tzinfo=UTC),
        24,
        24,
        48,
        True,
        False,
        False,
        30,
    )
    # A raw insert: no audit event.
    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM audit_events")
    assert count.n == 0


POLICY_RANGES = [
    ("booking_notice_hours", 0, 336),
    ("change_notice_hours", 0, 336),
    ("response_hours", 1, 168),
    ("makeup_window_days", 1, 365),
]


@pytest.mark.parametrize(("column", "low", "high"), POLICY_RANGES)
def test_each_policy_range_accepts_both_bounds(fresh_database_url, column, low, high):
    command.upgrade(alembic_config(fresh_database_url), "head")

    run_sql(fresh_database_url, *_policy(**{column: low}))
    run_sql(
        fresh_database_url,
        *_policy(effective_from=datetime(2031, 1, 1, tzinfo=UTC), **{column: high}),
    )

    rows = run_sql(
        fresh_database_url,
        POLICY_SELECT + " WHERE effective_from > '2001-01-01' ORDER BY effective_from",
    )
    assert [getattr(row, column) for row in rows] == [low, high]


@pytest.mark.parametrize(("column", "low", "high"), POLICY_RANGES)
@pytest.mark.parametrize("beyond", ["low", "high"])
def test_each_policy_range_rejects_beyond_its_bounds(
    fresh_database_url, column, low, high, beyond
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    value = low - 1 if beyond == "low" else high + 1

    with pytest.raises(IntegrityError, match=f"ck_scheduling_policies_{column}_range"):
        run_sql(fresh_database_url, *_policy(**{column: value}))


def test_a_duplicate_effective_from_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(
        IntegrityError, match="uq_scheduling_policies_effective_from"
    ) as exc:
        run_sql(
            fresh_database_url,
            *_policy(effective_from=datetime(2000, 1, 1, tzinfo=UTC)),
        )
    assert _sqlstate(exc.value) == "23505"


# --- 0032: course templates (#64 B) ---------------------------------------------------

COURSE_SUBJECT_ID = uuid.UUID("01900000-0000-7000-8000-00000000c001")


def _course_template(dsn: str, name: str = "Curso", **values: Any) -> uuid.UUID:
    columns: dict[str, Any] = {
        "name": name,
        "session_count": 10,
        "session_minutes": 90,
        "min_attendance_percent": None,
        "is_active": True,
        **values,
    }
    [row] = run_sql(
        dsn,
        "INSERT INTO course_templates (name, session_count, session_minutes,"
        " min_attendance_percent, is_active) VALUES (:name, :session_count,"
        " :session_minutes, :min_attendance_percent, :is_active) RETURNING id",
        columns,
    )
    template_id: uuid.UUID = row.id
    return template_id


def _in_one_transaction(dsn: str, *statements: Statement) -> None:
    async def _run() -> None:
        engine = short_lived_engine(dsn)
        try:
            async with engine.begin() as conn:
                for sql, params in statements:
                    await conn.execute(text(sql), params)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _course_module(template_id: uuid.UUID, position: int, title: str) -> Statement:
    return (
        "INSERT INTO course_template_modules (template_id, position, title)"
        " VALUES (:template_id, :position, :title)",
        {"template_id": template_id, "position": position, "title": title},
    )


def test_0032_adds_exactly_the_course_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0031")
    before = public_tables(fresh_database_url)

    command.upgrade(config, "0032")

    assert public_tables(fresh_database_url) - before == set(COURSE_TABLES)
    # Only the tables of later revisions (0033 class_session_overrides, 0034
    # the offer tables, 0035 the series) differ.
    assert [
        diff
        for diff in metadata_diff(fresh_database_url)
        if not _of_later_revision(
            diff, [*OVERRIDE_TABLES, *OFFER_TABLES, *SERIES_TABLES]
        )
    ] == []
    [trigger] = run_sql(
        fresh_database_url,
        "SELECT count(*) AS n FROM pg_trigger"
        " WHERE tgname = 'course_template_snapshots_immutable'",
    )
    assert trigger.n == 1

    command.downgrade(config, "0031")

    assert version_nums(fresh_database_url) == ["0031"]
    assert public_tables(fresh_database_url) == before
    [function] = run_sql(
        fresh_database_url,
        "SELECT count(*) AS n FROM pg_proc"
        " WHERE proname = 'course_template_snapshots_immutable'",
    )
    assert function.n == 0
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_course_templates_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "course_templates"

    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "name": ("character varying", "NO", 100, None),
        "description": ("character varying", "YES", 2000, None),
        "session_count": ("smallint", "NO", None, None),
        "session_minutes": ("smallint", "NO", None, None),
        "min_attendance_percent": ("smallint", "YES", None, None),
        "is_active": ("boolean", "NO", None, "true"),
    }
    assert set(_constraints(fresh_database_url, table)) == {
        "pk_course_templates",
        "ck_course_templates_name_trimmed",
        "ck_course_templates_name_not_blank",
        "ck_course_templates_session_count_range",
        "ck_course_templates_session_minutes_range",
        "ck_course_templates_min_attendance_percent_range",
    }
    assert set(_indexes(fresh_database_url, table)) == {
        "pk_course_templates",
        "uq_course_templates_name_ci",
    }


def test_course_template_child_tables_constraints(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    modules = _constraints(fresh_database_url, "course_template_modules")
    assert set(modules) == {
        "pk_course_template_modules",
        "fk_course_template_modules_template_id_course_templates",
        "fk_course_template_modules_subject_id_subjects",
        "uq_course_template_modules_template_position",
        "ck_course_template_modules_position_range",
        "ck_course_template_modules_title_trimmed",
        "ck_course_template_modules_title_not_blank",
        "ck_course_template_modules_planned_sessions_range",
    }
    assert modules["uq_course_template_modules_template_position"] == (
        'UNIQUE (template_id, "position") DEFERRABLE INITIALLY DEFERRED'
    )
    assessments = _constraints(fresh_database_url, "course_template_assessments")
    assert assessments["uq_course_template_assessments_template_position"] == (
        'UNIQUE (template_id, "position") DEFERRABLE INITIALLY DEFERRED'
    )
    assert (
        "ON DELETE RESTRICT"
        in assessments["fk_course_template_assessments_module_id_modules"]
    )
    assert set(_constraints(fresh_database_url, "course_template_subjects")) == {
        "pk_course_template_subjects",
        "fk_course_template_subjects_template_id_course_templates",
        "fk_course_template_subjects_subject_id_subjects",
        "uq_course_template_subjects_template_subject",
    }
    assert set(_constraints(fresh_database_url, "course_template_snapshots")) == {
        "pk_course_template_snapshots",
        "fk_course_template_snapshots_template_id_course_templates",
        "uq_course_template_snapshots_template_version",
    }
    assert "ix_course_template_subjects_subject_id" in _indexes(
        fresh_database_url, "course_template_subjects"
    )
    for child in ("course_template_modules", "course_template_assessments"):
        assert f"ix_{child}_template_id" in _indexes(fresh_database_url, child)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("session_count", 0),
        ("session_count", 201),
        ("session_minutes", 14),
        ("session_minutes", 481),
        ("min_attendance_percent", 0),
        ("min_attendance_percent", 101),
    ],
)
def test_course_template_ranges_are_checked(fresh_database_url, column, value):
    command.upgrade(alembic_config(fresh_database_url), "head")

    with pytest.raises(IntegrityError, match=f"ck_course_templates_{column}_range"):
        _course_template(fresh_database_url, **{column: value})


def test_course_template_bounds_and_null_attendance_are_accepted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    _course_template(
        fresh_database_url,
        "Uno",
        session_count=1,
        session_minutes=15,
        min_attendance_percent=1,
    )
    _course_template(
        fresh_database_url,
        "Dos",
        session_count=200,
        session_minutes=480,
        min_attendance_percent=100,
    )
    _course_template(fresh_database_url, "Tres", min_attendance_percent=None)

    [count] = run_sql(fresh_database_url, "SELECT count(*) AS n FROM course_templates")
    assert count.n == 3


def test_a_course_template_name_in_another_case_is_rejected(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    _course_template(fresh_database_url, "Curso de Verano", is_active=False)

    with pytest.raises(IntegrityError, match="uq_course_templates_name_ci"):
        _course_template(fresh_database_url, "CURSO DE VERANO")


def test_two_modules_at_one_position_are_rejected_at_commit(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    template_id = _course_template(fresh_database_url)

    with pytest.raises(
        IntegrityError, match="uq_course_template_modules_template_position"
    ):
        _in_one_transaction(
            fresh_database_url,
            _course_module(template_id, 1, "Uno"),
            _course_module(template_id, 1, "Dos"),
        )

    [count] = run_sql(
        fresh_database_url, "SELECT count(*) AS n FROM course_template_modules"
    )
    assert count.n == 0


def test_swapping_two_module_positions_in_one_transaction_is_accepted(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    template_id = _course_template(fresh_database_url)
    run_sql(fresh_database_url, *_course_module(template_id, 1, "Uno"))
    run_sql(fresh_database_url, *_course_module(template_id, 2, "Dos"))
    move = (
        "UPDATE course_template_modules SET position = :position"
        " WHERE template_id = :template_id AND title = :title"
    )

    _in_one_transaction(
        fresh_database_url,
        (move, {"position": 2, "template_id": template_id, "title": "Uno"}),
        (move, {"position": 1, "template_id": template_id, "title": "Dos"}),
    )

    rows = run_sql(
        fresh_database_url,
        "SELECT title FROM course_template_modules ORDER BY position",
    )
    assert [row.title for row in rows] == ["Dos", "Uno"]


def test_a_snapshot_row_can_be_neither_updated_nor_deleted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    template_id = _course_template(fresh_database_url)
    [snapshot] = run_sql(
        fresh_database_url,
        "INSERT INTO course_template_snapshots (template_id, template_version, content)"
        ' VALUES (:template_id, 1, \'{"name": "Curso"}\') RETURNING id, schema_version',
        {"template_id": template_id},
    )
    assert snapshot.schema_version == 1

    for statement in (
        "UPDATE course_template_snapshots SET template_version = 2 WHERE id = :id",
        "DELETE FROM course_template_snapshots WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="rows never change") as exc:
            run_sql(fresh_database_url, statement, {"id": snapshot.id})
        assert _sqlstate(exc.value) == "42501"

    with pytest.raises(
        IntegrityError, match="uq_course_template_snapshots_template_version"
    ):
        run_sql(
            fresh_database_url,
            "INSERT INTO course_template_snapshots"
            " (template_id, template_version, content)"
            " VALUES (:template_id, 1, '{}')",
            {"template_id": template_id},
        )


# --- 0033: session overrides (#49 D1) ---------------------------------------------

OVERRIDES_TRIGGER = (
    "SELECT tgname FROM pg_trigger"
    " WHERE tgrelid = 'class_session_overrides'::regclass AND NOT tgisinternal"
)
OVERRIDES_FUNCTION = (
    "SELECT proname FROM pg_proc WHERE proname = 'class_session_overrides_immutable'"
)


def test_0033_adds_exactly_the_override_table_and_downgrade_drops_it(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0032")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0033")

    assert public_tables(fresh_database_url) - before == set(OVERRIDE_TABLES)
    # Only the objects of later revisions (0034 the offers, 0035 the series)
    # differ.
    assert [
        diff
        for diff in metadata_diff(fresh_database_url)
        if not _of_later_revision(diff, [*OFFER_TABLES, *SERIES_TABLES])
    ] == []
    assert [r.tgname for r in run_sql(fresh_database_url, OVERRIDES_TRIGGER)] == [
        "class_session_overrides_immutable"
    ]
    assert len(run_sql(fresh_database_url, OVERRIDES_FUNCTION)) == 1

    command.downgrade(config, "0032")

    assert version_nums(fresh_database_url) == ["0032"]
    assert public_tables(fresh_database_url) == before
    assert run_sql(fresh_database_url, OVERRIDES_FUNCTION) == []
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_class_session_overrides_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "class_session_overrides"

    assert _columns(fresh_database_url, table) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        # 0041 (#56 A6): nullable, one of the two subjects.
        "class_session_id": ("uuid", "YES", None, None),
        "codes": ("ARRAY", "NO", None, None),
        "conflicts": ("jsonb", "NO", None, None),
        "reason": ("text", "NO", None, None),
        "actor_user_id": ("uuid", "NO", None, None),
        # 0039 (#236 D1)
        "class_series_id": ("uuid", "YES", None, None),
        # 0041 (#56 A6)
        "group_enrollment_id": ("uuid", "YES", None, None),
        # 0047 (#66 A3)
        "cohort_enrollment_id": ("uuid", "YES", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    # The naming convention shortens it to PostgreSQL's 63 characters.
    enrollment_fk = "fk_class_session_overrides_group_enrollment_id_group_en_59d0"
    assert set(constraints) == {
        "pk_class_session_overrides",
        "fk_class_session_overrides_class_session_id_class_sessions",
        "fk_class_session_overrides_actor_user_id_users",
        OVERRIDE_CONTEXT_FK,
        enrollment_fk,
        COHORT_ENROLLMENT_FK,
        "ck_class_session_overrides_codes_soft",
        "ck_class_session_overrides_reason_length",
        "ck_class_session_overrides_one_subject",
    }
    for name in (
        "fk_class_session_overrides_class_session_id_class_sessions",
        "fk_class_session_overrides_actor_user_id_users",
        OVERRIDE_CONTEXT_FK,
        enrollment_fk,
        COHORT_ENROLLMENT_FK,
    ):
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(fresh_database_url, table)) == {
        "pk_class_session_overrides",
        "ix_class_session_overrides_class_session_id",
        OVERRIDE_CONTEXT_INDEX,
        "ix_class_session_overrides_group_enrollment_id",
        COHORT_ENROLLMENT_INDEX,
    }


# --- 0034: assignment offers (#52 A) ----------------------------------------------


def test_0034_adds_exactly_the_offer_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0033")
    before = public_tables(fresh_database_url)
    command.upgrade(config, "0034")

    assert public_tables(fresh_database_url) - before == set(OFFER_TABLES)
    # Only the objects of a later revision (0035 the series) differ.
    assert [
        diff
        for diff in metadata_diff(fresh_database_url)
        if not _of_later_revision(diff, SERIES_TABLES)
    ] == []

    command.downgrade(config, "0033")

    assert version_nums(fresh_database_url) == ["0033"]
    assert public_tables(fresh_database_url) == before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_assignment_offers_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "assignment_offers"

    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "scope": ("character varying", "NO", 20, None),
        "tutor_user_id": ("uuid", "NO", None, None),
        "status": ("character varying", "NO", 20, "'pending'::character varying"),
        "offered_at": ("timestamp with time zone", "NO", None, None),
        "offered_by_user_id": ("uuid", "NO", None, None),
        "respond_by": ("timestamp with time zone", "NO", None, None),
        "responded_at": ("timestamp with time zone", "YES", None, None),
        "decline_note": ("character varying", "YES", 500, None),
        # 0036 (#233 A1)
        "class_series_id": ("uuid", "YES", None, None),
        "from_date": ("date", "YES", None, None),
        # 0040 (#55 A1)
        "kind": ("character varying", "NO", 20, "'assignment'::character varying"),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        "ck_assignment_offers_kind_valid",
        "ck_assignment_offers_substitution_session_scope",
        "pk_assignment_offers",
        "fk_assignment_offers_tutor_user_id_users",
        "fk_assignment_offers_offered_by_user_id_users",
        "fk_assignment_offers_class_series_id_class_series",
        "ck_assignment_offers_scope_valid",
        "ck_assignment_offers_status_valid",
        "ck_assignment_offers_respond_by_after_offered",
        "ck_assignment_offers_responded_iff_answered",
        "ck_assignment_offers_decline_note_only_declined",
        "ck_assignment_offers_series_iff_series_id",
        "ck_assignment_offers_series_iff_from_date",
    }
    for name in (
        "fk_assignment_offers_tutor_user_id_users",
        "fk_assignment_offers_offered_by_user_id_users",
        "fk_assignment_offers_class_series_id_class_series",
    ):
        assert "ON DELETE RESTRICT" in constraints[name]
    assert "'series'" in constraints["ck_assignment_offers_scope_valid"]
    indexes = _indexes(fresh_database_url, table)
    assert set(indexes) == {
        "pk_assignment_offers",
        "ix_assignment_offers_tutor_user_id_status",
        "ix_assignment_offers_pending_respond_by",
        "ix_assignment_offers_class_series_id",
        "uq_assignment_offers_pending_series",
    }
    for name in (
        "ix_assignment_offers_pending_respond_by",
        "uq_assignment_offers_pending_series",
    ):
        assert "WHERE ((status)::text = 'pending'::text)" in indexes[name]
    assert "UNIQUE" in indexes["uq_assignment_offers_pending_series"]

    sessions = "assignment_offer_sessions"
    assert set(_constraints(fresh_database_url, sessions)) == {
        "pk_assignment_offer_sessions",
        "fk_assignment_offer_sessions_offer_id_assignment_offers",
        "fk_assignment_offer_sessions_class_session_id_class_sessions",
        "uq_assignment_offer_sessions_offer_session",
    }
    assert set(_indexes(fresh_database_url, sessions)) == {
        "pk_assignment_offer_sessions",
        "uq_assignment_offer_sessions_offer_session",
        "ix_assignment_offer_sessions_class_session_id",
    }


def _offer_refs(dsn: str) -> tuple[Any, Any]:
    """A user (tutor and offerer) and a class session to cover."""
    [user] = run_sql(
        dsn,
        "INSERT INTO users (username, display_name, state)"
        " VALUES ('offer.tutor', 'Tutora', 'active') RETURNING id",
    )
    [level] = run_sql(
        dsn,
        "INSERT INTO education_levels (name, sort_order) VALUES ('Primaria', 1)"
        " RETURNING id",
    )
    [subject] = run_sql(
        dsn,
        "INSERT INTO subjects (name, education_level_id) VALUES ('Mate', :l)"
        " RETURNING id",
        {"l": level.id},
    )
    [session] = run_sql(
        dsn,
        "INSERT INTO class_sessions (kind, state, subject_id, starts_at, ends_at,"
        " scheduled_minutes) VALUES ('individual', 'unassigned', :s,"
        " '2031-01-06 16:00+00', '2031-01-06 17:00+00', 60) RETURNING id",
        {"s": subject.id},
    )
    return user.id, session.id


def _insert_offer(dsn: str, user_id: Any, **columns: Any) -> Any:
    values = {
        "scope": "session",
        "status": "pending",
        "offered_at": datetime(2031, 1, 1, 12, tzinfo=UTC),
        "respond_by": datetime(2031, 1, 3, 12, tzinfo=UTC),
        "responded_at": None,
        "decline_note": None,
        "class_series_id": None,
        "from_date": None,
    } | columns
    [row] = run_sql(
        dsn,
        "INSERT INTO assignment_offers (scope, tutor_user_id, status, offered_at,"
        " offered_by_user_id, respond_by, responded_at, decline_note,"
        " class_series_id, from_date)"
        " VALUES (:scope, :u, :status, :offered_at, :u,"
        " :respond_by, :responded_at,"
        " :decline_note, :class_series_id, :from_date) RETURNING id",
        {"u": user_id, **values},
    )
    return row.id


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        (
            {"responded_at": datetime(2031, 1, 2, 12, tzinfo=UTC)},
            "responded_iff_answered",
        ),
        ({"status": "declined"}, "responded_iff_answered"),
        (
            {"respond_by": datetime(2031, 1, 1, 12, tzinfo=UTC)},
            "respond_by_after_offered",
        ),
        (
            {
                "status": "accepted",
                "responded_at": datetime(2031, 1, 2, 12, tzinfo=UTC),
                "decline_note": "No",
            },
            "decline_note_only_declined",
        ),
        ({"scope": "other"}, "scope_valid"),
        (
            {"status": "draft", "responded_at": datetime(2031, 1, 2, 12, tzinfo=UTC)},
            "status_valid",
        ),
    ],
)
def test_assignment_offer_checks(fresh_database_url, columns, constraint):
    command.upgrade(alembic_config(fresh_database_url), "head")
    user_id, _ = _offer_refs(fresh_database_url)

    with pytest.raises(IntegrityError, match=f"ck_assignment_offers_{constraint}"):
        _insert_offer(fresh_database_url, user_id, **columns)


def test_assignment_offer_valid_rows_and_a_duplicate_session_row(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    user_id, session_id = _offer_refs(fresh_database_url)
    pending = _insert_offer(fresh_database_url, user_id)
    declined = _insert_offer(
        fresh_database_url,
        user_id,
        status="declined",
        responded_at=datetime(2031, 1, 2, 12, tzinfo=UTC),
        decline_note="Viajo",
    )
    insert = (
        "INSERT INTO assignment_offer_sessions (offer_id, class_session_id)"
        " VALUES (:o, :s)"
    )
    run_sql(fresh_database_url, insert, {"o": pending, "s": session_id})
    run_sql(fresh_database_url, insert, {"o": declined, "s": session_id})

    with pytest.raises(
        IntegrityError, match="uq_assignment_offer_sessions_offer_session"
    ):
        run_sql(fresh_database_url, insert, {"o": pending, "s": session_id})


# --- 0035: class series (#53 B) -------------------------------------------------------

SERIES_INSERT = (
    "INSERT INTO class_series (kind, subject_id, starts_on, start_time,"
    " duration_minutes, time_zone, weekdays, interval, until_on, count, rrule)"
    " VALUES (:kind, :subject_id, :starts_on, :start_time, :duration_minutes,"
    " :time_zone, :weekdays, :interval, :until_on, :count, :rrule) RETURNING id"
)


def _subject_row(dsn: str) -> uuid.UUID:
    [level] = run_sql(
        dsn,
        "INSERT INTO education_levels (name, sort_order) VALUES (:n, 1) RETURNING id",
        {"n": f"Nivel {uuid.uuid4().hex[:8]}"},
    )
    [subject] = run_sql(
        dsn,
        "INSERT INTO subjects (education_level_id, name) VALUES (:l, 'Materia')"
        " RETURNING id",
        {"l": level.id},
    )
    return uuid.UUID(str(subject.id))


def _series(dsn: str, subject_id: uuid.UUID, **values: Any) -> uuid.UUID:
    columns: dict[str, Any] = {
        "kind": "individual",
        "subject_id": subject_id,
        "starts_on": date(2031, 1, 6),
        "start_time": time(16),
        "duration_minutes": 60,
        "time_zone": "America/Mexico_City",
        "weekdays": [1, 3],
        "interval": 1,
        "until_on": None,
        "count": None,
        "rrule": "FREQ=WEEKLY;INTERVAL=1;WKST=MO;BYDAY=MO,WE",
        **values,
    }
    [row] = run_sql(dsn, SERIES_INSERT, columns)
    return uuid.UUID(str(row.id))


def _session(dsn: str, subject_id: uuid.UUID, **values: Any) -> None:
    starts_at = datetime(2031, 1, 6, 22, tzinfo=UTC)
    columns: dict[str, Any] = {
        "subject_id": subject_id,
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(hours=1),
        "series_id": None,
        "original_start": None,
        "is_exception": False,
        **values,
    }
    run_sql(
        dsn,
        "INSERT INTO class_sessions (kind, state, subject_id, starts_at, ends_at,"
        " scheduled_minutes, series_id, original_start, is_exception) VALUES"
        " ('individual', 'unassigned', :subject_id, :starts_at, :ends_at, 60,"
        " :series_id, :original_start, :is_exception)",
        columns,
    )


def test_0035_adds_the_series_tables_and_columns_and_downgrade_drops_them(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0034")
    before = public_tables(fresh_database_url)
    columns_before = set(_columns(fresh_database_url, "class_sessions"))
    # An existing session gets the defaults.
    subject = _subject_row(fresh_database_url)
    run_sql(
        fresh_database_url,
        "INSERT INTO class_sessions (kind, state, subject_id, starts_at, ends_at,"
        " scheduled_minutes) VALUES ('individual', 'unassigned', :s,"
        " '2031-01-06T22:00:00Z', '2031-01-06T23:00:00Z', 60)",
        {"s": subject},
    )

    command.upgrade(config, "0035")

    assert public_tables(fresh_database_url) - before == set(SERIES_TABLES)
    assert set(_columns(fresh_database_url, "class_sessions")) - columns_before == set(
        SERIES_SESSION_COLUMNS
    )
    # Only 0036's and 0038's objects differ.
    assert [
        d
        for d in metadata_diff(fresh_database_url)
        if not (
            _of_0036(d)
            or _of_0038(d)
            or _of_0039(d)
            or _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    [row] = run_sql(
        fresh_database_url,
        "SELECT series_id, original_start, is_exception, conflict_codes"
        " FROM class_sessions",
    )
    assert (row.series_id, row.original_start, row.is_exception) == (
        None,
        None,
        False,
    )
    assert list(row.conflict_codes) == []

    command.downgrade(config, "0034")

    assert version_nums(fresh_database_url) == ["0034"]
    assert public_tables(fresh_database_url) == before
    assert set(_columns(fresh_database_url, "class_sessions")) == columns_before
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_class_series_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "class_series"

    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "kind": ("character varying", "NO", 20, None),
        "subject_id": ("uuid", "NO", None, None),
        "tutor_user_id": ("uuid", "YES", None, None),
        "classroom_id": ("uuid", "YES", None, None),
        "starts_on": ("date", "NO", None, None),
        "start_time": ("time without time zone", "NO", None, None),
        "duration_minutes": ("smallint", "NO", None, None),
        "time_zone": ("character varying", "NO", 64, None),
        "weekdays": ("ARRAY", "NO", None, None),
        "interval": ("smallint", "NO", None, None),
        "until_on": ("date", "YES", None, None),
        "count": ("smallint", "YES", None, None),
        "rrule": ("text", "NO", None, None),
        "last_occurrence_on": ("date", "YES", None, None),
        # 0036 (#233 A2)
        "assignment_status": ("character varying", "YES", 20, None),
        "assignment_from": ("date", "YES", None, None),
        # 0038 (#54 A2)
        "split_from_series_id": ("uuid", "YES", None, None),
        # 0041 (#56 A3)
        "group_id": ("uuid", "YES", None, None),
        # 0046 (#65 A3)
        "cohort_id": ("uuid", "YES", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    foreign_keys = {
        f"fk_{table}_subject_id_subjects",
        f"fk_{table}_tutor_user_id_users",
        f"fk_{table}_classroom_id_classrooms",
        f"fk_{table}_group_id_class_groups",
        f"fk_{table}_cohort_id_course_cohorts",
        LINEAGE_FK,
    }
    assert set(constraints) == {
        f"pk_{table}",
        *foreign_keys,
        *(
            f"ck_{table}_{name}"
            for name in (
                "kind_valid",
                "start_time_whole_minutes",
                "duration_range",
                "weekdays_valid",
                "interval_range",
                "count_range",
                "until_or_count",
                "assignment_status_valid",
                "assignment_iff_tutor",
                "assignment_from_iff_status",
                "split_from_not_self",
                "group_iff_kind",
                "cohort_iff_kind",
            )
        ),
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(fresh_database_url, table)) == {
        f"pk_{table}",
        f"ix_{table}_subject_id",
        f"ix_{table}_tutor_user_id",
        f"ix_{table}_classroom_id",
        f"ix_{table}_group_id",
        f"ix_{table}_cohort_id",
        LINEAGE_INDEX,
    }

    participants = "class_series_participants"
    assert _columns(fresh_database_url, participants) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "class_series_id": ("uuid", "NO", None, None),
        "student_id": ("uuid", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, participants)
    assert set(constraints) == {
        f"pk_{participants}",
        f"fk_{participants}_class_series_id_class_series",
        f"fk_{participants}_student_id_students",
        "uq_class_series_participants_series_student",
    }
    assert constraints["uq_class_series_participants_series_student"] == (
        "UNIQUE (class_series_id, student_id)"
    )
    assert set(_indexes(fresh_database_url, participants)) == {
        f"pk_{participants}",
        f"ix_{participants}_student_id",
        "uq_class_series_participants_series_student",
    }


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"kind": "other"}, "ck_class_series_kind_valid"),
        ({"start_time": time(16, 0, 30)}, "ck_class_series_start_time_whole_minutes"),
        ({"duration_minutes": 14}, "ck_class_series_duration_range"),
        ({"duration_minutes": 481}, "ck_class_series_duration_range"),
        ({"weekdays": []}, "ck_class_series_weekdays_valid"),
        ({"weekdays": [0]}, "ck_class_series_weekdays_valid"),
        ({"weekdays": [8]}, "ck_class_series_weekdays_valid"),
        ({"interval": 0}, "ck_class_series_interval_range"),
        ({"interval": 5}, "ck_class_series_interval_range"),
        ({"count": 0}, "ck_class_series_count_range"),
        ({"count": 201}, "ck_class_series_count_range"),
        (
            {"count": 3, "until_on": date(2031, 2, 1)},
            "ck_class_series_until_or_count",
        ),
    ],
)
def test_invalid_class_series_rows_are_rejected(fresh_database_url, values, constraint):
    command.upgrade(alembic_config(fresh_database_url), "head")
    subject = _subject_row(fresh_database_url)

    with pytest.raises(IntegrityError, match=constraint):
        _series(fresh_database_url, subject, **values)


def test_valid_class_series_bounds_are_accepted(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    subject = _subject_row(fresh_database_url)

    _series(fresh_database_url, subject, duration_minutes=15, interval=4, count=1)
    _series(
        fresh_database_url,
        subject,
        duration_minutes=480,
        weekdays=[1, 2, 3, 4, 5, 6, 7],
        count=200,
    )
    _series(fresh_database_url, subject, until_on=date(2031, 6, 30))


def test_the_occurrence_columns_are_checked(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    subject = _subject_row(fresh_database_url)
    series = _series(fresh_database_url, subject)
    start = datetime(2031, 1, 6, 22, tzinfo=UTC)

    with pytest.raises(IntegrityError, match="series_original_start_together"):
        _session(fresh_database_url, subject, series_id=series)
    with pytest.raises(IntegrityError, match="series_original_start_together"):
        _session(fresh_database_url, subject, original_start=start)
    with pytest.raises(IntegrityError, match="exception_needs_series"):
        _session(fresh_database_url, subject, is_exception=True)
    _session(fresh_database_url, subject, series_id=series, original_start=start)
    with pytest.raises(
        IntegrityError, match="uq_class_sessions_series_original_start"
    ) as exc:
        _session(fresh_database_url, subject, series_id=series, original_start=start)
    assert _sqlstate(exc.value) == "23505"
    # An exception of a series is fine.
    _session(
        fresh_database_url,
        subject,
        series_id=series,
        original_start=start + timedelta(days=2),
        is_exception=True,
    )


# --- 0036: series offers (#233 A) -----------------------------------------------------


def _user_row(dsn: str) -> uuid.UUID:
    [user] = run_sql(
        dsn,
        "INSERT INTO users (username, display_name, state)"
        " VALUES (:u, 'Tutora', 'active') RETURNING id",
        {"u": f"series.tutor.{uuid.uuid4().hex[:8]}"},
    )
    return uuid.UUID(str(user.id))


def test_0036_backfills_series_and_downgrade_removes_series_offers(
    fresh_database_url,
):
    config = alembic_config(fresh_database_url)
    command.upgrade(config, "0035")
    subject = _subject_row(fresh_database_url)
    tutor = _user_row(fresh_database_url)
    with_tutor = _series(fresh_database_url, subject, tutor_user_id=None)
    run_sql(
        fresh_database_url,
        "UPDATE class_series SET tutor_user_id = :t WHERE id = :id",
        {"t": tutor, "id": with_tutor},
    )
    without_tutor = _series(fresh_database_url, subject)
    offers_before = set(_columns(fresh_database_url, "assignment_offers"))
    series_before = set(_columns(fresh_database_url, "class_series"))

    command.upgrade(config, "0036")

    assert [
        d
        for d in metadata_diff(fresh_database_url)
        if not (
            _of_0038(d)
            or _of_0039(d)
            or _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    for table, added in SERIES_OFFER_COLUMNS.items():
        before = offers_before if table == "assignment_offers" else series_before
        assert set(_columns(fresh_database_url, table)) - before == added
    rows = {
        row.id: (row.assignment_status, row.assignment_from)
        for row in run_sql(
            fresh_database_url,
            "SELECT id, assignment_status, assignment_from FROM class_series",
        )
    }
    assert rows == {
        with_tutor: ("pending", date(2031, 1, 6)),
        without_tutor: (None, None),
    }
    # A series offer and its covered row, then the downgrade deletes both.
    offer = _insert_offer(
        fresh_database_url,
        tutor,
        scope="series",
        class_series_id=with_tutor,
        from_date=date(2031, 1, 6),
    )
    _, session_id = _offer_refs(fresh_database_url)
    run_sql(
        fresh_database_url,
        "INSERT INTO assignment_offer_sessions (offer_id, class_session_id)"
        " VALUES (:o, :s)",
        {"o": offer, "s": session_id},
    )

    command.downgrade(config, "0035")

    assert version_nums(fresh_database_url) == ["0035"]
    assert set(_columns(fresh_database_url, "assignment_offers")) == offers_before
    assert set(_columns(fresh_database_url, "class_series")) == series_before
    [left] = run_sql(
        fresh_database_url,
        "SELECT (SELECT count(*) FROM assignment_offers) AS offers,"
        " (SELECT count(*) FROM assignment_offer_sessions) AS rows",
    )
    assert (left.offers, left.rows) == (0, 0)
    with pytest.raises(IntegrityError, match="ck_assignment_offers_scope_valid"):
        run_sql(
            fresh_database_url,
            "INSERT INTO assignment_offers (scope, tutor_user_id, offered_at,"
            " offered_by_user_id, respond_by) VALUES ('series', :u,"
            " '2031-01-01T12:00:00Z', :u, '2031-01-03T12:00:00Z')",
            {"u": tutor},
        )
    command.upgrade(config, "head")
    assert version_nums(fresh_database_url) == [HEAD]


def test_series_offer_rows_are_checked(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    subject = _subject_row(fresh_database_url)
    tutor, _ = _offer_refs(fresh_database_url)
    series = _series(fresh_database_url, subject)
    monday = date(2031, 1, 6)

    with pytest.raises(IntegrityError, match="series_iff_series_id"):
        _insert_offer(fresh_database_url, tutor, scope="series", from_date=monday)
    with pytest.raises(IntegrityError, match="series_iff_from_date"):
        _insert_offer(fresh_database_url, tutor, scope="series", class_series_id=series)
    with pytest.raises(IntegrityError, match="series_iff_series_id"):
        _insert_offer(fresh_database_url, tutor, class_series_id=series)
    with pytest.raises(IntegrityError, match="series_iff_from_date"):
        _insert_offer(fresh_database_url, tutor, from_date=monday)
    series_offer = {"scope": "series", "class_series_id": series, "from_date": monday}
    _insert_offer(fresh_database_url, tutor, **series_offer)
    with pytest.raises(
        IntegrityError, match="uq_assignment_offers_pending_series"
    ) as exc:
        _insert_offer(fresh_database_url, tutor, **series_offer)
    assert _sqlstate(exc.value) == "23505"
    # Answered series offers don't count.
    _insert_offer(
        fresh_database_url,
        tutor,
        status="declined",
        responded_at=datetime(2031, 1, 2, 12, tzinfo=UTC),
        **series_offer,
    )


def test_the_series_assignment_is_tied_to_the_tutor(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    subject = _subject_row(fresh_database_url)
    tutor = _user_row(fresh_database_url)
    monday = date(2031, 1, 6)
    insert = (
        "INSERT INTO class_series (kind, subject_id, tutor_user_id, starts_on,"
        " start_time, duration_minutes, time_zone, weekdays, interval, rrule,"
        " assignment_status, assignment_from) VALUES ('individual', :s, :t,"
        " :d, '16:00', 60, 'America/Mexico_City', '{1}', 1, 'FREQ=WEEKLY',"
        " :status, :from_date)"
    )

    def insert_series(tutor_id: Any, status: Any, from_date: Any) -> None:
        run_sql(
            fresh_database_url,
            insert,
            {
                "s": subject,
                "t": tutor_id,
                "d": monday,
                "status": status,
                "from_date": from_date,
            },
        )

    with pytest.raises(IntegrityError, match="assignment_iff_tutor"):
        insert_series(tutor, None, None)
    with pytest.raises(IntegrityError, match="assignment_iff_tutor"):
        insert_series(None, "accepted", monday)
    with pytest.raises(IntegrityError, match="assignment_from_iff_status"):
        insert_series(tutor, "pending", None)
    with pytest.raises(IntegrityError, match="assignment_status_valid"):
        insert_series(tutor, "declined", monday)
    insert_series(tutor, "pending", monday)
    insert_series(tutor, "accepted", monday)
    insert_series(None, None, None)


# --- 0037: clear the series assignments 0036 backfilled (#263 C) ----------------------

BROKEN_PENDING_SERIES = (
    "SELECT s.id FROM class_series s WHERE s.assignment_status = 'pending'"
    " AND NOT EXISTS (SELECT 1 FROM assignment_offers o WHERE o.scope = 'series'"
    " AND o.status = 'pending' AND o.class_series_id = s.id"
    " AND o.tutor_user_id = s.tutor_user_id)"
)
"""#233 A3: a pending series has a pending series offer of its tutor."""
UNCOVERED_OFFERED_SESSIONS = (
    "SELECT cs.id FROM class_sessions cs WHERE cs.state = 'offered'"
    " AND NOT EXISTS (SELECT 1 FROM assignment_offer_sessions aos"
    " JOIN assignment_offers o ON o.id = aos.offer_id"
    " WHERE aos.class_session_id = cs.id AND o.status = 'pending')"
)
"""#233 A3: a pending offer covers every offered session."""


def _occurrence(
    dsn: str,
    subject_id: uuid.UUID,
    series_id: uuid.UUID,
    starts_at: datetime,
    state: str,
    tutor_id: uuid.UUID | None,
) -> uuid.UUID:
    [row] = run_sql(
        dsn,
        "INSERT INTO class_sessions (kind, state, subject_id, tutor_user_id,"
        " starts_at, ends_at, scheduled_minutes, series_id, original_start)"
        " VALUES ('individual', :state, :subject, :tutor, :starts_at, :ends_at, 60,"
        " :series, :starts_at) RETURNING id",
        {
            "state": state,
            "subject": subject_id,
            "tutor": tutor_id,
            "starts_at": starts_at,
            "ends_at": starts_at + timedelta(hours=1),
            "series": series_id,
        },
    )
    return uuid.UUID(str(row.id))


def _cover(dsn: str, offer_id: Any, session_id: uuid.UUID) -> None:
    run_sql(
        dsn,
        "INSERT INTO assignment_offer_sessions (offer_id, class_session_id)"
        " VALUES (:o, :s)",
        {"o": offer_id, "s": session_id},
    )


def _session_rows(dsn: str) -> dict[uuid.UUID, tuple[Any, ...]]:
    return {
        uuid.UUID(str(row.id)): (row.state, row.tutor_user_id, row.version)
        for row in run_sql(
            dsn, "SELECT id, state, tutor_user_id, version FROM class_sessions"
        )
    }


def _series_rows(dsn: str) -> dict[uuid.UUID, tuple[Any, ...]]:
    return {
        uuid.UUID(str(row.id)): (
            row.tutor_user_id,
            row.assignment_status,
            row.assignment_from,
            row.version,
        )
        for row in run_sql(
            dsn,
            "SELECT id, tutor_user_id, assignment_status, assignment_from, version"
            " FROM class_series",
        )
    }


def test_0037_clears_the_backfilled_series_assignments(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0035")
    subject = _subject_row(dsn)
    tutor = _user_row(dsn)
    monday = datetime(2031, 1, 6, 22, tzinfo=UTC)
    # S1: a #53 series with a tutor and offered rows without a series offer.
    s1 = _series(dsn, subject)
    run_sql(
        dsn,
        "UPDATE class_series SET tutor_user_id = :t WHERE id = :id",
        {"t": tutor, "id": s1},
    )
    future = _occurrence(dsn, subject, s1, monday, "offered", tutor)
    past = _occurrence(
        dsn, subject, s1, datetime(2020, 1, 6, 22, tzinfo=UTC), "offered", tutor
    )
    covered = _occurrence(
        dsn, subject, s1, monday + timedelta(days=2), "offered", tutor
    )
    conflict = _occurrence(
        dsn, subject, s1, monday + timedelta(days=7), "conflict", tutor
    )
    # A #52 "This session only" offer (revision 0034 columns).
    [session_offer] = run_sql(
        dsn,
        "INSERT INTO assignment_offers (scope, tutor_user_id, offered_at,"
        " offered_by_user_id, respond_by) VALUES ('session', :u,"
        " '2031-01-01T12:00:00Z', :u, '2031-01-03T12:00:00Z') RETURNING id",
        {"u": tutor},
    )
    _cover(dsn, session_offer.id, covered)
    # S2: no tutor.
    s2 = _series(dsn, subject)
    unassigned = _occurrence(
        dsn, subject, s2, monday + timedelta(days=9), "unassigned", None
    )

    command.upgrade(config, "0036")

    # The broken state 0036 leaves.
    assert [row.id for row in run_sql(dsn, BROKEN_PENDING_SERIES)] == [s1]
    # S3: a proper pending series with its offer covering its offered row.
    s3 = _series(dsn, subject)
    run_sql(
        dsn,
        "UPDATE class_series SET tutor_user_id = :t, assignment_status = 'pending',"
        " assignment_from = starts_on WHERE id = :id",
        {"t": tutor, "id": s3},
    )
    s3_row = _occurrence(
        dsn, subject, s3, monday + timedelta(days=14), "offered", tutor
    )
    series_offer = _insert_offer(
        dsn,
        tutor,
        scope="series",
        class_series_id=s3,
        from_date=date(2031, 1, 6),
    )
    _cover(dsn, series_offer, s3_row)
    sessions_before = _session_rows(dsn)
    series_before = _series_rows(dsn)
    offers_before = run_sql(
        dsn, "SELECT id, status, tutor_user_id FROM assignment_offers ORDER BY id"
    )

    command.upgrade(config, "0037")

    assert version_nums(dsn) == ["0037"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0038(d)
            or _of_0039(d)
            or _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    sessions = _session_rows(dsn)
    for cleared in (future, past):
        state, tutor_id, version = sessions_before[cleared]
        assert (state, tutor_id) == ("offered", tutor)
        assert sessions[cleared] == ("unassigned", None, version + 1)
    for kept in (covered, conflict, unassigned, s3_row):
        assert sessions[kept] == sessions_before[kept]
    series = _series_rows(dsn)
    assert series[s1] == (None, None, None, series_before[s1][3] + 1)
    assert series[s2] == series_before[s2] == (None, None, None, 1)
    assert series[s3] == series_before[s3]
    assert (
        run_sql(
            dsn, "SELECT id, status, tutor_user_id FROM assignment_offers ORDER BY id"
        )
        == offers_before
    )
    # #233 A3 holds over the whole database.
    assert run_sql(dsn, BROKEN_PENDING_SERIES) == []
    assert run_sql(dsn, UNCOVERED_OFFERED_SESSIONS) == []

    # The downgrade is a no-op and a second run changes nothing.
    command.downgrade(config, "0036")
    command.upgrade(config, "0037")
    assert _session_rows(dsn) == sessions
    assert _series_rows(dsn) == series
    # A 0036 round trip deletes S3's offer and backfills it again without one;
    # the same upgrade clears it.
    command.downgrade(config, "0035")
    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert _series_rows(dsn)[s3] == (None, None, None, series[s3][3] + 1)
    assert _session_rows(dsn)[s3_row][:2] == ("unassigned", None)
    assert run_sql(dsn, BROKEN_PENDING_SERIES) == []
    assert run_sql(dsn, UNCOVERED_OFFERED_SESSIONS) == []


def test_0038_adds_the_series_lineage_and_downgrade_drops_it(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0037")
    subject = _subject_row(dsn)
    old = _series(dsn, subject)
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0038(d)
            or _of_0039(d)
            or _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []

    command.upgrade(config, "0038")

    assert version_nums(dsn) == ["0038"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0039(d)
            or _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    [row] = run_sql(
        dsn, "SELECT split_from_series_id FROM class_series WHERE id = :id", {"id": old}
    )
    assert row.split_from_series_id is None
    new = _series(dsn, subject)
    run_sql(
        dsn,
        "UPDATE class_series SET split_from_series_id = :old WHERE id = :id",
        {"old": old, "id": new},
    )
    with pytest.raises(IntegrityError, match="split_from_not_self"):
        run_sql(
            dsn,
            "UPDATE class_series SET split_from_series_id = id WHERE id = :id",
            {"id": old},
        )
    with pytest.raises(IntegrityError, match=LINEAGE_FK):
        run_sql(dsn, "DELETE FROM class_series WHERE id = :id", {"id": old})

    command.downgrade(config, "0037")

    assert version_nums(dsn) == ["0037"]
    assert LINEAGE_COLUMN not in _columns(dsn, "class_series")
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0038(d)
            or _of_0039(d)
            or _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    assert {r.id for r in run_sql(dsn, "SELECT id FROM class_series")} >= {old, new}

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


# --- 0039: the series override context (#236 D1) ------------------------------------


def test_0039_adds_the_override_context_and_downgrade_drops_it(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0038")
    subject = _subject_row(dsn)
    series = _series(dsn, subject)
    [session] = run_sql(
        dsn,
        "INSERT INTO class_sessions (kind, state, subject_id, starts_at, ends_at,"
        " scheduled_minutes) VALUES ('individual', 'unassigned', :subject,"
        " '2031-01-12 16:00Z', '2031-01-12 17:00Z', 60) RETURNING id",
        {"subject": subject},
    )
    actor = _user_row(dsn)
    [old] = run_sql(
        dsn,
        "INSERT INTO class_session_overrides"
        " (class_session_id, codes, conflicts, reason, actor_user_id)"
        " VALUES (:s, ARRAY['notice_window'], '[]', 'Antes', :a) RETURNING id",
        {"s": session.id, "a": actor},
    )

    command.upgrade(config, "0039")

    assert version_nums(dsn) == ["0039"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    [row] = run_sql(
        dsn,
        "SELECT class_series_id, reason FROM class_session_overrides WHERE id = :id",
        {"id": old.id},
    )
    assert (row.class_series_id, row.reason) == (None, "Antes")
    [new] = run_sql(
        dsn,
        "INSERT INTO class_session_overrides"
        " (class_session_id, class_series_id, codes, conflicts, reason,"
        " actor_user_id) VALUES (:s, :series, ARRAY['notice_window'], '[]',"
        " 'Con serie', :a) RETURNING id",
        {"s": session.id, "series": series, "a": actor},
    )
    # Still append-only, with the context column too (42501).
    for statement in (
        "UPDATE class_session_overrides SET class_series_id = NULL WHERE id = :id",
        "DELETE FROM class_session_overrides WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(dsn, statement, {"id": new.id})
        assert _sqlstate(raised.value) == "42501", raised.value
    with pytest.raises(IntegrityError, match=OVERRIDE_CONTEXT_FK):
        run_sql(
            dsn,
            "INSERT INTO class_session_overrides"
            " (class_session_id, class_series_id, codes, conflicts, reason,"
            " actor_user_id) VALUES (:s, :series, ARRAY['notice_window'], '[]',"
            " 'x', :a)",
            {"s": session.id, "series": uuid.uuid4(), "a": actor},
        )

    command.downgrade(config, "0038")

    assert version_nums(dsn) == ["0038"]
    assert OVERRIDE_CONTEXT_COLUMN not in _columns(dsn, "class_session_overrides")
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0039(d)
            or _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    assert len(run_sql(dsn, "SELECT id FROM class_session_overrides")) == 2

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


# --- 0040: substitutions (#55 A1, A2, A4) -------------------------------------------

SUBSTITUTION_INSERT = (
    "INSERT INTO class_session_substitutions (class_session_id, offer_id,"
    " original_tutor_user_id, substitute_tutor_user_id, reason,"
    " requested_by_user_id, slot_starts_at, slot_ends_at)"
    " VALUES (:s, :o, :original, :substitute, :reason, :original,"
    " '2031-01-12 16:00Z', '2031-01-12 17:00Z') RETURNING id"
)


def _offer_row(dsn: str, tutor: uuid.UUID, kind: str | None = None) -> uuid.UUID:
    """A session-scope offer, with ``kind`` when given (else the default)."""
    if kind is None:
        [row] = run_sql(
            dsn,
            "INSERT INTO assignment_offers (scope, tutor_user_id, offered_at,"
            " offered_by_user_id, respond_by) VALUES ('session', :u,"
            " '2031-01-01T12:00:00Z', :u, '2031-01-03T12:00:00Z') RETURNING id",
            {"u": tutor},
        )
    else:
        [row] = run_sql(
            dsn,
            "INSERT INTO assignment_offers (scope, kind, tutor_user_id, offered_at,"
            " offered_by_user_id, respond_by) VALUES ('session', :k, :u,"
            " '2031-01-01T12:00:00Z', :u, '2031-01-03T12:00:00Z') RETURNING id",
            {"u": tutor, "k": kind},
        )
    return uuid.UUID(str(row.id))


def test_0040_adds_kind_and_substitutions_and_downgrade_drops_them(
    fresh_database_url,
):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0039")
    tutor, substitute = _user_row(dsn), _user_row(dsn)
    old = _offer_row(dsn, tutor)
    before = public_tables(dsn)

    command.upgrade(config, "0040")

    assert version_nums(dsn) == ["0040"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    assert public_tables(dsn) - before == {SUBSTITUTIONS_TABLE}
    [row] = run_sql(
        dsn, "SELECT kind FROM assignment_offers WHERE id = :id", {"id": old}
    )
    assert row.kind == "assignment"
    subject = _subject_row(dsn)
    [session] = run_sql(
        dsn,
        "INSERT INTO class_sessions (kind, state, subject_id, tutor_user_id,"
        " starts_at, ends_at, scheduled_minutes) VALUES ('individual',"
        " 'confirmed', :subject, :t, '2031-01-12 16:00Z', '2031-01-12 17:00Z', 60)"
        " RETURNING id",
        {"subject": subject, "t": tutor},
    )
    offer = _offer_row(dsn, substitute, kind="substitution")
    substitution = {
        "s": session.id,
        "o": offer,
        "original": tutor,
        "substitute": substitute,
        "reason": "Enfermedad",
    }
    run_sql(dsn, SUBSTITUTION_INSERT, substitution)

    # A4: the raw-SQL rejections.
    with pytest.raises(IntegrityError, match="ck_assignment_offers_kind_valid"):
        _offer_row(dsn, tutor, kind="swap")
    with pytest.raises(
        IntegrityError, match="ck_assignment_offers_substitution_session_scope"
    ):
        run_sql(
            dsn,
            "INSERT INTO assignment_offers (scope, kind, tutor_user_id, offered_at,"
            " offered_by_user_id, respond_by, class_series_id, from_date)"
            " VALUES ('series', 'substitution', :u, '2031-01-01T12:00:00Z', :u,"
            " '2031-01-03T12:00:00Z', :series, '2031-01-06')",
            {"u": tutor, "series": _series(dsn, subject)},
        )
    for changes, constraint in (
        ({"substitute": tutor}, "ck_class_session_substitutions_substitute_differs"),
        ({"reason": " Enfermedad "}, "ck_class_session_substitutions_reason_trimmed"),
        ({"reason": ""}, "ck_class_session_substitutions_reason_trimmed"),
        ({}, "uq_class_session_substitutions_offer_id"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, SUBSTITUTION_INSERT, substitution | changes)

    command.downgrade(config, "0039")

    assert version_nums(dsn) == ["0039"]
    assert public_tables(dsn) == before
    assert "kind" not in _columns(dsn, "assignment_offers")
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0040(d)
            or _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    assert {r.id for r in run_sql(dsn, "SELECT id FROM assignment_offers")} == {old}

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_class_session_substitutions_columns_constraints_and_indexes(
    fresh_database_url,
):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = SUBSTITUTIONS_TABLE

    assert _columns(fresh_database_url, table) == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "class_session_id": ("uuid", "NO", None, None),
        "offer_id": ("uuid", "NO", None, None),
        "original_tutor_user_id": ("uuid", "NO", None, None),
        "substitute_tutor_user_id": ("uuid", "NO", None, None),
        "reason": ("character varying", "NO", 1000, None),
        "requested_by_user_id": ("uuid", "NO", None, None),
        "slot_starts_at": ("timestamp with time zone", "NO", None, None),
        "slot_ends_at": ("timestamp with time zone", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    foreign_keys = {
        "fk_class_session_substitutions_class_session_id_class_sessions",
        "fk_class_session_substitutions_offer_id_assignment_offers",
        "fk_class_session_substitutions_original_tutor_user_id_users",
        "fk_class_session_substitutions_substitute_tutor_user_id_users",
        "fk_class_session_substitutions_requested_by_user_id_users",
    }
    assert set(constraints) == {
        "pk_class_session_substitutions",
        "uq_class_session_substitutions_offer_id",
        "ck_class_session_substitutions_substitute_differs",
        "ck_class_session_substitutions_reason_trimmed",
        *foreign_keys,
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(fresh_database_url, table)) == {
        "pk_class_session_substitutions",
        "uq_class_session_substitutions_offer_id",
        SUBSTITUTIONS_INDEX,
    }


# --- 0041: tutoring groups (#56 A) ----------------------------------------------------


def _student_row(dsn: str) -> uuid.UUID:
    student = uuid.uuid4()
    run_sql(
        dsn,
        "INSERT INTO students (id, given_names, family_names, birthdate)"
        " VALUES (:id, 'Ana', 'Grupo', DATE '2014-03-01')",
        {"id": student},
    )
    return student


def _session_row(dsn: str, subject: uuid.UUID, **values: Any) -> uuid.UUID:
    columns: dict[str, Any] = {
        "kind": "individual",
        "state": "unassigned",
        "subject_id": subject,
        "starts_at": datetime(2031, 1, 12, 16, tzinfo=UTC),
        "ends_at": datetime(2031, 1, 12, 17, tzinfo=UTC),
        "scheduled_minutes": 60,
        **values,
    }
    names = ", ".join(columns)
    params = ", ".join(f":{name}" for name in columns)
    [row] = run_sql(
        dsn,
        f"INSERT INTO class_sessions ({names}) VALUES ({params}) RETURNING id",  # noqa: S608 (fixed names)
        columns,
    )
    return uuid.UUID(str(row.id))


OVERRIDE_INSERT = (
    "INSERT INTO class_session_overrides (class_session_id, group_enrollment_id,"
    " codes, conflicts, reason, actor_user_id) VALUES (:s, :e,"
    " ARRAY['group_capacity_exceeded'], '[]'::jsonb, 'Motivo', :u) RETURNING id"
)


def test_0041_adds_the_group_tables_and_downgrade_drops_them(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0040")
    user, subject, student = _user_row(dsn), _subject_row(dsn), _student_row(dsn)
    session_id = _session_row(dsn, subject)
    [kept] = run_sql(
        dsn,
        "INSERT INTO class_session_overrides (class_session_id, codes, conflicts,"
        " reason, actor_user_id) VALUES (:s, ARRAY['notice_window'], '[]'::jsonb,"
        " 'Motivo', :u) RETURNING id",
        {"s": session_id, "u": user},
    )
    before = public_tables(dsn)

    command.upgrade(config, "0041")

    assert version_nums(dsn) == ["0041"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    assert public_tables(dsn) - before == set(GROUP_TABLES)
    [group] = run_sql(
        dsn,
        "INSERT INTO class_groups (name, subject_id, max_students)"
        " VALUES ('Álgebra', :s, 6) RETURNING id",
        {"s": subject},
    )
    enrollment_insert = (
        "INSERT INTO group_enrollments (group_id, student_id, enrolled_at,"
        " enrolled_by_user_id) VALUES (:g, :st, '2031-01-05T18:00Z', :u)"
        " RETURNING id"
    )
    values = {"g": group.id, "st": student, "u": user}
    [enrollment] = run_sql(dsn, enrollment_insert, values)
    [override] = run_sql(
        dsn, OVERRIDE_INSERT, {"s": None, "e": enrollment.id, "u": user}
    )
    _session_row(dsn, subject, kind="group", group_id=group.id)

    # The CHECKs and the unique indexes.
    for statement, params, constraint in (
        (
            "INSERT INTO class_groups (name, subject_id, max_students)"
            " VALUES ('ÁLGEBRA', :s, 6)",
            {"s": subject},
            "uq_class_groups_name",
        ),
        (
            "INSERT INTO class_groups (name, subject_id, max_students)"
            " VALUES (' Otro', :s, 6)",
            {"s": subject},
            "ck_class_groups_name_trimmed",
        ),
        (
            "INSERT INTO class_groups (name, subject_id, max_students)"
            " VALUES ('Otro', :s, 501)",
            {"s": subject},
            "ck_class_groups_max_students_range",
        ),
        (enrollment_insert, values, "uq_group_enrollments_active"),
        (OVERRIDE_INSERT, {"s": None, "e": None, "u": user}, "one_subject"),
        (
            OVERRIDE_INSERT,
            {"s": session_id, "e": enrollment.id, "u": user},
            "one_subject",
        ),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, statement, params)
    with pytest.raises(IntegrityError, match="ck_class_sessions_group_iff_kind"):
        _session_row(dsn, subject, kind="group")
    with pytest.raises(IntegrityError, match="ck_class_sessions_group_iff_kind"):
        _session_row(dsn, subject, group_id=group.id)
    with pytest.raises(IntegrityError, match="ck_class_series_group_iff_kind"):
        _series(dsn, subject, kind="group")

    # The write-once trigger: one withdrawal, then nothing.
    run_sql(
        dsn,
        "UPDATE group_enrollments SET withdrawn_at = '2031-01-06T18:00Z',"
        " withdrawn_by_user_id = :u, withdrawal_reason = 'Mudanza',"
        " version = version + 1 WHERE id = :id",
        {"u": user, "id": enrollment.id},
    )
    for statement in (
        "UPDATE group_enrollments SET withdrawal_reason = 'Otra' WHERE id = :id",
        "UPDATE group_enrollments SET enrolled_by_user_id = NULL WHERE id = :id",
        "UPDATE group_enrollments SET enrolled_at = '2031-01-01T00:00Z' WHERE id = :id",
        "DELETE FROM group_enrollments WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(dsn, statement, {"id": enrollment.id})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"
    # Re-enrolling after the withdrawal is a new period.
    run_sql(dsn, enrollment_insert, values)

    command.downgrade(config, "0040")

    assert version_nums(dsn) == ["0040"]
    assert public_tables(dsn) == before
    assert "group_id" not in _columns(dsn, "class_sessions")
    assert "group_id" not in _columns(dsn, "class_series")
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0041(d)
            or _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    remaining = {r.id for r in run_sql(dsn, "SELECT id FROM class_session_overrides")}
    assert remaining == {kept.id}
    assert override.id not in remaining
    # A group session lost its group; it must go before upgrading again.
    run_sql(dsn, "DELETE FROM class_sessions WHERE kind = 'group'")

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_group_tables_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")

    assert _columns(dsn, "class_groups") == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "version": STANDARD_COLUMNS["version"],
        "name": ("character varying", "NO", 120, None),
        "subject_id": ("uuid", "NO", None, None),
        "max_students": ("smallint", "NO", None, None),
    }
    assert _columns(dsn, "group_enrollments") == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "version": STANDARD_COLUMNS["version"],
        "group_id": ("uuid", "NO", None, None),
        "student_id": ("uuid", "NO", None, None),
        "enrolled_at": ("timestamp with time zone", "NO", None, None),
        "enrolled_by_user_id": ("uuid", "NO", None, None),
        "withdrawn_at": ("timestamp with time zone", "YES", None, None),
        "withdrawn_by_user_id": ("uuid", "YES", None, None),
        "withdrawal_reason": ("character varying", "YES", 1000, None),
    }
    for table in ("class_series", "class_sessions"):
        assert _columns(dsn, table)["group_id"] == ("uuid", "YES", None, None)
        constraints = _constraints(dsn, table)
        assert f"ck_{table}_group_iff_kind" in constraints
        assert "ON DELETE RESTRICT" in constraints[f"fk_{table}_group_id_class_groups"]
    enrollment_constraints = _constraints(dsn, "group_enrollments")
    assert set(enrollment_constraints) == {
        "pk_group_enrollments",
        "fk_group_enrollments_group_id_class_groups",
        "fk_group_enrollments_student_id_students",
        "fk_group_enrollments_enrolled_by_user_id_users",
        "fk_group_enrollments_withdrawn_by_user_id_users",
        "ck_group_enrollments_withdrawn_after_enrolled",
        "ck_group_enrollments_withdrawn_together",
        "ck_group_enrollments_withdrawal_reason_valid",
    }
    indexes = _indexes(dsn, "group_enrollments")
    assert set(indexes) == {
        "pk_group_enrollments",
        "ix_group_enrollments_group_id",
        "ix_group_enrollments_student_id",
        "uq_group_enrollments_active",
    }
    assert "WHERE (withdrawn_at IS NULL)" in indexes["uq_group_enrollments_active"]
    assert (
        "lower((name)::text)" in _indexes(dsn, "class_groups")["uq_class_groups_name"]
    )
    assert "ix_class_sessions_group_id_starts_at" in _indexes(dsn, "class_sessions")
    assert "ix_class_series_group_id" in _indexes(dsn, "class_series")


# --- 0042: session requests (#59 A) ---------------------------------------------------

SESSION_REQUEST_INSERT = (
    "INSERT INTO session_requests (student_id, subject_id, requested_by_user_id,"
    " starts_at, ends_at, duration_minutes, note, status, resolved_at)"
    " VALUES (:student, :subject, :user, :starts_at, :ends_at, :duration, :note,"
    " :status, :resolved_at) RETURNING id"
)
REQUEST_START = datetime(2031, 1, 6, 16, 0, tzinfo=UTC)


def _request_values(
    student: uuid.UUID, subject: uuid.UUID, user: uuid.UUID, **overrides: Any
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "student": student,
        "subject": subject,
        "user": user,
        "starts_at": REQUEST_START,
        "ends_at": REQUEST_START + timedelta(minutes=60),
        "duration": 60,
        "note": None,
        "status": "pending",
        "resolved_at": None,
    }
    return values | overrides


def test_0042_adds_the_session_requests_table_and_downgrade_drops_it(
    fresh_database_url,
):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0041")
    before = public_tables(dsn)

    command.upgrade(config, "0042")

    assert version_nums(dsn) == ["0042"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    assert public_tables(dsn) - before == set(SESSION_REQUEST_TABLES)

    command.downgrade(config, "0041")

    assert version_nums(dsn) == ["0041"]
    assert public_tables(dsn) == before
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0042(d)
            or _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_session_requests_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")

    assert _columns(dsn, "session_requests") == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "version": STANDARD_COLUMNS["version"],
        "student_id": ("uuid", "NO", None, None),
        "subject_id": ("uuid", "NO", None, None),
        "requested_by_user_id": ("uuid", "NO", None, None),
        "starts_at": ("timestamp with time zone", "NO", None, None),
        "ends_at": ("timestamp with time zone", "NO", None, None),
        "duration_minutes": ("smallint", "NO", None, None),
        "note": ("character varying", "YES", 500, None),
        "status": ("character varying", "NO", 16, "'pending'::character varying"),
        "resolved_at": ("timestamp with time zone", "YES", None, None),
        # 0043 (#60 A1)
        "class_session_id": ("uuid", "YES", None, None),
        "rejection_reason": ("character varying", "YES", 1000, None),
    }
    constraints = _constraints(dsn, "session_requests")
    assert set(constraints) == {
        "pk_session_requests",
        "fk_session_requests_student_id_students",
        "fk_session_requests_subject_id_subjects",
        "fk_session_requests_requested_by_user_id_users",
        "ck_session_requests_whole_minutes",
        "ck_session_requests_duration_valid",
        "ck_session_requests_ends_after_duration",
        "ck_session_requests_note_trimmed",
        "ck_session_requests_status_valid",
        "ck_session_requests_resolved_iff_not_pending",
        "ex_session_requests_student_pending_overlap",
        # 0043 (#60 A2)
        "fk_session_requests_class_session_id_class_sessions",
        "ck_session_requests_confirmed_iff_session",
        "ck_session_requests_rejected_iff_reason",
    }
    for name in (
        "fk_session_requests_student_id_students",
        "fk_session_requests_subject_id_subjects",
        "fk_session_requests_requested_by_user_id_users",
    ):
        assert "ON DELETE RESTRICT" in constraints[name]
    exclusion = constraints["ex_session_requests_student_pending_overlap"]
    assert "EXCLUDE USING gist" in exclusion
    assert "WHERE (((status)::text = 'pending'::text))" in exclusion
    indexes = _indexes(dsn, "session_requests")
    assert (
        "(student_id, created_at DESC)"
        in (indexes["ix_session_requests_student_id_created_at"])
    )
    assert (
        "(requested_by_user_id, created_at DESC)"
        in (indexes["ix_session_requests_requested_by_user_id_created_at"])
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"duration": 50, "ends_at": REQUEST_START + timedelta(minutes=50)},
        {
            "starts_at": REQUEST_START + timedelta(seconds=30),
            "ends_at": REQUEST_START + timedelta(minutes=60, seconds=30),
        },
        {"ends_at": REQUEST_START + timedelta(minutes=45)},
        {"resolved_at": REQUEST_START},
        {"status": "withdrawn"},
        {"status": "booked", "resolved_at": REQUEST_START},
        {"note": " padded "},
        {"note": ""},
    ],
    ids=[
        "duration-50",
        "not-whole-minute",
        "end-not-duration",
        "pending-resolved",
        "withdrawn-unresolved",
        "unknown-status",
        "untrimmed-note",
        "empty-note",
    ],
)
def test_session_requests_reject_invalid_rows(fresh_database_url, overrides):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    values = _request_values(
        _student_row(dsn), _subject_row(dsn), _user_row(dsn), **overrides
    )

    with pytest.raises(IntegrityError):
        run_sql(dsn, SESSION_REQUEST_INSERT, values)


def test_pending_requests_of_one_student_never_overlap(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    student, subject, user = _student_row(dsn), _subject_row(dsn), _user_row(dsn)
    run_sql(dsn, SESSION_REQUEST_INSERT, _request_values(student, subject, user))
    half_later = REQUEST_START + timedelta(minutes=30)

    with pytest.raises(IntegrityError):
        run_sql(
            dsn,
            SESSION_REQUEST_INSERT,
            _request_values(
                student,
                subject,
                user,
                starts_at=half_later,
                ends_at=half_later + timedelta(minutes=60),
            ),
        )

    # Back-to-back, a withdrawn overlapping row, and another student's
    # overlapping row are all accepted.
    back_to_back = REQUEST_START + timedelta(minutes=60)
    run_sql(
        dsn,
        SESSION_REQUEST_INSERT,
        _request_values(
            student,
            subject,
            user,
            starts_at=back_to_back,
            ends_at=back_to_back + timedelta(minutes=30),
            duration=30,
        ),
    )
    run_sql(
        dsn,
        SESSION_REQUEST_INSERT,
        _request_values(
            student,
            subject,
            user,
            starts_at=half_later,
            ends_at=half_later + timedelta(minutes=60),
            status="withdrawn",
            resolved_at=REQUEST_START,
        ),
    )
    run_sql(
        dsn,
        SESSION_REQUEST_INSERT,
        _request_values(
            _student_row(dsn),
            subject,
            user,
            starts_at=half_later,
            ends_at=half_later + timedelta(minutes=60),
        ),
    )


# --- 0043: session request decisions (#60 A) -----------------------------------------

DECIDED_INSERT = (
    "INSERT INTO session_requests (student_id, subject_id, requested_by_user_id,"
    " starts_at, ends_at, duration_minutes, status, resolved_at, class_session_id,"
    " rejection_reason) VALUES (:student, :subject, :user, :starts_at, :ends_at, 60,"
    " :status, :resolved_at, :session, :reason) RETURNING id"
)


def _decided_values(
    student: uuid.UUID, subject: uuid.UUID, user: uuid.UUID, **overrides: Any
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "student": student,
        "subject": subject,
        "user": user,
        "starts_at": REQUEST_START,
        "ends_at": REQUEST_START + timedelta(minutes=60),
        "status": "pending",
        "resolved_at": None,
        "session": None,
        "reason": None,
    }
    return values | overrides


def test_0043_adds_the_decision_columns_and_downgrade_drops_them(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0042")
    student, subject, user = _student_row(dsn), _subject_row(dsn), _user_row(dsn)
    [kept] = run_sql(
        dsn, SESSION_REQUEST_INSERT, _request_values(student, subject, user)
    )

    command.upgrade(config, "0043")

    assert version_nums(dsn) == ["0043"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0044(d) or _of_0045(d) or _of_0046(d) or _of_0047(d) or _of_0048_on(d)
        )
    ] == []
    # Existing rows satisfy both CHECKs without a backfill.
    [row] = run_sql(
        dsn,
        "SELECT status, class_session_id, rejection_reason FROM session_requests"
        " WHERE id = :id",
        {"id": kept.id},
    )
    assert tuple(row) == ("pending", None, None)
    constraints = _constraints(dsn, "session_requests")
    assert (
        "ON DELETE RESTRICT"
        in (constraints["fk_session_requests_class_session_id_class_sessions"])
    )
    index = _indexes(dsn, "session_requests")["uq_session_requests_class_session_id"]
    assert "UNIQUE" in index and "WHERE (class_session_id IS NOT NULL)" in index

    command.downgrade(config, "0042")

    assert version_nums(dsn) == ["0042"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0043(d)
            or _of_0044(d)
            or _of_0045(d)
            or _of_0046(d)
            or _of_0047(d)
            or _of_0048_on(d)
        )
    ] == []
    assert "class_session_id" not in _columns(dsn, "session_requests")

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


@pytest.mark.parametrize(
    ("overrides", "needs_session"),
    [
        ({"status": "confirmed", "resolved_at": REQUEST_START}, False),
        ({}, True),
        ({"status": "rejected", "resolved_at": REQUEST_START}, False),
        (
            {"status": "withdrawn", "resolved_at": REQUEST_START, "reason": "No"},
            False,
        ),
        ({"status": "pending", "reason": "No"}, False),
    ],
    ids=[
        "confirmed-without-session",
        "pending-with-session",
        "rejected-without-reason",
        "withdrawn-with-reason",
        "pending-with-reason",
    ],
)
def test_session_request_decisions_reject_invalid_rows(
    fresh_database_url, overrides, needs_session
):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    subject = _subject_row(dsn)
    if needs_session:
        overrides = overrides | {"session": _session_row(dsn, subject)}
    values = _decided_values(_student_row(dsn), subject, _user_row(dsn), **overrides)

    with pytest.raises(IntegrityError):
        run_sql(dsn, DECIDED_INSERT, values)


def test_one_session_belongs_to_one_request(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    subject, user = _subject_row(dsn), _user_row(dsn)
    session_id = _session_row(dsn, subject)
    confirmed = {
        "status": "confirmed",
        "resolved_at": REQUEST_START,
        "session": session_id,
    }
    run_sql(
        dsn,
        DECIDED_INSERT,
        _decided_values(_student_row(dsn), subject, user, **confirmed),
    )
    run_sql(
        dsn,
        DECIDED_INSERT,
        _decided_values(
            _student_row(dsn),
            subject,
            user,
            status="rejected",
            resolved_at=REQUEST_START,
            reason="Sin cupo",
        ),
    )

    with pytest.raises(IntegrityError):
        run_sql(
            dsn,
            DECIDED_INSERT,
            _decided_values(_student_row(dsn), subject, user, **confirmed),
        )


# --- 0044: change requests (#62 A) ---------------------------------------------------

CHANGE_REQUEST_COLUMNS = (
    "class_session_id",
    "kind",
    "initiated_by",
    "requested_by_user_id",
    "student_id",
    "note",
    "session_starts_at",
    "session_ends_at",
    "proposed_starts_at",
    "proposed_ends_at",
    "policy_id",
    "classification",
    "makeup_allowed",
    "makeup_until",
    "status",
    "exception",
    "decision_reason",
    "decided_by_user_id",
    "resolved_at",
    "final_classification",
    "final_makeup_allowed",
    "final_makeup_until",
)
CHANGE_REQUEST_INSERT = (
    f"INSERT INTO session_change_requests ({', '.join(CHANGE_REQUEST_COLUMNS)})"  # noqa: S608 (fixed names)
    f" VALUES ({', '.join(f':{name}' for name in CHANGE_REQUEST_COLUMNS)})"
    " RETURNING id"
)
CHANGE_START = datetime(2031, 1, 12, 16, tzinfo=UTC)


def _change_values(dsn: str, session: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    [policy] = run_sql(dsn, "SELECT id FROM scheduling_policies LIMIT 1")
    values: dict[str, Any] = {
        "class_session_id": session,
        "kind": "cancellation",
        "initiated_by": "family",
        "requested_by_user_id": _user_row(dsn),
        "student_id": _student_row(dsn),
        "note": None,
        "session_starts_at": CHANGE_START,
        "session_ends_at": CHANGE_START + timedelta(hours=1),
        "proposed_starts_at": None,
        "proposed_ends_at": None,
        "policy_id": policy.id,
        "classification": "late",
        "makeup_allowed": False,
        "makeup_until": None,
        "status": "pending",
        "exception": False,
        "decision_reason": None,
        "decided_by_user_id": None,
        "resolved_at": None,
        "final_classification": None,
        "final_makeup_allowed": None,
        "final_makeup_until": None,
    }
    return values | overrides


def _approved(dsn: str, **overrides: Any) -> dict[str, Any]:
    return {
        "status": "approved",
        "resolved_at": CHANGE_START,
        "decided_by_user_id": _user_row(dsn),
        "final_classification": "late",
        "final_makeup_allowed": False,
    } | overrides


def test_0044_adds_the_change_requests_table_and_downgrade_drops_it(
    fresh_database_url,
):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0043")
    before = public_tables(dsn)

    command.upgrade(config, "0044")

    assert version_nums(dsn) == ["0044"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (_of_0045(d) or _of_0046(d) or _of_0047(d) or _of_0048_on(d))
    ] == []
    assert public_tables(dsn) - before == set(CHANGE_REQUEST_TABLES)

    command.downgrade(config, "0043")

    assert version_nums(dsn) == ["0043"]
    assert public_tables(dsn) == before
    assert [
        d
        for d in metadata_diff(dsn)
        if not (
            _of_0044(d) or _of_0045(d) or _of_0046(d) or _of_0047(d) or _of_0048_on(d)
        )
    ] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_change_requests_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    instant = ("timestamp with time zone", "NO", None, None)
    optional_instant = ("timestamp with time zone", "YES", None, None)

    assert _columns(dsn, "session_change_requests") == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "version": STANDARD_COLUMNS["version"],
        "class_session_id": ("uuid", "NO", None, None),
        "kind": ("character varying", "NO", 20, None),
        "initiated_by": ("character varying", "NO", 10, None),
        "requested_by_user_id": ("uuid", "NO", None, None),
        "student_id": ("uuid", "YES", None, None),
        "note": ("character varying", "YES", 500, None),
        "session_starts_at": instant,
        "session_ends_at": instant,
        "proposed_starts_at": optional_instant,
        "proposed_ends_at": optional_instant,
        "policy_id": ("uuid", "NO", None, None),
        "classification": ("character varying", "NO", 10, None),
        "makeup_allowed": ("boolean", "NO", None, None),
        "makeup_until": optional_instant,
        "status": ("character varying", "NO", 16, "'pending'::character varying"),
        "exception": ("boolean", "NO", None, "false"),
        "decision_reason": ("character varying", "YES", 1000, None),
        "decided_by_user_id": ("uuid", "YES", None, None),
        "resolved_at": optional_instant,
        "final_classification": ("character varying", "YES", 10, None),
        "final_makeup_allowed": ("boolean", "YES", None, None),
        "final_makeup_until": optional_instant,
    }
    constraints = _constraints(dsn, "session_change_requests")
    foreign_keys = {
        "fk_session_change_requests_class_session_id_class_sessions",
        "fk_session_change_requests_requested_by_user_id_users",
        "fk_session_change_requests_student_id_students",
        "fk_session_change_requests_policy_id_scheduling_policies",
        "fk_session_change_requests_decided_by_user_id_users",
    }
    checks = {f"ck_session_change_requests_{name}" for name in CHANGE_REQUEST_CHECKS}
    assert set(constraints) == {"pk_session_change_requests"} | foreign_keys | checks
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    indexes = _indexes(dsn, "session_change_requests")
    pending = indexes["uq_session_change_requests_pending_session"]
    assert "UNIQUE" in pending and "WHERE ((status)::text = 'pending'::text)" in pending
    assert (
        "(class_session_id)" in indexes["ix_session_change_requests_class_session_id"]
    )
    assert (
        "(student_id, created_at DESC)"
        in indexes["ix_session_change_requests_student_id_created_at"]
    )
    assert (
        "(requested_by_user_id, created_at DESC)"
        in indexes["ix_session_change_requests_requested_by_user_id_created_at"]
    )


RESCHEDULE = {
    "kind": "reschedule",
    "proposed_starts_at": CHANGE_START + timedelta(days=1),
    "proposed_ends_at": CHANGE_START + timedelta(days=1, hours=1),
}


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "reschedule"},
        RESCHEDULE | {"kind": "cancellation"},
        RESCHEDULE | {"proposed_ends_at": None},
        RESCHEDULE | {"proposed_ends_at": CHANGE_START + timedelta(days=1)},
        RESCHEDULE
        | {"proposed_starts_at": CHANGE_START + timedelta(days=1, seconds=30)},
        {"student_id": None},
        {"initiated_by": "tutor"},
        {"initiated_by": "center", "student_id": None},
        {"kind": "absence"},
        {"makeup_until": CHANGE_START},
        {"makeup_allowed": True},
        RESCHEDULE
        | {"makeup_allowed": True, "makeup_until": CHANGE_START + timedelta(days=30)},
        {"classification": "maybe"},
        {"session_ends_at": CHANGE_START},
        {"status": "approved", "resolved_at": CHANGE_START},
        "approved-without-final",
        "rejected-without-reason",
        "exception-not-excused",
        "exception-without-reason",
        "exception-pending",
        "approved-without-decider",
        {"decided_by_user_id": "user"},
        {"resolved_at": CHANGE_START},
        {"status": "withdrawn"},
        {"status": "withdrawn", "resolved_at": CHANGE_START, "decision_reason": "x"},
        {"note": " padded "},
        {"note": ""},
        "final-makeup-without-until",
        "final-makeup-on-reschedule",
    ],
    ids=[
        "reschedule-without-proposal",
        "cancellation-with-proposal",
        "proposal-without-end",
        "proposal-ends-at-start",
        "proposal-not-whole-minute",
        "family-without-student",
        "tutor-with-student",
        "unknown-initiator",
        "unknown-kind",
        "makeup-until-without-makeup",
        "makeup-without-until",
        "makeup-on-reschedule",
        "unknown-classification",
        "session-ends-at-start",
        "approved-without-decider-or-final",
        "approved-without-final",
        "rejected-without-reason",
        "exception-not-excused",
        "exception-without-reason",
        "exception-pending",
        "approved-without-decider",
        "pending-with-decider",
        "pending-resolved",
        "withdrawn-unresolved",
        "withdrawn-with-reason",
        "untrimmed-note",
        "empty-note",
        "final-makeup-without-until",
        "final-makeup-on-reschedule",
    ],
)
def test_change_requests_reject_invalid_rows(fresh_database_url, overrides):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    session = _session_row(dsn, _subject_row(dsn))
    named: dict[str, dict[str, Any]] = {
        "approved-without-final": _approved(
            dsn, final_classification=None, final_makeup_allowed=None
        ),
        "rejected-without-reason": {
            "status": "rejected",
            "resolved_at": CHANGE_START,
            "decided_by_user_id": _user_row(dsn),
        },
        "exception-not-excused": _approved(
            dsn, exception=True, decision_reason="Enfermedad"
        ),
        "exception-without-reason": _approved(
            dsn, exception=True, final_classification="excused"
        ),
        "exception-pending": {"exception": True, "decision_reason": "Enfermedad"},
        "approved-without-decider": _approved(dsn, decided_by_user_id=None),
        "final-makeup-without-until": _approved(dsn, final_makeup_allowed=True),
        "final-makeup-on-reschedule": RESCHEDULE
        | _approved(
            dsn,
            final_classification="excused",
            final_makeup_allowed=True,
            final_makeup_until=CHANGE_START + timedelta(days=30),
        ),
    }
    if isinstance(overrides, str):
        overrides = named[overrides]
    if overrides.get("decided_by_user_id") == "user":
        overrides = overrides | {"decided_by_user_id": _user_row(dsn)}
    values = _change_values(dsn, session, **overrides)

    with pytest.raises(IntegrityError):
        run_sql(dsn, CHANGE_REQUEST_INSERT, values)


def test_change_requests_accept_every_valid_shape(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    subject = _subject_row(dsn)

    def insert(**overrides: Any) -> None:
        session = _session_row(dsn, subject)
        run_sql(dsn, CHANGE_REQUEST_INSERT, _change_values(dsn, session, **overrides))

    insert()
    insert(**RESCHEDULE)
    insert(initiated_by="tutor", student_id=None, classification="excused")
    insert(makeup_allowed=True, makeup_until=CHANGE_START + timedelta(days=30))
    insert(**_approved(dsn))
    insert(
        **_approved(
            dsn,
            exception=True,
            decision_reason="Enfermedad",
            final_classification="excused",
            final_makeup_allowed=True,
            final_makeup_until=CHANGE_START + timedelta(days=30),
        )
    )
    insert(
        status="rejected",
        resolved_at=CHANGE_START,
        decided_by_user_id=_user_row(dsn),
        decision_reason="Sin cupo",
    )
    insert(status="withdrawn", resolved_at=CHANGE_START)


def test_one_pending_change_request_per_session(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    session = _session_row(dsn, _subject_row(dsn))
    run_sql(dsn, CHANGE_REQUEST_INSERT, _change_values(dsn, session))

    with pytest.raises(IntegrityError):
        run_sql(
            dsn,
            CHANGE_REQUEST_INSERT,
            _change_values(dsn, session, initiated_by="tutor", student_id=None),
        )

    # Decided requests don't count: two approved ones on one session too.
    for _ in range(2):
        run_sql(
            dsn, CHANGE_REQUEST_INSERT, _change_values(dsn, session, **_approved(dsn))
        )


# --- 0045: availability change requests and reassignment flags (#63 A) ---------------

AVAILABILITY_CHANGE_INSERT = (
    "INSERT INTO tutor_availability_change_requests (tutor_user_id,"
    " requested_by_user_id, kind, status, resolved_at, payload,"
    " affected_session_ids, affected_sessions, approval_request_id,"
    " rejection_reason) VALUES (:tutor_user_id, :requested_by_user_id, :kind,"
    " :status, :resolved_at, CAST(:payload AS jsonb),"
    " CAST(:affected_session_ids AS uuid[]), CAST(:affected_sessions AS jsonb),"
    " :approval_request_id, :rejection_reason) RETURNING id"
)
FLAG_INSERT = (
    "INSERT INTO class_session_reassignment_flags (class_session_id,"
    " tutor_user_id, reason, source_id, created_by_user_id) VALUES (:session,"
    " :tutor, :reason, :source, :tutor) RETURNING id"
)


def _availability_change_values(dsn: str, **overrides: Any) -> dict[str, Any]:
    user = _user_row(dsn)
    values: dict[str, Any] = {
        "tutor_user_id": user,
        "requested_by_user_id": user,
        "kind": "weekly",
        "status": "pending",
        "resolved_at": None,
        "payload": '{"base_version": 0, "windows": []}',
        "affected_session_ids": [str(uuid.uuid4())],
        "affected_sessions": "[]",
        "approval_request_id": uuid.uuid4(),
        "rejection_reason": None,
    }
    return values | overrides


def test_0045_adds_the_two_tables_and_downgrade_drops_them(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0044")
    before = public_tables(dsn)

    command.upgrade(config, "0045")

    assert version_nums(dsn) == ["0045"]
    assert [
        d
        for d in metadata_diff(dsn)
        if not (_of_0046(d) or _of_0047(d) or _of_0048_on(d))
    ] == []
    assert public_tables(dsn) - before == set(AVAILABILITY_CHANGE_TABLES)

    command.downgrade(config, "0044")

    assert version_nums(dsn) == ["0044"]
    assert public_tables(dsn) == before
    assert [
        d
        for d in metadata_diff(dsn)
        if not (_of_0045(d) or _of_0046(d) or _of_0047(d) or _of_0048_on(d))
    ] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_0045_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    optional_instant = ("timestamp with time zone", "YES", None, None)

    assert _columns(dsn, "tutor_availability_change_requests") == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "version": STANDARD_COLUMNS["version"],
        "tutor_user_id": ("uuid", "NO", None, None),
        "requested_by_user_id": ("uuid", "NO", None, None),
        "kind": ("character varying", "NO", 20, None),
        "status": ("character varying", "NO", 20, "'pending'::character varying"),
        "resolved_at": optional_instant,
        "payload": ("jsonb", "NO", None, None),
        "affected_session_ids": ("ARRAY", "NO", None, None),
        "affected_sessions": ("jsonb", "NO", None, None),
        "approval_request_id": ("uuid", "NO", None, None),
        "rejection_reason": ("character varying", "YES", 1000, None),
    }
    constraints = _constraints(dsn, "tutor_availability_change_requests")
    # The requester's key name is longer than 63 characters: Postgres keeps
    # the naming convention's truncated form.
    foreign_keys = {name for name in constraints if name.startswith("fk_")}
    assert len(foreign_keys) == 2
    checks = {
        f"ck_tutor_availability_change_requests_{name}"
        for name in AVAILABILITY_CHANGE_CHECKS
    }
    assert set(constraints) == (
        {"pk_tutor_availability_change_requests"} | foreign_keys | checks
    )
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert (
        "(tutor_user_id, status)"
        in _indexes(dsn, "tutor_availability_change_requests")[
            "ix_tutor_availability_change_requests_tutor_user_id_status"
        ]
    )

    assert _columns(dsn, "class_session_reassignment_flags") == {
        "id": STANDARD_COLUMNS["id"],
        "created_at": STANDARD_COLUMNS["created_at"],
        "updated_at": STANDARD_COLUMNS["updated_at"],
        "class_session_id": ("uuid", "NO", None, None),
        "tutor_user_id": ("uuid", "NO", None, None),
        "reason": ("character varying", "NO", 40, None),
        "source_id": ("uuid", "NO", None, None),
        "created_by_user_id": ("uuid", "NO", None, None),
    }
    constraints = _constraints(dsn, "class_session_reassignment_flags")
    foreign_keys = {name for name in constraints if name.startswith("fk_")}
    assert len(foreign_keys) == 3
    assert set(constraints) - foreign_keys == {
        "pk_class_session_reassignment_flags",
        "ck_class_session_reassignment_flags_reason_valid",
        "uq_class_session_reassignment_flags_source",
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert (
        "(class_session_id, tutor_user_id, source_id)"
        in constraints["uq_class_session_reassignment_flags_source"]
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "monthly"},
        {"status": "pending", "resolved_at": CHANGE_START},
        {"status": "approved"},
        {"status": "expired", "resolved_at": CHANGE_START},
        {"affected_session_ids": []},
        {"status": "rejected", "resolved_at": CHANGE_START},
        {"status": "approved", "resolved_at": CHANGE_START, "rejection_reason": "No"},
    ],
    ids=[
        "unknown-kind",
        "pending-resolved",
        "approved-unresolved",
        "unknown-status",
        "no-affected-session",
        "rejected-without-reason",
        "reason-without-rejection",
    ],
)
def test_availability_change_requests_reject_invalid_rows(
    fresh_database_url, overrides
):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")

    with pytest.raises(IntegrityError):
        run_sql(
            dsn,
            AVAILABILITY_CHANGE_INSERT,
            _availability_change_values(dsn, **overrides),
        )


def test_availability_change_requests_accept_every_status(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")

    for overrides in (
        {},
        {"kind": "exception_create", "status": "approved", "resolved_at": CHANGE_START},
        {
            "kind": "exception_delete",
            "status": "rejected",
            "resolved_at": CHANGE_START,
            "rejection_reason": "Sin sustituto",
        },
        {"status": "cancelled", "resolved_at": CHANGE_START},
    ):
        run_sql(
            dsn,
            AVAILABILITY_CHANGE_INSERT,
            _availability_change_values(dsn, **overrides),
        )


def test_reassignment_flags_take_one_reason_and_flag_once(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    session = _session_row(dsn, _subject_row(dsn))
    values = {
        "session": session,
        "tutor": _user_row(dsn),
        "reason": "availability_change",
        "source": uuid.uuid4(),
    }
    run_sql(dsn, FLAG_INSERT, values)

    with pytest.raises(IntegrityError):
        run_sql(dsn, FLAG_INSERT, values | {"reason": "tutor_left"})
    with pytest.raises(IntegrityError):
        run_sql(dsn, FLAG_INSERT, values)
    # Another change flags the same session again.
    run_sql(dsn, FLAG_INSERT, values | {"source": uuid.uuid4()})


# --- 0046: course cohorts (#65 A) -----------------------------------------------------


COHORT_INSERT = (
    "INSERT INTO course_cohorts (name, template_id, template_snapshot_id,"
    " subject_id, max_students) VALUES (:name, :template, :snapshot, :subject,"
    " :max_students) RETURNING id"
)


def _snapshot_row(dsn: str, template_id: uuid.UUID) -> uuid.UUID:
    [row] = run_sql(
        dsn,
        "INSERT INTO course_template_snapshots (template_id, template_version,"
        " content) VALUES (:t, 1, '{}'::jsonb) RETURNING id",
        {"t": template_id},
    )
    return uuid.UUID(str(row.id))


def _cohort_world(dsn: str) -> dict[str, Any]:
    subject = _subject_row(dsn)
    template = _course_template(dsn, name=f"Curso {uuid.uuid4().hex[:8]}")
    return {
        "name": f"Cohorte {uuid.uuid4().hex[:8]}",
        "template": template,
        "snapshot": _snapshot_row(dsn, template),
        "subject": subject,
        "max_students": 12,
    }


def _series_with(dsn: str, subject: uuid.UUID, **values: Any) -> uuid.UUID:
    columns: dict[str, Any] = {
        "kind": "individual",
        "subject_id": subject,
        "starts_on": date(2031, 1, 6),
        "start_time": time(16),
        "duration_minutes": 60,
        "time_zone": "America/Mexico_City",
        "weekdays": [1, 3],
        "interval": 1,
        "rrule": "FREQ=WEEKLY;INTERVAL=1;WKST=MO;BYDAY=MO,WE",
        **values,
    }
    names = ", ".join(columns)
    params = ", ".join(f":{name}" for name in columns)
    [row] = run_sql(
        dsn,
        f"INSERT INTO class_series ({names}) VALUES ({params}) RETURNING id",  # noqa: S608 (fixed names)
        columns,
    )
    return uuid.UUID(str(row.id))


def test_0046_adds_the_cohorts_and_downgrade_drops_them(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0045")
    subject = _subject_row(dsn)
    _series_with(dsn, subject)
    _session_row(dsn, subject)
    before = public_tables(dsn)

    command.upgrade(config, "0046")

    assert version_nums(dsn) == ["0046"]
    assert [d for d in metadata_diff(dsn) if not (_of_0047(d) or _of_0048_on(d))] == []
    assert public_tables(dsn) - before == set(COHORT_TABLES)
    world = _cohort_world(dsn)
    [cohort] = run_sql(dsn, COHORT_INSERT, world)
    _series_with(dsn, subject, kind="course", cohort_id=cohort.id)
    _session_row(dsn, subject, kind="course", cohort_id=cohort.id)

    command.downgrade(config, "0045")

    assert version_nums(dsn) == ["0045"]
    assert public_tables(dsn) == before
    for table in ("class_series", "class_sessions"):
        assert "cohort_id" not in _columns(dsn, table)
    assert [
        d
        for d in metadata_diff(dsn)
        if not (_of_0046(d) or _of_0047(d) or _of_0048_on(d))
    ] == []
    # Upgrading again needs the course rows without a cohort removed first.
    run_sql(dsn, "DELETE FROM class_sessions WHERE kind = 'course'")
    run_sql(dsn, "DELETE FROM class_series WHERE kind = 'course'")
    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]


def test_course_cohorts_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "course_cohorts"
    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "version": ("integer", "NO", None, "1"),
        "name": ("character varying", "NO", 120, None),
        "template_id": ("uuid", "NO", None, None),
        "template_snapshot_id": ("uuid", "NO", None, None),
        "subject_id": ("uuid", "NO", None, None),
        "max_students": ("smallint", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    foreign_keys = {
        "fk_course_cohorts_template_id_course_templates",
        "fk_course_cohorts_template_snapshot_id_snapshots",
        "fk_course_cohorts_subject_id_subjects",
    }
    assert set(constraints) == {
        "pk_course_cohorts",
        "ck_course_cohorts_name_trimmed",
        "ck_course_cohorts_name_not_blank",
        "ck_course_cohorts_max_students_range",
        *foreign_keys,
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(fresh_database_url, table)) == {
        "pk_course_cohorts",
        "uq_course_cohorts_name_ci",
        "ix_course_cohorts_template_id",
        "ix_course_cohorts_template_snapshot_id",
        "ix_course_cohorts_subject_id",
    }


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"max_students": 0}, "ck_course_cohorts_max_students_range"),
        ({"max_students": 501}, "ck_course_cohorts_max_students_range"),
        ({"name": " Cohorte"}, "ck_course_cohorts_name_trimmed"),
        ({"name": ""}, "ck_course_cohorts_name_not_blank"),
    ],
)
def test_invalid_cohorts_are_rejected(fresh_database_url, changes, constraint):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")

    with pytest.raises(IntegrityError, match=constraint):
        run_sql(dsn, COHORT_INSERT, _cohort_world(dsn) | changes)


def test_cohort_bounds_names_and_references_are_checked(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    world = _cohort_world(dsn)
    run_sql(dsn, COHORT_INSERT, world | {"max_students": 1})
    run_sql(
        dsn, COHORT_INSERT, world | {"name": world["name"] + " B", "max_students": 500}
    )

    # A duplicate name in another letter case.
    with pytest.raises(IntegrityError, match="uq_course_cohorts_name_ci"):
        run_sql(dsn, COHORT_INSERT, world | {"name": world["name"].upper()})
    # The snapshot can't be deleted (its trigger), nor the template (the key).
    with pytest.raises(DBAPIError, match="course_template_snapshots"):
        run_sql(
            dsn,
            "DELETE FROM course_template_snapshots WHERE id = :id",
            {"id": world["snapshot"]},
        )
    # A cohort whose template has no snapshot left to block the delete.
    other = _course_template(dsn, name=f"Curso {uuid.uuid4().hex[:8]}")
    run_sql(dsn, COHORT_INSERT, world | {"name": "Otra", "template": other})
    with pytest.raises(IntegrityError, match="fk_course_cohorts_template_id"):
        run_sql(dsn, "DELETE FROM course_templates WHERE id = :id", {"id": other})


def test_course_rows_need_a_cohort_and_only_they_have_one(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    world = _cohort_world(dsn)
    [cohort] = run_sql(dsn, COHORT_INSERT, world)
    subject = world["subject"]

    with pytest.raises(IntegrityError, match="ck_class_series_cohort_iff_kind"):
        _series_with(dsn, subject, kind="course")
    with pytest.raises(IntegrityError, match="ck_class_sessions_cohort_iff_kind"):
        _session_row(dsn, subject, kind="course")
    for kind in ("individual", "group"):
        with pytest.raises(IntegrityError, match="cohort_iff_kind"):
            _series_with(dsn, subject, kind=kind, cohort_id=cohort.id)
        with pytest.raises(IntegrityError, match="cohort_iff_kind"):
            _session_row(dsn, subject, kind=kind, cohort_id=cohort.id)
    _series_with(dsn, subject, kind="course", cohort_id=cohort.id)
    _session_row(dsn, subject, kind="course", cohort_id=cohort.id)
    # A cohort with sessions can't be deleted.
    with pytest.raises(IntegrityError, match="cohort_id_course_cohorts"):
        run_sql(dsn, "DELETE FROM course_cohorts WHERE id = :id", {"id": cohort.id})


# --- 0047: cohort enrollments (#66 A) -------------------------------------------------


COHORT_ENROLLMENT_INSERT = (
    "INSERT INTO cohort_enrollments (cohort_id, student_id, enrolled_at,"
    " enrolled_by_user_id) VALUES (:c, :st, '2031-01-05T18:00Z', :u) RETURNING id"
)
COHORT_OVERRIDE_INSERT = (
    "INSERT INTO class_session_overrides (class_session_id, group_enrollment_id,"
    " cohort_enrollment_id, codes, conflicts, reason, actor_user_id)"
    " VALUES (:s, :g, :c, ARRAY['group_capacity_exceeded'], '[]'::jsonb,"
    " 'Motivo', :u) RETURNING id"
)


def _group_enrollment_row(dsn: str, subject: uuid.UUID, user: uuid.UUID) -> Any:
    [group] = run_sql(
        dsn,
        "INSERT INTO class_groups (name, subject_id, max_students)"
        " VALUES (:n, :s, 6) RETURNING id",
        {"n": f"Grupo {uuid.uuid4().hex[:8]}", "s": subject},
    )
    [enrollment] = run_sql(
        dsn,
        "INSERT INTO group_enrollments (group_id, student_id, enrolled_at,"
        " enrolled_by_user_id) VALUES (:g, :st, '2031-01-05T18:00Z', :u)"
        " RETURNING id",
        {"g": group.id, "st": _student_row(dsn), "u": user},
    )
    return enrollment.id


def test_0047_adds_the_cohort_enrollments_and_downgrade_drops_them(
    fresh_database_url,
):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0046")
    user, student = _user_row(dsn), _student_row(dsn)
    world = _cohort_world(dsn)
    [cohort] = run_sql(dsn, COHORT_INSERT, world)
    group_enrollment = _group_enrollment_row(dsn, world["subject"], user)
    [kept] = run_sql(
        dsn,
        "INSERT INTO class_session_overrides (group_enrollment_id, codes,"
        " conflicts, reason, actor_user_id) VALUES (:g,"
        " ARRAY['group_capacity_exceeded'], '[]'::jsonb, 'Motivo', :u)"
        " RETURNING id",
        {"g": group_enrollment, "u": user},
    )
    before = public_tables(dsn)

    command.upgrade(config, "0047")

    assert version_nums(dsn) == ["0047"]
    assert [d for d in metadata_diff(dsn) if not _of_0048_on(d)] == []
    assert public_tables(dsn) - before == set(COHORT_ENROLLMENT_TABLES)
    values = {"c": cohort.id, "st": student, "u": user}
    [enrollment] = run_sql(dsn, COHORT_ENROLLMENT_INSERT, values)
    [override] = run_sql(
        dsn,
        COHORT_OVERRIDE_INSERT,
        {"s": None, "g": None, "c": enrollment.id, "u": user},
    )

    command.downgrade(config, "0046")

    assert version_nums(dsn) == ["0046"]
    assert public_tables(dsn) == before
    assert "cohort_enrollment_id" not in _columns(dsn, "class_session_overrides")
    assert [d for d in metadata_diff(dsn) if not (_of_0047(d) or _of_0048_on(d))] == []
    remaining = {r.id for r in run_sql(dsn, "SELECT id FROM class_session_overrides")}
    assert remaining == {kept.id}
    assert override.id not in remaining
    # The 0041 CHECK is back: a row needs exactly one of the two subjects.
    with pytest.raises(IntegrityError, match="one_subject"):
        run_sql(
            dsn,
            "INSERT INTO class_session_overrides (codes, conflicts, reason,"
            " actor_user_id) VALUES (ARRAY['group_capacity_exceeded'],"
            " '[]'::jsonb, 'Motivo', :u)",
            {"u": user},
        )

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_cohort_enrollments_columns_constraints_and_indexes(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")
    table = "cohort_enrollments"
    assert _columns(fresh_database_url, table) == {
        **STANDARD_COLUMNS,
        "version": ("integer", "NO", None, "1"),
        "cohort_id": ("uuid", "NO", None, None),
        "student_id": ("uuid", "NO", None, None),
        "enrolled_at": ("timestamp with time zone", "NO", None, None),
        "enrolled_by_user_id": ("uuid", "NO", None, None),
        "withdrawn_at": ("timestamp with time zone", "YES", None, None),
        "withdrawn_by_user_id": ("uuid", "YES", None, None),
        "withdrawal_reason": ("character varying", "YES", 1000, None),
    }
    constraints = _constraints(fresh_database_url, table)
    foreign_keys = {
        "fk_cohort_enrollments_cohort_id_course_cohorts",
        "fk_cohort_enrollments_student_id_students",
        "fk_cohort_enrollments_enrolled_by_user_id_users",
        "fk_cohort_enrollments_withdrawn_by_user_id_users",
    }
    assert set(constraints) == {
        "pk_cohort_enrollments",
        "ck_cohort_enrollments_withdrawn_after_enrolled",
        "ck_cohort_enrollments_withdrawn_together",
        "ck_cohort_enrollments_withdrawal_reason_valid",
        *foreign_keys,
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(fresh_database_url, table)) == {
        "pk_cohort_enrollments",
        "ix_cohort_enrollments_cohort_id",
        "ix_cohort_enrollments_student_id",
        "uq_cohort_enrollments_active",
    }


def test_cohort_enrollment_rows_are_checked_and_written_once(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    user, student = _user_row(dsn), _student_row(dsn)
    world = _cohort_world(dsn)
    [cohort] = run_sql(dsn, COHORT_INSERT, world)
    values = {"c": cohort.id, "st": student, "u": user}
    [enrollment] = run_sql(dsn, COHORT_ENROLLMENT_INSERT, values)
    group_enrollment = _group_enrollment_row(dsn, world["subject"], user)

    # A4: a second active row; a withdrawal before the enrollment; one
    # without its actor; an override with two subjects.
    for statement, params, constraint in (
        (COHORT_ENROLLMENT_INSERT, values, "uq_cohort_enrollments_active"),
        (
            "UPDATE cohort_enrollments SET withdrawn_at = '2031-01-01T00:00Z',"
            " withdrawn_by_user_id = :u WHERE id = :id",
            {"u": user, "id": enrollment.id},
            "ck_cohort_enrollments_withdrawn_after_enrolled",
        ),
        (
            "UPDATE cohort_enrollments SET withdrawn_at = '2031-01-06T00:00Z'"
            " WHERE id = :id",
            {"id": enrollment.id},
            "ck_cohort_enrollments_withdrawn_together",
        ),
        (
            COHORT_OVERRIDE_INSERT,
            {"s": None, "g": group_enrollment, "c": enrollment.id, "u": user},
            "ck_class_session_overrides_one_subject",
        ),
        (
            COHORT_OVERRIDE_INSERT,
            {"s": None, "g": None, "c": None, "u": user},
            "ck_class_session_overrides_one_subject",
        ),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, statement, params)
    run_sql(
        dsn,
        COHORT_OVERRIDE_INSERT,
        {"s": None, "g": None, "c": enrollment.id, "u": user},
    )

    # The write-once trigger: one withdrawal, then nothing.
    for statement in (
        "UPDATE cohort_enrollments SET enrolled_at = '2031-01-01T00:00Z'"
        " WHERE id = :id",
        "DELETE FROM cohort_enrollments WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(dsn, statement, {"id": enrollment.id})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"
    run_sql(
        dsn,
        "UPDATE cohort_enrollments SET withdrawn_at = '2031-01-06T18:00Z',"
        " withdrawn_by_user_id = :u, withdrawal_reason = 'Mudanza',"
        " version = version + 1 WHERE id = :id",
        {"u": user, "id": enrollment.id},
    )
    for statement in (
        "UPDATE cohort_enrollments SET withdrawn_at = '2031-01-07T18:00Z'"
        " WHERE id = :id",
        "UPDATE cohort_enrollments SET withdrawal_reason = 'Otra' WHERE id = :id",
        "DELETE FROM cohort_enrollments WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(dsn, statement, {"id": enrollment.id})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"
    # Re-enrolling after the withdrawal is a new period.
    run_sql(dsn, COHORT_ENROLLMENT_INSERT, values)


# --- 0048: class_sessions.first_confirmed_at (#67 A1) --------------------------------


def test_0048_backfills_first_confirmed_at_and_downgrade_drops_it(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0047")
    subject = _subject_row(dsn)
    tutor = _user_row(dsn)
    day = datetime(2031, 1, 12, 16, tzinfo=UTC)
    rows = {}
    for index, state in enumerate(
        (
            "unassigned",
            "offered",
            "confirmed",
            "completed",
            "cancelled",
            "skipped_closure",
            "conflict",
        )
    ):
        values: dict[str, Any] = {
            "state": state,
            "starts_at": day + timedelta(days=index),
            "ends_at": day + timedelta(days=index, hours=1),
        }
        if state in {"offered", "confirmed", "completed"}:
            values["tutor_user_id"] = tutor
        if state == "cancelled":
            values["cancelled_at"] = day
            values["cancellation_reason"] = "Prueba"
        rows[state] = _session_row(dsn, subject, **values)
    updated = {
        uuid.UUID(str(row.id)): row.updated_at
        for row in run_sql(dsn, "SELECT id, updated_at FROM class_sessions")
    }

    command.upgrade(config, "0048")

    assert version_nums(dsn) == ["0048"]
    assert [d for d in metadata_diff(dsn) if not _of_0049_on(d)] == []
    found = {
        uuid.UUID(str(row.id)): row.first_confirmed_at
        for row in run_sql(dsn, "SELECT id, first_confirmed_at FROM class_sessions")
    }
    for state, session_id in rows.items():
        expected = updated[session_id] if state in {"confirmed", "completed"} else None
        assert found[session_id] == expected, state
    # The CHECK: confirmed and completed rows need it.
    for state in ("confirmed", "completed"):
        with pytest.raises(IntegrityError, match="confirmed_has_first_confirmed_at"):
            _session_row(
                dsn,
                subject,
                state=state,
                tutor_user_id=tutor,
                starts_at=day + timedelta(days=20),
                ends_at=day + timedelta(days=20, hours=1),
            )
    _session_row(
        dsn,
        subject,
        state="confirmed",
        tutor_user_id=tutor,
        starts_at=day + timedelta(days=21),
        ends_at=day + timedelta(days=21, hours=1),
        first_confirmed_at=day,
    )

    command.downgrade(config, "0047")

    assert version_nums(dsn) == ["0047"]
    assert "first_confirmed_at" not in _columns(dsn, "class_sessions")
    assert "ck_class_sessions_confirmed_has_first_confirmed_at" not in _constraints(
        dsn, "class_sessions"
    )
    assert [d for d in metadata_diff(dsn) if not _of_0048_on(d)] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


# --- 0049: attendance and its corrections (#76 A1) ------------------------------------

ATTENDANCE_INSERT = (
    "INSERT INTO class_session_attendance (class_session_id, student_id, status,"
    " marked_by_user_id, marked_at) VALUES (:s, :st, :status, :u, now())"
    " RETURNING id"
)
CORRECTION_INSERT = (
    "INSERT INTO class_session_attendance_corrections (attendance_id,"
    " previous_status, new_status, reason, corrected_by_user_id)"
    " VALUES (:a, :previous, :new, :reason, :u) RETURNING id"
)


def _attendance_world(dsn: str) -> dict[str, uuid.UUID]:
    """A session with one participant, a user and a student who isn't in
    it."""
    subject = _subject_row(dsn)
    session = _session_row(dsn, subject)
    student, outsider = _student_row(dsn), _student_row(dsn)
    run_sql(
        dsn,
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " VALUES (:s, :st)",
        {"s": session, "st": student},
    )
    return {
        "s": session,
        "st": student,
        "outsider": outsider,
        "u": _user_row(dsn),
    }


def test_0049_adds_the_attendance_tables_and_downgrade_drops_them(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0048")
    before = public_tables(dsn)

    command.upgrade(config, "0049")

    assert version_nums(dsn) == ["0049"]
    assert [d for d in metadata_diff(dsn) if not _of_0050_on(d)] == []
    assert public_tables(dsn) - before == set(ATTENDANCE_TABLES)
    world = _attendance_world(dsn)
    [mark] = run_sql(dsn, ATTENDANCE_INSERT, world | {"status": "present"})
    run_sql(
        dsn,
        CORRECTION_INSERT,
        {
            "a": mark.id,
            "previous": "present",
            "new": "late",
            "reason": "R",
            "u": world["u"],
        },
    )

    command.downgrade(config, "0048")

    assert version_nums(dsn) == ["0048"]
    assert public_tables(dsn) == before
    [function] = run_sql(
        dsn,
        "SELECT count(*) AS n FROM pg_proc"
        " WHERE proname = 'class_session_attendance_corrections_immutable'",
    )
    assert function.n == 0
    assert [d for d in metadata_diff(dsn) if not _of_0049_on(d)] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_attendance_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    standard = {k: v for k, v in STANDARD_COLUMNS.items() if k != "version"}

    assert _columns(dsn, "class_session_attendance") == {
        **standard,
        "class_session_id": ("uuid", "NO", None, None),
        "student_id": ("uuid", "NO", None, None),
        "status": ("character varying", "NO", 10, None),
        "marked_by_user_id": ("uuid", "NO", None, None),
        "marked_at": ("timestamp with time zone", "NO", None, None),
    }
    constraints = _constraints(dsn, "class_session_attendance")
    assert set(constraints) == {
        "pk_class_session_attendance",
        "ck_class_session_attendance_status_valid",
        "fk_class_session_attendance_participant",
        "fk_class_session_attendance_marked_by_user_id_users",
        "uq_class_session_attendance_session_student",
    }
    participant_fk = constraints["fk_class_session_attendance_participant"]
    assert "ON DELETE RESTRICT" in participant_fk
    assert (
        "FOREIGN KEY (class_session_id, student_id) REFERENCES"
        " class_session_participants(class_session_id, student_id)"
    ) in participant_fk
    assert set(_indexes(dsn, "class_session_attendance")) == {
        "pk_class_session_attendance",
        "uq_class_session_attendance_session_student",
        "ix_class_session_attendance_student_id_class_session_id",
    }

    table = "class_session_attendance_corrections"
    assert _columns(dsn, table) == {
        **standard,
        "attendance_id": ("uuid", "NO", None, None),
        "previous_status": ("character varying", "YES", 10, None),
        "new_status": ("character varying", "NO", 10, None),
        "reason": ("text", "NO", None, None),
        "corrected_by_user_id": ("uuid", "NO", None, None),
    }
    constraints = _constraints(dsn, table)
    foreign_keys = {
        "fk_attendance_corrections_attendance_id",
        "fk_attendance_corrections_corrected_by_user_id",
    }
    assert set(constraints) == {
        f"pk_{table}",
        f"ck_{table}_previous_status_valid",
        f"ck_{table}_new_status_valid",
        f"ck_{table}_status_changes",
        f"ck_{table}_reason_valid",
        *foreign_keys,
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(dsn, table)) == {f"pk_{table}", f"ix_{table}_attendance_id"}


def test_attendance_rows_are_checked_and_corrections_never_change(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    world = _attendance_world(dsn)

    # A1: the status set; a non-participant (composite key); a second row.
    with pytest.raises(IntegrityError, match="status_valid"):
        run_sql(dsn, ATTENDANCE_INSERT, world | {"status": "excused"})
    with pytest.raises(IntegrityError, match="fk_class_session_attendance_participant"):
        run_sql(
            dsn,
            ATTENDANCE_INSERT,
            world | {"st": world["outsider"], "status": "present"},
        )
    [mark] = run_sql(dsn, ATTENDANCE_INSERT, world | {"status": "present"})
    with pytest.raises(IntegrityError, match="uq_class_session_attendance_session"):
        run_sql(dsn, ATTENDANCE_INSERT, world | {"status": "late"})
    # A participant with attendance can't be removed (RESTRICT).
    with pytest.raises(IntegrityError, match="fk_class_session_attendance_participant"):
        run_sql(
            dsn,
            "DELETE FROM class_session_participants WHERE class_session_id = :s",
            {"s": world["s"]},
        )

    base = {"a": mark.id, "previous": "present", "new": "late", "u": world["u"]}
    for changes, constraint in (
        ({"reason": "   "}, "reason_valid"),
        ({"reason": " padded "}, "reason_valid"),
        ({"reason": "x" * 1001}, "reason_valid"),
        ({"reason": "R", "new": "present"}, "status_changes"),
        ({"reason": "R", "new": "excused"}, "new_status_valid"),
        ({"reason": "R", "previous": "excused"}, "previous_status_valid"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, CORRECTION_INSERT, base | changes)
    [correction] = run_sql(dsn, CORRECTION_INSERT, base | {"reason": "x" * 1000})
    run_sql(dsn, CORRECTION_INSERT, base | {"previous": None, "reason": "Primera"})

    # Append-only: UPDATE, DELETE and TRUNCATE raise 42501.
    for statement in (
        "UPDATE class_session_attendance_corrections SET reason = 'Otra'"
        " WHERE id = :id",
        "DELETE FROM class_session_attendance_corrections WHERE id = :id",
        "TRUNCATE class_session_attendance_corrections",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(dsn, statement, {"id": correction.id})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"
    # An attendance row with a correction can't be deleted.
    with pytest.raises(IntegrityError, match="fk_attendance_corrections_attendance_id"):
        run_sql(
            dsn, "DELETE FROM class_session_attendance WHERE id = :id", {"id": mark.id}
        )


# --- 0050: session closeouts (#77 A2) -------------------------------------------------

CLOSEOUT_INSERT_0050 = (
    "INSERT INTO session_closeouts (class_session_id, tutor_user_id, status,"
    " topics, performance, homework, next_steps, submitted_at)"
    " VALUES (:s, :u, :status, :topics, :performance, :homework, :next_steps,"
    " now()) RETURNING id"
)
CLOSEOUT_INSERT = (
    "INSERT INTO session_closeouts (class_session_id, tutor_user_id, status,"
    " topics, performance, homework, next_steps, submitted_at,"
    " last_submitted_at)"
    " VALUES (:s, :u, :status, :topics, :performance, :homework, :next_steps,"
    " now(), now()) RETURNING id"
)
"""At head (0051 adds ``last_submitted_at``)."""


def _closeout_values(dsn: str) -> dict[str, Any]:
    subject = _subject_row(dsn)
    return {
        "s": _session_row(dsn, subject),
        "u": _user_row(dsn),
        "status": "submitted",
        "topics": "Fracciones",
        "performance": "Bien",
        "homework": None,
        "next_steps": "Decimales",
    }


def test_0050_adds_the_closeout_table_and_downgrade_drops_it(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0049")
    before = public_tables(dsn)

    command.upgrade(config, "0050")

    assert version_nums(dsn) == ["0050"]
    assert [d for d in metadata_diff(dsn) if not _of_0051_on(d)] == []
    assert public_tables(dsn) - before == set(CLOSEOUT_TABLES)
    run_sql(dsn, CLOSEOUT_INSERT_0050, _closeout_values(dsn))

    command.downgrade(config, "0049")

    assert version_nums(dsn) == ["0049"]
    assert public_tables(dsn) == before
    assert [d for d in metadata_diff(dsn) if not _of_0050_on(d)] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_closeout_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    table = "session_closeouts"

    assert _columns(dsn, table) == {
        **STANDARD_COLUMNS,
        "class_session_id": ("uuid", "NO", None, None),
        "tutor_user_id": ("uuid", "NO", None, None),
        "status": ("character varying", "NO", 20, None),
        "topics": ("text", "YES", None, None),
        "performance": ("text", "YES", None, None),
        "homework": ("text", "YES", None, None),
        "next_steps": ("text", "YES", None, None),
        "submitted_at": ("timestamp with time zone", "NO", None, None),
        # #78 A1.
        "last_submitted_at": ("timestamp with time zone", "NO", None, None),
        "review_reason": ("text", "YES", None, None),
        "tutor_note": ("text", "YES", None, None),
        "current_approval_id": ("uuid", "YES", None, None),
        # #301 A1.
        "on_behalf_by_user_id": ("uuid", "YES", None, None),
        "on_behalf_reason": ("text", "YES", None, None),
    }
    constraints = _constraints(dsn, table)
    foreign_keys = {
        f"fk_{table}_class_session_id_class_sessions",
        f"fk_{table}_tutor_user_id_users",
        f"fk_{table}_current_approval_id",
        f"fk_{table}_on_behalf_by_user_id_users",
    }
    assert set(constraints) == {
        f"pk_{table}",
        f"uq_{table}_class_session_id",
        f"ck_{table}_status_valid",
        f"ck_{table}_topics_length",
        f"ck_{table}_performance_length",
        f"ck_{table}_homework_length",
        f"ck_{table}_next_steps_length",
        f"ck_{table}_review_reason_valid",
        f"ck_{table}_tutor_note_valid",
        f"ck_{table}_changes_requested_reason",
        f"ck_{table}_approved_has_approval",
        f"ck_{table}_on_behalf_reason_valid",
        f"ck_{table}_on_behalf_together",
        f"ck_{table}_on_behalf_not_tutor",
        *foreign_keys,
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert (
        "DEFERRABLE INITIALLY DEFERRED"
        in constraints[f"fk_{table}_current_approval_id"]
    )
    # A CHECK, not an enum type: #78 widened it.
    for status in ("'submitted'", "'changes_requested'", "'approved'"):
        assert status in constraints[f"ck_{table}_status_valid"]
    assert set(_indexes(dsn, table)) == {
        f"pk_{table}",
        f"uq_{table}_class_session_id",
        f"ix_{table}_tutor_user_id_submitted_at",
    }


def test_closeout_rows_are_checked(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    values = _closeout_values(dsn)

    for changes, constraint in (
        ({"status": "closed"}, "status_valid"),
        ({"topics": ""}, "topics_length"),
        ({"performance": "x" * 2001}, "performance_length"),
        ({"homework": ""}, "homework_length"),
        ({"next_steps": "x" * 2001}, "next_steps_length"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, CLOSEOUT_INSERT, values | changes)
    [row] = run_sql(
        dsn,
        CLOSEOUT_INSERT,
        values | {"topics": "x" * 2000, "performance": None, "next_steps": None},
    )
    # One closeout per session.
    with pytest.raises(IntegrityError, match="uq_session_closeouts_class_session_id"):
        run_sql(dsn, CLOSEOUT_INSERT, values)
    # A session with a closeout can't be deleted (RESTRICT).
    with pytest.raises(
        IntegrityError, match="fk_session_closeouts_class_session_id_class_sessions"
    ):
        run_sql(dsn, "DELETE FROM class_sessions WHERE id = :s", {"s": values["s"]})
    [version] = run_sql(
        dsn, "SELECT version FROM session_closeouts WHERE id = :id", {"id": row.id}
    )
    assert version.version == 1


# --- 0051: closeout review (#78 A1, I6) ----------------------------------------------

APPROVAL_INSERT = (
    "INSERT INTO session_closeout_approvals (closeout_id, revision,"
    " supersedes_approval_id, source, class_session_id, tutor_user_id,"
    " session_starts_at, session_kind, scheduled_minutes, approved_minutes,"
    " attendance, approved_by_user_id, approved_at)"
    " VALUES (:c, :revision, :supersedes, :source, :s, :u, now(), 'individual',"
    " 60, :minutes, '{}'::jsonb, :u, now()) RETURNING id"
)
CLOSEOUT_CORRECTION_INSERT = (
    "INSERT INTO session_closeout_corrections (closeout_id, fields,"
    " previous_minutes, new_minutes, reason, closeout_status, approval_id,"
    " corrected_by_user_id)"
    " VALUES (:c, CAST(:fields AS text[]), :previous, :new, :reason, :status,"
    " :approval, :u) RETURNING id"
)


def _review_world(dsn: str) -> dict[str, Any]:
    values = _closeout_values(dsn)
    [closeout] = run_sql(dsn, CLOSEOUT_INSERT, values)
    return {"c": closeout.id, "s": values["s"], "u": values["u"]}


def test_0051_adds_the_review_objects_and_downgrade_drops_them(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0050")
    before = public_tables(dsn)
    values = _closeout_values(dsn)
    [existing] = run_sql(dsn, CLOSEOUT_INSERT_0050, values)

    command.upgrade(config, "0051")

    assert version_nums(dsn) == ["0051"]
    assert [d for d in metadata_diff(dsn) if not _of_0052_on(d)] == []
    assert public_tables(dsn) - before == set(CLOSEOUT_REVIEW_TABLES)
    # last_submitted_at is backfilled from submitted_at.
    [row] = run_sql(
        dsn,
        "SELECT submitted_at, last_submitted_at, status, current_approval_id"
        " FROM session_closeouts WHERE id = :id",
        {"id": existing.id},
    )
    assert row.last_submitted_at == row.submitted_at
    assert (row.status, row.current_approval_id) == ("submitted", None)

    command.downgrade(config, "0050")

    assert version_nums(dsn) == ["0050"]
    assert public_tables(dsn) == before
    assert [d for d in metadata_diff(dsn) if not _of_0051_on(d)] == []
    assert not run_sql(
        dsn, "SELECT 1 FROM pg_proc WHERE proname LIKE 'session_closeout_%'"
    )

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_closeout_review_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    standard = {k: v for k, v in STANDARD_COLUMNS.items() if k != "version"}

    table = "session_closeout_approvals"
    assert _columns(dsn, table) == {
        **standard,
        "closeout_id": ("uuid", "NO", None, None),
        "revision": ("integer", "NO", None, None),
        "supersedes_approval_id": ("uuid", "YES", None, None),
        "source": ("character varying", "NO", 20, None),
        "class_session_id": ("uuid", "NO", None, None),
        "tutor_user_id": ("uuid", "NO", None, None),
        "session_starts_at": ("timestamp with time zone", "NO", None, None),
        "session_kind": ("character varying", "NO", 20, None),
        "scheduled_minutes": ("integer", "NO", None, None),
        "approved_minutes": ("integer", "NO", None, None),
        "attendance": ("jsonb", "NO", None, None),
        "approved_by_user_id": ("uuid", "NO", None, None),
        "approved_at": ("timestamp with time zone", "NO", None, None),
    }
    constraints = _constraints(dsn, table)
    foreign_keys = {
        f"fk_{table}_closeout_id_session_closeouts",
        f"fk_{table}_supersedes_approval_id",
        f"fk_{table}_class_session_id_class_sessions",
        f"fk_{table}_tutor_user_id_users",
        f"fk_{table}_approved_by_user_id_users",
    }
    assert set(constraints) == {
        f"pk_{table}",
        f"uq_{table}_revision",
        f"uq_{table}_supersedes_approval_id",
        f"ck_{table}_revision_positive",
        f"ck_{table}_revision_supersedes",
        f"ck_{table}_source_valid",
        f"ck_{table}_approved_minutes_range",
        *foreign_keys,
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(dsn, table)) == {
        f"pk_{table}",
        f"uq_{table}_revision",
        f"uq_{table}_supersedes_approval_id",
        f"ix_{table}_tutor_user_id_session_starts_at",
    }

    table = "session_closeout_corrections"
    assert _columns(dsn, table) == {
        **standard,
        "closeout_id": ("uuid", "NO", None, None),
        "fields": ("ARRAY", "NO", None, None),
        "previous_minutes": ("integer", "YES", None, None),
        "new_minutes": ("integer", "YES", None, None),
        "reason": ("text", "NO", None, None),
        "closeout_status": ("character varying", "NO", 20, None),
        "approval_id": ("uuid", "YES", None, None),
        "corrected_by_user_id": ("uuid", "NO", None, None),
    }
    constraints = _constraints(dsn, table)
    foreign_keys = {
        f"fk_{table}_closeout_id_session_closeouts",
        f"fk_{table}_approval_id",
        f"fk_{table}_corrected_by_user_id_users",
    }
    assert set(constraints) == {
        f"pk_{table}",
        f"ck_{table}_fields_valid",
        f"ck_{table}_fields_or_reapproval",
        f"ck_{table}_minutes_with_field",
        f"ck_{table}_new_minutes_range",
        f"ck_{table}_reason_valid",
        f"ck_{table}_closeout_status_valid",
        f"ck_{table}_approval_when_approved",
        *foreign_keys,
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(dsn, table)) == {
        f"pk_{table}",
        f"ix_{table}_closeout_id",
    }


def test_closeout_review_rows_are_checked(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    world = _review_world(dsn)
    closeout = {"c": world["c"]}

    # session_closeouts: the widened status set and its two CHECKs.
    for statement, constraint in (
        (
            "UPDATE session_closeouts SET status = 'closed' WHERE id = :c",
            "status_valid",
        ),
        (
            "UPDATE session_closeouts SET status = 'changes_requested' WHERE id = :c",
            "changes_requested_reason",
        ),
        (
            "UPDATE session_closeouts SET review_reason = ' x ' WHERE id = :c",
            "review_reason_valid",
        ),
        (
            "UPDATE session_closeouts SET tutor_note = repeat('x', 1001) WHERE id = :c",
            "tutor_note_valid",
        ),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, statement, closeout)
    # approved needs a current revision (checked at once) ...
    with pytest.raises(IntegrityError, match="approved_has_approval"):
        run_sql(
            dsn,
            "UPDATE session_closeouts SET status = 'approved' WHERE id = :c",
            closeout,
        )
    # ... and the key to it is deferred to the commit.
    with pytest.raises(
        IntegrityError, match="fk_session_closeouts_current_approval_id"
    ):
        run_sql(
            dsn,
            "UPDATE session_closeouts SET status = 'approved',"
            " current_approval_id = uuidv7() WHERE id = :c",
            closeout,
        )

    base = world | {
        "revision": 1,
        "supersedes": None,
        "source": "review",
        "minutes": 55,
    }
    for changes, constraint in (
        ({"revision": 0}, "revision_positive"),
        ({"revision": 2}, "revision_supersedes"),
        ({"source": "payroll"}, "source_valid"),
        ({"minutes": 721}, "approved_minutes_range"),
        ({"minutes": -1}, "approved_minutes_range"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, APPROVAL_INSERT, base | changes)
    [first] = run_sql(dsn, APPROVAL_INSERT, base | {"minutes": 720})
    with pytest.raises(IntegrityError, match="revision_supersedes"):
        run_sql(dsn, APPROVAL_INSERT, base | {"supersedes": first.id})
    with pytest.raises(IntegrityError, match="uq_session_closeout_approvals_revision"):
        run_sql(dsn, APPROVAL_INSERT, base)
    second = base | {
        "revision": 2,
        "supersedes": first.id,
        "source": "staff_correction",
    }
    run_sql(dsn, APPROVAL_INSERT, second)
    # Never two revisions superseding the same one (H2 backstop).
    with pytest.raises(
        IntegrityError, match="uq_session_closeout_approvals_supersedes_approval_id"
    ):
        run_sql(dsn, APPROVAL_INSERT, second | {"revision": 3})
    run_sql(
        dsn,
        "UPDATE session_closeouts SET status = 'approved', current_approval_id = :a"
        " WHERE id = :c",
        closeout | {"a": first.id},
    )

    correction = world | {
        "fields": ["actual_minutes"],
        "previous": 55,
        "new": 50,
        "reason": "Terminó antes",
        "status": "submitted",
        "approval": None,
    }
    for changes, constraint in (
        ({"fields": ["room"]}, "fields_valid"),
        ({"fields": [], "new": None}, "fields_or_reapproval"),
        ({"new": None}, "minutes_with_field"),
        ({"fields": ["topics"]}, "minutes_with_field"),
        ({"new": 721}, "new_minutes_range"),
        ({"reason": " "}, "reason_valid"),
        ({"reason": " padded "}, "reason_valid"),
        ({"reason": "x" * 1001}, "reason_valid"),
        ({"status": "changes_requested"}, "closeout_status_valid"),
        ({"status": "approved"}, "approval_when_approved"),
        ({"approval": first.id}, "approval_when_approved"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, CLOSEOUT_CORRECTION_INSERT, correction | changes)
    [fixed] = run_sql(
        dsn, CLOSEOUT_CORRECTION_INSERT, correction | {"reason": "x" * 1000}
    )
    # An attendance re-approval: no field, a revision.
    run_sql(
        dsn,
        CLOSEOUT_CORRECTION_INSERT,
        correction
        | {"fields": [], "new": None, "status": "approved", "approval": first.id},
    )

    # Append-only: UPDATE, DELETE and TRUNCATE raise 42501 on both tables.
    for statement, row_id in (
        (
            "UPDATE session_closeout_approvals SET approved_minutes = 1 WHERE id = :id",
            first.id,
        ),
        ("DELETE FROM session_closeout_approvals WHERE id = :id", first.id),
        ("TRUNCATE session_closeout_approvals CASCADE", first.id),
        (
            "UPDATE session_closeout_corrections SET reason = 'Otra' WHERE id = :id",
            fixed.id,
        ),
        ("DELETE FROM session_closeout_corrections WHERE id = :id", fixed.id),
        ("TRUNCATE session_closeout_corrections", fixed.id),
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(dsn, statement, {"id": row_id})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"
    # A closeout with revisions and corrections can't be deleted (RESTRICT).
    run_sql(
        dsn,
        "UPDATE session_closeouts SET current_approval_id = NULL,"
        " status = 'submitted' WHERE id = :c",
        closeout,
    )
    with pytest.raises(IntegrityError, match="fk_session_closeout_"):
        run_sql(dsn, "DELETE FROM session_closeouts WHERE id = :c", closeout)


# --- 0052: closeout on behalf (#301 A1, H6) -------------------------------------------


def test_0052_adds_the_on_behalf_objects_and_downgrade_drops_them(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0051")
    before = public_tables(dsn)
    [existing] = run_sql(dsn, CLOSEOUT_INSERT, _closeout_values(dsn))

    command.upgrade(config, "0052")

    assert version_nums(dsn) == ["0052"]
    assert [d for d in metadata_diff(dsn) if not _of_0053_on(d)] == []
    assert public_tables(dsn) == before
    assert OVERDUE_INDEX in _indexes(dsn, "class_sessions")
    assert (
        "WHERE ((state)::text = 'confirmed'::text)"
        in (_indexes(dsn, "class_sessions")[OVERDUE_INDEX])
    )
    [row] = run_sql(
        dsn,
        "SELECT on_behalf_by_user_id, on_behalf_reason FROM session_closeouts"
        " WHERE id = :id",
        {"id": existing.id},
    )
    assert (row.on_behalf_by_user_id, row.on_behalf_reason) == (None, None)

    command.downgrade(config, "0051")

    assert version_nums(dsn) == ["0051"]
    assert [d for d in metadata_diff(dsn) if not _of_0052_on(d)] == []
    assert OVERDUE_INDEX not in _indexes(dsn, "class_sessions")
    assert not {
        name for name in _constraints(dsn, "session_closeouts") if "on_behalf" in name
    }

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_closeout_on_behalf_rows_are_checked(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    world = _review_world(dsn)
    staff = _user_row(dsn)
    closeout = {"c": world["c"], "staff": staff, "tutor": world["u"]}
    statement = (
        "UPDATE session_closeouts SET on_behalf_by_user_id = :by,"
        " on_behalf_reason = :reason WHERE id = :c"
    )

    for by, reason, constraint in (
        (staff, None, "on_behalf_together"),
        (None, "Sin tutor", "on_behalf_together"),
        (staff, " ", "on_behalf_reason_valid"),
        (staff, " padded ", "on_behalf_reason_valid"),
        (staff, "x" * 1001, "on_behalf_reason_valid"),
        (world["u"], "Sin tutor", "on_behalf_not_tutor"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, statement, closeout | {"by": by, "reason": reason})
    with pytest.raises(
        IntegrityError, match="fk_session_closeouts_on_behalf_by_user_id_users"
    ):
        run_sql(dsn, statement, closeout | {"by": uuid.uuid4(), "reason": "x"})
    run_sql(dsn, statement, closeout | {"by": staff, "reason": "x" * 1000})
    # The reviewer's account can't be deleted while it is referenced.
    with pytest.raises(
        IntegrityError, match="fk_session_closeouts_on_behalf_by_user_id_users"
    ):
        run_sql(dsn, "DELETE FROM users WHERE id = :u", {"u": staff})
    run_sql(dsn, statement, closeout | {"by": None, "reason": None})


# --- 0053: session notes (#79 A1) -----------------------------------------------------

NOTE_INSERT = (
    "INSERT INTO session_notes (class_session_id, student_id, author_user_id,"
    " body, visibility, published_at, published_by_user_id, unpublished_at,"
    " unpublished_by_user_id, unpublish_reason, deleted_at, deleted_by_user_id)"
    " VALUES (:s, :st, :u, :body, :visibility, :published_at, :published_by,"
    " :unpublished_at, :unpublished_by, :reason, :deleted_at, :deleted_by)"
    " RETURNING id, visibility, version"
)


def _note_values(dsn: str) -> dict[str, Any]:
    student = _student_row(dsn)
    session = _session_row(dsn, _subject_row(dsn))
    run_sql(
        dsn,
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " VALUES (:s, :st)",
        {"s": session, "st": student},
    )
    return {
        "s": session,
        "st": student,
        "u": _user_row(dsn),
        "body": "Trajo la calculadora",
        "visibility": "internal",
        "published_at": None,
        "published_by": None,
        "unpublished_at": None,
        "unpublished_by": None,
        "reason": None,
        "deleted_at": None,
        "deleted_by": None,
    }


def test_0053_adds_the_notes_table_and_downgrade_drops_it(fresh_database_url):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0052")
    before = public_tables(dsn)

    command.upgrade(config, "0053")

    assert version_nums(dsn) == ["0053"]
    assert [d for d in metadata_diff(dsn) if not _of_0054_on(d)] == []
    assert public_tables(dsn) - before == set(NOTE_TABLES)
    run_sql(dsn, NOTE_INSERT, _note_values(dsn))

    command.downgrade(config, "0052")

    assert version_nums(dsn) == ["0052"]
    assert public_tables(dsn) == before
    assert [d for d in metadata_diff(dsn) if not _of_0053_on(d)] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_session_notes_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    table = "session_notes"
    timestamp = ("timestamp with time zone", "YES", None, None)

    assert _columns(dsn, table) == {
        **STANDARD_COLUMNS,
        "class_session_id": ("uuid", "NO", None, None),
        "student_id": ("uuid", "NO", None, None),
        "author_user_id": ("uuid", "NO", None, None),
        "body": ("text", "NO", None, None),
        "visibility": ("character varying", "NO", 10, "'internal'::character varying"),
        "published_at": timestamp,
        "published_by_user_id": ("uuid", "YES", None, None),
        "unpublished_at": timestamp,
        "unpublished_by_user_id": ("uuid", "YES", None, None),
        "unpublish_reason": ("text", "YES", None, None),
        "body_updated_at": timestamp,
        "deleted_at": timestamp,
        "deleted_by_user_id": ("uuid", "YES", None, None),
    }
    constraints = _constraints(dsn, table)
    foreign_keys = {
        f"fk_{table}_class_session_id_class_sessions",
        f"fk_{table}_student_id_students",
        f"fk_{table}_author_user_id_users",
        f"fk_{table}_published_by_user_id_users",
        f"fk_{table}_unpublished_by_user_id_users",
        f"fk_{table}_deleted_by_user_id_users",
    }
    assert set(constraints) == {
        f"pk_{table}",
        f"ck_{table}_body_valid",
        f"ck_{table}_visibility_valid",
        f"ck_{table}_published_together",
        f"ck_{table}_unpublished_together",
        f"ck_{table}_unpublish_reason_valid",
        f"ck_{table}_deleted_together",
        f"ck_{table}_deleted_internal",
        *foreign_keys,
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    indexes = _indexes(dsn, table)
    assert set(indexes) == {
        f"pk_{table}",
        "ix_session_notes_student_id_created_at",
        "ix_session_notes_class_session_id",
    }
    for name in (
        "ix_session_notes_student_id_created_at",
        "ix_session_notes_class_session_id",
    ):
        assert "WHERE (deleted_at IS NULL)" in indexes[name]


def test_session_note_rows_are_checked(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    values = _note_values(dsn)
    actor = values["u"]
    published = {
        "visibility": "family",
        "published_at": datetime.now(UTC),
        "published_by": actor,
    }
    unpublished = {"unpublished_at": datetime.now(UTC), "unpublished_by": actor}
    deleted = {"deleted_at": datetime.now(UTC), "deleted_by": actor}

    for changes, constraint in (
        ({"body": ""}, "body_valid"),
        ({"body": " padded "}, "body_valid"),
        ({"body": "x" * 4001}, "body_valid"),
        ({"visibility": "public"}, "visibility_valid"),
        ({"visibility": "family"}, "published_together"),
        (
            {"published_at": datetime.now(UTC), "published_by": actor},
            "published_together",
        ),
        (published | {"published_by": None}, "published_together"),
        ({"unpublished_at": datetime.now(UTC)}, "unpublished_together"),
        ({"reason": "Dato médico"}, "unpublish_reason_valid"),
        (unpublished | {"reason": " x "}, "unpublish_reason_valid"),
        (unpublished | {"reason": "x" * 501}, "unpublish_reason_valid"),
        ({"deleted_at": datetime.now(UTC)}, "deleted_together"),
        (published | deleted, "deleted_internal"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, NOTE_INSERT, values | changes)
    [note] = run_sql(dsn, NOTE_INSERT, values | {"body": "x" * 4000})
    assert (note.visibility, note.version) == ("internal", 1)
    run_sql(dsn, NOTE_INSERT, values | published)
    run_sql(dsn, NOTE_INSERT, values | unpublished | {"reason": "x" * 500})
    run_sql(dsn, NOTE_INSERT, values | unpublished | deleted)
    # Visibility defaults to internal.
    [row] = run_sql(
        dsn,
        "INSERT INTO session_notes (class_session_id, student_id, author_user_id,"
        " body) VALUES (:s, :st, :u, 'Hola') RETURNING visibility",
        values,
    )
    assert row.visibility == "internal"
    # A referenced author can't be deleted.
    with pytest.raises(IntegrityError, match="fk_session_notes_author_user_id_users"):
        run_sql(dsn, "DELETE FROM users WHERE id = :u", values)


# --- 0054: evaluations (#80 A1) -------------------------------------------------------

TEMPLATE_INSERT = (
    "INSERT INTO evaluation_templates (name, kind, subject_id, description)"
    " VALUES (:name, :kind, :subject, :description) RETURNING id, is_active, version"
)
CRITERION_INSERT = (
    "INSERT INTO evaluation_template_criteria (template_id, position, title,"
    " description, max_score) VALUES (:t, :position, :title, :description, :max)"
)
EVALUATION_INSERT = (
    "INSERT INTO evaluations (student_id, subject_id, author_user_id, template_id,"
    " template_version, template_name, kind, evaluated_on, class_session_id,"
    " cohort_id, course_assessment_id, course_assessment_title, status, feedback,"
    " score_total, max_total, score_percent, finalized_at, last_change_reason,"
    " voided_at, voided_by_user_id, void_reason, deleted_at, deleted_by_user_id)"
    " VALUES (:st, :sub, :u, :t, 1, :tname, :kind, :on, :session, :cohort,"
    " :assessment, :atitle, :status, :feedback, :total, :max_total, :percent,"
    " :finalized_at, :reason, :voided_at, :voided_by, :void_reason, :deleted_at,"
    " :deleted_by) RETURNING id, status, version"
)
SCORE_INSERT = (
    "INSERT INTO evaluation_scores (evaluation_id, position, criterion_title,"
    " criterion_description, max_score, score, comment)"
    " VALUES (:e, :position, :title, :description, :max, :score, :comment)"
)


def _template_values(dsn: str) -> dict[str, Any]:
    return {
        "name": f"Plantilla {uuid.uuid4().hex[:8]}",
        "kind": "diagnostic",
        "subject": _subject_row(dsn),
        "description": None,
    }


def _evaluation_values(dsn: str) -> dict[str, Any]:
    template = _template_values(dsn)
    [row] = run_sql(dsn, TEMPLATE_INSERT, template)
    return {
        "st": _student_row(dsn),
        "sub": template["subject"],
        "u": _user_row(dsn),
        "t": row.id,
        "tname": template["name"],
        "kind": "ongoing",
        "on": date(2031, 3, 5),
        "session": None,
        "cohort": None,
        "assessment": None,
        "atitle": None,
        "status": "draft",
        "feedback": None,
        "total": None,
        "max_total": None,
        "percent": None,
        "finalized_at": None,
        "reason": None,
        "voided_at": None,
        "voided_by": None,
        "void_reason": None,
        "deleted_at": None,
        "deleted_by": None,
    }


def test_0054_adds_the_evaluation_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0053")
    before = public_tables(dsn)

    command.upgrade(config, "0054")

    assert version_nums(dsn) == ["0054"]
    assert [d for d in metadata_diff(dsn) if not _of_0055_on(d)] == []
    assert public_tables(dsn) - before == set(EVALUATION_TABLES)
    run_sql(dsn, EVALUATION_INSERT, _evaluation_values(dsn))

    command.downgrade(config, "0053")

    assert version_nums(dsn) == ["0053"]
    assert public_tables(dsn) == before
    assert [d for d in metadata_diff(dsn) if not _of_0054_on(d)] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_evaluation_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    unversioned = {k: v for k, v in STANDARD_COLUMNS.items() if k != "version"}
    timestamp = ("timestamp with time zone", "YES", None, None)
    money = ("numeric", "NO", None, None)

    assert _columns(dsn, "evaluation_templates") == {
        **STANDARD_COLUMNS,
        "name": ("character varying", "NO", 120, None),
        "kind": ("character varying", "NO", 20, None),
        "subject_id": ("uuid", "YES", None, None),
        "description": ("character varying", "YES", 2000, None),
        "is_active": ("boolean", "NO", None, "true"),
    }
    assert _columns(dsn, "evaluation_template_criteria") == {
        **unversioned,
        "template_id": ("uuid", "NO", None, None),
        "position": ("smallint", "NO", None, None),
        "title": ("character varying", "NO", 120, None),
        "description": ("character varying", "YES", 500, None),
        "max_score": money,
    }
    assert _columns(dsn, "evaluations") == {
        **STANDARD_COLUMNS,
        "student_id": ("uuid", "NO", None, None),
        "subject_id": ("uuid", "NO", None, None),
        "author_user_id": ("uuid", "NO", None, None),
        "template_id": ("uuid", "NO", None, None),
        "template_version": ("integer", "NO", None, None),
        "template_name": ("character varying", "NO", 120, None),
        "kind": ("character varying", "NO", 20, None),
        "evaluated_on": ("date", "NO", None, None),
        "class_session_id": ("uuid", "YES", None, None),
        "cohort_id": ("uuid", "YES", None, None),
        "course_assessment_id": ("uuid", "YES", None, None),
        "course_assessment_title": ("character varying", "YES", 200, None),
        "status": ("character varying", "NO", 10, "'draft'::character varying"),
        "feedback": ("text", "YES", None, None),
        "score_total": ("numeric", "YES", None, None),
        "max_total": ("numeric", "YES", None, None),
        "score_percent": ("numeric", "YES", None, None),
        "finalized_at": timestamp,
        "amended_at": timestamp,
        "last_change_reason": ("text", "YES", None, None),
        "voided_at": timestamp,
        "voided_by_user_id": ("uuid", "YES", None, None),
        "void_reason": ("text", "YES", None, None),
        "deleted_at": timestamp,
        "deleted_by_user_id": ("uuid", "YES", None, None),
    }
    assert _columns(dsn, "evaluation_scores") == {
        **unversioned,
        "evaluation_id": ("uuid", "NO", None, None),
        "position": ("smallint", "NO", None, None),
        "criterion_title": ("character varying", "NO", 120, None),
        "criterion_description": ("character varying", "YES", 500, None),
        "max_score": money,
        "score": ("numeric", "YES", None, None),
        "comment": ("text", "YES", None, None),
    }
    evaluation_fks = {
        "fk_evaluations_student_id_students",
        "fk_evaluations_subject_id_subjects",
        "fk_evaluations_author_user_id_users",
        "fk_evaluations_template_id_evaluation_templates",
        "fk_evaluations_class_session_id_class_sessions",
        "fk_evaluations_cohort_id_course_cohorts",
        "fk_evaluations_voided_by_user_id_users",
        "fk_evaluations_deleted_by_user_id_users",
    }
    constraints = _constraints(dsn, "evaluations")
    assert set(constraints) == {
        "pk_evaluations",
        *evaluation_fks,
        *(
            f"ck_evaluations_{name}"
            for name in (
                "kind_valid",
                "status_valid",
                "template_name_valid",
                "course_together",
                "course_needs_session",
                "course_assessment_title_valid",
                "feedback_valid",
                "score_percent_range",
                "final_totals",
                "last_change_reason_valid",
                "voided_together",
                "void_reason_valid",
                "voided_final",
                "deleted_together",
                "deleted_draft",
            )
        ),
    }
    for name in evaluation_fks:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert "course_assessment_id" not in " ".join(
        constraints[name] for name in evaluation_fks
    )
    criteria = _constraints(dsn, "evaluation_template_criteria")
    assert (
        "DEFERRABLE INITIALLY DEFERRED"
        in (criteria["uq_evaluation_template_criteria_position"])
    )
    assert (
        'UNIQUE (evaluation_id, "position")'
        in (_constraints(dsn, "evaluation_scores")["uq_evaluation_scores_position"])
    )
    indexes = _indexes(dsn, "evaluations")
    assert set(indexes) == {
        "pk_evaluations",
        "ix_evaluations_student_id_evaluated_on",
        "ix_evaluations_student_id_created_at",
        "ix_evaluations_author_user_id_drafts",
        "ix_evaluations_cohort_id_course_assessment_id",
    }
    assert (
        "WHERE (((status)::text = 'final'::text) AND (voided_at IS NULL))"
        in (indexes["ix_evaluations_student_id_evaluated_on"])
    )
    assert (
        "WHERE (deleted_at IS NULL)" in indexes["ix_evaluations_student_id_created_at"]
    )
    assert (
        "lower((name)::text)"
        in (_indexes(dsn, "evaluation_templates")["uq_evaluation_templates_name_ci"])
    )


def test_evaluation_rows_are_checked(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    template = _template_values(dsn)
    for changes, constraint in (
        ({"name": " x "}, "evaluation_templates_name_valid"),
        ({"name": ""}, "evaluation_templates_name_valid"),
        ({"kind": "quiz"}, "kind_valid"),
        ({"description": ""}, "description_valid"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, TEMPLATE_INSERT, template | changes)
    [made] = run_sql(dsn, TEMPLATE_INSERT, template)
    assert (made.is_active, made.version) == (True, 1)
    with pytest.raises(IntegrityError, match="uq_evaluation_templates_name_ci"):
        run_sql(dsn, TEMPLATE_INSERT, template | {"name": template["name"].upper()})
    criterion = {
        "t": made.id,
        "position": 1,
        "title": "Lectura",
        "description": None,
        "max": Decimal("10"),
    }
    for changes, constraint in (
        ({"position": 0}, "position_range"),
        ({"position": 21}, "position_range"),
        ({"title": " x"}, "title_valid"),
        ({"max": Decimal("0.49")}, "max_score_range"),
        ({"max": Decimal("1000.01")}, "max_score_range"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, CRITERION_INSERT, criterion | changes)
    run_sql(dsn, CRITERION_INSERT, criterion | {"max": Decimal("0.5")})

    values = _evaluation_values(dsn)
    now = datetime.now(UTC)
    final = {
        "status": "final",
        "total": Decimal("7"),
        "max_total": Decimal("10"),
        "percent": Decimal("70"),
        "finalized_at": now,
    }
    voided = {"voided_at": now, "voided_by": values["u"], "void_reason": "Error"}
    deleted = {"deleted_at": now, "deleted_by": values["u"]}
    for changes, constraint in (
        ({"status": "sent"}, "status_valid"),
        ({"kind": "quiz"}, "kind_valid"),
        ({"status": "final"}, "final_totals"),
        (final | {"percent": None}, "final_totals"),
        (final | {"status": "draft"}, "final_totals"),
        (final | {"percent": Decimal("100.01")}, "score_percent_range"),
        ({"feedback": "x" * 4001}, "feedback_valid"),
        ({"reason": "x" * 501}, "last_change_reason_valid"),
        (final | {"voided_at": now}, "voided_together"),
        (final | voided | {"void_reason": ""}, "void_reason_valid"),
        (voided, "voided_final"),
        ({"deleted_at": now}, "deleted_together"),
        (final | deleted, "deleted_draft"),
        ({"assessment": uuid.uuid4()}, "course_together"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, EVALUATION_INSERT, values | changes)
    [draft] = run_sql(dsn, EVALUATION_INSERT, values)
    assert (draft.status, draft.version) == ("draft", 1)
    run_sql(dsn, EVALUATION_INSERT, values | final | voided)
    run_sql(dsn, EVALUATION_INSERT, values | deleted)
    score = {
        "e": draft.id,
        "position": 1,
        "title": "Lectura",
        "description": None,
        "max": Decimal("10"),
        "score": None,
        "comment": None,
    }
    for changes, constraint in (
        ({"score": Decimal("10.01")}, "score_range"),
        ({"score": Decimal("-1")}, "score_range"),
        ({"comment": ""}, "comment_valid"),
        ({"position": 21}, "position_range"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, SCORE_INSERT, score | changes)
    run_sql(dsn, SCORE_INSERT, score | {"score": Decimal("10")})
    with pytest.raises(IntegrityError, match="uq_evaluation_scores_position"):
        run_sql(dsn, SCORE_INSERT, score)
    # A referenced author or template can't be deleted.
    with pytest.raises(IntegrityError, match="fk_evaluations_author_user_id_users"):
        run_sql(dsn, "DELETE FROM users WHERE id = :u", values)
    with pytest.raises(IntegrityError, match="fk_evaluations_template_id"):
        run_sql(dsn, "DELETE FROM evaluation_templates WHERE id = :t", values)


# --- 0055: learning plans (#81 A1) ----------------------------------------------------

PLAN_INSERT = (
    "INSERT INTO learning_plans (student_id, subject_id, created_by_user_id,"
    " title, description, starts_on, target_on, status, activated_at,"
    " closed_at, deleted_at, deleted_by_user_id)"
    " VALUES (:st, :sub, :u, :title, :description, :starts_on, :target_on,"
    " :status, :activated_at, :closed_at, :deleted_at, :deleted_by)"
    " RETURNING id, status, version"
)
OBJECTIVE_INSERT = (
    "INSERT INTO learning_plan_objectives (plan_id, position, title, description)"
    " VALUES (:p, :position, :title, :description) RETURNING id"
)
MILESTONE_INSERT = (
    "INSERT INTO learning_plan_milestones (plan_id, objective_id, position,"
    " title, activities, due_on, status, completed_at, completed_by_user_id)"
    " VALUES (:p, :o, :position, :title, :activities, :due_on, :status,"
    " :completed_at, :completed_by) RETURNING id, status, version"
)


def _plan_values(dsn: str) -> dict[str, Any]:
    return {
        "st": _student_row(dsn),
        "sub": _subject_row(dsn),
        "u": _user_row(dsn),
        "title": "Lectura",
        "description": None,
        "starts_on": date(2031, 3, 5),
        "target_on": None,
        "status": "draft",
        "activated_at": None,
        "closed_at": None,
        "deleted_at": None,
        "deleted_by": None,
    }


def test_0055_adds_the_learning_plan_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0054")
    before = public_tables(dsn)

    command.upgrade(config, "0055")

    assert version_nums(dsn) == ["0055"]
    assert [d for d in metadata_diff(dsn) if not _of_0056(d)] == []
    assert public_tables(dsn) - before == set(LEARNING_PLAN_TABLES)
    run_sql(dsn, PLAN_INSERT, _plan_values(dsn))

    command.downgrade(config, "0054")

    assert version_nums(dsn) == ["0054"]
    assert public_tables(dsn) == before
    assert [d for d in metadata_diff(dsn) if not _of_0055_on(d)] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_learning_plan_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    unversioned = {k: v for k, v in STANDARD_COLUMNS.items() if k != "version"}
    timestamp = ("timestamp with time zone", "YES", None, None)

    assert _columns(dsn, "learning_plans") == {
        **STANDARD_COLUMNS,
        "student_id": ("uuid", "NO", None, None),
        "subject_id": ("uuid", "NO", None, None),
        "created_by_user_id": ("uuid", "NO", None, None),
        "title": ("character varying", "NO", 120, None),
        "description": ("text", "YES", None, None),
        "starts_on": ("date", "NO", None, None),
        "target_on": ("date", "YES", None, None),
        "status": ("character varying", "NO", 20, "'draft'::character varying"),
        "activated_at": timestamp,
        "closed_at": timestamp,
        "deleted_at": timestamp,
        "deleted_by_user_id": ("uuid", "YES", None, None),
    }
    assert _columns(dsn, "learning_plan_objectives") == {
        **unversioned,
        "plan_id": ("uuid", "NO", None, None),
        "position": ("smallint", "NO", None, None),
        "title": ("character varying", "NO", 200, None),
        "description": ("text", "YES", None, None),
    }
    assert _columns(dsn, "learning_plan_milestones") == {
        **STANDARD_COLUMNS,
        "plan_id": ("uuid", "NO", None, None),
        "objective_id": ("uuid", "NO", None, None),
        "position": ("smallint", "NO", None, None),
        "title": ("character varying", "NO", 200, None),
        "activities": ("text", "YES", None, None),
        "due_on": ("date", "YES", None, None),
        "status": ("character varying", "NO", 20, "'pending'::character varying"),
        "completed_at": timestamp,
        "completed_by_user_id": ("uuid", "YES", None, None),
    }
    plan_fks = {
        "fk_learning_plans_student_id_students",
        "fk_learning_plans_subject_id_subjects",
        "fk_learning_plans_created_by_user_id_users",
        "fk_learning_plans_deleted_by_user_id_users",
    }
    constraints = _constraints(dsn, "learning_plans")
    assert set(constraints) == {
        "pk_learning_plans",
        *plan_fks,
        *(
            f"ck_learning_plans_{name}"
            for name in (
                "title_valid",
                "description_valid",
                "target_after_start",
                "status_valid",
                "draft_not_activated",
                "active_activated",
                "closed_together",
                "deleted_together",
                "deleted_draft",
            )
        ),
    }
    milestone_constraints = _constraints(dsn, "learning_plan_milestones")
    for name in plan_fks:
        assert "ON DELETE RESTRICT" in constraints[name]
    for name in (
        "fk_learning_plan_milestones_plan_id_learning_plans",
        "fk_learning_plan_milestones_objective_id",
        "fk_learning_plan_milestones_completed_by_user_id_users",
    ):
        assert "ON DELETE RESTRICT" in milestone_constraints[name]
    assert (
        "DEFERRABLE INITIALLY DEFERRED"
        in milestone_constraints["uq_learning_plan_milestones_position"]
    )
    assert (
        "DEFERRABLE INITIALLY DEFERRED"
        in _constraints(dsn, "learning_plan_objectives")[
            "uq_learning_plan_objectives_position"
        ]
    )
    indexes = _indexes(dsn, "learning_plans")
    assert set(indexes) == {
        "pk_learning_plans",
        "uq_learning_plans_student_id_subject_id_active",
        "ix_learning_plans_student_id_created_at",
    }
    active = indexes["uq_learning_plans_student_id_subject_id_active"]
    assert "UNIQUE" in active
    assert (
        "WHERE (((status)::text = 'active'::text) AND (deleted_at IS NULL))" in active
    )
    assert set(_indexes(dsn, "learning_plan_milestones")) >= {
        "ix_learning_plan_milestones_plan_id",
        "ix_learning_plan_milestones_objective_id",
    }


def test_learning_plan_rows_are_checked(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    values = _plan_values(dsn)
    now = datetime.now(UTC)
    for changes, constraint in (
        ({"title": " x "}, "learning_plans_title_valid"),
        ({"title": ""}, "learning_plans_title_valid"),
        ({"title": "x" * 121}, "value too long"),
        ({"description": ""}, "description_valid"),
        ({"description": "x" * 2001}, "description_valid"),
        ({"target_on": date(2031, 3, 4)}, "target_after_start"),
        ({"status": "paused"}, "status_valid"),
        ({"activated_at": now}, "draft_not_activated"),
        ({"status": "active"}, "active_activated"),
        ({"status": "completed", "closed_at": now}, "active_activated"),
        (
            {"status": "active", "activated_at": now, "closed_at": now},
            "closed_together",
        ),
        ({"status": "discontinued"}, "closed_together"),
        ({"deleted_at": now}, "deleted_together"),
        (
            {
                "status": "discontinued",
                "closed_at": now,
                "deleted_at": now,
                "deleted_by": values["u"],
            },
            "deleted_draft",
        ),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            run_sql(dsn, PLAN_INSERT, values | changes)
    [draft] = run_sql(dsn, PLAN_INSERT, values)
    assert (draft.status, draft.version) == ("draft", 1)
    # A discontinued plan may or may not have been activated.
    run_sql(dsn, PLAN_INSERT, values | {"status": "discontinued", "closed_at": now})
    active = {"status": "active", "activated_at": now}
    run_sql(dsn, PLAN_INSERT, values | active)
    with pytest.raises(
        IntegrityError, match="uq_learning_plans_student_id_subject_id_active"
    ):
        run_sql(dsn, PLAN_INSERT, values | active)
    # A deleted draft and a completed plan don't count.
    run_sql(dsn, PLAN_INSERT, values | {"deleted_at": now, "deleted_by": values["u"]})
    run_sql(
        dsn,
        PLAN_INSERT,
        values | active | {"status": "completed", "closed_at": now},
    )

    objective = {"p": draft.id, "position": 1, "title": "Leer", "description": None}
    for changes, constraint in (
        ({"position": 0}, "position_range"),
        ({"position": 11}, "position_range"),
        ({"title": " x"}, "title_valid"),
        ({"description": "x" * 1001}, "description_valid"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, OBJECTIVE_INSERT, objective | changes)
    [made] = run_sql(dsn, OBJECTIVE_INSERT, objective)
    with pytest.raises(IntegrityError, match="uq_learning_plan_objectives_position"):
        run_sql(dsn, OBJECTIVE_INSERT, objective)
    milestone = {
        "p": draft.id,
        "o": made.id,
        "position": 1,
        "title": "Capítulo 1",
        "activities": None,
        "due_on": None,
        "status": "pending",
        "completed_at": None,
        "completed_by": None,
    }
    done = {"status": "done", "completed_at": now, "completed_by": values["u"]}
    for changes, constraint in (
        ({"position": 11}, "position_range"),
        ({"title": ""}, "title_valid"),
        ({"activities": ""}, "activities_valid"),
        ({"activities": "x" * 2001}, "activities_valid"),
        ({"status": "late"}, "status_valid"),
        ({"status": "done"}, "done_together"),
        (done | {"completed_by": None}, "done_together"),
        (done | {"status": "pending"}, "done_together"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, MILESTONE_INSERT, milestone | changes)
    [first] = run_sql(dsn, MILESTONE_INSERT, milestone | done)
    assert (first.status, first.version) == ("done", 1)
    with pytest.raises(IntegrityError, match="uq_learning_plan_milestones_position"):
        run_sql(dsn, MILESTONE_INSERT, milestone)
    # A referenced plan, objective or creator can't be deleted.
    with pytest.raises(IntegrityError, match="fk_learning_plan_milestones_objective"):
        run_sql(
            dsn, "DELETE FROM learning_plan_objectives WHERE id = :id", {"id": made.id}
        )
    with pytest.raises(IntegrityError, match="fk_learning_plans_created_by_user_id"):
        run_sql(dsn, "DELETE FROM users WHERE id = :u", values)


# --- 0056: homework assignments (#82 A1) ----------------------------------------------

ASSIGNMENT_INSERT = (
    "INSERT INTO assignments (creator_user_id, subject_id, class_session_id,"
    " title, instructions, due_on, due_time, max_score, deleted_at,"
    " deleted_by_user_id)"
    " VALUES (:u, :sub, :cs, :title, :instructions, :due_on, :due_time,"
    " :max_score, :deleted_at, :deleted_by) RETURNING id, version"
)
SUBMISSION_INSERT = (
    "INSERT INTO assignment_submissions (assignment_id, student_id, text, url,"
    " submitted_at, submitted_by_user_id, submitted_via, score, score_percent,"
    " feedback, graded_at, graded_on, graded_by_user_id, grade_amended_at,"
    " last_change_reason)"
    " VALUES (:a, :st, :text, :url, :submitted_at, :submitted_by, :via, :score,"
    " :percent, :feedback, :graded_at, :graded_on, :graded_by, :amended_at,"
    " :reason) RETURNING id, version"
)


def _assignment_values(dsn: str) -> dict[str, Any]:
    return {
        "u": _user_row(dsn),
        "sub": _subject_row(dsn),
        "cs": None,
        "title": "Lectura",
        "instructions": "Leer el capítulo 3",
        "due_on": date(2031, 3, 6),
        "due_time": None,
        "max_score": None,
        "deleted_at": None,
        "deleted_by": None,
    }


def _submission_values(assignment: Any, student: Any) -> dict[str, Any]:
    return {
        "a": assignment,
        "st": student,
        "text": None,
        "url": None,
        "submitted_at": None,
        "submitted_by": None,
        "via": None,
        "score": None,
        "percent": None,
        "feedback": None,
        "graded_at": None,
        "graded_on": None,
        "graded_by": None,
        "amended_at": None,
        "reason": None,
    }


def test_0056_adds_the_assignment_tables_and_downgrade_drops_them(
    fresh_database_url,
):
    dsn = fresh_database_url
    config = alembic_config(dsn)
    command.upgrade(config, "0055")
    before = public_tables(dsn)

    command.upgrade(config, "0056")

    assert version_nums(dsn) == ["0056"]
    assert metadata_diff(dsn) == []
    assert public_tables(dsn) - before == set(ASSIGNMENT_TABLES)
    [made] = run_sql(dsn, ASSIGNMENT_INSERT, _assignment_values(dsn))
    run_sql(dsn, SUBMISSION_INSERT, _submission_values(made.id, _student_row(dsn)))

    command.downgrade(config, "0055")

    assert version_nums(dsn) == ["0055"]
    assert public_tables(dsn) == before
    assert [d for d in metadata_diff(dsn) if not _of_0056(d)] == []

    command.upgrade(config, "head")
    assert version_nums(dsn) == [HEAD]
    assert metadata_diff(dsn) == []


def test_assignment_columns_constraints_and_indexes(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    timestamp = ("timestamp with time zone", "YES", None, None)

    assert _columns(dsn, "assignments") == {
        **STANDARD_COLUMNS,
        "creator_user_id": ("uuid", "NO", None, None),
        "subject_id": ("uuid", "NO", None, None),
        "class_session_id": ("uuid", "YES", None, None),
        "title": ("character varying", "NO", 200, None),
        "instructions": ("text", "NO", None, None),
        "due_on": ("date", "NO", None, None),
        "due_time": ("time without time zone", "YES", None, None),
        "max_score": ("numeric", "YES", None, None),
        "deleted_at": timestamp,
        "deleted_by_user_id": ("uuid", "YES", None, None),
    }
    assert _columns(dsn, "assignment_submissions") == {
        **STANDARD_COLUMNS,
        "assignment_id": ("uuid", "NO", None, None),
        "student_id": ("uuid", "NO", None, None),
        "text": ("text", "YES", None, None),
        "url": ("character varying", "YES", 2000, None),
        "submitted_at": timestamp,
        "submitted_by_user_id": ("uuid", "YES", None, None),
        "submitted_via": ("character varying", "YES", 10, None),
        "score": ("numeric", "YES", None, None),
        "score_percent": ("numeric", "YES", None, None),
        "feedback": ("text", "YES", None, None),
        "graded_at": timestamp,
        "graded_on": ("date", "YES", None, None),
        "graded_by_user_id": ("uuid", "YES", None, None),
        "grade_amended_at": timestamp,
        "last_change_reason": ("text", "YES", None, None),
    }
    assignment_fks = {
        "fk_assignments_creator_user_id_users",
        "fk_assignments_subject_id_subjects",
        "fk_assignments_class_session_id_class_sessions",
        "fk_assignments_deleted_by_user_id_users",
    }
    constraints = _constraints(dsn, "assignments")
    assert set(constraints) == {
        "pk_assignments",
        *assignment_fks,
        *(
            f"ck_assignments_{name}"
            for name in (
                "title_valid",
                "instructions_valid",
                "max_score_range",
                "deleted_together",
            )
        ),
    }
    for name in assignment_fks:
        assert "ON DELETE RESTRICT" in constraints[name]
    submission_fks = {
        "fk_assignment_submissions_assignment_id_assignments",
        "fk_assignment_submissions_student_id_students",
        "fk_assignment_submissions_submitted_by_user_id_users",
        "fk_assignment_submissions_graded_by_user_id_users",
    }
    submission_constraints = _constraints(dsn, "assignment_submissions")
    assert set(submission_constraints) == {
        "pk_assignment_submissions",
        "uq_assignment_submissions_assignment_id_student_id",
        *submission_fks,
        *(
            f"ck_assignment_submissions_{name}"
            for name in (
                "text_valid",
                "url_valid",
                "submitted_via_valid",
                "submitted_together",
                "content_needs_submission",
                "score_range",
                "score_percent_range",
                "feedback_valid",
                "graded_needs_submission",
                "graded_together",
                "score_together",
                "grade_needs_graded",
                "last_change_reason_valid",
                "amended_needs_graded",
            )
        ),
    }
    for name in submission_fks:
        assert "ON DELETE RESTRICT" in submission_constraints[name]
    indexes = _indexes(dsn, "assignments")
    assert set(indexes) == {
        "pk_assignments",
        "ix_assignments_creator_user_id_due_on",
        "ix_assignments_class_session_id",
    }
    assert (
        "WHERE (deleted_at IS NULL)" in indexes["ix_assignments_creator_user_id_due_on"]
    )
    submission_indexes = _indexes(dsn, "assignment_submissions")
    assert set(submission_indexes) == {
        "pk_assignment_submissions",
        "uq_assignment_submissions_assignment_id_student_id",
        "ix_assignment_submissions_assignment_id",
        "ix_assignment_submissions_student_id_assignment_id",
        "ix_assignment_submissions_student_id_graded_on",
    }
    assert (
        "WHERE (score IS NOT NULL)"
        in submission_indexes["ix_assignment_submissions_student_id_graded_on"]
    )


def test_assignment_rows_are_checked(fresh_database_url):
    dsn = fresh_database_url
    command.upgrade(alembic_config(dsn), "head")
    values = _assignment_values(dsn)
    now = datetime.now(UTC)
    for changes, constraint in (
        ({"title": " x"}, "assignments_title_valid"),
        ({"title": ""}, "assignments_title_valid"),
        ({"title": "x" * 201}, "value too long"),
        ({"instructions": ""}, "instructions_valid"),
        ({"instructions": "x" * 4001}, "instructions_valid"),
        ({"max_score": Decimal("0.4")}, "max_score_range"),
        ({"max_score": Decimal("1000.01")}, "max_score_range"),
        ({"deleted_at": now}, "deleted_together"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            run_sql(dsn, ASSIGNMENT_INSERT, values | changes)
    [made] = run_sql(
        dsn,
        ASSIGNMENT_INSERT,
        values | {"max_score": Decimal("10"), "due_time": time(16, 0)},
    )
    assert made.version == 1
    student = _student_row(dsn)
    row = _submission_values(made.id, student)
    submitted = {"submitted_at": now, "submitted_by": values["u"], "via": "student"}
    graded = {"graded_at": now, "graded_on": date(2031, 3, 6), "graded_by": values["u"]}
    for changes, constraint in (
        ({"text": "x"}, "content_needs_submission"),
        ({"url": "https://example.com"}, "content_needs_submission"),
        (submitted | {"text": ""}, "text_valid"),
        (submitted | {"text": "x" * 10001}, "text_valid"),
        (submitted | {"url": ""}, "url_valid"),
        (submitted | {"via": "guardian"}, "submitted_via_valid"),
        ({"submitted_at": now}, "submitted_together"),
        (submitted | {"via": None}, "submitted_together"),
        (graded, "graded_needs_submission"),
        (submitted | graded | {"graded_on": None}, "graded_together"),
        (submitted | graded | {"score": Decimal(1)}, "score_together"),
        (
            submitted | graded | {"score": Decimal(-1), "percent": Decimal(0)},
            "score_range",
        ),
        (
            submitted | graded | {"score": Decimal(1), "percent": Decimal(101)},
            "score_percent_range",
        ),
        (submitted | graded | {"feedback": ""}, "feedback_valid"),
        (submitted | {"feedback": "Bien"}, "grade_needs_graded"),
        (
            submitted | {"score": Decimal(1), "percent": Decimal(10)},
            "grade_needs_graded",
        ),
        (submitted | graded | {"reason": " x"}, "last_change_reason_valid"),
        (submitted | graded | {"reason": "x" * 501}, "last_change_reason_valid"),
        (submitted | {"amended_at": now}, "amended_needs_graded"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            run_sql(dsn, SUBMISSION_INSERT, row | changes)
    [first] = run_sql(
        dsn,
        SUBMISSION_INSERT,
        row
        | submitted
        | graded
        | {
            "text": "Mi respuesta",
            "score": Decimal("7.5"),
            "percent": Decimal("75"),
            "feedback": "Bien",
            "amended_at": now,
            "reason": "Corrección",
        },
    )
    assert first.version == 1
    with pytest.raises(
        IntegrityError, match="uq_assignment_submissions_assignment_id_student_id"
    ):
        run_sql(dsn, SUBMISSION_INSERT, row)
    # A referenced assignment, student or creator can't be deleted.
    with pytest.raises(IntegrityError, match="fk_assignment_submissions_assignment_id"):
        run_sql(dsn, "DELETE FROM assignments WHERE id = :id", {"id": made.id})
    with pytest.raises(IntegrityError, match="fk_assignment_submissions_student_id"):
        run_sql(dsn, "DELETE FROM students WHERE id = :id", {"id": student})
    with pytest.raises(IntegrityError, match="fk_assignments_creator_user_id"):
        run_sql(dsn, "DELETE FROM users WHERE id = :u", values)
