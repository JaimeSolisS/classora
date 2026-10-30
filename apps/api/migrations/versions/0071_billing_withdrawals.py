"""Billing: withdrawals (unused sessions or package units to a credit or a
refund).

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-30

M6-16 (#101 A1-A5; ``_docs/data-model.md`` §3.16, §3.20, §3.23; ADR-0006
E5, E9, E10).

- ``withdrawals`` (append-only, the §5.1 trigger pair, SQLSTATE 42501): one
  settlement of exactly one subject (S2): a group enrollment, a cohort
  enrollment or a package (``num_nonnulls(...) = 1``, one partial UNIQUE per
  subject column), with its ``outcome`` (``refund`` | ``account_credit``),
  ``amount`` (Σ applied credits), ``refund_amount`` (> 0 exactly for a
  refund, S4), the instant used (``withdrawn_at``), the staff ``reason``,
  the confirmed ``preview`` (JSONB) and the confirming user (``ON DELETE
  RESTRICT``). A package subject stays on its package's account and student
  (composite foreign key).
- ``refunds.withdrawal_id`` (NULL, RESTRICT, indexed): the refunds a
  withdrawal paid out ("column added by #101", #90 E3).
- ``package_credit_transactions``: ``withdrawal_id`` (NULL, RESTRICT), the
  ``refund`` kind added to the kind CHECK and to the units-sign CHECK
  (``refund`` < 0), ``(kind = 'refund') = (withdrawal_id IS NOT NULL)`` and
  one ``refund`` per package (``uq_package_credit_transactions_refund``,
  #94 L10, S7).

**S1 (the calculation)**, **S2** and **S7** are app rules
(``app.modules.billing.withdrawals``, ``app.workflows.withdrawals``): a line
is credited ``net × unused / deliverable`` (``Money.prorate``); a package
withdrawal writes one ``refund`` units row of ``−remaining`` plus a
``withdrawal`` adjustment on its purchase charge, and an expired,
cancelled, exhausted or withdrawn package can't be withdrawn.

Downgrading drops everything above and restores the 0066 CHECK texts. With
withdrawals present (and so with ``refund`` units rows, which need one) the
downgrade fails loudly before changing anything (``object_in_use``):
append-only rows are never rewritten or removed first.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071"
down_revision: str | Sequence[str] | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WITHDRAWALS: Final = "withdrawals"
REFUNDS: Final = "refunds"
TRANSACTIONS: Final = "package_credit_transactions"

KIND_0066: Final = "kind IN ('grant', 'consume', 'expire', 'reversal')"
SIGN_0066: Final = (
    "(kind = 'grant' AND units > 0)"
    " OR (kind IN ('consume', 'expire') AND units < 0)"
    " OR (kind = 'reversal' AND units <> 0)"
)
KIND: Final = "kind IN ('grant', 'consume', 'expire', 'reversal', 'refund')"
SIGN: Final = (
    "(kind = 'grant' AND units > 0)"
    " OR (kind IN ('consume', 'expire', 'refund') AND units < 0)"
    " OR (kind = 'reversal' AND units <> 0)"
)
REFUND_KEY: Final = "uq_package_credit_transactions_refund"
SUBJECT_KEYS: Final = (
    (
        "uq_withdrawals_group_enrollment",
        "group_enrollment_id",
        "group_enrollment_id IS NOT NULL",
    ),
    (
        "uq_withdrawals_cohort_enrollment",
        "cohort_enrollment_id",
        "cohort_enrollment_id IS NOT NULL",
    ),
    ("uq_withdrawals_package", "package_id", "package_id IS NOT NULL"),
)


def _append_only_function(table: str) -> str:
    return f"""
