"""Staff tables (#43; spec §4).

- ``tutor_profiles``: the professional experience of one tutor, at most one
  row per user (``uq_tutor_profiles_user_id``). The row is created lazily by
  the first profile edit (#43 A3): a tutor without one has an empty profile
  at ``version`` 0. Losing the ``tutor`` role hides the profile but keeps the
  row. ``Audited`` with nothing excluded, versioned.
- ``tutor_qualifications`` (#44): a tutor's request to teach one subject (the
  level follows from the subject, #32). ``status`` is ``pending`` →
  ``approved`` | ``rejected`` | ``cancelled``; ``decided_at`` is set exactly
  for the three final states and ``rejection_reason`` exactly for
  ``rejected``. The partial unique index
  ``uq_tutor_qualifications_active`` allows one active (``pending`` or
  ``approved``) qualification per tutor and subject; rejected and cancelled
  rows don't block a new request. The row keeps its own status, mirrored
  with its ``staff.qualification`` approval request in one transaction, so
  eligibility never reads ``approval_requests`` (ADR-0002). ``Audited``,
  versioned.
- ``tutor_availability_settings`` (#45): one row per tutor at most
  (``uq_tutor_availability_settings_user_id``), created lazily by the first
  weekly-availability change (``version`` 0 before). Its ``version`` covers
  the tutor's weekly windows and, from #197, the teaching limits and the
  preferred classrooms. #197 adds the optional teaching limits (minutes;
  ``NULL`` = no limit or no break): ``daily_limit_minutes`` 15-1440,
  ``weekly_limit_minutes`` 15-10080 and never below the daily one,
  ``required_break_minutes`` 0-240. ``Audited``, versioned.
- ``tutor_availability_windows`` (#45): the tutor's recurring weekly
  windows, the #39 shape: ISO weekday and ``[starts_at, ends_at)`` in local
  wall-clock time, whole minutes, never across midnight; windows of one
  weekday never overlap (``ex_tutor_windows_no_overlap``), touching is
  allowed. ``Audited``.
- ``tutor_availability_exceptions`` (#45): an inclusive range of local dates
  on which the tutor is ``unavailable`` (the whole day, or hours) or
  ``available`` (always hours), with an optional note. Overlaps are allowed.
  Created and deleted, never edited. ``Audited``.
- ``tutor_preferred_classrooms`` (#197): the classrooms a tutor prefers
  (unique per tutor and room, at most 10 per tutor, enforced by the
  service). They guide scheduling (M4-05, M4-06) and never restrict it.
  Created and deleted, never edited; the settings row's ``version`` covers
  them. ``Audited``.
"""

import uuid
from datetime import date, datetime, time
from typing import Final

from sqlalchemy import (
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
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

EXPERIENCE_MAX_LENGTH = 4000

# The unique constraint on user_id (NAMING_CONVENTION): a concurrent first
# edit that loses the race is 409 version_conflict.
USER_ID_UNIQUE = "uq_tutor_profiles_user_id"


class TutorProfile(Audited, VersionedMixin, Model):
    __tablename__ = "tutor_profiles"
    __table_args__ = (
        CheckConstraint(
            f"char_length(professional_experience) <= {EXPERIENCE_MAX_LENGTH}",
            name="professional_experience_length",
        ),
        CheckConstraint(
            "professional_experience = btrim(professional_experience)",
            name="professional_experience_trimmed",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    professional_experience: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Tutor qualifications (#44) ------------------------------------------------------

PENDING: Final = "pending"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"
CANCELLED: Final = "cancelled"

QUALIFICATION_STATUSES: Final = (PENDING, APPROVED, REJECTED, CANCELLED)
ACTIVE_STATUSES: Final = (PENDING, APPROVED)
"""The statuses that block another request for the same tutor and subject."""

REJECTION_REASON_MAX_LENGTH: Final = 1000

# The "no duplicate active qualification" index (#44 A1): a concurrent
# duplicate request that loses the race is 409 qualification_exists.
ACTIVE_UNIQUE: Final = "uq_tutor_qualifications_active"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class TutorQualification(Audited, VersionedMixin, Model):
    __tablename__ = "tutor_qualifications"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_quoted(QUALIFICATION_STATUSES)})", name="status_valid"
        ),
        CheckConstraint(
            "(status IN ('approved', 'rejected', 'cancelled'))"
            " = (decided_at IS NOT NULL)",
            name="decided_iff_final",
        ),
        CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="reason_iff_rejected",
        ),
        Index(
            ACTIVE_UNIQUE,
            "tutor_user_id",
            "subject_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
        ),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(REJECTION_REASON_MAX_LENGTH), nullable=True
    )


# --- Tutor availability (#45) --------------------------------------------------------

FIRST_WEEKDAY: Final = 1
LAST_WEEKDAY: Final = 7
AVAILABILITY_WINDOWS_MAX: Final = 42
"""At most this many weekly windows per tutor (six per weekday)."""
EXCEPTION_MAX_SPAN_DAYS: Final = 366
EXCEPTION_NOTE_MAX_LENGTH: Final = 200

UNAVAILABLE: Final = "unavailable"
AVAILABLE: Final = "available"
EXCEPTION_KINDS: Final = (UNAVAILABLE, AVAILABLE)

# A concurrent first weekly change that loses the race (the unique user_id)
# is 409 version_conflict.
SETTINGS_USER_ID_UNIQUE: Final = "uq_tutor_availability_settings_user_id"
TUTOR_WINDOW_OVERLAP_CONSTRAINT: Final = "ex_tutor_windows_no_overlap"
"""The exclusion constraint (``btree_gist``, revision 0021): windows of one
tutor on one weekday never overlap; touching windows are allowed."""

