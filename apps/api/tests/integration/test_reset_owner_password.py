"""``python -m app.cli reset-owner-password`` against PostgreSQL (#30 E)."""

import hashlib
import logging
import re

import pytest

from app import cli
from app.modules.identity.permissions import ADMINISTRATOR, OWNER
from app.modules.identity.sessions import decode_token
from app.seeds import runner
from tests.authz.csrf import origin_for
from tests.integration.auth_support import auth_client, create_user
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import (
    code_of,
    events,
    me,
    no_secret_in_audit,
    signed_in,
)
from tests.integration.recovery_support import (
    ISSUED_EVENT,
    RESET_PASSWORD,
    reset_redeem,
)

URL = re.compile(r"(?P<origin>\S+)/reset#token=(?P<token>[A-Za-z0-9_-]+)")


def no_prompt(text: str) -> str:
    raise AssertionError("the command must not ask for a password")


@pytest.fixture
def env_for(monkeypatch):
    def _set(dsn: str, app_env: str = "local") -> None:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("APP_ORIGIN", origin_for(app_env))
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@127.0.0.1:1/pooled")
        monkeypatch.setenv("DATABASE_DIRECT_URL", dsn)

    return _set


def reset_owner(*args: str) -> int:
    return cli.main(["reset-owner-password", *args], prompt=no_prompt)


def token_rows(dsn: str) -> list:  # type: ignore[type-arg]
    return run_sql(
        dsn,
        "SELECT id, user_id, kind, token_hash, issued_by_user_id, revoked_at"
        " FROM account_tokens ORDER BY created_at, id",
    )


def audit_count(dsn: str) -> int:
    return int(run_sql(dsn, "SELECT count(*) FROM audit_events")[0][0])


def printed_tokens(out: str) -> list[str]:
    return [m.group("token") for m in URL.finditer(out)]


# --- Success ---


@pytest.mark.parametrize("app_env", ["local", "prod"])
def test_prints_one_reset_link_for_an_active_owner(
    seeded_url, env_for, capsys, caplog, clock, app_env
):
    env_for(seeded_url, app_env)
    caplog.set_level(logging.INFO)
    owner = create_user(seeded_url, roles=(OWNER,))

    assert reset_owner("--username", f" {owner.username.upper()} ") == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    [raw] = printed_tokens(captured.out)
    assert len(raw) == 43
    [match] = URL.finditer(captured.out)
    assert match.group("origin") == origin_for(app_env)
    assert "Expires at 2026-10-19T12:00:00+00:00 (UTC)." in captured.out
    assert "shown once and expires in 24 hours" in captured.out
    [row] = token_rows(seeded_url)
    token = decode_token(raw)
    assert token is not None
    assert bytes(row.token_hash) == hashlib.sha256(token).digest()
    assert (row.user_id, row.kind, row.issued_by_user_id) == (
        owner.id,
        "password_reset",
        None,
    )
    [event] = events(seeded_url, record_id=owner.id, action=ISSUED_EVENT)
    assert event.actor_user_id is None
    assert event.details == {
        "link_id": str(row.id),
        "expires_at": "2026-10-19T12:00:00+00:00",
        "source": "cli",
    }
    records = [
        r for r in caplog.records if r.getMessage() == "owner_password_reset_issued"
    ]
    assert len(records) == 1
    assert records[0].user_id == str(owner.id)  # type: ignore[attr-defined]
    assert records[0].username == owner.username  # type: ignore[attr-defined]
    # E3: the raw token is in no log record and no audit row.
    for record in caplog.records:
        assert raw not in record.getMessage()
        assert raw not in repr(record.__dict__)
    json_lines = [line for line in captured.out.splitlines() if line.startswith("{")]
    assert all(raw not in line for line in json_lines)
    no_secret_in_audit(seeded_url, raw, bytes(row.token_hash).hex())


