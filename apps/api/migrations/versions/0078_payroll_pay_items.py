"""Payroll: bonuses and deductions.

Revision ID: 0078
Revises: 0077
Create Date: 2026-11-02

M7-06 (#109 A; ADR-0017 E8, E9, its #109 notes; ``_docs/data-model.md`` §11.6
to §11.8, §14.2, §16).

- ``pay_item_types``: staff-defined names with a ``direction``. A trigger keeps
  the direction (it decides a line's sign) and refuses ``TRUNCATE``.
- ``pay_items``: fixed amounts, always positive, for one tutor.
  ``pay_items_guard()`` has the ``custom_prices`` shape (§14.2): once a finalized
  line uses an item, only ``effective_to`` moves, and it can't be deleted. Its
  predicate
  ``pay_item_is_referenced(uuid)`` names its parameter ``id``, so the body
  qualifies it (#105 O4).
- ``payroll_lines`` gains the ``pay_item`` kind and ``pay_item_id``.
  ``amount_sign`` replaces ``amount_non_negative``, which refused every
  deduction. The foreign key is composite, so an item can't reach another
  tutor's line (invariant P12).

No rows are inserted, and no type is seeded (#109 E8). Downgrading removes the
``pay_item`` rows with the finalized-line trigger disabled (the #108 E5 reason:
the old CHECKs can't hold them), then restores the 0077 shape.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0078"
down_revision: str | Sequence[str] | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINES: Final = "payroll_lines"
TYPES: Final = "pay_item_types"
ITEMS: Final = "pay_items"
FINALIZED_TRIGGER: Final = "payroll_lines_no_finalized_change"
ITEM_FK: Final = "fk_payroll_lines_pay_item_tutor"
ITEM_TARGET: Final = "uq_pay_items_id_tutor_user_id"
PERIOD_KEY: Final = "uq_payroll_lines_pay_item_period"
ITEM_INDEX: Final = "ix_payroll_lines_pay_item_id"
TYPE_NAME_INDEX: Final = "uq_pay_item_types_lower_name"

SESSION_KINDS_SQL: Final = "'session', 'late_cancellation', 'no_show', 'makeup'"
OLD_SOURCE_CHECK: Final = (
    "(kind IN ('session', 'makeup')"
    " AND class_session_id IS NOT NULL AND approval_id IS NOT NULL)"
    " OR (kind = 'no_show' AND class_session_id IS NOT NULL)"
    " OR (kind = 'late_cancellation'"
    " AND class_session_id IS NOT NULL AND approval_id IS NULL)"
    " OR (kind = 'salary' AND class_session_id IS NULL AND approval_id IS NULL)"
)
SOURCE_CHECK: Final = (
    f"{OLD_SOURCE_CHECK}"
    " OR (kind = 'pay_item' AND class_session_id IS NULL AND approval_id IS NULL)"
)
AMOUNT_SIGN: Final = (
    "(kind = 'pay_item' AND amount <> 0) OR (kind <> 'pay_item' AND amount >= 0)"
)
NEW_CHECKS: Final = (
    ("pay_item_fields", "(pay_item_id IS NOT NULL) = (kind = 'pay_item')"),
    ("pay_item_no_rate", "kind <> 'pay_item' OR rate_model IS NULL"),
)

TYPES_GUARD: Final = f"""
CREATE FUNCTION {TYPES}_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION '{TYPES} is never truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- #109 E8: the direction decides a line's sign, so a wrong one is a new type.
    IF NEW.direction IS DISTINCT FROM OLD.direction THEN
        RAISE EXCEPTION '{TYPES}.direction never changes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

ITEM_PREDICATE: Final = """
CREATE FUNCTION pay_item_is_referenced(id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- Qualified, because the columns named id would win over the parameter.
    -- Only a finalized line freezes an item (ADR-0017 E8).
    SELECT EXISTS (
        SELECT 1
        FROM payroll_lines AS l
        JOIN payroll_periods AS p ON p.id = l.payroll_period_id
        WHERE l.pay_item_id = pay_item_is_referenced.id
            AND p.status = 'finalized'
    )
$$
"""

