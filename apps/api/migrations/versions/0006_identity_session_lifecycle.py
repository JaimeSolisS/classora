"""Identity: session expiry columns on ``auth_sessions``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18

M2-07 (#25; ADR-0007 D8). Each session stores its own limits, fixed at login
from the user's role class:

- ``expires_at`` (``TIMESTAMPTZ NOT NULL``): the absolute expiry;
- ``idle_timeout_seconds`` (``INTEGER NOT NULL``, CHECK ``> 0``): the idle
  limit, counted from ``last_seen_at``.

Existing rows get the strictest class (staff: 12 hours absolute from
``created_at``, 30 minutes idle) before the columns become ``NOT NULL``.
Downgrading drops exactly the two columns and the CHECK. Adds no index and no
extension.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The staff class (ADR-0007 D8), the strictest: fixed here, not imported, so
# the migration never changes when code constants do.
BACKFILL_ABSOLUTE = "12 hours"
BACKFILL_IDLE_SECONDS = 1800


def upgrade() -> None:
    op.add_column(
        "auth_sessions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "auth_sessions",
        sa.Column("idle_timeout_seconds", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE auth_sessions"
            " SET expires_at = created_at + CAST(:absolute AS interval),"
            " idle_timeout_seconds = :idle"
        ).bindparams(absolute=BACKFILL_ABSOLUTE, idle=BACKFILL_IDLE_SECONDS)
    )
    op.alter_column("auth_sessions", "expires_at", nullable=False)
    op.alter_column("auth_sessions", "idle_timeout_seconds", nullable=False)
    op.create_check_constraint(
        op.f("ck_auth_sessions_idle_timeout_seconds_positive"),
        "auth_sessions",
        "idle_timeout_seconds > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_auth_sessions_idle_timeout_seconds_positive"),
        "auth_sessions",
        type_="check",
    )
    op.drop_column("auth_sessions", "idle_timeout_seconds")
    op.drop_column("auth_sessions", "expires_at")
