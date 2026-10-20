"""``POST /api/v1/auth/login`` and the session cookie (#23 B, D)."""

import hashlib
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient
from httpx import Response

from app.core.errors import PROBLEM_DETAILS_REF, PROBLEM_JSON
from app.modules.identity import passwords, sessions
from app.modules.identity.permissions import ARCHIVED, PENDING, SUSPENDED
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    LOGIN,
    LOGOUT,
    ME,
    ORIGIN,
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

INVALID = {
    "status": 401,
    "code": "invalid_credentials",
    "detail": "The username or password is incorrect.",
}


@pytest.fixture
def client(seeded_url: str) -> Iterator[TestClient]:
    with auth_client(seeded_url) as test_client:
        yield test_client


def session_rows(url: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, user_id, token_hash, revoked_at FROM auth_sessions"
        " ORDER BY created_at, id",
    )


def assert_invalid_credentials(response: Response) -> None:
    assert response.status_code == 401, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert {k: body[k] for k in INVALID} == INVALID
    assert set(body) == {"type", "title", "status", "code", "request_id", "detail"}
    assert response.headers.get_list("set-cookie") == []


# --- B5: the cookie in both modes ---------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "max_age"),
    [(("owner",), "43200"), (("tutor",), "2592000")],
    ids=["staff", "extended"],
)
@pytest.mark.parametrize(
    ("secure", "name", "flags"),
    [
        (
            True,
            "__Host-classora_session",
            {"httponly", "max-age", "path", "samesite", "secure"},
        ),
        (False, "classora_session", {"httponly", "max-age", "path", "samesite"}),
    ],
    ids=["secure", "local"],
)
def test_login_sets_exactly_one_session_cookie(
    seeded_url, secure, name, flags, roles, max_age
):
    user = create_user(seeded_url, roles=roles)
    with auth_client(seeded_url, secure=secure) as client:
        response = login(client, user.username)

    assert response.status_code == 200, response.text
    cookie = only_set_cookie(response)
    assert cookie.name == name
    assert len(cookie.value) == 43
    assert set(cookie.attributes) == flags  # no Domain or Expires (#23, #25)
    # Max-Age is the class's absolute timeout (#25 E1).
    assert cookie.attributes["max-age"] == max_age
    assert cookie.attributes["path"] == "/"
    assert cookie.attributes["samesite"].lower() == "lax"
    [row] = session_rows(seeded_url)
    assert bytes(row.token_hash) == hashlib.sha256(token_of(cookie)).digest()
    assert row.user_id == user.id
    assert row.revoked_at is None


@pytest.mark.parametrize(
    ("secure", "name", "flags"),
    [
        (
            True,
            "__Host-classora_session",
            {"httponly", "max-age", "path", "samesite", "secure"},
        ),
        (False, "classora_session", {"httponly", "max-age", "path", "samesite"}),
    ],
    ids=["secure", "local"],
)
def test_logout_clears_the_cookie_with_the_same_attributes(
    seeded_url, secure, name, flags
):
    user = create_user(seeded_url)
    with auth_client(seeded_url, secure=secure) as client:
        cookie = only_set_cookie(login(client, user.username))
        client.cookies.clear()
        response = client.post(LOGOUT, headers=signed_headers(cookie))

    assert response.status_code == 204
    assert response.content == b""
    cleared = only_set_cookie(response)
    assert cleared.name == name
    assert cleared.value in ("", '""')
    assert set(cleared.attributes) == flags
    assert cleared.attributes["max-age"] == "0"
    assert cleared.attributes["path"] == "/"
    assert cleared.attributes["samesite"].lower() == "lax"


@pytest.mark.parametrize("secure", [True, False], ids=["secure", "local"])
def test_the_cookie_of_the_other_mode_is_ignored(seeded_url, secure):
    user = create_user(seeded_url)
    with auth_client(seeded_url, secure=not secure) as other:
        cookie = only_set_cookie(login(other, user.username))
    with auth_client(seeded_url, secure=secure) as client:
        client.cookies.clear()
        response = client.get(ME, headers=cookie_header(cookie))

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


# --- D2: success --------------------------------------------------------------------


def test_success_returns_the_session_user(seeded_url, client):
    user = create_user(seeded_url, roles=("tutor", "guardian"), display_name="Ana Ruiz")

    response = login(client, f"  {user.username.upper()} ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "id": str(user.id),
        "username": user.username,
        "display_name": "Ana Ruiz",
        "roles": ["guardian", "tutor"],
        "permissions": ["consent.record", "guardian.view", "student.view"],
        "csrf_token": csrf_for(only_set_cookie(response)),
        "privacy_notice_required": False,
        "preferences": {"language": None, "theme": None},
        "center_time_zone": "America/Mexico_City",
    }
    cookie = only_set_cookie(response)
    assert cookie.value not in response.text
    assert hashlib.sha256(token_of(cookie)).hexdigest() not in response.text
    [row] = session_rows(seeded_url)
    assert str(row.id) not in response.text


