"""Staff and scheduling: availability change requests and reassignment flags.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-23

M4-18 (#63 A; spec §3 "Changes affecting existing assignments require
administrator approval"; ADR-0005 D2).

- ``tutor_availability_change_requests`` (``staff``): ``tutor_user_id`` and
  ``requested_by_user_id`` (``users``, ``RESTRICT``); ``kind`` ``weekly`` |
  ``exception_create`` | ``exception_delete``; ``status`` (default
  ``pending``) with ``resolved_at`` set exactly when not pending;
  ``payload`` ``JSONB``; ``affected_session_ids`` ``UUID[]`` (at least one)
  and ``affected_sessions`` ``JSONB`` (the first 20 in display form);
  ``approval_request_id`` (no foreign key); ``rejection_reason`` exactly
  when ``rejected``; versioned. The CHECKs are
  ``staff.models.CHANGE_REQUEST_CHECKS``, copied here so that later edits
  of the model never change this revision. Index ``(tutor_user_id,
  status)``.
- ``class_session_reassignment_flags`` (``scheduling``):
  ``class_session_id`` (``class_sessions``), ``tutor_user_id`` and
  ``created_by_user_id`` (``users``), all ``RESTRICT``; ``reason``
  ``availability_change``; ``source_id`` (the change request, no foreign
  key); ``uq_class_session_reassignment_flags_source`` on ``(class_session_id,
  tutor_user_id, source_id)``.

No time-zone dependency: the payload holds local dates and times (#45), and
flags follow ``class_sessions``. Downgrading drops both tables.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUESTS: Final = "tutor_availability_change_requests"
FLAGS: Final = "class_session_reassignment_flags"

REQUEST_CHECKS: Final = {
    "kind_valid": "kind IN ('weekly', 'exception_create', 'exception_delete')",
    "status_valid": "status IN ('pending', 'approved', 'rejected', 'cancelled')",
    "resolved_iff_not_pending": "(status = 'pending') = (resolved_at IS NULL)",
    "affected_not_empty": "cardinality(affected_session_ids) >= 1",
    "reason_iff_rejected": ("(status = 'rejected') = (rejection_reason IS NOT NULL)"),
}


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="RESTRICT",
    )


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
        REQUESTS,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("affected_session_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("affected_sessions", postgresql.JSONB(), nullable=False),
        sa.Column("approval_request_id", sa.UUID(), nullable=False),
        sa.Column("rejection_reason", sa.String(length=1000), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_standard_columns(),
        *(
            sa.CheckConstraint(condition, name=op.f(f"ck_{REQUESTS}_{name}"))
            for name, condition in REQUEST_CHECKS.items()
        ),
        _fk(REQUESTS, "tutor_user_id", "users"),
        _fk(REQUESTS, "requested_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{REQUESTS}")),
    )
    op.create_index(
        "ix_tutor_availability_change_requests_tutor_user_id_status",
        REQUESTS,
        ["tutor_user_id", "status"],
    )
    op.create_table(
        FLAGS,
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            "reason IN ('availability_change')",
            name=op.f(f"ck_{FLAGS}_reason_valid"),
        ),
        _fk(FLAGS, "class_session_id", "class_sessions"),
        _fk(FLAGS, "tutor_user_id", "users"),
        _fk(FLAGS, "created_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{FLAGS}")),
        sa.UniqueConstraint(
            "class_session_id",
            "tutor_user_id",
            "source_id",
            name="uq_class_session_reassignment_flags_source",
        ),
    )


def downgrade() -> None:
    op.drop_table(FLAGS)
    op.drop_table(REQUESTS)