def test_the_printed_link_redeems_and_ends_the_owners_sessions(
    seeded_url, env_for, capsys, clock
):
    env_for(seeded_url)
    owner = create_user(seeded_url, roles=(OWNER,))
    with auth_client(seeded_url) as client:
        session = signed_in(client, owner.username)
        assert reset_owner("--username", owner.username) == 0
        [raw] = printed_tokens(capsys.readouterr().out)
        # The command itself changes no session.
        assert code_of(me(client, session)) == (200, None)

        assert code_of(reset_redeem(client, raw)) == (204, None)

        assert code_of(me(client, session)) == (401, "authentication_required")
        assert code_of(
            client.post(
                "/api/v1/auth/login",
                json={"username": owner.username, "password": RESET_PASSWORD},
            )
        ) == (200, None)


def test_a_second_run_replaces_the_first_link(seeded_url, env_for, capsys, clock):
    env_for(seeded_url)
    owner = create_user(seeded_url, roles=(OWNER,))
    assert reset_owner("--username", owner.username) == 0
    [first] = printed_tokens(capsys.readouterr().out)

    assert reset_owner("--username", owner.username) == 0

    [second] = printed_tokens(capsys.readouterr().out)
    assert first != second
    old, new = token_rows(seeded_url)
    assert old.revoked_at is not None
    assert new.revoked_at is None
    with auth_client(seeded_url) as client:
        assert code_of(reset_redeem(client, first)) == (410, "token_invalid")
        assert code_of(reset_redeem(client, second)) == (204, None)


# --- Refusals ---


def test_an_unknown_username_is_refused(seeded_url, env_for, capsys):
    env_for(seeded_url)

    assert reset_owner("--username", "nobody.here") == 1

    captured = capsys.readouterr()
    assert captured.err.strip() == cli.NO_OWNER_MESSAGE
    assert captured.out == ""
    assert token_rows(seeded_url) == []
    assert audit_count(seeded_url) == 0


@pytest.mark.parametrize("username", ["ab", "ana lopez", "josé"])
def test_an_invalid_username_is_refused(seeded_url, env_for, capsys, username):
    env_for(seeded_url)

    assert reset_owner("--username", username) == 1

    assert capsys.readouterr().err.strip() == cli.NO_OWNER_MESSAGE


def test_a_user_without_the_owner_role_is_refused(seeded_url, env_for, capsys):
    env_for(seeded_url)
    admin = create_user(seeded_url, roles=(ADMINISTRATOR,))
    before = audit_count(seeded_url)

    assert reset_owner("--username", admin.username) == 1

    assert capsys.readouterr().err.strip() == cli.NO_OWNER_MESSAGE
    assert token_rows(seeded_url) == []
    assert audit_count(seeded_url) == before


@pytest.mark.parametrize("state", ["suspended", "archived"])
def test_an_owner_who_is_not_active_is_refused(seeded_url, env_for, capsys, state):
    env_for(seeded_url)
    owner = create_user(seeded_url, roles=(OWNER,), state=state)
    before = audit_count(seeded_url)

    assert reset_owner("--username", owner.username) == 1

    assert capsys.readouterr().err.strip() == (
        f"The Owner account is {state}. A reset cannot sign in a non-active account."
    )
    assert token_rows(seeded_url) == []
    assert audit_count(seeded_url) == before


def test_a_database_not_at_head_is_refused(seeded_url, env_for, capsys, monkeypatch):
    env_for(seeded_url)
    owner = create_user(seeded_url, roles=(OWNER,))
    before = audit_count(seeded_url)
    monkeypatch.setattr(runner, "alembic_heads", lambda: {"9999"})

    assert reset_owner("--username", owner.username) == 1

    assert "make migrate" in capsys.readouterr().err
    assert token_rows(seeded_url) == []
    assert audit_count(seeded_url) == before


def test_an_empty_database_is_refused(fresh_database_url, env_for, capsys):
    env_for(fresh_database_url)

    assert reset_owner("--username", "owner") == 1

    assert capsys.readouterr().err.strip() == runner.NOT_AT_HEAD_MESSAGE


def test_a_configuration_error_exits_1(capsys):
    assert reset_owner("--username", "owner") == 1

    assert capsys.readouterr().err.startswith("Configuration error")


def test_the_username_is_required(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["reset-owner-password"])

    assert exit_info.value.code == 2
