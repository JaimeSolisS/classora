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

A series ``override`` (#236 B, :class:`SeriesOverride`) lists
``(occurrence_start, code)`` pairs instead of codes: ``pairs_required`` /
``pairs_too_many`` / ``duplicate_pair`` on ``override.pairs``;
``code_not_overridable`` / ``code_unknown`` on ``override.pairs[i].code``;
``occurrence_start_invalid`` (naive, or not a whole minute) on
``override.pairs[i].occurrence_start``; the reason as above.
"""

import re
import uuid
from datetime import UTC, date, datetime, time
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
    CANCELLATION_REASON_MAX_LENGTH,
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


SERIES_OVERRIDE_PAIRS_MAX: Final = 1400
"""At most 7 soft codes for each of the 200 occurrences a ``COUNT`` allows
(#236 B1)."""


def _check_occurrence_start(value: datetime) -> datetime:
    # An aware whole-minute instant, as the 409 body writes it.
    if value.tzinfo is None or value.utcoffset() is None:
        raise PydanticCustomError(
            "occurrence_start_invalid", "Give the occurrence's instant with an offset."
        )
    if value.second or value.microsecond:
        raise PydanticCustomError(
            "occurrence_start_invalid", "The occurrence starts on a whole minute."
        )
    return value


def _check_pair_code(code: str) -> str:
    if (error := _code_error(code)) is not None:
        raise error
    return code


class OverridePair(BaseModel):
    """One soft conflict of one occurrence (#236 B1; ADR-0005 D4):
    ``occurrence_start`` is ``details.occurrence_start`` of the ``409``
    (for an edit, the occurrence's **new** instant)."""

    model_config = ConfigDict(extra="forbid")

    occurrence_start: datetime = Field(
        description="The occurrence's instant, with an offset (RFC 3339)."
    )
    code: str = Field(description="A soft conflict code; never a hard one.")

    _occurrence_start = field_validator("occurrence_start")(_check_occurrence_start)
    _code = field_validator("code")(_check_pair_code)


def _check_pairs(pairs: list[OverridePair]) -> list[OverridePair]:
    if not pairs:  # empty, or missing (SeriesOverride._missing_members)
        raise PydanticCustomError("pairs_required", "List the conflicts to override.")
    if len(pairs) > SERIES_OVERRIDE_PAIRS_MAX:
        raise PydanticCustomError(
            "pairs_too_many", "List at most 1400 occurrence and rule pairs."
        )
    keys = {(pair.occurrence_start.astimezone(UTC), pair.code) for pair in pairs}
    if len(keys) != len(pairs):
        raise PydanticCustomError(
            "duplicate_pair", "List each occurrence and rule only once."
        )
    return pairs


class SeriesOverride(BaseModel):
    """The soft conflicts of a series write to override, occurrence by
    occurrence, and why (#236 B1; ADR-0005 D4: "listed occurrence and code
    pairs"). The server recomputes every occurrence's conflicts: a hard one
    is never overridden, every soft one must be listed with its occurrence,
    and listed pairs that don't occur are ignored. ``reason`` is the #49
    reason (trimmed, 1-1000)."""

    model_config = ConfigDict(extra="forbid")

    pairs: list[OverridePair] = Field(
        description=(
            "The (occurrence_start, code) pairs to override, 1-1400, no"
            " duplicates (instants compared in UTC)."
        )
    )
    reason: str = Field(
        description="Why the rules are overridden: 1-1000 characters, trimmed."
    )

    @model_validator(mode="before")
    @classmethod
    def _missing_members(cls, data: Any) -> Any:
        # A missing reason is reason_required and missing pairs are
        # pairs_required, not pydantic's "missing".
        if isinstance(data, dict):
            return {"pairs": [], "reason": None} | data
        return data

    _reason = field_validator("reason", mode="before")(_check_reason)
    _pairs = field_validator("pairs")(_check_pairs)


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
    """The pending offer of an ``offered`` session (#52 G3): its own, or the
    series offer that covers it (#233 F2)."""

    id: uuid.UUID
    scope: Literal["session", "series"]
    tutor: SessionTutor
    respond_by: datetime


class PendingSubstitution(BaseModel):
    """The pending substitution of a ``confirmed`` session (#55 E3)."""

    id: uuid.UUID
    substitute: SessionTutor
    respond_by: datetime


class SessionCancellation(BaseModel):
    """When and why a session was cancelled (#55 E3)."""

    cancelled_at: datetime
    reason: str


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
    pending_substitution: PendingSubstitution | None = Field(
        description=(
            "The pending substitution of a `confirmed` session (#55): the"
            " session stays with its tutor until the substitute accepts."
        ),
    )
    cancellation: SessionCancellation | None = Field(
        description="When and why a `cancelled` session was cancelled; null otherwise.",
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

type OfferScopeCode = Literal["session", "series"]
type OfferKindCode = Literal["assignment", "substitution"]
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


class OfferSeries(BaseModel):
    """The series a series offer is for (#233 F1): its rule and the local
    date the offer covers from. Times are local in ``time_zone``."""

    id: uuid.UUID
    kind: SessionKindCode
    subject: OfferSessionSubject
    weekdays: list[int] = Field(description="ISO weekdays (1 = Monday).")
    interval: int
    start_time: str = Field(description="The local start time, HH:MM.")
    duration_minutes: int
    time_zone: str
    from_date: date = Field(description="The local date the offer covers from.")
    until: date | None
    count: int | None
    last_occurrence_on: date | None


class OfferSubstitution(BaseModel):
    """What a substitute sees of a substitution (#55 E4): the original tutor,
    never the reason."""

    original_tutor: OfferPerson


class AssignmentOfferItem(BaseModel):
    """One assignment offer (#52 G1; a series offer, #233 F1)."""

    id: uuid.UUID
    scope: OfferScopeCode
    kind: OfferKindCode = Field(
        description=(
            "`assignment` gives the sessions a tutor; `substitution` (#55) hands"
            " one confirmed session over from its original tutor."
        )
    )
    substitution: OfferSubstitution | None = Field(
        description="For a substitution offer, whom it replaces; null otherwise."
    )
    status: OfferStatusCode
    tutor: OfferPerson
    offered_at: datetime
    offered_by: OfferPerson
    respond_by: datetime
    responded_at: datetime | None
    decline_note: str | None
    series: OfferSeries | None = Field(
        description="The series of a series offer; null for a session offer."
    )
    session_count: int = Field(description="How many sessions the offer covers.")
    sessions: list[OfferSession] = Field(
        description=(
            "The covered sessions, by start: every one for a session offer, the"
            " first 10 for a series offer."
        )
    )
    sessions_truncated: bool = Field(
        description="More sessions are covered than `sessions` lists."
    )


class ClassSeriesOfferCreate(BaseModel):
    """Offer a series without a tutor to one
    (``POST /api/v1/class-series/{id}/offers``, #233 C2). No ``override``
    (M4-09c): any conflict refuses the offer."""

    model_config = ConfigDict(extra="forbid")

    tutor_user_id: uuid.UUID
    version: int = Field(description="The series' version, as last read.")
    from_date: date | None = Field(
        default=None,
        description=(
            "The local date the offer covers from (not before today); default:"
            " the later of the series' start and today."
        ),
    )
    respond_by: AwareDatetime | None = Field(
        default=None,
        description=(
            "An earlier deadline for the tutor's answer, after now and no later"
            " than the default; truncated to whole minutes."
        ),
    )


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
    No ``rrule`` text (``422``). ``override`` (#236) needs
    ``schedule.override``."""

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
    respond_by: AwareDatetime | None = Field(
        default=None,
        description=(
            "With a tutor: an earlier deadline for the series offer, after now"
            " and no later than the default; truncated to whole minutes."
        ),
    )
    override: SeriesOverride | None = Field(
        default=None,
        description=(
            "Override the listed soft conflicts, occurrence by occurrence, with"
            " a reason (needs schedule.override). Null or absent: every"
            " conflict blocks."
        ),
    )

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
    overridden: bool = Field(
        description=(
            "Whether at least one override of its soft conflicts names it (#236)."
        )
    )
    conflict_codes: list[str] = Field(
        description=(
            "The codes that put it in `conflict` (informational; kept after it"
            " leaves `conflict`; #236)."
        )
    )
    version: int = Field(description="The session's version (#236).")


class SeriesAssignment(BaseModel):
    """The series' tutor assignment (#233 F2): ``pending`` while its series
    offer waits for an answer (``respond_by`` set), ``accepted`` after."""

    status: Literal["pending", "accepted"]
    tutor: SessionTutor
    from_date: date = Field(description="The local date it covers from.")
    offer_id: uuid.UUID | None = Field(
        description="The series offer; null for a series assigned before offers."
    )
    respond_by: datetime | None


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
        description=(
            "No occurrence after this local date; null: open-ended. A series"
            " cancelled before its first remaining occurrence ends the day"
            " before the cancellation date, which can be before `starts_on`."
        )
    )
    ended: bool = Field(
        description=(
            "Whether the series is over: `last_occurrence_on` is set and before"
            " the center's local today (#54)."
        )
    )
    split_from_series_id: uuid.UUID | None = Field(
        description=(
            "The series a 'this and following' edit split this one from (#54);"
            " null: none."
        )
    )
    split_into_series_ids: list[uuid.UUID] = Field(
        description=(
            "The series split from this one by later edits, oldest first (#54)."
        )
    )
    assignment: SeriesAssignment | None = Field(
        description="The tutor assignment; null when the series has no tutor."
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


# --- Editing sessions and series (#54 C) ------------------------------------------


class ClassSessionUpdate(BaseModel):
    """Move one session (``PATCH /api/v1/class-sessions/{id}``, #54 C1): new
    instants and optionally a new room. The tutor, the subject and the
    students never change here. ``classroom_id`` absent keeps the room;
    ``null`` removes it. No ``override`` yet (#58 adds it)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(description="The session's version, as read.")
    starts_at: datetime = Field(description="An instant with an offset (RFC 3339).")
    ends_at: datetime = Field(description="An instant with an offset (RFC 3339).")
    classroom_id: uuid.UUID | None = Field(
        default=None,
        description="The new classroom; null is no room; absent keeps the room.",
    )


class ClassSessionUpdated(ClassSessionDetail):
    """The moved session and its warnings."""

    warnings: list[SchedulingWarning] = Field(
        description="Always empty for an edit for now."
    )


type SeriesEditMode = Literal["following", "all"]


def _check_from_date(value: date | None, info: ValidationInfo) -> date | None:
    mode = info.data.get("mode")
    if mode == "following" and value is None:
        raise PydanticCustomError(
            "from_date_required", "Give the date the change starts on."
        )
    if mode == "all" and value is not None:
        raise PydanticCustomError(
            "from_date_not_allowed", "A change of the whole series takes no date."
        )
    return value


class ClassSeriesEdit(BaseModel):
    """Change a series from a date on (``POST
    /api/v1/class-series/{id}/edits``, #54 C3): the **full** new rule, not a
    patch. ``mode = "following"`` starts on ``from_date`` (a local date);
    ``"all"`` starts on the next occurrence that hasn't begun and takes
    ``from_date = null``. The recurrence ``422`` codes are the create's
    (``until_before_start`` is against the start date of the change).
    ``override`` (#236) names the **new** instants and needs
    ``schedule.override``."""

    model_config = ConfigDict(extra="forbid")

    mode: SeriesEditMode
    from_date: date | None = Field(
        description="Required for `following`; null for `all`."
    )
    version: int = Field(description="The series' version, as read.")
    start_time: time = Field(
        description="The local start time, HH:MM (whole minutes, no seconds)."
    )
    duration_minutes: int = Field(ge=15, le=480, description="15-480 minutes.")
    classroom_id: uuid.UUID | None = Field(
        description="The classroom; null is no room."
    )
    recurrence: SeriesRecurrence
    override: SeriesOverride | None = Field(
        default=None,
        description=(
            "Override the listed soft conflicts of the changed and new"
            " occurrences (their new instants) with a reason (needs"
            " schedule.override). Null or absent: every conflict blocks."
        ),
    )

    _start_time = field_validator("start_time", mode="before")(_check_start_time)
    _from_date = field_validator("from_date")(_check_from_date)


class SeriesEditSummary(BaseModel):
    """What an edit did to the occurrence rows."""

    moved: int = Field(description="Rows kept (same id) with the new rule.")
    created: int = Field(description="Rows created for dates the old rule lacked.")
    cancelled: int = Field(
        description="Rows cancelled (`series_changed`): their date left the rule."
    )
    skipped_closure: int = Field(
        description="Moved or created rows that are on a closure date."
    )
    exceptions_reset: int = Field(
        description="Edited occurrences that took the new rule's times."
    )


class ClassSeriesEdited(BaseModel):
    """The result of a series edit."""

    series: ClassSeriesDetail = Field(
        description="The resulting series: the new one after a split."
    )
    previous_series_id: uuid.UUID | None = Field(
        description="The series that was split (it ends the day before); null in place."
    )
    split: bool
    summary: SeriesEditSummary
    warnings: list[SchedulingWarning] = Field(
        description="Always empty for a series for now."
    )


def _check_cancel_reason(value: Any) -> Any:
    if value is None:
        raise PydanticCustomError("reason_required", "Give a reason.")
    if not isinstance(value, str):
        return value
    reason = value.strip()
    if not reason:
        raise PydanticCustomError("reason_required", "Give a reason.")
    if len(reason) > CANCELLATION_REASON_MAX_LENGTH:
        raise PydanticCustomError(
            "reason_too_long", "The reason is at most 1000 characters."
        )
    return reason


class ClassSeriesCancel(BaseModel):
    """Cancel a series' occurrences from a date on (``POST
    /api/v1/class-series/{id}/cancellations``, #54 C5); ``mode`` and
    ``from_date`` as in :class:`ClassSeriesEdit`. ``reason`` is trimmed,
    1-1000 characters (``reason_required`` / ``reason_too_long``)."""

    model_config = ConfigDict(extra="forbid")

    mode: SeriesEditMode
    from_date: date | None = Field(
        description="Required for `following`; null for `all`."
    )
    reason: str = Field(description="Why: 1-1000 characters, trimmed.")
    version: int = Field(description="The series' version, as read.")

    @model_validator(mode="before")
    @classmethod
    def _missing_reason(cls, data: Any) -> Any:
        # A missing reason is reason_required, not pydantic's "missing".
        if isinstance(data, dict):
            return {"reason": None} | data
        return data

    _from_date = field_validator("from_date")(_check_from_date)
    _reason = field_validator("reason", mode="before")(_check_cancel_reason)


class SeriesCancelSummary(BaseModel):
    """What a cancellation did to the occurrence rows."""

    cancelled: int
    skipped_closure_kept: int = Field(
        description="Rows skipped for a closure, left as they are."
    )


class ClassSeriesCancelled(BaseModel):
    """The result of a series cancellation."""

    series: ClassSeriesDetail
    summary: SeriesCancelSummary


# --- Overriding a conflict occurrence (#236 G3) ----------------------------------


class ConflictOverrideCreate(BaseModel):
    """Override the soft conflicts of one ``conflict`` occurrence and schedule
    it (``POST /api/v1/class-sessions/{id}/conflict-overrides``, #236 G3):
    the #49 ``{codes, reason}`` member, required."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(description="The session's version, as read.")
    override: SessionOverride


# --- Substitutions and cancellations (#55) ---------------------------------------


class SubstitutionCreate(BaseModel):
    """Offer one ``confirmed`` session to a substitute tutor (``POST
    /api/v1/class-sessions/{id}/substitutions``, #55 B2). ``reason`` is
    trimmed, 1-1000 characters (``reason_required`` /
    ``reason_too_long``)."""

    model_config = ConfigDict(extra="forbid")

    substitute_tutor_user_id: uuid.UUID
    version: int = Field(description="The session's version, as last read.")
    reason: str = Field(
        description=(
            "Why the tutor is replaced, for the staff (never shown to the"
            " substitute): 1-1000 characters, trimmed."
        )
    )
    respond_by: AwareDatetime | None = Field(
        default=None,
        description=(
            "An earlier deadline for the substitute's answer, after now and no"
            " later than the default; truncated to whole minutes."
        ),
    )
    override: SessionOverride | None = Field(
        default=None,
        description=(
            "Override the listed soft conflicts with a reason (needs"
            " schedule.override)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _missing_reason(cls, data: Any) -> Any:
        # A missing reason is reason_required, not pydantic's "missing".
        if isinstance(data, dict):
            return {"reason": None} | data
        return data

    _reason = field_validator("reason", mode="before")(_check_cancel_reason)


class SubstitutionItem(BaseModel):
    """One substitution of a session's tutor (#55 E1); ``status`` is its
    offer's."""

    id: uuid.UUID
    class_session_id: uuid.UUID
    status: OfferStatusCode
    offer_id: uuid.UUID
    original_tutor: OfferPerson
    substitute_tutor: OfferPerson
    reason: str
    requested_by: OfferPerson
    requested_at: datetime
    respond_by: datetime
    responded_at: datetime | None
    decline_note: str | None


class SubstitutionCreated(BaseModel):
    """The new substitution and the substitute's workload warnings."""

    substitution: SubstitutionItem
    warnings: list[SchedulingWarning] = Field(
        description=(
            "The substitute's workload warnings for the session. They never block."
        )
    )


class ClassSessionSubstitutions(BaseModel):
    """A session's substitutions, newest first (#55 E2)."""

    items: list[SubstitutionItem]


class ClassSessionCancel(BaseModel):
    """Cancel one session or occurrence (``POST
    /api/v1/class-sessions/{id}/cancel``, #55 C1). ``reason`` is trimmed,
    1-1000 characters (``reason_required`` / ``reason_too_long``)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(description="The session's version, as last read.")
    reason: str = Field(description="Why: 1-1000 characters, trimmed.")

    @model_validator(mode="before")
    @classmethod
    def _missing_reason(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {"reason": None} | data
        return data

    _reason = field_validator("reason", mode="before")(_check_cancel_reason)
