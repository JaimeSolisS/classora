"""Academics: session notes.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-25

M5-12 (#79 A1; spec §2 "Internal tutor notes are not automatically shared
with families", §4, §9, W5.1, W5.3; architecture §4.1).

- ``session_notes``: ``class_session_id`` (``class_sessions``),
  ``student_id`` (``students``) and ``author_user_id`` (``users``), each
  ``ON DELETE RESTRICT``; ``body`` (``TEXT``,
  ``ck_session_notes_body_valid``: 1–4000 characters, stored trimmed);
  ``visibility`` (``ck_session_notes_visibility_valid``: ``internal`` or
  ``family``, default ``internal``); ``published_at`` and
  ``published_by_user_id`` (set exactly when ``family``:
  ``ck_session_notes_published_together``); ``unpublished_at``,
  ``unpublished_by_user_id`` (together) and ``unpublish_reason`` (1–500,
  trimmed, only with ``unpublished_at``); ``body_updated_at``;
  ``deleted_at`` and ``deleted_by_user_id`` (together; a deleted note is
  ``internal``: ``ck_session_notes_deleted_internal``); ``version`` and
  the standard timestamps. The three nullable user keys are ``ON DELETE
  RESTRICT`` too.
- Partial indexes over the live rows (``deleted_at IS NULL``):
  ``ix_session_notes_student_id_created_at`` (``student_id, created_at,
  id``: a student's history, read newest first) and
  ``ix_session_notes_class_session_id``.

No time-zone dependency. No rows are inserted. Downgrading drops the table.
"""

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: str | Sequence[str] | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "session_notes"
STUDENT_INDEX: Final = "ix_session_notes_student_id_created_at"
SESSION_INDEX: Final = "ix_session_notes_class_session_id"
LIVE: Final = "deleted_at IS NULL"
USER_KEYS: Final = (
    "author_user_id",
    "published_by_user_id",
    "unpublished_by_user_id",
    "deleted_by_user_id",
)


def _timestamp(name: str, *, nullable: bool = True) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "visibility",
            sa.String(length=10),
            server_default=sa.text("'internal'"),
            nullable=False,
        ),
        _timestamp("published_at"),
        sa.Column("published_by_user_id", sa.UUID(), nullable=True),
        _timestamp("unpublished_at"),
        sa.Column("unpublished_by_user_id", sa.UUID(), nullable=True),
        sa.Column("unpublish_reason", sa.Text(), nullable=True),
        _timestamp("body_updated_at"),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
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
        _timestamp("deleted_at"),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 4000 AND body = btrim(body)",
            name=op.f(f"ck_{TABLE}_body_valid"),
        ),
        sa.CheckConstraint(
            "visibility IN ('internal', 'family')",
            name=op.f(f"ck_{TABLE}_visibility_valid"),
        ),
        sa.CheckConstraint(
            "(visibility = 'family') = (published_at IS NOT NULL"
            " AND published_by_user_id IS NOT NULL)",
            name=op.f(f"ck_{TABLE}_published_together"),
        ),
        sa.CheckConstraint(
            "(unpublished_at IS NULL) = (unpublished_by_user_id IS NULL)",
            name=op.f(f"ck_{TABLE}_unpublished_together"),
        ),
        sa.CheckConstraint(
            "unpublish_reason IS NULL OR (unpublished_at IS NOT NULL"
            " AND char_length(unpublish_reason) BETWEEN 1 AND 500"
            " AND unpublish_reason = btrim(unpublish_reason))",
            name=op.f(f"ck_{TABLE}_unpublish_reason_valid"),
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
            name=op.f(f"ck_{TABLE}_deleted_together"),
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR visibility = 'internal'",
            name=op.f(f"ck_{TABLE}_deleted_internal"),
        ),
        sa.ForeignKeyConstraint(
            ["class_session_id"],
            ["class_sessions.id"],
            name=op.f(f"fk_{TABLE}_class_session_id_class_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name=op.f(f"fk_{TABLE}_student_id_students"),
            ondelete="RESTRICT",
        ),
        *(
            sa.ForeignKeyConstraint(
                [column],
                ["users.id"],
                name=op.f(f"fk_{TABLE}_{column}_users"),
                ondelete="RESTRICT",
            )
            for column in USER_KEYS
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    op.create_index(
        STUDENT_INDEX,
        TABLE,
        ["student_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text(LIVE),
    )
    op.create_index(
        SESSION_INDEX,
        TABLE,
        ["class_session_id"],
        unique=False,
        postgresql_where=sa.text(LIVE),
    )


def downgrade() -> None:
    op.drop_index(SESSION_INDEX, table_name=TABLE)
    op.drop_index(STUDENT_INDEX, table_name=TABLE)
    op.drop_table(TABLE)
