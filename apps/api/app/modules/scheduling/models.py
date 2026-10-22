"""Scheduling tables (#47 M4-02; spec §3, §16, W1; ADR-0005).

- ``class_sessions``: one scheduled class (``kind`` ``individual`` |
  ``group`` | ``course``) of one subject (the level follows from the
  subject, #32), with an optional tutor (``tutor_user_id``, ``users``) and an
  optional classroom (``classroom_id``; ``NULL`` = no room). ``starts_at`` /
  ``ends_at`` are instants (``TIMESTAMPTZ``, ADR-0004), whole minutes, 15–480
  minutes apart; ``scheduled_minutes`` stores that duration (ISSUE-F2) and a
  CHECK ties it to the bounds. ``actual_minutes`` (0–1440) is set at closeout
  (M5/M7). ``cancelled_at`` and ``cancellation_reason`` are set exactly when
  the session is ``cancelled``. ``Audited``, versioned. Sessions are never
  deleted (they are cancelled).
- ``class_session_participants``: the students of a session, one row per
  session and student. ``Audited``; not versioned: the session's
  ``version`` covers its participants (the #39 windows pattern).

**States (ADR-0005 D2, D6):** ``unassigned``, ``offered``, ``confirmed``,
``completed``, ``cancelled``, ``skipped_closure``, ``conflict`` (no
``draft``). An ``unassigned`` session has no tutor; ``offered``,
``confirmed`` and ``completed`` always have one. Transitions are enforced by
``scheduling.service.transition_class_session`` (``states.ALLOWED_TRANSITIONS``),
not by the database.

**Occupying sets and exclusion constraints (D1, D2):** the tutor is held in
:data:`~app.modules.scheduling.states.TUTOR_OCCUPYING_STATES` (``offered``,
``confirmed``, ``completed``), the room (and the students, checked by the
application in #48) in
:data:`~app.modules.scheduling.states.ROOM_OCCUPYING_STATES` (also
``unassigned``). ``cancelled``, ``skipped_closure`` and ``conflict`` hold
nothing. Two partial ``EXCLUDE USING gist`` constraints over
``tstzrange(starts_at, ends_at, '[)')`` (half-open: back-to-back sessions
are allowed; ``btree_gist`` is enabled by revision 0021, #38) make overlaps
impossible:

- ``ex_class_sessions_tutor_overlap``: one tutor, overlapping, both in a
  tutor-occupying state;
- ``ex_class_sessions_room_overlap``: one room, overlapping, both in a
  room-occupying state.

Their predicates are built from the Python sets, so the code and the
database agree (the integration drift test reads them back). See
[ADR-0005](../../../../../_docs/adr/0005-scheduling-invariants-overrides-and-recurrence.md).

- ``class_session_overrides`` (#49 D1): one override of a session's soft
  conflicts: ``codes`` (``TEXT[]``, non-empty, each one of the seven soft
  codes, in rule order), ``conflicts`` (``JSONB``, the overridden conflicts
  ``[{code, details}]`` as computed), ``reason`` (1-1000 characters once
  trimmed) and ``actor_user_id``. **Append-only:** the trigger
  :data:`OVERRIDES_IMMUTABLE_TRIGGER` rejects ``UPDATE`` and ``DELETE``
  (SQLSTATE 42501). Not ``Audited``: the named event
  ``scheduling.session_override`` is its audit record.

- ``class_series`` (#53 B2): a recurring series (``kind`` ``individual``
  here; ``group`` / ``course`` by #56 / #65) of one subject, with an
  optional tutor and classroom. Its weekly rule is stored as structured
  fields (``weekdays`` ISO 1-7, ``interval`` 1-4, an inclusive local
  ``until_on`` **or** a ``count`` 1-200) plus the derived, normalized
  ``rrule`` text, the local ``starts_on`` date, the local wall-clock
  ``start_time`` (whole minutes), ``duration_minutes`` (15-480) and the
  ``time_zone`` it was created in (ADR-0004, ADR-0005 D4).
  ``last_occurrence_on`` is the local date of a finite series' last
  occurrence (``NULL``: open-ended). ``assignment_status`` (``pending`` |
  ``accepted``) and the local ``assignment_from`` date are the series' tutor
  assignment (#233 A2): both are set exactly when ``tutor_user_id`` is.
  ``last_occurrence_on`` means "no occurrence after this date": a series
  cancelled before its first remaining occurrence (#54 C5) has
  ``until_on = last_occurrence_on`` = the day before the cancellation date,
  which can be earlier than ``starts_on``. ``split_from_series_id`` (#54 A2)
  is the series a "this and following" edit split this one from (never
  itself). ``Audited``, versioned.
- ``class_series_participants``: the students of a series (the #47
  participants pattern); the series ``version`` covers them. ``Audited``.
- ``class_sessions`` occurrence columns (#53 B4): ``series_id`` and
  ``original_start`` (both set, or both ``NULL`` for a one-off session),
  ``is_exception`` (set by #54 only) and ``conflict_codes`` (the codes that
  put a generated occurrence in ``conflict``; informational, kept after it
  leaves ``conflict``). ``uq_class_sessions_series_original_start`` makes
  materialization idempotent (ADR-0005 D4).

- ``assignment_offers`` (#52 A1; ADR-0005 D3): one offer of sessions to a
  tutor. ``scope`` (``session``, or ``series`` with ``class_series_id``
  and the local ``from_date`` it covers from, #233 A1; at most one
  ``pending`` series offer per series), ``status``
  (``pending`` → ``accepted`` | ``declined`` | ``expired`` |
  ``withdrawn``), ``offered_at`` / ``offered_by_user_id``, ``respond_by``
  (after ``offered_at``), ``responded_at`` (set exactly when no longer
  ``pending``) and an optional ``decline_note`` (only when ``declined``,
  at most 500 characters). ``Audited``, versioned.
- ``assignment_offer_sessions`` (#52 A2): the sessions an offer covers, one
  row per offer and session (a session-scope offer has exactly one).
  ``Audited``; not versioned (the offer's ``version`` covers them).

The rules (the "``offered`` iff exactly one pending offer" invariant and the
lock order) are in :mod:`~app.modules.scheduling.offers`.

Groups and cohorts add their own columns and tables (#56, #65).
"""

