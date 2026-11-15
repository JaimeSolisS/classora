"""The self-service password change (#30 F, G) with real logins, the shared
``reauth.user`` budget (#27 E1) and session rotation (ADR-0007 D8)."""

import logging
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.csrf import csrf_token_for
from app.modules.identity import passwords
from app.modules.identity.permissions import GUARDIAN, OWNER, TUTOR
from tests.authz.csrf import CSRF_HEADER
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    LOGOUT,
    ORIGIN,
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
    csrf_for,
    login,
    only_set_cookie,
    signed_headers,
    token_of,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import REAUTHENTICATE
from tests.integration.lifecycle_support import (
    code_of,
    events,
    me,
    no_secret_in_audit,
    post_ping,
    signed_in,
)
from tests.integration.lifecycle_support import ping_router as lifecycle_router
from tests.integration.recovery_support import (
    CHANGE_FAILED_EVENT,
    CHANGED_EVENT,
    change,
    issue_reset,
    password_hash_of,
    reset_lookup,
    reset_rows,
    reset_token_of,
    staff_reset,
)

OK = (200, None)
UNAUTHENTICATED = (401, "authentication_required")
WRONG = (403, "current_password_invalid")
RATE_LIMITED = (429, "rate_limited")
NEW_PASSWORD = "a-changed-password-1"
WRONG_PASSWORD = "not-the-right-password"
REVOKED_EVENT = "identity.sessions_revoked"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url, routers=[lifecycle_router]) as test_client:
        yield test_client


@pytest.fixture
def user(module_seeded_url: str) -> CreatedUser:
    # A username of 12+ characters, so it can also be a policy-valid password.
    return create_user(
        module_seeded_url,
        roles=(GUARDIAN,),
        username=f"change.guardian.{uuid.uuid4().hex[:10]}",
    )


@pytest.fixture
def verify_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = passwords.verify_password

    def spy(stored_hash: str, password: str) -> bool:
        calls.append(stored_hash)
        return real(stored_hash, password)

    monkeypatch.setattr(passwords, "verify_password", spy)
    return calls


def limiter_total(url: str) -> int:
    [row] = run_sql(
        url, "SELECT coalesce(sum(count), 0) AS used FROM rate_limit_counters"
    )
    return int(row.used)


def wrong_reauth(client: TestClient, cookie) -> tuple[int, str | None]:  # type: ignore[no-untyped-def]
    client.cookies.clear()
    return code_of(
        client.post(
            REAUTHENTICATE,
            json={"password": WRONG_PASSWORD},
            headers=signed_headers(cookie),
        )
    )


# --- F3, F4: success rotates the session ---


def test_a_change_rotates_the_session_and_ends_every_other(
    module_seeded_url, client, clock, user
):
    current = signed_in(client, user.username)
    other_a = signed_in(client, user.username)
    other_b = signed_in(client, user.username)
    old_csrf = csrf_for(current)

    response = change(client, current, TEST_USER_PASSWORD, NEW_PASSWORD)

    assert code_of(response) == OK
    rotated = only_set_cookie(response)
    assert rotated.name == current.name
    assert rotated.value != current.value
    assert rotated.attributes["path"] == "/"
    assert "httponly" in rotated.attributes
    assert rotated.attributes["samesite"].lower() == "lax"
    # Extended class (guardian): 30 days absolute.
    assert rotated.attributes["max-age"] == str(30 * 24 * 3600)
    body = response.json()
    assert body["csrf_token"] != old_csrf
    assert body["csrf_token"] == csrf_token_for(token_of(rotated))
    assert body["username"] == user.username
    assert body["roles"] == [GUARDIAN]
    for old in (current, other_a, other_b):
        assert code_of(me(client, old)) == UNAUTHENTICATED
    assert code_of(me(client, rotated)) == OK
    assert code_of(post_ping(client, rotated)) == OK
    # The old CSRF token does not work with the new cookie.
    client.cookies.clear()
    stale = client.post(
        "/test-only/lifecycle/ping",
        headers={**ORIGIN, **cookie_header(rotated), CSRF_HEADER: old_csrf},
    )
    assert code_of(stale) == (403, "csrf_failed")
    assert code_of(login(client, user.username, NEW_PASSWORD)) == OK
    assert code_of(login(client, user.username)) == (401, "invalid_credentials")
    [revoked] = events(module_seeded_url, record_id=user.id, action=REVOKED_EVENT)
    assert revoked.details == {"reason": "password_changed", "count": 3}


def test_a_staff_change_gets_the_staff_session_class(module_seeded_url, client, clock):
    owner = create_user(module_seeded_url, roles=(OWNER,))
    cookie = signed_in(client, owner.username)

    response = change(client, cookie, TEST_USER_PASSWORD, NEW_PASSWORD)

    assert code_of(response) == OK
    assert only_set_cookie(response).attributes["max-age"] == str(12 * 3600)


