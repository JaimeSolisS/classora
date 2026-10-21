"""Scheduling: ``class_series``, ``class_series_participants`` and the
occurrence columns of ``class_sessions``.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-21

M4-08 (#53 B; spec §1, §3; ADR-0005 D4, D6; ADR-0004: a local ``TIME`` plus
a duration and a ``TZID``). Both new tables are audited by the ORM.

- ``class_series`` (versioned): ``kind`` ``individual`` | ``group`` |
  ``course``; ``subject_id`` (``subjects``), nullable ``tutor_user_id``
  (``users``) and ``classroom_id`` (``classrooms``), all indexed and ``ON
  DELETE RESTRICT``; ``starts_on`` (``DATE``), ``start_time`` (``TIME``,
  whole minutes), ``duration_minutes`` 15-480, ``time_zone``; the weekly
  rule as ``weekdays`` (``SMALLINT[]``, non-empty, ISO 1-7), ``interval``
  1-4, ``until_on`` or ``count`` 1-200 (never both), the derived ``rrule``
  text and ``last_occurrence_on``.
- ``class_series_participants``: ``class_series_id`` and ``student_id``
  (indexed), ``ON DELETE RESTRICT``; one row per series and student.
- ``class_sessions`` gains ``series_id`` (``class_series``, ``RESTRICT``),
  ``original_start``, ``is_exception`` (default false) and
  ``conflict_codes`` (``VARCHAR(40)[]``, default empty); CHECKs that
  ``series_id`` and ``original_start`` are set together and that only a
  series occurrence is an exception, and
  ``uq_class_sessions_series_original_start``. Existing rows get the
  defaults.

No rows are inserted. Downgrading drops the columns and both tables.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SERIES: Final = "class_series"
PARTICIPANTS: Final = "class_series_participants"
SESSIONS: Final = "class_sessions"

SERIES_CHECKS: Final = {
    "kind_valid": "kind IN ('course', 'group', 'individual')",
    "start_time_whole_minutes": "EXTRACT(SECOND FROM start_time) = 0",
    "duration_range": "duration_minutes BETWEEN 15 AND 480",
    "weekdays_valid": (
        "cardinality(weekdays) >= 1 AND weekdays <@ '{1,2,3,4,5,6,7}'::smallint[]"
    ),
    "interval_range": '"interval" BETWEEN 1 AND 4',
    "count_range": '"count" BETWEEN 1 AND 200',
    "until_or_count": 'until_on IS NULL OR "count" IS NULL',
}
SESSION_CHECKS: Final = {
    "series_original_start_together": (
        "(series_id IS NULL) = (original_start IS NULL)"
    ),
    "exception_needs_series": "series_id IS NOT NULL OR NOT is_exception",
}


def _standard_columns(*, versioned: bool) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]
    if versioned:
        columns.append(
            sa.Column(
                "version", sa.Integer(), server_default=sa.text("1"), nullable=False
            )
        )
    return columns


def _fk(table: str, column: str, referred: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{referred}.id"],
        name=op.f(f"fk_{table}_{column}_{referred}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        SERIES,
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("tutor_user_id", sa.UUID(), nullable=True),
        sa.Column("classroom_id", sa.UUID(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("weekdays", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        sa.Column("interval", sa.SmallInteger(), nullable=False),
        sa.Column("until_on", sa.Date(), nullable=True),
        sa.Column("count", sa.SmallInteger(), nullable=True),
        sa.Column("rrule", sa.Text(), nullable=False),
        sa.Column("last_occurrence_on", sa.Date(), nullable=True),
        *_standard_columns(versioned=True),
        *(
            sa.CheckConstraint(condition, name=op.f(f"ck_{SERIES}_{name}"))
            for name, condition in SERIES_CHECKS.items()
        ),
        _fk(SERIES, "subject_id", "subjects"),
        _fk(SERIES, "tutor_user_id", "users"),
        _fk(SERIES, "classroom_id", "classrooms"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SERIES}")),
    )
    for column in ("subject_id", "tutor_user_id", "classroom_id"):
        op.create_index(op.f(f"ix_{SERIES}_{column}"), SERIES, [column], unique=False)

    op.create_table(
        PARTICIPANTS,
        sa.Column("class_series_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        *_standard_columns(versioned=False),
        _fk(PARTICIPANTS, "class_series_id", "class_series"),
        _fk(PARTICIPANTS, "student_id", "students"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PARTICIPANTS}")),
        sa.UniqueConstraint(
            "class_series_id",
            "student_id",
            name="uq_class_series_participants_series_student",
        ),
    )
    op.create_index(
        op.f(f"ix_{PARTICIPANTS}_student_id"),
        PARTICIPANTS,
        ["student_id"],
        unique=False,
    )

    op.add_column(SESSIONS, sa.Column("series_id", sa.UUID(), nullable=True))
    op.add_column(
        SESSIONS,
        sa.Column("original_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        SESSIONS,
        sa.Column(
            "is_exception",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        SESSIONS,
        sa.Column(
            "conflict_codes",
            postgresql.ARRAY(sa.String(length=40)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        op.f(f"fk_{SESSIONS}_series_id_class_series"),
        SESSIONS,
        SERIES,
        ["series_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for name, condition in SESSION_CHECKS.items():
        op.create_check_constraint(op.f(f"ck_{SESSIONS}_{name}"), SESSIONS, condition)
    op.create_unique_constraint(
        "uq_class_sessions_series_original_start",
        SESSIONS,
        ["series_id", "original_start"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_class_sessions_series_original_start", SESSIONS)
    for name in SESSION_CHECKS:
        op.drop_constraint(op.f(f"ck_{SESSIONS}_{name}"), SESSIONS, type_="check")
    op.drop_constraint(
        op.f(f"fk_{SESSIONS}_series_id_class_series"), SESSIONS, type_="foreignkey"
    )
    for column in ("conflict_codes", "is_exception", "original_start", "series_id"):
        op.drop_column(SESSIONS, column)
    op.drop_table(PARTICIPANTS)
    op.drop_table(SERIES)
