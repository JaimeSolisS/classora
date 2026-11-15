"""Administration: ``notifications``.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-20

M3-05 (#41; spec §1, §9: in-app notifications). Neither audited nor
versioned.

- ``notifications``: ``recipient_user_id`` (``users``, ``ON DELETE
  RESTRICT``), ``notification_type VARCHAR(64)`` (dotted code, CHECK
  ``ck_notifications_notification_type_format``), ``params JSONB`` (default
  ``{}``, always an object: ``ck_notifications_params_object``) and
  ``read_at`` (``NULL`` while unread).
- Indexes: ``ix_notifications_recipient_created`` on ``(recipient_user_id,
  created_at DESC, id DESC)`` for the list, and the partial
  ``ix_notifications_recipient_unread`` on ``(recipient_user_id) WHERE
  read_at IS NULL`` for the unread count.

No rows are inserted. Downgrading drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "notifications"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("recipient_user_id", sa.UUID(), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "jsonb_typeof(params) = 'object'",
            name=op.f(f"ck_{TABLE}_params_object"),
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_recipient_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    op.create_index(
        "ix_notifications_recipient_created",
        TABLE,
        ["recipient_user_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_notifications_recipient_unread",
        TABLE,
        ["recipient_user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
