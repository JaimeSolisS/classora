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

- ``assignment_offers.kind`` (#55 A1): ``assignment`` (the default) or
  ``substitution``; a substitution offer is always session scope.
- ``class_session_substitutions`` (#55 A2): one row per ``substitution``
  offer (``UNIQUE offer_id``): the original tutor and the substitute (never
  the same), the trimmed reason, the requester and the slot snapshot.
  ``Audited``; append-only. The rules are in
  :mod:`~app.modules.scheduling.substitutions`.

- ``class_groups`` (#56 A2; ISSUE-G2 naming): an ongoing tutoring group:
  ``name`` (trimmed, 1-120, unique case-insensitively), ``subject_id`` and
  ``max_students`` (1-500). Its tutor, classroom and timing live on its
  ``group`` series (``class_series.group_id``), so #54 splits and #55
  substitutions keep one source of truth. ``Audited``, versioned.
- ``group_enrollments`` (#56 A5): one row per enrollment **period** of a
  student in a group (``enrolled_at`` / ``enrolled_by_user_id``, then a
  write-once ``withdrawn_at`` / ``withdrawn_by_user_id`` /
  ``withdrawal_reason``). At most one active (not withdrawn) row per group
  and student (``uq_group_enrollments_active``). The trigger
  :data:`GROUP_ENROLLMENTS_WRITE_ONCE_TRIGGER` rejects every ``DELETE`` and
  any ``UPDATE`` other than the one-time withdrawal (SQLSTATE 42501): the
  periods are the billing record M6-07 charges from (spec §6).
  ``Audited``, versioned.
- ``class_series.group_id`` / ``class_sessions.group_id`` (#56 A3, A4): set
  exactly for ``kind = 'group'``; an occurrence copies it from its series.
- ``class_series.cohort_id`` / ``class_sessions.cohort_id`` (#65 A3, A4):
  the ``course_cohorts`` row (``courses`` module) of a ``course`` series or
  occurrence, set exactly for ``kind = 'course'``; an occurrence copies it
  from its series. A cohort may own several series over time (a #54
  split), so it is not unique.
- ``class_session_overrides.group_enrollment_id`` (#56 A6): an override of
  an enrollment's ``group_capacity_exceeded``. Exactly one of
  ``class_session_id`` and ``group_enrollment_id`` is set
  (``class_series_id`` is context, never a subject, #236).

- ``session_requests`` (#59 A1; spec §1 "request, not booking", W2.2): a
  guardian's or a student's request for one individual session of a
  subject: ``student_id``, ``subject_id``, ``requested_by_user_id``, the
  whole-minute instants ``starts_at`` / ``ends_at`` (``ends_at = starts_at +
  duration_minutes``), ``duration_minutes`` (30, 45, 60, 90 or 120), an
  optional trimmed ``note`` (at most 500 characters), ``status``
  (``pending`` → ``confirmed`` | ``rejected`` by #60, or ``withdrawn`` by
  the requester) and ``resolved_at`` (set exactly when not ``pending``). A
  request **holds no time**: nothing else reads it as occupancy.
  ``ex_session_requests_student_pending_overlap`` (``EXCLUDE USING gist``,
  partial on ``pending``) backs the "no two overlapping pending requests
  per student" rule (back-to-back is allowed). ``Audited``, versioned.
  #60 A1/A2: ``class_session_id`` (``class_sessions``, ``RESTRICT``; one
  request per session, ``uq_session_requests_class_session_id`` partial on
  ``NOT NULL``) is set exactly when ``confirmed``
  (``ck_session_requests_confirmed_iff_session``), and ``rejection_reason``
  (at most 1000 characters, shown to the family) exactly when ``rejected``
  (``ck_session_requests_rejected_iff_reason``).

- ``session_change_requests`` (#62 A): a family's or a tutor's request to
  cancel or reschedule one ``confirmed`` individual session
  (:class:`SessionChangeRequest`). ``kind`` ``cancellation`` |
  ``reschedule``; ``initiated_by`` ``family`` (with ``student_id``, the
  participant the requester acts for) | ``tutor`` (without it); the
  session's times at request time; a reschedule's proposed instants; the
  **request-time outcome** of the scheduling policy (``policy_id``,
  ``classification``, ``makeup_allowed``, ``makeup_until``: never
  recomputed); ``status`` ``pending`` | ``approved`` | ``rejected`` |
  ``withdrawn``; and the decision (``exception``, ``decision_reason``,
  ``decided_by_user_id``, ``resolved_at``) with the **effective outcome**
  (``final_*``, set exactly when ``approved``). CHECKs tie each group to
  the status; ``uq_session_change_requests_pending_session`` (partial
  unique, ``WHERE status = 'pending'``) allows one pending request per
  session. ``Audited``, versioned.

- ``class_session_reassignment_flags`` (M4-18 #63 A2, E3): a session that
  needs another tutor because its tutor's availability changed
  (:class:`ClassSessionReassignmentFlag`): the session, the tutor whose
  availability changed, ``reason`` (``availability_change``), ``source_id``
  (the change request in ``staff``; no foreign key) and the approver.
  **Append-only**; a flag is *open* by a read-time rule
  (:mod:`~app.modules.scheduling.reassignment`), never closed by a write.
  ``uq_class_session_reassignment_flags_source`` flags a session once per
  tutor and change. ``Audited`` (``create`` only).

Cohorts add their own columns and tables (#65).
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
    func,
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
GROUP_ENROLLMENTS_WRITE_ONCE_TRIGGER: Final = "group_enrollments_write_once"
GROUP_NAME_UNIQUE: Final = "uq_class_groups_name"
GROUP_ENROLLMENT_ACTIVE_UNIQUE: Final = "uq_group_enrollments_active"
OVERRIDE_COHORT_ENROLLMENT_FK: Final = "fk_class_session_overrides_cohort_enrollment_id"
"""The foreign key of ``class_session_overrides.cohort_enrollment_id`` (#66
A3), named explicitly: the convention's name would pass PostgreSQL's
63-character limit."""
GROUP_NAME_MAX_LENGTH: Final = 120
SESSION_REQUEST_PENDING_OVERLAP_CONSTRAINT: Final = (
    "ex_session_requests_student_pending_overlap"
)
SESSION_REQUEST_NOTE_MAX_LENGTH: Final = 500
SESSION_REQUEST_DURATIONS: Final = (30, 45, 60, 90, 120)
SESSION_REQUEST_REJECTION_REASON_MAX_LENGTH: Final = 1000
"""The #40 ``REASON_MAX_LENGTH`` (#60 A1)."""
SESSION_REQUEST_PENDING: Final = "pending"
SESSION_REQUEST_CONFIRMED: Final = "confirmed"
SESSION_REQUEST_REJECTED: Final = "rejected"
SESSION_REQUEST_WITHDRAWN: Final = "withdrawn"
SESSION_REQUEST_STATUSES: Final = (
    SESSION_REQUEST_PENDING,
    SESSION_REQUEST_CONFIRMED,
    SESSION_REQUEST_REJECTED,
    SESSION_REQUEST_WITHDRAWN,
)
CHANGE_REQUEST_PENDING_UNIQUE: Final = "uq_session_change_requests_pending_session"
CHANGE_REQUEST_NOTE_MAX_LENGTH: Final = 500
CHANGE_REQUEST_REASON_MAX_LENGTH: Final = 1000
CHANGE_REQUEST_CANCELLATION: Final = "cancellation"
CHANGE_REQUEST_RESCHEDULE: Final = "reschedule"
CHANGE_REQUEST_KINDS: Final = (CHANGE_REQUEST_CANCELLATION, CHANGE_REQUEST_RESCHEDULE)
CHANGE_REQUEST_FAMILY: Final = "family"
CHANGE_REQUEST_TUTOR: Final = "tutor"
CHANGE_REQUEST_INITIATORS: Final = (CHANGE_REQUEST_FAMILY, CHANGE_REQUEST_TUTOR)
CHANGE_REQUEST_PENDING: Final = "pending"
CHANGE_REQUEST_APPROVED: Final = "approved"
CHANGE_REQUEST_REJECTED: Final = "rejected"
CHANGE_REQUEST_WITHDRAWN: Final = "withdrawn"
CHANGE_REQUEST_STATUSES: Final = (
    CHANGE_REQUEST_PENDING,
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_REJECTED,
    CHANGE_REQUEST_WITHDRAWN,
)
CHANGE_REQUEST_CLASSIFICATIONS: Final = ("on_time", "late", "no_show", "excused")
GROUP_MAX_STUDENTS_MAX: Final = 500
WITHDRAWAL_REASON_MAX_LENGTH: Final = 1000
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
        CheckConstraint(
            "(kind = 'group') = (group_id IS NOT NULL)", name="group_iff_kind"
        ),
        Index("ix_class_sessions_group_id_starts_at", "group_id", "starts_at"),
        CheckConstraint(
            "(kind = 'course') = (cohort_id IS NOT NULL)", name="cohort_iff_kind"
        ),
        Index("ix_class_sessions_cohort_id_starts_at", "cohort_id", "starts_at"),
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
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_groups.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cohort_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_cohorts.id", ondelete="RESTRICT"),
        nullable=True,
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
    ``scheduling.session_override`` / ``scheduling.series_override`` /
    ``scheduling.enrollment_override`` event is its record. Its **subject**
    is exactly one of ``class_session_id``, ``group_enrollment_id`` (#56
    A6: a group enrollment's ``group_capacity_exceeded``) and
    ``cohort_enrollment_id`` (#66 A3: a cohort enrollment's, the ``courses``
    row; ADR-0002 allows the cross-module key, the model isn't imported).
    ``class_series_id`` (#236 D1) is **context**, not a subject: the series
    a series write (or a ``conflict``-row override) targeted; ``NULL`` for a
    one-off session's or an enrollment's override."""

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
        CheckConstraint(
            "num_nonnulls(class_session_id, group_enrollment_id,"
            " cohort_enrollment_id) = 1",
            name="one_subject",
        ),
    )

    class_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    group_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("group_enrollments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    cohort_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # An explicit name: the convention's would pass PostgreSQL's
        # 63-character limit.
        ForeignKey(
            "cohort_enrollments.id",
            ondelete="RESTRICT",
            name=OVERRIDE_COHORT_ENROLLMENT_FK,
        ),
        nullable=True,
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
        CheckConstraint(
            "(kind = 'group') = (group_id IS NOT NULL)", name="group_iff_kind"
        ),
        CheckConstraint(
            "(kind = 'course') = (cohort_id IS NOT NULL)", name="cohort_iff_kind"
        ),
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
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_groups.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    cohort_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_cohorts.id", ondelete="RESTRICT"),
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

OFFER_KIND_ASSIGNMENT: Final = "assignment"
OFFER_KIND_SUBSTITUTION: Final = "substitution"
OFFER_KINDS: Final = (OFFER_KIND_ASSIGNMENT, OFFER_KIND_SUBSTITUTION)
"""The offer kinds (#55 A1): an ``assignment`` gives sessions a tutor
(#52, #233); a ``substitution`` offers one ``confirmed`` session to another
tutor while it stays with its original tutor until accepted (ADR-0005 D3
amendment)."""
SUBSTITUTION_REASON_MAX_LENGTH: Final = 1000


class AssignmentOffer(Audited, VersionedMixin, Model):
    """An offer of sessions to a tutor (ADR-0005 D3; #52 A1). See the module
    docstring."""

    __tablename__ = "assignment_offers"
    __table_args__ = (
        CheckConstraint(f"scope IN ({_quoted(OFFER_SCOPES)})", name="scope_valid"),
        CheckConstraint(f"kind IN ({_quoted(OFFER_KINDS)})", name="kind_valid"),
        CheckConstraint(
            "kind = 'assignment' OR scope = 'session'",
            name="substitution_session_scope",
        ),
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
    kind: Mapped[str] = mapped_column(
        String(20),
        default=OFFER_KIND_ASSIGNMENT,
        server_default=text("'assignment'"),
    )
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


# --- Substitutions (#55 A2) --------------------------------------------------------


class ClassSessionSubstitution(Audited, Model):
    """One substitution of a ``confirmed`` session's tutor (#55 A2; spec §2
    "Substitution"): the ``substitution`` offer it was made with (its status
    is the offer's), the original tutor and the substitute, the reason, who
    asked, and the session's times when it was offered (the slot snapshot
    the accept re-checks). Not versioned and **append-only**: no code path
    updates or deletes a row."""

    __tablename__ = "class_session_substitutions"
    __table_args__ = (
        CheckConstraint(
            "original_tutor_user_id <> substitute_tutor_user_id",
            name="substitute_differs",
        ),
        CheckConstraint(
            "reason = btrim(reason) AND reason <> ''", name="reason_trimmed"
        ),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assignment_offers.id", ondelete="RESTRICT"),
        unique=True,
    )
    original_tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    substitute_tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(String(SUBSTITUTION_REASON_MAX_LENGTH))
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    slot_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slot_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# --- Reassignment flags (M4-18 #63 A2) ---------------------------------------------

REASSIGNMENT_AVAILABILITY_CHANGE: Final = "availability_change"
REASSIGNMENT_REASONS: Final = (REASSIGNMENT_AVAILABILITY_CHANGE,)
REASSIGNMENT_FLAG_SOURCE_UNIQUE: Final = "uq_class_session_reassignment_flags_source"


class ClassSessionReassignmentFlag(Audited, Model):
    """A session flagged for reassignment (#63 A2). Not versioned and
    **append-only**: no code path updates or deletes a row."""

    __tablename__ = "class_session_reassignment_flags"
    __table_args__ = (
        CheckConstraint(
            f"reason IN ({_quoted(REASSIGNMENT_REASONS)})", name="reason_valid"
        ),
        # Its leading column also serves the per-session lookups.
        UniqueConstraint(
            "class_session_id",
            "tutor_user_id",
            "source_id",
            name=REASSIGNMENT_FLAG_SOURCE_UNIQUE,
        ),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("class_sessions.id", ondelete="RESTRICT")
    )
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )


# --- Ongoing tutoring groups (#56 A) -----------------------------------------------


class ClassGroup(Audited, VersionedMixin, Model):
    """An ongoing tutoring group (#56 A2; spec §3). See the module
    docstring."""

    __tablename__ = "class_groups"
    __table_args__ = (
        CheckConstraint(
            f"name = btrim(name) AND char_length(name) BETWEEN 1"
            f" AND {GROUP_NAME_MAX_LENGTH}",
            name="name_trimmed",
        ),
        CheckConstraint(
            f"max_students BETWEEN 1 AND {GROUP_MAX_STUDENTS_MAX}",
            name="max_students_range",
        ),
        Index(GROUP_NAME_UNIQUE, func.lower(sql_column("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(GROUP_NAME_MAX_LENGTH))
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), index=True
    )
    max_students: Mapped[int] = mapped_column(SmallInteger)


class GroupEnrollment(Audited, VersionedMixin, Model):
    """One enrollment period of a student in a group (#56 A5). See the
    module docstring; the write-once trigger is created by the migration."""

    __tablename__ = "group_enrollments"
    __table_args__ = (
        CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= enrolled_at",
            name="withdrawn_after_enrolled",
        ),
        CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_by_user_id IS NULL)",
            name="withdrawn_together",
        ),
        CheckConstraint(
            "withdrawal_reason IS NULL OR (withdrawn_at IS NOT NULL"
            " AND withdrawal_reason = btrim(withdrawal_reason)"
            " AND withdrawal_reason <> '')",
            name="withdrawal_reason_valid",
        ),
        Index(
            GROUP_ENROLLMENT_ACTIVE_UNIQUE,
            "group_id",
            "student_id",
            unique=True,
            postgresql_where=text("withdrawn_at IS NULL"),
        ),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_groups.id", ondelete="RESTRICT"),
        index=True,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT"), index=True
    )
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enrolled_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    withdrawn_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(
        String(WITHDRAWAL_REASON_MAX_LENGTH), nullable=True
    )


# --- Session requests by families (#59 A) -------------------------------------------


class SessionRequest(Audited, VersionedMixin, Model):
    """A family's request for one individual session (#59 A1). See the
    module docstring; it holds no time."""

    __tablename__ = "session_requests"
    __table_args__ = (
        CheckConstraint(
            "mod(EXTRACT(EPOCH FROM starts_at), 60) = 0"
            " AND mod(EXTRACT(EPOCH FROM ends_at), 60) = 0",
            name="whole_minutes",
        ),
        CheckConstraint(
            "duration_minutes IN"
            f" ({', '.join(str(m) for m in SESSION_REQUEST_DURATIONS)})",
            name="duration_valid",
        ),
        CheckConstraint(
            "ends_at = starts_at + duration_minutes * interval '1 minute'",
            name="ends_after_duration",
        ),
        CheckConstraint(
            "note IS NULL OR (note = btrim(note) AND note <> '')",
            name="note_trimmed",
        ),
        CheckConstraint(
            f"status IN ({_quoted(SESSION_REQUEST_STATUSES)})", name="status_valid"
        ),
        CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="resolved_iff_not_pending",
        ),
        CheckConstraint(
            "(status = 'confirmed') = (class_session_id IS NOT NULL)",
            name="confirmed_iff_session",
        ),
        CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="rejected_iff_reason",
        ),
        ExcludeConstraint(
            (text("student_id"), "="),
            (text(SESSION_RANGE_SQL), "&&"),
            name=SESSION_REQUEST_PENDING_OVERLAP_CONSTRAINT,
            using="gist",
            where=sql_column("status") == SESSION_REQUEST_PENDING,
        ),
        Index(
            "ix_session_requests_student_id_created_at",
            "student_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_session_requests_requested_by_user_id_created_at",
            "requested_by_user_id",
            text("created_at DESC"),
        ),
        Index(
            "uq_session_requests_class_session_id",
            "class_session_id",
            unique=True,
            postgresql_where=text("class_session_id IS NOT NULL"),
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT")
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT")
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(
        String(SESSION_REQUEST_NOTE_MAX_LENGTH), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=SESSION_REQUEST_PENDING,
        server_default=text("'pending'"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    class_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """The session a confirmation created (#60 A1)."""
    rejection_reason: Mapped[str | None] = mapped_column(
        String(SESSION_REQUEST_REJECTION_REASON_MAX_LENGTH), nullable=True
    )
    """Why staff rejected it (#60 A1, E5); the family sees it."""


CHANGE_REQUEST_CHECKS: Final = {
    "kind_valid": f"kind IN ({_quoted(CHANGE_REQUEST_KINDS)})",
    "initiated_by_valid": f"initiated_by IN ({_quoted(CHANGE_REQUEST_INITIATORS)})",
    "student_iff_family": "(initiated_by = 'family') = (student_id IS NOT NULL)",
    "note_trimmed": "note IS NULL OR (note = btrim(note) AND note <> '')",
    "session_times_order": "session_ends_at > session_starts_at",
    "proposal_both_or_none": (
        "(proposed_starts_at IS NULL) = (proposed_ends_at IS NULL)"
    ),
    "proposal_iff_reschedule": (
        "(kind = 'reschedule') = (proposed_starts_at IS NOT NULL)"
    ),
    "proposal_order": (
        "proposed_starts_at IS NULL OR proposed_ends_at > proposed_starts_at"
    ),
    "proposal_whole_minutes": (
        "proposed_starts_at IS NULL"
        " OR (mod(EXTRACT(EPOCH FROM proposed_starts_at), 60) = 0"
        " AND mod(EXTRACT(EPOCH FROM proposed_ends_at), 60) = 0)"
    ),
    "classification_valid": (
        f"classification IN ({_quoted(CHANGE_REQUEST_CLASSIFICATIONS)})"
    ),
    "makeup_iff_until": "makeup_allowed = (makeup_until IS NOT NULL)",
    "no_makeup_on_reschedule": "kind = 'cancellation' OR NOT makeup_allowed",
    "status_valid": f"status IN ({_quoted(CHANGE_REQUEST_STATUSES)})",
    "resolved_iff_not_pending": "(status = 'pending') = (resolved_at IS NULL)",
    "final_iff_approved": "(status = 'approved') = (final_classification IS NOT NULL)",
    "final_classification_valid": (
        "final_classification IS NULL OR final_classification IN"
        f" ({_quoted(CHANGE_REQUEST_CLASSIFICATIONS)})"
    ),
    "final_makeup_with_final": (
        "(final_classification IS NULL) = (final_makeup_allowed IS NULL)"
    ),
    "final_makeup_iff_until": (
        "(final_makeup_until IS NOT NULL) = (final_makeup_allowed IS TRUE)"
    ),
    "no_final_makeup_on_reschedule": (
        "kind = 'cancellation' OR final_makeup_allowed IS NOT TRUE"
    ),
    "rejected_needs_reason": "status <> 'rejected' OR decision_reason IS NOT NULL",
    "reason_only_when_decided": (
        "decision_reason IS NULL OR status IN ('approved', 'rejected')"
    ),
    "reason_trimmed": (
        "decision_reason IS NULL"
        " OR (decision_reason = btrim(decision_reason) AND decision_reason <> '')"
    ),
    "exception_rule": (
        "NOT exception OR (status = 'approved' AND decision_reason IS NOT NULL"
        " AND final_classification = 'excused')"
    ),
    "decided_by_iff_decided": (
        "(decided_by_user_id IS NOT NULL) = (status IN ('approved', 'rejected'))"
    ),
}
"""The CHECKs of ``session_change_requests`` (#62 A1, A2), by name suffix
(``ck_session_change_requests_<name>``); the migration uses the same
text."""


class SessionChangeRequest(Audited, VersionedMixin, Model):
    """A family's or a tutor's request to cancel or reschedule one session
    (#62 A1; see the module docstring)."""

    __tablename__ = "session_change_requests"
    __table_args__ = (
        *(
            CheckConstraint(condition, name=name)
            for name, condition in CHANGE_REQUEST_CHECKS.items()
        ),
        Index(
            CHANGE_REQUEST_PENDING_UNIQUE,
            "class_session_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_session_change_requests_student_id_created_at",
            "student_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_session_change_requests_requested_by_user_id_created_at",
            "requested_by_user_id",
            text("created_at DESC"),
        ),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20))
    initiated_by: Mapped[str] = mapped_column(String(10))
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    student_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """The participant a family requester acts for; ``NULL`` for a tutor."""
    note: Mapped[str | None] = mapped_column(
        String(CHANGE_REQUEST_NOTE_MAX_LENGTH), nullable=True
    )
    session_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    """The session's times at request time (the classification's basis)."""
    proposed_starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    proposed_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduling_policies.id", ondelete="RESTRICT"),
    )
    classification: Mapped[str] = mapped_column(String(10))
    makeup_allowed: Mapped[bool] = mapped_column(Boolean)
    makeup_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=CHANGE_REQUEST_PENDING,
        server_default=text("'pending'"),
    )
    exception: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    decision_reason: Mapped[str | None] = mapped_column(
        String(CHANGE_REQUEST_REASON_MAX_LENGTH), nullable=True
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_classification: Mapped[str | None] = mapped_column(String(10), nullable=True)
    final_makeup_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    final_makeup_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
