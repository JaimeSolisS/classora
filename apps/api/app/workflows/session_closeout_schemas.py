"""Request and response bodies of the session closeout routes (#77 C2, D1;
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
type CloseoutStatus = Literal["submitted"]


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


class SessionCloseoutRecord(_Closed):
    """A submitted closeout. Submitted hours are not approved: approval is a
    later review. `summary` is `null` with `summary_hidden: true` for
    staff who may see the minutes but not the lesson content."""

    id: uuid.UUID
    status: CloseoutStatus
    actual_minutes: int | None = Field(
        description="The actual teaching minutes (stored on the session)."
    )
    summary: CloseoutSummary | None
    summary_hidden: bool
    submitted_by: CloseoutPerson
    submitted_at: datetime
    approval_request_id: uuid.UUID | None = Field(
        description="The pending item in the approval inbox."
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
