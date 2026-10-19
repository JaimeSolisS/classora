"""Helpers for the family grouping tests (#172).

Real logins through the real app (``auth_support.auth_client``); requests go
through the ``catalog_support`` helpers and :func:`put` / ``guardian_support
.delete``, which send the cookie (and, when unsafe, the CSRF token and
``Origin``) by hand.
"""

from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import SetCookie, signed_headers
from tests.integration.catalog_support import patch, post
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.student_support import suffix

FAMILIES = "/api/v1/families"
FAMILY_RECORD_TYPES = ("families", "family_students", "family_guardians")


def family_path(family_id: Any) -> str:
    return f"{FAMILIES}/{family_id}"


def member_path(family_id: Any, kind: str, member_id: Any) -> str:
    """``kind`` is ``students`` or ``guardians``."""
    return f"{family_path(family_id)}/{kind}/{member_id}"


def put(client: TestClient, cookie: SetCookie, path: str) -> Response:
    client.cookies.clear()
    return client.put(path, headers=signed_headers(cookie))


def family_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": f"Familia Prueba {suffix()}"}
    body.update(overrides)
    return body


def create_family(
    client: TestClient, cookie: SetCookie, **overrides: Any
) -> dict[str, Any]:
    """``POST /families`` that must succeed; returns the detail."""
    response = post(client, cookie, FAMILIES, family_body(**overrides))
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def add(
    client: TestClient, cookie: SetCookie, family_id: Any, kind: str, member_id: Any
) -> Response:
    return put(client, cookie, member_path(family_id, kind, member_id))


def added(
    client: TestClient, cookie: SetCookie, family_id: Any, kind: str, member_id: Any
) -> None:
    """An add that must succeed."""
    response = add(client, cookie, family_id, kind, member_id)
    assert response.status_code == 204, response.text


def remove(
    client: TestClient, cookie: SetCookie, family_id: Any, kind: str, member_id: Any
) -> Response:
    return delete(client, cookie, member_path(family_id, kind, member_id))


def set_family_active(
    client: TestClient, cookie: SetCookie, family: dict[str, Any], active: bool
) -> dict[str, Any]:
    response = patch(
        client,
        cookie,
        family_path(family["id"]),
        {"version": family["version"], "is_active": active},
    )
    assert response.status_code == 200, response.text
    detail: dict[str, Any] = response.json()
    return detail


def family_row(url: str, family_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT name, is_active, version, updated_at FROM families WHERE id = :id",
        {"id": family_id},
    )
    return row


def members(url: str, family_id: Any) -> tuple[set[str], set[str]]:
    """The family's (student ids, guardian ids) in the database."""
    students = run_sql(
        url,
        "SELECT student_id FROM family_students WHERE family_id = :id",
        {"id": family_id},
    )
    guardians = run_sql(
        url,
        "SELECT guardian_id FROM family_guardians WHERE family_id = :id",
        {"id": family_id},
    )
    return (
        {str(row.student_id) for row in students},
        {str(row.guardian_id) for row in guardians},
    )


def family_count(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM families")
    return int(row.n)


def family_event_count(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE record_type IN ('families', 'family_students', 'family_guardians')",
    )
    return int(row.n)


def student_ids(detail: dict[str, Any]) -> list[str]:
    return [item["student_id"] for item in detail["students"]]


def guardian_ids(detail: dict[str, Any]) -> list[str]:
    return [item["guardian_id"] for item in detail["guardians"]]
