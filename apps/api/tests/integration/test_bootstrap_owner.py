"""``python -m app.cli bootstrap-owner`` against PostgreSQL (M2-02 H, E4; M2-04 E3)."""

import io
import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import cli
from app.seeds import runner
from tests.integration.conftest import run_sql

PASSWORD = "bootstrap-pass-123"
OWNERS = (
    "SELECT u.username, u.display_name, u.state, u.password_hash, r.code "
    "FROM users u JOIN user_roles ur ON ur.user_id = u.id "
    "JOIN roles r ON r.id = ur.role_id ORDER BY u.username"
)


def prompt_with(*answers: str) -> Callable[[str], str]:
    remaining = list(answers)
    asked: list[str] = []

    def _prompt(text: str) -> str:
        asked.append(text)
        return remaining.pop(0)

    _prompt.asked = asked  # type: ignore[attr-defined]
    return _prompt


def no_prompt(text: str) -> str:
    raise AssertionError("the password must not be asked for")


@pytest.fixture
def env_for(monkeypatch):
    def _set(dsn: str, app_env: str = "local") -> None:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@127.0.0.1:1/pooled")
        monkeypatch.setenv("DATABASE_DIRECT_URL", dsn)

    return _set


def user_count(dsn: str) -> int:
    return int(run_sql(dsn, "SELECT count(*) FROM users")[0][0])


def audit_count(dsn: str) -> int:
    return int(run_sql(dsn, "SELECT count(*) FROM audit_events")[0][0])


AUDIT_ROWS = (
    "SELECT action, record_type, record_id, actor_user_id, request_id, details,"
    " changes::text AS changes FROM audit_events ORDER BY action, record_type"
)


def bootstrap(*args: str, **kwargs) -> int:
    return cli.main(["bootstrap-owner", *args], **kwargs)


# --- Success ------------------------------------------------------------------------


@pytest.mark.parametrize("app_env", ["local", "prod"])
def test_creates_the_first_owner(seeded_url, env_for, capsys, caplog, app_env):
    env_for(seeded_url, app_env)
    caplog.set_level(logging.INFO)
    prompt = prompt_with(PASSWORD, PASSWORD)

    assert bootstrap("--username", " Ana.Owner ", prompt=prompt) == 0

    rows = run_sql(seeded_url, OWNERS)
    assert len(rows) == 1
    username, display_name, state, password_hash, role = rows[0]
    assert (username, display_name, state, role) == (
        "ana.owner",
        "ana.owner",
        "active",
        "owner",
    )
    assert password_hash.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert prompt.asked == ["Password: ", "Repeat the password: "]  # type: ignore[attr-defined]

    captured = capsys.readouterr()
    assert "Created the Owner account 'ana.owner'." in captured.out.splitlines()
    assert captured.err == ""
    for text in (captured.out, captured.err, caplog.text):
        assert PASSWORD not in text
        assert password_hash not in text

    # H3: one structured log line (kept next to the audit event of #22).
    records = [r for r in caplog.records if r.getMessage() == "owner_bootstrapped"]
    assert len(records) == 1
    user_id = run_sql(seeded_url, "SELECT id FROM users")[0][0]
    assert records[0].event == "owner_bootstrapped"  # type: ignore[attr-defined]
    assert records[0].user_id == str(user_id)  # type: ignore[attr-defined]
    assert records[0].username == "ana.owner"  # type: ignore[attr-defined]
    json_lines = [
        json.loads(line) for line in captured.out.splitlines() if line.startswith("{")
    ]
    assert [line["message"] for line in json_lines] == ["owner_bootstrapped"]
    assert json_lines[0]["user_id"] == str(user_id)

    # #22 E3: exactly three audit events, no actor, no request, no secret.
    rows = run_sql(seeded_url, AUDIT_ROWS)
    role_assignment_id = run_sql(seeded_url, "SELECT id FROM user_roles")[0][0]
    assert [(r.action, r.record_type, r.record_id) for r in rows] == [
        ("create", "user_roles", role_assignment_id),
        ("create", "users", user_id),
        ("identity.owner_bootstrapped", "users", user_id),
    ]
    assert all(r.actor_user_id is None and r.request_id is None for r in rows)
    assert rows[2].details == {"source": "cli"}
    for row in rows:
        stored = f"{row.changes} {row.details}"
        for secret in (PASSWORD, password_hash, "$argon2id$", "password"):
            assert secret not in stored


