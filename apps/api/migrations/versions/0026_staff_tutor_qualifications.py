"""Staff: ``tutor_qualifications``.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-20

M3-08 (#44; spec §3 "subject + education-level qualifications, approved by an
administrator", §16 "explicit qualification relationships"). Audited by the
ORM and versioned.

- ``tutor_qualifications``: ``tutor_user_id`` (``users``) and ``subject_id``
  (``subjects``; the level follows from the subject), both ``ON DELETE
  RESTRICT`` and indexed; ``status VARCHAR(20)`` (``pending`` by default,
  ``approved``, ``rejected``, ``cancelled``); ``decided_at``,
  ``decided_by_user_id`` (``users``) and ``rejection_reason VARCHAR(1000)``.
- CHECKs: ``decided_at`` is set exactly for ``approved``, ``rejected`` and
  ``cancelled``; ``rejection_reason`` exactly for ``rejected``.
- The partial unique index ``uq_tutor_qualifications_active`` on
  ``(tutor_user_id, subject_id) WHERE status IN ('pending', 'approved')``:
  no duplicate active qualification; rejected and cancelled rows don't block
  a new request.

No rows are inserted. Downgrading drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tutor_qualifications"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=1000), nullable=True),
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
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name=op.f(f"ck_{TABLE}_status_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected', 'cancelled'))"
            " = (decided_at IS NOT NULL)",
            name=op.f(f"ck_{TABLE}_decided_iff_final"),
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name=op.f(f"ck_{TABLE}_reason_iff_rejected"),
        ),
        sa.ForeignKeyConstraint(
            ["tutor_user_id"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_tutor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f(f"fk_{TABLE}_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_decided_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    op.create_index(
        op.f(f"ix_{TABLE}_tutor_user_id"), TABLE, ["tutor_user_id"], unique=False
    )
    op.create_index(op.f(f"ix_{TABLE}_subject_id"), TABLE, ["subject_id"], unique=False)
    op.create_index(
        "uq_tutor_qualifications_active",
        TABLE,
        ["tutor_user_id", "subject_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'approved')"),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
