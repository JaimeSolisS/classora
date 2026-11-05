"""Administration request and response bodies: the audit log (#22), the
business settings (#37), the approval requests (#40), the notifications
(#41) and the scheduling policy (#61)."""

import unicodedata
import uuid
import zoneinfo
from datetime import datetime
from functools import cache
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
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
    MAKEUP_WINDOW_DAYS_MAX,
    MAKEUP_WINDOW_DAYS_MIN,
    NOTICE_HOURS_MAX,
    NOTICE_HOURS_MIN,
    PHONE_MAX_LENGTH,
    RECEIPT_MESSAGE_MAX_LENGTH,
    RESPONSE_HOURS_MAX,
    RESPONSE_HOURS_MIN,
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


def _normalize_message(value: Any) -> Any:
    """CRLF and CR become LF, then trim; an empty message clears it."""
    if isinstance(value, str):
        value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        return value or None
    return value


def _no_control_characters(value: str | None) -> str | None:
    if value is not None and any(
        char != "\n" and unicodedata.category(char) == "Cc" for char in value
    ):
        raise ValueError("Only line breaks are allowed as control characters.")
    return value


ReceiptMessage = Annotated[
    Annotated[str, StringConstraints(max_length=RECEIPT_MESSAGE_MAX_LENGTH)] | None,
    BeforeValidator(_normalize_message),
    AfterValidator(_no_control_characters),
    Field(
        description="Plain text printed on new receipts in this language, at most"
        " 500 characters; line breaks are kept. null (or an empty string) clears"
        " it."
    ),
]
"""A receipt message (#121 B2): trimmed, CRLF/CR as LF, at most 500
characters, no control character other than LF; ``""`` → ``None``."""
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
    receipt_message_es: str | None = Field(
        description="The message printed on new Spanish receipts (null: none)."
    )
    receipt_message_en: str | None = Field(
        description="The message printed on new English receipts (null: none)."
    )
    version: int
    updated_at: datetime


