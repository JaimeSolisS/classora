"""Scheduling: when a class session was first confirmed.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-23

M4-22 (#67 A1, E2; ADR-0005 D2: families see cancelled or skipped sessions
"they had already seen as confirmed").

- ``class_sessions.first_confirmed_at``: ``TIMESTAMPTZ NULL``, the first time
  the session was ``confirmed`` (set once by the ORM, never cleared).
- Backfill (conservative): ``first_confirmed_at = updated_at`` for the rows
  in ``confirmed`` or ``completed``. Every other row stays ``NULL``, so an
  existing cancelled or skipped row is not shown to families.
- ``ck_class_sessions_confirmed_has_first_confirmed_at``: ``state NOT IN
  ('completed', 'confirmed') OR first_confirmed_at IS NOT NULL``.

Downgrade drops the CHECK and the column.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | Sequence[str] | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "class_sessions"
COLUMN: Final = "first_confirmed_at"
CHECK: Final = "ck_class_sessions_confirmed_has_first_confirmed_at"
CHECK_SQL: Final = (
    "state NOT IN ('completed', 'confirmed') OR first_confirmed_at IS NOT NULL"
)


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE class_sessions SET first_confirmed_at = updated_at"
        " WHERE state IN ('confirmed', 'completed')"
    )
    op.create_check_constraint(op.f(CHECK), TABLE, CHECK_SQL)


def downgrade() -> None:
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)
