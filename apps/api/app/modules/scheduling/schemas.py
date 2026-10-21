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
