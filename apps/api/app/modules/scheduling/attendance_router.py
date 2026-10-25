"""Attendance routes (#76; :mod:`app.modules.scheduling.attendance` gives the
orders, :mod:`app.modules.scheduling.attendance_rules` the record policy).

- ``GET /api/v1/class-sessions/{id}/attendance``: ``authenticated()`` + the
  record policy (the session's current tutor, or a ``schedule.view``
  holder; anyone else ``404``).
- ``PUT /api/v1/class-sessions/{id}/attendance``: ``authenticated()`` + the
  record policy: the current tutor only (``403 attendance_tutor_only`` for
  other visible callers, ``404`` for the rest).
- ``POST /api/v1/class-sessions/{id}/attendance/corrections``:
  ``require("attendance.correct")`` (Owner, Administrator, Academic
  Coordinator).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service
from app.modules.scheduling.attendance_schemas import (
    AttendanceCorrectionCreate,
    AttendanceMarks,
    SessionAttendance,
)

ATTENDANCE_CORRECT: Final = "attendance.correct"

router = APIRouter(prefix="/api/v1/class-sessions", tags=["attendance"])

_NOT_FOUND: Final = problem_response_doc(
    "No such class session, or not one whose attendance you may see (not_found)."
)
_BLOCKED: Final = (
    " the session is not confirmed or completed (attendance_not_recordable,"
    " details.state) or hasn't started (attendance_not_started)"
)


@router.get(
    "/{class_session_id}/attendance",
    responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND},
)
async def get_class_session_attendance(
    principal: Annotated[Principal, authenticated()],
    class_session_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> SessionAttendance:
    """Every participant's attendance (present, late, absent or not marked
    yet), who marked it and when, and each staff correction with its
    reason. For the session's current tutor, or staff with
    schedule.view."""
    return await service.get_session_attendance(
        uow.session, principal, class_session_id
    )


@router.put(
    "/{class_session_id}/attendance",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Only the session's current tutor marks attendance"
            " (attendance_tutor_only); a failed Origin or X-CSRF-Token check"
            " (csrf_failed); privacy_notice_required."
        ),
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The session changed since it was read (version_conflict);"
            + _BLOCKED
            + "; it is completed (attendance_locked: ask the center); or a mark"
            " would change a status the center corrected (attendance_corrected,"
            " details.student_id). Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, no marks or more than 500, a student listed twice"
            " (duplicate_student), or a student who isn't in the session"
            " (student_not_participant at marks[i].student_id)."
        ),
    },
)
async def mark_class_session_attendance(
    principal: Annotated[Principal, authenticated()],
    class_session_id: uuid.UUID,
    body: AttendanceMarks,
    uow: UnitOfWorkDep,
) -> SessionAttendance:
    """Mark participants present, late or absent once the class has started
    (no deadline while it is confirmed). Unlisted participants keep their
    mark; an identical mark changes nothing. Any change bumps the session's
    version. The session's state never changes."""
    return await service.record_attendance(
        uow.session,
        actor=principal,
        class_session_id=class_session_id,
        version=body.version,
        marks=body.marks,
    )


@router.post(
    "/{class_session_id}/attendance/corrections",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such class session (not_found)."),
        409: problem_response_doc(
            "The session changed since it was read (version_conflict); or"
            + _BLOCKED
            + ". Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, the reason (reason_required, reason_too_long), a student"
            " who isn't in the session (student_not_participant) or the status"
            " the student already has (attendance_unchanged)."
        ),
    },
)
async def correct_class_session_attendance(
    principal: Annotated[Principal, require(ATTENDANCE_CORRECT)],
    class_session_id: uuid.UUID,
    body: AttendanceCorrectionCreate,
    uow: UnitOfWorkDep,
) -> SessionAttendance:
    """Correct one participant's attendance with a reason (also a completed
    session's, or a first mark). The correction is kept with its reason and
    audited; the tutor can no longer change that student's mark. The
    session's state never changes."""
    return await service.correct_attendance(
        uow.session,
        actor=principal,
        class_session_id=class_session_id,
        body=body,
    )