def test_every_login_creates_a_new_session(seeded_url, client):
    user = create_user(seeded_url)

    first = only_set_cookie(login(client, user.username))
    client.cookies.clear()
    second = only_set_cookie(login(client, user.username))

    assert first.value != second.value
    rows = session_rows(seeded_url)
    assert len(rows) == 2
    assert [row.revoked_at for row in rows] == [None, None]


def test_login_revokes_the_session_on_the_request_cookie(seeded_url, client):
    user = create_user(seeded_url)
    old = only_set_cookie(login(client, user.username))

    client.cookies.clear()
    response = client.post(
        LOGIN,
        json={"username": user.username, "password": TEST_USER_PASSWORD},
        headers={**ORIGIN, **cookie_header(old)},
    )

    assert response.status_code == 200
    new = only_set_cookie(response)
    assert new.value != old.value
    client.cookies.clear()
    assert client.get(ME, headers=cookie_header(old)).status_code == 401
    assert client.get(ME, headers=cookie_header(new)).status_code == 200
    old_row, new_row = session_rows(seeded_url)
    assert old_row.revoked_at is not None
    assert new_row.revoked_at is None


def test_login_as_another_user_revokes_the_previous_users_session(seeded_url, client):
    first_user = create_user(seeded_url)
    second_user = create_user(seeded_url)
    first = only_set_cookie(login(client, first_user.username))

    client.cookies.clear()
    response = client.post(
        LOGIN,
        json={"username": second_user.username, "password": TEST_USER_PASSWORD},
        headers={**ORIGIN, **cookie_header(first)},
    )

    assert response.status_code == 200
    client.cookies.clear()
    assert client.get(ME, headers=cookie_header(first)).status_code == 401
    rows = {row.user_id: row for row in session_rows(seeded_url)}
    assert rows[first_user.id].revoked_at is not None
    assert rows[second_user.id].revoked_at is None


def test_a_planted_token_is_never_adopted(seeded_url, client):
    """Session fixation: a token chosen by the client before login is useless."""
    user = create_user(seeded_url)
    planted = "A" * 43
    client.cookies.clear()

    response = client.post(
        LOGIN,
        json={"username": user.username, "password": TEST_USER_PASSWORD},
        headers={**ORIGIN, "Cookie": f"classora_session={planted}"},
    )

    assert response.status_code == 200
    issued = only_set_cookie(response)
    assert issued.value != planted
    [row] = session_rows(seeded_url)
    planted_token = sessions.decode_token(planted)
    assert planted_token is not None
    planted_hash = hashlib.sha256(planted_token).digest()
    assert bytes(row.token_hash) != planted_hash
    client.cookies.clear()
    response = client.get(ME, headers={"Cookie": f"classora_session={planted}"})
    assert response.status_code == 401


def test_login_upgrades_an_outdated_hash(seeded_url, client):
    weaker = PasswordHasher(
        time_cost=1, memory_cost=8192, parallelism=1, hash_len=32, type=Type.ID
    )
    old_hash = weaker.hash(TEST_USER_PASSWORD)
    assert passwords.needs_rehash(old_hash)
    user = create_user(seeded_url, password_hash=old_hash)

    assert login(client, user.username).status_code == 200

    [row] = run_sql(
        seeded_url, "SELECT password_hash FROM users WHERE id = :id", {"id": user.id}
    )
    assert row.password_hash != old_hash
    assert row.password_hash.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert not passwords.needs_rehash(row.password_hash)
    assert passwords.verify_password(row.password_hash, TEST_USER_PASSWORD)


def test_a_current_hash_is_left_alone(seeded_url, client):
    user = create_user(seeded_url)
    [before] = run_sql(
        seeded_url,
        "SELECT password_hash, version FROM users WHERE id = :id",
        {"id": user.id},
    )

    assert login(client, user.username).status_code == 200

    [after] = run_sql(
        seeded_url,
        "SELECT password_hash, version FROM users WHERE id = :id",
        {"id": user.id},
    )
    assert tuple(after) == tuple(before)


# --- D3, D4: failures look the same -------------------------------------------------

FailureCase = Callable[[str], tuple[str, str]]
"""Given the test database URL, returns the (username, password) to submit."""


def _unknown(url: str) -> tuple[str, str]:
    return "nobody.here", TEST_USER_PASSWORD


def _invalid_username(url: str) -> tuple[str, str]:
    return "x", TEST_USER_PASSWORD


def _non_ascii_username(url: str) -> tuple[str, str]:
    return "usuário", TEST_USER_PASSWORD


def _no_password(url: str) -> tuple[str, str]:
    return create_user(url, password_hash=None).username, TEST_USER_PASSWORD


def _wrong_password(url: str) -> tuple[str, str]:
    return create_user(url).username, "wrong-password-1"


