"""Billing: the import reference of an opening balance.

Revision ID: 0089
Revises: 0088
Create Date: 2026-11-05

M8-06 (#123 D1, D2, E6; data-model §3.9; architecture ISSUE-L1). An
imported opening balance is an ordinary ``manual`` charge that keeps the
import job and CSV line it came from, so it can never be posted twice.

- ``charges.import_job_id UUID NULL`` (FK ``import_jobs(id)`` ``ON DELETE
  RESTRICT``) and ``charges.import_row_number INTEGER NULL``;
- ``ck_charges_import_ref``: both columns or neither, and only on a
  ``manual`` charge;
- ``uq_charges_import_row (import_job_id, import_row_number)``: one charge per
  job line (a worker retry);
- ``uq_charges_import_line (student_id, service_date, lower(description))
  WHERE import_job_id IS NOT NULL``: one imported charge per line across
  jobs (the same file imported again). Charges posted by hand are never
  matched.

Existing rows keep ``NULL``. The ledger view is unchanged.

Downgrading drops them, and **refuses while any charge has an import
reference** (``charges`` is append-only, so those rows can't be removed
first, and dropping the columns would silently drop the double-posting
guard).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0089"
down_revision: str | Sequence[str] | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHARGES: Final = "charges"

REFUSE_DOWNGRADE: Final = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM charges WHERE import_job_id IS NOT NULL) THEN
        RAISE EXCEPTION 'imported charges exist; revision 0089 cannot be downgraded'
            USING ERRCODE = 'object_in_use';
    END IF;
END
$$
"""


def upgrade() -> None:
    op.add_column(
        CHARGES,
        sa.Column("import_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(CHARGES, sa.Column("import_row_number", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_charges_import_job_id_import_jobs"),
        CHARGES,
        "import_jobs",
        ["import_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_charges_import_ref"),
        CHARGES,
        "(import_job_id IS NULL) = (import_row_number IS NULL)"
        " AND (import_job_id IS NULL OR kind = 'manual')",
    )
    op.create_unique_constraint(
        "uq_charges_import_row", CHARGES, ["import_job_id", "import_row_number"]
    )
    op.create_index(
        "uq_charges_import_line",
        CHARGES,
        ["student_id", "service_date", sa.text("lower(description)")],
        unique=True,
        postgresql_where=sa.text("import_job_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute(REFUSE_DOWNGRADE)
    op.drop_index("uq_charges_import_line", table_name=CHARGES)
    op.drop_constraint("uq_charges_import_row", CHARGES, type_="unique")
    op.drop_constraint(op.f("ck_charges_import_ref"), CHARGES, type_="check")
    op.drop_constraint(
        op.f("fk_charges_import_job_id_import_jobs"), CHARGES, type_="foreignkey"
    )
    op.drop_column(CHARGES, "import_row_number")
    op.drop_column(CHARGES, "import_job_id")
