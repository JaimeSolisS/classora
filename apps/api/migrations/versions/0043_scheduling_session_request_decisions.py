"""Scheduling: the staff decision of a session request.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-23

M4-15 (#60 A; spec W2.3-W2.4; ADR-0005 D2). ``session_requests`` gains:

- ``class_session_id`` ``UUID NULL``, foreign key to ``class_sessions``
  ``ON DELETE RESTRICT``: the session a confirmation created. One request per
  session: ``uq_session_requests_class_session_id`` (unique, partial ``WHERE
  class_session_id IS NOT NULL``);
- ``rejection_reason`` ``VARCHAR(1000) NULL`` (the #40 reason length): why
  staff rejected it, shown to the family;
- ``ck_session_requests_confirmed_iff_session``: ``(status = 'confirmed') =
  (class_session_id IS NOT NULL)``;
- ``ck_session_requests_rejected_iff_reason``: ``(status = 'rejected') =
  (rejection_reason IS NOT NULL)``.

#59 never writes ``confirmed`` or ``rejected``, so existing rows
(``pending`` / ``withdrawn``) satisfy both CHECKs without a backfill.
Downgrading drops the CHECKs, the index, the key and both columns.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "session_requests"
FOREIGN_KEY: Final = "fk_session_requests_class_session_id_class_sessions"
UNIQUE_INDEX: Final = "uq_session_requests_class_session_id"
CHECKS: Final = {
    "confirmed_iff_session": "(status = 'confirmed') = (class_session_id IS NOT NULL)",
    "rejected_iff_reason": "(status = 'rejected') = (rejection_reason IS NOT NULL)",
}


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("class_session_id", sa.UUID(), nullable=True))
    op.add_column(
        TABLE, sa.Column("rejection_reason", sa.String(length=1000), nullable=True)
    )
    op.create_foreign_key(
        op.f(FOREIGN_KEY),
        TABLE,
        "class_sessions",
        ["class_session_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        UNIQUE_INDEX,
        TABLE,
        ["class_session_id"],
        unique=True,
        postgresql_where=sa.text("class_session_id IS NOT NULL"),
    )
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, condition)


def downgrade() -> None:
    for name in CHECKS:
        op.drop_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, type_="check")
    op.drop_index(UNIQUE_INDEX, table_name=TABLE)
    op.drop_constraint(op.f(FOREIGN_KEY), TABLE, type_="foreignkey")
    op.drop_column(TABLE, "rejection_reason")
    op.drop_column(TABLE, "class_session_id")