def test_display_name_option(seeded_url, env_for):
    env_for(seeded_url)

    code = bootstrap(
        "--username",
        "owner",
        "--display-name",
        "  Dueña del Centro ",
        prompt=prompt_with(PASSWORD, PASSWORD),
    )

    assert code == 0
    assert run_sql(seeded_url, OWNERS)[0].display_name == "Dueña del Centro"


def test_password_from_stdin(seeded_url, env_for, capsys):
    env_for(seeded_url)

    code = bootstrap(
        "--username",
        "owner",
        "--password-stdin",
        stdin=io.StringIO(PASSWORD + "\nignored second line\n"),
        prompt=no_prompt,
    )

    assert code == 0
    from app.modules.identity.service import verify_password

    stored = run_sql(seeded_url, OWNERS)[0].password_hash
    assert verify_password(stored, PASSWORD)
    assert PASSWORD not in capsys.readouterr().out


# --- Refusals (exit 1, nothing written) -----------------------------------------------


def test_second_run_is_refused(seeded_url, env_for, capsys):
    env_for(seeded_url)
    assert bootstrap("--username", "first", prompt=prompt_with(PASSWORD, PASSWORD)) == 0
    capsys.readouterr()

    assert bootstrap("--username", "second", prompt=no_prompt) == 1

    assert capsys.readouterr().err.strip() == cli.OWNER_EXISTS_MESSAGE
    assert [row.username for row in run_sql(seeded_url, OWNERS)] == ["first"]
    assert user_count(seeded_url) == 1
    assert audit_count(seeded_url) == 3  # the first run's events only


@pytest.mark.parametrize("state", ["suspended", "archived", "pending"])
def test_any_existing_owner_blocks_the_run(seeded_url, env_for, capsys, state):
    env_for(seeded_url)
    run_sql(
        seeded_url,
        "WITH u AS (INSERT INTO users (username, display_name, state) "
        "VALUES ('old.owner', 'Old', :state) RETURNING id) "
        "INSERT INTO user_roles (user_id, role_id) "
        "SELECT u.id, r.id FROM u, roles r WHERE r.code = 'owner'",
        {"state": state},
    )

    assert bootstrap("--username", "new.owner", prompt=no_prompt) == 1

    assert cli.OWNER_EXISTS_MESSAGE in capsys.readouterr().err
    assert user_count(seeded_url) == 1
    assert audit_count(seeded_url) == 0


def test_empty_database_is_refused(fresh_database_url, env_for, capsys):
    env_for(fresh_database_url)

    assert bootstrap("--username", "owner", prompt=no_prompt) == 1

    err = capsys.readouterr().err
    assert err.strip() == runner.NOT_AT_HEAD_MESSAGE
    assert "make migrate" in err


def test_pending_migration_is_refused(seeded_url, env_for, capsys, monkeypatch):
    env_for(seeded_url)
    monkeypatch.setattr(runner, "alembic_heads", lambda: {"9999"})

    assert bootstrap("--username", "owner", prompt=no_prompt) == 1

    assert "make migrate" in capsys.readouterr().err
    assert user_count(seeded_url) == 0
    assert audit_count(seeded_url) == 0


def test_roles_not_seeded_is_refused(head_url, env_for, capsys):
    env_for(head_url)

    assert bootstrap("--username", "owner", prompt=no_prompt) == 1

    err = capsys.readouterr().err
    assert err.strip() == cli.ROLES_NOT_SEEDED_MESSAGE
    assert "make seed" in err
    assert user_count(head_url) == 0
    assert audit_count(head_url) == 0


