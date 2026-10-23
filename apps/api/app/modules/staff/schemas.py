"""Tutor profile bodies (#43 C1-C3), tutor qualifications (#44 B3, D1;
revocation #198 B1, B3), tutor availability (#45 C) and the teaching limits
and preferred classrooms (#197 B).

The contact fields are the account's (``users.email``/``phone``, #31) with
the #31 formats; ``display_name`` is the account's too. Only
``professional_experience`` is stored by ``staff``. Subject and level names
come from ``catalog.service``.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.modules.catalog.schemas import (
    WINDOW_OVERLAP_MESSAGE,
    AvailabilityWindow,
    ClassroomReference,
    LocalDate,
    LocalTime,
    range_error,
    window_overlaps,
)
from app.modules.identity.schemas import (
    ContactEmail,
    ContactPhone,
    DisplayName,
    UserState,
)
from app.modules.staff.models import (
    AVAILABILITY_WINDOWS_MAX,
    BREAK_MAX,
    BREAK_MIN,
    DAILY_LIMIT_MAX,
    DAILY_LIMIT_MIN,
    EXCEPTION_MAX_SPAN_DAYS,
    EXCEPTION_NOTE_MAX_LENGTH,
    EXPERIENCE_MAX_LENGTH,
    PREFERRED_CLASSROOMS_MAX,
    REVOCATION_REASON_MAX_LENGTH,
    WEEKLY_LIMIT_MAX,
    WEEKLY_LIMIT_MIN,
)
from app.modules.staff.permissions import TutorField


def _blank_to_none(value: Any) -> Any:
    """Trim the experience; blank text clears it (``None``)."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


ProfessionalExperience = Annotated[
    Annotated[str, StringConstraints(max_length=EXPERIENCE_MAX_LENGTH)] | None,
    BeforeValidator(_blank_to_none),
]


class TutorListItem(BaseModel):
    """One tutor: a user holding the ``tutor`` role, with the account's
    contact fields."""

    user_id: uuid.UUID
    username: str
    display_name: str
    state: UserState
    email: str | None
    phone: str | None


class TeachingSubject(BaseModel):
    """A subject the tutor holds an **approved** qualification for (#44 D1).
    A subject deactivated later stays, with ``subject_is_active`` false."""

    subject_id: uuid.UUID
    subject_name: str
    education_level_id: uuid.UUID
    education_level_name: str
    subject_is_active: bool


class TutorDetail(TutorListItem):
    """One tutor profile, with what the caller may edit."""

    professional_experience: str | None
    version: int = Field(
        description=(
            "The profile's version; 0 while the tutor has no stored profile"
            " (the first edit creates it, at version 1)."
        )
    )
    editable_fields: list[TutorField] = Field(
        description=(
            "The fields the caller may change, sorted; empty: read only."
            " Sending another field is 403 permission_denied."
        )
    )
    teaching_subjects: list[TeachingSubject] = Field(
        description=(
            "The subjects of the tutor's approved qualifications, by the"
            " level's order, then the subject name."
        )
    )


class TutorUpdate(BaseModel):
    """A profile change (``PATCH /api/v1/tutors/{user_id}``). Fields not sent
    stay unchanged; ``""`` or ``null`` clears ``email``, ``phone`` or
    ``professional_experience``."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        ge=0, description="The version the caller loaded (0: no profile yet)."
    )
    display_name: DisplayName | None = None
    email: ContactEmail = None
    phone: ContactPhone = None
    professional_experience: ProfessionalExperience = None

    @field_validator("display_name")
    @classmethod
    def _display_name_not_null(cls, value: str | None) -> str | None:
        # Runs only when the field is sent: null is not a display name.
        if value is None:
            raise ValueError("The display name cannot be empty.")
        return value

    def sent_fields(self) -> frozenset[TutorField]:
        """The profile fields in the body (``version`` excluded)."""
        return cast(
            frozenset[TutorField],
            frozenset(field for field in self.model_fields_set if field != "version"),
        )


# --- Tutor qualifications (#44) ------------------------------------------------------

type QualificationStatus = Literal[
    "pending", "approved", "rejected", "cancelled", "revoked"
]
type QualificationStatusFilter = Literal[
    "all", "pending", "approved", "rejected", "cancelled", "revoked"
]


class QualificationCreate(BaseModel):
    """A tutor's request to teach one subject (the level follows from it)."""

    model_config = ConfigDict(extra="forbid")

    subject_id: uuid.UUID