import uuid
from datetime import date, datetime, time
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy import column as sql_column
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin
from app.modules.scheduling.conflicts import CODE_ORDER, SOFT
from app.modules.scheduling.states import (
    ROOM_OCCUPYING_STATES,
    TUTOR_OCCUPYING_STATES,
    SessionKind,
    SessionState,
)

CANCELLATION_REASON_MAX_LENGTH: Final = 1000
ACTUAL_MINUTES_MAX: Final = 1440

TUTOR_OVERLAP_CONSTRAINT: Final = "ex_class_sessions_tutor_overlap"
ROOM_OVERLAP_CONSTRAINT: Final = "ex_class_sessions_room_overlap"
PARTICIPANT_UNIQUE: Final = "uq_class_session_participants_session_student"
SERIES_ORIGINAL_START_UNIQUE: Final = "uq_class_sessions_series_original_start"
SERIES_PARTICIPANT_UNIQUE: Final = "uq_class_series_participants_series_student"
CONFLICT_CODE_MAX_LENGTH: Final = 40
SERIES_DURATION_MIN: Final = 15
SERIES_DURATION_MAX: Final = 480
OVERRIDES_IMMUTABLE_TRIGGER: Final = "class_session_overrides_immutable"
OVERRIDE_REASON_MAX_LENGTH: Final = 1000
SOFT_CODES_IN_ORDER: Final = tuple(code for code in CODE_ORDER if code in SOFT)
"""The seven soft codes in rule order: the override ``codes`` CHECK."""

SESSION_RANGE_SQL: Final = "tstzrange(starts_at, ends_at, '[)')"


