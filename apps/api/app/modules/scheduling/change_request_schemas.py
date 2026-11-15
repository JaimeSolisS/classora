"""Change request bodies (#62 B1, C, D; S3).

What families and tutors receive (:class:`ChangeRequestDetail`,
:class:`ChangeRequestItem`) is a **closed allow-list** (``extra="forbid"``
on the output models too; ``tests/integration/test_change_requests.py``
compares their JSON keys to the expected sets): never a tutor id or name, a
classroom, another participant, the requester's name, a ``policy_id`` or
scheduling conflict details. The staff review (:class:`ChangeRequestReview`)
is ``schedule.manage`` only and outside that allow-list.

Times are instants with an offset (ADR-0004).
"""

import uuid
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.core.pagination import OffsetPage
from app.modules.scheduling.models import (
    CHANGE_REQUEST_NOTE_MAX_LENGTH,
    CHANGE_REQUEST_REASON_MAX_LENGTH,
)
from app.modules.scheduling.schemas import (
    ClassSessionDetail,
    SchedulingWarning,
    SessionKindCode,
    SessionOverride,
    SessionStateCode,
)

type ChangeRequestKind = Literal["cancellation", "reschedule"]
type ChangeRequestInitiator = Literal["family", "tutor"]
type ChangeRequestStatus = Literal["pending", "approved", "rejected", "withdrawn"]
type ChangeRequestStatusFilter = Literal[
    "pending", "approved", "rejected", "withdrawn", "all"
]
type ChangeClassification = Literal["on_time", "late", "no_show", "excused"]

CHANGE_REQUESTS_DEFAULT_LIMIT: Final = 25
CHANGE_REQUESTS_MAX_LIMIT: Final = 100


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Create, withdraw (B1, D3) -------------------------------------------------------


class ChangeRequestCreate(_Closed):
    """``POST /api/v1/change-requests`` (#62 B1)."""

    class_session_id: uuid.UUID
    kind: ChangeRequestKind
    proposed_starts_at: datetime | None = Field(
        default=None,
        description=(
            "Only for a `reschedule` (required then): the proposed new start, an"
            " instant with an offset on a whole minute. The end follows from the"
            " session's length, which never changes."
        ),
    )
    note: str | None = Field(
        default=None,
        description="An optional note for the center (trimmed, at most 500).",
    )

    @field_validator("note", mode="after")
    @classmethod
    def _trim_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if len(trimmed) > CHANGE_REQUEST_NOTE_MAX_LENGTH:
            raise PydanticCustomError(
                "string_too_long", "The note can be at most 500 characters long."
            )
        return trimmed or None


class ChangeRequestWithdraw(_Closed):
    """``POST /api/v1/change-requests/{change_request_id}/withdraw`` (D3)."""

    version: int = Field(ge=1)


# --- The family / tutor view (S3) ----------------------------------------------------


class ChangeRequestSubject(_Closed):
    id: uuid.UUID
    name: str
    education_level_name: str


class ChangeRequestSession(_Closed):
    """The session, as it is now (its times change once a reschedule is
    approved)."""

    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    subject: ChangeRequestSubject


class ChangeRequestStudent(_Closed):
    id: uuid.UUID
    display_name: str


class ChangeRequestOutcome(_Closed):
    """A policy classification and its makeup eligibility (#61)."""

    classification: ChangeClassification
    makeup_allowed: bool
    makeup_until: datetime | None


class ChangeRequestDecision(_Closed):
    """How staff decided (only once approved or rejected)."""

    exception: bool = Field(description="Approved as an exception (excused).")
    reason: str | None = Field(
        description=(
            "The reviewer's reason (always set for a rejection or an exception)."
        )
    )
    decided_at: datetime


class ChangeRequestDetail(_Closed):
    """A change request as its family or tutor sees it (#62 S3 allow-list)."""

    id: uuid.UUID
    kind: ChangeRequestKind
    status: ChangeRequestStatus
    class_session: ChangeRequestSession
    student: ChangeRequestStudent | None = Field(
        description="The student the session is for."
    )
    proposed_starts_at: datetime | None
    proposed_ends_at: datetime | None
    note: str | None
    outcome: ChangeRequestOutcome = Field(
        description=(
            "The policy outcome: the effective one once `approved`, otherwise"
            " the one recorded when the request was made (as requested)."
        )
    )
    decision: ChangeRequestDecision | None
    requested_by_me: bool
    can_withdraw: bool = Field(description="You made it and it is still pending.")
    created_at: datetime
    resolved_at: datetime | None
    version: int


