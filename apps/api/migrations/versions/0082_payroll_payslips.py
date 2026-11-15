"""Payroll: payslips.

Revision ID: 0082
Revises: 0081
Create Date: 2026-11-03

M7-10 (#113 A1; ADR-0017 E14; ``_docs/data-model.md`` §11.12, §5.1, §6).

- ``payslips``: one per finalized period that has lines, issued by
  ``finalize`` in its transaction (``payroll_period_id`` UNIQUE), numbered
  ``P-YYYY-NNNNNN`` from ``number_series`` ``P`` (``number`` UNIQUE,
  CHECKed), with the composite foreign key ``(payroll_period_id,
  tutor_user_id)`` → ``payroll_periods (id, tutor_user_id)`` (RESTRICT),
  ``issued_at``, ``language`` (``es``/``en``), ``template_version > 0`` and
  the frozen ``snapshot`` (a JSON object). Index ``(tutor_user_id,
  issued_at)``. The §5.1 trigger pair (``payslips_append_only()``, SQLSTATE
  42501) refuses ``UPDATE``, ``DELETE`` and ``TRUNCATE``.

Periods finalized before this revision get no payslip (no backfill, ADR-0017
E14). ``number_series`` already allows ``P`` (migration 0068).

Downgrading refuses while a payslip exists (issued documents are never dropped
silently, the 0080/0081 pattern); otherwise it drops the triggers, their
function and the table.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082"
down_revision: str | Sequence[str] | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAYSLIPS: Final = "payslips"
INDEX: Final = "ix_payslips_tutor_user_id_issued_at"

REFUSE_DOWNGRADE: Final = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM payslips) THEN
        RAISE EXCEPTION 'payslips has rows: issued payslips are never dropped'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
END;
$$
"""

APPEND_ONLY_FUNCTION: Final = f"""
CREATE FUNCTION {PAYSLIPS}_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{PAYSLIPS} is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def _check(name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{PAYSLIPS}_{name}"))


def upgrade() -> None:
    op.create_table(
        PAYSLIPS,
        sa.Column("number", sa.String(length=16), nullable=False),
        sa.Column("payroll_period_id", sa.UUID(), nullable=False),
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("template_version", sa.SmallInteger(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _check("number_format", "number ~ '^P-[0-9]{4}-[0-9]{6}$'"),
        _check("language", "language IN ('es', 'en')"),
        _check("template_version_positive", "template_version > 0"),
        _check("snapshot_object", "jsonb_typeof(snapshot) = 'object'"),
        sa.ForeignKeyConstraint(
            ["payroll_period_id", "tutor_user_id"],
            ["payroll_periods.id", "payroll_periods.tutor_user_id"],
            name="fk_payslips_period_tutor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PAYSLIPS}")),
        sa.UniqueConstraint("number", name="uq_payslips_number"),
        sa.UniqueConstraint("payroll_period_id", name="uq_payslips_payroll_period_id"),
    )
    op.create_index(INDEX, PAYSLIPS, ["tutor_user_id", "issued_at"])
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {PAYSLIPS}_no_update_delete BEFORE UPDATE OR DELETE"
        f" ON {PAYSLIPS} FOR EACH ROW EXECUTE FUNCTION {PAYSLIPS}_append_only()"
    )
    op.execute(
        f"CREATE TRIGGER {PAYSLIPS}_no_truncate BEFORE TRUNCATE ON {PAYSLIPS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {PAYSLIPS}_append_only()"
    )


def downgrade() -> None:
    op.execute(REFUSE_DOWNGRADE)
    op.execute(f"DROP TRIGGER {PAYSLIPS}_no_truncate ON {PAYSLIPS}")
    op.execute(f"DROP TRIGGER {PAYSLIPS}_no_update_delete ON {PAYSLIPS}")
    op.execute(f"DROP FUNCTION {PAYSLIPS}_append_only()")
    op.drop_index(INDEX, table_name=PAYSLIPS)
    op.drop_table(PAYSLIPS)
