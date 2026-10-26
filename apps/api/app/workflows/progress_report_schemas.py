"""Progress report bodies and closed views (#83 C1, C3, D, E1, E2, F2, F3,
AM2).

Every model is a closed allow-list (``extra="forbid"``). Narrative text is
plain and trimmed; whitespace-only text is ``null``. Percentages are JSON
strings (``"75.50"``; a plan's progress a whole number, ``"50"``;
ADR-0004).

Two families of shapes:

- the **working** shapes (:class:`ProgressReportView`,
  :class:`ProgressReportListItem`): the author and ``progress_report.review``
  holders, with the status, the version, the review history and reasons;
- the **family-safe** shapes (:class:`ProgressReportFamilyView`,
  :class:`ProgressReportFamilyListItem`): approved reports only, the same for
  every audience (staff, tutors, the student, guardians), never the status,
  version, ids of people, the approver, reasons, retraction data,
  ``submitted_at`` or a live preview.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic_core import PydanticCustomError

from app.modules.academics.service import progress_report_rules as rules
from app.workflows.evaluation_schemas import DecimalOut
from app.workflows.learning_plan_schemas import PercentOut

type ReportKind = Literal["weekly", "monthly", "on_demand"]
type ReportStatus = Literal[
    "draft", "submitted", "changes_requested", "approved_for_family", "retracted"
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _text(maximum: int) -> Any:
    def validate(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        cleaned = rules.clean(value)
        if cleaned is not None and len(cleaned) > maximum:
            raise PydanticCustomError(
                "too_long", "At most {max} characters.", {"max": maximum}
            )
        return cleaned

    return BeforeValidator(validate)


Summary = Annotated[str | None, _text(rules.SUMMARY_MAX)]
Section = Annotated[str | None, _text(rules.SECTION_MAX)]


# --- Bodies (C1, C3, D) ---------------------------------------------------------------


class ProgressReportCreate(_Closed):
    """`POST /api/v1/progress-reports` (#83 C1): a draft report about one of
    your current students. Nobody sees it until you submit it, and families
    only once a reviewer approves it."""

    student_id: uuid.UUID
    kind: ReportKind = Field(
        description="weekly (a Monday to Sunday week), monthly (a calendar"
        " month) or on_demand (any period of at most 366 days)."
    )
    period_start: date = Field(
        description="weekly: a Monday; monthly: the 1st; not after the"
        " center's today (period_invalid / period_in_future)."
    )
    period_end: date | None = Field(
        default=None,
        description="Required for on_demand; weekly and monthly derive it (a"
        " different value is period_invalid).",
    )
    subject_id: uuid.UUID | None = Field(
        default=None,
        description="Optional: an active subject you currently teach the"
        " student (subject_unknown / report_subject_not_assigned). It never"
        " changes.",
    )
    summary: Summary = Field(
        default=None, description="Up to 4000 characters; required to submit."
    )
    strengths: Section = Field(default=None, description="Up to 2000 characters.")
    areas_for_improvement: Section = Field(
        default=None, description="Up to 2000 characters."
    )
    next_steps: Section = Field(default=None, description="Up to 2000 characters.")


class ProgressReportUpdate(_Closed):
    """`PATCH /api/v1/progress-reports/{id}` (#83 C3): the narrative of a
    draft or a report sent back for changes. Keys left out don't change;
    `null` or blank text clears a field. The student, kind, period and
    subject never change."""

    version: int
    summary: Summary = Field(default=None, description="Up to 4000 characters.")
    strengths: Section = Field(default=None, description="Up to 2000 characters.")
    areas_for_improvement: Section = Field(
        default=None, description="Up to 2000 characters."
    )
    next_steps: Section = Field(default=None, description="Up to 2000 characters.")


class ProgressReportVersion(_Closed):
    """`POST …/submit` and `…/approve` (#83 D2, D3): the version you read."""

    version: int


class ProgressReportReason(_Closed):
    """`POST …/request-changes` and `…/retract` (#83 D4, D5)."""

    version: int
    reason: str = Field(
        description="1-1000 characters after trimming (reason_required /"
        " too_long). The author reads it; families never do."
    )


# --- Shared parts ---------------------------------------------------------------------


class ProgressReportPerson(_Closed):
    display_name: str


class ProgressReportStudent(_Closed):
    id: uuid.UUID
    display_name: str


class ProgressReportSubject(_Closed):
    id: uuid.UUID
    name: str


class FactsPeriod(_Closed):
    start: date
    end: date


class FactsAttendance(_Closed):
    sessions: int
    present: int
    late: int
    absent: int
    not_recorded: int


class FactsEvaluationSubject(_Closed):
    subject: ProgressReportSubject
    count: int
    average_percent: DecimalOut | None


class FactsEvaluationEntry(_Closed):
    evaluated_on: date
    subject: ProgressReportSubject
    evaluation_kind: str
    template_name: str
    score_percent: DecimalOut


class FactsEvaluations(_Closed):
    count: int
    average_percent: DecimalOut | None
    by_subject: list[FactsEvaluationSubject]
    entries: list[FactsEvaluationEntry] = Field(description="At most 50, newest first.")
    truncated: bool


class FactsAssignments(_Closed):
    count: int
    pending: int
    missing: int
    submitted: int
    graded: int
    late: int
    average_percent: DecimalOut | None


class FactsLearningPlan(_Closed):
    title: str
    subject: ProgressReportSubject
    status: str
    progress_percent: PercentOut
    objectives_completed: int
    objectives_total: int
    milestones_done: int
    milestones_total: int


class ProgressReportFacts(_Closed):
    """The facts the server compiled for the report's student, period and
    subject (#83 B2, schema version 1): aggregates of records the family can
    already see. Frozen at each submit; families see them exactly as
    approved."""

    schema_version: Literal[1]
    period: FactsPeriod
    subject: ProgressReportSubject | None
    attendance: FactsAttendance
    evaluations: FactsEvaluations
    assignments: FactsAssignments
    learning_plans: list[FactsLearningPlan] = Field(description="At most 20.")
    learning_plans_truncated: bool


# --- Working views (E1, E2) -----------------------------------------------------------


class ProgressReportChangesRequested(_Closed):
    at: datetime
    by: ProgressReportPerson
    reason: str


class ProgressReportApproved(_Closed):
    at: datetime
    by: ProgressReportPerson


class ProgressReportRetracted(_Closed):
    at: datetime
    by: ProgressReportPerson
    reason: str


class ProgressReportView(_Closed):
    """One progress report (#83 E1): the author and
    `progress_report.review` holders only."""

    id: uuid.UUID
    version: int
    status: ReportStatus
    student: ProgressReportStudent
    author: ProgressReportPerson
    is_own: bool
    kind: ReportKind
    period_start: date
    period_end: date
    subject: ProgressReportSubject | None
    summary: str | None
    strengths: str | None
    areas_for_improvement: str | None
    next_steps: str | None
    facts: ProgressReportFacts | None = Field(
        description="Frozen at the last submit; null for a draft never submitted."
    )
    facts_computed_at: datetime | None
    facts_preview: ProgressReportFacts | None = Field(
        description="A live compile, for the author while the report is a"
        " draft or sent back; null otherwise."
    )
    submitted_at: datetime | None
    changes_requested: ProgressReportChangesRequested | None = Field(
        description="The last request for changes (kept until approval)."
    )
    approved: ProgressReportApproved | None
    retracted: ProgressReportRetracted | None
    created_at: datetime
    updated_at: datetime
    can_edit: bool
    can_submit: bool
    can_delete: bool
    can_approve: bool
    can_request_changes: bool
    can_retract: bool


class ProgressReportListItem(_Closed):
    """A report in the working list (#83 E2): no text."""

    id: uuid.UUID
    version: int
    status: ReportStatus
    student: ProgressReportStudent
    author: ProgressReportPerson
    kind: ReportKind
    period_start: date
    period_end: date
    subject: ProgressReportSubject | None
    submitted_at: datetime | None
    approved_at: datetime | None


class ProgressReportPage(_Closed):
    items: list[ProgressReportListItem]
    next_cursor: str | None


# --- Family-safe views (F2, F3, AM2) --------------------------------------------------


class ProgressReportFamilyView(_Closed):
    """An approved progress report (#83 F2): the same closed shape for every
    audience."""

    id: uuid.UUID
    student_id: uuid.UUID
    kind: ReportKind
    period_start: date
    period_end: date
    subject: ProgressReportSubject | None
    author: ProgressReportPerson
    summary: str | None
    strengths: str | None
    areas_for_improvement: str | None
    next_steps: str | None
    facts: ProgressReportFacts
    approved_at: datetime


class ProgressReportFamilyListItem(_Closed):
    id: uuid.UUID
    kind: ReportKind
    period_start: date
    period_end: date
    subject: ProgressReportSubject | None
    author: ProgressReportPerson
    approved_at: datetime


class ProgressReportFamilyPage(_Closed):
    """A student's approved reports (#83 F3), with the caller's create hints
    (AM2: not affected by the filters or the cursor)."""

    items: list[ProgressReportFamilyListItem]
    next_cursor: str | None
    can_create: bool = Field(
        description="Whether you may write a report about this student (a"
        " tutor who currently teaches them)."
    )
    creatable_subjects: list[ProgressReportSubject] = Field(
        description="The active subjects you currently teach the student, by"
        " name; empty without can_create (a report without a subject needs"
        " none)."
    )
