"""Helpers for the invitation tests (#29).

Real logins through the real app (``auth_support.auth_client``); every
request sends its cookie, CSRF token and ``Origin`` by hand, so the client's
own cookie jar never carries a session from one call to the next.
"""

import re
import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.authz.csrf import TEST_APP_ORIGIN
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import SetCookie, signed_headers
from tests.integration.lifecycle_support import signed_in

INVITATIONS = "/api/v1/invitations"
LOOKUP = "/api/v1/auth/invitation/lookup"
REDEEM = "/api/v1/auth/invitation/redeem"
REAUTHENTICATE = "/api/v1/auth/reauthenticate"

INVITE_URL_PREFIX = f"{TEST_APP_ORIGIN}/invite#token="
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")

NEW_PASSWORD = "a-brand-new-password"
"""A password that passes the policy (test-only)."""


def unique_username(prefix: str = "invitee") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


def reissue_path(user_id: uuid.UUID | str) -> str:
    return f"{INVITATIONS}/{user_id}/reissue"


def sign_in(client: TestClient, username: str) -> SetCookie:
    return signed_in(client, username, TEST_USER_PASSWORD)


def reauthenticate(client: TestClient, cookie: SetCookie) -> None:
    client.cookies.clear()
    response = client.post(
        REAUTHENTICATE,
        json={"password": TEST_USER_PASSWORD},
        headers=signed_headers(cookie),
    )
    assert response.status_code == 200, response.text


def invite(
    client: TestClient,
    cookie: SetCookie,
    *,
    role: str,
    username: str | None = None,
    display_name: str = "Invited Person",
) -> Response:
    client.cookies.clear()
    return client.post(
        INVITATIONS,
        json={
            "username": username or unique_username(),
            "display_name": display_name,
            "role": role,
        },
        headers=signed_headers(cookie),
    )


def reissue(client: TestClient, cookie: SetCookie, user_id: Any) -> Response:
    client.cookies.clear()
    return client.post(reissue_path(user_id), headers=signed_headers(cookie))


def list_invitations(client: TestClient, cookie: SetCookie, **params: Any) -> Response:
    client.cookies.clear()
    return client.get(
        INVITATIONS, params=params, headers={"Cookie": f"{cookie.name}={cookie.value}"}
    )


def token_of(issued: Response) -> str:
    """The raw token in an ``InvitationIssued`` response's link."""
    url = issued.json()["invitation_url"]
    assert url.startswith(INVITE_URL_PREFIX), url
    token = url.removeprefix(INVITE_URL_PREFIX)
    assert TOKEN_PATTERN.fullmatch(token), "not a 43-character base64url token"
    return token


def lookup(client: TestClient, token: Any) -> Response:
    client.cookies.clear()
    return client.post(LOOKUP, json={"token": token})


def redeem(client: TestClient, token: Any, password: Any = NEW_PASSWORD) -> Response:
    client.cookies.clear()
    return client.post(REDEEM, json={"token": token, "password": password})
