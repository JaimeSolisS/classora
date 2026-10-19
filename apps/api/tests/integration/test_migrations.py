"""Alembic history (M1-07 C5-C8, M1-16 B, M2-02 D4, M2-05 A3, M2-07 B2, B3,
M2-08 A1, A2, M2-09 A, M2-11 B, M2-13 A, M2-14 A, M2-15 A); each test uses a fresh
database."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
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


HEAD = "0018"

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


def test_alembic_ini_has_no_database_url():
    ini = (API_ROOT / "alembic.ini").read_text()

    assert "sqlalchemy.url" not in ini
    assert "postgresql" not in ini


def test_upgrade_empty_database_to_head(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert version_nums(fresh_database_url) == [HEAD]
    # Beyond plpgsql, only the ISSUE-D6 name-search extensions (#33):
    # usernames are normalized and CHECKed, not citext.
    extensions = run_sql(
        fresh_database_url, "SELECT extname FROM pg_extension ORDER BY extname"
    )
    assert [row.extname for row in extensions] == ["pg_trgm", "plpgsql", "unaccent"]
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
            *AUTH_TABLES,
            *CATALOG_TABLES,
            *CONSENT_TABLES,
            *FAMILY_TABLES,
            *GUARDIAN_TABLES,
            *GROUPING_TABLES,
            *IDENTITY_TABLES,
            *PRIVACY_TABLES,
            *PROCRASTINATE_TABLES,
            *RATE_LIMIT_TABLES,
            *REGISTRATION_TABLES,
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
