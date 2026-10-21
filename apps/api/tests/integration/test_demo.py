"""Demo-data command against PostgreSQL (M1-17 C5, E2)."""

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncSession

from tests.authz.csrf import origin_for
from tests.factories import demo
from tests.factories.base import persist_batch
from tests.integration.conftest import run_sql
from tests.integration.samples import TEST_REFERENCE_SEED, SampleProfileFactory
from tests.integration.test_migrations import alembic_config


class SampleProfiles:
    name = "sample_profiles"

    async def generate(self, session: AsyncSession) -> int:
        return len(await persist_batch(session, SampleProfileFactory, 4))


class GeneratorFailed(Exception):
    pass


class FailingGenerator:
    name = "failing"

    async def generate(self, session: AsyncSession) -> int:
        raise GeneratorFailed()


@pytest.fixture
def migrated_url(tables_url: str) -> str:
    command.upgrade(alembic_config(tables_url), "head")
    return tables_url


@pytest.fixture
def env_for(monkeypatch):
    def _set(app_env: str, dsn: str) -> None:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("APP_ORIGIN", origin_for(app_env))
        monkeypatch.setenv("DATABASE_URL", dsn)
        monkeypatch.setenv("DATABASE_DIRECT_URL", dsn)

    return _set


def counts(dsn: str) -> tuple[int, int]:
    rows = run_sql(
        dsn,
        "SELECT (SELECT count(*) FROM seed_references),"
        " (SELECT count(*) FROM sample_profiles)",
    )
    return rows[0][0], rows[0][1]


def test_local_writes_reference_seeds_and_generated_rows(migrated_url, env_for, capsys):
    env_for("local", migrated_url)

    code = demo.main(
        ["--seed", "3"], seeds=[TEST_REFERENCE_SEED], generators=[SampleProfiles()]
    )

    assert code == 0
    assert counts(migrated_url) == (3, 4)
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "test_references: 3 inserted, 0 updated",
        "sample_profiles: 4 rows",
    ]
    emails = run_sql(migrated_url, "SELECT email FROM sample_profiles")
    assert all(row.email not in out for row in emails)


def test_same_seed_generates_the_same_data(migrated_url, env_for):
    env_for("dev", migrated_url)
    query = "SELECT full_name, email, amount FROM sample_profiles ORDER BY created_at"

    assert demo.main(["--seed", "5"], generators=[SampleProfiles()]) == 0
    first = run_sql(migrated_url, query)
    run_sql(migrated_url, "DELETE FROM sample_profiles")
    assert demo.main(["--seed", "5"], generators=[SampleProfiles()]) == 0
    second = run_sql(migrated_url, query)

    assert sorted(first) == sorted(second)


def test_without_generators_only_reference_seeds_run(migrated_url, env_for, capsys):
    env_for("local", migrated_url)

    assert demo.main([], seeds=[TEST_REFERENCE_SEED], generators=()) == 0

    assert counts(migrated_url) == (3, 0)
    assert capsys.readouterr().out.splitlines() == [
        "test_references: 3 inserted, 0 updated",
        "No demo generators are registered.",
    ]


def test_registered_command_applies_the_registered_reference_seeds(
    migrated_url, env_for, capsys
):
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "identity.permissions: 25 inserted, 0 updated",
        "identity.roles: 7 inserted, 0 updated",
        "identity.role_permissions: 60 inserted, 0 updated",
        "catalog.education_levels: 4 inserted, 0 updated",
        "identity.demo_owner: 1 rows",
        "identity.demo_admin: 1 rows",
        "identity.demo_coordinator: 1 rows",
        "identity.demo_finance: 1 rows",
        "identity.demo_tutor: 1 rows",
        "identity.demo_student: 1 rows",
        "identity.demo_guardian: 1 rows",
        "families.demo_families: 34 rows",
    ]


def test_nothing_registered(migrated_url, env_for, capsys):
    env_for("local", migrated_url)

    assert demo.main([], seeds=(), generators=()) == 0

    assert capsys.readouterr().out.splitlines() == [
        "No reference seeds are registered.",
        "No demo generators are registered.",
    ]


def test_prod_is_refused_and_writes_nothing(migrated_url, env_for, capsys):
    env_for("prod", migrated_url)

    code = demo.main([], seeds=[TEST_REFERENCE_SEED], generators=[SampleProfiles()])

    assert code == 2
    assert "Refusing to generate demo data: APP_ENV=prod" in capsys.readouterr().err
    assert counts(migrated_url) == (0, 0)


def test_a_failing_generator_leaves_nothing_written(migrated_url, env_for):
    env_for("local", migrated_url)

    with pytest.raises(GeneratorFailed):
        demo.main(
            [],
            seeds=[TEST_REFERENCE_SEED],
            generators=[SampleProfiles(), FailingGenerator()],
        )

    assert counts(migrated_url) == (0, 0)


def test_not_at_head_is_refused(tables_url, env_for, capsys):
    env_for("local", tables_url)

    code = demo.main([], seeds=[TEST_REFERENCE_SEED], generators=[SampleProfiles()])

    assert code == 1
    assert "make migrate" in capsys.readouterr().err
    assert counts(tables_url) == (0, 0)