class QualificationItem(BaseModel):
    """One qualification of a tutor, with its subject and level names."""

    id: uuid.UUID
    subject_id: uuid.UUID
    subject_name: str
    education_level_id: uuid.UUID
    education_level_name: str
    subject_is_active: bool
    status: QualificationStatus
    requested_at: datetime
    decided_at: datetime | None
    rejection_reason: str | None = Field(
        description="The reviewer's reason; set only for rejected."
    )
    revoked_at: datetime | None = Field(
        description="When the approval was revoked; set only for revoked."
    )
    revocation_reason: str | None = Field(
        description="The reviewer's revocation reason; set only for revoked."
    )
    version: int


class QualificationRevoke(BaseModel):
    """A reviewer withdraws an approved qualification (#198 B1)."""

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=REVOCATION_REASON_MAX_LENGTH,
        ),
    ] = Field(description="Why the qualification is revoked; the tutor sees it.")
    version: int = Field(ge=1, description="The version the caller loaded.")


# --- Tutor availability (#45) --------------------------------------------------------

type ExceptionKind = Literal["unavailable", "available"]
type AvailabilityReason = Literal[
    "center_closed", "time_off", "outside_weekly_availability"
]


def _no_window_overlaps(value: list[AvailabilityWindow]) -> list[AvailabilityWindow]:
    # 422 on each overlapping item (["body", "windows", <index>,
    # "starts_at"]), as for classrooms (#39 B5).
    overlaps = window_overlaps(value)
    if overlaps:
        raise ValidationError.from_exception_data(
            "AvailabilityWindows",
            [
                InitErrorDetails(
                    type=PydanticCustomError("window_overlap", WINDOW_OVERLAP_MESSAGE),
                    loc=(index, "starts_at"),
                    input=value[index].starts_at,
                )
                for index in overlaps
            ],
        )
    return value


