"""Helpers for the self-registration tests (#169), reviewed through the
approval inbox since #40 (``identity.registration`` approval requests).

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
APPROVALS = "/api/v1/approval-requests"
REGISTRATION_TYPE = "identity.registration"

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
    """The approval list, by default of registrations only
    (``request_type=None``: every type)."""
    client.cookies.clear()
    params.setdefault("request_type", REGISTRATION_TYPE)
    params = {key: value for key, value in params.items() if value is not None}
    return client.get(APPROVALS, params=params, headers=cookie_header(cookie))


def decide(
    client: TestClient,
    cookie: SetCookie,
    approval_id: Any,
    decision: str,
    body: Any = None,
) -> Response:
    """``POST /approval-requests/{approval_id}/{decision}``."""
    client.cookies.clear()
    return client.post(
        f"{APPROVALS}/{approval_id}/{decision}",
        headers=signed_headers(cookie),
        json=body,
    )


def approve(client: TestClient, cookie: SetCookie, approval_id: Any) -> Response:
    return decide(client, cookie, approval_id, "approve")


def reject(client: TestClient, cookie: SetCookie, approval_id: Any) -> Response:
    return decide(client, cookie, approval_id, "reject")


def request_row(url: str, username: str) -> Any:
    """The request, its approval request (``approval_id``,
    ``approval_status``, ``resolved_at``, ``resolved_by_user_id``) and its
    user, by username (``None`` when absent)."""
    rows = run_sql(
        url,
        "SELECT r.id, r.user_id, r.requested_role, r.status, r.decided_at,"
        " r.decided_by_user_id, u.state, u.password_hash, u.display_name,"
        " a.id AS approval_id, a.status AS approval_status, a.resolved_at,"
        " a.resolved_by_user_id, a.requester_user_id"
        " FROM registration_requests r JOIN users u ON u.id = r.user_id"
        " LEFT JOIN approval_requests a ON a.subject_id = r.id"
        " AND a.request_type = 'identity.registration'"
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
