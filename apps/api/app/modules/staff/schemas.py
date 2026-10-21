"""Tutor profile bodies (#43 C1-C3), tutor qualifications (#44 B3, D1) and
tutor availability (#45 C).

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
    EXCEPTION_MAX_SPAN_DAYS,
    EXCEPTION_NOTE_MAX_LENGTH,
    EXPERIENCE_MAX_LENGTH,
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

type QualificationStatus = Literal["pending", "approved", "rejected", "cancelled"]
type QualificationStatusFilter = Literal[
    "all", "pending", "approved", "rejected", "cancelled"
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
    version: int


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


class TutorAvailabilityDetail(BaseModel):
    """A tutor's availability in the center's local wall-clock time."""

    weekly: list[AvailabilityWindow] = Field(
        description="The weekly windows by weekday, then start."
    )
    exceptions: list[AvailabilityExceptionItem] = Field(
        description="By starts_on, then starts_at (whole days first)."
    )
    version: int = Field(
        description=("The weekly availability's version; 0 while it was never changed.")
    )


class AvailabilityCheck(BaseModel):
    """Whether the tutor is available for a local interval, and why not."""

    available: bool
    reasons: list[AvailabilityReason] = Field(
        description=(
            "Every reason that applies, in this order: center_closed,"
            " time_off, outside_weekly_availability. Empty when available."
        )
    )