def _quoted(values: frozenset[str] | tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


def _occupying(column: str, states: frozenset[SessionState]) -> ColumnElement[bool]:
    """``<column> IS NOT NULL AND state IN (<states>)``: an exclusion
    predicate, built from the occupying set (rendered with literal values in
    the DDL)."""
    return and_(
        sql_column(column).is_not(None),
        sql_column("state").in_(sorted(state.value for state in states)),
    )


_KINDS: Final = tuple(kind.value for kind in SessionKind)
_STATES: Final = tuple(state.value for state in SessionState)


class ClassSession(Audited, VersionedMixin, Model):
    """One scheduled class (ADR-0005). See the module docstring."""

    __tablename__ = "class_sessions"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_quoted(_KINDS)})", name="kind_valid"),
        CheckConstraint(f"state IN ({_quoted(_STATES)})", name="state_valid"),
        CheckConstraint("starts_at < ends_at", name="starts_before_ends"),
        CheckConstraint(
            "mod(EXTRACT(EPOCH FROM starts_at), 60) = 0"
            " AND mod(EXTRACT(EPOCH FROM ends_at), 60) = 0",
            name="whole_minutes",
        ),
        CheckConstraint(
            "ends_at - starts_at BETWEEN interval '15 minutes'"
            " AND interval '480 minutes'",
            name="duration_range",
        ),
        CheckConstraint(
            "scheduled_minutes * interval '1 minute' = ends_at - starts_at",
            name="scheduled_minutes_match",
        ),
        CheckConstraint(
            f"actual_minutes BETWEEN 0 AND {ACTUAL_MINUTES_MAX}",
            name="actual_minutes_range",
        ),
        CheckConstraint(
            "state <> 'unassigned' OR tutor_user_id IS NULL",
            name="unassigned_has_no_tutor",
        ),
        CheckConstraint(
            f"state NOT IN ({_quoted(TUTOR_OCCUPYING_STATES)})"
            " OR tutor_user_id IS NOT NULL",
            name="assigned_has_tutor",
        ),
        CheckConstraint(
            "(cancelled_at IS NULL) = (cancellation_reason IS NULL)"
            " AND (state = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="cancellation_iff_cancelled",
        ),
        CheckConstraint(
            "cancellation_reason = btrim(cancellation_reason)"
            " AND cancellation_reason <> ''",
            name="cancellation_reason_trimmed",
        ),
        ExcludeConstraint(
            (text("tutor_user_id"), "="),
            (text(SESSION_RANGE_SQL), "&&"),
            name=TUTOR_OVERLAP_CONSTRAINT,
            using="gist",
            where=_occupying("tutor_user_id", TUTOR_OCCUPYING_STATES),
        ),
        ExcludeConstraint(
            (text("classroom_id"), "="),
            (text(SESSION_RANGE_SQL), "&&"),
            name=ROOM_OVERLAP_CONSTRAINT,
            using="gist",
            where=_occupying("classroom_id", ROOM_OCCUPYING_STATES),
        ),
        CheckConstraint(
            "(series_id IS NULL) = (original_start IS NULL)",
            name="series_original_start_together",
        ),
        CheckConstraint(
            "series_id IS NOT NULL OR NOT is_exception",
            name="exception_needs_series",
        ),
        UniqueConstraint(
            "series_id", "original_start", name=SERIES_ORIGINAL_START_UNIQUE
        ),
    )

    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), index=True
    )
    tutor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    classroom_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classrooms.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scheduled_minutes: Mapped[int] = mapped_column(SmallInteger)
    actual_minutes: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(
        String(CANCELLATION_REASON_MAX_LENGTH), nullable=True
    )
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_series.id", ondelete="RESTRICT"),
        nullable=True,
    )
    original_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_exception: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    conflict_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(CONFLICT_CODE_MAX_LENGTH)),
        server_default=text("'{}'"),
        default=list,
    )


class ClassSessionParticipant(Audited, Model):
    """A student of a class session. Not versioned: the session's
    ``version`` covers its participants."""

    __tablename__ = "class_session_participants"
    __table_args__ = (
        # Its leading column also serves the per-session lookups.
        UniqueConstraint("class_session_id", "student_id", name=PARTICIPANT_UNIQUE),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("class_sessions.id", ondelete="RESTRICT")
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )


class ClassSessionOverride(Model):
    """One override of a session's soft conflicts (#49 D1). Append-only (a
    trigger rejects ``UPDATE`` and ``DELETE``); not audited, the
    ``scheduling.session_override`` / ``scheduling.series_override`` event is
    its record. ``class_series_id`` (#236 D1) is **context**, not a second
    subject: the series a series write (or a ``conflict``-row override)
    targeted; ``NULL`` for a one-off session's override."""

    __tablename__ = "class_session_overrides"
    __table_args__ = (
        CheckConstraint(
            "cardinality(codes) > 0 AND codes <@ ARRAY[{}]::text[]".format(
                ", ".join(f"'{code}'" for code in SOFT_CODES_IN_ORDER)
            ),
            name="codes_soft",
        ),
        CheckConstraint(
            f"char_length(btrim(reason)) BETWEEN 1 AND {OVERRIDE_REASON_MAX_LENGTH}",
            name="reason_length",
        ),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    class_series_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_series.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    codes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )


SERIES_ASSIGNMENT_PENDING: Final = "pending"
SERIES_ASSIGNMENT_ACCEPTED: Final = "accepted"
SERIES_ASSIGNMENT_STATUSES: Final = (
    SERIES_ASSIGNMENT_PENDING,
    SERIES_ASSIGNMENT_ACCEPTED,
)
"""``class_series.assignment_status`` (#233 A2): ``NULL`` exactly when the
series has no tutor."""


