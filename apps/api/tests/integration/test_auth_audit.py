"""Login and logout audit events (#23 F) and no secrets in logs (#23 G)."""

import asyncio
import hashlib
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient

from app.core.audit import AuditEvent
from app.core.db import create_sessionmaker
from app.core.logging import configure_logging
from app.modules.identity import auth
from app.modules.identity.permissions import ARCHIVED, PENDING, SUSPENDED
from tests.audit.assertions import assert_no_secret_in_audit, audit_events_for
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    LOGOUT,
    ME,
    auth_client,
    cookie_header,
    create_user,
    login,
    only_set_cookie,
    signed_headers,
    token_of,
)
from tests.integration.conftest import run_sql, short_lived_engine

WRONG_PASSWORD = "wrong-password-1"


@pytest.fixture
def client(seeded_url: str) -> Iterator[TestClient]:
    with auth_client(seeded_url) as test_client:
        yield test_client


def events(url: str, **filters: Any) -> list[AuditEvent]:
    async def _load() -> list[AuditEvent]:
        engine = short_lived_engine(url)
        try:
            async with create_sessionmaker(engine)() as session:
                return await audit_events_for(session, **filters)
        finally:
            await engine.dispose()

    return asyncio.run(_load())


def login_events(url: str) -> list[AuditEvent]:
    return [e for e in events(url) if e.action.startswith("identity.log")]


def no_secret_in_audit(url: str, *secrets: str) -> None:
    async def _check() -> None:
        engine = short_lived_engine(url)
        try:
            async with create_sessionmaker(engine)() as session:
                await assert_no_secret_in_audit(session, *secrets)
        finally:
            await engine.dispose()

    asyncio.run(_check())


def session_count(url: str) -> int:
    return int(run_sql(url, "SELECT count(*) FROM auth_sessions")[0][0])


# --- F1: success ----------------------------------------------------------------------


def test_login_succeeded_event(seeded_url, client):
    user = create_user(seeded_url)

    response = login(client, user.username, TEST_USER_PASSWORD)

    assert response.status_code == 200
    [event] = login_events(seeded_url)
    [row] = run_sql(seeded_url, "SELECT id FROM auth_sessions")
    assert event.action == "identity.login_succeeded"
    assert (event.record_type, event.record_id) == ("users", user.id)
    assert event.details == {"auth_session_id": str(row.id)}
    assert event.actor_user_id is None
    assert event.changes is None
    assert event.request_id == response.headers["x-request-id"]


# --- F2: failures ---------------------------------------------------------------------


def test_unknown_username_stores_neither_username_nor_password(seeded_url, client):
    response = login(client, "nobody.here", "secret-in-the-name-field")

    assert response.status_code == 401
    [event] = login_events(seeded_url)
    assert event.action == "identity.login_failed"
    assert event.details == {"reason": "unknown_username"}
    assert (event.record_type, event.record_id) == (None, None)
    assert event.actor_user_id is None
    assert event.request_id == response.headers["x-request-id"]
    no_secret_in_audit(seeded_url, "nobody.here", "secret-in-the-name-field")
    assert session_count(seeded_url) == 0


def test_a_username_failing_normalization_is_an_unknown_username(seeded_url, client):
    assert login(client, "Ünïcode user", WRONG_PASSWORD).status_code == 401

    [event] = login_events(seeded_url)
    assert event.details == {"reason": "unknown_username"}
    no_secret_in_audit(seeded_url, "Ünïcode user", WRONG_PASSWORD)


@pytest.mark.parametrize(
    ("overrides", "password", "details"),
    [
        ({}, WRONG_PASSWORD, {"reason": "invalid_password"}),
        ({"password_hash": None}, TEST_USER_PASSWORD, {"reason": "no_password"}),
        (
            {"state": SUSPENDED},
            TEST_USER_PASSWORD,
            {"reason": "not_active", "state": "suspended"},
        ),
        (
            {"state": ARCHIVED},
            TEST_USER_PASSWORD,
            {"reason": "not_active", "state": "archived"},
        ),
        (
            {"state": PENDING},
            TEST_USER_PASSWORD,
            {"reason": "not_active", "state": "pending"},
        ),
        (
            {"state": SUSPENDED},
            WRONG_PASSWORD,
            {"reason": "invalid_password"},
        ),
    ],
    ids=[
        "wrong password",
        "no password",
        "suspended",
        "archived",
        "pending",
        "suspended, wrong password",
    ],
)
def test_failure_events_name_the_user(seeded_url, client, overrides, password, details):
    user = create_user(seeded_url, **overrides)

    assert login(client, user.username, password).status_code == 401

    [event] = login_events(seeded_url)
    assert event.action == "identity.login_failed"
    assert (event.record_type, event.record_id) == ("users", user.id)
    assert event.details == details
    assert event.actor_user_id is None
    assert session_count(seeded_url) == 0
    no_secret_in_audit(seeded_url, password)


def test_a_failed_login_leaves_exactly_one_committed_event(seeded_url, client):
    user = create_user(seeded_url)
    before = len(events(seeded_url))

    assert login(client, user.username, WRONG_PASSWORD).status_code == 401

    after = events(seeded_url)
    assert len(after) == before + 1
    assert after[-1].action == "identity.login_failed"
    assert session_count(seeded_url) == 0


# --- F3: logout -----------------------------------------------------------------------


