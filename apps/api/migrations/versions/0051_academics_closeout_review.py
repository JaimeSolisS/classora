"""Academics: closeout review (approval revisions and corrections).

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-24

M5-11 (#78 A1; spec §3 "Approved hours feed payroll. Tutors can request
corrections and admins reapprove; finalized payroll remains locked", W3.2–
W3.5; architecture §4.1, ISSUE-F7).

- ``session_closeouts``: ``ck_session_closeouts_status_valid`` widened to
  ``submitted``, ``changes_requested``, ``approved``; new columns
  ``last_submitted_at`` (``NOT NULL``, backfilled from ``submitted_at``),
  ``review_reason`` and ``tutor_note`` (``TEXT NULL``, trimmed 1–1000:
  ``ck_session_closeouts_review_reason_valid`` /
  ``ck_session_closeouts_tutor_note_valid``) and ``current_approval_id``
  (``fk_session_closeouts_current_approval_id``: ``ON DELETE RESTRICT``,
  ``DEFERRABLE INITIALLY DEFERRED``); ``ck_session_closeouts_changes
  _requested_reason`` (a reason while ``changes_requested``) and
  ``ck_session_closeouts_approved_has_approval`` (a current revision while
  ``approved``).
- ``session_closeout_approvals`` (E1): append-only approval revisions,
  ``uq_session_closeout_approvals_revision`` (``closeout_id``,
  ``revision``), ``supersedes_approval_id`` unique (self key), revision 1
  and only revision 1 supersedes nothing, ``source`` (``review`` /
  ``staff_correction``), the payroll snapshots and ``approved_minutes``
  0–720; ``ix_session_closeout_approvals_tutor_user_id_session_starts_at``
  for M7-04.
- ``session_closeout_corrections`` (E5): append-only staff corrections.

Both new tables reject ``UPDATE``, ``DELETE`` (row triggers) and
``TRUNCATE`` (a statement trigger) with SQLSTATE 42501 (the 0049 pattern).
No rows are inserted. Downgrading drops the triggers, the two tables and
the new columns and narrows the CHECK again (it fails while a closeout
isn't ``submitted``: a dev-only downgrade).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051"
down_revision: str | Sequence[str] | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLOSEOUTS: Final = "session_closeouts"
APPROVALS: Final = "session_closeout_approvals"
CORRECTIONS: Final = "session_closeout_corrections"
CURRENT_APPROVAL_FK: Final = "fk_session_closeouts_current_approval_id"
APPROVALS_INDEX: Final = "ix_session_closeout_approvals_tutor_user_id_session_starts_at"
CORRECTIONS_INDEX: Final = "ix_session_closeout_corrections_closeout_id"
FIELDS: Final = "'actual_minutes', 'topics', 'performance', 'homework', 'next_steps'"


def _reason_valid(column: str) -> str:
    return (
        f"char_length(btrim({column})) BETWEEN 1 AND 1000"
        f" AND {column} = btrim({column})"
    )


def _immutable(table: str) -> str:
    return f"""
CREATE FUNCTION {table}_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{table} rows never change'
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


def _append_only(table: str) -> None:
    op.execute(_immutable(table))
    op.execute(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}"
        f" FOR EACH ROW EXECUTE FUNCTION {table}_immutable()"
    )
    op.execute(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {table}_immutable()"
    )


def _drop_append_only(table: str) -> None:
    op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
    op.execute(f"DROP TRIGGER {table}_immutable ON {table}")


