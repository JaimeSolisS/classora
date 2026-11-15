"""Billing: corrections (the package_cancellation adjustment kind).

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-30

M6-15 (#100 A1, R6; ``_docs/data-model.md`` §3.10, §3.20; ADR-0006 E5,
E10).

Replaces the named CHECK ``ck_charge_adjustments_kind`` with the 0061 list
plus ``package_cancellation``: the credit a package-purchase cancellation
posts on its ``package_purchase`` charge (``source_key =
package_cancellation:<package_id>``). Nothing else changes: the reversal,
release and refund tables exist since 0061, and the package ``reversal``
transaction kind since 0066.

Downgrading restores the 0061 CHECK text. With ``package_cancellation``
adjustments present the downgrade fails loudly on the restored CHECK
(``charge_adjustments`` is append-only, so its rows are never rewritten or
removed first).
"""

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0070"
down_revision: str | Sequence[str] | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADJUSTMENTS: Final = "charge_adjustments"
KIND_0061: Final = (
    "kind IN ('duration_change', 'cancellation_credit', 'closure_credit',"
    " 'enrollment_removal', 'withdrawal', 'manual')"
)
KIND: Final = (
    "kind IN ('duration_change', 'cancellation_credit', 'closure_credit',"
    " 'enrollment_removal', 'withdrawal', 'manual', 'package_cancellation')"
)


def _replace_kind(expression: str) -> None:
    op.drop_constraint(op.f("ck_charge_adjustments_kind"), ADJUSTMENTS, type_="check")
    op.create_check_constraint(
        op.f("ck_charge_adjustments_kind"), ADJUSTMENTS, expression
    )


def upgrade() -> None:
    _replace_kind(KIND)


def downgrade() -> None:
    _replace_kind(KIND_0061)
