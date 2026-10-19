"""Helpers for the self-registration tests (#169).

Real requests through the real app (``auth_support.auth_client``); every
request sends its cookie, CSRF token and ``Origin`` by hand, so the client's
own cookie jar never carries a session from one call to the next.
"""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import SetCookie, cookie_header, signed_headers
from tests.integration.conftest import run_sql

REGISTRATIONS = "/api/v1/auth/registrations"
REQUESTS = "/api/v1/registration-requests"

REQUEST_PASSWORD = "requester-password-one"
"""A password that passes the policy (test-only, low entropy)."""


def unique_username(prefix: str = "requester") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


def register(
    client: TestClient,
    *,
    username: str | None = None,
    role: Any = "guardian",
    password: Any = REQUEST_PASSWORD,
    display_name: Any = "Requesting Person",
) -> Response:
    client.cookies.clear()
    return client.post(
        REGISTRATIONS,
        json={
            "username": username or unique_username(),
            "display_name": display_name,
            "password": password,
            "role": role,
        },
    )


def list_requests(client: TestClient, cookie: SetCookie, **params: Any) -> Response:
    client.cookies.clear()
    return client.get(REQUESTS, params=params, headers=cookie_header(cookie))


def decide(
    client: TestClient, cookie: SetCookie, request_id: Any, decision: str
) -> Response:
    client.cookies.clear()
    return client.post(
        f"{REQUESTS}/{request_id}/{decision}", headers=signed_headers(cookie)
    )


def approve(client: TestClient, cookie: SetCookie, request_id: Any) -> Response:
    return decide(client, cookie, request_id, "approve")


def reject(client: TestClient, cookie: SetCookie, request_id: Any) -> Response:
    return decide(client, cookie, request_id, "reject")


def request_row(url: str, username: str) -> Any:
    """The request and its user, by username (``None`` when absent)."""
    rows = run_sql(
        url,
        "SELECT r.id, r.user_id, r.requested_role, r.status, r.decided_at,"
        " r.decided_by_user_id, u.state, u.password_hash, u.display_name"
        " FROM registration_requests r JOIN users u ON u.id = r.user_id"
        " WHERE u.username = :username",
        {"username": username},
    )
    return rows[0] if rows else None


def roles_of(url: str, user_id: Any) -> list[str]:
    rows = run_sql(
        url,
        "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
        " WHERE ur.user_id = :id ORDER BY r.code",
        {"id": user_id},
    )
    return [row.code for row in rows]
