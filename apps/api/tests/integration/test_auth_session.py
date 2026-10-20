"""The current principal from the session cookie, logout and ``/auth/me``
(#23 C, E)."""

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.modules.identity import service as identity
from app.modules.identity.permissions import (
    ACTIVE,
    ADMINISTRATOR,
    ARCHIVED,
    GUARDIAN,
    OWNER,
    PAYROLL_FINALIZE,
    PENDING,
    SUSPENDED,
    TUTOR,
)
from tests.authz.csrf import CSRF_HEADER
from tests.integration.auth_support import (
    LOGOUT,
    ME,
    ORIGIN,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    login,
    only_set_cookie,
    signed_headers,
)
from tests.integration.conftest import run_sql, short_lived_engine


@pytest.fixture
def client(seeded_url: str) -> Iterator[TestClient]:
    with auth_client(seeded_url) as test_client:
        yield test_client


def load_principal(url: str, user_id: uuid.UUID) -> Principal | None:
    async def _load() -> Principal | None:
        engine = short_lived_engine(url)
        try:
            async with create_sessionmaker(engine)() as session:
                return await identity.load_principal(session, user_id)
        finally:
            await engine.dispose()

    return asyncio.run(_load())


def signed_in(client: TestClient, username: str) -> SetCookie:
    response = login(client, username)
    assert response.status_code == 200, response.text
    client.cookies.clear()
    return only_set_cookie(response)


def me(client: TestClient, cookie: SetCookie | None, **headers: str):
    client.cookies.clear()
    sent = {**headers, **(cookie_header(cookie) if cookie else {})}
    return client.get(ME, headers=sent)


def assert_unauthenticated(response) -> None:
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


# --- C4: principal resolution -------------------------------------------------------


def test_a_valid_cookie_gives_the_users_principal(seeded_url, client):
    user = create_user(seeded_url, roles=(TUTOR, GUARDIAN))
    cookie = signed_in(client, user.username)

    response = me(client, cookie)

    assert response.status_code == 200
    principal = load_principal(seeded_url, user.id)
    assert principal is not None
    body = response.json()
    assert body["id"] == str(principal.user_id)
    assert body["roles"] == sorted(principal.roles)
    assert body["permissions"] == sorted(principal.permissions)


def test_the_cookie_jar_round_trip(seeded_url, client):
    """The client's own cookie jar stores the cookie and sends it back."""
    user = create_user(seeded_url)
    client.cookies.clear()

    response = login(client, user.username)
    assert response.status_code == 200
    assert client.get(ME).status_code == 200
    token = response.json()["csrf_token"]
    assert client.post(LOGOUT, headers={CSRF_HEADER: token}).status_code == 204
    assert_unauthenticated(client.get(ME))


def test_no_cookie_is_401(client):
    assert_unauthenticated(me(client, None))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-token",
        "A" * 42,
        "A" * 44,
        "A" * 42 + "=",
        "A" * 42 + "B",  # 43 characters, but not the canonical spelling
        "A" * 42 + "+",
        "%41" * 43,
    ],
    ids=[
        "empty",
        "short text",
        "42 chars",
        "44 chars",
        "padded",
        "non-canonical",
        "not base64url",
        "percent-encoded",
    ],
)
def test_a_malformed_cookie_is_401(client, value):
    client.cookies.clear()
    response = client.get(ME, headers={"Cookie": f"classora_session={value}"})

    assert_unauthenticated(response)


def test_an_unknown_token_is_401(client):
    client.cookies.clear()
    response = client.get(ME, headers={"Cookie": "classora_session=" + "B" * 43})

    assert_unauthenticated(response)


def test_a_revoked_session_is_401(seeded_url, client):
    user = create_user(seeded_url)
    cookie = signed_in(client, user.username)
    run_sql(seeded_url, "UPDATE auth_sessions SET revoked_at = now()")

    assert_unauthenticated(me(client, cookie))


@pytest.mark.parametrize("state", [SUSPENDED, ARCHIVED, PENDING])
def test_a_session_of_a_user_who_is_no_longer_active_is_401(seeded_url, client, state):
    user = create_user(seeded_url)
    cookie = signed_in(client, user.username)
    assert me(client, cookie).status_code == 200

    run_sql(
        seeded_url,
        "UPDATE users SET state = :state WHERE id = :id",
        {"state": state, "id": user.id},
    )

    assert_unauthenticated(me(client, cookie))
    run_sql(
        seeded_url,
        "UPDATE users SET state = :state WHERE id = :id",
        {"state": ACTIVE, "id": user.id},
    )
    assert me(client, cookie).status_code == 200


