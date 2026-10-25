"""Attendance bodies (#76 B2, C1, D1).

Every model is a **closed allow-list** (``extra="forbid"``, output models
too; ``tests/integration/test_attendance.py`` compares the JSON keys). Times
are instants with an offset (ADR-0004).
"""

import uuid
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.modules.scheduling.attendance_rules import AttendanceBlock, AttendanceStatus
from app.modules.scheduling.models import ATTENDANCE_REASON_MAX_LENGTH
from app.modules.scheduling.schedule_schemas import ScheduleSubject
from app.modules.scheduling.schemas import SessionKindCode, SessionStateCode

MARKS_MAX: Final = 500


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttendancePerson(_Closed):
    """Who marked or corrected: the display name only."""

    display_name: str


class AttendanceStudent(_Closed):
    """A participant of the session."""

    id: uuid.UUID
    display_name: str


class AttendanceCorrectionItem(_Closed):
    """One staff correction, with its reason."""

    id: uuid.UUID
    previous_status: AttendanceStatus | None = Field(
        description="`null` when staff made the first mark."
    )
    new_status: AttendanceStatus
    reason: str
    corrected_by: AttendancePerson
    created_at: datetime


class AttendanceItem(_Closed):
    """One participant's attendance (`status` `null`: not marked yet)."""

    student: AttendanceStudent
    status: AttendanceStatus | None
    marked_by: AttendancePerson | None
    marked_at: datetime | None
    corrected: bool = Field(
        description=(
            "Staff corrected this mark; the tutor can no longer change it"
            " (attendance_corrected)."
        )
    )
    corrections: list[AttendanceCorrectionItem] = Field(description="Oldest first.")


class SessionAttendance(_Closed):
    """A session's attendance (#76 B2): every participant, by display name,
    marked or not. `can_mark`: you are the current tutor and the tutor may
    mark now; `can_correct`: you hold attendance.correct and a correction is
    possible now; `block`: why attendance can't be written now for you
    (`attendance_not_recordable`, `attendance_not_started`,
    `attendance_locked`), or `null`. Any change bumps the session's
    `version`."""

    class_session_id: uuid.UUID
    version: int
    kind: SessionKindCode
    state: SessionStateCode
    starts_at: datetime
    ends_at: datetime
    subject: ScheduleSubject
    can_mark: bool
    can_correct: bool
    block: AttendanceBlock | None
    items: list[AttendanceItem]


class AttendanceMark(_Closed):
    """One participant's mark."""

    student_id: uuid.UUID
    status: AttendanceStatus


class AttendanceMarks(_Closed):
    """`PUT /api/v1/class-sessions/{id}/attendance` (#76 C1): 1-500 marks,
    each student once (duplicate_student). Unlisted participants keep their
    current mark; a mark can't be cleared."""

    version: int = Field(description="The session's version, as last read.")
    marks: list[AttendanceMark] = Field(min_length=1, max_length=MARKS_MAX)

    @field_validator("marks")
    @classmethod
    def _unique_students(cls, marks: list[AttendanceMark]) -> list[AttendanceMark]:
        if len({mark.student_id for mark in marks}) != len(marks):
            raise PydanticCustomError(
                "duplicate_student", "List each student only once."
            )
        return marks


def _check_reason(value: Any) -> Any:
    if value is None:
        raise PydanticCustomError("reason_required", "Give a reason.")
    if not isinstance(value, str):
        return value
    reason = value.strip()
    if not reason:
        raise PydanticCustomError("reason_required", "Give a reason.")
    if len(reason) > ATTENDANCE_REASON_MAX_LENGTH:
        raise PydanticCustomError(
            "reason_too_long", "The reason is at most 1000 characters."
        )
    return reason


class AttendanceCorrectionCreate(_Closed):
    """`POST /api/v1/class-sessions/{id}/attendance/corrections` (#76 D1):
    the new status of one participant and why (trimmed, 1-1000 characters:
    reason_required / reason_too_long)."""

    version: int = Field(description="The session's version, as last read.")
    student_id: uuid.UUID
    status: AttendanceStatus
    reason: str = Field(description="Why: 1-1000 characters, trimmed.")

    @model_validator(mode="before")
    @classmethod
    def _missing_reason(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {"reason": None} | data
        return data

    _reason = field_validator("reason", mode="before")(_check_reason)
