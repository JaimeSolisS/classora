"""Administration: ``notification_opt_outs``.

Revision ID: 0086
Revises: 0085
Create Date: 2026-11-04

M8-02 (#119 B; spec §9 "in-app notification preferences", architecture §7.2).
Neither audited nor versioned (a per-user preference, like
``user_preferences``).

- ``notification_opt_outs``: one row per optional notification type a user
  turned off (no row: on). ``user_id`` (``users``, ``ON DELETE RESTRICT``),
  ``notification_type VARCHAR(64)`` (dotted code, CHECK
  ``ck_notification_opt_outs_notification_type_format``, as on
  ``notifications``), ``UNIQUE (user_id, notification_type)``
  (``uq_notification_opt_outs_user_type``, which also serves the lookups).

No rows are inserted. Downgrading drops the table (the preferences with it).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: str | Sequence[str] | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "notification_opt_outs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
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
            "notification_type ~ '^[a-z_]+(\\.[a-z_]+)+$'",
            name=op.f(f"ck_{TABLE}_notification_type_format"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint(
            "user_id",
            "notification_type",
            name="uq_notification_opt_outs_user_type",
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