@pytest.mark.parametrize("username", ["ab", "ana lopez", "josé", "Kelvin"])
def test_invalid_username_is_refused(seeded_url, env_for, capsys, username):
    env_for(seeded_url)

    assert bootstrap("--username", username, prompt=no_prompt) == 1

    err = capsys.readouterr().err
    assert "3 to 64 characters" in err
    assert user_count(seeded_url) == 0
    assert audit_count(seeded_url) == 0


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short", "at least 12 characters"),
        ("x" * 129, "at most 128 characters"),
        ("Owner.Name.2026", "same as the username"),
    ],
)
def test_policy_violation_is_refused(seeded_url, env_for, capsys, password, message):
    env_for(seeded_url)

    code = bootstrap(
        "--username", "owner.name.2026", prompt=prompt_with(password, password)
    )

    assert code == 1
    captured = capsys.readouterr()
    assert message in captured.err
    assert password not in captured.err + captured.out
    assert user_count(seeded_url) == 0
    assert audit_count(seeded_url) == 0


def test_policy_violation_from_stdin(seeded_url, env_for, capsys):
    env_for(seeded_url)

    code = bootstrap(
        "--username",
        "owner",
        "--password-stdin",
        stdin=io.StringIO(""),
        prompt=no_prompt,
    )

    assert code == 1
    assert "at least 12 characters" in capsys.readouterr().err
    assert user_count(seeded_url) == 0
    assert audit_count(seeded_url) == 0


def test_confirmation_mismatch_is_refused(seeded_url, env_for, capsys):
    env_for(seeded_url)

    code = bootstrap(
        "--username", "owner", prompt=prompt_with(PASSWORD, PASSWORD + "x")
    )

    assert code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == cli.MISMATCH_MESSAGE
    assert PASSWORD not in captured.err + captured.out
    assert user_count(seeded_url) == 0
    assert audit_count(seeded_url) == 0


def test_display_name_too_long_is_refused(seeded_url, env_for, capsys):
    env_for(seeded_url)

    code = bootstrap(
        "--username", "owner", "--display-name", "x" * 201, prompt=no_prompt
    )

    assert code == 1
    assert "at most 200 characters" in capsys.readouterr().err
    assert user_count(seeded_url) == 0


def test_configuration_error_exits_1(capsys):
    assert bootstrap("--username", "owner", prompt=no_prompt) == 1

    assert capsys.readouterr().err.startswith("Configuration error")


def test_password_is_never_accepted_as_an_argument(capsys):
    with pytest.raises(SystemExit) as exited:
        bootstrap("--username", "owner", "--password", PASSWORD)

    assert exited.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_password_is_never_read_from_the_environment(
    seeded_url, env_for, monkeypatch, capsys
):
    env_for(seeded_url)
    for name in ("PASSWORD", "OWNER_PASSWORD", "BOOTSTRAP_OWNER_PASSWORD"):
        monkeypatch.setenv(name, PASSWORD)

    code = bootstrap(
        "--username",
        "owner",
        "--password-stdin",
        stdin=io.StringIO(""),
        prompt=no_prompt,
    )

    assert code == 1
    assert user_count(seeded_url) == 0


def test_usage_without_a_command(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main([])

    assert exited.value.code == 2


# --- Concurrency (the advisory lock) --------------------------------------------------


def test_concurrent_runs_create_exactly_one_owner(seeded_url, env_for):
    env_for(seeded_url)
    # Both runs pass the read-only precheck before either one writes.
    both_prechecked = threading.Barrier(2, timeout=30)

    def run(username: str) -> int:
        def prompt(text: str) -> str:
            if text == "Password: ":
                both_prechecked.wait()
            return PASSWORD

        return bootstrap("--username", username, prompt=prompt)

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(run, ["owner.one", "owner.two"]))

    assert codes == [0, 1]
    assert len(run_sql(seeded_url, OWNERS)) == 1
    assert user_count(seeded_url) == 1
    assert audit_count(seeded_url) == 3
