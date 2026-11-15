"""Helpers for the student profile tests (#33).

Real logins through the real app (``auth_support.auth_client``); requests go
through the ``catalog_support`` helpers, which send the cookie (and, when
unsafe, the CSRF token and ``Origin``) by hand. Tutor assignments are
injected with :func:`assigned` (the F3 seam: one dependency override).
"""

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.families.assignments import get_tutor_assignment_source
from tests.integration.auth_support import SetCookie
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql

STUDENTS = "/api/v1/students"
FAMILY_RECORD_TYPES = ("students", "student_emergency_contacts")


def student_path(student_id: Any) -> str:
    return f"{STUDENTS}/{student_id}"


def suffix() -> str:
    """A unique word, so tests sharing a database never see each other's rows."""
    return f"z{uuid.uuid4().hex[:10]}"


def contact(
    name: str = "María Pérez",
    relationship: str = "Madre",
    phone: str = "+52 55 1234 5678",
    **extra: Any,
) -> dict[str, Any]:
    return {"name": name, "relationship": relationship, "phone": phone, **extra}


def student_body(**overrides: Any) -> dict[str, Any]:
    """A valid ``StudentCreate`` body with only the required fields."""
    body: dict[str, Any] = {
        "given_names": "José",
        "family_names": f"Prueba {suffix()}",
        "birthdate": "2012-05-04",
        "emergency_contacts": [contact()],
    }
    body.update(overrides)
    return body


def create_student(
    client: TestClient, cookie: SetCookie, **overrides: Any
) -> dict[str, Any]:
    """``POST /students`` that must succeed; returns the detail."""
    response = post(client, cookie, STUDENTS, student_body(**overrides))
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def contacts_of(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = detail["personal"]["emergency_contacts"]
    return contacts


def as_update_items(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detail contacts as ``PATCH`` items (with their ids, no position)."""
    return [
        {key: c[key] for key in ("id", "name", "relationship", "phone")}
        for c in contacts
    ]


def student_row(url: str, student_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT given_names, family_names, birthdate, email, phone, school_name,"
        " school_grade, education_level_id, is_active, version, updated_at"
        " FROM students WHERE id = :id",
        {"id": student_id},
    )
    return row


def contact_rows(url: str, student_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, name, relationship, phone, position"
        " FROM student_emergency_contacts WHERE student_id = :id ORDER BY position",
        {"id": student_id},
    )


def student_count(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM students")
    return int(row.n)


def family_event_count(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE record_type IN ('students', 'student_emergency_contacts')",
    )
    return int(row.n)


class FixedAssignments:
    """A test :class:`TutorAssignmentSource`: a fixed tutor → students map."""

    def __init__(self, mapping: Mapping[uuid.UUID, set[Any]]) -> None:
        self.mapping = {
            tutor: frozenset(uuid.UUID(str(s)) for s in students)
            for tutor, students in mapping.items()
        }
        self.calls = 0

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        self.calls += 1
        return self.mapping.get(user_id, frozenset())


@contextmanager
def assigned(
    client: TestClient, mapping: Mapping[uuid.UUID, set[Any]]
) -> Iterator[FixedAssignments]:
    """Inject tutor assignments into the client's app for the block."""
    source = FixedAssignments(mapping)
    app = client.app
    app.dependency_overrides[get_tutor_assignment_source] = lambda: source  # type: ignore[attr-defined]
    try:
        yield source
    finally:
        app.dependency_overrides.pop(get_tutor_assignment_source, None)  # type: ignore[attr-defined]
