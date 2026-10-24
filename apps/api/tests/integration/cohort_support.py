"""Helpers for the cohort tests (#65 I1-I4).

The cohorts reuse the series timeline (:mod:`tests.integration.series_support`):
"now" is a Sunday 12:00 (Mexico City) and a Monday/Wednesday 16:00 cohort
starts the next day, so the default 12-week horizon holds 24 occurrences.
Templates are created through the API (``POST /course-templates``) so the
snapshot is the real one.
"""

import uuid
from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.catalog_support import get, patch, post, unique_name
from tests.integration.conftest import run_sql
from tests.integration.courses_support import (
    create_template,
    module,
    put,
    template_path,
)

COHORTS = "/api/v1/cohorts"


def new_template(
    client: TestClient,
    cookie: Any,
    subject_ids: list[uuid.UUID],
    *,
    session_count: int = 10,
    session_minutes: int = 90,
    **values: Any,
) -> dict[str, Any]:
    """An active template (``session_count`` × ``session_minutes``) with the
    given subjects, two modules and one assessment; its detail."""
    return create_template(
        client,
        cookie,
        [str(s) for s in subject_ids],
        session_count=session_count,
        session_minutes=session_minutes,
        modules=[
            module("Introducción", planned_sessions=1),
            module("Práctica", is_required=False, materials="Guía"),
        ],
        assessments=[{"title": "Final", "is_required": True, "module_index": None}],
        **values,
    )


def cohort_body(
    *,
    template: Any,
    subject: Any,
    starts_on: date,
    name: str | None = None,
    max_students: int = 12,
    tutor: Any = None,
    room: Any = None,
    start_time: str = "16:00",
    weekdays: list[int] | None = None,
    interval: int = 1,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name or unique_name("Cohorte"),
        "template_id": str(template),
        "subject_id": str(subject),
        "max_students": max_students,
        "tutor_user_id": None if tutor is None else str(tutor),
        "classroom_id": None if room is None else str(room),
        "starts_on": starts_on.isoformat(),
        "start_time": start_time,
        "recurrence": {
            "weekdays": [1, 3] if weekdays is None else weekdays,
            "interval": interval,
        },
        **extra,
    }


def create_cohort(client: TestClient, cookie: Any, payload: dict[str, Any]) -> Response:
    return post(client, cookie, COHORTS, payload)


def read_cohort(client: TestClient, cookie: Any, cohort_id: Any) -> Response:
    return get(client, cookie, f"{COHORTS}/{cohort_id}")


def update_cohort(
    client: TestClient, cookie: Any, cohort_id: Any, payload: dict[str, Any]
) -> Response:
    return patch(client, cookie, f"{COHORTS}/{cohort_id}", payload)


def list_cohorts(client: TestClient, cookie: Any, **params: Any) -> Response:
    return get(client, cookie, COHORTS, **params)


def cohort_sessions(
    client: TestClient, cookie: Any, cohort_id: Any, **params: Any
) -> Response:
    return get(client, cookie, f"{COHORTS}/{cohort_id}/sessions", **params)


def put_template(
    client: TestClient, cookie: Any, template_id: Any, body: Any
) -> Response:
    return put(client, cookie, template_path(template_id), body)


def cohort_rows(url: str, cohort_id: Any) -> list[Any]:
    """The cohort's sessions by start."""
    return run_sql(
        url,
        "SELECT id, state, starts_at, ends_at, series_id, classroom_id, kind,"
        " cohort_id, subject_id, tutor_user_id, scheduled_minutes, version"
        " FROM class_sessions WHERE cohort_id = :id ORDER BY starts_at",
        {"id": str(cohort_id)},
    )


def cohort_series(url: str, cohort_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, kind, cohort_id, count, duration_minutes, subject_id,"
        " tutor_user_id, assignment_status, last_occurrence_on, version"
        " FROM class_series WHERE cohort_id = :id ORDER BY created_at",
        {"id": str(cohort_id)},
    )


def cohort_row(url: str, cohort_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, name, template_id, template_snapshot_id, subject_id,"
        " max_students, version FROM course_cohorts WHERE id = :id",
        {"id": str(cohort_id)},
    )
    return row


def participant_count(url: str, session_ids: list[Any]) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM class_session_participants"
        " WHERE class_session_id = ANY(:ids)",
        {"ids": [uuid.UUID(str(i)) for i in session_ids]},
    )
    return int(row.n)


def table_counts(url: str) -> tuple[int, int, int, int, int]:
    """``(cohorts, snapshots, series, sessions, offers)``."""
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM course_cohorts) AS a,"
        " (SELECT count(*) FROM course_template_snapshots) AS b,"
        " (SELECT count(*) FROM class_series) AS c,"
        " (SELECT count(*) FROM class_sessions) AS d,"
        " (SELECT count(*) FROM assignment_offers) AS e",
    )
    return (int(row.a), int(row.b), int(row.c), int(row.d), int(row.e))


# --- Enrollments (#66) ---------------------------------------------------------------


def enroll(
    client: TestClient, cookie: Any, cohort_id: Any, student_id: Any, **extra: Any
) -> Response:
    return post(
        client,
        cookie,
        f"{COHORTS}/{cohort_id}/enrollments",
        {"student_id": str(student_id), **extra},
    )


def withdraw(
    client: TestClient,
    cookie: Any,
    cohort_id: Any,
    enrollment_id: Any,
    version: int = 1,
    **extra: Any,
) -> Response:
    return post(
        client,
        cookie,
        f"{COHORTS}/{cohort_id}/enrollments/{enrollment_id}/withdraw",
        {"version": version, **extra},
    )


def enrollment_rows(url: str, cohort_id: Any) -> list[Any]:
    """The cohort's enrollment periods, oldest first."""
    return run_sql(
        url,
        "SELECT id, student_id, enrolled_at, enrolled_by_user_id, withdrawn_at,"
        " withdrawn_by_user_id, withdrawal_reason, version"
        " FROM cohort_enrollments WHERE cohort_id = :id ORDER BY enrolled_at, id",
        {"id": str(cohort_id)},
    )


def enrollment_overrides(url: str, enrollment_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, class_session_id, class_series_id, group_enrollment_id, codes,"
        " reason FROM class_session_overrides WHERE cohort_enrollment_id = :id",
        {"id": str(enrollment_id)},
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


def write_counts(url: str) -> tuple[int, int, int, int]:
    """``(enrollments, series participants, session participants,
    overrides)``."""
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM cohort_enrollments) AS a,"
        " (SELECT count(*) FROM class_series_participants) AS b,"
        " (SELECT count(*) FROM class_session_participants) AS c,"
        " (SELECT count(*) FROM class_session_overrides) AS d",
    )
    return (int(row.a), int(row.b), int(row.c), int(row.d))
