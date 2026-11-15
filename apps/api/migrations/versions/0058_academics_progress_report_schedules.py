"""Academics: progress report schedules and placeholders.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-26

M5-16c (#316 A1, A2; spec §4, §9, §10, §16; architecture §4.1, §6;
ADR-0003, ADR-0004).

- ``progress_report_schedules`` (audited, versioned): the student, the
  author (a tutor), an optional subject, ``cadence`` (``weekly`` /
  ``monthly``), ``starts_on`` (a Monday for weekly, the 1st for monthly;
  center-local), ``is_active`` and who created it. At most one active
  schedule per student, author, cadence and subject (``COALESCE`` of the
  subject with the nil UUID); a student's schedules by creation time; the
  active schedules by id (the job's scan).
- ``progress_reports.schedule_id``: the schedule whose job created a
  placeholder (``NULL`` for every report a tutor starts), with a unique
  index on ``(schedule_id, period_start)`` that is **not** filtered on
  ``deleted_at`` or ``status``: a deleted or retracted placeholder is never
  created again for its period (E2).

The immutability trigger of 0057 is unchanged (nothing ever updates the new
column). Every foreign key is ``ON DELETE RESTRICT``. No time-zone
dependency (local dates only). No rows are inserted. Downgrading drops the
index, the column and the table.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: str | Sequence[str] | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEDULES: Final = "progress_report_schedules"
REPORTS: Final = "progress_reports"
ACTIVE_UNIQUE: Final = "uq_progress_report_schedules_active"
STUDENT_INDEX: Final = "ix_progress_report_schedules_student_id_created_at"
ACTIVE_INDEX: Final = "ix_progress_report_schedules_active_id"
SCHEDULE_PERIOD_UNIQUE: Final = "uq_progress_reports_schedule_period"
REPORT_SCHEDULE_FK: Final = "fk_progress_reports_schedule_id_progress_report_schedules"


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{SCHEDULES}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def _check(name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{SCHEDULES}_{name}"))


def upgrade() -> None:
    op.create_table(
        SCHEDULES,
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("cadence", sa.String(length=20), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        _check("cadence_valid", "cadence IN ('weekly', 'monthly')"),
        _check(
            "weekly_starts_on",
            "cadence <> 'weekly' OR extract(isodow from starts_on) = 1",
        ),
        _check(
            "monthly_starts_on",
            "cadence <> 'monthly' OR extract(day from starts_on) = 1",
        ),
        _fk("student_id", "students"),
        _fk("author_user_id", "users"),
        _fk("subject_id", "subjects"),
        _fk("created_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SCHEDULES}")),
    )
    op.create_index(
        ACTIVE_UNIQUE,
        SCHEDULES,
        [
            sa.text("student_id"),
            sa.text("author_user_id"),
            sa.text("cadence"),
            sa.text(
                "COALESCE(subject_id, '00000000-0000-0000-0000-000000000000'::uuid)"
            ),
        ],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        STUDENT_INDEX, SCHEDULES, ["student_id", "created_at"], unique=False
    )
    op.create_index(
        ACTIVE_INDEX,
        SCHEDULES,
        ["id"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    op.add_column(REPORTS, sa.Column("schedule_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        REPORT_SCHEDULE_FK,
        REPORTS,
        SCHEDULES,
        ["schedule_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        SCHEDULE_PERIOD_UNIQUE,
        REPORTS,
        ["schedule_id", "period_start"],
        unique=True,
        postgresql_where=sa.text("schedule_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(SCHEDULE_PERIOD_UNIQUE, table_name=REPORTS)
    op.drop_constraint(REPORT_SCHEDULE_FK, REPORTS, type_="foreignkey")
    op.drop_column(REPORTS, "schedule_id")
    op.drop_index(ACTIVE_INDEX, table_name=SCHEDULES)
    op.drop_index(STUDENT_INDEX, table_name=SCHEDULES)
    op.drop_index(ACTIVE_UNIQUE, table_name=SCHEDULES)
    op.drop_table(SCHEDULES)