class BusinessSettingsUpdate(BaseModel):
    """A change to the settings (``PATCH /api/v1/business-settings``) with the
    ``version`` the caller loaded. Fields not sent stay unchanged; ``null``
    (or an empty string) clears a contact field or a receipt message (#121
    B2: it only changes receipts issued afterwards); ``null`` is ``422`` for
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
    receipt_message_es: ReceiptMessage = None
    receipt_message_en: ReceiptMessage = None

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
type ApprovalSort = Literal["priority", "oldest", "newest"]
"""The inbox orders (#118 E7): ``priority`` (the default: high first, then
oldest first), ``oldest`` and ``newest`` (by creation); each ends on ``id``."""


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


class AssignApprovalBody(BaseModel):
    """``POST …/assign`` (#118 A1): the new assignee (``null`` unassigns) and
    the request's ``version`` as the caller last read it. Both are required."""

    model_config = ConfigDict(extra="forbid")

    assignee_user_id: uuid.UUID | None
    version: int


class ApprovalAssigneeOptions(BaseModel):
    """``GET …/assignee-options`` (#118 B1): the active users who hold the
    request's review permission now, by display name then id."""

    items: list[AccountName]


class ApprovalRequestChangesBody(BaseModel):
    """``POST …/request-changes``: what the requester must change (required;
    at most 1000 characters once trimmed). A missing reason is a ``422`` on
    ``reason`` after the ``404`` and ``409`` checks."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


# --- Notifications (#41) -------------------------------------------------------------

NotificationParamValue = str | int | bool | None


class NotificationItem(BaseModel):
    """One of the caller's notifications. The client renders the message from
    ``notification_type`` and ``params``; ``read_at`` is null while unread."""

    id: uuid.UUID
    notification_type: str
    params: dict[str, NotificationParamValue]
    created_at: datetime
    read_at: datetime | None


class NotificationUnreadCount(BaseModel):
    count: int


class NotificationReadAllBody(BaseModel):
    """``up_to``: the newest notification the caller has seen; only that one
    and older ones are marked read. ``null`` marks every unread one."""

    model_config = ConfigDict(extra="forbid")

    up_to: uuid.UUID | None = None


class NotificationReadAllResult(BaseModel):
    updated: int


# --- Notification preferences (#119 C) ----------------------------------------------

NOTIFICATION_PREFERENCE_CHANGES_MAX = 50


class NotificationPreferenceItem(BaseModel):
    """One type in the caller's catalogue. A mandatory type is always
    ``enabled``."""

    notification_type: str
    mandatory: bool
    enabled: bool


class NotificationPreferences(BaseModel):
    """The caller's catalogue: the types of their roles, by
    ``notification_type``."""

    items: list[NotificationPreferenceItem]


class NotificationPreferenceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_type: str
    enabled: bool


class NotificationPreferencesBody(BaseModel):
    """1 to 50 changes, applied all or none (set semantics: a resend changes
    nothing)."""

    model_config = ConfigDict(extra="forbid")

    items: Annotated[
        list[NotificationPreferenceChange],
        Field(min_length=1, max_length=NOTIFICATION_PREFERENCE_CHANGES_MAX),
    ]


# --- Scheduling policy (#61) ---------------------------------------------------------

NoticeHours = Annotated[int, Field(ge=NOTICE_HOURS_MIN, le=NOTICE_HOURS_MAX)]
ResponseHours = Annotated[int, Field(ge=RESPONSE_HOURS_MIN, le=RESPONSE_HOURS_MAX)]
MakeupWindowDays = Annotated[
    int, Field(ge=MAKEUP_WINDOW_DAYS_MIN, le=MAKEUP_WINDOW_DAYS_MAX)
]


class SchedulingPolicyRules(BaseModel):
    """The rules of one scheduling policy version."""

    booking_notice_hours: NoticeHours = Field(
        description="Hours of notice a booking needs (0 = none)."
    )
    change_notice_hours: NoticeHours = Field(
        description=(
            "A family's cancellation or reschedule later than this many hours"
            " before the start is late (0 = never late before the start)."
        )
    )
    response_hours: ResponseHours = Field(
        description="Hours a tutor has to answer an offer (ADR-0005 D3)."
    )
    makeup_after_on_time: bool = Field(
        description="A makeup is allowed after an on-time cancellation."
    )
    makeup_after_late: bool = Field(
        description="A makeup is allowed after a late cancellation."
    )
    makeup_after_no_show: bool = Field(
        description="A makeup is allowed after a no-show."
    )
    makeup_window_days: MakeupWindowDays = Field(
        description="Days after the missed class within which a makeup happens."
    )


class SchedulingPolicyItem(SchedulingPolicyRules):
    """One policy version, in force from ``effective_from`` until the next
    version's ``effective_from``."""

    id: uuid.UUID
    effective_from: datetime
    created_at: datetime


class SchedulingPolicyOverview(BaseModel):
    """The version in force now, the upcoming ones and the past ones."""

    current: SchedulingPolicyItem
    upcoming: list[SchedulingPolicyItem] = Field(
        description="Versions that take effect later, oldest first."
    )
    history: list[SchedulingPolicyItem] = Field(
        description="Versions no longer in force, newest first (at most 50)."
    )


class SchedulingPolicyCreate(SchedulingPolicyRules):
    """A new version: every rule, the latest version it replaces and when it
    takes effect."""

    model_config = ConfigDict(extra="forbid")

    based_on_id: uuid.UUID = Field(
        description=(
            "The id of the latest version (the current one or the last"
            " upcoming one); otherwise 409 version_conflict."
        )
    )
    effective_from: AwareDatetime | None = Field(
        default=None,
        description=(
            "When it takes effect: now when omitted; otherwise not in the past"
            " and later than every existing version (422"
            " invalid_effective_from)."
        ),
    )