# A window's times as a range on a fixed Monday, so GiST can compare them.
WINDOW_RANGE_SQL: Final = (
    "tsrange(DATE '2000-01-03' + starts_at, DATE '2000-01-03' + ends_at, '[)')"
)


DAILY_LIMIT_MIN: Final = 15
DAILY_LIMIT_MAX: Final = 1440
"""A day's teaching limit in minutes (24 h)."""
WEEKLY_LIMIT_MIN: Final = 15
WEEKLY_LIMIT_MAX: Final = 10080
"""A week's teaching limit in minutes (7 x 24 h)."""
BREAK_MIN: Final = 0
BREAK_MAX: Final = 240
"""The required break between two sessions, in minutes (4 h)."""
PREFERRED_CLASSROOMS_MAX: Final = 10
PREFERRED_CLASSROOM_UNIQUE: Final = "uq_tutor_preferred_classrooms_tutor_classroom"


class TutorAvailabilitySettings(Audited, VersionedMixin, Model):
    """The version anchor of a tutor's weekly windows, teaching limits and
    preferred classrooms: the weekly ``PUT`` and the settings ``PATCH`` take
    and bump it. Created by the first change (``version`` 0 = no row). The
    limits (#197) are minutes; ``NULL`` is no limit (no break)."""

    __tablename__ = "tutor_availability_settings"
    __table_args__ = (
        CheckConstraint(
            f"daily_limit_minutes BETWEEN {DAILY_LIMIT_MIN} AND {DAILY_LIMIT_MAX}",
            name="daily_limit_range",
        ),
        CheckConstraint(
            f"weekly_limit_minutes BETWEEN {WEEKLY_LIMIT_MIN} AND {WEEKLY_LIMIT_MAX}",
            name="weekly_limit_range",
        ),
        CheckConstraint(
            "weekly_limit_minutes >= daily_limit_minutes",
            name="weekly_not_below_daily",
        ),
        CheckConstraint(
            f"required_break_minutes BETWEEN {BREAK_MIN} AND {BREAK_MAX}",
            name="required_break_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    daily_limit_minutes: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    weekly_limit_minutes: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )
    required_break_minutes: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )


class TutorAvailabilityWindow(Audited, Model):
    """One recurring weekly window in which the tutor is available: an ISO
    weekday (1 = Monday) and ``[starts_at, ends_at)`` in local wall-clock
    time, whole minutes, never across midnight. Not versioned: the settings
    row's ``version`` covers the windows."""

    __tablename__ = "tutor_availability_windows"
    __table_args__ = (
        CheckConstraint(
            f"weekday BETWEEN {FIRST_WEEKDAY} AND {LAST_WEEKDAY}", name="weekday_range"
        ),
        CheckConstraint("starts_at < ends_at", name="starts_before_ends"),
        CheckConstraint(
            "EXTRACT(SECOND FROM starts_at) = 0 AND EXTRACT(SECOND FROM ends_at) = 0",
            name="zero_seconds",
        ),
        ExcludeConstraint(
            (text("tutor_user_id"), "="),
            (text("weekday"), "="),
            (text(WINDOW_RANGE_SQL), "&&"),
            name=TUTOR_WINDOW_OVERLAP_CONSTRAINT,
            using="gist",
        ),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    weekday: Mapped[int] = mapped_column(SmallInteger)
    starts_at: Mapped[time] = mapped_column(Time(timezone=False))
    ends_at: Mapped[time] = mapped_column(Time(timezone=False))


class TutorAvailabilityException(Audited, Model):
    """An exception to the weekly windows on an inclusive range of local
    dates: ``unavailable`` (time off; the whole day when both times are
    null, otherwise those hours of each day) or ``available`` (extra hours
    of each day; always with hours). ``unavailable`` wins where both apply.
    Never edited: created and deleted."""

    __tablename__ = "tutor_availability_exceptions"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_quoted(EXCEPTION_KINDS)})", name="kind_valid"),
        CheckConstraint("starts_on <= ends_on", name="starts_before_ends"),
        CheckConstraint(
            f"ends_on - starts_on <= {EXCEPTION_MAX_SPAN_DAYS}", name="span_max"
        ),
        CheckConstraint(
            "(starts_at IS NULL AND ends_at IS NULL)"
            " OR (starts_at IS NOT NULL AND ends_at IS NOT NULL"
            " AND starts_at < ends_at)",
            name="hours_valid",
        ),
        CheckConstraint(
            "starts_at IS NULL OR (EXTRACT(SECOND FROM starts_at) = 0"
            " AND EXTRACT(SECOND FROM ends_at) = 0)",
            name="zero_seconds",
        ),
        CheckConstraint(
            "kind = 'unavailable' OR starts_at IS NOT NULL",
            name="available_has_hours",
        ),
        CheckConstraint("note = btrim(note)", name="note_trimmed"),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[time | None] = mapped_column(Time(timezone=False), nullable=True)
    ends_at: Mapped[time | None] = mapped_column(Time(timezone=False), nullable=True)
    note: Mapped[str | None] = mapped_column(
        String(EXCEPTION_NOTE_MAX_LENGTH), nullable=True
    )


class TutorPreferredClassroom(Audited, Model):
    """A classroom the tutor prefers (#197). Guides scheduling, never
    restricts it. A room deactivated later stays until the list is edited.
    Not versioned: the settings row's ``version`` covers the list."""

    __tablename__ = "tutor_preferred_classrooms"
    __table_args__ = (
        # Its leading column also serves the per-tutor lookups.
        UniqueConstraint(
            "tutor_user_id", "classroom_id", name=PREFERRED_CLASSROOM_UNIQUE
        ),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    classroom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classrooms.id", ondelete="RESTRICT"),
        index=True,
    )
