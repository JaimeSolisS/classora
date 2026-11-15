"""Payroll: salary lines.

Revision ID: 0077
Revises: 0076
Create Date: 2026-11-02

M7-05 (#108 A; ADR-0017 E1, E7, E9; ``_docs/data-model.md`` §11.6, §14.2, §16).

- ``payroll_lines`` gains the ``salary`` kind and its seven columns, all set on
  a salary line and ``NULL`` on every other kind: the terms version, the
  ``salary_amount`` the line was prorated from, the piece's dates, the fraction
  and the basis. ``kind`` and ``source_per_kind`` widen.
- One line per piece (#108 E1): ``uq_payroll_lines_salary_piece`` keys it on
  ``(payroll_period_id, salary_starts_on)``, because one terms version can pay
  several natural periods inside a long period.
- ``ix_payroll_lines_tutor_pay_terms_id`` serves the guard and the ``RESTRICT``
  check.
- ``tutor_pay_terms_is_referenced(uuid)`` gets its real body (#105's staged
  guard), with the parameter qualified as in
  ``compensation_rate_is_referenced`` (0076).

No rows are inserted. Downgrading restores the ``SELECT false`` body, removes
the ``salary`` rows with the finalized-line trigger disabled (#108 E5: the old
CHECKs can't hold them, as 0076's downgrade drops the table with its rows), then
restores the 0076 CHECKs and drops the indexes and columns.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0077"
down_revision: str | Sequence[str] | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINES: Final = "payroll_lines"
TERMS_FK: Final = "fk_payroll_lines_tutor_pay_terms_id_tutor_pay_terms"
PIECE_INDEX: Final = "uq_payroll_lines_salary_piece"
TERMS_INDEX: Final = "ix_payroll_lines_tutor_pay_terms_id"
FINALIZED_TRIGGER: Final = "payroll_lines_no_finalized_change"

SESSION_KINDS_SQL: Final = "'session', 'late_cancellation', 'no_show', 'makeup'"
OLD_SOURCE_CHECK: Final = (
    "(kind IN ('session', 'makeup')"
    " AND class_session_id IS NOT NULL AND approval_id IS NOT NULL)"
    " OR (kind = 'no_show' AND class_session_id IS NOT NULL)"
    " OR (kind = 'late_cancellation'"
    " AND class_session_id IS NOT NULL AND approval_id IS NULL)"
)
SOURCE_CHECK: Final = (
    f"{OLD_SOURCE_CHECK}"
    " OR (kind = 'salary' AND class_session_id IS NULL AND approval_id IS NULL)"
)
NEW_CHECKS: Final = (
    (
        "salary_fields",
        "num_nonnulls(tutor_pay_terms_id, salary_amount, salary_starts_on,"
        " salary_ends_on, proration_numerator, proration_denominator,"
        " proration_basis) = CASE WHEN kind = 'salary' THEN 7 ELSE 0 END",
    ),
    ("salary_no_rate", "kind <> 'salary' OR rate_model IS NULL"),
    ("salary_amount_positive", "salary_amount > 0"),
    ("salary_dates", "salary_starts_on <= salary_ends_on"),
    (
        "proration_fraction",
        "proration_denominator > 0 AND proration_numerator >= 0"
        " AND proration_numerator <= proration_denominator",
    ),
    ("proration_basis_valid", "proration_basis IN ('calendar_days', 'weekdays')"),
)

TERMS_PREDICATE: Final = """
CREATE OR REPLACE FUNCTION tutor_pay_terms_is_referenced(id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- #108: qualified, because the columns named id would win over the
    -- parameter. Only a finalized line freezes a version (#107 E8).
    SELECT EXISTS (
        SELECT 1
        FROM payroll_lines AS l
        JOIN payroll_periods AS p ON p.id = l.payroll_period_id
        WHERE l.tutor_pay_terms_id = tutor_pay_terms_is_referenced.id
            AND p.status = 'finalized'
    )
$$
"""

STAGED_TERMS_PREDICATE: Final = """
CREATE OR REPLACE FUNCTION tutor_pay_terms_is_referenced(id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- #105: payroll_lines doesn't exist yet. #108 replaces this body with
    -- its EXISTS over the lines of a finalized period.
    SELECT false
$$
"""


def _replace_check(name: str, condition: str) -> None:
    constraint = op.f(f"ck_{LINES}_{name}")
    op.drop_constraint(constraint, LINES, type_="check")
    op.create_check_constraint(constraint, LINES, condition)


def upgrade() -> None:
    op.add_column(LINES, sa.Column("tutor_pay_terms_id", sa.UUID(), nullable=True))
    op.add_column(
        LINES,
        sa.Column("salary_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(LINES, sa.Column("salary_starts_on", sa.Date(), nullable=True))
    op.add_column(LINES, sa.Column("salary_ends_on", sa.Date(), nullable=True))
    op.add_column(LINES, sa.Column("proration_numerator", sa.Integer(), nullable=True))
    op.add_column(
        LINES, sa.Column("proration_denominator", sa.Integer(), nullable=True)
    )
    op.add_column(
        LINES, sa.Column("proration_basis", sa.String(length=20), nullable=True)
    )
    op.create_foreign_key(
        op.f(TERMS_FK),
        LINES,
        "tutor_pay_terms",
        ["tutor_pay_terms_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    _replace_check("kind_valid", f"kind IN ({SESSION_KINDS_SQL}, 'salary')")
    _replace_check("source_per_kind", SOURCE_CHECK)
    for name, condition in NEW_CHECKS:
        op.create_check_constraint(op.f(f"ck_{LINES}_{name}"), LINES, condition)
    op.create_index(
        PIECE_INDEX,
        LINES,
        ["payroll_period_id", "salary_starts_on"],
        unique=True,
        postgresql_where=sa.text("kind = 'salary'"),
    )
    op.create_index(
        op.f(TERMS_INDEX),
        LINES,
        ["tutor_pay_terms_id"],
        postgresql_where=sa.text("tutor_pay_terms_id IS NOT NULL"),
    )
    op.execute(TERMS_PREDICATE)


def downgrade() -> None:
    op.execute(STAGED_TERMS_PREDICATE)
    op.execute(f"ALTER TABLE {LINES} DISABLE TRIGGER {FINALIZED_TRIGGER}")
    op.execute(f"DELETE FROM {LINES} WHERE kind = 'salary'")  # noqa: S608
    op.execute(f"ALTER TABLE {LINES} ENABLE TRIGGER {FINALIZED_TRIGGER}")
    op.drop_index(op.f(TERMS_INDEX), table_name=LINES)
    op.drop_index(PIECE_INDEX, table_name=LINES)
    for name, _ in NEW_CHECKS:
        op.drop_constraint(op.f(f"ck_{LINES}_{name}"), LINES, type_="check")
    _replace_check("source_per_kind", OLD_SOURCE_CHECK)
    _replace_check("kind_valid", f"kind IN ({SESSION_KINDS_SQL})")
    op.drop_constraint(op.f(TERMS_FK), LINES, type_="foreignkey")
    op.drop_column(LINES, "proration_basis")
    op.drop_column(LINES, "proration_denominator")
    op.drop_column(LINES, "proration_numerator")
    op.drop_column(LINES, "salary_ends_on")
    op.drop_column(LINES, "salary_starts_on")
    op.drop_column(LINES, "salary_amount")
    op.drop_column(LINES, "tutor_pay_terms_id")
