"""Administration: ``approval_requests``, backfilled from registrations.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-20

M3-04 (#40; spec §9). Audited by the ORM and versioned.

- ``approval_requests``: ``request_type VARCHAR(64)`` (dotted code, CHECK
  ``ck_approval_requests_request_type_format``), ``subject_id UUID`` (no
  foreign key; unique with the type: ``uq_approval_requests_request_type``),
  ``requester_user_id``, ``assignee_user_id`` and ``resolved_by_user_id``
  (``users``, ``ON DELETE RESTRICT``), ``status`` (default ``pending``),
  ``priority`` (default ``normal``), ``resolved_at`` (set exactly for the
  final states), ``reason VARCHAR(1000)`` (required for
  ``changes_requested``). Indexes for the inbox ``(status, priority,
  created_at, id)``, ``(assignee_user_id, status)`` and
  ``(requester_user_id)``.
- **Backfill** (A2): one ``identity.registration`` approval request per
  ``registration_requests`` row, with its status, ``decided_at`` and
  ``decided_by_user_id`` as ``resolved_*``, its ``created_at``, priority
  ``normal`` and version 1. No audit rows. ``registration_requests`` keeps its
  columns (expand-and-contract: the identity handler keeps them in step).

Downgrading drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "approval_requests"

BACKFILL = """
INSERT INTO approval_requests (
    id, request_type, subject_id, requester_user_id, status, priority,
    resolved_at, resolved_by_user_id, created_at, updated_at, version
)
SELECT
    uuidv7(), 'identity.registration', r.id, r.user_id, r.status, 'normal',
    r.decided_at, r.decided_by_user_id, r.created_at, r.updated_at, 1
FROM registration_requests AS r
ORDER BY r.created_at, r.id
"""


def _user_fk(column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        ["users.id"],
        name=op.f(f"fk_{TABLE}_{column}_users"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("request_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("requester_user_id", sa.UUID(), nullable=True),
        sa.Column("assignee_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.String(length=10),
            server_default=sa.text("'normal'"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=True),
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
        sa.CheckConstraint(
            "request_type ~ '^[a-z_]+(\\.[a-z_]+)+$'",
            name=op.f(f"ck_{TABLE}_request_type_format"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'changes_requested',"
            " 'cancelled')",
            name=op.f(f"ck_{TABLE}_status_valid"),
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high')",
            name=op.f(f"ck_{TABLE}_priority_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected', 'cancelled'))"
            " = (resolved_at IS NOT NULL)",
            name=op.f(f"ck_{TABLE}_resolved_iff_final"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'rejected')"
            " OR resolved_by_user_id IS NOT NULL",
            name=op.f(f"ck_{TABLE}_resolver_unless_cancelled"),
        ),
        sa.CheckConstraint(
            "status <> 'changes_requested' OR reason IS NOT NULL",
            name=op.f(f"ck_{TABLE}_reason_for_changes"),
        ),
        _user_fk("requester_user_id"),
        _user_fk("assignee_user_id"),
        _user_fk("resolved_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint(
            "request_type", "subject_id", name=op.f(f"uq_{TABLE}_request_type")
        ),
    )
    op.create_index(
        "ix_approval_requests_status_priority_created_at_id",
        TABLE,
        ["status", "priority", "created_at", "id"],
    )
    op.create_index(
        "ix_approval_requests_assignee_user_id_status",
        TABLE,
        ["assignee_user_id", "status"],
    )
    op.create_index(
        "ix_approval_requests_requester_user_id", TABLE, ["requester_user_id"]
    )
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_table(TABLE)
