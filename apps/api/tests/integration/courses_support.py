"""Helpers for the course template tests (#64).

Real logins through the real app; every request sends its cookie (and,
when unsafe, its CSRF token and ``Origin``) by hand, as in the catalog
tests.
"""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import SetCookie, signed_headers
from tests.integration.catalog_support import (
    create_level,
    create_subject,
    unique_name,
)
from tests.integration.conftest import run_sql

TEMPLATES = "/api/v1/course-templates"

COURSE_TABLES = (
    "course_templates",
    "course_template_subjects",
    "course_template_modules",
    "course_template_assessments",
    "course_template_snapshots",
)


def template_path(template_id: Any) -> str:
    return f"{TEMPLATES}/{template_id}"


def put(client: TestClient, cookie: SetCookie, path: str, body: Any) -> Response:
    client.cookies.clear()
    return client.put(path, json=body, headers=signed_headers(cookie))


def subjects(client: TestClient, cookie: SetCookie, count: int = 2) -> list[str]:
    """``count`` new active subjects of a new level; their ids."""
    level = create_level(client, cookie)
    return [create_subject(client, cookie, level["id"])["id"] for _ in range(count)]


def module(title: str, **values: Any) -> dict[str, Any]:
    return {"title": title, "is_required": True, **values}


def assessment(title: str, **values: Any) -> dict[str, Any]:
    return {"title": title, "is_required": True, **values}


def template_body(subject_ids: list[str], **values: Any) -> dict[str, Any]:
    """A valid ``POST`` body: three modules, one required assessment on
    module 2, 80% attendance."""
    return {
        "name": unique_name("Curso"),
        "description": "Un curso de prueba",
        "subject_ids": subject_ids,
        "session_count": 10,
        "session_minutes": 90,
        "min_attendance_percent": 80,
        "modules": [
            module("Introducción", subject_id=subject_ids[0], planned_sessions=2),
            module("Desarrollo", planned_sessions=5, materials="Libro, capítulo 2"),
            module("Cierre", is_required=False),
        ],
        "assessments": [assessment("Parcial", module_index=1, min_score_percent=60)],
        **values,
    }


def create_template(
    client: TestClient, cookie: SetCookie, subject_ids: list[str], **values: Any
) -> dict[str, Any]:
    """``POST /course-templates`` that must succeed; returns the detail."""
    client.cookies.clear()
    response = client.post(
        TEMPLATES,
        json=template_body(subject_ids, **values),
        headers=signed_headers(cookie),
    )
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def write_body(detail: dict[str, Any]) -> dict[str, Any]:
    """The ``PUT`` body that keeps ``detail`` as it is (ids kept,
    ``module_index`` from each assessment's ``module_id``)."""
    module_ids = [item["id"] for item in detail["modules"]]
    return {
        "version": detail["version"],
        "name": detail["name"],
        "description": detail["description"],
        "subject_ids": [subject["id"] for subject in detail["subjects"]],
        "session_count": detail["session_count"],
        "session_minutes": detail["session_minutes"],
        "min_attendance_percent": detail["min_attendance_percent"],
        "modules": [
            {
                key: item[key]
                for key in (
                    "id",
                    "title",
                    "description",
                    "subject_id",
                    "planned_sessions",
                    "is_required",
                    "materials",
                )
            }
            for item in detail["modules"]
        ],
        "assessments": [
            {
                "id": item["id"],
                "title": item["title"],
                "module_index": None
                if item["module_id"] is None
                else module_ids.index(item["module_id"]),
                "is_required": item["is_required"],
                "min_score_percent": item["min_score_percent"],
            }
            for item in detail["assessments"]
        ],
    }


def template_events(
    url: str, template_id: Any
) -> list[tuple[str, str, dict[str, Any]]]:
    """``(record_type, action, changes)`` of the audit events of one
    template and its children, in order."""
    rows = run_sql(
        url,
        "SELECT record_type, action, changes FROM audit_events"
        " WHERE record_type = ANY(:tables) AND (record_id = :id"
        " OR coalesce(changes->'template_id'->>'new',"
        " changes->'template_id'->>'old') = :text_id"
        " OR record_id IN (SELECT id FROM course_template_modules"
        " WHERE template_id = :id UNION SELECT id FROM course_template_assessments"
        " WHERE template_id = :id))"
        " ORDER BY occurred_at, id",
        {
            "tables": list(COURSE_TABLES),
            "id": uuid.UUID(str(template_id)),
            "text_id": str(template_id),
        },
    )
    return [(row.record_type, row.action, row.changes) for row in rows]


def course_event_count(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE record_type = ANY(:tables)",
        {"tables": list(COURSE_TABLES)},
    )
    return int(row.n)


def field_errors(response: Response) -> list[tuple[list[Any], str]]:
    """``(path, code)`` of a ``422 validation_error``."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "validation_error"
    return [(error["path"], error["code"]) for error in body["errors"]]