def _state(state: str) -> FailureCase:
    def _case(url: str) -> tuple[str, str]:
        return create_user(url, state=state).username, TEST_USER_PASSWORD

    return _case


FAILURES: dict[str, FailureCase] = {
    "unknown username": _unknown,
    "username failing normalization": _invalid_username,
    "non-ASCII username": _non_ascii_username,
    "user without a password": _no_password,
    "wrong password": _wrong_password,
    "pending user, right password": _state(PENDING),
    "suspended user, right password": _state(SUSPENDED),
    "archived user, right password": _state(ARCHIVED),
}


@pytest.mark.parametrize("case", FAILURES.values(), ids=FAILURES.keys())
def test_every_failure_is_invalid_credentials(seeded_url, client, case):
    username, password = case(seeded_url)

    response = login(client, username, password)

    assert_invalid_credentials(response)
    assert session_rows(seeded_url) == []


def comparable(response: Response) -> tuple[int, dict[str, Any], frozenset[str]]:
    body = response.json()
    body.pop("request_id")
    return response.status_code, body, frozenset(response.headers.keys())


def test_no_account_enumeration(seeded_url, client):
    """Every failure answers the same status, body (apart from request_id) and
    header names."""
    responses = {}
    for name, case in FAILURES.items():
        username, password = case(seeded_url)
        responses[name] = comparable(login(client, username, password))

    names = list(responses)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            assert responses[first] == responses[second], (first, second)


def test_a_failure_leaves_an_existing_session_as_it_is(seeded_url, client):
    user = create_user(seeded_url)
    cookie = only_set_cookie(login(client, user.username))

    client.cookies.clear()
    response = client.post(
        LOGIN,
        json={"username": user.username, "password": "wrong-password-1"},
        headers={**ORIGIN, **cookie_header(cookie)},
    )

    assert_invalid_credentials(response)
    client.cookies.clear()
    assert client.get(ME, headers=cookie_header(cookie)).status_code == 200
    [row] = session_rows(seeded_url)
    assert row.revoked_at is None


# --- D5: exactly one verification per request ---------------------------------------


@pytest.fixture
def verify_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = passwords.verify_password

    def spy(stored_hash: str, password: str) -> bool:
        calls.append(stored_hash)
        return real(stored_hash, password)

    monkeypatch.setattr(passwords, "verify_password", spy)
    return calls


@pytest.mark.parametrize("case", FAILURES.values(), ids=FAILURES.keys())
def test_one_verification_per_failed_login(seeded_url, client, verify_calls, case):
    username, password = case(seeded_url)

    assert login(client, username, password).status_code == 401

    assert len(verify_calls) == 1
    assert verify_calls[0].startswith("$argon2id$v=19$m=19456,t=2,p=1$")


def test_one_verification_per_successful_login(seeded_url, client, verify_calls):
    user = create_user(seeded_url)

    assert login(client, user.username).status_code == 200

    assert len(verify_calls) == 1


# --- D1, D6: the body and the contract ----------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"username": "someone"},
        {"password": TEST_USER_PASSWORD},
        {"username": "", "password": TEST_USER_PASSWORD},
        {"username": "someone", "password": ""},
        {"username": "u" * 257, "password": TEST_USER_PASSWORD},
        {"username": "someone", "password": "p" * 1025},
        {"username": 5, "password": TEST_USER_PASSWORD},
    ],
    ids=[
        "empty",
        "no password",
        "no username",
        "empty username",
        "empty password",
        "username too long",
        "password too long",
        "username not a string",
    ],
)
def test_a_malformed_body_is_a_validation_error(seeded_url, client, body):
    response = client.post(LOGIN, json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.headers.get_list("set-cookie") == []
    assert TEST_USER_PASSWORD not in response.text
    # Only the reference seed's education-level events (#32 B2) exist.
    assert (
        run_sql(
            seeded_url,
            "SELECT count(*) FROM audit_events"
            " WHERE record_type IS DISTINCT FROM 'education_levels'",
        )[0][0]
        == 0
    )


def test_the_longest_accepted_values_reach_the_credential_check(seeded_url, client):
    response = login(client, "u" * 256, "p" * 1024)

    assert_invalid_credentials(response)


def test_login_documents_401_403_and_422_as_problem_details(client):
    document = client.app.openapi()  # type: ignore[attr-defined]
    responses = document["paths"][LOGIN]["post"]["responses"]

    for status in ("401", "403", "422"):
        schema = responses[status]["content"][PROBLEM_JSON]["schema"]
        assert schema == {"$ref": PROBLEM_DETAILS_REF}
    assert set(document["components"]["schemas"]["SessionUser"]["properties"]) == {
        "id",
        "username",
        "display_name",
        "roles",
        "permissions",
        "csrf_token",
        "privacy_notice_required",
        "preferences",
        "center_time_zone",
    }
