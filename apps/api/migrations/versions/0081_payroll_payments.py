"""Payroll: payments and their reversals.

Revision ID: 0081
Revises: 0080
Create Date: 2026-11-03

M7-09 (#112 A; ADR-0017 E12, E13; ``_docs/data-model.md`` §11.10, §11.11, §14.1,
§16).

- ``payroll_payments``: money paid to a tutor outside the platform, against a
  finalized period. ``amount > 0``; ``reference`` required and ``note``
  optional, trimmed, 1 to 100 and 1 to 1000 characters (they are the proof: no
  file). ``method_code`` references ``payment_methods (code)``. Indexed by
  period and by ``paid_on`` (§16).
- ``payroll_payment_reversals``: at most one per payment (the UNIQUE), with a
  trimmed reason of 1 to 1000 characters.

Both are append-only, the migration 0004 pattern: ``<table>_append_only()``
refuses ``UPDATE``, ``DELETE`` and ``TRUNCATE`` with 42501. Every foreign key is
``ON DELETE RESTRICT``. The duplicate rule (#112 E2, E3) and the cap (P7) are
checked by the application under the period lock: no database key, because a
reversed payment frees its method and reference.

No rows are inserted. Downgrading refuses while a payment exists (money rows are
never dropped silently); otherwise it restores the 0080 shape. The permission
``payroll.payment.record`` is seeded by ``make seed`` (the identity catalog).
"""

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op

revision: str = "0081"
down_revision: str | Sequence[str] | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAYMENTS: Final = "payroll_payments"
REVERSALS: Final = "payroll_payment_reversals"
REVERSAL_KEY: Final = "uq_payroll_payment_reversals_payroll_payment_id"
REVERSAL_FK: Final = "fk_payroll_payment_reversals_payment"

REFUSE_DOWNGRADE: Final = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM payroll_payments) THEN
        RAISE EXCEPTION 'payroll_payments has rows: money is never dropped'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
END;
$$
"""


def _trimmed(column: str, maximum: int) -> str:
    return (
        f"char_length({column}) BETWEEN 1 AND {maximum} AND {column} = btrim({column})"
    )


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{table}_{column}_{target.split('.')[0]}"),
        ondelete="RESTRICT",
    )


def _standard_columns() -> list[sa.Column[Any]]:
    return [
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
    ]


def _append_only(table: str) -> None:
    op.execute(
        f"""
CREATE FUNCTION {table}_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{table} is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE"
        f" ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_append_only()"
    )
    op.execute(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {table}_append_only()"
    )


def _drop_append_only(table: str) -> None:
    op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
    op.execute(f"DROP TRIGGER {table}_no_update_delete ON {table}")
    op.execute(f"DROP FUNCTION {table}_append_only()")


def upgrade() -> None:
    op.create_table(
        PAYMENTS,
        sa.Column("payroll_period_id", sa.UUID(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("method_code", sa.String(length=32), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint("amount > 0", name=op.f(f"ck_{PAYMENTS}_amount_positive")),
        sa.CheckConstraint(
            _trimmed("reference", 100), name=op.f(f"ck_{PAYMENTS}_reference_trimmed")
        ),
        sa.CheckConstraint(
            _trimmed("note", 1000), name=op.f(f"ck_{PAYMENTS}_note_trimmed")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PAYMENTS}")),
        _fk(PAYMENTS, "payroll_period_id", "payroll_periods.id"),
        _fk(PAYMENTS, "method_code", "payment_methods.code"),
        _fk(PAYMENTS, "recorded_by_user_id", "users.id"),
    )
    op.create_index(
        op.f(f"ix_{PAYMENTS}_payroll_period_id"), PAYMENTS, ["payroll_period_id"]
    )
    op.create_index(op.f(f"ix_{PAYMENTS}_paid_on"), PAYMENTS, ["paid_on"])
    _append_only(PAYMENTS)

    op.create_table(
        REVERSALS,
        sa.Column("payroll_payment_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reversed_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            _trimmed("reason", 1000), name=op.f(f"ck_{REVERSALS}_reason_trimmed")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{REVERSALS}")),
        # Named: the convention's name is beyond PostgreSQL's 63 characters.
        sa.ForeignKeyConstraint(
            ["payroll_payment_id"],
            [f"{PAYMENTS}.id"],
            name=REVERSAL_FK,
            ondelete="RESTRICT",
        ),
        _fk(REVERSALS, "reversed_by_user_id", "users.id"),
        sa.UniqueConstraint("payroll_payment_id", name=REVERSAL_KEY),
    )
    _append_only(REVERSALS)


def downgrade() -> None:
    op.execute(REFUSE_DOWNGRADE)
    _drop_append_only(REVERSALS)
    op.drop_table(REVERSALS)
    _drop_append_only(PAYMENTS)
    op.drop_table(PAYMENTS)