@pytest.mark.parametrize("secure", [True, False], ids=["secure", "local"])
def test_a_cookie_with_the_other_modes_name_is_401(seeded_url, secure):
    user = create_user(seeded_url)
    with auth_client(seeded_url, secure=secure) as client:
        cookie = signed_in(client, user.username)
        other = "classora_session" if secure else "__Host-classora_session"
        client.cookies.clear()
        response = client.get(ME, headers={"Cookie": f"{other}={cookie.value}"})
        assert_unauthenticated(response)
        assert me(client, cookie).status_code == 200


def test_resolution_writes_nothing(seeded_url, client):
    user = create_user(seeded_url)
    cookie = signed_in(client, user.username)
    query = (
        "SELECT (SELECT count(*) FROM audit_events) AS events,"
        " (SELECT max(updated_at) FROM auth_sessions) AS updated,"
        " (SELECT max(last_seen_at) FROM auth_sessions) AS seen"
    )
    before = tuple(run_sql(seeded_url, query)[0])

    for _ in range(3):
        assert me(client, cookie).status_code == 200

    assert tuple(run_sql(seeded_url, query)[0]) == before


# --- E1-E3: logout and /auth/me ------------------------------------------------------


def test_login_me_logout_me(seeded_url, client):
    user = create_user(seeded_url, display_name="Luis Mora")
    cookie = signed_in(client, user.username)

    first = me(client, cookie)
    assert first.status_code == 200
    assert first.json()["display_name"] == "Luis Mora"
    assert set(first.json()) == {
        "id",
        "username",
        "display_name",
        "roles",
        "permissions",
        "csrf_token",
        "privacy_notice_required",
        "preferences",
    }

    client.cookies.clear()
    logout = client.post(LOGOUT, headers=signed_headers(cookie))
    assert logout.status_code == 204

    assert_unauthenticated(me(client, cookie))
    [row] = run_sql(seeded_url, "SELECT revoked_at FROM auth_sessions")
    assert row.revoked_at is not None


def test_logout_leaves_the_users_other_sessions_valid(seeded_url, client):
    user = create_user(seeded_url)
    phone = signed_in(client, user.username)
    laptop = signed_in(client, user.username)

    client.cookies.clear()
    assert client.post(LOGOUT, headers=signed_headers(phone)).status_code == 204

    assert_unauthenticated(me(client, phone))
    assert me(client, laptop).status_code == 200


def test_logout_without_a_session_is_401(client):
    client.cookies.clear()
    response = client.post(LOGOUT, headers=ORIGIN)

    assert_unauthenticated(response)
    assert response.headers.get_list("set-cookie") == []


def test_logout_twice_is_401_the_second_time(seeded_url, client):
    user = create_user(seeded_url)
    cookie = signed_in(client, user.username)

    client.cookies.clear()
    assert client.post(LOGOUT, headers=signed_headers(cookie)).status_code == 204
    client.cookies.clear()
    assert_unauthenticated(client.post(LOGOUT, headers=signed_headers(cookie)))


@pytest.mark.parametrize(
    ("roles", "delegations"),
    [
        ((TUTOR, GUARDIAN), ()),
        ((ADMINISTRATOR,), (PAYROLL_FINALIZE,)),
        ((OWNER,), ()),
    ],
    ids=["tutor+guardian", "administrator with delegation", "owner"],
)
def test_me_matches_load_principal(seeded_url, client, roles, delegations):
    user = create_user(seeded_url, roles=roles, delegations=delegations)
    cookie = signed_in(client, user.username)

    response = me(client, cookie)

    principal = load_principal(seeded_url, user.id)
    assert principal is not None
    assert response.json()["roles"] == sorted(principal.roles) == sorted(roles)
    assert response.json()["permissions"] == sorted(principal.permissions)
    if delegations:
        assert PAYROLL_FINALIZE in response.json()["permissions"]


def test_me_documents_401_as_problem_details(client):
    document = client.app.openapi()  # type: ignore[attr-defined]

    for path, method in ((ME, "get"), (LOGOUT, "post")):
        responses = document["paths"][path][method]["responses"]
        assert "401" in responses
