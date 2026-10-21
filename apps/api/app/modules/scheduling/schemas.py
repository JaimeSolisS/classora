"""Class session bodies (#48 B4, C1, E1; #49 B, E; ADR-0005 wire format).

Times are instants (ADR-0004): ``starts_at`` / ``ends_at`` carry an offset
(RFC 3339); a naive value is ``422`` (``naive_datetime``). Names come from
the other modules' references (``catalog``, ``identity``, ``families``).

An ``override`` (#49 B) lists the soft codes to override and a reason. Its
``422`` codes: ``reason_required`` / ``reason_too_long`` on
``override.reason``; ``codes_required`` / ``duplicate_code`` on
``override.codes``; ``code_not_overridable`` (a hard code) /
``code_unknown`` (a warning code or any other string) on
``override.codes[i]``. The sets are :mod:`~app.modules.scheduling.conflicts`'.
"""

import uuid
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.modules.scheduling.conflicts import HARD, SOFT
from app.modules.scheduling.models import OVERRIDE_REASON_MAX_LENGTH

OVERRIDE_CODES_MAX: Final = len(SOFT)


def _check_reason(value: Any) -> Any:
    # Before the str check: a missing reason arrives as None (see
    # SessionOverride._missing_members); other types fail as usual.
    if value is None:
        raise PydanticCustomError("reason_required", "Give a reason for the override.")
    if not isinstance(value, str):
        return value
    reason = value.strip()
    if not reason:
        raise PydanticCustomError("reason_required", "Give a reason for the override.")
    if len(reason) > OVERRIDE_REASON_MAX_LENGTH:
        raise PydanticCustomError(
            "reason_too_long", "The reason is at most 1000 characters."
        )
    return reason


def _code_error(code: str) -> PydanticCustomError | None:
    if code in SOFT:
        return None
    if code in HARD:
        return PydanticCustomError(
            "code_not_overridable", "This rule can never be overridden."
        )
    return PydanticCustomError("code_unknown", "Not a rule that can be overridden.")


def _check_codes(codes: list[str]) -> list[str]:
    if not codes:  # empty, or missing (SessionOverride._missing_members)
        raise PydanticCustomError("codes_required", "List the rules to override.")
    errors = [
        InitErrorDetails(type=error, loc=(index,), input=code)
        for index, code in enumerate(codes)
        if (error := _code_error(code)) is not None
    ]
    if errors:
        raise ValidationError.from_exception_data("SessionOverrideCodes", errors)
    if len(set(codes)) != len(codes):
        raise PydanticCustomError("duplicate_code", "List each rule only once.")
    return codes


class SessionOverride(BaseModel):
    """The soft conflicts to override and why (#49 B1; ADR-0005). The server
    recomputes every conflict: a hard one is never overridden, and every soft
    one must be listed; listed codes that don't occur are ignored."""

    model_config = ConfigDict(extra="forbid")

    codes: list[str] = Field(
        max_length=OVERRIDE_CODES_MAX,
        description=(
            "The soft conflict codes to override, 1-7, no duplicates; never a"
            " hard code."
        ),
    )
    reason: str = Field(
        description="Why the rules are overridden: 1-1000 characters, trimmed."
    )

    @model_validator(mode="before")
    @classmethod
    def _missing_members(cls, data: Any) -> Any:
        # A missing reason is reason_required and missing codes are
        # codes_required, not pydantic's "missing".
        if isinstance(data, dict):
            return {"codes": [], "reason": None} | data
        return data

    _reason = field_validator("reason", mode="before")(_check_reason)
    _codes = field_validator("codes")(_check_codes)


class ClassSessionCreate(BaseModel):
    """A one-off individual session (``POST /api/v1/class-sessions``).

    Groups and courses (#56, #65) extend ``kind``. ``override`` (#49) needs
    ``schedule.override``."""

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
    override: SessionOverride | None = Field(
        default=None,
        description=(
            "Override the listed soft conflicts with a reason (needs"
            " schedule.override). Null or absent: every conflict blocks."
        ),
    )


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


class OverrideActor(BaseModel):
    """Who overrode the rules."""

    id: uuid.UUID
    display_name: str


class SessionOverrideItem(BaseModel):
    """One override of a session's soft conflicts (#49 E1)."""

    id: uuid.UUID
    codes: list[str] = Field(description="The overridden soft codes, in rule order.")
    reason: str
    actor: OverrideActor
    created_at: datetime


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
    overrides: list[SessionOverrideItem] = Field(
        description="The overrides of its soft conflicts, oldest first."
    )


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