def test_after_a_change_an_earlier_reset_link_is_gone(
    module_seeded_url, client, clock, user
):
    owner = create_user(module_seeded_url, roles=(OWNER,))
    raw = reset_token_of(staff_reset(client, owner.username, user.username))
    cookie = signed_in(client, user.username)

    assert code_of(change(client, cookie, TEST_USER_PASSWORD, NEW_PASSWORD)) == OK

    assert code_of(reset_lookup(client, raw)) == (410, "token_invalid")
    [row] = reset_rows(module_seeded_url, user.id)
    assert row.revoked_at is not None
    assert row.used_at is None


def test_the_new_session_is_not_reauthenticated(module_seeded_url, client, clock):
    owner = create_user(module_seeded_url, roles=(OWNER,))
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = signed_in(client, owner.username)
    client.cookies.clear()
    reauthed = client.post(
        REAUTHENTICATE,
        json={"password": TEST_USER_PASSWORD},
        headers=signed_headers(cookie),
    )
    assert code_of(reauthed) == OK

    rotated = only_set_cookie(change(client, cookie, TEST_USER_PASSWORD, NEW_PASSWORD))

    response = issue_reset(client, rotated, person.username)
    assert code_of(response) == (403, "reauth_required")


def test_a_change_writes_the_named_event_without_secrets(
    module_seeded_url, client, clock, user
):
    cookie = signed_in(client, user.username)

    response = change(client, cookie, TEST_USER_PASSWORD, NEW_PASSWORD)

    rotated = only_set_cookie(response)
    [event] = events(module_seeded_url, record_id=user.id, action=CHANGED_EVENT)
    [new_session] = run_sql(
        module_seeded_url,
        "SELECT id FROM auth_sessions WHERE user_id = :id AND revoked_at IS NULL",
        {"id": user.id},
    )
    assert event.record_type == "users"
    assert event.actor_user_id == user.id
    assert event.details == {"auth_session_id": str(new_session.id)}
    # password_hash is excluded from automatic diffs: no users update event.
    assert events(module_seeded_url, record_id=user.id, action="update") == []
    no_secret_in_audit(
        module_seeded_url,
        TEST_USER_PASSWORD,
        NEW_PASSWORD,
        rotated.value,
        cookie.value,
        password_hash_of(module_seeded_url, user.id) or "",
    )


# --- F2, F4: failures ---


def test_a_wrong_current_password_is_403_and_changes_nothing(
    module_seeded_url, client, clock, user
):
    cookie = signed_in(client, user.username)
    before = password_hash_of(module_seeded_url, user.id)

    response = change(client, cookie, WRONG_PASSWORD, NEW_PASSWORD)

    assert code_of(response) == WRONG
    assert response.headers.get_list("set-cookie") == []
    assert code_of(me(client, cookie)) == OK
    assert password_hash_of(module_seeded_url, user.id) == before
    [current] = run_sql(
        module_seeded_url,
        "SELECT id FROM auth_sessions WHERE user_id = :id",
        {"id": user.id},
    )
    [event] = events(module_seeded_url, record_id=user.id, action=CHANGE_FAILED_EVENT)
    assert event.actor_user_id == user.id
    assert event.details == {
        "auth_session_id": str(current.id),
        "reason": "invalid_password",
    }
    assert events(module_seeded_url, record_id=user.id, action=CHANGED_EVENT) == []
    no_secret_in_audit(module_seeded_url, WRONG_PASSWORD, NEW_PASSWORD)


def test_a_user_without_a_password_hash_gets_the_same_403(
    module_seeded_url, client, clock, user, verify_calls
):
    cookie = signed_in(client, user.username)
    run_sql(
        module_seeded_url,
        "UPDATE users SET password_hash = NULL WHERE id = :id",
        {"id": user.id},
    )
    verify_calls.clear()

    response = change(client, cookie, TEST_USER_PASSWORD, NEW_PASSWORD)

    assert code_of(response) == WRONG
    assert len(verify_calls) == 1
    [event] = events(module_seeded_url, record_id=user.id, action=CHANGE_FAILED_EVENT)
    assert event.details["reason"] == "no_password"


