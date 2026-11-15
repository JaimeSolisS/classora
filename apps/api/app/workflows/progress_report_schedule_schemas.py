"""Progress report schedule bodies and views (#316 C1-C3).

Every model is a closed allow-list (``extra="forbid"``). Schedules are a
staff configuration (``progress_report.review``): the views carry names and
ids, never report text. ``latest_period.report_id`` is set only for a report
a reviewer may open (submitted, sent back, approved or retracted): drafts
and deleted placeholders are the author's (#83 E5).
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type ScheduleCadence = Literal["weekly", "monthly"]
type ScheduleIneligibleReason = Literal[
    "student_inactive",
    "author_not_tutor",
    "author_not_current",
    "subject_inactive",
    "subject_not_assigned",
]
type SchedulePeriodStatus = Literal[
    "not_created",
    "draft",
    "deleted",
    "submitted",
    "changes_requested",
    "approved_for_family",
    "retracted",
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Bodies (C2, C3) ------------------------------------------------------------------


class ProgressReportScheduleCreate(_Closed):
    """`POST /api/v1/students/{id}/progress-report-schedules` (#316 C2)."""

    author_user_id: uuid.UUID = Field(
        description="An active tutor who currently teaches the student"
        " (schedule_author_invalid otherwise)."
    )
    cadence: ScheduleCadence = Field(
        description="weekly (Monday to Sunday) or monthly (a calendar month),"
        " in the center's local dates."
    )
    subject_id: uuid.UUID | None = Field(
        default=None,
        description="Optional: an active subject the tutor currently teaches"
        " the student (subject_unknown / schedule_subject_not_assigned)."
        " Without it the reports cover all subjects.",
    )
    starts_on: date | None = Field(
        default=None,
        description="The first period that gets a draft: a Monday (weekly) or"
        " the 1st (monthly), from the current period on and at most 366 days"
        " ahead (schedule_starts_on_invalid). Default: the current period.",
    )


class ProgressReportScheduleUpdate(_Closed):
    """`PATCH /api/v1/progress-report-schedules/{id}` (#316 C3): deactivate
    or reactivate. To change the tutor, cadence or subject, deactivate it
    and add another."""

    version: int
    is_active: bool


# --- Views (C1) -----------------------------------------------------------------------


class ScheduleAuthor(_Closed):
    user_id: uuid.UUID
    display_name: str


class SchedulePerson(_Closed):
    display_name: str


class ScheduleSubject(_Closed):
    id: uuid.UUID
    name: str


class ScheduleLatestPeriod(_Closed):
    """The latest ended period of an active schedule and what exists for it
    (computed when read; nothing is stored)."""

    period_start: date
    period_end: date
    status: SchedulePeriodStatus = Field(
        description="not_created; draft (the tutor's, no link); deleted (the"
        " tutor deleted the placeholder: it is never created again);"
        " submitted, changes_requested, approved_for_family or retracted"
        " (with report_id)."
    )
    report_id: uuid.UUID | None = Field(
        description="Only for submitted, changes_requested, approved_for_family"
        " and retracted reports."
    )


class ProgressReportScheduleView(_Closed):
    """One schedule (#316 C1)."""

    id: uuid.UUID
    version: int
    student_id: uuid.UUID
    author: ScheduleAuthor
    cadence: ScheduleCadence
    subject: ScheduleSubject | None = Field(
        description="null: the reports cover all subjects."
    )
    starts_on: date
    is_active: bool
    created_at: datetime
    created_by: SchedulePerson
    ineligible_reason: ScheduleIneligibleReason | None = Field(
        description="Why no draft would be created now (the first failing"
        " rule), or null."
    )
    latest_period: ScheduleLatestPeriod | None = Field(
        description="null for an inactive schedule or before its first"
        " period has ended."
    )


class EligibleAuthor(_Closed):
    """A tutor who currently teaches the student, with the active subjects
    they teach them."""

    user_id: uuid.UUID
    display_name: str
    subjects: list[ScheduleSubject]


class ProgressReportScheduleList(_Closed):
    """`GET /api/v1/students/{id}/progress-report-schedules` (#316 C1):
    active schedules first, then the newest, at most 100."""

    items: list[ProgressReportScheduleView]
    eligible_authors: list[EligibleAuthor]
