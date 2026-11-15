"""Identity: user preferences.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-19

M2-18 (#36; spec §10, §12 ``Preference``).

- ``user_preferences``: one row per user (``user_id`` unique, a foreign key to
  ``users`` with ``ON DELETE RESTRICT``) holding the UI ``language``
  (``es``/``en``) and the ``theme`` (``light``/``dark``/``system``). ``NULL``
  means the user never stored the value. No backfill: a row is written on the
  first save. Not audited, no ``version`` column.

Downgrading drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("theme", sa.Text(), nullable=True),
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
            "language IN ('es', 'en')",
            name=op.f("ck_user_preferences_language_valid"),
        ),
        sa.CheckConstraint(
            "theme IN ('light', 'dark', 'system')",
            name=op.f("ck_user_preferences_theme_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_preferences_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_preferences")),
        sa.UniqueConstraint("user_id", name=op.f("uq_user_preferences_user_id")),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
