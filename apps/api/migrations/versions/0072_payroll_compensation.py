"""Payroll: tutor pay terms and compensation rates.

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-31

M7-02 (#105 B; ADR-0017 E1; ``_docs/data-model.md`` §11.2, §11.3, §14.2).

- ``tutor_pay_terms`` (audited, guarded): a tutor's effective-dated pay
  frequency and salary, ``UNIQUE (tutor_user_id, effective_from)``.
  ``pay_frequency`` ``NULL`` = not on payroll from that date; no salary
  without a frequency.
- ``compensation_rates`` (audited, guarded): a tutor's effective-dated rate
  per class kind, ``UNIQUE (tutor_user_id, class_kind, effective_from)``
  (the "no overlap" constraint, invariant P1). ``rate_model`` and ``rate``
  are both ``NULL`` for "no rate from that date"; ``per_course`` only on
  ``course``.
- **Staged guards (§14.2):** ``compensation_rate_is_referenced(uuid)`` and
  ``tutor_pay_terms_is_referenced(uuid)`` are ``SELECT false`` here, because
  ``payroll_lines`` doesn't exist yet: #107 and #108 replace them (``CREATE
  OR REPLACE``) and their downgrades restore these bodies. The row triggers
  raise 42501 on an ``UPDATE`` or ``DELETE`` of a referenced row, and the
  statement triggers refuse every ``TRUNCATE`` (42501).

Every foreign key is ``ON DELETE RESTRICT``. No rows are inserted
(``payroll.view`` and ``payroll.manage`` are seeded by ``make seed``).
Downgrading drops the triggers, the functions, then the tables.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: str | Sequence[str] | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TERMS: Final = "tutor_pay_terms"
RATES: Final = "compensation_rates"

TERMS_KEY: Final = "uq_tutor_pay_terms_tutor_user_id_effective_from"
RATE_KEY: Final = "uq_compensation_rates_tutor_user_id_class_kind_effective_from"

GUARDED: Final = (
    # table, predicate, issue that replaces the predicate
    (TERMS, "tutor_pay_terms_is_referenced", "#108"),
    (RATES, "compensation_rate_is_referenced", "#107"),
)


def _predicate(name: str, issue: str) -> str:
    return f"""
CREATE FUNCTION {name}(id uuid) RETURNS boolean
LANGUAGE sql VOLATILE AS $$
    -- #105: payroll_lines doesn't exist yet. {issue} replaces this body with
    -- its EXISTS over the lines of a finalized period.
    SELECT false
$$
"""


def _guard(table: str, predicate: str) -> str:
    return f"""
CREATE FUNCTION {table}_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION '{table} is never truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF {predicate}(OLD.id) THEN
        RAISE EXCEPTION '{table} row is referenced'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$
"""


def _standard_columns() -> list[sa.Column[object]]:
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


def _tutor_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tutor_user_id"],
        ["users.id"],
        name=op.f(f"fk_{table}_tutor_user_id_users"),
        ondelete="RESTRICT",
    )


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def upgrade() -> None:
    op.create_table(
        TERMS,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("pay_frequency", sa.String(length=10), nullable=True),
        sa.Column("salary_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        *_standard_columns(),
        _check(
            TERMS,
            "pay_frequency_valid",
            "pay_frequency IN ('weekly', 'biweekly', 'monthly')",
        ),
        _check(TERMS, "salary_positive", "salary_amount > 0"),
        _check(
            TERMS,
            "salary_needs_frequency",
            "pay_frequency IS NOT NULL OR salary_amount IS NULL",
        ),
        _tutor_fk(TERMS),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TERMS}")),
        sa.UniqueConstraint("tutor_user_id", "effective_from", name=TERMS_KEY),
    )
    op.create_table(
        RATES,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("class_kind", sa.String(length=20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("rate_model", sa.String(length=20), nullable=True),
        sa.Column("rate", sa.Numeric(precision=14, scale=4), nullable=True),
        *_standard_columns(),
        _check(
            RATES,
            "class_kind_valid",
            "class_kind IN ('individual', 'group', 'course')",
        ),
        _check(
            RATES,
            "rate_model_valid",
            "rate_model IN ('per_hour', 'per_session', 'per_course')",
        ),
        _check(RATES, "rate_non_negative", "rate >= 0"),
        _check(RATES, "rate_model_with_rate", "(rate_model IS NULL) = (rate IS NULL)"),
        _check(
            RATES,
            "per_course_only_course",
            "rate_model <> 'per_course' OR class_kind = 'course'",
        ),
        _tutor_fk(RATES),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{RATES}")),
        sa.UniqueConstraint(
            "tutor_user_id", "class_kind", "effective_from", name=RATE_KEY
        ),
    )
    for table, predicate, issue in GUARDED:
        op.execute(_predicate(predicate, issue))
        op.execute(_guard(table, predicate))
        op.execute(
            f"CREATE TRIGGER {table}_no_referenced_change"
            f" BEFORE UPDATE OR DELETE ON {table}"
            f" FOR EACH ROW EXECUTE FUNCTION {table}_guard()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}"
            f" FOR EACH STATEMENT EXECUTE FUNCTION {table}_guard()"
        )


def downgrade() -> None:
    for table, predicate, _issue in GUARDED:
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER {table}_no_referenced_change ON {table}")
        op.execute(f"DROP FUNCTION {table}_guard()")
        op.execute(f"DROP FUNCTION {predicate}(uuid)")
    op.drop_table(RATES)
    op.drop_table(TERMS)
