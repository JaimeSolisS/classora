"""Alembic history (M1-07 C5-C8, M1-16 B, M2-02 D4, M2-05 A3, M2-07 B2, B3,
M2-08 A1, A2); each test uses a fresh database."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

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


HEAD = "0007"

IDENTITY_TABLES = [
    "permissions",
    "role_permissions",
    "roles",
    "user_delegations",
    "user_roles",
    "users",
]

AUTH_TABLES = ["auth_sessions"]

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


def test_alembic_ini_has_no_database_url():
    ini = (API_ROOT / "alembic.ini").read_text()

    assert "sqlalchemy.url" not in ini
    assert "postgresql" not in ini


def test_upgrade_empty_database_to_head(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert version_nums(fresh_database_url) == [HEAD]
    # Neither the Procrastinate schema nor identity needs an extension beyond
    # plpgsql (usernames are normalized and CHECKed, not citext).
    extensions = run_sql(fresh_database_url, "SELECT extname FROM pg_extension")
    assert [row.extname for row in extensions] == ["plpgsql"]
    tables = run_sql(
        fresh_database_url,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name",
    )
    assert [row.table_name for row in tables] == sorted(
        [
            "alembic_version",
            "audit_events",
            *AUTH_TABLES,
            *IDENTITY_TABLES,
            *PROCRASTINATE_TABLES,
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
    command.upgrade(config, "head")
    assert public_tables(fresh_database_url) - before == set(AUTH_TABLES)

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
