"""Scheduling: session requests by guardians and students.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-23

M4-14 (#59 A; spec §1 "request, not booking", §3, W2.1-W2.2; ADR-0005).

- ``session_requests``: ``student_id`` (``students``), ``subject_id``
  (``subjects``) and ``requested_by_user_id`` (``users``), all
  ``RESTRICT``; the whole-minute instants ``starts_at`` / ``ends_at``
  (``ck_session_requests_whole_minutes``) with ``ends_at = starts_at +
  duration_minutes`` (``ck_session_requests_ends_after_duration``);
  ``duration_minutes`` ``SMALLINT`` in 30, 45, 60, 90, 120
  (``ck_session_requests_duration_valid``); ``note`` ``VARCHAR(500)``,
  trimmed and non-empty when set (``ck_session_requests_note_trimmed``);
  ``status`` ``VARCHAR(16)`` default ``pending``, one of ``pending``,
  ``confirmed``, ``rejected``, ``withdrawn``
  (``ck_session_requests_status_valid``; #60 writes ``confirmed`` /
  ``rejected``); ``resolved_at`` set exactly when not ``pending``
  (``ck_session_requests_resolved_iff_not_pending``); versioned.
- ``ex_session_requests_student_pending_overlap``: ``EXCLUDE USING gist
  (student_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE
  (status = 'pending')`` (``btree_gist``, revision 0021): one student's
  pending requests never overlap; back-to-back is allowed.
- ``ix_session_requests_student_id_created_at`` and
  ``ix_session_requests_requested_by_user_id_created_at`` (``created_at
  DESC``) for the lists.

The pending rows are a time-zone dependency (``scheduling.session_requests``,
registered in ``app/wiring.py``). Downgrading drops the table.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "session_requests"

CHECKS: Final = {
    "whole_minutes": (
        "mod(EXTRACT(EPOCH FROM starts_at), 60) = 0"
        " AND mod(EXTRACT(EPOCH FROM ends_at), 60) = 0"
    ),
    "duration_valid": "duration_minutes IN (30, 45, 60, 90, 120)",
    "ends_after_duration": (
        "ends_at = starts_at + duration_minutes * interval '1 minute'"
    ),
    "note_trimmed": "note IS NULL OR (note = btrim(note) AND note <> '')",
    "status_valid": "status IN ('confirmed', 'pending', 'rejected', 'withdrawn')",
    "resolved_iff_not_pending": "(status = 'pending') = (resolved_at IS NULL)",
}


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{TABLE}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        *(
            sa.CheckConstraint(condition, name=op.f(f"ck_{TABLE}_{name}"))
            for name, condition in CHECKS.items()
        ),
        _fk("student_id", "students"),
        _fk("subject_id", "subjects"),
        _fk("requested_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        postgresql.ExcludeConstraint(
            (sa.text("student_id"), "="),
            (sa.text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            name="ex_session_requests_student_pending_overlap",
            using="gist",
            where=sa.text("status = 'pending'"),
        ),
    )
    op.create_index(
        "ix_session_requests_student_id_created_at",
        TABLE,
        ["student_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_session_requests_requested_by_user_id_created_at",
        TABLE,
        ["requested_by_user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table(TABLE)