ITEMS_GUARD: Final = f"""
CREATE FUNCTION {ITEMS}_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION '{ITEMS} is never truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF pay_item_is_referenced(OLD.id) THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION '{ITEMS} row is referenced'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- ADR-0017 E8: an applied item may only be ended (effective_to).
        IF (to_jsonb(NEW) - 'effective_to' - 'version' - 'updated_at')
            IS DISTINCT FROM (to_jsonb(OLD) - 'effective_to' - 'version' - 'updated_at')
        THEN
            RAISE EXCEPTION '{ITEMS} row is referenced'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    ]


def _create_types() -> None:
    op.create_table(
        TYPES,
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_standard_columns(),
        sa.CheckConstraint(
            "direction IN ('bonus', 'deduction')",
            name=op.f(f"ck_{TYPES}_direction_valid"),
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100 AND name = btrim(name)",
            name=op.f(f"ck_{TYPES}_name_trimmed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TYPES}")),
    )
    op.create_index(TYPE_NAME_INDEX, TYPES, [sa.text("lower(name)")], unique=True)
    op.execute(TYPES_GUARD)
    op.execute(
        f"CREATE TRIGGER {TYPES}_no_direction_change BEFORE UPDATE ON {TYPES}"
        f" FOR EACH ROW EXECUTE FUNCTION {TYPES}_guard()"
    )
    op.execute(
        f"CREATE TRIGGER {TYPES}_no_truncate BEFORE TRUNCATE ON {TYPES}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {TYPES}_guard()"
    )


def _create_items() -> None:
    op.create_table(
        ITEMS,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("pay_item_type_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("recurrence", sa.String(length=10), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        *_standard_columns(),
        sa.CheckConstraint("amount > 0", name=op.f(f"ck_{ITEMS}_amount_positive")),
        sa.CheckConstraint(
            "recurrence IN ('one_time', 'recurring')",
            name=op.f(f"ck_{ITEMS}_recurrence_valid"),
        ),
        sa.CheckConstraint(
            "char_length(description) BETWEEN 1 AND 500"
            " AND description = btrim(description)",
            name=op.f(f"ck_{ITEMS}_description_trimmed"),
        ),
        sa.CheckConstraint(
            "effective_to >= effective_from", name=op.f(f"ck_{ITEMS}_dates_ordered")
        ),
        sa.CheckConstraint(
            "recurrence = 'recurring' OR effective_to IS NULL",
            name=op.f(f"ck_{ITEMS}_end_only_recurring"),
        ),
        sa.ForeignKeyConstraint(
            ["tutor_user_id"],
            ["users.id"],
            name=op.f(f"fk_{ITEMS}_tutor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pay_item_type_id"],
            [f"{TYPES}.id"],
            name=op.f(f"fk_{ITEMS}_pay_item_type_id_{TYPES}"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ITEMS}")),
        sa.UniqueConstraint("id", "tutor_user_id", name=ITEM_TARGET),
    )
    op.create_index(
        op.f(f"ix_{ITEMS}_tutor_user_id_effective_from"),
        ITEMS,
        ["tutor_user_id", "effective_from"],
    )
    op.create_index(op.f(f"ix_{ITEMS}_pay_item_type_id"), ITEMS, ["pay_item_type_id"])


def _replace_check(old: str, new: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{LINES}_{old}"), LINES, type_="check")
    op.create_check_constraint(op.f(f"ck_{LINES}_{new}"), LINES, condition)


def _change_lines() -> None:
    op.add_column(LINES, sa.Column("pay_item_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        ITEM_FK,
        LINES,
        ITEMS,
        ["pay_item_id", "tutor_user_id"],
        ["id", "tutor_user_id"],
        ondelete="RESTRICT",
    )
    _replace_check(
        "kind_valid",
        "kind_valid",
        f"kind IN ({SESSION_KINDS_SQL}, 'salary', 'pay_item')",
    )
    _replace_check("source_per_kind", "source_per_kind", SOURCE_CHECK)
    _replace_check("amount_non_negative", "amount_sign", AMOUNT_SIGN)
    for name, condition in NEW_CHECKS:
        op.create_check_constraint(op.f(f"ck_{LINES}_{name}"), LINES, condition)
    op.create_index(
        PERIOD_KEY,
        LINES,
        ["pay_item_id", "payroll_period_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'pay_item'"),
    )
    op.create_index(
        ITEM_INDEX,
        LINES,
        ["pay_item_id"],
        postgresql_where=sa.text("pay_item_id IS NOT NULL"),
    )


def upgrade() -> None:
    _create_types()
    _create_items()
    _change_lines()
    op.execute(ITEM_PREDICATE)
    op.execute(ITEMS_GUARD)
    op.execute(
        f"CREATE TRIGGER {ITEMS}_no_referenced_change"
        f" BEFORE UPDATE OR DELETE ON {ITEMS}"
        f" FOR EACH ROW EXECUTE FUNCTION {ITEMS}_guard()"
    )
    op.execute(
        f"CREATE TRIGGER {ITEMS}_no_truncate BEFORE TRUNCATE ON {ITEMS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {ITEMS}_guard()"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {LINES} DISABLE TRIGGER {FINALIZED_TRIGGER}")
    op.execute(f"DELETE FROM {LINES} WHERE kind = 'pay_item'")  # noqa: S608
    op.execute(f"ALTER TABLE {LINES} ENABLE TRIGGER {FINALIZED_TRIGGER}")
    op.drop_index(ITEM_INDEX, table_name=LINES)
    op.drop_index(PERIOD_KEY, table_name=LINES)
    for name, _ in NEW_CHECKS:
        op.drop_constraint(op.f(f"ck_{LINES}_{name}"), LINES, type_="check")
    _replace_check("amount_sign", "amount_non_negative", "amount >= 0")
    _replace_check("source_per_kind", "source_per_kind", OLD_SOURCE_CHECK)
    _replace_check(
        "kind_valid", "kind_valid", f"kind IN ({SESSION_KINDS_SQL}, 'salary')"
    )
    op.drop_constraint(ITEM_FK, LINES, type_="foreignkey")
    op.drop_column(LINES, "pay_item_id")
    op.execute(f"DROP TRIGGER {ITEMS}_no_truncate ON {ITEMS}")
    op.execute(f"DROP TRIGGER {ITEMS}_no_referenced_change ON {ITEMS}")
    op.execute(f"DROP FUNCTION {ITEMS}_guard()")
    op.execute("DROP FUNCTION pay_item_is_referenced(uuid)")
    op.drop_table(ITEMS)
    op.execute(f"DROP TRIGGER {TYPES}_no_truncate ON {TYPES}")
    op.execute(f"DROP TRIGGER {TYPES}_no_direction_change ON {TYPES}")
    op.execute(f"DROP FUNCTION {TYPES}_guard()")
    op.drop_table(TYPES)
