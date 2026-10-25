"""Academics: closeout on behalf of a tutor (overdue closeouts).

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-25

M5-10b (#301 A1; spec §2, §3 "admins reapprove", §7, §16; architecture
§4.1, ISSUE-F7).

- ``session_closeouts`` gains ``on_behalf_by_user_id`` (``UUID NULL``,
  ``fk_session_closeouts_on_behalf_by_user_id_users``, ``ON DELETE
  RESTRICT``) and ``on_behalf_reason`` (``TEXT NULL``, trimmed 1–1000:
  ``ck_session_closeouts_on_behalf_reason_valid``); they describe the latest
  submission when a reviewer made it on the tutor's behalf.
  ``ck_session_closeouts_on_behalf_together`` (both or neither) and
  ``ck_session_closeouts_on_behalf_not_tutor`` (never the tutor of record).
- ``ix_class_sessions_confirmed_ends_at``: a partial index on
  ``class_sessions (ends_at, id) WHERE state = 'confirmed'`` for the overdue
  list (confirmed sessions that have ended, oldest first).

No rows change. Downgrading drops the index, the CHECKs, the key and the
two columns.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | Sequence[str] | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLOSEOUTS: Final = "session_closeouts"
ON_BEHALF_FK: Final = "fk_session_closeouts_on_behalf_by_user_id_users"
OVERDUE_INDEX: Final = "ix_class_sessions_confirmed_ends_at"
CHECKS: Final = {
    "on_behalf_reason_valid": (
        "on_behalf_reason IS NULL OR ("
        "char_length(btrim(on_behalf_reason)) BETWEEN 1 AND 1000"
        " AND on_behalf_reason = btrim(on_behalf_reason))"
    ),
    "on_behalf_together": (
        "(on_behalf_by_user_id IS NULL) = (on_behalf_reason IS NULL)"
    ),
    "on_behalf_not_tutor": (
        "on_behalf_by_user_id IS NULL OR on_behalf_by_user_id <> tutor_user_id"
    ),
}


def upgrade() -> None:
    op.add_column(
        CLOSEOUTS, sa.Column("on_behalf_by_user_id", sa.UUID(), nullable=True)
    )
    op.add_column(CLOSEOUTS, sa.Column("on_behalf_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f(ON_BEHALF_FK),
        CLOSEOUTS,
        "users",
        ["on_behalf_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_{CLOSEOUTS}_{name}"), CLOSEOUTS, condition)
    op.create_index(
        OVERDUE_INDEX,
        "class_sessions",
        ["ends_at", "id"],
        unique=False,
        postgresql_where=sa.text("state = 'confirmed'"),
    )


def downgrade() -> None:
    op.drop_index(OVERDUE_INDEX, table_name="class_sessions")
    for name in reversed(CHECKS):
        op.drop_constraint(op.f(f"ck_{CLOSEOUTS}_{name}"), CLOSEOUTS, type_="check")
    op.drop_constraint(op.f(ON_BEHALF_FK), CLOSEOUTS, type_="foreignkey")
    op.drop_column(CLOSEOUTS, "on_behalf_reason")
    op.drop_column(CLOSEOUTS, "on_behalf_by_user_id")
