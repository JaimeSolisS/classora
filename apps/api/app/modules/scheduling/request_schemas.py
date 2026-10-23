"""Session request and available-slot bodies (#59 B, C, D; S3).

Every model a family receives is a **closed allow-list** (``extra="forbid"``
on the output models too; ``tests/integration/test_session_requests_security.py``
compares their JSON keys to the expected sets): no tutor id, name or count,
no class session id, no classroom, no other student and never the
requester's name (only ``requested_by_me``).

#60 adds ``rejection_reason`` to the family detail (E5) and the **staff**
models of the review (:class:`SessionRequestReview`, the confirm and reject
bodies): staff-only, so they are outside the family allow-list.

Times are instants with an offset (ADR-0004); dates are local dates in the
center zone.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.modules.scheduling.models import (
    SESSION_REQUEST_NOTE_MAX_LENGTH,
    SESSION_REQUEST_REJECTION_REASON_MAX_LENGTH,
)
from app.modules.scheduling.schemas import ClassSessionCreated, SessionOverride

type SessionRequestDuration = Literal[30, 45, 60, 90, 120]
type SessionRequestStatus = Literal["pending", "confirmed", "rejected", "withdrawn"]
type SessionRequestStatusFilter = Literal[
    "pending", "confirmed", "rejected", "withdrawn", "all"
]

SESSION_REQUESTS_DEFAULT_LIMIT: Final = 25
SESSION_REQUESTS_MAX_LIMIT: Final = 100


def _query_int(value: Any) -> Any:
    # Query values arrive as text; the Literal then checks the number.
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Available slots (B) -------------------------------------------------------------


class AvailableSlotsQuery(_Closed):
    """The query of ``GET /api/v1/available-slots`` (#59 B1). ``from`` and
    ``to`` are local dates in the center zone: ``to >= from``, at most 14
    dates, from today up to today + 60 days. Unknown parameters are
    ``422``."""

    student_id: uuid.UUID
    subject_id: uuid.UUID
    duration_minutes: Annotated[SessionRequestDuration, BeforeValidator(_query_int)] = (
        Field(description="The session length in minutes: 30, 45, 60, 90 or 120.")
    )
    from_: date = Field(alias="from", description="The first local date.")
    to: date = Field(description="The last local date (inclusive).")


class AvailableSlot(_Closed):
    """A time the family may request. Never names a tutor or a room."""

    starts_at: datetime
    ends_at: datetime
    already_requested: bool = Field(
        description=(
            "This student already has a pending request overlapping it (a hint;"
            " a request holds no time)."
        )
    )


class AvailableDay(_Closed):
    """One local date of the range and its slots (empty when none)."""

    date: date
    slots: list[AvailableSlot]


class AvailableSlots(_Closed):
    """``GET /api/v1/available-slots``: every date of the range, in order."""

    time_zone: str = Field(description="The center's IANA time zone.")
    student_id: uuid.UUID
    subject_id: uuid.UUID
    duration_minutes: int
    from_: date = Field(serialization_alias="from")
    to: date
    days: list[AvailableDay]


# --- Requests (C, D) -----------------------------------------------------------------


class SessionRequestCreate(_Closed):
    """``POST /api/v1/session-requests`` (#59 C1). No tutor, classroom or end
    (``422`` through ``extra="forbid"``): the center chooses the tutor."""

    student_id: uuid.UUID
    subject_id: uuid.UUID
    starts_at: datetime = Field(
        description=(
            "An instant with an offset (RFC 3339), on the 30-minute local grid"
            " (:00 or :30 in the center zone)."
        )
    )
    duration_minutes: SessionRequestDuration
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
        if len(trimmed) > SESSION_REQUEST_NOTE_MAX_LENGTH:
            raise ValueError("The note is at most 500 characters.")
        return trimmed or None


class SessionRequestWithdraw(_Closed):
    """``POST /api/v1/session-requests/{request_id}/withdraw`` (#59 D3)."""

    version: int = Field(ge=1)


class SessionRequestStudent(_Closed):
    id: uuid.UUID
    display_name: str


class SessionRequestSubject(_Closed):
    id: uuid.UUID
    name: str
    education_level_name: str


class SessionRequestDetail(_Closed):
    """A request as a family sees it (#59 D2; S3 allow-list)."""

    id: uuid.UUID
    student: SessionRequestStudent
    subject: SessionRequestSubject
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    status: SessionRequestStatus
    note: str | None
    requested_by_me: bool
    can_withdraw: bool = Field(description="You made it and it is still pending.")
    created_at: datetime
    resolved_at: datetime | None
    version: int
    rejection_reason: str | None = Field(
        description="Why the center rejected it (only when `rejected`; #60 E5)."
    )


class SessionRequestItem(SessionRequestDetail):
    """A list item: the same members as the detail."""


# --- Staff review (#60 B, C, D) ------------------------------------------------------


class SessionRequestReviewStudent(BaseModel):
    id: uuid.UUID
    display_name: str
    is_active: bool


class SessionRequestReviewSubject(BaseModel):
    id: uuid.UUID
    name: str
    education_level_name: str
    is_active: bool = Field(description="False when the subject or its level is.")


class SessionRequestRequester(BaseModel):
    id: uuid.UUID
    display_name: str


class SessionRequestReview(BaseModel):
    """A session request as staff review it (#60 B2; ``schedule.manage``)."""

    id: uuid.UUID
    status: SessionRequestStatus
    version: int
    created_at: datetime
    resolved_at: datetime | None
    student: SessionRequestReviewStudent
    subject: SessionRequestReviewSubject
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    note: str | None
    requested_by: SessionRequestRequester
    approval_request_id: uuid.UUID | None = Field(
        description="The request's inbox item."
    )
    class_session_id: uuid.UUID | None = Field(
        description="The session a confirmation created (only when `confirmed`)."
    )
    rejection_reason: str | None
    can_decide: bool = Field(
        description=(
            "Pending and not yet started: it can be confirmed (a started one"
            " can still be rejected)."
        )
    )


class SessionRequestConfirm(BaseModel):
    """``POST /api/v1/session-requests/{request_id}/confirm`` (#60 C1)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    tutor_user_id: uuid.UUID = Field(
        description="The qualified tutor the session is offered to (required)."
    )
    classroom_id: uuid.UUID | None = Field(
        default=None, description="The classroom; null is no room."
    )
    respond_by: AwareDatetime | None = Field(
        default=None,
        description=(
            "An earlier deadline for the tutor's answer, after now and no later"
            " than the default (#52). Null or absent: the default."
        ),
    )
    override: SessionOverride | None = Field(
        default=None,
        description=(
            "Override the listed soft conflicts with a reason (needs"
            " schedule.override)."
        ),
    )


def _check_rejection_reason(value: Any) -> Any:
    # The #40 codes: an empty reason is "missing", a long one
    # "string_too_long".
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        raise PydanticCustomError("missing", "Give a reason.")
    if len(trimmed) > SESSION_REQUEST_REJECTION_REASON_MAX_LENGTH:
        raise PydanticCustomError(
            "string_too_long", "The reason can be at most 1000 characters long."
        )
    return trimmed


class SessionRequestReject(BaseModel):
    """``POST /api/v1/session-requests/{request_id}/reject`` (#60 D1)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: str = Field(
        description="Why: 1-1000 characters, trimmed. The family sees it."
    )

    _reason = field_validator("reason", mode="before")(_check_rejection_reason)


class SessionRequestConfirmed(BaseModel):
    """The confirm's ``200`` (#60 C2 step 12): the confirmed request and the
    session it created (``offered`` to the tutor, with #50's warnings)."""

    request: SessionRequestReview
    class_session: ClassSessionCreated
