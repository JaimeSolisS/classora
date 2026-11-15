"""Payroll: a returned period keeps its reason.

Revision ID: 0079
Revises: 0078
Create Date: 2026-11-02

M7-07 (#110 A, E7; ADR-0017 E10, its #110 notes; ``_docs/data-model.md`` §11.4).

``payroll_periods`` gains ``returned_at``, ``returned_by_user_id`` and
``return_reason``. ``approval_requests.reason`` can only be written through a
decision that needs ``payroll.finalize``, which Finance doesn't hold, and the
reason is free text that can state an amount, so it stays out of the audit log.

``ck_payroll_periods_returned_fields``: the three are all set or all ``NULL``,
and set only while the period is ``open``, so the next ``prepare`` clears them.

No rows change. Downgrading drops the columns.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0079"
down_revision: str | Sequence[str] | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERIODS: Final = "payroll_periods"
RETURNED_BY_KEY: Final = "fk_payroll_periods_returned_by_user_id_users"
RETURNED_FIELDS: Final = "ck_payroll_periods_returned_fields"
RETURNED_CONDITION: Final = (
    "(returned_at IS NULL AND returned_by_user_id IS NULL"
    " AND return_reason IS NULL)"
    " OR (status = 'open' AND returned_at IS NOT NULL"
    " AND returned_by_user_id IS NOT NULL AND return_reason IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column(
        PERIODS, sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(PERIODS, sa.Column("returned_by_user_id", sa.UUID(), nullable=True))
    op.add_column(
        PERIODS, sa.Column("return_reason", sa.String(length=1000), nullable=True)
    )
    op.create_foreign_key(
        RETURNED_BY_KEY,
        PERIODS,
        "users",
        ["returned_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(op.f(RETURNED_FIELDS), PERIODS, RETURNED_CONDITION)


def downgrade() -> None:
    op.drop_constraint(op.f(RETURNED_FIELDS), PERIODS, type_="check")
    op.drop_constraint(RETURNED_BY_KEY, PERIODS, type_="foreignkey")
    op.drop_column(PERIODS, "return_reason")
    op.drop_column(PERIODS, "returned_by_user_id")
    op.drop_column(PERIODS, "returned_at")
