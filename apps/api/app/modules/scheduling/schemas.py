"""Class session bodies (#48 B4, C1, E1; #49 B, E; ADR-0005 wire format).

Times are instants (ADR-0004): ``starts_at`` / ``ends_at`` carry an offset
(RFC 3339); a naive value is ``422`` (``naive_datetime``). Names come from
the other modules' references (``catalog``, ``identity``, ``families``).

The create body (:class:`ClassSessionCreate`) is :class:`ClassSessionSlot`
plus the ``override``; the slot suggestions body (#50,
:class:`SlotSuggestionRequest`) is :class:`ClassSessionSlot` plus the
search (``limit``, ``search_from``, ``search_to``).

An ``override`` (#49 B) lists the soft codes to override and a reason. Its
``422`` codes: ``reason_required`` / ``reason_too_long`` on
``override.reason``; ``codes_required`` / ``duplicate_code`` on
``override.codes``; ``code_not_overridable`` (a hard code) /
``code_unknown`` (a warning code or any other string) on
``override.codes[i]``. The sets are :mod:`~app.modules.scheduling.conflicts`'.
"""

import re
import uuid
from datetime import date, datetime, time
from typing import Any, Final, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.modules.scheduling.conflicts import HARD, SOFT
from app.modules.scheduling.models import (
    DECLINE_NOTE_MAX_LENGTH,
    OVERRIDE_REASON_MAX_LENGTH,
)

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


class ClassSessionSlot(BaseModel):
    """The members a one-off individual session is made of: the create body
    without the ``override`` (#48 B1). Shared by the create and the slot
    suggestions (#50 B1)."""

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


