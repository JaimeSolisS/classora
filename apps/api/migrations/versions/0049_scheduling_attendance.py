"""Scheduling: attendance and its corrections.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-24

M5-09 (#76 A1, E3; spec §3 "Attendance, session completion and changes",
§16; architecture ISSUE-A3).

- ``class_session_attendance``: ``class_session_id``, ``student_id``,
  ``status`` (``ck_class_session_attendance_status_valid``: ``present``,
  ``late`` or ``absent``), ``marked_by_user_id`` (``users``, ``ON DELETE
  RESTRICT``) and ``marked_at``. The composite foreign key
  ``fk_class_session_attendance_participant`` to
  ``class_session_participants(class_session_id, student_id)`` (``ON DELETE
  RESTRICT``) allows rows only for a participant of that session;
  ``uq_class_session_attendance_session_student`` one row per session and
  student; ``ix_class_session_attendance_student_id_class_session_id`` for
  a student's attendance across sessions.
- ``class_session_attendance_corrections``: ``attendance_id``
  (``class_session_attendance``, indexed), ``previous_status`` (``NULL``
  when staff made the first mark), ``new_status``, ``reason`` (trimmed,
  1-1000 characters) and ``corrected_by_user_id`` (``users``), both keys
  ``ON DELETE RESTRICT``; ``previous_status IS DISTINCT FROM new_status``.
  Append-only: the trigger function
  ``class_session_attendance_corrections_immutable`` rejects ``UPDATE``,
  ``DELETE`` (row triggers) and ``TRUNCATE`` (a statement trigger) with
  SQLSTATE 42501 (the 0017 / 0004 pattern).

No rows are inserted. Downgrading drops the triggers, both tables and the
function.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | Sequence[str] | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTENDANCE: Final = "class_session_attendance"
CORRECTIONS: Final = "class_session_attendance_corrections"
FUNCTION: Final = "class_session_attendance_corrections_immutable"
ROW_TRIGGER: Final = "class_session_attendance_corrections_immutable"
TRUNCATE_TRIGGER: Final = "class_session_attendance_corrections_no_truncate"
STATUSES: Final = "'absent', 'late', 'present'"

IMMUTABLE_FUNCTION: Final = f"""
CREATE FUNCTION {FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'class_session_attendance_corrections rows never change'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def _standard_columns() -> list[sa.Column[object]]:
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


def upgrade() -> None:
    op.create_table(
        ATTENDANCE,
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("marked_by_user_id", sa.UUID(), nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            f"status IN ({STATUSES})", name=op.f(f"ck_{ATTENDANCE}_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["class_session_id", "student_id"],
            [
                "class_session_participants.class_session_id",
                "class_session_participants.student_id",
            ],
            name=op.f("fk_class_session_attendance_participant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["marked_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{ATTENDANCE}_marked_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ATTENDANCE}")),
        sa.UniqueConstraint(
            "class_session_id",
            "student_id",
            name=op.f("uq_class_session_attendance_session_student"),
        ),
    )
    op.create_index(
        "ix_class_session_attendance_student_id_class_session_id",
        ATTENDANCE,
        ["student_id", "class_session_id"],
        unique=False,
    )
    op.create_table(
        CORRECTIONS,
        sa.Column("attendance_id", sa.UUID(), nullable=False),
        sa.Column("previous_status", sa.String(length=10), nullable=True),
        sa.Column("new_status", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrected_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            f"previous_status IS NULL OR previous_status IN ({STATUSES})",
            name=op.f(f"ck_{CORRECTIONS}_previous_status_valid"),
        ),
        sa.CheckConstraint(
            f"new_status IN ({STATUSES})",
            name=op.f(f"ck_{CORRECTIONS}_new_status_valid"),
        ),
        sa.CheckConstraint(
            "previous_status IS DISTINCT FROM new_status",
            name=op.f(f"ck_{CORRECTIONS}_status_changes"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 1 AND 1000 AND reason = btrim(reason)",
            name=op.f(f"ck_{CORRECTIONS}_reason_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["attendance_id"],
            [f"{ATTENDANCE}.id"],
            name=op.f("fk_attendance_corrections_attendance_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["corrected_by_user_id"],
            ["users.id"],
            name=op.f("fk_attendance_corrections_corrected_by_user_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CORRECTIONS}")),
    )
    op.create_index(
        op.f(f"ix_{CORRECTIONS}_attendance_id"),
        CORRECTIONS,
        ["attendance_id"],
        unique=False,
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {ROW_TRIGGER} BEFORE UPDATE OR DELETE ON {CORRECTIONS}"
        f" FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {TRUNCATE_TRIGGER} BEFORE TRUNCATE ON {CORRECTIONS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {TRUNCATE_TRIGGER} ON {CORRECTIONS}")
    op.execute(f"DROP TRIGGER {ROW_TRIGGER} ON {CORRECTIONS}")
    op.drop_table(CORRECTIONS)
    op.drop_index(
        "ix_class_session_attendance_student_id_class_session_id",
        table_name=ATTENDANCE,
    )
    op.drop_table(ATTENDANCE)
    op.execute(f"DROP FUNCTION {FUNCTION}()")