class ChangeRequestItem(ChangeRequestDetail):
    """A list item: the same members as the detail."""


ChangeRequestPage = OffsetPage[ChangeRequestItem]


# --- Staff review (C) ----------------------------------------------------------------


class ChangeRequestPerson(BaseModel):
    id: uuid.UUID
    display_name: str


class ChangeRequestRoom(BaseModel):
    id: uuid.UUID
    name: str


class ChangeRequestReviewSession(BaseModel):
    """The session, live."""

    id: uuid.UUID
    state: SessionStateCode
    kind: SessionKindCode
    starts_at: datetime
    ends_at: datetime
    series_id: uuid.UUID | None
    tutor: ChangeRequestPerson | None
    classroom: ChangeRequestRoom | None
    version: int


class ChangeRequestPolicy(BaseModel):
    """The scheduling policy version the request was classified with."""

    id: uuid.UUID
    effective_from: datetime


class ChangeRequestReview(BaseModel):
    """A change request as staff review it (C1; ``schedule.manage``)."""

    id: uuid.UUID
    kind: ChangeRequestKind
    status: ChangeRequestStatus
    initiated_by: ChangeRequestInitiator
    requested_by: ChangeRequestPerson
    student: ChangeRequestStudent | None
    subject: ChangeRequestSubject
    session: ChangeRequestReviewSession
    session_starts_at: datetime = Field(
        description="The session's start when the request was made."
    )
    session_ends_at: datetime
    proposed_starts_at: datetime | None
    proposed_ends_at: datetime | None
    note: str | None
    policy: ChangeRequestPolicy
    request_outcome: ChangeRequestOutcome = Field(
        description="The outcome recorded when the request was made (never recomputed)."
    )
    final_outcome: ChangeRequestOutcome | None = Field(
        description="The effective outcome, once approved."
    )
    outcome: ChangeRequestOutcome = Field(
        description="The effective outcome once approved, else the request-time one."
    )
    decision: ChangeRequestDecision | None
    decided_by: ChangeRequestPerson | None
    approval_request_id: uuid.UUID | None
    created_at: datetime
    resolved_at: datetime | None
    version: int
    can_decide: bool = Field(
        description=(
            "Pending, the session still confirmed and not started: it can be"
            " approved (a started one can still be rejected)."
        )
    )


def _check_reason(value: Any) -> Any:
    # The #40 codes: an empty reason is "missing", a long one
    # "string_too_long".
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        raise PydanticCustomError("missing", "Give a reason.")
    if len(trimmed) > CHANGE_REQUEST_REASON_MAX_LENGTH:
        raise PydanticCustomError(
            "string_too_long", "The reason can be at most 1000 characters long."
        )
    return trimmed


def _optional_reason(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return _check_reason(value)


class ChangeRequestApprove(BaseModel):
    """``POST /api/v1/change-requests/{change_request_id}/approve`` (C2)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="The request's version, as read.")
    exception: bool = Field(
        default=False,
        description=(
            "Approve as an exception: the outcome becomes `excused` (a"
            " cancellation then allows a makeup). Needs a `reason`."
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Why (trimmed, at most 1000; required with `exception`). The"
            " requester sees it."
        ),
    )
    classroom_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Only for a reschedule: the room at the new time; null is no room;"
            " absent keeps the room."
        ),
    )
    override: SessionOverride | None = Field(
        default=None,
        description=(
            "Only for a reschedule: override the listed soft conflicts of the"
            " new time with a reason (needs schedule.override)."
        ),
    )

    _reason = field_validator("reason", mode="before")(_optional_reason)


class ChangeRequestReject(BaseModel):
    """``POST /api/v1/change-requests/{change_request_id}/reject`` (C4)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: str = Field(
        description="Why: 1-1000 characters, trimmed. The requester sees it."
    )

    _reason = field_validator("reason", mode="before")(_check_reason)


class ChangeRequestApproved(BaseModel):
    """The approve's ``200`` (C2 step 16): the request, the session after the
    change and the tutor's workload warnings at a new time (empty for a
    cancellation)."""

    change_request: ChangeRequestReview
    class_session: ClassSessionDetail
    warnings: list[SchedulingWarning]
