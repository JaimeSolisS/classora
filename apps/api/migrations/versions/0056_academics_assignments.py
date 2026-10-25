"""Academics: homework assignments and their submissions.

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-25

M5-15 (#82 A1; spec §4 "tutors provide instructions/deadlines, students
upload work, tutors grade and provide feedback, guardians track
completion", as rescoped on 2026-10-21: text and/or a URL, no uploads; §9,
§16; architecture §4.1; ADR-0004).

- ``assignments``: the creator, the subject, an optional class session,
  ``title`` (1–200, trimmed), ``instructions`` (1–4000, trimmed), the
  center-local deadline ``due_on`` + optional ``due_time`` (``NULL``: the
  end of the day; no time-zone dependency), an optional ``max_score``
  (0.5–1000, two places), the soft delete (``deleted_at``,
  ``deleted_by_user_id``: together) and a ``version``.
- ``assignment_submissions``: one row per recipient (unique per assignment
  and student): the submission (``text`` ≤ 10000, ``url`` ≤ 2000,
  ``submitted_at`` / ``submitted_by_user_id`` / ``submitted_via``
  together; no content without a submission), the grade (``score`` ≥ 0
  and ``score_percent`` 0–100 together, ``feedback`` ≤ 4000, ``graded_at``
  / ``graded_on`` / ``graded_by_user_id`` together, only on a submitted
  row; no score or feedback without a grade), ``grade_amended_at``, the
  internal ``last_change_reason`` (1–500, trimmed) and a ``version``.

Every foreign key is ``ON DELETE RESTRICT``. No rows are inserted.
Downgrading drops both tables.
"""

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | Sequence[str] | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSIGNMENTS: Final = "assignments"
SUBMISSIONS: Final = "assignment_submissions"
CREATOR_INDEX: Final = "ix_assignments_creator_user_id_due_on"
SESSION_INDEX: Final = "ix_assignments_class_session_id"
SUBMISSIONS_ASSIGNMENT_INDEX: Final = "ix_assignment_submissions_assignment_id"
SUBMISSIONS_STUDENT_INDEX: Final = "ix_assignment_submissions_student_id_assignment_id"
HISTORY_INDEX: Final = "ix_assignment_submissions_student_id_graded_on"


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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    ]


