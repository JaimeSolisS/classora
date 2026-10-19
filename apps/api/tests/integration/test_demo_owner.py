"""The ``identity.demo_owner`` demo generator (#23 I; ADR-0007 D12)."""

import asyncio
import io

import pytest
from alembic import command

from app.cli import main as cli_main
from app.modules.identity.passwords import needs_rehash, verify_password
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.identity import (
    DEMO_OWNER_DISPLAY_NAME,
    DEMO_OWNER_PASSWORD,
    DEMO_OWNER_USERNAME,
    DemoOwnerGenerator,
)
from tests.integration.auth_support import auth_client, login
from tests.integration.conftest import run_sql
from tests.integration.test_migrations import alembic_config

REFERENCE_LINES = [
    "identity.permissions: 19 inserted, 0 updated",
    "identity.roles: 7 inserted, 0 updated",
    "identity.role_permissions: 43 inserted, 0 updated",
]
RERUN_REFERENCE_LINES = [
    "identity.permissions: 0 inserted, 0 updated",
    "identity.roles: 0 inserted, 0 updated",
    "identity.role_permissions: 0 inserted, 0 updated",
]


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    command.upgrade(alembic_config(fresh_database_url), "head")
    return fresh_database_url


@pytest.fixture
def env_for(monkeypatch):
    def _set(app_env: str, dsn: str) -> None:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("DATABASE_URL", dsn)
        monkeypatch.setenv("DATABASE_DIRECT_URL", dsn)

    return _set


def demo_owner_rows(url: str) -> list:
    return run_sql(
        url,
        "SELECT u.username, u.display_name, u.state, u.password_hash, r.code"
        " FROM users u JOIN user_roles ur ON ur.user_id = u.id"
        " JOIN roles r ON r.id = ur.role_id",
    )


def test_the_generator_is_registered():
    assert [g.name for g in demo.DEMO_GENERATORS] == ["identity.demo_owner"]


def test_local_creates_the_active_demo_owner(migrated_url, env_for, capsys):
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *REFERENCE_LINES,
        "identity.demo_owner: 1 rows",
    ]
    [row] = demo_owner_rows(migrated_url)
    assert (row.username, row.display_name, row.state, row.code) == (
        DEMO_OWNER_USERNAME,
        DEMO_OWNER_DISPLAY_NAME,
        "active",
        "owner",
    )
    assert verify_password(row.password_hash, DEMO_OWNER_PASSWORD)
    assert not needs_rehash(row.password_hash)


def test_dev_creates_nothing_and_says_why(migrated_url, env_for, capsys):
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *REFERENCE_LINES,
        "identity.demo_owner: skipped (APP_ENV=dev; local only)",
    ]
    assert run_sql(migrated_url, "SELECT count(*) FROM users")[0][0] == 0


def test_a_second_run_creates_nothing(migrated_url, env_for, capsys):
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        "identity.demo_owner: 0 rows",
    ]
    assert len(demo_owner_rows(migrated_url)) == 1


class UntouchedSession:
    """Fails the test if the generator uses the session."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the session was used ({name})")


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
def test_the_generator_writes_nothing_outside_local(app_env):
    generator = DemoOwnerGenerator(app_env=lambda: app_env)

    result = asyncio.run(generator.generate(UntouchedSession()))  # type: ignore[arg-type]

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")


def test_the_demo_owner_can_log_in(migrated_url, env_for):
    env_for("local", migrated_url)
    assert demo.main([]) == 0

    with auth_client(migrated_url) as client:
        response = login(client, "Demo.Owner", DEMO_OWNER_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == DEMO_OWNER_USERNAME
    assert body["roles"] == ["owner"]
    assert len(body["permissions"]) == 19


def test_bootstrap_owner_is_refused_after_the_demo_owner(migrated_url, env_for, capsys):
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()

    code = cli_main(
        ["bootstrap-owner", "--username", "someone", "--password-stdin"],
        stdin=io.StringIO("another-long-pass\n"),
    )

    assert code == 1
    assert "An Owner account already exists" in capsys.readouterr().err
    assert run_sql(migrated_url, "SELECT count(*) FROM users")[0][0] == 1
