"""Alembic history (M1-07 C5-C8, M1-16 B): each test migrates its own fresh database."""

import asyncio
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


HEAD = "0002"

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


def test_alembic_ini_has_no_database_url():
    ini = (API_ROOT / "alembic.ini").read_text()

    assert "sqlalchemy.url" not in ini
    assert "postgresql" not in ini


def test_upgrade_empty_database_to_head(fresh_database_url):
    command.upgrade(alembic_config(fresh_database_url), "head")

    assert version_nums(fresh_database_url) == [HEAD]
    # The Procrastinate schema needs no extension beyond plpgsql.
    extensions = run_sql(fresh_database_url, "SELECT extname FROM pg_extension")
    assert [row.extname for row in extensions] == ["plpgsql"]
    tables = run_sql(
        fresh_database_url,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name",
    )
    assert [row.table_name for row in tables] == [
        "alembic_version",
        *PROCRASTINATE_TABLES,
    ]


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
    # Closed port: migrating through DATABASE_URL would fail.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@127.0.0.1:1/unused")
    monkeypatch.setenv("DATABASE_DIRECT_URL", fresh_database_url)

    command.upgrade(alembic_config(), "head")

    assert version_nums(fresh_database_url) == [HEAD]


def test_offline_mode_is_refused(fresh_database_url):
    with pytest.raises(SystemExit):
        command.upgrade(alembic_config(fresh_database_url), "head", sql=True)
