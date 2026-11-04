"""Finance: recurring expense templates.

Revision ID: 0085
Revises: 0084
Create Date: 2026-11-04

M7-13 (#116 A1; ADR-0017 E17 and its #116 notes; ``_docs/data-model.md``
§12.3, §12.7, §13.1 X2, §16).

- ``expense_templates`` (mutable config, audited, versioned): ``name`` trimmed,
  1 to 200 characters, unique ignoring case (``uq_expense_templates_lower_name``,
  #116 E3); ``category_id`` references ``expense_categories``, ``vendor_id``
  (optional) ``vendors``; ``amount > 0``; ``frequency`` ``weekly``, ``monthly``
  or ``annual``; ``ends_on >= starts_on``; ``due_offset_days`` 0 to 60;
  ``next_period_start >= starts_on``. Indexed by ``category_id`` and
  ``vendor_id`` (the deactivation refusal) and by ``next_period_start WHERE
  is_active`` (the job's candidates). A ``BEFORE TRUNCATE`` trigger refuses
  ``TRUNCATE`` with 42501 (#116 E10); there is no delete route.
- ``expenses`` gains ``expense_template_id`` (references ``expense_templates``)
  and ``period_start``, set together or not at all
  (``ck_expenses_template_period``), with the natural key of a generated
  expense: the partial unique index ``uq_expenses_template_period`` on
  ``(expense_template_id, period_start) WHERE expense_template_id IS NOT NULL``
  (X2). The append-only triggers refuse row changes only, so ``ALTER TABLE``
  passes.

Every foreign key is ``ON DELETE RESTRICT``. No rows are inserted. Downgrading
refuses while a template exists (a generated expense references one);
otherwise it restores the 0084 shape.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: str | Sequence[str] | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEMPLATES: Final = "expense_templates"
EXPENSES: Final = "expenses"
NAME_KEY: Final = "uq_expense_templates_lower_name"
PERIOD_KEY: Final = "uq_expenses_template_period"
GUARD: Final = "expense_templates_no_truncate"

REFUSE_DOWNGRADE: Final = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM expense_templates) THEN
        RAISE EXCEPTION 'expense_templates has rows: templates are never dropped'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
END;
$$
"""

GUARD_FUNCTION: Final = f"""
CREATE FUNCTION {GUARD}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- #116 E10: a template is paused with is_active, never removed wholesale.
    RAISE EXCEPTION 'expense_templates is never truncated'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def _check(table: str, condition: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{table}_{column}_{target.split('.')[0]}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        TEMPLATES,
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category_id", sa.UUID(), nullable=False),
        sa.Column("vendor_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("frequency", sa.String(length=10), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("due_offset_days", sa.SmallInteger(), nullable=False),
        sa.Column("next_period_start", sa.Date(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _check(
            TEMPLATES,
            "char_length(name) BETWEEN 1 AND 200 AND name = btrim(name)",
            "name_trimmed",
        ),
        _check(TEMPLATES, "amount > 0", "amount_positive"),
        _check(
            TEMPLATES,
            "frequency IN ('weekly', 'monthly', 'annual')",
            "frequency_valid",
        ),
        _check(TEMPLATES, "ends_on >= starts_on", "ends_on_after_starts_on"),
        _check(TEMPLATES, "due_offset_days BETWEEN 0 AND 60", "due_offset_days_range"),
        _check(
            TEMPLATES,
            "next_period_start >= starts_on",
            "next_period_start_after_starts_on",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TEMPLATES}")),
        _fk(TEMPLATES, "category_id", "expense_categories.id"),
        _fk(TEMPLATES, "vendor_id", "vendors.id"),
    )
    op.create_index(NAME_KEY, TEMPLATES, [sa.text("lower(name)")], unique=True)
    op.create_index(op.f(f"ix_{TEMPLATES}_category_id"), TEMPLATES, ["category_id"])
    op.create_index(op.f(f"ix_{TEMPLATES}_vendor_id"), TEMPLATES, ["vendor_id"])
    op.create_index(
        op.f(f"ix_{TEMPLATES}_next_period_start_active"),
        TEMPLATES,
        ["next_period_start"],
        postgresql_where=sa.text("is_active"),
    )
    op.execute(GUARD_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {GUARD} BEFORE TRUNCATE ON {TEMPLATES}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {GUARD}()"
    )

    op.add_column(EXPENSES, sa.Column("expense_template_id", sa.UUID(), nullable=True))
    op.add_column(EXPENSES, sa.Column("period_start", sa.Date(), nullable=True))
    op.create_foreign_key(
        op.f(f"fk_{EXPENSES}_expense_template_id_{TEMPLATES}"),
        EXPENSES,
        TEMPLATES,
        ["expense_template_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f(f"ck_{EXPENSES}_template_period"),
        EXPENSES,
        "(expense_template_id IS NULL) = (period_start IS NULL)",
    )
    op.create_index(
        PERIOD_KEY,
        EXPENSES,
        ["expense_template_id", "period_start"],
        unique=True,
        postgresql_where=sa.text("expense_template_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute(REFUSE_DOWNGRADE)
    op.drop_index(PERIOD_KEY, table_name=EXPENSES)
    op.drop_constraint(op.f(f"ck_{EXPENSES}_template_period"), EXPENSES, "check")
    op.drop_constraint(
        op.f(f"fk_{EXPENSES}_expense_template_id_{TEMPLATES}"),
        EXPENSES,
        "foreignkey",
    )
    op.drop_column(EXPENSES, "period_start")
    op.drop_column(EXPENSES, "expense_template_id")
    op.execute(f"DROP TRIGGER {GUARD} ON {TEMPLATES}")
    op.execute(f"DROP FUNCTION {GUARD}()")
    op.drop_table(TEMPLATES)
