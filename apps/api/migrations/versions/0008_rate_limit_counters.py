"""Rate limits: the ``rate_limit_counters`` table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-18

M2-09 (#27; ADR-0008 D15). One row per rate-limit policy, key and fixed
window. The key is stored only as ``key_hash`` (SHA-256 of policy and key,
exactly 32 bytes), so no username or IP address is kept in clear text.
``(policy, key_hash, window_start)`` is unique (the upsert target);
``window_end`` is indexed for the hourly purge. No foreign keys. Downgrading
drops exactly this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_counters",
        sa.Column("policy", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
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
            "octet_length(key_hash) = 32",
            name=op.f("ck_rate_limit_counters_key_hash_length"),
        ),
        sa.CheckConstraint(
            "window_end > window_start",
            name=op.f("ck_rate_limit_counters_window_order"),
        ),
        sa.CheckConstraint(
            "count >= 0", name=op.f("ck_rate_limit_counters_count_not_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_limit_counters")),
        sa.UniqueConstraint(
            "policy",
            "key_hash",
            "window_start",
            name="uq_rate_limit_counters_policy_key_window",
        ),
    )
    op.create_index(
        "ix_rate_limit_counters_window_end",
        "rate_limit_counters",
        ["window_end"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rate_limit_counters_window_end", table_name="rate_limit_counters")
    op.drop_table("rate_limit_counters")
