"""Administration request and response bodies: the audit log (#22), the
business settings (#37) and the approval requests (#40)."""

import uuid
import zoneinfo
from datetime import datetime
from functools import cache
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.modules.administration.settings_models import (
    ADDRESS_MAX_LENGTH,
    CENTER_NAME_MAX_LENGTH,
    EMAIL_MAX_LENGTH,
    FIRST_WEEKDAY,
    LAST_WEEKDAY,
    PHONE_MAX_LENGTH,
    TIME_ZONE_MAX_LENGTH,
)


class AuditChange(BaseModel):
    """One column's value before and after the change (null when there is none)."""

    old: Any
    new: Any


class AuditEventListItem(BaseModel):
    """One audit event; the actor and the record are given by id."""

    id: uuid.UUID
    occurred_at: datetime
    actor_id: uuid.UUID | None
    action: str
    record_type: str | None
    record_id: uuid.UUID | None
    changes: dict[str, AuditChange] | None
    details: dict[str, Any] | None
    request_id: str | None


# --- Business settings (#37) ---------------------------------------------------------

# The #31 contact patterns (``settings_models.EMAIL_PATTERN`` /
# ``PHONE_PATTERN``; PostgreSQL ``[:space:]`` is ``\s`` here). A unit test
# keeps them equal to identity's.
EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
PHONE_REGEX = r"^\+?[0-9][0-9 ().-]{5,30}[0-9]$"
LOCAL_TIME_REGEX = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


@cache
def available_time_zones() -> frozenset[str]:
    """The IANA keys of the system zone data (``zoneinfo``, as
    ``app.core.clock``)."""
    return frozenset(zoneinfo.available_timezones())


def _blank_to_none(value: Any) -> Any:
    """Trim an optional text field; an empty one clears it (``None``)."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _not_null[T](value: T | None) -> T:
    # Runs only when the field is sent: null never clears these fields.
    if value is None:
        raise ValueError("This field cannot be null.")
    return value


CenterName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=CENTER_NAME_MAX_LENGTH
    ),
]
"""The center's name: trimmed, 1 to 100 characters, never blank."""
OptionalEmail = Annotated[
    Annotated[str, StringConstraints(max_length=EMAIL_MAX_LENGTH, pattern=EMAIL_REGEX)]
    | None,
    BeforeValidator(_blank_to_none),
]
OptionalPhone = Annotated[
    Annotated[str, StringConstraints(max_length=PHONE_MAX_LENGTH, pattern=PHONE_REGEX)]
    | None,
    BeforeValidator(_blank_to_none),
]
OptionalAddress = Annotated[
    Annotated[str, StringConstraints(max_length=ADDRESS_MAX_LENGTH)] | None,
    BeforeValidator(_blank_to_none),
]
LocalTime = Annotated[
    str,
    StringConstraints(pattern=LOCAL_TIME_REGEX),
    Field(description="A local wall-clock time, HH:MM (24-hour)."),
]
Weekday = Annotated[
    int,
    Field(
        ge=FIRST_WEEKDAY,
        le=LAST_WEEKDAY,
        description="ISO weekday: 1 = Monday … 7 = Sunday.",
    ),
]


class OperatingHoursItem(BaseModel):
    """The opening interval of one weekday, in the center's local time."""

    model_config = ConfigDict(extra="forbid")

    weekday: Weekday
    opens_at: LocalTime
    closes_at: LocalTime

    @field_validator("closes_at")
    @classmethod
    def _closes_after_opening(cls, value: str, info: ValidationInfo) -> str:
        opens_at = info.data.get("opens_at")
        # HH:MM strings compare like the times they hold.
        if isinstance(opens_at, str) and value <= opens_at:
            raise ValueError("The closing time must be after the opening time.")
        return value


class BusinessSettingsDetail(BaseModel):
    """The center's business settings (``GET /api/v1/business-settings``).

    ``operating_hours`` lists the open weekdays, by weekday; a weekday that
    is not listed is closed."""

    center_name: str
    contact_phone: str | None
    contact_email: str | None
    address: str | None
    time_zone: str = Field(description="The center's IANA time zone.")
    operating_hours: list[OperatingHoursItem]
    version: int
    updated_at: datetime


class BusinessSettingsUpdate(BaseModel):
    """A change to the settings (``PATCH /api/v1/business-settings``) with the
    ``version`` the caller loaded. Fields not sent stay unchanged; ``null``
    (or an empty string) clears a contact field; ``null`` is ``422`` for
    ``center_name``, ``time_zone`` and ``operating_hours``.
    ``operating_hours``, when sent, replaces the whole week (0 to 7 entries,
    one per weekday)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    center_name: CenterName | None = None
    contact_phone: OptionalPhone = None
    contact_email: OptionalEmail = None
    address: OptionalAddress = None
    time_zone: (
        Annotated[
            str,
            StringConstraints(max_length=TIME_ZONE_MAX_LENGTH),
            Field(description="An IANA time zone key, e.g. America/Mexico_City."),
        ]
        | None
    ) = None
    operating_hours: (
        Annotated[list[OperatingHoursItem], Field(max_length=LAST_WEEKDAY)] | None
    ) = None

    _fields_not_null = field_validator("center_name", "time_zone", "operating_hours")(
        _not_null
    )

    @field_validator("time_zone")
    @classmethod
    def _known_time_zone(cls, value: str | None) -> str | None:
        if value is not None and value not in available_time_zones():
            raise ValueError("Not a known IANA time zone.")
        return value

    @field_validator("operating_hours")
    @classmethod
    def _one_entry_per_weekday(
        cls, value: list[OperatingHoursItem] | None
    ) -> list[OperatingHoursItem] | None:
        if value is not None:
            weekdays = [item.weekday for item in value]
            if len(set(weekdays)) != len(weekdays):
                raise ValueError("Each weekday may appear only once.")
        return value


# --- Approval requests (#40) -------------------------------------------------------

type ApprovalStatus = Literal[
    "pending", "approved", "rejected", "changes_requested", "cancelled"
]
type ApprovalPriority = Literal["low", "normal", "high"]
type ApprovalAction = Literal["approve", "reject", "request_changes"]
type ApprovalStatusFilter = Literal[
    "open", "pending", "approved", "rejected", "changes_requested", "cancelled", "all"
]
type ApprovalAssigneeFilter = Literal["any", "me", "unassigned"]


class AccountName(BaseModel):
    """A user named in an approval request: the id and the display name only."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    display_name: str


class ApprovalRequestItem(BaseModel):
    """One approval request as a reviewer sees it.

    ``subject`` is the type's own display data (for ``identity.registration``:
    ``username``, ``display_name`` and ``requested_role``); never a secret.
    ``allowed_actions`` are the decisions the request takes now (from its
    status and its type)."""

    id: uuid.UUID
    request_type: str
    status: ApprovalStatus
    priority: ApprovalPriority
    requester: AccountName | None
    assignee: AccountName | None
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: AccountName | None
    reason: str | None
    subject: dict[str, Any]
    allowed_actions: list[ApprovalAction]
    version: int


class ApprovalRejectBody(BaseModel):
    """``POST …/reject``: the reason, required only by some types (at most
    1000 characters once trimmed)."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class ApprovalRequestChangesBody(BaseModel):
    """``POST …/request-changes``: what the requester must change (required;
    at most 1000 characters once trimmed). A missing reason is a ``422`` on
    ``reason`` after the ``404`` and ``409`` checks."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
