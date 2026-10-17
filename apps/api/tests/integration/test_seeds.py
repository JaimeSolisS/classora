"""Reference seeds against PostgreSQL (M1-17 B2, B3, E2)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.seeds import runner
from app.seeds.registry import UpsertSeed
from app.seeds.upsert import UpsertResult
from tests.integration.conftest import run_sql, run_sql_async, settings_for
from tests.integration.models import SeedReference, VersionedSeedReference
from tests.integration.samples import TEST_REFERENCE_ROWS, TEST_REFERENCE_SEED
from tests.integration.test_migrations import alembic_config


class SeedFailed(Exception):
    pass


class FailingSeed:
    name = "failing"

    async def apply(self, session: AsyncSession) -> UpsertResult:
        raise SeedFailed()


async def snapshot(dsn: str, table: str = "seed_references") -> dict[str, Any]:
    """Rows by code: (id, label, note/version, created_at, updated_at)."""
    extra = "version" if table.startswith("versioned") else "note"
    rows = await run_sql_async(
        dsn,
        f"SELECT code, id, label, {extra} AS extra, created_at, updated_at "
        f"FROM {table} ORDER BY code",
    )
    return {row.code: row for row in rows}


@pytest.fixture
def migrated_url(tables_url: str) -> str:
    """The test-only tables, with the database at the Alembic head."""
    command.upgrade(alembic_config(tables_url), "head")
    return tables_url


@pytest.fixture
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    seed_engine = runner.create_seed_engine(settings_for(migrated_url))
    yield seed_engine
    await seed_engine.dispose()


@pytest.fixture
def env_for(monkeypatch):
    def _set(dsn: str) -> None:
        monkeypatch.setenv("APP_ENV", "prod")  # reference seeds run everywhere
        monkeypatch.setenv("DATABASE_URL", dsn)
        monkeypatch.setenv("DATABASE_DIRECT_URL", dsn)

    return _set


# --- Idempotency, change, no delete --------------------------------------------


@pytest.mark.anyio
async def test_second_run_changes_nothing(engine, migrated_url):
    first = await runner.run_reference_seeds(engine, [TEST_REFERENCE_SEED])
    before = await snapshot(migrated_url)
    second = await runner.run_reference_seeds(engine, [TEST_REFERENCE_SEED])
    after = await snapshot(migrated_url)

    assert len(after) == len(TEST_REFERENCE_ROWS)
    assert [row.id for row in after.values()] == [row.id for row in before.values()]
    assert [row.updated_at for row in after.values()] == [
        row.updated_at for row in before.values()
    ]
    assert after == before
    assert first == [("test_references", UpsertResult(inserted=3, updated=0))]
    assert second == [("test_references", UpsertResult(inserted=0, updated=0))]
    assert all(row.id.version == 7 for row in after.values())


@pytest.mark.anyio
async def test_changed_label_updates_one_row_and_keeps_its_id(engine, migrated_url):
    await runner.run_reference_seeds(engine, [TEST_REFERENCE_SEED])
    before = await snapshot(migrated_url)
    changed = UpsertSeed(
        name="test_references",
        model=SeedReference,
        rows=[
            {**row, "label": "Beta nueva"} if row["code"] == "beta" else row
            for row in TEST_REFERENCE_ROWS
        ],
    )

    result = await runner.run_reference_seeds(engine, [changed])
    after = await snapshot(migrated_url)

    assert result == [("test_references", UpsertResult(inserted=0, updated=1))]
    assert after["beta"].label == "Beta nueva"
    assert after["beta"].id == before["beta"].id
    assert after["beta"].created_at == before["beta"].created_at
    assert after["beta"].updated_at > before["beta"].updated_at
    assert after["alpha"] == before["alpha"]
    assert after["gamma"] == before["gamma"]


@pytest.mark.anyio
async def test_versioned_change_increments_version_by_one(engine, migrated_url):
    rows = [{"code": "a", "label": "Uno"}, {"code": "b", "label": "Dos"}]
    seed = UpsertSeed(name="versioned", model=VersionedSeedReference, rows=rows)
    table = "versioned_seed_references"
    await runner.run_reference_seeds(engine, [seed])
    await runner.run_reference_seeds(engine, [seed])  # unchanged: no new version
    before = await snapshot(migrated_url, table)

    changed = UpsertSeed(
        name="versioned",
        model=VersionedSeedReference,
        rows=[rows[0], {"code": "b", "label": "Dos nuevo"}],
    )
    result = await runner.run_reference_seeds(engine, [changed])
    after = await snapshot(migrated_url, table)

    assert result == [("versioned", UpsertResult(inserted=0, updated=1))]
    assert before["a"].extra == before["b"].extra == 1
    assert after["b"].extra == 2
    assert after["b"].id == before["b"].id
    assert after["b"].updated_at > before["b"].updated_at
    assert after["a"] == before["a"]


@pytest.mark.anyio
async def test_rows_and_columns_outside_the_seed_are_left_alone(engine, migrated_url):
    await runner.run_reference_seeds(engine, [TEST_REFERENCE_SEED])
    await run_sql_async(
        migrated_url, "UPDATE seed_references SET note = 'nota' WHERE code = 'alpha'"
    )
    before = await snapshot(migrated_url)
    smaller = UpsertSeed(
        name="test_references",
        model=SeedReference,
        rows=[{"code": "alpha", "label": "Alfa nueva"}],  # no beta, no gamma
    )

    result = await runner.run_reference_seeds(engine, [smaller])
    after = await snapshot(migrated_url)

    assert result == [("test_references", UpsertResult(inserted=0, updated=1))]
    assert set(after) == {"alpha", "beta", "gamma"}
    assert after["alpha"].label == "Alfa nueva"
    assert after["alpha"].extra == "nota"  # note: not listed by the seed
    assert after["beta"] == before["beta"]
    assert after["gamma"] == before["gamma"]


# --- Atomicity and head check --------------------------------------------------


@pytest.mark.anyio
async def test_a_failing_seed_rolls_back_every_seed(engine, migrated_url):
    with pytest.raises(SeedFailed):
        await runner.run_reference_seeds(engine, [TEST_REFERENCE_SEED, FailingSeed()])

    rows = await run_sql_async(migrated_url, "SELECT count(*) FROM seed_references")
    assert rows[0][0] == 0


def test_not_at_head_refuses_and_writes_nothing(tables_url, env_for, capsys):
    # The test tables exist, but Alembic never ran (no alembic_version).
    env_for(tables_url)

    assert runner.main(seeds=[TEST_REFERENCE_SEED]) == 1

    captured = capsys.readouterr()
    assert captured.err.strip() == runner.NOT_AT_HEAD_MESSAGE
    assert "make migrate" in captured.err
    assert captured.out == ""
    assert run_sql(tables_url, "SELECT count(*) FROM seed_references")[0][0] == 0


def test_empty_database_is_refused(fresh_database_url, env_for, capsys):
    env_for(fresh_database_url)

    assert runner.main() == 1

    assert "make migrate" in capsys.readouterr().err
    tables = run_sql(
        fresh_database_url,
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'",
    )
    assert tables[0][0] == 0


def test_pending_migration_is_refused(migrated_url, env_for, capsys, monkeypatch):
    env_for(migrated_url)
    monkeypatch.setattr(runner, "alembic_heads", lambda: {"0002"})

    assert runner.main(seeds=[TEST_REFERENCE_SEED]) == 1

    assert "make migrate" in capsys.readouterr().err
    assert run_sql(migrated_url, "SELECT count(*) FROM seed_references")[0][0] == 0


# --- Command ---------------------------------------------------------------------


def test_command_with_an_empty_registry(migrated_url, env_for, capsys):
    env_for(migrated_url)

    assert runner.main() == 0
    assert runner.main() == 0

    out = capsys.readouterr().out
    assert out.splitlines() == ["No reference seeds are registered."] * 2


def test_command_prints_counts_and_never_values(migrated_url, env_for, capsys):
    env_for(migrated_url)

    assert runner.main(seeds=[TEST_REFERENCE_SEED]) == 0
    assert runner.main(seeds=[TEST_REFERENCE_SEED]) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "test_references: 3 inserted, 0 updated",
        "test_references: 0 inserted, 0 updated",
    ]
    assert "Alfa" not in out


def test_unexpected_seed_error_exits_non_zero(migrated_url, env_for):
    env_for(migrated_url)

    with pytest.raises(SeedFailed):
        runner.main(seeds=[TEST_REFERENCE_SEED, FailingSeed()])

    assert run_sql(migrated_url, "SELECT count(*) FROM seed_references")[0][0] == 0
