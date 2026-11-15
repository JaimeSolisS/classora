"""Academics: evaluation templates, evaluations and their scores.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-25

M5-13 (#80 A1; spec §2, §4 "structured diagnostic and ongoing
evaluations", §9, §16; architecture §4.1; ADR-0004).

- ``evaluation_templates``: ``name`` (1–120, trimmed; unique ignoring case,
  inactive rows count: ``uq_evaluation_templates_name_ci``), ``kind``
  (``diagnostic`` / ``ongoing``), an optional ``subject_id`` (``NULL`` =
  any subject), an optional ``description`` (1–2000, trimmed),
  ``is_active`` (default true) and a ``version``.
- ``evaluation_template_criteria``: ``template_id``, ``position`` (1–20,
  unique per template, deferrable: the #64 pattern), ``title`` (1–120,
  trimmed), ``description`` (1–500, trimmed) and ``max_score``
  (``NUMERIC(6,2)``, 0.5–1000).
- ``evaluations``: the student, subject, author and template (with the
  copied ``template_version``, ``template_name`` and ``kind``), the
  ``evaluated_on`` local date, an optional ``class_session_id``, the
  optional course link (``cohort_id``, the snapshot
  ``course_assessment_id`` with no foreign key, its title: all or none,
  only with a session), ``status`` (``draft`` / ``final``), ``feedback``
  (≤ 4000), the totals (``score_total``, ``max_total`` ``NUMERIC(8,2)``,
  ``score_percent`` ``NUMERIC(5,2)`` 0–100, set exactly when final with
  ``finalized_at``), ``amended_at`` / ``last_change_reason`` (≤ 500), the
  void (``voided_at``, ``voided_by_user_id``, ``void_reason`` 1–500: all or
  none, final only) and the soft delete (``deleted_at``,
  ``deleted_by_user_id``: together, drafts only). Partial indexes: the
  history (``student_id, evaluated_on, id`` over final, non-voided rows),
  the working list (``student_id, created_at, id`` over live rows), an
  author's drafts, and ``(cohort_id, course_assessment_id)`` for #85.
- ``evaluation_scores``: ``evaluation_id``, ``position`` (1–20, unique per
  evaluation), the copied ``criterion_title`` / ``criterion_description`` /
  ``max_score``, the ``score`` (``NULL`` or 0..max_score) and ``comment``
  (1–1000).

Every foreign key is ``ON DELETE RESTRICT``. No time-zone dependency
(``evaluated_on`` is a date). No rows are inserted. Downgrading drops the
four tables.
"""

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: str | Sequence[str] | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEMPLATES: Final = "evaluation_templates"
CRITERIA: Final = "evaluation_template_criteria"
EVALUATIONS: Final = "evaluations"
SCORES: Final = "evaluation_scores"
NAME_INDEX: Final = "uq_evaluation_templates_name_ci"
CRITERIA_TEMPLATE_INDEX: Final = "ix_evaluation_template_criteria_template_id"
HISTORY_INDEX: Final = "ix_evaluations_student_id_evaluated_on"
WORKING_INDEX: Final = "ix_evaluations_student_id_created_at"
DRAFTS_INDEX: Final = "ix_evaluations_author_user_id_drafts"
COURSE_INDEX: Final = "ix_evaluations_cohort_id_course_assessment_id"


