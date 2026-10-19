"""Identity: reauthentication time on ``auth_sessions``.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18

M2-08 (#26; ADR-0007 D10). ``reauthenticated_at`` (``TIMESTAMPTZ NULL``) is
the time of the session's last password reauthentication; ``NULL`` means
never in this session. The 5-minute window is computed from it in code.
Existing rows stay ``NULL`` (no backfill). Adds no index. Downgrading drops
exactly this column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions",
        sa.Column("reauthenticated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_sessions", "reauthenticated_at")
