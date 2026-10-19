"""``POST /api/v1/auth/reauthenticate`` and ``require_recent_auth`` on the real
resolver (#26 A3, D, C6) with real logins and the ``clock`` fixture.

The sensitive routes are test-only (:data:`reauth_router`, added to a
``build_app()`` instance here, never to the real app).
"""

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Annotated, Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel, ConfigDict

from app.core.authz import Principal, authenticated, require
from app.core.reauth import (
    REAUTH_RESPONSES,
    RecentAuthGuard,
    recent_auth_guard,
    require_recent_auth,
)
from app.modules.identity import passwords
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    OWNER,
    USER_PASSWORD_RESET_ISSUE,
)
from tests.conftest import CLOCK_START
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    LOGIN,
    CreatedUser,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    only_set_cookie,
    signed_headers,
    token_of,
)
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import (
    code_of,
    events,
    no_secret_in_audit,
    signed_in,
)

REAUTHENTICATE = "/api/v1/auth/reauthenticate"
SENSITIVE = "/test/sensitive"
CONDITIONAL = "/test/conditional"
WRONG_PASSWORD = "not-the-password-at-all"

OK = (200, None)
REAUTH_REQUIRED = (403, "reauth_required")
REAUTH_FAILED = (403, "reauth_failed")
SECOND = timedelta(seconds=1)
MINUTE = timedelta(minutes=1)

reauth_router = APIRouter()


class SensitiveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int


class ConditionalBody(BaseModel):
    sensitive: bool


@reauth_router.post(SENSITIVE, responses=REAUTH_RESPONSES)
async def sensitive(
    principal: Annotated[Principal, require(USER_PASSWORD_RESET_ISSUE)],
    _: Annotated[Principal, require_recent_auth("user.password_reset.issue")],
) -> dict[str, str]:
    return {"user_id": str(principal.user_id)}


@reauth_router.put(
    SENSITIVE,
    responses=REAUTH_RESPONSES,
    dependencies=[require_recent_auth("user.password_reset.issue")],
)
async def sensitive_put(
    principal: Annotated[Principal, require(USER_PASSWORD_RESET_ISSUE)],
    body: SensitiveBody,
) -> dict[str, int]:
    return {"amount": body.amount}


@reauth_router.post(CONDITIONAL, responses=REAUTH_RESPONSES)
async def conditional(
    principal: Annotated[Principal, authenticated()],
    guard: Annotated[RecentAuthGuard, recent_auth_guard("user.suspend_or_archive")],
    body: ConditionalBody,
) -> dict[str, bool]:
    if body.sensitive:
        guard.ensure()
    return {"sensitive": body.sensitive}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url, routers=[reauth_router]) as test_client:
        yield test_client


@pytest.fixture
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


def reauthenticate(
    client: TestClient,
    cookie: SetCookie | None,
    password: Any = TEST_USER_PASSWORD,
    *,
    csrf: bool = True,
    body: Any = None,
) -> Response:
    client.cookies.clear()
    headers: dict[str, str] = {}
    if cookie is not None:
        headers = signed_headers(cookie) if csrf else cookie_header(cookie)
    payload = {"password": password} if body is None else body
    return client.post(REAUTHENTICATE, json=payload, headers=headers)


def post_sensitive(
    client: TestClient, cookie: SetCookie | None, *, csrf: bool = True
) -> Response:
    client.cookies.clear()
    headers: dict[str, str] = {}
    if cookie is not None:
        headers = signed_headers(cookie) if csrf else cookie_header(cookie)
    return client.post(SENSITIVE, headers=headers)


def put_sensitive(client: TestClient, cookie: SetCookie, body: Any) -> Response:
    client.cookies.clear()
    return client.put(SENSITIVE, json=body, headers=signed_headers(cookie))


def session_row(url: str, cookie: SetCookie) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, reauthenticated_at, revoked_at FROM auth_sessions"
        " WHERE token_hash = sha256(:token)",
        {"token": token_of(cookie)},
    )
    return row


def reauth_events(url: str, user: CreatedUser, action: str) -> list[Any]:
    return events(url, record_id=user.id, action=action)


def until(response: Response) -> datetime:
    return datetime.fromisoformat(response.json()["reauthenticated_until"])


# --- A3, D2: login is not a reauthentication; success opens the window ----------------