CREATE FUNCTION {table}_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{table} is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{WITHDRAWALS}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def _check(name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{WITHDRAWALS}_{name}"))


def _create_withdrawals() -> None:
    op.create_table(
        WITHDRAWALS,
        sa.Column("billing_account_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("group_enrollment_id", sa.UUID(), nullable=True),
        sa.Column("cohort_enrollment_id", sa.UUID(), nullable=True),
        sa.Column("package_id", sa.UUID(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("refund_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confirmed_by_user_id", sa.UUID(), nullable=False),
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
        _check(
            "one_subject",
            "num_nonnulls(group_enrollment_id, cohort_enrollment_id, package_id) = 1",
        ),
        _check("outcome_valid", "outcome IN ('refund', 'account_credit')"),
        _check("amount_non_negative", "amount >= 0"),
        _check("refund_amount_non_negative", "refund_amount >= 0"),
        _check("refund_outcome", "(outcome = 'refund') = (refund_amount > 0)"),
        _check(
            "reason_valid",
            "length(btrim(reason)) BETWEEN 1 AND 500 AND reason = btrim(reason)",
        ),
        _fk("billing_account_id", "billing_accounts"),
        _fk("student_id", "students"),
        _fk("group_enrollment_id", "group_enrollments"),
        _fk("cohort_enrollment_id", "cohort_enrollments"),
        _fk("confirmed_by_user_id", "users"),
        sa.ForeignKeyConstraint(
            ["package_id", "billing_account_id", "student_id"],
            ["packages.id", "packages.billing_account_id", "packages.student_id"],
            name="fk_withdrawals_package",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{WITHDRAWALS}")),
    )
    for name, column, where in SUBJECT_KEYS:
        op.create_index(
            name, WITHDRAWALS, [column], unique=True, postgresql_where=sa.text(where)
        )
    op.create_index(
        "ix_withdrawals_account_created_at",
        WITHDRAWALS,
        ["billing_account_id", "created_at"],
    )
    op.create_index("ix_withdrawals_student_id", WITHDRAWALS, ["student_id"])
    op.execute(_append_only_function(WITHDRAWALS))
    op.execute(
        f"CREATE TRIGGER {WITHDRAWALS}_no_update_delete BEFORE UPDATE OR DELETE"
        f" ON {WITHDRAWALS} FOR EACH ROW EXECUTE FUNCTION {WITHDRAWALS}_append_only()"
    )
    op.execute(
        f"CREATE TRIGGER {WITHDRAWALS}_no_truncate BEFORE TRUNCATE ON {WITHDRAWALS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {WITHDRAWALS}_append_only()"
    )


def _change_refunds() -> None:
    op.add_column(REFUNDS, sa.Column("withdrawal_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_refunds_withdrawal_id_withdrawals"),
        REFUNDS,
        WITHDRAWALS,
        ["withdrawal_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_refunds_withdrawal_id", REFUNDS, ["withdrawal_id"])


def _replace(name: str, expression: str) -> None:
    op.drop_constraint(op.f(f"ck_{TRANSACTIONS}_{name}"), TRANSACTIONS, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{TRANSACTIONS}_{name}"), TRANSACTIONS, expression
    )


def _change_transactions() -> None:
    op.add_column(TRANSACTIONS, sa.Column("withdrawal_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_package_credit_transactions_withdrawal_id_withdrawals"),
        TRANSACTIONS,
        WITHDRAWALS,
        ["withdrawal_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    _replace("kind", KIND)
    _replace("units_sign", SIGN)
    op.create_check_constraint(
        op.f(f"ck_{TRANSACTIONS}_refund_withdrawal"),
        TRANSACTIONS,
        "(kind = 'refund') = (withdrawal_id IS NOT NULL)",
    )
    op.create_index(
        REFUND_KEY,
        TRANSACTIONS,
        ["package_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'refund'"),
    )


def upgrade() -> None:
    _create_withdrawals()
    _change_refunds()
    _change_transactions()


REFUSE_WITH_ROWS: Final = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM withdrawals) THEN
        RAISE EXCEPTION 'withdrawals exist: 0071 cannot be downgraded'
            USING ERRCODE = 'object_in_use';
    END IF;
END;
$$
"""


def downgrade() -> None:
    op.execute(REFUSE_WITH_ROWS)
    op.drop_index(REFUND_KEY, table_name=TRANSACTIONS)
    op.drop_constraint(
        op.f(f"ck_{TRANSACTIONS}_refund_withdrawal"), TRANSACTIONS, type_="check"
    )
    _replace("units_sign", SIGN_0066)
    _replace("kind", KIND_0066)
    op.drop_constraint(
        op.f("fk_package_credit_transactions_withdrawal_id_withdrawals"),
        TRANSACTIONS,
        type_="foreignkey",
    )
    op.drop_column(TRANSACTIONS, "withdrawal_id")
    op.drop_index("ix_refunds_withdrawal_id", table_name=REFUNDS)
    op.drop_constraint(
        op.f("fk_refunds_withdrawal_id_withdrawals"), REFUNDS, type_="foreignkey"
    )
    op.drop_column(REFUNDS, "withdrawal_id")
    op.execute(f"DROP TRIGGER {WITHDRAWALS}_no_truncate ON {WITHDRAWALS}")
    op.execute(f"DROP TRIGGER {WITHDRAWALS}_no_update_delete ON {WITHDRAWALS}")
    op.execute(f"DROP FUNCTION {WITHDRAWALS}_append_only()")
    op.drop_index("ix_withdrawals_student_id", table_name=WITHDRAWALS)
    op.drop_index("ix_withdrawals_account_created_at", table_name=WITHDRAWALS)
    for name, _, _ in reversed(SUBJECT_KEYS):
        op.drop_index(name, table_name=WITHDRAWALS)
    op.drop_table(WITHDRAWALS)