class ClassSessionCreate(ClassSessionSlot):
    """A one-off individual session (``POST /api/v1/class-sessions``).

    Groups and courses (#56, #65) extend ``kind``. ``override`` (#49) needs
    ``schedule.override``."""

    override: SessionOverride | None = Field(
        default=None,
        description=(
            "Override the listed soft conflicts with a reason (needs"
            " schedule.override). Null or absent: every conflict blocks."
        ),
    )
    respond_by: AwareDatetime | None = Field(
        default=None,
        description=(
            "With a tutor only (#52): an earlier deadline for the tutor's"
            " answer, after now and no later than the default (the scheduling"
            " policy's response time, but never after the start); truncated to"
            " whole minutes. Null or absent: the default."
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


class PendingOffer(BaseModel):
    """The pending offer of an ``offered`` session (#52 G3)."""

    id: uuid.UUID
    tutor: SessionTutor
    respond_by: datetime


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
    series_id: uuid.UUID | None = Field(
        description="The series this session is an occurrence of (#53); null: one-off."
    )
    original_start: datetime | None = Field(
        description=(
            "The occurrence's instant in its series rule (null for a one-off"
            " session); it stays when the occurrence is moved."
        )
    )
    is_exception: bool = Field(
        description="An occurrence edited apart from its series (#54)."
    )
    conflict_codes: list[str] = Field(
        description=(
            "The codes that put a generated occurrence in `conflict`"
            " (informational; kept after it leaves `conflict`)."
        )
    )
    pending_offer: PendingOffer | None = Field(
        description=("The pending offer of an `offered` session (#52); null otherwise.")
    )


type SchedulingWarningCode = Literal[
    "daily_limit_exceeded", "weekly_limit_exceeded", "break_too_short"
]


class SchedulingWarning(BaseModel):
    """A rule that never blocks (ADR-0005 warnings; #50 A): the tutor's
    teaching limits and required break (:mod:`~app.modules.scheduling.workload`).

    ``details``: ``daily_limit_exceeded`` ``{date, limit_minutes,
    scheduled_minutes}``; ``weekly_limit_exceeded`` ``{week_start,
    limit_minutes, scheduled_minutes}`` (``week_start`` is the Monday of the
    ISO week); ``break_too_short`` ``{class_session_id, gap_minutes,
    required_break_minutes}``."""

    code: SchedulingWarningCode
    details: dict[str, Any]


class ClassSessionCreated(ClassSessionDetail):
    """The created session and its workload warnings (#50 A7)."""

    warnings: list[SchedulingWarning] = Field(
        description=(
            "The tutor's workload warnings for this session: daily, weekly,"
            " then break; empty without a tutor. They never block."
        )
    )


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


# --- Slot suggestions (#50 B) -----------------------------------------------------


SUGGESTIONS_DEFAULT_LIMIT: Final = 5
SUGGESTIONS_MAX_LIMIT: Final = 10
SEARCH_DEFAULT_DAYS: Final = 7
SEARCH_MAX_DAYS: Final = 14


def search_range_error(
    search_from: date, search_to: date
) -> PydanticCustomError | None:
    """The ``search_to`` error of a window (#50 B1), or ``None``."""
    if search_to < search_from:
        return PydanticCustomError(
            "search_range_invalid", "The search end must not be before its start."
        )
    if (search_to - search_from).days + 1 > SEARCH_MAX_DAYS:
        return PydanticCustomError(
            "search_range_too_long", "Search at most 14 days at a time."
        )
    return None


class SlotSuggestionRequest(ClassSessionSlot):
    """``POST /api/v1/class-sessions/suggestions`` (#50 B1): the session's
    members (the requested start and end give only the duration and the
    ranking anchor) plus the search. No ``override``: only slots that need
    none are returned."""

    limit: int = Field(
        default=SUGGESTIONS_DEFAULT_LIMIT,
        ge=1,
        le=SUGGESTIONS_MAX_LIMIT,
        description="How many slots to return at most: 1-10, default 5.",
    )
    search_from: date | None = Field(
        default=None,
        description=(
            "The first local date (center zone) to search; default the local"
            " date of starts_at."
        ),
    )
    search_to: date | None = Field(
        default=None,
        description=(
            "The last local date to search, at most 14 dates in all; default"
            " search_from + 6 days."
        ),
    )

    @field_validator("search_to")
    @classmethod
    def _window(cls, value: date | None, info: ValidationInfo) -> date | None:
        start = info.data.get("search_from")
        if value is not None and start is not None:
            error = search_range_error(start, value)
            if error is not None:
                raise error
        return value


class SuggestedSlot(BaseModel):
    """One free time: every hard and soft rule passes (#50 B4)."""

    starts_at: datetime
    ends_at: datetime
    warnings: list[SchedulingWarning] = Field(
        description="The workload warnings a create at this time would get."
    )


class SlotSuggestions(BaseModel):
    """The free times nearest the requested start (#50 B5, B7)."""

    search_from: date
    search_to: date
    limit: int
    slots: list[SuggestedSlot] = Field(
        description=(
            "0 to limit slots, nearest the requested start first (a tie goes"
            " to the earlier start)."
        )
    )


# --- Tutor suggestions (#51 D, E) ---------------------------------------------------

TUTOR_SUGGESTIONS_DEFAULT_LIMIT: Final = 10
TUTOR_SUGGESTIONS_MAX_LIMIT: Final = 50


class TutorSuggestionQuery(BaseModel):
    """The query of ``GET /api/v1/tutor-suggestions`` (#51 D1): a new slot.
    Unknown parameters are ``422``."""

    model_config = ConfigDict(extra="forbid")

    subject_id: uuid.UUID
    starts_at: datetime = Field(description="An instant with an offset (RFC 3339).")
    ends_at: datetime = Field(description="An instant with an offset (RFC 3339).")
    classroom_id: uuid.UUID | None = Field(
        default=None,
        description="The classroom; ranks the tutors who prefer it first on ties.",
    )
    limit: int = Field(
        default=TUTOR_SUGGESTIONS_DEFAULT_LIMIT,
        ge=1,
        le=TUTOR_SUGGESTIONS_MAX_LIMIT,
        description="At most this many items (1-50).",
    )


class TutorSuggestion(BaseModel):
    """An eligible tutor and the values of the workload ranking rule.
    The limits are shown only: they never drop or reorder a tutor."""

    rank: int = Field(description="The position in the ranking, from 1.")
    tutor: SessionTutor
    weekly_minutes: int = Field(
        description=(
            "Scheduled minutes as tutor (offered, confirmed, completed)"
            " starting in the slot's local ISO week."
        )
    )
    daily_minutes: int = Field(
        description="The same, starting on the slot's local date."
    )
    weekly_limit_minutes: int | None = Field(description="Null is no limit.")
    daily_limit_minutes: int | None = Field(description="Null is no limit.")
    prefers_classroom: bool = Field(
        description="The slot has a classroom and it is one of the tutor's preferred."
    )


class TutorSuggestionList(BaseModel):
    """The first ``limit`` eligible tutors in ranking order, and how many
    are eligible in all (advisory: creating or offering re-checks
    everything)."""

    items: list[TutorSuggestion]
    eligible_count: int


# --- Assignment offers (#52 C, D, G) ----------------------------------------------

type OfferScopeCode = Literal["session"]
type OfferStatusCode = Literal[
    "pending", "accepted", "declined", "expired", "withdrawn"
]
type OfferStatusFilter = Literal["pending", "answered", "all"]

OFFERS_DEFAULT_LIMIT: Final = 20
OFFERS_MAX_LIMIT: Final = 100


class AssignmentOfferCreate(BaseModel):
    """Offer an ``unassigned`` session to a tutor
    (``POST /api/v1/class-sessions/{id}/offers``, #52 C2)."""

    model_config = ConfigDict(extra="forbid")

    tutor_user_id: uuid.UUID
    version: int = Field(description="The session's version, as last read.")
    respond_by: AwareDatetime | None = Field(
        default=None,
        description=(
            "An earlier deadline for the tutor's answer, after now and no later"
            " than the default; truncated to whole minutes."
        ),
    )
    override: SessionOverride | None = Field(
        default=None,
        description=(
            "Override the listed soft conflicts with a reason (needs"
            " schedule.override)."
        ),
    )


def _check_note(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    note = value.strip()
    if len(note) > DECLINE_NOTE_MAX_LENGTH:
        raise PydanticCustomError(
            "decline_note_too_long", "The note is at most 500 characters."
        )
    return note or None


class AssignmentOfferDecline(BaseModel):
    """The optional body of a decline (#52 D3)."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(
        default=None,
        description=(
            "Why the tutor declines, for the staff: trimmed, at most 500"
            " characters; empty is no note."
        ),
    )

    _note = field_validator("note", mode="before")(_check_note)


class OfferPerson(BaseModel):
    """A user named on an offer."""

    id: uuid.UUID
    display_name: str


class OfferSessionSubject(BaseModel):
    """The subject of an offered session."""

    name: str
    education_level_name: str


class OfferSessionClassroom(BaseModel):
    """The classroom of an offered session."""

    name: str


class OfferSessionParticipant(BaseModel):
    """A student of an offered session."""

    display_name: str


class OfferSession(BaseModel):
    """A session the offer covers."""

    id: uuid.UUID
    state: SessionStateCode
    starts_at: datetime
    ends_at: datetime
    subject: OfferSessionSubject
    classroom: OfferSessionClassroom | None
    participants: list[OfferSessionParticipant] = Field(
        description="The students, by display name."
    )


class AssignmentOfferItem(BaseModel):
    """One assignment offer (#52 G1)."""

    id: uuid.UUID
    scope: OfferScopeCode
    status: OfferStatusCode
    tutor: OfferPerson
    offered_at: datetime
    offered_by: OfferPerson
    respond_by: datetime
    responded_at: datetime | None
    decline_note: str | None
    sessions: list[OfferSession] = Field(description="The covered sessions, by start.")


class AssignmentOfferCreated(BaseModel):
    """The new offer and the tutor's workload warnings (#52 C2 step 12)."""

    offer: AssignmentOfferItem
    warnings: list[SchedulingWarning] = Field(
        description="The tutor's workload warnings for the session. They never block."
    )


class AssignmentOfferPage(BaseModel):
    """A page of the caller's offers (#52 G2)."""

    items: list[AssignmentOfferItem]
    total: int
    limit: int
    offset: int


class ClassSessionOffers(BaseModel):
    """A session's offer history, newest first (#52 G3)."""

    items: list[AssignmentOfferItem]


# --- Class series (#53 E, I) --------------------------------------------------------

_START_TIME_FORMAT: Final = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


class SeriesRecurrence(BaseModel):
    """A weekly rule as structured fields (ADR-0005 D4): the API never takes
    ``RRULE`` text. The ranges are checked after the locks, as ``422`` on
    ``recurrence.<field>`` (``weekdays_empty``, ``weekday_out_of_range``,
    ``weekdays_duplicate``, ``interval_out_of_range``, ``until_and_count``,
    ``count_out_of_range``, ``until_before_start``; ``no_occurrences`` on
    ``recurrence``)."""

    model_config = ConfigDict(extra="forbid")

    weekdays: list[int] = Field(
        description=(
            "ISO weekdays (1 = Monday … 7 = Sunday), at least one, no duplicates."
        )
    )
    interval: int = Field(default=1, description="Every 1-4 weeks (default 1).")
    until: date | None = Field(
        default=None,
        description="The last local date (inclusive); not together with `count`.",
    )
    count: int | None = Field(
        default=None,
        description=(
            "How many occurrences in all (1-200), the ones skipped for a closure"
            " included; not together with `until`."
        ),
    )


def _check_start_time(value: Any) -> Any:
    # Only HH:MM (JSON) or a whole-minute time (Python callers): a value with
    # seconds, or any other shape, is refused.
    if isinstance(value, time) and value.tzinfo is None:
        if value.second == 0 and value.microsecond == 0:
            return value
    elif isinstance(value, str) and _START_TIME_FORMAT.fullmatch(value):
        return value
    raise PydanticCustomError("time_format", "Give the time as HH:MM.")


class ClassSeriesCreate(BaseModel):
    """A recurring individual series (``POST /api/v1/class-series``, #53 E1).

    ``starts_on`` is the local date of the rule's start (DTSTART) and
    ``start_time`` the local wall-clock time (``HH:MM``) in the center zone.
    No ``override`` (#236) and no ``rrule`` text: both are ``422``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["individual"]
    student_id: uuid.UUID
    subject_id: uuid.UUID
    tutor_user_id: uuid.UUID | None = Field(
        default=None,
        description="The tutor to offer the occurrences to; null: unassigned.",
    )
    classroom_id: uuid.UUID | None = Field(
        default=None, description="The classroom; null is no room."
    )
    starts_on: date = Field(
        description="The local date the rule starts on (not before today)."
    )
    start_time: time = Field(
        description="The local start time, HH:MM (whole minutes, no seconds)."
    )
    duration_minutes: int = Field(ge=15, le=480, description="15-480 minutes.")
    recurrence: SeriesRecurrence

    _start_time = field_validator("start_time", mode="before")(_check_start_time)


class SeriesRecurrenceDetail(BaseModel):
    """The stored rule's structured fields."""

    weekdays: list[int]
    interval: int
    until: date | None
    count: int | None


class SeriesOccurrence(BaseModel):
    """One occurrence row of a series."""

    id: uuid.UUID
    state: SessionStateCode
    starts_at: datetime
    ends_at: datetime
    original_start: datetime
    is_exception: bool


class ClassSeriesDetail(BaseModel):
    """One class series (``GET /api/v1/class-series/{id}``). The web app
    never parses ``rrule``: it reads ``recurrence``."""

    id: uuid.UUID
    kind: SessionKindCode
    subject: SessionSubject
    tutor: SessionTutor | None
    classroom: SessionClassroom | None
    participants: list[SessionParticipant] = Field(
        description="The students, by display name."
    )
    starts_on: date
    start_time: str = Field(description="The local start time, HH:MM.")
    duration_minutes: int
    time_zone: str = Field(description="The center zone the series was created in.")
    recurrence: SeriesRecurrenceDetail
    rrule: str = Field(description="The normalized RFC 5545 rule (derived).")
    last_occurrence_on: date | None = Field(
        description="The local date of the last occurrence; null: open-ended."
    )
    version: int
    created_at: datetime
    updated_at: datetime
    occurrences: list[SeriesOccurrence] = Field(
        description=(
            "The occurrence rows starting now or later, by start (rows are"
            " generated up to the rolling horizon)."
        )
    )


class ClassSeriesCreated(ClassSeriesDetail):
    """The created series and its workload warnings."""

    warnings: list[SchedulingWarning] = Field(
        description="Always empty for a series for now."
    )