@pytest.mark.parametrize(
    ("new", "code"),
    [
        pytest.param("short", "password_too_short", id="too-short"),
        pytest.param("x" * 129, "password_too_long", id="too-long"),
        pytest.param(None, "password_equals_username", id="equals-username"),
    ],
)
def test_a_policy_failure_is_422_before_the_budget_or_any_verification(
    module_seeded_url, client, clock, user, verify_calls, new, code
):
    cookie = signed_in(client, user.username)
    verify_calls.clear()
    used_before = limiter_total(module_seeded_url)

    response = change(client, cookie, WRONG_PASSWORD, new or user.username.upper())

    assert code_of(response) == (422, code)
    assert verify_calls == []
    assert limiter_total(module_seeded_url) == used_before
    assert (
        events(module_seeded_url, record_id=user.id, action=CHANGE_FAILED_EVENT) == []
    )
    assert response.headers.get_list("set-cookie") == []
    assert code_of(me(client, cookie)) == OK


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"current_password": "x"},
        {"current_password": "", "new_password": NEW_PASSWORD},
        {"current_password": "x", "new_password": "y" * 1025},
    ],
)
def test_an_invalid_body_is_422(module_seeded_url, client, clock, user, body):
    cookie = signed_in(client, user.username)
    client.cookies.clear()

    response = client.post(
        "/api/v1/auth/password", json=body, headers=signed_headers(cookie)
    )

    assert code_of(response) == (422, "validation_error")


# --- F2, F4: the shared reauth.user budget ---


@pytest.mark.parametrize("last", ["change", "reauthenticate"])
def test_reauthentication_and_change_share_one_budget(
    module_seeded_url, client, clock, user, verify_calls, last
):
    cookie = signed_in(client, user.username)
    for _ in range(3):
        assert wrong_reauth(client, cookie) == (403, "reauth_failed")
    for _ in range(2):
        assert code_of(change(client, cookie, WRONG_PASSWORD, NEW_PASSWORD)) == WRONG
    verify_calls.clear()

    if last == "change":
        response = change(client, cookie, TEST_USER_PASSWORD, NEW_PASSWORD)
        assert code_of(response) == RATE_LIMITED
        assert 1 <= int(response.headers["Retry-After"]) <= 15 * 60
        assert response.headers.get_list("set-cookie") == []
    else:
        assert wrong_reauth(client, cookie) == RATE_LIMITED

    assert verify_calls == []
    assert code_of(me(client, cookie)) == OK
    assert code_of(login(client, user.username)) == OK  # password unchanged
    assert events(module_seeded_url, record_id=user.id, action=CHANGED_EVENT) == []
    assert (
        len(events(module_seeded_url, record_id=user.id, action=CHANGE_FAILED_EVENT))
        == 2
    )


def test_a_success_gives_its_unit_back(module_seeded_url, client, clock, user):
    cookie = signed_in(client, user.username)
    for _ in range(4):
        assert code_of(change(client, cookie, WRONG_PASSWORD, NEW_PASSWORD)) == WRONG

    rotated = only_set_cookie(change(client, cookie, TEST_USER_PASSWORD, NEW_PASSWORD))

    # 4 failures and a released success: the next failure is still answered.
    assert code_of(change(client, rotated, WRONG_PASSWORD, NEW_PASSWORD)) == WRONG
    assert code_of(change(client, rotated, WRONG_PASSWORD, NEW_PASSWORD)) == (
        RATE_LIMITED
    )


# --- F2: authentication and CSRF ---


def test_without_a_csrf_token_it_is_csrf_failed(module_seeded_url, client, user):
    cookie = signed_in(client, user.username)

    response = change(
        client,
        cookie,
        TEST_USER_PASSWORD,
        NEW_PASSWORD,
        headers={**ORIGIN, **cookie_header(cookie)},
    )

    assert code_of(response) == (403, "csrf_failed")
    assert code_of(login(client, user.username)) == OK


def test_without_a_cookie_it_is_401(module_seeded_url, client):
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": "x", "new_password": NEW_PASSWORD},
        headers=ORIGIN,
    )

    assert code_of(response) == UNAUTHENTICATED


# --- G3: no secret in any log ---


def test_no_password_reaches_a_log(
    module_seeded_url, client, clock, user, caplog, capsys
):
    caplog.set_level(logging.DEBUG)
    cookie = signed_in(client, user.username)
    change(client, cookie, WRONG_PASSWORD, NEW_PASSWORD)  # 403
    change(client, cookie, WRONG_PASSWORD, "short-pw")  # 422
    rotated = only_set_cookie(change(client, cookie, TEST_USER_PASSWORD, NEW_PASSWORD))
    for _ in range(5):
        last = change(client, rotated, WRONG_PASSWORD, NEW_PASSWORD)
    assert code_of(last) == RATE_LIMITED
    client.cookies.clear()
    client.post(LOGOUT, headers=signed_headers(rotated))

    captured = capsys.readouterr()
    texts = [captured.out, captured.err]
    for record in caplog.records:
        texts.append(record.getMessage())
        texts.append(repr(record.__dict__))
    for secret in (TEST_USER_PASSWORD, NEW_PASSWORD, WRONG_PASSWORD, "short-pw"):
        for text in texts:
            assert secret not in text
    assert any("Rate limit exceeded" in text for text in texts)


def test_the_route_is_in_the_openapi_document(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/auth/password"][
        "post"
    ]

    assert set(operation["responses"]) >= {"200", "401", "403", "422", "429"}
    assert "current_password_invalid" in operation["responses"]["403"]["description"]
