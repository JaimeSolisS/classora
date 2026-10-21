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

Series, groups, cohorts, offers and overrides add their own columns and
tables (#53, #56, #65, #52, #49).
"""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy import column as sql_column
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin
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
