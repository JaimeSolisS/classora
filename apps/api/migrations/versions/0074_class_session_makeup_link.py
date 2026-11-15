"""Class sessions: the makeup link.

Revision ID: 0074
Revises: 0073
Create Date: 2026-11-01

M7-04a (#423 A; ADR-0017 E5; ``_docs/data-model.md`` §11.13).

- ``class_sessions.makeup_of_session_id``: a nullable self-reference,
  ``ON DELETE RESTRICT``, with no default and no data step. Every existing
  row reads ``NULL`` (no backfill, #423 E8) and no ``UPDATE`` runs, so
  ``version``, ``updated_at`` and the audit log don't change.
- ``uq_class_sessions_makeup_of_session_id``: one **live** makeup per
  cancelled session. A cancelled makeup keeps its link and leaves the index
  (#423 E5); ``cancelled`` is final, so no row re-enters it.
- ``makeup_is_one_off_individual``: no other writer can put the link on a
  group, course or series row.

**Previous image:** it doesn't name the column and ``NULL`` is what it would
write, so expand-and-contract holds. The foreign key and the index take brief
table locks, like earlier ``class_sessions`` revisions; no ``CONCURRENTLY``,
because Alembic runs in a transaction here.

Downgrading drops the index, the CHECK, the foreign key and the column. The
links are lost; ordinary session data isn't.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: str | Sequence[str] | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "class_sessions"
COLUMN: Final = "makeup_of_session_id"
FOREIGN_KEY: Final = "fk_class_sessions_makeup_of_session_id_class_sessions"
LIVE_MAKEUP_UNIQUE: Final = "uq_class_sessions_makeup_of_session_id"
ONE_OFF_INDIVIDUAL: Final = "ck_class_sessions_makeup_is_one_off_individual"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f(FOREIGN_KEY), TABLE, TABLE, [COLUMN], ["id"], ondelete="RESTRICT"
    )
    op.create_check_constraint(
        op.f(ONE_OFF_INDIVIDUAL),
        TABLE,
        "makeup_of_session_id IS NULL OR (kind = 'individual' AND series_id IS NULL)",
    )
    op.create_index(
        LIVE_MAKEUP_UNIQUE,
        TABLE,
        [COLUMN],
        unique=True,
        postgresql_where=sa.text(
            "makeup_of_session_id IS NOT NULL AND state <> 'cancelled'"
        ),
    )


def downgrade() -> None:
    op.drop_index(LIVE_MAKEUP_UNIQUE, table_name=TABLE)
    op.drop_constraint(op.f(ONE_OFF_INDIVIDUAL), TABLE, type_="check")
    op.drop_constraint(op.f(FOREIGN_KEY), TABLE, type_="foreignkey")
    op.drop_column(TABLE, COLUMN)