def test_after_login_the_session_is_not_recent(module_seeded_url, client, clock, owner):
    cookie = signed_in(client, owner.username)

    assert code_of(post_sensitive(client, cookie)) == REAUTH_REQUIRED
    assert session_row(module_seeded_url, cookie).reauthenticated_at is None


def test_reauthenticating_opens_a_five_minute_window(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)

    response = reauthenticate(client, cookie)

    assert code_of(response) == OK
    assert response.json() == {"reauthenticated_until": "2026-10-18T12:05:00Z"}
    assert until(response) == CLOCK_START + timedelta(minutes=5)
    row = session_row(module_seeded_url, cookie)
    assert row.reauthenticated_at == CLOCK_START
    assert row.revoked_at is None
    assert code_of(post_sensitive(client, cookie)) == OK


def test_success_is_audited_with_the_session_id(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)

    reauthenticate(client, cookie)

    [event] = reauth_events(module_seeded_url, owner, "identity.reauthenticated")
    assert event.record_type == "users"
    assert event.actor_user_id == owner.id
    assert event.details == {
        "auth_session_id": str(session_row(module_seeded_url, cookie).id)
    }
    assert reauth_events(module_seeded_url, owner, "identity.reauth_failed") == []


def test_the_response_holds_no_token_hash_or_session_id(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)

    response = reauthenticate(client, cookie)

    assert set(response.json()) == {"reauthenticated_until"}
    assert str(session_row(module_seeded_url, cookie).id) not in response.text
    assert cookie.value not in response.text
    assert response.headers.get_list("set-cookie") == []


# --- C2, C7, D7: the window boundary --------------------------------------------------


def test_the_window_is_not_extended_by_activity(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)
    reauthenticate(client, cookie)

    for minute in range(1, 5):
        clock.set(CLOCK_START + minute * MINUTE)
        assert code_of(post_sensitive(client, cookie)) == OK
        assert code_of(put_sensitive(client, cookie, {"amount": 1})) == OK
    clock.set(CLOCK_START + 5 * MINUTE - SECOND)
    assert code_of(post_sensitive(client, cookie)) == OK

    clock.set(CLOCK_START + 5 * MINUTE)
    assert code_of(post_sensitive(client, cookie)) == REAUTH_REQUIRED
    assert code_of(put_sensitive(client, cookie, {"amount": 1})) == REAUTH_REQUIRED
    assert session_row(module_seeded_url, cookie).reauthenticated_at == CLOCK_START


def test_a_later_reauthentication_restarts_the_window(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)
    reauthenticate(client, cookie)

    clock.set(CLOCK_START + 3 * MINUTE)
    again = reauthenticate(client, cookie)

    assert until(again) == CLOCK_START + 8 * MINUTE
    clock.set(CLOCK_START + 8 * MINUTE - SECOND)
    assert code_of(post_sensitive(client, cookie)) == OK
    clock.set(CLOCK_START + 8 * MINUTE)
    assert code_of(post_sensitive(client, cookie)) == REAUTH_REQUIRED
    assert len(reauth_events(module_seeded_url, owner, "identity.reauthenticated")) == 2


# --- D3, D4: failures -----------------------------------------------------------------


def failed_events(url: str, user: CreatedUser) -> list[Any]:
    return reauth_events(url, user, "identity.reauth_failed")


def test_a_wrong_password_without_a_window(module_seeded_url, client, clock, owner):
    cookie = signed_in(client, owner.username)

    response = reauthenticate(client, cookie, WRONG_PASSWORD)

    assert code_of(response) == REAUTH_FAILED
    assert response.json()["detail"] == "The password is incorrect."
    row = session_row(module_seeded_url, cookie)
    assert row.reauthenticated_at is None
    assert row.revoked_at is None
    [event] = failed_events(module_seeded_url, owner)
    assert event.actor_user_id == owner.id
    assert event.record_type == "users"
    assert event.details == {
        "auth_session_id": str(row.id),
        "reason": "invalid_password",
    }
    assert code_of(post_sensitive(client, cookie)) == REAUTH_REQUIRED
    assert reauth_events(module_seeded_url, owner, "identity.reauthenticated") == []


def test_a_wrong_password_leaves_a_live_window_as_it_was(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)
    reauthenticate(client, cookie)

    clock.set(CLOCK_START + MINUTE)
    assert code_of(reauthenticate(client, cookie, WRONG_PASSWORD)) == REAUTH_FAILED

    assert session_row(module_seeded_url, cookie).reauthenticated_at == CLOCK_START
    clock.set(CLOCK_START + 2 * MINUTE)
    assert code_of(post_sensitive(client, cookie)) == OK
    clock.set(CLOCK_START + 5 * MINUTE)
    assert code_of(post_sensitive(client, cookie)) == REAUTH_REQUIRED
    assert len(failed_events(module_seeded_url, owner)) == 1


