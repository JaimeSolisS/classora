"""Helpers for the password reset and change tests (#30).

Real logins through the real app (``auth_support.auth_client``); every
request sends its cookie, CSRF token and ``Origin`` by hand, so the client's
own cookie jar never carries a session from one call to the next.
"""

import hashlib
import re
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.sessions import decode_token
from tests.authz.csrf import TEST_APP_ORIGIN
from tests.integration.auth_support import SetCookie, signed_headers
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in

PASSWORD_RESETS = "/api/v1/password-resets"
RESET_LOOKUP = "/api/v1/auth/password-reset/lookup"
RESET_REDEEM = "/api/v1/auth/password-reset/redeem"
CHANGE_PASSWORD = "/api/v1/auth/password"

RESET_URL_PREFIX = f"{TEST_APP_ORIGIN}/reset#token="
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")

RESET_PASSWORD = "a-reset-new-password"
"""A password that passes the policy (test-only)."""

ISSUED_EVENT = "identity.password_reset_issued"
REDEEMED_EVENT = "identity.password_reset_redeemed"
CHANGED_EVENT = "identity.password_changed"
CHANGE_FAILED_EVENT = "identity.password_change_failed"


def issue_reset(client: TestClient, cookie: SetCookie, username: str) -> Response:
    client.cookies.clear()
    return client.post(
        PASSWORD_RESETS, json={"username": username}, headers=signed_headers(cookie)
    )


def staff_reset(client: TestClient, staff_username: str, username: str) -> Response:
    """Sign ``staff_username`` in, reauthenticate and issue a reset."""
    cookie = sign_in(client, staff_username)
    reauthenticate(client, cookie)
    return issue_reset(client, cookie, username)


def reset_token_of(issued: Response) -> str:
    """The raw token in a ``PasswordResetIssued`` response's link."""
    url = issued.json()["reset_url"]
    assert url.startswith(RESET_URL_PREFIX), url
    token = url.removeprefix(RESET_URL_PREFIX)
    assert TOKEN_PATTERN.fullmatch(token), "not a 43-character base64url token"
    return token


def hex_hash(raw_token: str) -> str:
    token = decode_token(raw_token)
    assert token is not None
    return hashlib.sha256(token).hexdigest()


def reset_lookup(client: TestClient, token: Any) -> Response:
    client.cookies.clear()
    return client.post(RESET_LOOKUP, json={"token": token})


def reset_redeem(
    client: TestClient, token: Any, password: Any = RESET_PASSWORD
) -> Response:
    client.cookies.clear()
    return client.post(RESET_REDEEM, json={"token": token, "password": password})


def change(
    client: TestClient,
    cookie: SetCookie,
    current: str,
    new: str,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    client.cookies.clear()
    return client.post(
        CHANGE_PASSWORD,
        json={"current_password": current, "new_password": new},
        headers=signed_headers(cookie) if headers is None else headers,
    )


def reset_rows(url: str, user_id: Any) -> list[Any]:
    """The user's ``password_reset`` rows, oldest first."""
    return run_sql(
        url,
        "SELECT id, kind, token_hash, used_at, revoked_at, issued_by_user_id"
        " FROM account_tokens WHERE user_id = :id AND kind = 'password_reset'"
        " ORDER BY created_at, id",
        {"id": user_id},
    )


def password_hash_of(url: str, user_id: Any) -> str | None:
    [row] = run_sql(
        url, "SELECT password_hash FROM users WHERE id = :id", {"id": user_id}
    )
    hashed: str | None = row.password_hash
    return hashed


def without_request_id(response: Response) -> dict[str, Any]:
    body = dict(response.json())
    body.pop("request_id")
    return body