class ClassSeries(Audited, VersionedMixin, Model):
    """A recurring series (#53 B2; ADR-0005 D4). See the module docstring."""

    __tablename__ = "class_series"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_quoted(_KINDS)})", name="kind_valid"),
        CheckConstraint(
            "EXTRACT(SECOND FROM start_time) = 0", name="start_time_whole_minutes"
        ),
        CheckConstraint(
            f"duration_minutes BETWEEN {SERIES_DURATION_MIN} AND {SERIES_DURATION_MAX}",
            name="duration_range",
        ),
        CheckConstraint(
            "cardinality(weekdays) >= 1 AND weekdays <@ '{1,2,3,4,5,6,7}'::smallint[]",
            name="weekdays_valid",
        ),
        CheckConstraint('"interval" BETWEEN 1 AND 4', name="interval_range"),
        CheckConstraint('"count" BETWEEN 1 AND 200', name="count_range"),
        CheckConstraint('until_on IS NULL OR "count" IS NULL', name="until_or_count"),
        CheckConstraint(
            f"assignment_status IN ({_quoted(SERIES_ASSIGNMENT_STATUSES)})",
            name="assignment_status_valid",
        ),
        CheckConstraint(
            "(tutor_user_id IS NULL) = (assignment_status IS NULL)",
            name="assignment_iff_tutor",
        ),
        CheckConstraint(
            "(assignment_status IS NULL) = (assignment_from IS NULL)",
            name="assignment_from_iff_status",
        ),
        CheckConstraint("split_from_series_id <> id", name="split_from_not_self"),
    )

    kind: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), index=True
    )
    tutor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    assignment_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    assignment_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    classroom_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classrooms.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    starts_on: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time] = mapped_column(Time)
    duration_minutes: Mapped[int] = mapped_column(SmallInteger)
    time_zone: Mapped[str] = mapped_column(String(64))
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))
    interval: Mapped[int] = mapped_column(SmallInteger)
    until_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    rrule: Mapped[str] = mapped_column(Text)
    last_occurrence_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    split_from_series_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_series.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


class ClassSeriesParticipant(Audited, Model):
    """A student of a class series. Not versioned: the series ``version``
    covers its participants."""

    __tablename__ = "class_series_participants"
    __table_args__ = (
        UniqueConstraint(
            "class_series_id", "student_id", name=SERIES_PARTICIPANT_UNIQUE
        ),
    )

    class_series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("class_series.id", ondelete="RESTRICT")
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )


# --- Assignment offers (#52 A) ---------------------------------------------------

OFFER_SCOPE_SESSION: Final = "session"
OFFER_SCOPE_SERIES: Final = "series"
OFFER_SCOPES: Final = (OFFER_SCOPE_SESSION, OFFER_SCOPE_SERIES)
"""The offer scopes (#52 ``session``; #233 ``series``)."""

OFFER_PENDING: Final = "pending"
OFFER_ACCEPTED: Final = "accepted"
OFFER_DECLINED: Final = "declined"
OFFER_EXPIRED: Final = "expired"
OFFER_WITHDRAWN: Final = "withdrawn"
OFFER_STATUSES: Final = (
    OFFER_PENDING,
    OFFER_ACCEPTED,
    OFFER_DECLINED,
    OFFER_EXPIRED,
    OFFER_WITHDRAWN,
)
DECLINE_NOTE_MAX_LENGTH: Final = 500
OFFER_SESSION_UNIQUE: Final = "uq_assignment_offer_sessions_offer_session"


class AssignmentOffer(Audited, VersionedMixin, Model):
    """An offer of sessions to a tutor (ADR-0005 D3; #52 A1). See the module
    docstring."""

    __tablename__ = "assignment_offers"
    __table_args__ = (
        CheckConstraint(f"scope IN ({_quoted(OFFER_SCOPES)})", name="scope_valid"),
        CheckConstraint(f"status IN ({_quoted(OFFER_STATUSES)})", name="status_valid"),
        CheckConstraint("respond_by > offered_at", name="respond_by_after_offered"),
        CheckConstraint(
            "(status = 'pending') = (responded_at IS NULL)",
            name="responded_iff_answered",
        ),
        CheckConstraint(
            "decline_note IS NULL OR status = 'declined'",
            name="decline_note_only_declined",
        ),
        CheckConstraint(
            "(scope = 'series') = (class_series_id IS NOT NULL)",
            name="series_iff_series_id",
        ),
        CheckConstraint(
            "(scope = 'series') = (from_date IS NOT NULL)",
            name="series_iff_from_date",
        ),
        Index("ix_assignment_offers_tutor_user_id_status", "tutor_user_id", "status"),
        Index(
            "ix_assignment_offers_pending_respond_by",
            "respond_by",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "uq_assignment_offers_pending_series",
            "class_series_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    scope: Mapped[str] = mapped_column(String(20))
    class_series_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_series.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    from_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(
        String(20), default=OFFER_PENDING, server_default=text("'pending'")
    )
    offered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    offered_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    respond_by: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decline_note: Mapped[str | None] = mapped_column(
        String(DECLINE_NOTE_MAX_LENGTH), nullable=True
    )


class AssignmentOfferSession(Audited, Model):
    """A session an offer covers (#52 A2). Not versioned: the offer's
    ``version`` covers its sessions."""

    __tablename__ = "assignment_offer_sessions"
    __table_args__ = (
        # Its leading column also serves the per-offer lookups.
        UniqueConstraint("offer_id", "class_session_id", name=OFFER_SESSION_UNIQUE),
    )

    offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assignment_offers.id", ondelete="RESTRICT")
    )
    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
