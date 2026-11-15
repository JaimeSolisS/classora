"""Finance: operating expenses, their payments, reversals and adjustments.

Revision ID: 0084
Revises: 0083
Create Date: 2026-11-03

M7-12 (#115 A1; ADR-0017 E12, E16; ``_docs/data-model.md`` §12.3-§12.6,
§13.1 X1, X3, §14.1, §16).

- ``expenses``: a one-time operating expense. ``amount > 0``; ``description``
  trimmed, 1 to 500 characters; ``receipt_reference`` (1 to 100) and ``note``
  (1 to 1000) optional and trimmed; ``due_on >= incurred_on`` (#115 E2).
  ``category_id`` references ``expense_categories``, ``vendor_id`` (optional)
  ``vendors``, ``created_by_user_id`` (``NULL`` only for #116's job) ``users``.
  Indexed by ``due_on``, ``incurred_on``, ``(category_id, incurred_on)`` and
  ``vendor_id`` (§16).
- ``expense_payments``: ``amount > 0``; ``method_code`` references
  ``payment_methods (code)``; ``reference`` (1 to 100) and ``note`` (1 to 1000)
  optional and trimmed. Indexed by ``expense_id`` and ``paid_on``.
- ``expense_payment_reversals``: at most one per payment (the UNIQUE), with a
  trimmed reason of 1 to 1000 characters.
- ``expense_adjustments``: a signed ``amount <> 0`` with a trimmed reason of 1
  to 1000 characters. Indexed by ``expense_id``.

All four are append-only, the migration 0004 pattern: ``<table>_append_only()``
refuses ``UPDATE``, ``DELETE`` and ``TRUNCATE`` with 42501 (X3). Every foreign
key is ``ON DELETE RESTRICT``. The cap (X1), the void rule and the duplicate
rule (#115 E4, E5) are checked by the application under the expense lock: no
database key, because a reversed payment frees its method and reference.

No rows are inserted. Downgrading refuses while an expense exists (money rows
are never dropped silently); otherwise it restores the 0083 shape. The
permissions ``expense.view`` and ``expense.manage`` are seeded by ``make
seed`` (the identity catalog).
"""

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op

revision: str = "0084"
down_revision: str | Sequence[str] | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPENSES: Final = "expenses"
PAYMENTS: Final = "expense_payments"
REVERSALS: Final = "expense_payment_reversals"
ADJUSTMENTS: Final = "expense_adjustments"
REVERSAL_KEY: Final = "uq_expense_payment_reversals_expense_payment_id"
REVERSAL_FK: Final = "fk_expense_payment_reversals_payment"

REFUSE_DOWNGRADE: Final = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM expenses) THEN
        RAISE EXCEPTION 'expenses has rows: money is never dropped'
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


def _check(table: str, condition: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


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


def _money(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.Numeric(precision=14, scale=2), nullable=False)


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
        EXPENSES,
        sa.Column("category_id", sa.UUID(), nullable=False),
        sa.Column("vendor_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        _money("amount"),
        sa.Column("incurred_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("receipt_reference", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        *_standard_columns(),
        _check(EXPENSES, "amount > 0", "amount_positive"),
        _check(EXPENSES, _trimmed("description", 500), "description_trimmed"),
        _check(
            EXPENSES, _trimmed("receipt_reference", 100), "receipt_reference_trimmed"
        ),
        _check(EXPENSES, _trimmed("note", 1000), "note_trimmed"),
        _check(EXPENSES, "due_on >= incurred_on", "due_on_after_incurred_on"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{EXPENSES}")),
        _fk(EXPENSES, "category_id", "expense_categories.id"),
        _fk(EXPENSES, "vendor_id", "vendors.id"),
        _fk(EXPENSES, "created_by_user_id", "users.id"),
    )
    op.create_index(op.f(f"ix_{EXPENSES}_due_on"), EXPENSES, ["due_on"])
    op.create_index(op.f(f"ix_{EXPENSES}_incurred_on"), EXPENSES, ["incurred_on"])
    op.create_index(
        op.f(f"ix_{EXPENSES}_category_id_incurred_on"),
        EXPENSES,
        ["category_id", "incurred_on"],
    )
    op.create_index(op.f(f"ix_{EXPENSES}_vendor_id"), EXPENSES, ["vendor_id"])
    _append_only(EXPENSES)

    op.create_table(
        PAYMENTS,
        sa.Column("expense_id", sa.UUID(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        _money("amount"),
        sa.Column("method_code", sa.String(length=32), nullable=False),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        _check(PAYMENTS, "amount > 0", "amount_positive"),
        _check(PAYMENTS, _trimmed("reference", 100), "reference_trimmed"),
        _check(PAYMENTS, _trimmed("note", 1000), "note_trimmed"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PAYMENTS}")),
        _fk(PAYMENTS, "expense_id", "expenses.id"),
        _fk(PAYMENTS, "method_code", "payment_methods.code"),
        _fk(PAYMENTS, "recorded_by_user_id", "users.id"),
    )
    op.create_index(op.f(f"ix_{PAYMENTS}_expense_id"), PAYMENTS, ["expense_id"])
    op.create_index(op.f(f"ix_{PAYMENTS}_paid_on"), PAYMENTS, ["paid_on"])
    _append_only(PAYMENTS)

    op.create_table(
        REVERSALS,
        sa.Column("expense_payment_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reversed_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        _check(REVERSALS, _trimmed("reason", 1000), "reason_trimmed"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{REVERSALS}")),
        # Named: the convention's name is beyond PostgreSQL's 63 characters.
        sa.ForeignKeyConstraint(
            ["expense_payment_id"],
            [f"{PAYMENTS}.id"],
            name=REVERSAL_FK,
            ondelete="RESTRICT",
        ),
        _fk(REVERSALS, "reversed_by_user_id", "users.id"),
        sa.UniqueConstraint("expense_payment_id", name=REVERSAL_KEY),
    )
    _append_only(REVERSALS)

    op.create_table(
        ADJUSTMENTS,
        sa.Column("expense_id", sa.UUID(), nullable=False),
        _money("amount"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        _check(ADJUSTMENTS, "amount <> 0", "amount_not_zero"),
        _check(ADJUSTMENTS, _trimmed("reason", 1000), "reason_trimmed"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ADJUSTMENTS}")),
        _fk(ADJUSTMENTS, "expense_id", "expenses.id"),
        _fk(ADJUSTMENTS, "created_by_user_id", "users.id"),
    )
    op.create_index(op.f(f"ix_{ADJUSTMENTS}_expense_id"), ADJUSTMENTS, ["expense_id"])
    _append_only(ADJUSTMENTS)


def downgrade() -> None:
    op.execute(REFUSE_DOWNGRADE)
    for table in (ADJUSTMENTS, REVERSALS, PAYMENTS, EXPENSES):
        _drop_append_only(table)
        op.drop_table(table)
