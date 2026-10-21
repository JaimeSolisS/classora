"""Class session bodies (#48 B4, C1, E1; ADR-0005 wire format).

Times are instants (ADR-0004): ``starts_at`` / ``ends_at`` carry an offset
(RFC 3339); a naive value is ``422`` (``naive_datetime``). Names come from
the other modules' references (``catalog``, ``identity``, ``families``).
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ClassSessionCreate(BaseModel):
    """A one-off individual session (``POST /api/v1/class-sessions``).

    Groups and courses (#56, #65) extend ``kind``; overrides (#49) add an
    ``override`` member, which is an unknown field until then."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["individual"]
    student_id: uuid.UUID
    subject_id: uuid.UUID
    tutor_user_id: uuid.UUID | None = Field(
        default=None,
        description=("The tutor to offer the session to; null creates it unassigned."),
    )
    classroom_id: uuid.UUID | None = Field(
        default=None, description="The classroom; null is no room."
    )
    starts_at: datetime = Field(description="An instant with an offset (RFC 3339).")
    ends_at: datetime = Field(description="An instant with an offset (RFC 3339).")


class SessionSubject(BaseModel):
    """The session's subject with its level's name. ``is_active`` is false
    when the subject or its level is inactive."""

    id: uuid.UUID
    name: str
    education_level_name: str
    is_active: bool


class SessionTutor(BaseModel):
    """The tutor the session is offered to, or who teaches it."""

    id: uuid.UUID
    display_name: str


class SessionClassroom(BaseModel):
    """The session's classroom (an inactive room stays shown as such)."""

    id: uuid.UUID
    name: str
    is_active: bool


class SessionParticipant(BaseModel):
    """A student of the session."""

    student_id: uuid.UUID
    display_name: str
    is_active: bool


type SessionKindCode = Literal["individual", "group", "course"]
type SessionStateCode = Literal[
    "unassigned",
    "offered",
    "confirmed",
    "completed",
    "cancelled",
    "skipped_closure",
    "conflict",
]


class ClassSessionDetail(BaseModel):
    """One class session (``GET /api/v1/class-sessions/{id}``)."""

    id: uuid.UUID
    kind: SessionKindCode
    state: SessionStateCode
    subject: SessionSubject
    tutor: SessionTutor | None
    classroom: SessionClassroom | None
    participants: list[SessionParticipant] = Field(
        description="The students, by display name."
    )
    starts_at: datetime
    ends_at: datetime
    scheduled_minutes: int
    version: int
    created_at: datetime
    updated_at: datetime


class SchedulingWarning(BaseModel):
    """A rule that never blocks (ADR-0005 warnings; filled by #50)."""

    code: str
    details: dict[str, Any]


class ClassSessionCreated(ClassSessionDetail):
    """The created session and its warnings (always empty until #50)."""

    warnings: list[SchedulingWarning]


# --- The calendar range read (#57 A) ----------------------------------------------


class ClassSessionRangeQuery(BaseModel):
    """The query of ``GET /api/v1/class-sessions`` (#57 A1). ``from`` and
    ``to`` are instants with an offset; a session matches when it overlaps
    ``[from, to)``. Unknown parameters are ``422``."""

    model_config = ConfigDict(extra="forbid")

    from_: datetime = Field(
        alias="from", description="An instant with an offset (RFC 3339)."
    )
    to: datetime = Field(
        description=(
            "An instant with an offset (RFC 3339), after `from` and at most"
            " 45 days later."
        )
    )
    tutor_user_id: uuid.UUID | None = Field(
        default=None, description="Only the sessions of this tutor."
    )
    student_id: uuid.UUID | None = Field(
        default=None, description="Only the sessions this student takes part in."
    )
    classroom_id: uuid.UUID | None = Field(
        default=None, description="Only the sessions in this classroom."
    )
    kind: SessionKindCode | None = Field(
        default=None, description="Only the sessions of this kind."
    )
    include_cancelled: bool = Field(
        default=False, description="Also return `cancelled` sessions."
    )


type MarkerCode = Literal["center_closed", "tutor_not_qualified"]


class CalendarSubject(BaseModel):
    """The session's subject with its level's name."""

    id: uuid.UUID
    name: str
    education_level_name: str


class CalendarClassroom(BaseModel):
    """The session's classroom."""

    id: uuid.UUID
    name: str


class CalendarParticipant(BaseModel):
    """A student of the session."""

    student_id: uuid.UUID
    display_name: str


class ClassSessionCalendarItem(BaseModel):
    """One session on the calendar (#57 A4). ``markers`` are computed on
    read, in a fixed order (``center_closed``, ``tutor_not_qualified``)."""

    id: uuid.UUID
    kind: SessionKindCode
    state: SessionStateCode
    starts_at: datetime
    ends_at: datetime
    subject: CalendarSubject
    tutor: SessionTutor | None
    classroom: CalendarClassroom | None
    participants: list[CalendarParticipant] = Field(
        description="The students, by display name."
    )
    markers: list[MarkerCode]


class ClassSessionCalendar(BaseModel):
    """The sessions overlapping the range, by ``starts_at`` then ``id``; at
    most 1000, with ``truncated`` true when more match."""

    items: list[ClassSessionCalendarItem]
    truncated: bool


# --- Tutor filter options (#57 B) ---------------------------------------------------


type TutorOptionState = Literal["pending", "active", "suspended", "archived"]


class TutorOption(BaseModel):
    """A user holding the tutor role, for the calendar's tutor filter. No
    contact data."""

    id: uuid.UUID
    display_name: str
    state: TutorOptionState


class TutorOptions(BaseModel):
    """The matching tutors, in the user list order."""

    items: list[TutorOption]
