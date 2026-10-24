"""Helpers for the tutoring group tests (#56 J1-J5).

The groups reuse the series timeline (:mod:`tests.integration.series_support`):
"now" is a Sunday 12:00 (Mexico City) and the group's Monday/Wednesday
16:00 series starts the next day, so the default 12-week horizon holds 24
occurrences.
"""

import uuid
from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.catalog_support import get, patch, post
from tests.integration.conftest import run_sql

GROUPS = "/api/v1/groups"


def group_body(
    *,
    subject: uuid.UUID,
    starts_on: date,
    students: list[uuid.UUID] | None = None,
    max_students: int = 6,
    name: str | None = None,
    tutor: uuid.UUID | None = None,
    room: uuid.UUID | None = None,
    start_time: str = "16:00",
    duration_minutes: int = 60,
    weekdays: list[int] | None = None,
    count: int | None = None,
    until: date | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name or f"Grupo {uuid.uuid4().hex[:10]}",
        "subject_id": str(subject),
        "max_students": max_students,
        "tutor_user_id": None if tutor is None else str(tutor),
        "classroom_id": None if room is None else str(room),
        "starts_on": starts_on.isoformat(),
        "start_time": start_time,
        "duration_minutes": duration_minutes,
        "recurrence": {
            "weekdays": [1, 3] if weekdays is None else weekdays,
            "interval": 1,
            "until": None if until is None else until.isoformat(),
            "count": count,
        },
        "student_ids": [str(s) for s in students or []],
        **extra,
    }


def create_group(client: TestClient, cookie: Any, payload: dict[str, Any]) -> Response:
    return post(client, cookie, GROUPS, payload)


def read_group(client: TestClient, cookie: Any, group_id: Any) -> Response:
    return get(client, cookie, f"{GROUPS}/{group_id}")


def update_group(
    client: TestClient, cookie: Any, group_id: Any, payload: dict[str, Any]
) -> Response:
    return patch(client, cookie, f"{GROUPS}/{group_id}", payload)


def enroll(
    client: TestClient,
    cookie: Any,
    group_id: Any,
    student_id: Any,
    **extra: Any,
) -> Response:
    return post(
        client,
        cookie,
        f"{GROUPS}/{group_id}/enrollments",
        {"student_id": str(student_id), **extra},
    )


def withdraw(
    client: TestClient,
    cookie: Any,
    group_id: Any,
    enrollment_id: Any,
    version: int = 1,
    **extra: Any,
) -> Response:
    return post(
        client,
        cookie,
        f"{GROUPS}/{group_id}/enrollments/{enrollment_id}/withdraw",
        {"version": version, **extra},
    )


def group_rows(url: str, group_id: Any) -> list[Any]:
    """The group's sessions by start: id, state, instants, series, room."""
    return run_sql(
        url,
        "SELECT id, state, starts_at, ends_at, series_id, classroom_id, version,"
        " tutor_user_id, kind, group_id"
        " FROM class_sessions WHERE group_id = :id ORDER BY starts_at",
        {"id": str(group_id)},
    )


def participants(url: str, session_id: Any) -> set[str]:
    return {
        str(row.student_id)
        for row in run_sql(
            url,
            "SELECT student_id FROM class_session_participants"
            " WHERE class_session_id = :id",
            {"id": str(session_id)},
        )
    }


def series_of(url: str, group_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, kind, group_id, version FROM class_series WHERE group_id = :id"
        " ORDER BY created_at",
        {"id": str(group_id)},
    )


def series_students(url: str, series_id: Any) -> set[str]:
    return {
        str(row.student_id)
        for row in run_sql(
            url,
            "SELECT student_id FROM class_series_participants"
            " WHERE class_series_id = :id",
            {"id": str(series_id)},
        )
    }


def enrollments(url: str, group_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, student_id, enrolled_at, enrolled_by_user_id, withdrawn_at,"
        " withdrawn_by_user_id, withdrawal_reason, version"
        " FROM group_enrollments WHERE group_id = :id ORDER BY enrolled_at, id",
        {"id": str(group_id)},
    )


def enrollment_overrides(url: str, enrollment_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, class_session_id, class_series_id, codes, reason"
        " FROM class_session_overrides WHERE group_enrollment_id = :id",
        {"id": str(enrollment_id)},
    )


def set_state(url: str, session_id: Any, state: str) -> None:
    """Force a stored occurrence into ``state`` (test setup only)."""
    if state == "cancelled":
        run_sql(
            url,
            "UPDATE class_sessions SET state = 'cancelled', cancelled_at = now(),"
            " cancellation_reason = 'Prueba' WHERE id = :id",
            {"id": str(session_id)},
        )
        return
    run_sql(
        url,
        # #67 A1: a confirmed or completed row needs first_confirmed_at.
        "UPDATE class_sessions SET state = :state, first_confirmed_at ="
        " coalesce(first_confirmed_at, now()) WHERE id = :id"
        if state in {"confirmed", "completed"}
        else "UPDATE class_sessions SET state = :state WHERE id = :id",
        {"id": str(session_id), "state": state},
    )
