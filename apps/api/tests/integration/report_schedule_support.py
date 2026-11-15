"""Shared helpers of the #316 report schedule and placeholder tests.

The center zone is America/Mexico_City (UTC-6). ``NOW`` is Wednesday 5 March
2031, 12:00 there: the current week starts on Monday 3 March, the latest
ended week is 24 February - 2 March, the latest ended month is February.
"""

import itertools
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.authz import Principal
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ROLE_PERMISSIONS,
    TUTOR,
)
from tests.integration.auth_support import CreatedUser, create_user
from tests.integration.conftest import run_sql
from tests.integration.schedule_support import stored
from tests.integration.test_class_session_create import new_student, new_subject

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
TODAY = date(2031, 3, 5)
MONDAY = date(2031, 3, 3)
LAST_MONDAY = date(2031, 2, 24)
FEBRUARY = date(2031, 2, 1)
MARCH = date(2031, 3, 1)
STARTED = NOW - timedelta(minutes=30)
NEXT_MONDAY_RUN = datetime(2031, 3, 10, 7, 0, tzinfo=UTC)
"""Monday 10 March, 01:00 in Mexico City: the week of 3-9 March ended."""

SCHEDULES = "/api/v1/progress-report-schedules"


def schedules_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/progress-report-schedules"


def schedule_path(schedule_id: Any) -> str:
    return f"{SCHEDULES}/{schedule_id}"


def principal(user_id: uuid.UUID, role: str = ACADEMIC_COORDINATOR) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({role}),
        permissions=frozenset(ROLE_PERMISSIONS[role]),
    )


def a_tutor(url: str, name: str = "Tutora Programada") -> CreatedUser:
    return create_user(url, roles=(TUTOR,), display_name=name)


_SLOTS = itertools.count(1)


def slot() -> datetime:
    """A new upcoming instant (two hours apart), so one tutor's sessions
    never overlap."""
    return STARTED + timedelta(hours=2 * next(_SLOTS))


def teaching(
    url: str,
    *,
    tutor: CreatedUser | None = None,
    student: uuid.UUID | None = None,
    subject: uuid.UUID | None = None,
    state: str = "confirmed",
    starts_at: datetime | None = None,
) -> tuple[CreatedUser, uuid.UUID, uuid.UUID]:
    """A stored session of ``tutor`` with ``student`` in ``subject`` (each
    new by default), upcoming unless ``starts_at`` is given: ``(tutor,
    student, subject)``."""
    starts_at = starts_at or slot()
    tutor = tutor or a_tutor(url)
    student = student or new_student(url)
    subject = subject or new_subject(url)
    stored(
        url,
        starts_at,
        state=state,
        students=(student,),
        tutor_user_id=tutor.id,
        subject_id=subject,
    )
    return tutor, student, subject


def insert_schedule(
    url: str,
    *,
    student: uuid.UUID,
    author: uuid.UUID,
    created_by: uuid.UUID | None = None,
    cadence: str = "weekly",
    starts_on: date = LAST_MONDAY,
    subject: uuid.UUID | None = None,
    active: bool = True,
) -> uuid.UUID:
    """A schedule row written directly (a past ``starts_on`` the routes
    refuse, so its latest period is due at once)."""
    [row] = run_sql(
        url,
        "INSERT INTO progress_report_schedules (student_id, author_user_id,"
        " subject_id, cadence, starts_on, is_active, created_by_user_id)"
        " VALUES (:st, :a, :sub, :c, :s, :active, :by) RETURNING id",
        {
            "st": student,
            "a": author,
            "sub": subject,
            "c": cadence,
            "s": starts_on,
            "active": active,
            "by": created_by or author,
        },
    )
    return uuid.UUID(str(row.id))


FACTS = '{"schema_version": 1}'


def insert_report(
    url: str,
    *,
    student: uuid.UUID,
    author: uuid.UUID,
    kind: str = "weekly",
    period_start: date = LAST_MONDAY,
    subject: uuid.UUID | None = None,
    schedule: uuid.UUID | None = None,
    status: str = "draft",
    deleted: bool = False,
    reviewer: uuid.UUID | None = None,
) -> uuid.UUID:
    """A report row in ``status`` with the columns its CHECKs need."""
    period_end = (
        period_start + timedelta(days=6)
        if kind == "weekly"
        else (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        - timedelta(days=1)
    )
    reviewer = reviewer or author
    reviewed = status != "draft"
    approved = status in ("approved_for_family", "retracted")
    params: dict[str, Any] = {
        "st": student,
        "u": author,
        "sub": subject,
        "kind": kind,
        "start": period_start,
        "end": period_end,
        "status": status,
        "facts": FACTS if reviewed else None,
        "at": NOW if reviewed else None,
        "cr_at": NOW if status == "changes_requested" else None,
        "cr_by": reviewer if status == "changes_requested" else None,
        "cr_reason": "Más detalle" if status == "changes_requested" else None,
        "approved_at": NOW if approved else None,
        "approved_by": reviewer if approved else None,
        "retracted_at": NOW if status == "retracted" else None,
        "retracted_by": reviewer if status == "retracted" else None,
        "retract_reason": "Equivocado" if status == "retracted" else None,
        "deleted_at": NOW if deleted else None,
        "deleted_by": author if deleted else None,
        "schedule": schedule,
    }
    [row] = run_sql(
        url,
        "INSERT INTO progress_reports (student_id, author_user_id, subject_id,"
        " kind, period_start, period_end, status, summary, facts,"
        " facts_computed_at, submitted_at, changes_requested_at,"
        " changes_requested_by_user_id, changes_requested_reason, approved_at,"
        " approved_by_user_id, retracted_at, retracted_by_user_id,"
        " retract_reason, deleted_at, deleted_by_user_id, schedule_id)"
        " VALUES (:st, :u, :sub, :kind, :start, :end, :status, 'Resumen',"
        " CAST(:facts AS jsonb), :at, :at, :cr_at, :cr_by, :cr_reason,"
        " :approved_at, :approved_by, :retracted_at, :retracted_by,"
        " :retract_reason, :deleted_at, :deleted_by, :schedule) RETURNING id",
        params,
    )
    return uuid.UUID(str(row.id))


def placeholders(url: str, schedule: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, student_id, author_user_id, subject_id, kind, period_start,"
        " period_end, status, summary, strengths, areas_for_improvement,"
        " next_steps, facts, deleted_at FROM progress_reports"
        " WHERE schedule_id = :s ORDER BY period_start",
        {"s": schedule},
    )


def due_notifications(url: str, report_ids: list[Any] | None = None) -> list[Any]:
    rows = run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = 'academics.progress_report_due'",
    )
    if report_ids is None:
        return rows
    wanted = {str(report_id) for report_id in report_ids}
    return [row for row in rows if row.params.get("report_id") in wanted]


def set_zone(url: str, zone: str) -> None:
    run_sql(url, "UPDATE business_settings SET time_zone = :zone", {"zone": zone})
