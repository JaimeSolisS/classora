"""Courses: cohorts, and the cohort of course series and sessions.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-23

M4-20 (#65 A; spec §3; ADR-0005 D1, D4, D7).

- ``course_cohorts``: ``name`` ``VARCHAR(120)`` (trimmed and not blank,
  ``ck_course_cohorts_name_trimmed`` / ``ck_course_cohorts_name_not_blank``;
  unique ignoring case through the expression index
  ``uq_course_cohorts_name_ci`` on ``lower(name)``), ``template_id``
  (``course_templates``) and ``template_snapshot_id``
  (``course_template_snapshots``), ``subject_id`` (``subjects``), each
  ``RESTRICT`` and indexed, and ``max_students`` ``SMALLINT`` 1-500
  (``ck_course_cohorts_max_students_range``); versioned. No price column
  (M6-04 / M6-07 add the price link) and no enrollment table (#66).
- ``class_series.cohort_id`` and ``class_sessions.cohort_id``: ``UUID NULL``
  to ``course_cohorts`` (``RESTRICT``) with ``(kind = 'course') =
  (cohort_id IS NOT NULL)`` (``ck_<table>_cohort_iff_kind``; existing rows
  are ``individual`` or ``group``, so the CHECK holds);
  ``ix_class_series_cohort_id`` and ``ix_class_sessions_cohort_id_starts_at``.

No time-zone dependency: a cohort stores no instant computed from the
center's local time; its series is already covered by ``scheduling.
class_series`` (#65 A6). Downgrading drops both columns and the table.
Sessions and series of ``kind = 'course'`` keep their kind without a cohort;
upgrading again needs them removed first (the CHECK would refuse them).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COHORTS: Final = "course_cohorts"


def _cohort_column(table: str) -> None:
    op.add_column(table, sa.Column("cohort_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f(f"fk_{table}_cohort_id_course_cohorts"),
        table,
        COHORTS,
        ["cohort_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f(f"ck_{table}_cohort_iff_kind"),
        table,
        "(kind = 'course') = (cohort_id IS NOT NULL)",
    )


def upgrade() -> None:
    op.create_table(
        COHORTS,
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("template_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("max_students", sa.SmallInteger(), nullable=False),
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
        sa.CheckConstraint(
            "name = btrim(name)", name=op.f("ck_course_cohorts_name_trimmed")
        ),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name=op.f("ck_course_cohorts_name_not_blank")
        ),
        sa.CheckConstraint(
            "max_students BETWEEN 1 AND 500",
            name=op.f("ck_course_cohorts_max_students_range"),
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["course_templates.id"],
            name=op.f("fk_course_cohorts_template_id_course_templates"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_snapshot_id"],
            ["course_template_snapshots.id"],
            # An explicit name (the convention's has 64 characters).
            name=op.f("fk_course_cohorts_template_snapshot_id_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_course_cohorts_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_cohorts")),
    )
    for column in ("template_id", "template_snapshot_id", "subject_id"):
        op.create_index(
            op.f(f"ix_course_cohorts_{column}"), COHORTS, [column], unique=False
        )
    op.create_index(
        "uq_course_cohorts_name_ci", COHORTS, [sa.text("lower(name)")], unique=True
    )

    _cohort_column("class_series")
    op.create_index(
        op.f("ix_class_series_cohort_id"), "class_series", ["cohort_id"], unique=False
    )
    _cohort_column("class_sessions")
    op.create_index(
        op.f("ix_class_sessions_cohort_id_starts_at"),
        "class_sessions",
        ["cohort_id", "starts_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_class_sessions_cohort_id_starts_at"), "class_sessions")
    op.drop_index(op.f("ix_class_series_cohort_id"), "class_series")
    for table in ("class_sessions", "class_series"):
        op.drop_constraint(op.f(f"ck_{table}_cohort_iff_kind"), table, type_="check")
        op.drop_constraint(
            op.f(f"fk_{table}_cohort_id_course_cohorts"), table, type_="foreignkey"
        )
        op.drop_column(table, "cohort_id")
    op.drop_index("uq_course_cohorts_name_ci", COHORTS)
    for column in ("subject_id", "template_snapshot_id", "template_id"):
        op.drop_index(op.f(f"ix_course_cohorts_{column}"), COHORTS)
    op.drop_table(COHORTS)