def test_logout_event_names_the_user_as_actor(seeded_url, client):
    user = create_user(seeded_url)
    cookie = only_set_cookie(login(client, user.username))
    [row] = run_sql(seeded_url, "SELECT id FROM auth_sessions")

    client.cookies.clear()
    response = client.post(LOGOUT, headers=signed_headers(cookie))

    assert response.status_code == 204
    succeeded, logout = login_events(seeded_url)
    assert succeeded.action == "identity.login_succeeded"
    assert logout.action == "identity.logout"
    assert (logout.record_type, logout.record_id) == ("users", user.id)
    assert logout.details == {"auth_session_id": str(row.id)}
    assert logout.actor_user_id == user.id
    assert logout.request_id == response.headers["x-request-id"]


def test_me_writes_no_event(seeded_url, client):
    user = create_user(seeded_url)
    cookie = only_set_cookie(login(client, user.username))
    before = len(events(seeded_url))

    client.cookies.clear()
    assert client.get(ME, headers=cookie_header(cookie)).status_code == 200

    assert len(events(seeded_url)) == before


# --- F4: secrets, rehash and rollback -------------------------------------------------


def test_no_password_token_or_hash_in_any_audit_row(seeded_url, client):
    user = create_user(seeded_url)
    response = login(client, user.username)
    cookie = only_set_cookie(response)
    login(client, user.username, WRONG_PASSWORD)
    login(client, "nobody.here", WRONG_PASSWORD)
    client.cookies.clear()
    client.post(LOGOUT, headers=signed_headers(cookie))
    token_hash = hashlib.sha256(token_of(cookie)).hexdigest()

    assert [e.action for e in login_events(seeded_url)] == [
        "identity.login_succeeded",
        "identity.login_failed",
        "identity.login_failed",
        "identity.logout",
    ]
    no_secret_in_audit(
        seeded_url, TEST_USER_PASSWORD, WRONG_PASSWORD, cookie.value, token_hash
    )


def test_a_rehash_on_login_writes_no_users_update_event(seeded_url, client):
    weaker = PasswordHasher(
        time_cost=1, memory_cost=8192, parallelism=1, hash_len=32, type=Type.ID
    )
    user = create_user(seeded_url, password_hash=weaker.hash(TEST_USER_PASSWORD))
    before = events(seeded_url, record_type="users", record_id=user.id)

    assert login(client, user.username).status_code == 200

    after = events(seeded_url, record_type="users", record_id=user.id)
    new = after[len(before) :]
    assert [e.action for e in new] == ["identity.login_succeeded"]
    [changed] = run_sql(
        seeded_url, "SELECT version FROM users WHERE id = :id", {"id": user.id}
    )
    assert changed.version == 2  # the hash was replaced
    no_secret_in_audit(seeded_url, "$argon2id$")


def test_a_failure_after_the_session_row_rolls_everything_back(
    seeded_url, client, monkeypatch
):
    user = create_user(seeded_url)
    real = auth.record_audit_event

    def failing(session: Any, *, action: str, **kwargs: Any) -> None:
        if action == auth.LOGIN_SUCCEEDED_EVENT:
            raise RuntimeError("audit write failed")
        real(session, action=action, **kwargs)

    monkeypatch.setattr(auth, "record_audit_event", failing)
    before = len(events(seeded_url))

    response = login(client, user.username)

    assert response.status_code == 500
    assert response.headers.get_list("set-cookie") == []
    assert session_count(seeded_url) == 0
    assert len(events(seeded_url)) == before


def test_a_failure_after_the_session_row_also_rolls_back_the_rehash(
    seeded_url, client, monkeypatch
):
    """The rehash, the rotation and the new session share one transaction."""
    weaker = PasswordHasher(
        time_cost=1, memory_cost=8192, parallelism=1, hash_len=32, type=Type.ID
    )
    old_hash = weaker.hash(TEST_USER_PASSWORD)
    user = create_user(seeded_url, password_hash=old_hash)

    def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(auth, "record_audit_event", failing)

    response = login(client, user.username)

    assert response.status_code == 500
    [row] = run_sql(
        seeded_url, "SELECT password_hash FROM users WHERE id = :id", {"id": user.id}
    )
    assert row.password_hash == old_hash
    assert session_count(seeded_url) == 0


# --- G1: no secrets in logs ----------------------------------------------------------


def test_no_password_token_or_hash_in_logs(seeded_url, caplog, capsys):
    configure_logging()
    caplog.set_level(logging.DEBUG)
    user = create_user(seeded_url)
    with auth_client(seeded_url) as client:
        response = login(client, user.username)
        cookie = only_set_cookie(response)
        login(client, user.username, WRONG_PASSWORD)
        login(client, "nobody.here", WRONG_PASSWORD)
        client.cookies.clear()
        assert client.get(ME, headers=cookie_header(cookie)).status_code == 200
        client.cookies.clear()
        client.post(LOGOUT, headers=signed_headers(cookie))
    token = token_of(cookie)
    secrets = [
        TEST_USER_PASSWORD,
        WRONG_PASSWORD,
        cookie.value,
        hashlib.sha256(token).hexdigest(),
        token.hex(),
    ]

    captured = capsys.readouterr()
    logged = [record.getMessage() for record in caplog.records]
    logged += [repr(record.__dict__) for record in caplog.records]
    for text in [captured.out, captured.err, *logged]:
        for secret in secrets:
            assert secret not in text
    access = [r for r in caplog.records if r.name == "app.access"]
    assert {getattr(r, "path", None) for r in access} >= {"/api/v1/auth/login"}


def test_the_login_request_repr_hides_the_password():
    from app.modules.identity.schemas import LoginRequest

    body = LoginRequest(username="ana", password=TEST_USER_PASSWORD)

    assert TEST_USER_PASSWORD not in repr(body)
    assert TEST_USER_PASSWORD not in str(body)
