"""Courses: course templates, modules, assessments and snapshots.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-21

M4-19 (#64; spec §3 structured course; owner decisions E1 (a), E2 (a)).

- ``course_templates`` (audited, versioned): ``name`` (trimmed, not blank,
  unique ignoring case: ``uq_course_templates_name_ci``), ``description``,
  ``session_count`` SMALLINT 1-200 (ADR-0005 D4), ``session_minutes``
  SMALLINT 15-480, ``min_attendance_percent`` SMALLINT 1-100 or ``NULL``,
  ``is_active``.
- ``course_template_subjects`` (audited): ``template_id``, ``subject_id``
  (``RESTRICT``), unique (``template_id``, ``subject_id``).
- ``course_template_modules`` (audited): ``position`` SMALLINT 1-50, unique
  per template ``DEFERRABLE INITIALLY DEFERRED``; ``title``, ``description``,
  ``subject_id`` (``RESTRICT``, optional), ``planned_sessions`` 1-200,
  ``is_required``, ``materials`` (free text).
- ``course_template_assessments`` (audited): ``position`` SMALLINT 1-30,
  unique per template ``DEFERRABLE INITIALLY DEFERRED``; ``title``,
  ``module_id`` (``RESTRICT``, ``NULL`` = the whole course), ``is_required``,
  ``min_score_percent`` 1-100 or ``NULL``.
- ``course_template_snapshots`` (audited, never updated): ``template_id``,
  ``template_version``, unique together; ``schema_version`` (default 1),
  ``content`` JSONB. The trigger ``course_template_snapshots_immutable``
  rejects ``UPDATE`` and ``DELETE`` (the revision 0017 pattern).

No seed rows. Downgrading drops the tables, the trigger and its function.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_FUNCTION = """
CREATE FUNCTION course_template_snapshots_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'course_template_snapshots rows never change'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "course_templates",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("session_count", sa.SmallInteger(), nullable=False),
        sa.Column("session_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("min_attendance_percent", sa.SmallInteger(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
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
            "name ~ '[^[:space:]]'", name=op.f("ck_course_templates_name_not_blank")
        ),
        sa.CheckConstraint(
            "min_attendance_percent BETWEEN 1 AND 100",
            name=op.f("ck_course_templates_min_attendance_percent_range"),
        ),
        sa.CheckConstraint(
            "name = btrim(name)", name=op.f("ck_course_templates_name_trimmed")
        ),
        sa.CheckConstraint(
            "session_count BETWEEN 1 AND 200",
            name=op.f("ck_course_templates_session_count_range"),
        ),
        sa.CheckConstraint(
            "session_minutes BETWEEN 15 AND 480",
            name=op.f("ck_course_templates_session_minutes_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_templates")),
    )
    op.create_index(
        "uq_course_templates_name_ci",
        "course_templates",
        [sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "course_template_snapshots",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column(
            "schema_version",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["course_templates.id"],
            name=op.f("fk_course_template_snapshots_template_id_course_templates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_template_snapshots")),
        sa.UniqueConstraint(
            "template_id",
            "template_version",
            name="uq_course_template_snapshots_template_version",
        ),
    )
    op.create_table(
        "course_template_modules",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("planned_sessions", sa.SmallInteger(), nullable=True),
        sa.Column(
            "is_required", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("materials", sa.String(length=2000), nullable=True),
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
            "title ~ '[^[:space:]]'",
            name=op.f("ck_course_template_modules_title_not_blank"),
        ),
        sa.CheckConstraint(
            "planned_sessions BETWEEN 1 AND 200",
            name=op.f("ck_course_template_modules_planned_sessions_range"),
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 50",
            name=op.f("ck_course_template_modules_position_range"),
        ),
        sa.CheckConstraint(
            "title = btrim(title)",
            name=op.f("ck_course_template_modules_title_trimmed"),
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_course_template_modules_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["course_templates.id"],
            name=op.f("fk_course_template_modules_template_id_course_templates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_template_modules")),
        sa.UniqueConstraint(
            "template_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name="uq_course_template_modules_template_position",
        ),
    )
    op.create_index(
        op.f("ix_course_template_modules_template_id"),
        "course_template_modules",
        ["template_id"],
        unique=False,
    )
    op.create_table(
        "course_template_subjects",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_course_template_subjects_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["course_templates.id"],
            name=op.f("fk_course_template_subjects_template_id_course_templates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_template_subjects")),
        sa.UniqueConstraint(
            "template_id",
            "subject_id",
            name="uq_course_template_subjects_template_subject",
        ),
    )
    op.create_index(
        op.f("ix_course_template_subjects_subject_id"),
        "course_template_subjects",
        ["subject_id"],
        unique=False,
    )
    op.create_table(
        "course_template_assessments",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("module_id", sa.UUID(), nullable=True),
        sa.Column(
            "is_required", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("min_score_percent", sa.SmallInteger(), nullable=True),
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
            "title ~ '[^[:space:]]'",
            name=op.f("ck_course_template_assessments_title_not_blank"),
        ),
        sa.CheckConstraint(
            "min_score_percent BETWEEN 1 AND 100",
            name=op.f("ck_course_template_assessments_min_score_percent_range"),
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 30",
            name=op.f("ck_course_template_assessments_position_range"),
        ),
        sa.CheckConstraint(
            "title = btrim(title)",
            name=op.f("ck_course_template_assessments_title_trimmed"),
        ),
        sa.ForeignKeyConstraint(
            ["module_id"],
            ["course_template_modules.id"],
            name="fk_course_template_assessments_module_id_modules",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["course_templates.id"],
            name=op.f("fk_course_template_assessments_template_id_course_templates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_template_assessments")),
        sa.UniqueConstraint(
            "template_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name="uq_course_template_assessments_template_position",
        ),
    )
    op.create_index(
        op.f("ix_course_template_assessments_template_id"),
        "course_template_assessments",
        ["template_id"],
        unique=False,
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER course_template_snapshots_immutable"
        " BEFORE UPDATE OR DELETE ON course_template_snapshots"
        " FOR EACH ROW EXECUTE FUNCTION course_template_snapshots_immutable()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER course_template_snapshots_immutable ON course_template_snapshots"
    )
    op.drop_index(
        op.f("ix_course_template_assessments_template_id"),
        table_name="course_template_assessments",
    )
    op.drop_table("course_template_assessments")
    op.drop_index(
        op.f("ix_course_template_subjects_subject_id"),
        table_name="course_template_subjects",
    )
    op.drop_table("course_template_subjects")
    op.drop_index(
        op.f("ix_course_template_modules_template_id"),
        table_name="course_template_modules",
    )
    op.drop_table("course_template_modules")
    op.drop_table("course_template_snapshots")
    op.drop_index("uq_course_templates_name_ci", table_name="course_templates")
    op.drop_table("course_templates")
    op.execute("DROP FUNCTION course_template_snapshots_immutable()")
