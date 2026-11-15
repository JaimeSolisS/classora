"""Billing: the billing settings singleton.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-28

M6-06 (#91 A1, A2; ``_docs/data-model.md`` §3.2, §8; ADR-0006 E1).

- ``billing_settings`` (audited, versioned): the one billing settings row
  (the ``business_settings`` pattern: ``singleton BOOLEAN`` with ``CHECK
  (singleton)`` and ``UNIQUE``). ``billing_starts_on DATE NULL`` is the
  billing start floor: while it is ``NULL`` the sweepers post nothing;
  afterwards they post only for service dates on or after it.
- The upgrade inserts the one row with ``billing_starts_on = NULL``
  (data-model §8): billing stays off until staff set the date. A raw insert
  writes no audit event (the 0020 precedent).

A local ``DATE``, not an instant: no time-zone dependency (#37 E1).
Downgrading drops the table with its row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | Sequence[str] | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    settings = op.create_table(
        "billing_settings",
        sa.Column(
            "singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("billing_starts_on", sa.Date(), nullable=True),
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
        sa.CheckConstraint(
            "singleton", name=op.f("ck_billing_settings_singleton_true")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_settings")),
        sa.UniqueConstraint("singleton", name=op.f("uq_billing_settings_singleton")),
    )
    op.execute(
        sa.insert(settings).values(singleton=True, billing_starts_on=None, version=1)
    )


def downgrade() -> None:
    op.drop_table("billing_settings")
