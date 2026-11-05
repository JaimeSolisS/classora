"""Administration: the receipt message on ``business_settings``.

Revision ID: 0087
Revises: 0086
Create Date: 2026-11-05

M8-04 (#121 B1, E4, E6; ADR-0015). Two nullable columns on the settings
record: ``receipt_message_es`` and ``receipt_message_en``
(``VARCHAR(500)``), the center's message printed on new receipts of that
language (template v2 freezes it into the snapshot). ``NULL`` means no
message. Each has two CHECKs: trimmed (``= btrim(...)``) and not blank
(``~ '[^[:space:]]'``).

No row changes (both start ``NULL``). Downgrading drops the two columns
(the messages with them); issued receipts keep their frozen copy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087"
down_revision: str | Sequence[str] | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "business_settings"
COLUMNS = ("receipt_message_es", "receipt_message_en")
MAX_LENGTH = 500


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(
            TABLE, sa.Column(column, sa.String(length=MAX_LENGTH), nullable=True)
        )
        op.create_check_constraint(
            op.f(f"ck_{TABLE}_{column}_trimmed"), TABLE, f"{column} = btrim({column})"
        )
        op.create_check_constraint(
            op.f(f"ck_{TABLE}_{column}_not_blank"), TABLE, f"{column} ~ '[^[:space:]]'"
        )


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column(TABLE, column)
