"""Billing: the proration snapshot of enrollment charges.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-28

M6-07 (#92 A1, A2, J3, J8; ``_docs/data-model.md`` §3.9).

A group or course enrollment fee that starts late is prorated by sessions
(J3): ``list_amount = fee × remaining / total``, rounded once half away
from zero (``Money.prorate``), **before** the discounts. For a cohort,
``total`` is the snapshot's ``session_count`` and ``remaining`` the
cohort's occurrences that start after the enrollment instant and are not
``cancelled`` or ``skipped_closure`` (materialized rows and projected ones
beyond the horizon), clamped to ``[0, total]``. A group's flat fee is
prorated only when every series of the group is finite (``total`` = its
occurrences that are not cancelled or skipped); an open-ended group pays
the full fee.

The two counts are snapshotted on the charge (J8), so a later view (#101,
receipts, the family portal) shows "7 of 10 sessions" without the session
history as it was at posting:

- ``charges.proration_remaining SMALLINT NULL`` and
  ``charges.proration_total SMALLINT NULL``;
- ``ck_charges_proration``: both ``NULL``, or the kind is
  ``group_enrollment`` or ``cohort_enrollment`` with ``proration_total >=
  1`` and ``proration_remaining BETWEEN 0 AND proration_total``.

Every cohort fee and every finite-group fee sets both; an open-ended group
fee leaves both ``NULL``. The append-only row triggers of ``charges`` fire
on ``UPDATE``/``DELETE``/``TRUNCATE`` only, so they don't block the DDL.
Downgrading drops the CHECK and the two columns.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: str | Sequence[str] | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHARGES: Final = "charges"
PRORATION_CHECK: Final = (
    "num_nulls(proration_remaining, proration_total) = 2"
    " OR (num_nulls(proration_remaining, proration_total) = 0"
    " AND kind IN ('group_enrollment', 'cohort_enrollment')"
    " AND proration_total >= 1"
    " AND proration_remaining BETWEEN 0 AND proration_total)"
)


def upgrade() -> None:
    op.add_column(
        CHARGES, sa.Column("proration_remaining", sa.SmallInteger(), nullable=True)
    )
    op.add_column(
        CHARGES, sa.Column("proration_total", sa.SmallInteger(), nullable=True)
    )
    op.create_check_constraint(op.f("ck_charges_proration"), CHARGES, PRORATION_CHECK)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_charges_proration"), CHARGES, type_="check")
    op.drop_column(CHARGES, "proration_total")
    op.drop_column(CHARGES, "proration_remaining")
