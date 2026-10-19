"""Helpers for the user administration tests (#31, #170).

Real logins through the real app (``auth_support.auth_client``); every
request sends its cookie, CSRF token and ``Origin`` by hand, so the client's
own cookie jar never carries a session from one call to the next.
"""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    cookie_header,
    create_user,
    signed_headers,
)
from tests.integration.conftest import run_sql

USERS = "/api/v1/users"


def unique_token(prefix: str = "u") -> str:
    """A short lower-case token that no other test's user contains."""
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def user_path(user_id: Any, action: str | None = None) -> str:
    return f"{USERS}/{user_id}" + (f"/{action}" if action else "")


def list_users(client: TestClient, cookie: SetCookie, **params: Any) -> Response:
    client.cookies.clear()
    return client.get(USERS, params=params, headers=cookie_header(cookie))


def get_user(client: TestClient, cookie: SetCookie, user_id: Any) -> Response:
    client.cookies.clear()
    return client.get(user_path(user_id), headers=cookie_header(cookie))


def patch_user(
    client: TestClient, cookie: SetCookie, user_id: Any, body: dict[str, Any]
) -> Response:
    client.cookies.clear()
    return client.patch(user_path(user_id), json=body, headers=signed_headers(cookie))


def state_action(
    client: TestClient,
    cookie: SetCookie,
    user_id: Any,
    action: str,
    version: Any,
) -> Response:
    """``POST /users/{id}/{action}`` with ``{"version": version}``."""
    client.cookies.clear()
    return client.post(
        user_path(user_id, action),
        json={"version": version},
        headers=signed_headers(cookie),
    )


def put_roles(
    client: TestClient,
    cookie: SetCookie,
    user_id: Any,
    roles: Any,
    version: Any,
) -> Response:
    """``PUT /users/{id}/roles`` with ``{"roles": roles, "version": version}``."""
    client.cookies.clear()
    return client.put(
        user_path(user_id, "roles"),
        json={"roles": roles, "version": version},
        headers=signed_headers(cookie),
    )


def put_delegations(
    client: TestClient,
    cookie: SetCookie,
    user_id: Any,
    permissions: Any,
    version: Any,
) -> Response:
    """``PUT /users/{id}/delegations`` with ``{"permissions": …, "version": …}``."""
    client.cookies.clear()
    return client.put(
        user_path(user_id, "delegations"),
        json={"permissions": permissions, "version": version},
        headers=signed_headers(cookie),
    )


def roles_of(url: str, user_id: Any) -> list[str]:
    """The user's role codes, sorted."""
    rows = run_sql(
        url,
        "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
        " WHERE ur.user_id = :id ORDER BY r.code",
        {"id": user_id},
    )
    return [row.code for row in rows]


def delegations_of(url: str, user_id: Any) -> list[str]:
    """The user's delegated permission codes, sorted."""
    rows = run_sql(
        url,
        "SELECT p.code FROM user_delegations d"
        " JOIN permissions p ON p.id = d.permission_id"
        " WHERE d.user_id = :id ORDER BY p.code",
        {"id": user_id},
    )
    return [row.code for row in rows]


def version_of(url: str, user_id: Any) -> int:
    [row] = run_sql(url, "SELECT version FROM users WHERE id = :id", {"id": user_id})
    version: int = row.version
    return version


def user_row(url: str, user_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT state, version, display_name, email, phone, password_hash"
        " FROM users WHERE id = :id",
        {"id": user_id},
    )
    return row


def make_user(url: str, *roles: str, **overrides: Any) -> CreatedUser:
    """A committed user with ``roles`` (none when empty)."""
    return create_user(url, roles=roles, **overrides)