def upgrade() -> None:
    op.create_table(
        APPROVALS,
        sa.Column("closeout_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_approval_id", sa.UUID(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("session_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_kind", sa.String(length=20), nullable=False),
        sa.Column("scheduled_minutes", sa.Integer(), nullable=False),
        sa.Column("approved_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "attendance", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("approved_by_user_id", sa.UUID(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            "revision >= 1", name=op.f(f"ck_{APPROVALS}_revision_positive")
        ),
        sa.CheckConstraint(
            "(revision = 1) = (supersedes_approval_id IS NULL)",
            name=op.f(f"ck_{APPROVALS}_revision_supersedes"),
        ),
        sa.CheckConstraint(
            "source IN ('review', 'staff_correction')",
            name=op.f(f"ck_{APPROVALS}_source_valid"),
        ),
        sa.CheckConstraint(
            "approved_minutes BETWEEN 0 AND 720",
            name=op.f(f"ck_{APPROVALS}_approved_minutes_range"),
        ),
        sa.ForeignKeyConstraint(
            ["closeout_id"],
            [f"{CLOSEOUTS}.id"],
            name=op.f(f"fk_{APPROVALS}_closeout_id_{CLOSEOUTS}"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_approval_id"],
            [f"{APPROVALS}.id"],
            name=op.f(f"fk_{APPROVALS}_supersedes_approval_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["class_session_id"],
            ["class_sessions.id"],
            name=op.f(f"fk_{APPROVALS}_class_session_id_class_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tutor_user_id"],
            ["users.id"],
            name=op.f(f"fk_{APPROVALS}_tutor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{APPROVALS}_approved_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{APPROVALS}")),
        sa.UniqueConstraint(
            "closeout_id", "revision", name=op.f(f"uq_{APPROVALS}_revision")
        ),
        sa.UniqueConstraint(
            "supersedes_approval_id",
            name=op.f(f"uq_{APPROVALS}_supersedes_approval_id"),
        ),
    )
    op.create_index(
        APPROVALS_INDEX,
        APPROVALS,
        ["tutor_user_id", "session_starts_at"],
        unique=False,
    )
    op.create_table(
        CORRECTIONS,
        sa.Column("closeout_id", sa.UUID(), nullable=False),
        sa.Column("fields", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("previous_minutes", sa.Integer(), nullable=True),
        sa.Column("new_minutes", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("closeout_status", sa.String(length=20), nullable=False),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("corrected_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            f"fields <@ ARRAY[{FIELDS}]::text[]",
            name=op.f(f"ck_{CORRECTIONS}_fields_valid"),
        ),
        sa.CheckConstraint(
            "cardinality(fields) > 0 OR approval_id IS NOT NULL",
            name=op.f(f"ck_{CORRECTIONS}_fields_or_reapproval"),
        ),
        sa.CheckConstraint(
            "(new_minutes IS NOT NULL) = ('actual_minutes' = ANY (fields))",
            name=op.f(f"ck_{CORRECTIONS}_minutes_with_field"),
        ),
        sa.CheckConstraint(
            "new_minutes IS NULL OR new_minutes BETWEEN 0 AND 720",
            name=op.f(f"ck_{CORRECTIONS}_new_minutes_range"),
        ),
        sa.CheckConstraint(
            _reason_valid("reason"), name=op.f(f"ck_{CORRECTIONS}_reason_valid")
        ),
        sa.CheckConstraint(
            "closeout_status IN ('submitted', 'approved')",
            name=op.f(f"ck_{CORRECTIONS}_closeout_status_valid"),
        ),
        sa.CheckConstraint(
            "(closeout_status = 'approved') = (approval_id IS NOT NULL)",
            name=op.f(f"ck_{CORRECTIONS}_approval_when_approved"),
        ),
        sa.ForeignKeyConstraint(
            ["closeout_id"],
            [f"{CLOSEOUTS}.id"],
            name=op.f(f"fk_{CORRECTIONS}_closeout_id_{CLOSEOUTS}"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            [f"{APPROVALS}.id"],
            name=op.f(f"fk_{CORRECTIONS}_approval_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["corrected_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{CORRECTIONS}_corrected_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CORRECTIONS}")),
    )
    op.create_index(CORRECTIONS_INDEX, CORRECTIONS, ["closeout_id"], unique=False)
    _append_only(APPROVALS)
    _append_only(CORRECTIONS)

    op.add_column(
        CLOSEOUTS,
        sa.Column("last_submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE session_closeouts SET last_submitted_at = submitted_at")
    op.alter_column(CLOSEOUTS, "last_submitted_at", nullable=False)
    op.add_column(CLOSEOUTS, sa.Column("review_reason", sa.Text(), nullable=True))
    op.add_column(CLOSEOUTS, sa.Column("tutor_note", sa.Text(), nullable=True))
    op.add_column(CLOSEOUTS, sa.Column("current_approval_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f(CURRENT_APPROVAL_FK),
        CLOSEOUTS,
        APPROVALS,
        ["current_approval_id"],
        ["id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_constraint(op.f(f"ck_{CLOSEOUTS}_status_valid"), CLOSEOUTS, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{CLOSEOUTS}_status_valid"),
        CLOSEOUTS,
        "status IN ('submitted', 'changes_requested', 'approved')",
    )
    for column in ("review_reason", "tutor_note"):
        op.create_check_constraint(
            op.f(f"ck_{CLOSEOUTS}_{column}_valid"),
            CLOSEOUTS,
            f"{column} IS NULL OR ({_reason_valid(column)})",
        )
    op.create_check_constraint(
        op.f(f"ck_{CLOSEOUTS}_changes_requested_reason"),
        CLOSEOUTS,
        "status <> 'changes_requested' OR review_reason IS NOT NULL",
    )
    op.create_check_constraint(
        op.f(f"ck_{CLOSEOUTS}_approved_has_approval"),
        CLOSEOUTS,
        "status <> 'approved' OR current_approval_id IS NOT NULL",
    )


def downgrade() -> None:
    for name in (
        "approved_has_approval",
        "changes_requested_reason",
        "tutor_note_valid",
        "review_reason_valid",
        "status_valid",
    ):
        op.drop_constraint(op.f(f"ck_{CLOSEOUTS}_{name}"), CLOSEOUTS, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{CLOSEOUTS}_status_valid"), CLOSEOUTS, "status IN ('submitted')"
    )
    op.drop_constraint(op.f(CURRENT_APPROVAL_FK), CLOSEOUTS, type_="foreignkey")
    for column in ("current_approval_id", "tutor_note", "review_reason"):
        op.drop_column(CLOSEOUTS, column)
    op.drop_column(CLOSEOUTS, "last_submitted_at")
    _drop_append_only(CORRECTIONS)
    _drop_append_only(APPROVALS)
    op.drop_index(CORRECTIONS_INDEX, table_name=CORRECTIONS)
    op.drop_table(CORRECTIONS)
    op.drop_index(APPROVALS_INDEX, table_name=APPROVALS)
    op.drop_table(APPROVALS)
    op.execute(f"DROP FUNCTION {CORRECTIONS}_immutable()")
    op.execute(f"DROP FUNCTION {APPROVALS}_immutable()")