class WeeklyAvailabilityUpdate(BaseModel):
    """The tutor's full list of weekly windows
    (``PUT /api/v1/tutors/{user_id}/availability/weekly``); ``[]`` clears
    them."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        ge=0,
        description=("The availability version the caller loaded (0: never changed)."),
    )
    windows: list[AvailabilityWindow] = Field(
        max_length=AVAILABILITY_WINDOWS_MAX,
        description=(
            "At most 42 windows; windows of one weekday may touch but not"
            " overlap. Local wall-clock times of the center."
        ),
    )

    _windows = field_validator("windows")(_no_window_overlaps)


def _blank_note_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


ExceptionNote = Annotated[
    Annotated[str, StringConstraints(max_length=EXCEPTION_NOTE_MAX_LENGTH)] | None,
    BeforeValidator(_blank_note_to_none),
]


class AvailabilityExceptionCreate(BaseModel):
    """A new exception (``POST …/availability/exceptions``): time off
    (``unavailable``: the whole day without times, or those hours) or extra
    availability (``available``: always with times) on each day of the
    inclusive range."""

    model_config = ConfigDict(extra="forbid")

    kind: ExceptionKind
    starts_on: LocalDate
    ends_on: LocalDate = Field(
        description="On or after starts_on, at most 366 days later (inclusive)."
    )
    starts_at: LocalTime | None = Field(
        default=None,
        validate_default=True,
        description="Required for available; omit both times for a whole day.",
    )
    ends_at: LocalTime | None = Field(
        default=None,
        validate_default=True,
        description="After starts_at (same day); set together with starts_at.",
    )
    note: ExceptionNote = Field(
        default=None, description="At most 200 characters; blank clears it."
    )

    @field_validator("ends_on")
    @classmethod
    def _range(cls, value: date, info: ValidationInfo) -> date:
        starts_on = info.data.get("starts_on")
        if isinstance(starts_on, date):
            message = range_error(starts_on, value, EXCEPTION_MAX_SPAN_DAYS)
            if message is not None:
                raise ValueError(message)
        return value

    @field_validator("starts_at")
    @classmethod
    def _hours_for_available(cls, value: str | None, info: ValidationInfo) -> Any:
        if value is None and info.data.get("kind") == "available":
            raise ValueError("Extra availability needs a start and an end time.")
        return value

    @field_validator("ends_at")
    @classmethod
    def _hours(cls, value: str | None, info: ValidationInfo) -> Any:
        if "starts_at" not in info.data:
            return value  # starts_at already failed
        starts_at = info.data["starts_at"]
        if (starts_at is None) != (value is None):
            raise ValueError("Set both times or neither.")
        if starts_at is not None and value is not None and value <= starts_at:
            raise ValueError("The end time must be after the start time.")
        return value


class AvailabilityExceptionItem(BaseModel):
    """One exception. ``starts_at``/``ends_at`` are null for a whole day."""

    id: uuid.UUID
    kind: ExceptionKind
    starts_on: date
    ends_on: date
    starts_at: str | None = Field(description="HH:MM, or null for a whole day.")
    ends_at: str | None = Field(description="HH:MM, or null for a whole day.")
    note: str | None
    created_at: datetime


class TeachingLimitsDetail(BaseModel):
    """A tutor's teaching limits (#197), in minutes; null is no limit (no
    break). Exceeding them is a warning when scheduling (M4-05), never a
    block."""

    daily_limit_minutes: int | None
    weekly_limit_minutes: int | None
    required_break_minutes: int | None


# --- Availability change requests (M4-18 #63) ----------------------------------------

type ChangeKind = Literal["weekly", "exception_create", "exception_delete"]
type ChangeStatus = Literal["pending", "approved", "rejected", "cancelled"]
type AffectedSessionState = Literal["offered", "confirmed"]

AFFECTED_SESSIONS_SHOWN = 20
"""How many affected sessions a change request shows (G1, D2)."""


class AvailabilityChangeException(BaseModel):
    """The exception a change adds or deletes, in local dates and times."""

    id: uuid.UUID | None = Field(
        description="The exception being deleted; null for a new one."
    )
    kind: ExceptionKind
    starts_on: date
    ends_on: date
    starts_at: str | None = Field(description="HH:MM, or null for a whole day.")
    ends_at: str | None = Field(description="HH:MM, or null for a whole day.")
    note: str | None


class AvailabilityChange(BaseModel):
    """What a change request changes: the full wanted weekly windows
    (``weekly``) or the exception added or deleted."""

    windows: list[AvailabilityWindow] | None = Field(
        description="weekly: every window the tutor wants, by weekday; else null."
    )
    exception: AvailabilityChangeException | None = Field(
        description="exception_create / exception_delete: the exception; else null."
    )


class AffectedSessionItem(BaseModel):
    """A scheduled session a change affects, as it was when the change was
    requested."""

    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    subject_name: str
    state: AffectedSessionState


class AvailabilityChangeRequester(BaseModel):
    """Who asked for the change: the tutor or an Owner or Administrator."""

    id: uuid.UUID
    display_name: str


class AvailabilityChangeRequestItem(BaseModel):
    """An availability change that affects scheduled sessions and waits for
    (or got) an Owner's or Administrator's decision (``202`` of the three
    availability writes; ``pending_changes`` / ``recent_changes``)."""

    id: uuid.UUID
    kind: ChangeKind
    status: ChangeStatus
    change: AvailabilityChange
    affected_count: int = Field(
        description="How many sessions the change affected when it was requested."
    )
    affected_sessions: list[AffectedSessionItem] = Field(
        description="The first 20 of them, by start."
    )
    requested_by: AvailabilityChangeRequester
    created_at: datetime
    resolved_at: datetime | None
    rejection_reason: str | None = Field(
        description="The reviewer's reason; set only for rejected."
    )
    approval_request_id: uuid.UUID
    version: int


class ChangeRequestCancel(BaseModel):
    """Withdraw a pending change request
    (``POST …/availability/change-requests/{id}/cancel``)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="The version the caller loaded.")


