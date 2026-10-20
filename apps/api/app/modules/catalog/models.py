"""Catalog tables (spec §3, §4, §12; #32, #38).

- ``education_levels``: a level (Primaria, Secundaria, …) with a position
  (``sort_order``) for lists. ``seed_key`` is set only on the rows the
  ``catalog.education_levels`` reference seed created, so the seed recognizes
  them after a rename; it never appears in the API.
- ``subjects``: a subject taught at exactly one level. Its level never
  changes.

Names are free text, stored once and shown as stored in every language
(owner decision #32 E1 (a)). They are trimmed (CHECK ``name = btrim(name)``),
never blank, and unique ignoring case: level names across all levels, subject
names within their level. Inactive rows count, so a duplicate is never
created next to a deactivated record. Uniqueness does not fold accents.

Nothing is deleted: rows are deactivated (``is_active``) and can be
reactivated, so later records keep valid references (``ON DELETE RESTRICT``).
Both tables are :class:`~app.core.audit.Audited` with nothing excluded or
masked (#22) and versioned (optimistic concurrency, architecture §4.4).

#38 adds ``academic_periods`` and ``closures``: inclusive ``DATE`` ranges
(local calendar days, ADR-0004) with the same lifecycle (deactivate, never
delete), audit and versioning.

#39 adds ``classrooms`` (name, capacity, active or not) and their weekly
availability windows (``classroom_availability_windows``, local wall-clock
times, ADR-0004). A room without windows has no time restriction.
"""

import uuid
from datetime import date, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

NAME_MAX_LENGTH = 100
SORT_ORDER_MAX = 9999
SEED_KEY_PATTERN = "^[a-z][a-z_]*$"

# Explicit names of the expression indexes (NAMING_CONVENTION has no column
# name to use for them); the service maps a violation of either to
# 409 name_taken.
LEVEL_NAME_INDEX = "uq_education_levels_name_ci"
SUBJECT_NAME_INDEX = "uq_subjects_level_name_ci"


def _name_checks() -> tuple[CheckConstraint, CheckConstraint]:
    return (
        CheckConstraint("name = btrim(name)", name="name_trimmed"),
        CheckConstraint("name ~ '[^[:space:]]'", name="name_not_blank"),
    )