def test_five_wrong_attempts_are_all_answered_and_audited(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)

    codes = [
        code_of(reauthenticate(client, cookie, f"{WRONG_PASSWORD}-{i}"))
        for i in range(5)
    ]

    assert codes == [REAUTH_FAILED] * 5
    assert [e.details["reason"] for e in failed_events(module_seeded_url, owner)] == [
        "invalid_password"
    ] * 5
    assert session_row(module_seeded_url, cookie).revoked_at is None


def test_a_user_without_a_password(module_seeded_url, client, clock, owner):
    cookie = signed_in(client, owner.username)
    run_sql(
        module_seeded_url,
        "UPDATE users SET password_hash = NULL WHERE id = :id",
        {"id": owner.id},
    )

    response = reauthenticate(client, cookie)

    assert code_of(response) == REAUTH_FAILED
    [event] = failed_events(module_seeded_url, owner)
    assert event.details["reason"] == "no_password"
    assert session_row(module_seeded_url, cookie).reauthenticated_at is None


# --- D6: the mark belongs to one session ----------------------------------------------


def test_another_session_of_the_same_user_is_not_recent(
    module_seeded_url, client, clock, owner
):
    first = signed_in(client, owner.username)
    second = signed_in(client, owner.username)

    reauthenticate(client, first)

    assert code_of(post_sensitive(client, first)) == OK
    assert code_of(post_sensitive(client, second)) == REAUTH_REQUIRED
    assert session_row(module_seeded_url, second).reauthenticated_at is None


def test_a_new_login_rotation_is_not_recent(module_seeded_url, client, clock, owner):
    old = signed_in(client, owner.username)
    reauthenticate(client, old)

    client.cookies.clear()
    response = client.post(
        LOGIN,
        json={"username": owner.username, "password": TEST_USER_PASSWORD},
        headers=cookie_header(old),
    )
    client.cookies.clear()
    new = only_set_cookie(response)

    assert session_row(module_seeded_url, old).revoked_at is not None
    assert session_row(module_seeded_url, new).reauthenticated_at is None
    assert code_of(post_sensitive(client, new)) == REAUTH_REQUIRED


# --- C6: the order on the real resolver -----------------------------------------------


def test_no_cookie_is_401(client, clock):
    assert code_of(post_sensitive(client, None)) == (401, "authentication_required")


def test_an_expired_session_is_session_expired(module_seeded_url, client, clock, owner):
    cookie = signed_in(client, owner.username)
    reauthenticate(client, cookie)

    clock.set(CLOCK_START + timedelta(minutes=30))

    assert code_of(post_sensitive(client, cookie)) == (401, "session_expired")


def test_a_missing_csrf_token_is_csrf_failed(module_seeded_url, client, clock, owner):
    cookie = signed_in(client, owner.username)

    assert code_of(post_sensitive(client, cookie, csrf=False)) == (403, "csrf_failed")
    reauthenticate(client, cookie)
    assert code_of(post_sensitive(client, cookie, csrf=False)) == (403, "csrf_failed")


def test_a_user_without_the_permission_is_denied_before_reauth(
    module_seeded_url, client, clock
):
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
    cookie = signed_in(client, coordinator.username)

    assert code_of(post_sensitive(client, cookie)) == (403, "permission_denied")
    assert code_of(put_sensitive(client, cookie, {"amount": "x"})) == (
        403,
        "permission_denied",
    )
    assert (
        reauth_events(module_seeded_url, coordinator, "identity.reauthenticated") == []
    )


def test_reauth_required_comes_before_validation(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)
    bad = {"amount": "x", "extra": True}

    assert code_of(put_sensitive(client, cookie, bad)) == REAUTH_REQUIRED
    reauthenticate(client, cookie)
    assert code_of(put_sensitive(client, cookie, bad)) == (422, "validation_error")


def test_the_conditional_guard_on_the_real_resolver(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)

    def call(sensitive: bool) -> tuple[int, str | None]:
        client.cookies.clear()
        return code_of(
            client.post(
                CONDITIONAL,
                json={"sensitive": sensitive},
                headers=signed_headers(cookie),
            )
        )

    assert call(False) == OK
    assert call(True) == REAUTH_REQUIRED
    reauthenticate(client, cookie)
    assert call(True) == OK


