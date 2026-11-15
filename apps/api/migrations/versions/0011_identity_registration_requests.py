"""Identity: the ``registration_requests`` table (self-registration).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19

M2-11b (#169; ADR-0007 D13). One row per self-registration request: the
``pending`` user it created (unique, ``ON DELETE RESTRICT``), the requested
role (``guardian`` or ``student`` only), the status (``pending``,
``approved`` or ``rejected``) and, once decided, when and by whom
(``decided_by_user_id`` refers to ``users`` with ``ON DELETE RESTRICT``). A
CHECK keeps ``status = 'pending'`` exactly when ``decided_at`` and
``decided_by_user_id`` are both ``NULL`` (a decided request has both). An
index on ``(status, created_at, id)`` serves the review list. Holds no secret.
Adds no extension. Downgrading drops exactly this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registration_requests",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("requested_role", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
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
            "requested_role IN ('guardian', 'student')",
            name=op.f("ck_registration_requests_requested_role_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_registration_requests_status_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL AND decided_by_user_id IS NULL)"
            " OR (status <> 'pending' AND decided_at IS NOT NULL"
            " AND decided_by_user_id IS NOT NULL)",
            name=op.f("ck_registration_requests_decided_iff_not_pending"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_registration_requests_decided_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_registration_requests_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registration_requests")),
        sa.UniqueConstraint("user_id", name=op.f("uq_registration_requests_user_id")),
    )
    op.create_index(
        "ix_registration_requests_status_created_at_id",
        "registration_requests",
        ["status", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_registration_requests_status_created_at_id",
        table_name="registration_requests",
    )
    op.drop_table("registration_requests")
