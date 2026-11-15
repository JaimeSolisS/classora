"""Progress dashboard views (#84 M5-17 B2, C2, AM1).

Every model is a closed allow-list (``extra="forbid"``) with **one shape for
every audience** (#84 E2): staff, tutors and the family read the same keys.
Percentages and averages are decimals with two places as JSON strings
(``"66.67"``, the #80 ``score_percent`` representation; ADR-0004), or
``null`` when there is nothing to divide by; a plan's progress is a whole
number (``"67"``, #81). ``month`` is the center-local ``"YYYY-MM"``.

Never in any of them: feedback, comments, instructions, submission text,
notes, review or correction reasons, tutor notes, on-behalf data, closeout
statuses, user ids, or another participant's name, id or attendance (#84
B6, C2).
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.workflows.evaluation_schemas import DecimalOut
from app.workflows.learning_plan_schemas import PercentOut

type SessionLogKind = Literal["individual", "group", "course"]
type SessionLogMark = Literal["present", "late", "absent"]
type ProgressPlanStatus = Literal["active", "completed"]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProgressSubject(_Closed):
    id: uuid.UUID
    name: str


# --- B2: GET /students/{id}/progress --------------------------------------------------


class ProgressAttendance(_Closed):
    sessions: int = Field(
        description="In-scope sessions (started, confirmed or completed)."
    )
    present: int
    late: int
    absent: int
    not_recorded: int
    attendance_percent: DecimalOut | None = Field(
        description="(present + late) / (present + late + absent) × 100, two"
        " places; null with no marked session."
    )


class ProgressAttendanceMonth(_Closed):
    month: str = Field(description="Center-local YYYY-MM.", examples=["2031-03"])
    sessions: int
    present: int
    late: int
    absent: int
    not_recorded: int


class ProgressGradeSubject(_Closed):
    subject: ProgressSubject
    evaluation_count: int
    evaluation_average_percent: DecimalOut | None
    assignment_count: int
    assignment_average_percent: DecimalOut | None


class ProgressGradeMonth(_Closed):
    month: str = Field(description="Center-local YYYY-MM.", examples=["2031-03"])
    subject: ProgressSubject
    evaluation_count: int
    evaluation_average_percent: DecimalOut | None
    assignment_count: int
    assignment_average_percent: DecimalOut | None


class ProgressGrades(_Closed):
    evaluation_count: int = Field(
        description="Final, non-voided evaluations with evaluated_on in the range."
    )
    evaluation_average_percent: DecimalOut | None
    assignment_count: int = Field(
        description="Scored homework grades with graded_on in the range."
    )
    assignment_average_percent: DecimalOut | None
    by_subject: list[ProgressGradeSubject] = Field(
        description="Subjects with a value, by name (casefold), then id."
    )
    by_month: list[ProgressGradeMonth] = Field(
        description="(month, subject) pairs with a value, by month, then subject name."
    )


class ProgressHomework(_Closed):
    pending: int
    missing: int
    submitted: int
    graded: int
    late: int
    completion_percent: DecimalOut | None = Field(
        description="(submitted + graded) / (submitted + graded + missing) × 100"
        " over the homework due in the range; null when none counts."
    )


class ProgressPlanItem(_Closed):
    id: uuid.UUID
    title: str
    subject: ProgressSubject
    status: ProgressPlanStatus
    progress_percent: PercentOut = Field(
        description="Done milestones / milestones × 100, a whole number (#81)."
    )
    objectives_complete: int
    objectives_total: int
    milestones_done: int
    milestones_total: int
    milestones_overdue: int = Field(
        description="Milestones not done whose due date is before the center's"
        " today; always 0 for a completed plan."
    )
    target_on: date | None


class ProgressPlans(_Closed):
    active: int
    completed: int
    objectives_complete: int
    objectives_total: int
    milestones_overdue: int = Field(
        description="The overdue milestones of the listed plans; completed plans"
        " count 0, so this is the active plans' sum."
    )
    items: list[ProgressPlanItem] = Field(
        description="At most 20: active plans first by target date (none last),"
        " then completed plans, the latest completed first."
    )


class StudentProgress(_Closed):
    """`GET /api/v1/students/{id}/progress` (#84 B2, AM1)."""

    student_id: uuid.UUID
    from_: date = Field(serialization_alias="from")
    to: date
    subject: ProgressSubject | None = Field(
        description="The subject filter (null: every subject)."
    )
    subjects: list[ProgressSubject] = Field(
        description="Every subject of the range whatever the subject filter"
        " (sessions, counted grades, homework, listed plans), by name"
        " (casefold), then id: the options of a subject filter."
    )
    attendance: ProgressAttendance
    attendance_by_month: list[ProgressAttendanceMonth] = Field(
        description="Every month of the range, oldest first, zeros included."
    )
    grades: ProgressGrades
    homework: ProgressHomework
    plans: ProgressPlans


# --- C2: GET /students/{id}/session-log -----------------------------------------------


class SessionLogTutor(_Closed):
    display_name: str


class SessionLogSummary(_Closed):
    """The approved closeout summary (`null` members: left empty)."""

    topics: str | None
    performance: str | None
    homework: str | None
    next_steps: str | None


class SessionLogItem(_Closed):
    class_session_id: uuid.UUID
    starts_at: datetime
    subject: ProgressSubject
    session_kind: SessionLogKind
    tutor: SessionLogTutor | None = Field(description="The session's current tutor.")
    attendance: SessionLogMark | None = Field(
        description="This student's own mark (null: not recorded)."
    )
    summary: SessionLogSummary | None = Field(
        description="Only while the session's closeout is approved (null when"
        " none, under review, sent back or empty)."
    )
    published_note_count: int = Field(
        description="The tutor's notes about this student in this session that"
        " are shared with the family."
    )


class SessionLog(_Closed):
    """`GET /api/v1/students/{id}/session-log` (#84 C1, C2)."""

    items: list[SessionLogItem]
    next_cursor: str | None
