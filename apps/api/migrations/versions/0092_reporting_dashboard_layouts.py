"""Reporting: ``dashboard_layouts``.

Revision ID: 0092
Revises: 0091
Create Date: 2026-11-05

M8-10 (#127 A1; spec §8 customizable dashboards; architecture ISSUE-A2,
ISSUE-X3). The staff home dashboard's per-user layout: which KPI widgets a
user shows and in which order. No row means the role default.

- ``dashboard_layouts`` (not audited, not versioned: a per-user preference,
  as ``user_preferences``): ``user_id`` (``users``, ``ON DELETE RESTRICT``,
  unique), ``widgets JSONB`` (an array of 1-20 ``{widget, visible}``
  entries). No other index.

No rows are inserted. Downgrading drops the table (with every saved layout).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0092"
down_revision: str | Sequence[str] | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LAYOUTS: Final = "dashboard_layouts"


def _timestamp(name: str) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        LAYOUTS,
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("widgets", postgresql.JSONB(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "jsonb_typeof(widgets) = 'array'"
            " AND jsonb_array_length(widgets) BETWEEN 1 AND 20",
            name=op.f(f"ck_{LAYOUTS}_widgets_array"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f(f"fk_{LAYOUTS}_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{LAYOUTS}")),
        sa.UniqueConstraint("user_id", name=op.f(f"uq_{LAYOUTS}_user_id")),
    )


def downgrade() -> None:
    op.drop_table(LAYOUTS)