class EducationLevel(Audited, VersionedMixin, Model):
    __tablename__ = "education_levels"
    __table_args__ = (
        *_name_checks(),
        CheckConstraint(
            f"sort_order BETWEEN 0 AND {SORT_ORDER_MAX}", name="sort_order_range"
        ),
        CheckConstraint(f"seed_key ~ '{SEED_KEY_PATTERN}'", name="seed_key_format"),
        Index(LEVEL_NAME_INDEX, func.lower(text("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    sort_order: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    seed_key: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)


class Subject(Audited, VersionedMixin, Model):
    __tablename__ = "subjects"
    __table_args__ = (
        *_name_checks(),
        Index(
            SUBJECT_NAME_INDEX,
            text("education_level_id"),
            func.lower(text("name")),
            unique=True,
        ),
    )

    education_level_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("education_levels.id", ondelete="RESTRICT"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


# --- Academic periods and closures (#38) ---------------------------------------------

PERIOD_TYPES = ("school_year", "semester", "term")
CLOSURE_TYPES = ("holiday", "closure", "vacation")
PERIOD_MAX_SPAN_DAYS = 730
CLOSURE_MAX_SPAN_DAYS = 366
RANGE_TYPE_MAX_LENGTH = 20

PERIOD_NAME_INDEX = "uq_academic_periods_name_ci"
PERIOD_OVERLAP_CONSTRAINT = "ex_academic_periods_no_overlap"
"""The exclusion constraint (``btree_gist``): active periods of one type never
share a day. The service maps a violation at flush to ``409 period_overlap``."""
CLOSURE_RANGE_INDEX = "ix_closures_active_range"

# The inclusive range of a row: ``[starts_on, ends_on]``.
DATE_RANGE_SQL = "daterange(starts_on, ends_on, '[]')"


def _range_checks(max_span: int) -> tuple[CheckConstraint, CheckConstraint]:
    return (
        CheckConstraint("starts_on <= ends_on", name="starts_before_ends"),
        CheckConstraint(f"ends_on - starts_on <= {max_span}", name="span_max"),
    )


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AcademicPeriod(Audited, VersionedMixin, Model):
    """A school year, semester or term: an inclusive range of local dates
    (ADR-0004). Names are unique ignoring case (inactive rows count); active
    periods of one type never overlap (``ex_academic_periods_no_overlap``);
    the type never changes."""

    __tablename__ = "academic_periods"
    __table_args__ = (
        *_name_checks(),
        CheckConstraint(
            f"period_type IN ({_quoted(PERIOD_TYPES)})", name="period_type_known"
        ),
        *_range_checks(PERIOD_MAX_SPAN_DAYS),
        Index(PERIOD_NAME_INDEX, func.lower(text("name")), unique=True),
        ExcludeConstraint(
            (text("period_type"), "="),
            (text(DATE_RANGE_SQL), "&&"),
            name=PERIOD_OVERLAP_CONSTRAINT,
            using="gist",
            where=text("is_active"),
        ),
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    period_type: Mapped[str] = mapped_column(String(RANGE_TYPE_MAX_LENGTH))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


class Closure(Audited, VersionedMixin, Model):
    """A center-wide holiday, closure or vacation: whole local days, an
    inclusive range. Names repeat (every year has its "Navidad") and closures
    may overlap each other."""

    __tablename__ = "closures"
    __table_args__ = (
        *_name_checks(),
        CheckConstraint(
            f"closure_type IN ({_quoted(CLOSURE_TYPES)})", name="closure_type_known"
        ),
        *_range_checks(CLOSURE_MAX_SPAN_DAYS),
        Index(
            CLOSURE_RANGE_INDEX,
            text(DATE_RANGE_SQL),
            postgresql_using="gist",
            postgresql_where=text("is_active"),
        ),
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    closure_type: Mapped[str] = mapped_column(String(RANGE_TYPE_MAX_LENGTH))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


# --- Classrooms (#39) ----------------------------------------------------------------

CLASSROOM_CAPACITY_MIN = 1
CLASSROOM_CAPACITY_MAX = 500
CLASSROOM_WINDOWS_MAX = 28
FIRST_WEEKDAY = 1
LAST_WEEKDAY = 7

CLASSROOM_NAME_INDEX = "uq_classrooms_name_ci"
CLASSROOM_WINDOW_OVERLAP_CONSTRAINT = "ex_classroom_windows_no_overlap"
"""The exclusion constraint (``btree_gist``, #38): windows of one room on one
weekday never overlap; touching windows (``[)`` bounds) are allowed. The
service maps a violation at flush to ``422 validation_error``."""

# A window as a timestamp range on a fixed Monday, so a GiST range operator
# can compare TIME values (there is no built-in time range type).
WINDOW_RANGE_SQL = (
    "tsrange(DATE '2000-01-03' + starts_at, DATE '2000-01-03' + ends_at, '[)')"
)


class Classroom(Audited, VersionedMixin, Model):
    """A room of the center (spec §3: a basic catalog, no equipment). Names
    are unique ignoring case (inactive rows count); capacity is 1 to 500.
    Its weekly windows (:class:`ClassroomAvailabilityWindow`) change only
    through the classroom ``PATCH``, whose ``version`` covers them."""

    __tablename__ = "classrooms"
    __table_args__ = (
        *_name_checks(),
        CheckConstraint(
            f"capacity BETWEEN {CLASSROOM_CAPACITY_MIN} AND {CLASSROOM_CAPACITY_MAX}",
            name="capacity_range",
        ),
        Index(CLASSROOM_NAME_INDEX, func.lower(text("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    capacity: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


class ClassroomAvailabilityWindow(Audited, Model):
    """One weekly window in which a room may be used: an ISO weekday
    (1 = Monday) and ``[starts_at, ends_at)`` in local wall-clock time,
    whole minutes, never across midnight. Not versioned: the classroom's
    ``version`` covers its windows."""

    __tablename__ = "classroom_availability_windows"
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
            (text("classroom_id"), "="),
            (text("weekday"), "="),
            (text(WINDOW_RANGE_SQL), "&&"),
            name=CLASSROOM_WINDOW_OVERLAP_CONSTRAINT,
            using="gist",
        ),
    )

    classroom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classrooms.id", ondelete="RESTRICT"),
        index=True,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger)
    starts_at: Mapped[time] = mapped_column(Time(timezone=False))
    ends_at: Mapped[time] = mapped_column(Time(timezone=False))
