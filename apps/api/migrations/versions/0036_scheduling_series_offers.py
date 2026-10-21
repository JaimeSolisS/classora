"""Scheduling: series-scope assignment offers and the series assignment.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-21

M4-07b (#233 A1, A2; ADR-0005 D3).

- ``assignment_offers``: the ``scope`` CHECK takes ``series`` too; new
  ``class_series_id`` (``class_series``, ``ON DELETE RESTRICT``, indexed)
  and ``from_date`` (``DATE``), each set exactly for a ``series`` offer
  (two CHECKs); ``uq_assignment_offers_pending_series``: at most one
  ``pending`` offer per series.
- ``class_series``: ``assignment_status`` (``pending`` | ``accepted``) and
  ``assignment_from`` (``DATE``), both set exactly when ``tutor_user_id``
  is (two CHECKs).

**Backfill:** a series that already has a tutor gets ``assignment_status =
'pending'`` and ``assignment_from = starts_on``. No offer rows are made
(only test and development databases have such series).

**Downgrade:** deletes the ``series`` offers and their covered-session rows
first, then drops the columns and restores the #52 CHECK.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OFFERS: Final = "assignment_offers"
SERIES: Final = "class_series"

OFFER_CHECKS: Final = {
    "series_iff_series_id": "(scope = 'series') = (class_series_id IS NOT NULL)",
    "series_iff_from_date": "(scope = 'series') = (from_date IS NOT NULL)",
}
SERIES_CHECKS: Final = {
    "assignment_status_valid": "assignment_status IN ('accepted', 'pending')",
    "assignment_iff_tutor": "(tutor_user_id IS NULL) = (assignment_status IS NULL)",
    "assignment_from_iff_status": (
        "(assignment_status IS NULL) = (assignment_from IS NULL)"
    ),
}


def upgrade() -> None:
    op.drop_constraint(op.f("ck_assignment_offers_scope_valid"), OFFERS, type_="check")
    op.create_check_constraint(
        op.f("ck_assignment_offers_scope_valid"),
        OFFERS,
        "scope IN ('series', 'session')",
    )
    op.add_column(OFFERS, sa.Column("class_series_id", sa.UUID(), nullable=True))
    op.add_column(OFFERS, sa.Column("from_date", sa.Date(), nullable=True))
    op.create_foreign_key(
        op.f("fk_assignment_offers_class_series_id_class_series"),
        OFFERS,
        SERIES,
        ["class_series_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_assignment_offers_class_series_id"),
        OFFERS,
        ["class_series_id"],
        unique=False,
    )
    op.create_index(
        "uq_assignment_offers_pending_series",
        OFFERS,
        ["class_series_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    for name, condition in OFFER_CHECKS.items():
        op.create_check_constraint(
            op.f(f"ck_assignment_offers_{name}"), OFFERS, condition
        )

    op.add_column(
        SERIES, sa.Column("assignment_status", sa.String(length=20), nullable=True)
    )
    op.add_column(SERIES, sa.Column("assignment_from", sa.Date(), nullable=True))
    op.execute(
        "UPDATE class_series SET assignment_status = 'pending',"
        " assignment_from = starts_on WHERE tutor_user_id IS NOT NULL"
    )
    for name, condition in SERIES_CHECKS.items():
        op.create_check_constraint(op.f(f"ck_class_series_{name}"), SERIES, condition)


def downgrade() -> None:
    for name in SERIES_CHECKS:
        op.drop_constraint(op.f(f"ck_class_series_{name}"), SERIES, type_="check")
    op.drop_column(SERIES, "assignment_from")
    op.drop_column(SERIES, "assignment_status")

    op.execute(
        "DELETE FROM assignment_offer_sessions WHERE offer_id IN"
        " (SELECT id FROM assignment_offers WHERE scope = 'series')"
    )
    op.execute("DELETE FROM assignment_offers WHERE scope = 'series'")
    for name in OFFER_CHECKS:
        op.drop_constraint(op.f(f"ck_assignment_offers_{name}"), OFFERS, type_="check")
    op.drop_index(
        "uq_assignment_offers_pending_series",
        table_name=OFFERS,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(op.f("ix_assignment_offers_class_series_id"), table_name=OFFERS)
    op.drop_constraint(
        op.f("fk_assignment_offers_class_series_id_class_series"),
        OFFERS,
        type_="foreignkey",
    )
    op.drop_column(OFFERS, "from_date")
    op.drop_column(OFFERS, "class_series_id")
    op.drop_constraint(op.f("ck_assignment_offers_scope_valid"), OFFERS, type_="check")
    op.create_check_constraint(
        op.f("ck_assignment_offers_scope_valid"), OFFERS, "scope IN ('session')"
    )
