"""The ``identity.demo_owner`` demo generator (#23 I; ADR-0007 D12) and the
registered demo generators (#195 E1)."""

import asyncio
import io

import pytest

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
from tests.integration.demo_support import (
    DEV_LINES,
    FIRST_RUN_LINES,
    GENERATOR_NAMES,
    RERUN_LINES,
    UntouchedSession,
    demo_env,
    migrate,
)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def demo_owner_rows(url: str) -> list:
    return run_sql(
        url,
        "SELECT u.username, u.display_name, u.state, u.password_hash, r.code"
        " FROM users u JOIN user_roles ur ON ur.user_id = u.id"
        " JOIN roles r ON r.id = ur.role_id WHERE u.username = :username",
        {"username": DEMO_OWNER_USERNAME},
    )


def test_the_generators_are_registered_in_order():
    # #195 E1: the seven demo accounts, then the demo families; #299 A1:
    # then the catalog, the tutor and the schedule; #303 A1: demo.tutor2
    # after demo.guardian, then its setup, the course and the changes.
    assert [g.name for g in demo.DEMO_GENERATORS] == GENERATOR_NAMES
    assert GENERATOR_NAMES == [
        "identity.demo_owner",
        "identity.demo_admin",
        "identity.demo_coordinator",
        "identity.demo_finance",
        "identity.demo_tutor",
        "identity.demo_student",
        "identity.demo_guardian",
        "identity.demo_tutor2",
        "families.demo_families",
        "catalog.demo_catalog",
        "staff.demo_tutor",
        "scheduling.demo_schedule",
        "staff.demo_tutor2",
        "courses.demo_courses",
        "scheduling.demo_changes",
    ]


def test_local_creates_the_active_demo_owner(migrated_url, env_for, capsys):
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    [row] = demo_owner_rows(migrated_url)
    assert (row.username, row.display_name, row.state, row.code) == (
        DEMO_OWNER_USERNAME,
        DEMO_OWNER_DISPLAY_NAME,
        "active",
        "owner",
    )
    assert verify_password(row.password_hash, DEMO_OWNER_PASSWORD)
    assert not needs_rehash(row.password_hash)
    # #36 E3: stored preferences, so E2E specs never migrate theirs onto it.
    assert demo_owner_preferences(migrated_url) == [("es", "system")]


def demo_owner_preferences(url: str) -> list[tuple[str, str]]:
    return [
        (row.language, row.theme)
        for row in run_sql(
            url,
            "SELECT p.language, p.theme FROM user_preferences p"
            " JOIN users u ON u.id = p.user_id WHERE u.username = :username",
            {"username": DEMO_OWNER_USERNAME},
        )
    ]


def test_dev_creates_nothing_and_says_why(migrated_url, env_for, capsys):
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    assert run_sql(migrated_url, "SELECT count(*) FROM users")[0][0] == 0


def test_a_second_run_creates_nothing(migrated_url, env_for, capsys):
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert len(demo_owner_rows(migrated_url)) == 1
    assert demo_owner_preferences(migrated_url) == [("es", "system")]


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
    assert len(body["permissions"]) == 32
    assert body["preferences"] == {"language": "es", "theme": "system"}


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
    # #195, #303: the eight demo accounts, and no bootstrap Owner.
    assert run_sql(migrated_url, "SELECT count(*) FROM users")[0][0] == 8
