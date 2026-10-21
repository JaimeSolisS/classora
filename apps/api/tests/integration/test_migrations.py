"""Alembic history (M1-07 C5-C8, M1-16 B, M2-02 D4, M2-05 A3, M2-07 B2, B3,
M2-08 A1, A2, M2-09 A, M2-11 B, M2-13 A, M2-14 A, M2-15 A, M3-01 A, M3-02 A,
M3-03 A, M3-04 A, M3-05 A, M3-09 A, M4-02 A, M4-16 A, M4-19 B); each test uses a
fresh
database."""

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
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


HEAD = "0034"

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
            *OVERRIDE_TABLES,
            *OFFER_TABLES,
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
    # course tables, 0033 class_session_overrides, 0034 the offer tables)
    # differ.
    assert [
        diff
        for diff in metadata_diff(fresh_database_url)
        if not _of_later_revision(
            diff, [*POLICY_TABLES, *COURSE_TABLES, *OVERRIDE_TABLES, *OFFER_TABLES]
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


def _of_later_revision(diff: Any, tables: list[str]) -> bool:
    """A metadata difference that is a table (or one of its indexes) a later
    revision adds."""
    if diff[0] == "add_table":
        return diff[1].name in tables
    if diff[0] == "add_index":
        return diff[1].table.name in tables
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
    }
    constraints = _constraints(fresh_database_url, table)
    checks = {
        f"ck_{table}_{name}"
        for name in (
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
        )
    }
    foreign_keys = {
        f"fk_{table}_subject_id_subjects",
        f"fk_{table}_tutor_user_id_users",
        f"fk_{table}_classroom_id_classrooms",
    }
    assert set(constraints) == {
        f"pk_{table}",
        *checks,
        *foreign_keys,
        "ex_class_sessions_tutor_overlap",
        "ex_class_sessions_room_overlap",
    }
    for name in foreign_keys:
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(fresh_database_url, table)) == {
        f"pk_{table}",
        f"ix_{table}_subject_id",
        f"ix_{table}_tutor_user_id",
        f"ix_{table}_classroom_id",
        f"ix_{table}_starts_at",
        "ex_class_sessions_tutor_overlap",
        "ex_class_sessions_room_overlap",
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
    # the offer tables) differ.
    assert [
        diff
        for diff in metadata_diff(fresh_database_url)
        if not _of_later_revision(diff, [*OVERRIDE_TABLES, *OFFER_TABLES])
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
    # Only the tables of a later revision (0034 the offer tables) differ.
    assert [
        diff
        for diff in metadata_diff(fresh_database_url)
        if not _of_later_revision(diff, OFFER_TABLES)
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
        "class_session_id": ("uuid", "NO", None, None),
        "codes": ("ARRAY", "NO", None, None),
        "conflicts": ("jsonb", "NO", None, None),
        "reason": ("text", "NO", None, None),
        "actor_user_id": ("uuid", "NO", None, None),
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        "pk_class_session_overrides",
        "fk_class_session_overrides_class_session_id_class_sessions",
        "fk_class_session_overrides_actor_user_id_users",
        "ck_class_session_overrides_codes_soft",
        "ck_class_session_overrides_reason_length",
    }
    for name in (
        "fk_class_session_overrides_class_session_id_class_sessions",
        "fk_class_session_overrides_actor_user_id_users",
    ):
        assert "ON DELETE RESTRICT" in constraints[name]
    assert set(_indexes(fresh_database_url, table)) == {
        "pk_class_session_overrides",
        "ix_class_session_overrides_class_session_id",
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
    assert metadata_diff(fresh_database_url) == []

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
    }
    constraints = _constraints(fresh_database_url, table)
    assert set(constraints) == {
        "pk_assignment_offers",
        "fk_assignment_offers_tutor_user_id_users",
        "fk_assignment_offers_offered_by_user_id_users",
        "ck_assignment_offers_scope_valid",
        "ck_assignment_offers_status_valid",
        "ck_assignment_offers_respond_by_after_offered",
        "ck_assignment_offers_responded_iff_answered",
        "ck_assignment_offers_decline_note_only_declined",
    }
    for name in (
        "fk_assignment_offers_tutor_user_id_users",
        "fk_assignment_offers_offered_by_user_id_users",
    ):
        assert "ON DELETE RESTRICT" in constraints[name]
    indexes = _indexes(fresh_database_url, table)
    assert set(indexes) == {
        "pk_assignment_offers",
        "ix_assignment_offers_tutor_user_id_status",
        "ix_assignment_offers_pending_respond_by",
    }
    assert (
        "WHERE ((status)::text = 'pending'::text)"
        in indexes["ix_assignment_offers_pending_respond_by"]
    )

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
    } | columns
    [row] = run_sql(
        dsn,
        "INSERT INTO assignment_offers (scope, tutor_user_id, status, offered_at,"
        " offered_by_user_id, respond_by, responded_at, decline_note)"
        " VALUES (:scope, :u, :status, :offered_at, :u,"
        " :respond_by, :responded_at,"
        " :decline_note) RETURNING id",
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
        ({"scope": "series"}, "scope_valid"),
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