class TutorAvailabilityDetail(BaseModel):
    """A tutor's availability in the center's local wall-clock time, with
    the teaching limits and preferred classrooms (#197)."""

    weekly: list[AvailabilityWindow] = Field(
        description="The weekly windows by weekday, then start."
    )
    exceptions: list[AvailabilityExceptionItem] = Field(
        description="By starts_on, then starts_at (whole days first)."
    )
    limits: TeachingLimitsDetail
    preferred_classrooms: list[ClassroomReference] = Field(
        description=(
            "By name. A room deactivated after it was chosen stays, with"
            " is_active false."
        )
    )
    version: int = Field(
        description=(
            "The version of the weekly windows, limits and preferred"
            " classrooms together; 0 while they were never changed."
        )
    )
    pending_changes: list[AvailabilityChangeRequestItem] = Field(
        description=(
            "Changes waiting for approval because they affect scheduled"
            " classes, oldest first (M4-18)."
        )
    )
    recent_changes: list[AvailabilityChangeRequestItem] = Field(
        description="The last 5 decided or withdrawn change requests, newest first."
    )


def _weekly_not_below_daily(value: int | None, info: ValidationInfo) -> int | None:
    daily = info.data.get("daily_limit_minutes")
    if value is not None and daily is not None and value < daily:
        raise PydanticCustomError(
            "weekly_below_daily",
            "The weekly limit cannot be below the daily limit.",
        )
    return value


def _no_duplicate_ids(value: list[uuid.UUID] | None) -> list[uuid.UUID]:
    if value is None:
        raise ValueError("This field cannot be null.")
    if len(set(value)) != len(value):
        raise PydanticCustomError(
            "duplicate_classroom", "Choose each classroom only once."
        )
    return value


class AvailabilitySettingsUpdate(BaseModel):
    """A change to the tutor's teaching limits and preferred classrooms
    (``PATCH /api/v1/tutors/{user_id}/availability/settings``) with the
    availability ``version`` the caller loaded. Fields not sent stay
    unchanged; a limit sent as null is cleared."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        ge=0,
        description="The availability version the caller loaded (0: never changed).",
    )
    daily_limit_minutes: int | None = Field(
        default=None,
        ge=DAILY_LIMIT_MIN,
        le=DAILY_LIMIT_MAX,
        description="Minutes a day, 15-1440; null: no limit.",
    )
    weekly_limit_minutes: int | None = Field(
        default=None,
        ge=WEEKLY_LIMIT_MIN,
        le=WEEKLY_LIMIT_MAX,
        description=(
            "Minutes a week, 15-10080, not below the daily limit; null: no limit."
        ),
    )
    required_break_minutes: int | None = Field(
        default=None,
        ge=BREAK_MIN,
        le=BREAK_MAX,
        description="Minutes between two sessions, 0-240; null: no break.",
    )
    preferred_classroom_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=PREFERRED_CLASSROOMS_MAX,
        description=(
            "Replaces the list: at most 10 active classrooms, each once; [] clears it."
        ),
    )

    _weekly = field_validator("weekly_limit_minutes")(_weekly_not_below_daily)
    _rooms = field_validator("preferred_classroom_ids")(_no_duplicate_ids)


class AvailabilityCheck(BaseModel):
    """Whether the tutor is available for a local interval, and why not."""

    available: bool
    reasons: list[AvailabilityReason] = Field(
        description=(
            "Every reason that applies, in this order: center_closed,"
            " time_off, outside_weekly_availability. Empty when available."
        )
    )
