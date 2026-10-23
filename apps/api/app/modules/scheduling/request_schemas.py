"""Session request and available-slot bodies (#59 B, C, D; S3).

Every model a family receives is a **closed allow-list** (``extra="forbid"``
on the output models too; ``tests/integration/test_session_requests_security.py``
compares their JSON keys to the expected sets): no tutor id, name or count,
no class session id, no classroom, no other student and never the
requester's name (only ``requested_by_me``).

Times are instants with an offset (ADR-0004); dates are local dates in the
center zone.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.modules.scheduling.models import SESSION_REQUEST_NOTE_MAX_LENGTH

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


class SessionRequestItem(SessionRequestDetail):
    """A list item: the same members as the detail."""
