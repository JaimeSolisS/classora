"""Academics: session closeouts.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-24

M5-10 (#77 A2; spec §2, §3 "Attendance, session completion and changes",
§7; architecture §4.1, ISSUE-F2).

- ``session_closeouts``: ``class_session_id`` (``class_sessions``, ``ON
  DELETE RESTRICT``, unique: ``uq_session_closeouts_class_session_id``, one
  closeout per session), ``tutor_user_id`` (``users``, ``ON DELETE
  RESTRICT``: the submitting tutor), ``status``
  (``ck_session_closeouts_status_valid``: ``submitted``; a CHECK, not an
  enum type, so #78 widens it), ``topics``, ``performance``, ``homework``
  and ``next_steps`` (``TEXT NULL``, each ``ck_session_closeouts_<column>
  _length``: ``NULL`` or 1–2000 characters), ``submitted_at``, ``version``
  and the standard timestamps; ``ix_session_closeouts_tutor_user_id
  _submitted_at`` for #78 and M7 lists.

``class_sessions`` is unchanged (``actual_minutes`` exists since 0030).
No rows are inserted. Downgrading drops the table.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | Sequence[str] | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "session_closeouts"
SUMMARY_COLUMNS: Final = ("topics", "performance", "homework", "next_steps")
INDEX: Final = "ix_session_closeouts_tutor_user_id_submitted_at"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        *(sa.Column(column, sa.Text(), nullable=True) for column in SUMMARY_COLUMNS),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('submitted')", name=op.f(f"ck_{TABLE}_status_valid")
        ),
        *(
            sa.CheckConstraint(
                f"{column} IS NULL OR char_length({column}) BETWEEN 1 AND 2000",
                name=op.f(f"ck_{TABLE}_{column}_length"),
            )
            for column in SUMMARY_COLUMNS
        ),
        sa.ForeignKeyConstraint(
            ["class_session_id"],
            ["class_sessions.id"],
            name=op.f(f"fk_{TABLE}_class_session_id_class_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tutor_user_id"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_tutor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint(
            "class_session_id", name=op.f(f"uq_{TABLE}_class_session_id")
        ),
    )
    op.create_index(INDEX, TABLE, ["tutor_user_id", "submitted_at"], unique=False)


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
