"""Helpers for the guardian profile and link tests (#34).

Real logins through the real app (``auth_support.auth_client``); requests go
through the ``catalog_support`` helpers (and :func:`delete` here), which send
the cookie (and, when unsafe, the CSRF token and ``Origin``) by hand.
"""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import SetCookie, signed_headers
from tests.integration.catalog_support import patch, post
from tests.integration.conftest import run_sql
from tests.integration.student_support import STUDENTS, suffix

GUARDIANS = "/api/v1/guardians"
GUARDIAN_RECORD_TYPES = ("guardians", "student_guardians")


def guardian_path(guardian_id: Any) -> str:
    return f"{GUARDIANS}/{guardian_id}"


def links_path(student_id: Any) -> str:
    return f"{STUDENTS}/{student_id}/guardians"


def link_path(student_id: Any, guardian_id: Any) -> str:
    return f"{links_path(student_id)}/{guardian_id}"


def delete(client: TestClient, cookie: SetCookie, path: str) -> Response:
    client.cookies.clear()
    return client.delete(path, headers=signed_headers(cookie))


def guardian_body(**overrides: Any) -> dict[str, Any]:
    """A valid ``GuardianCreate`` body with only the names."""
    body: dict[str, Any] = {
        "given_names": "Laura",
        "family_names": f"Tutora {suffix()}",
    }
    body.update(overrides)
    return body


def create_guardian(
    client: TestClient, cookie: SetCookie, **overrides: Any
) -> dict[str, Any]:
    """``POST /guardians`` that must succeed; returns the detail."""
    response = post(client, cookie, GUARDIANS, guardian_body(**overrides))
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def link(
    client: TestClient,
    cookie: SetCookie,
    student_id: Any,
    guardian_id: Any,
    relationship: str = "mother",
) -> Response:
    return post(
        client,
        cookie,
        links_path(student_id),
        {"guardian_id": str(guardian_id), "relationship": relationship},
    )


def linked(
    client: TestClient,
    cookie: SetCookie,
    student_id: Any,
    guardian_id: Any,
    relationship: str = "mother",
) -> dict[str, Any]:
    """A link that must succeed; returns the item."""
    response = link(client, cookie, student_id, guardian_id, relationship)
    assert response.status_code == 201, response.text
    item: dict[str, Any] = response.json()
    return item


def unlink(
    client: TestClient, cookie: SetCookie, student_id: Any, guardian_id: Any
) -> Response:
    return delete(client, cookie, link_path(student_id, guardian_id))


def set_active(
    client: TestClient, cookie: SetCookie, guardian: dict[str, Any], active: bool
) -> dict[str, Any]:
    """Deactivate or reactivate at the guardian's current version; returns the
    new detail."""
    response = patch(
        client,
        cookie,
        guardian_path(guardian["id"]),
        {"version": guardian["version"], "is_active": active},
    )
    assert response.status_code == 200, response.text
    detail: dict[str, Any] = response.json()
    return detail


def guardian_row(url: str, guardian_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT given_names, family_names, email, phone, user_id, is_active,"
        " version, updated_at FROM guardians WHERE id = :id",
        {"id": guardian_id},
    )
    return row


def link_rows(url: str, student_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT guardian_id, relationship FROM student_guardians"
        " WHERE student_id = :id ORDER BY guardian_id",
        {"id": student_id},
    )


def guardian_count(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM guardians")
    return int(row.n)


def guardian_event_count(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE record_type IN ('guardians', 'student_guardians')",
    )
    return int(row.n)


def ids(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def as_uuid(value: Any) -> uuid.UUID:
    return uuid.UUID(str(value))
