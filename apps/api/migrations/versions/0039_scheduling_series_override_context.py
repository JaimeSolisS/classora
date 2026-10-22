"""Scheduling: ``class_session_overrides.class_series_id`` (series override
context).

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-22

M4-09c (#236 D1; ADR-0005 D4). A series create or edit that overrides soft
conflicts stores one ``class_session_overrides`` row per overridden
occurrence; each row keeps its ``class_session_id`` (still ``NOT NULL``) and
records the series the write targeted (the new series after a split) in a
nullable **context** column:

- ``class_series_id`` ``UUID NULL``, foreign key to ``class_series``
  ``ON DELETE RESTRICT``, indexed.

The codes and reason CHECKs and the append-only trigger
(``class_session_overrides_immutable``, 0033) are unchanged: adding a column
is DDL and doesn't fire it, and existing rows get ``NULL``. Downgrading drops
the column with its index and key.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "class_session_overrides"
COLUMN: Final = "class_series_id"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_class_session_overrides_class_series_id_class_series"),
        TABLE,
        "class_series",
        [COLUMN],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_class_session_overrides_class_series_id"),
        TABLE,
        [COLUMN],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_class_session_overrides_class_series_id"), table_name=TABLE)
    op.drop_constraint(
        op.f("fk_class_session_overrides_class_series_id_class_series"),
        TABLE,
        type_="foreignkey",
    )
    op.drop_column(TABLE, COLUMN)