# --- D1, D5: the endpoint's own checks ------------------------------------------------


def test_reauthenticate_without_a_cookie_is_401(client, clock):
    assert code_of(reauthenticate(client, None)) == (401, "authentication_required")


def test_reauthenticate_without_the_csrf_token(module_seeded_url, client, clock, owner):
    cookie = signed_in(client, owner.username)

    assert code_of(reauthenticate(client, cookie, csrf=False)) == (403, "csrf_failed")
    assert session_row(module_seeded_url, cookie).reauthenticated_at is None
    assert failed_events(module_seeded_url, owner) == []


def test_reauthenticate_with_a_suspended_users_session(
    module_seeded_url, client, clock, owner
):
    cookie = signed_in(client, owner.username)
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'suspended' WHERE id = :id",
        {"id": owner.id},
    )

    assert code_of(reauthenticate(client, cookie)) == (401, "authentication_required")
    assert failed_events(module_seeded_url, owner) == []


@pytest.mark.parametrize(
    "body",
    [{}, {"password": ""}, {"password": "x" * 1025}, {"password": 5}],
    ids=["empty", "blank", "too-long", "not-a-string"],
)
def test_reauthenticate_validates_the_body(
    module_seeded_url, client, clock, owner, body
):
    cookie = signed_in(client, owner.username)

    response = reauthenticate(client, cookie, body=body)

    assert code_of(response) == (422, "validation_error")
    assert failed_events(module_seeded_url, owner) == []


def test_reauthenticate_is_in_the_openapi_document(client):
    operation = client.get("/openapi.json").json()["paths"][REAUTHENTICATE]["post"]

    assert set(operation["responses"]) >= {"200", "401", "403", "422"}
    assert "reauth_failed" in operation["responses"]["403"]["description"]


# --- D2 (#23 D5): exactly one verification per attempt --------------------------------


@pytest.fixture
def verify_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = passwords.verify_password

    def spy(stored_hash: str, password: str) -> bool:
        calls.append(stored_hash)
        return real(stored_hash, password)

    monkeypatch.setattr(passwords, "verify_password", spy)
    return calls


@pytest.mark.parametrize("case", ["success", "invalid_password", "no_password"])
def test_one_verification_per_attempt(
    module_seeded_url, client, clock, owner, verify_calls, case
):
    cookie = signed_in(client, owner.username)
    if case == "no_password":
        run_sql(
            module_seeded_url,
            "UPDATE users SET password_hash = NULL WHERE id = :id",
            {"id": owner.id},
        )
    verify_calls.clear()  # the login's own verification

    password = WRONG_PASSWORD if case == "invalid_password" else TEST_USER_PASSWORD
    reauthenticate(client, cookie, password)

    assert len(verify_calls) == 1
    assert verify_calls[0].startswith("$argon2id$")


def test_the_stored_hash_is_not_changed(module_seeded_url, client, clock, owner):
    cookie = signed_in(client, owner.username)
    [before] = run_sql(
        module_seeded_url,
        "SELECT password_hash, updated_at FROM users WHERE id = :id",
        {"id": owner.id},
    )

    reauthenticate(client, cookie)

    [after] = run_sql(
        module_seeded_url,
        "SELECT password_hash, updated_at FROM users WHERE id = :id",
        {"id": owner.id},
    )
    assert after == before


# --- No secret in the audit trail or the logs -----------------------------------------


def test_no_password_or_token_in_audit_or_logs(
    module_seeded_url, client, clock, owner, caplog, capsys
):
    caplog.set_level(logging.DEBUG)
    cookie = signed_in(client, owner.username)

    reauthenticate(client, cookie, WRONG_PASSWORD)
    reauthenticate(client, cookie)
    post_sensitive(client, cookie)

    no_secret_in_audit(
        module_seeded_url, TEST_USER_PASSWORD, WRONG_PASSWORD, cookie.value
    )
    captured = capsys.readouterr()
    logged = [record.getMessage() for record in caplog.records]
    logged += [repr(record.__dict__) for record in caplog.records]
    text = "\n".join([*logged, captured.out, captured.err])
    for secret in (TEST_USER_PASSWORD, WRONG_PASSWORD, cookie.value):
        assert secret not in text