def _timestamp(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _trimmed(table: str, column: str, maximum: int) -> sa.CheckConstraint:
    return _check(
        table,
        f"{column}_valid",
        f"char_length({column}) BETWEEN 1 AND {maximum} AND {column} = btrim({column})",
    )


def _length(table: str, column: str, maximum: int) -> sa.CheckConstraint:
    return _check(
        table,
        f"{column}_valid",
        f"{column} IS NULL OR char_length({column}) BETWEEN 1 AND {maximum}",
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
        ASSIGNMENTS,
        sa.Column("creator_user_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("class_session_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("due_time", sa.Time(), nullable=True),
        sa.Column("max_score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
        _timestamp("deleted_at"),
        *_standard(),
        _trimmed(ASSIGNMENTS, "title", 200),
        _trimmed(ASSIGNMENTS, "instructions", 4000),
        _check(
            ASSIGNMENTS,
            "max_score_range",
            "max_score IS NULL OR (max_score >= 0.5 AND max_score <= 1000)",
        ),
        _check(
            ASSIGNMENTS,
            "deleted_together",
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
        ),
        _fk(ASSIGNMENTS, "creator_user_id", "users"),
        _fk(ASSIGNMENTS, "subject_id", "subjects"),
        _fk(ASSIGNMENTS, "class_session_id", "class_sessions"),
        _fk(ASSIGNMENTS, "deleted_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ASSIGNMENTS}")),
    )
    op.create_index(
        CREATOR_INDEX,
        ASSIGNMENTS,
        ["creator_user_id", "due_on", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        SESSION_INDEX,
        ASSIGNMENTS,
        ["class_session_id"],
        unique=False,
        postgresql_where=sa.text("class_session_id IS NOT NULL"),
    )

    op.create_table(
        SUBMISSIONS,
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=2000), nullable=True),
        _timestamp("submitted_at"),
        sa.Column("submitted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("submitted_via", sa.String(length=10), nullable=True),
        sa.Column("score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("score_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        _timestamp("graded_at"),
        sa.Column("graded_on", sa.Date(), nullable=True),
        sa.Column("graded_by_user_id", sa.UUID(), nullable=True),
        _timestamp("grade_amended_at"),
        sa.Column("last_change_reason", sa.Text(), nullable=True),
        *_standard(),
        _length(SUBMISSIONS, "text", 10000),
        _length(SUBMISSIONS, "url", 2000),
        _check(
            SUBMISSIONS,
            "submitted_via_valid",
            "submitted_via IS NULL OR submitted_via IN ('student', 'tutor')",
        ),
        _check(
            SUBMISSIONS,
            "submitted_together",
            "(submitted_at IS NULL) = (submitted_by_user_id IS NULL)"
            " AND (submitted_at IS NULL) = (submitted_via IS NULL)",
        ),
        _check(
            SUBMISSIONS,
            "content_needs_submission",
            "submitted_at IS NOT NULL OR (text IS NULL AND url IS NULL)",
        ),
        _check(SUBMISSIONS, "score_range", "score IS NULL OR score >= 0"),
        _check(
            SUBMISSIONS,
            "score_percent_range",
            "score_percent IS NULL OR (score_percent >= 0 AND score_percent <= 100)",
        ),
        _length(SUBMISSIONS, "feedback", 4000),
        _check(
            SUBMISSIONS,
            "graded_needs_submission",
            "graded_at IS NULL OR submitted_at IS NOT NULL",
        ),
        _check(
            SUBMISSIONS,
            "graded_together",
            "(graded_at IS NULL) = (graded_on IS NULL)"
            " AND (graded_at IS NULL) = (graded_by_user_id IS NULL)",
        ),
        _check(
            SUBMISSIONS,
            "score_together",
            "(score IS NULL) = (score_percent IS NULL)",
        ),
        _check(
            SUBMISSIONS,
            "grade_needs_graded",
            "graded_at IS NOT NULL OR (score IS NULL AND feedback IS NULL)",
        ),
        _check(
            SUBMISSIONS,
            "last_change_reason_valid",
            "last_change_reason IS NULL"
            " OR (char_length(last_change_reason) BETWEEN 1 AND 500"
            " AND last_change_reason = btrim(last_change_reason))",
        ),
        _check(
            SUBMISSIONS,
            "amended_needs_graded",
            "grade_amended_at IS NULL OR graded_at IS NOT NULL",
        ),
        _fk(SUBMISSIONS, "assignment_id", ASSIGNMENTS),
        _fk(SUBMISSIONS, "student_id", "students"),
        _fk(SUBMISSIONS, "submitted_by_user_id", "users"),
        _fk(SUBMISSIONS, "graded_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SUBMISSIONS}")),
        sa.UniqueConstraint(
            "assignment_id",
            "student_id",
            name=op.f("uq_assignment_submissions_assignment_id_student_id"),
        ),
    )
    op.create_index(
        SUBMISSIONS_ASSIGNMENT_INDEX, SUBMISSIONS, ["assignment_id"], unique=False
    )
    op.create_index(
        SUBMISSIONS_STUDENT_INDEX,
        SUBMISSIONS,
        ["student_id", "assignment_id"],
        unique=False,
    )
    op.create_index(
        HISTORY_INDEX,
        SUBMISSIONS,
        ["student_id", "graded_on"],
        unique=False,
        postgresql_where=sa.text("score IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(HISTORY_INDEX, table_name=SUBMISSIONS)
    op.drop_index(SUBMISSIONS_STUDENT_INDEX, table_name=SUBMISSIONS)
    op.drop_index(SUBMISSIONS_ASSIGNMENT_INDEX, table_name=SUBMISSIONS)
    op.drop_table(SUBMISSIONS)
    op.drop_index(SESSION_INDEX, table_name=ASSIGNMENTS)
    op.drop_index(CREATOR_INDEX, table_name=ASSIGNMENTS)
    op.drop_table(ASSIGNMENTS)