def _standard() -> list[sa.Column[Any]]:
    return [
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


def _version() -> sa.Column[Any]:
    return sa.Column(
        "version", sa.Integer(), server_default=sa.text("1"), nullable=False
    )


def _timestamp(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _trimmed(table: str, column: str, maximum: int, *, nullable: bool) -> Any:
    valid = (
        f"char_length({column}) BETWEEN 1 AND {maximum} AND {column} = btrim({column})"
    )
    return _check(
        table,
        f"{column}_valid",
        f"{column} IS NULL OR ({valid})" if nullable else valid,
    )


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        TEMPLATES,
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        _version(),
        *_standard(),
        _trimmed(TEMPLATES, "name", 120, nullable=False),
        _check(TEMPLATES, "kind_valid", "kind IN ('diagnostic', 'ongoing')"),
        _trimmed(TEMPLATES, "description", 2000, nullable=True),
        _fk(TEMPLATES, "subject_id", "subjects"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TEMPLATES}")),
    )
    op.create_index(NAME_INDEX, TEMPLATES, [sa.text("lower(name)")], unique=True)

    op.create_table(
        CRITERIA,
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("max_score", sa.Numeric(precision=6, scale=2), nullable=False),
        *_standard(),
        _check(CRITERIA, "position_range", "position BETWEEN 1 AND 20"),
        _trimmed(CRITERIA, "title", 120, nullable=False),
        _trimmed(CRITERIA, "description", 500, nullable=True),
        _check(CRITERIA, "max_score_range", "max_score >= 0.5 AND max_score <= 1000"),
        _fk(CRITERIA, "template_id", TEMPLATES),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CRITERIA}")),
        sa.UniqueConstraint(
            "template_id",
            "position",
            name="uq_evaluation_template_criteria_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(CRITERIA_TEMPLATE_INDEX, CRITERIA, ["template_id"], unique=False)

    op.create_table(
        EVALUATIONS,
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("evaluated_on", sa.Date(), nullable=False),
        sa.Column("class_session_id", sa.UUID(), nullable=True),
        sa.Column("cohort_id", sa.UUID(), nullable=True),
        sa.Column("course_assessment_id", sa.UUID(), nullable=True),
        sa.Column("course_assessment_title", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.String(length=10),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("score_total", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("max_total", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("score_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        _timestamp("finalized_at"),
        _timestamp("amended_at"),
        sa.Column("last_change_reason", sa.Text(), nullable=True),
        _timestamp("voided_at"),
        sa.Column("voided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
        _timestamp("deleted_at"),
        _version(),
        *_standard(),
        _check(EVALUATIONS, "kind_valid", "kind IN ('diagnostic', 'ongoing')"),
        _check(EVALUATIONS, "status_valid", "status IN ('draft', 'final')"),
        _trimmed(EVALUATIONS, "template_name", 120, nullable=False),
        _check(
            EVALUATIONS,
            "course_together",
            "(cohort_id IS NULL) = (course_assessment_id IS NULL)"
            " AND (cohort_id IS NULL) = (course_assessment_title IS NULL)",
        ),
        _check(
            EVALUATIONS,
            "course_needs_session",
            "cohort_id IS NULL OR class_session_id IS NOT NULL",
        ),
        _check(
            EVALUATIONS,
            "course_assessment_title_valid",
            "course_assessment_title IS NULL"
            " OR char_length(course_assessment_title) BETWEEN 1 AND 200",
        ),
        _check(
            EVALUATIONS,
            "feedback_valid",
            "feedback IS NULL OR char_length(feedback) BETWEEN 1 AND 4000",
        ),
        _check(
            EVALUATIONS,
            "score_percent_range",
            "score_percent >= 0 AND score_percent <= 100",
        ),
        _check(
            EVALUATIONS,
            "final_totals",
            "(status = 'final') = (finalized_at IS NOT NULL"
            " AND score_total IS NOT NULL AND max_total IS NOT NULL"
            " AND score_percent IS NOT NULL)",
        ),
        _check(
            EVALUATIONS,
            "last_change_reason_valid",
            "last_change_reason IS NULL"
            " OR char_length(last_change_reason) BETWEEN 1 AND 500",
        ),
        _check(
            EVALUATIONS,
            "voided_together",
            "(voided_at IS NULL) = (voided_by_user_id IS NULL)"
            " AND (voided_at IS NULL) = (void_reason IS NULL)",
        ),
        _check(
            EVALUATIONS,
            "void_reason_valid",
            "void_reason IS NULL OR char_length(void_reason) BETWEEN 1 AND 500",
        ),
        _check(EVALUATIONS, "voided_final", "voided_at IS NULL OR status = 'final'"),
        _check(
            EVALUATIONS,
            "deleted_together",
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
        ),
        _check(EVALUATIONS, "deleted_draft", "deleted_at IS NULL OR status = 'draft'"),
        _fk(EVALUATIONS, "student_id", "students"),
        _fk(EVALUATIONS, "subject_id", "subjects"),
        _fk(EVALUATIONS, "author_user_id", "users"),
        _fk(EVALUATIONS, "template_id", TEMPLATES),
        _fk(EVALUATIONS, "class_session_id", "class_sessions"),
        _fk(EVALUATIONS, "cohort_id", "course_cohorts"),
        _fk(EVALUATIONS, "voided_by_user_id", "users"),
        _fk(EVALUATIONS, "deleted_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{EVALUATIONS}")),
    )
    op.create_index(
        HISTORY_INDEX,
        EVALUATIONS,
        ["student_id", "evaluated_on", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'final' AND voided_at IS NULL"),
    )
    op.create_index(
        WORKING_INDEX,
        EVALUATIONS,
        ["student_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        DRAFTS_INDEX,
        EVALUATIONS,
        ["author_user_id"],
        unique=False,
        postgresql_where=sa.text("status = 'draft' AND deleted_at IS NULL"),
    )
    op.create_index(
        COURSE_INDEX,
        EVALUATIONS,
        ["cohort_id", "course_assessment_id"],
        unique=False,
        postgresql_where=sa.text("cohort_id IS NOT NULL"),
    )

    op.create_table(
        SCORES,
        sa.Column("evaluation_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("criterion_title", sa.String(length=120), nullable=False),
        sa.Column("criterion_description", sa.String(length=500), nullable=True),
        sa.Column("max_score", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        *_standard(),
        _check(SCORES, "position_range", "position BETWEEN 1 AND 20"),
        _trimmed(SCORES, "criterion_title", 120, nullable=False),
        _check(
            SCORES,
            "criterion_description_valid",
            "criterion_description IS NULL"
            " OR char_length(criterion_description) BETWEEN 1 AND 500",
        ),
        _check(SCORES, "max_score_range", "max_score >= 0.5 AND max_score <= 1000"),
        _check(
            SCORES,
            "score_range",
            "score IS NULL OR (score >= 0 AND score <= max_score)",
        ),
        _check(
            SCORES,
            "comment_valid",
            "comment IS NULL OR char_length(comment) BETWEEN 1 AND 1000",
        ),
        _fk(SCORES, "evaluation_id", EVALUATIONS),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SCORES}")),
        sa.UniqueConstraint(
            "evaluation_id", "position", name="uq_evaluation_scores_position"
        ),
    )


def downgrade() -> None:
    op.drop_table(SCORES)
    op.drop_index(COURSE_INDEX, table_name=EVALUATIONS)
    op.drop_index(DRAFTS_INDEX, table_name=EVALUATIONS)
    op.drop_index(WORKING_INDEX, table_name=EVALUATIONS)
    op.drop_index(HISTORY_INDEX, table_name=EVALUATIONS)
    op.drop_table(EVALUATIONS)
    op.drop_index(CRITERIA_TEMPLATE_INDEX, table_name=CRITERIA)
    op.drop_table(CRITERIA)
    op.drop_index(NAME_INDEX, table_name=TEMPLATES)
    op.drop_table(TEMPLATES)
