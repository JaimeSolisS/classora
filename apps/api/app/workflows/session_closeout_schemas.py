"""Request and response bodies of the session closeout routes (#77 C2, D1;
#78 E1;
``app.workflows.session_closeout_router``).

They combine ``scheduling`` (the session, its attendance) and
``academics`` (the closeout row), so they live next to the workflow that
composes the two (ADR-0002). Every model is a **closed allow-list**
(``extra="forbid"``, output models too). Times are instants with an offset
(ADR-0004).
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.academics.closeout_rules import ACTUAL_MINUTES_RANGE
from app.modules.academics.schemas import CloseoutSummary, CloseoutSummaryInput
from app.modules.scheduling.schemas import SessionKindCode, SessionStateCode

type CloseoutBlockCode = Literal[
    "closeout_already_submitted",
    "session_not_closable",
    "session_not_started",
    "closeout_tutor_only",
]
type CloseoutStatus = Literal["submitted", "changes_requested", "approved"]
type CloseoutApprovalSource = Literal["review", "staff_correction"]
type CloseoutCorrectedField = Literal[
    "actual_minutes", "topics", "performance", "homework", "next_steps"
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CloseoutPerson(_Closed):
    """A person by display name only."""

    display_name: str


class CloseoutSessionSubject(_Closed):
    """The session's subject with its level's name."""

    id: uuid.UUID
    name: str
    education_level_name: str


class CloseoutAttendance(_Closed):
    """How complete the session's attendance is (the marks themselves are
    on `GET …/attendance`)."""

    total: int = Field(description="Participants.")
    marked: int = Field(description="Participants with a mark.")
    all_absent: bool = Field(
        description="Every participant is marked absent: the summary is optional."
    )
    missing_student_ids: list[uuid.UUID] = Field(
        description="Participants without a mark, sorted."
    )


class CloseoutReview(_Closed):
    """Where the review stands (#78 E1). `reason`: the latest
    request-changes reason; `tutor_note`: the tutor's note on a
    resubmission or the reason of a correction request. Both are `null`
    for staff who don't see the lesson content."""

    status: CloseoutStatus
    reason: str | None
    last_submitted_at: datetime = Field(
        description="The latest submission, resubmission or correction request."
    )
    tutor_note: str | None


class CloseoutApprovalRevision(_Closed):
    """One immutable approval revision (#78 E1): what payroll reads."""

    id: uuid.UUID
    revision: int
    approved_minutes: int
    approved_by: CloseoutPerson
    approved_at: datetime
    source: CloseoutApprovalSource = Field(
        description="review (approved by a reviewer) or staff_correction (a"
        " correction of an approved closeout)."
    )


class CloseoutCorrectionItem(_Closed):
    """One staff correction (#78 E5). `reason` is `null` for staff who
    don't see the lesson content; `fields` is empty for an attendance
    re-approval."""

    id: uuid.UUID
    fields: list[CloseoutCorrectedField]
    previous_minutes: int | None
    new_minutes: int | None
    reason: str | None
    corrected_by: CloseoutPerson
    created_at: datetime
    approval_id: uuid.UUID | None = Field(
        description="The approval revision the correction created, if any."
    )


class CloseoutOnBehalf(_Closed):
    """The latest submission was made by a reviewer on the tutor's behalf
    (#301 E3, E5). The closeout stays the tutor's (`submitted_by`).
    `reason` is `null` for staff who don't see the lesson content."""

    by: CloseoutPerson = Field(description="The reviewer who submitted it.")
    reason: str | None
    submitted_at: datetime = Field(
        description="When (the closeout's latest submission)."
    )


class SessionCloseoutRecord(_Closed):
    """A submitted closeout. Submitted hours are not approved until a
    reviewer approves them (`approval`); a correction never changes an
    approved revision, it adds one (`revisions`, newest first). `summary`
    is `null` with `summary_hidden: true` for staff who may see the minutes
    but not the lesson content."""

    id: uuid.UUID
    version: int = Field(
        description="The closeout's version: send it to approve, request"
        " changes, correct or resubmit."
    )
    status: CloseoutStatus
    actual_minutes: int | None = Field(
        description="The actual teaching minutes (stored on the session)."
    )
    summary: CloseoutSummary | None
    summary_hidden: bool
    submitted_by: CloseoutPerson
    submitted_at: datetime
    approval_request_id: uuid.UUID | None = Field(
        description="The item in the approval inbox."
    )
    review: CloseoutReview
    approval: CloseoutApprovalRevision | None = Field(
        description="The current approval revision (in force for payroll), if any."
    )
    revisions: list[CloseoutApprovalRevision] = Field(
        description="Every approval revision, newest first."
    )
    corrections: list[CloseoutCorrectionItem] = Field(
        description="Every staff correction, newest first."
    )
    attendance_changed_since_approval: bool = Field(
        description="The attendance differs from the current approval's snapshot."
    )
    on_behalf: CloseoutOnBehalf | None = Field(
        description="Set when a reviewer made the latest submission on the"
        " tutor's behalf (#301); cleared when the tutor resubmits."
    )


class SessionCloseoutView(_Closed):
    """A session's closeout (#77 C2). `can_submit`: you are the current
    tutor and the session can be closed out now (even with attendance
    missing: `attendance` lists who). `block`: why it can't
    (`session_not_started`, `session_not_closable`,
    `closeout_already_submitted`, or `closeout_tutor_only` for staff), or
    `null`. `closeout` is `null` until one is submitted, also for a
    completed session without one."""

    class_session_id: uuid.UUID
    version: int = Field(description="The session's version (send it back).")
    kind: SessionKindCode
    state: SessionStateCode
    starts_at: datetime
    ends_at: datetime
    scheduled_minutes: int
    subject: CloseoutSessionSubject
    tutor: CloseoutPerson | None
    can_submit: bool
    can_review: bool = Field(
        description="You may approve or request changes now (a submitted"
        " closeout that isn't your own)."
    )
    can_correct: bool = Field(
        description="You may correct the minutes or the summary now."
    )
    can_resubmit: bool = Field(
        description="You submitted it and changes were requested."
    )
    can_request_correction: bool = Field(
        description="You submitted it and it is approved."
    )
    can_request_changes: bool = Field(
        description="You may send it back to the tutor now: `can_review`, and"
        " it wasn't submitted on the tutor's behalf (#301)."
    )
    can_submit_on_behalf: bool = Field(
        description="You hold teaching_hours.approve, the class has ended"
        " without a closeout and you don't tutor it (#301)."
    )
    can_take_over: bool = Field(
        description="You hold teaching_hours.approve, changes were requested"
        " and you aren't its tutor: resubmit it on the tutor's behalf (#301)."
    )
    block: CloseoutBlockCode | None
    attendance: CloseoutAttendance
    closeout: SessionCloseoutRecord | None


class SessionCloseoutSubmit(_Closed):
    """`POST /api/v1/class-sessions/{id}/closeout` (#77 D1): the session's
    version, the actual teaching minutes (0-720) and the structured
    summary. Every participant's attendance must be marked first."""

    version: int = Field(description="The session's version, as last read.")
    actual_minutes: int = Field(
        ge=ACTUAL_MINUTES_RANGE[0],
        le=ACTUAL_MINUTES_RANGE[1],
        description="Actual teaching minutes, 0-720.",
    )
    summary: CloseoutSummaryInput


# --- #301 B3: the overdue list -------------------------------------------------------


class OverdueCloseoutSubject(_Closed):
    """The session's subject with its level's name."""

    name: str
    education_level_name: str


class OverdueCloseoutTutor(_Closed):
    """The session's current tutor. `can_submit`: the account is active and
    holds the tutor role (otherwise only a reviewer can close it out)."""

    user_id: uuid.UUID
    display_name: str
    can_submit: bool


class OverdueCloseoutAttendance(_Closed):
    """How many participants have an attendance mark."""

    marked: int
    total: int


class OverdueCloseoutItem(_Closed):
    """A confirmed class that has ended without a closeout (#301 B3). No
    student, group or cohort names."""

    class_session_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    kind: SessionKindCode
    scheduled_minutes: int
    subject: OverdueCloseoutSubject
    tutor: OverdueCloseoutTutor
    participant_count: int
    attendance: OverdueCloseoutAttendance
