"""Billing: discounts, scholarships and custom prices.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-28

M6-10 (#95 A1-A7; ``_docs/data-model.md`` §3.6-§3.9, §5; ADR-0006 E9).

- ``custom_prices`` (audited, versioned): a student's or a family's price
  for one catalog item (H6), from ``effective_from`` to ``expires_on``
  (inclusive; ``NULL`` = open-ended, H7). Exactly one target
  (``ck_custom_prices_one_target``); two ``EXCLUDE USING gist``
  constraints (``btree_gist``, enabled by 0021) keep the ranges of one
  target and item from overlapping.
- ``discounts`` (audited, versioned, never deleted, H8): ``kind``
  (``scholarship``, ``sibling``, ``discount``, ``promotion``), ``audience``
  (``all`` or ``assigned``, H2), a percentage or a fixed amount, the
  ``eligibility`` criteria (a JSON object validated by the API), an optional
  date window and ``is_active``. The kind rules are CHECKs: a scholarship is
  assigned, a sibling discount is for all, a promotion has both dates.
- ``discount_assignments`` (audited): the students and families an
  ``assigned`` discount is for (H2).
- ``charge_discounts`` (audited, append-only): the discounts applied to a
  charge with the discount's kind, name, percent or fixed amount as they were
  at posting (H8), the line amount and the position. Stacking (H4): the base
  price (custom, then catalog) → at most one discount per kind, kinds in the
  order scholarship → sibling → discount → promotion, each on the running
  remainder, a fixed amount capped at it, one half-up rounding per line. The
  §5.1 triggers refuse ``UPDATE``, ``DELETE`` and ``TRUNCATE`` (42501).
- ``charges.custom_price_id`` (``RESTRICT``, partial index) with
  ``ck_charges_one_price`` (at most one of ``price_version_id`` and
  ``custom_price_id``) and ``ck_charges_manual_unpriced``.
- The guard ``custom_prices_guard()`` (H7): once a charge references a custom
  price, ``DELETE`` is refused and ``UPDATE`` is refused unless only
  ``expires_on``, ``version`` and ``updated_at`` change (42501);
  ``TRUNCATE`` is always refused.

Downgrading drops exactly what the upgrade created, in reverse order.
"""

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0063"
down_revision: str | Sequence[str] | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CUSTOM: Final = "custom_prices"
DISCOUNTS: Final = "discounts"
ASSIGNMENTS: Final = "discount_assignments"
LINES: Final = "charge_discounts"
CHARGES: Final = "charges"
KINDS: Final = "kind IN ('scholarship', 'sibling', 'discount', 'promotion')"
RANGE: Final = "daterange(effective_from, expires_on, '[]')"

GUARD_FUNCTION: Final = """
CREATE FUNCTION custom_prices_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'custom_prices is never truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF EXISTS (SELECT 1 FROM charges WHERE custom_price_id = OLD.id) THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'custom_prices row is referenced'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- #95 H7: a referenced custom price may only be ended (expires_on).
        IF (to_jsonb(NEW) - 'expires_on' - 'version' - 'updated_at')
            IS DISTINCT FROM (to_jsonb(OLD) - 'expires_on' - 'version' - 'updated_at')
        THEN
            RAISE EXCEPTION 'custom_prices row is referenced'
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

APPEND_ONLY_FUNCTION: Final = f"""
CREATE FUNCTION {LINES}_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{LINES} is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def _standard_columns(*, versioned: bool) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [
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
    if versioned:
        columns.insert(
            0,
            sa.Column(
                "version", sa.Integer(), server_default=sa.text("1"), nullable=False
            ),
        )
    return columns


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _overlap(target: Any, where: Any, name: str) -> postgresql.ExcludeConstraint:
    return postgresql.ExcludeConstraint(
        (sa.text("service_price_id"), "="),
        (target, "="),
        (sa.text(RANGE), "&&"),
        name=name,
        using="gist",
        where=where,
    )


def _partial_index(
    name: str, table: str, columns: list[str], where: str, *, unique: bool = False
) -> None:
    op.create_index(
        name, table, columns, unique=unique, postgresql_where=sa.text(where)
    )


def _create_custom_prices() -> None:
    op.create_table(
        CUSTOM,
        sa.Column("service_price_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=True),
        sa.Column("family_id", sa.UUID(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        *_standard_columns(versioned=True),
        _check(CUSTOM, "one_target", "num_nonnulls(student_id, family_id) = 1"),
        _check(CUSTOM, "dates", "expires_on IS NULL OR expires_on >= effective_from"),
        _check(CUSTOM, "amount_non_negative", "amount >= 0"),
        _fk(CUSTOM, "service_price_id", "service_prices"),
        _fk(CUSTOM, "student_id", "students"),
        _fk(CUSTOM, "family_id", "families"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CUSTOM}")),
        _overlap(
            sa.text("student_id"),
            sa.text("student_id IS NOT NULL"),
            "ex_custom_prices_student_overlap",
        ),
        _overlap(
            sa.text("family_id"),
            sa.text("family_id IS NOT NULL"),
            "ex_custom_prices_family_overlap",
        ),
    )
    _partial_index(
        "ix_custom_prices_student_item",
        CUSTOM,
        ["student_id", "service_price_id"],
        "student_id IS NOT NULL",
    )
    _partial_index(
        "ix_custom_prices_family_item",
        CUSTOM,
        ["family_id", "service_price_id"],
        "family_id IS NOT NULL",
    )


def _create_discounts() -> None:
    op.create_table(
        DISCOUNTS,
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("percent", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "eligibility",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_standard_columns(versioned=True),
        _check(DISCOUNTS, "kind_valid", KINDS),
        _check(DISCOUNTS, "audience_valid", "audience IN ('all', 'assigned')"),
        _check(
            DISCOUNTS,
            "name_valid",
            "length(btrim(name)) BETWEEN 1 AND 120 AND name = btrim(name)",
        ),
        _check(
            DISCOUNTS,
            "percent_range",
            "percent IS NULL OR (percent > 0 AND percent <= 100)",
        ),
        _check(DISCOUNTS, "amount_positive", "amount IS NULL OR amount > 0"),
        _check(DISCOUNTS, "one_value", "num_nonnulls(percent, amount) = 1"),
        _check(DISCOUNTS, "eligibility_object", "jsonb_typeof(eligibility) = 'object'"),
        _check(
            DISCOUNTS,
            "dates_order",
            "starts_on IS NULL OR expires_on IS NULL OR starts_on <= expires_on",
        ),
        _check(
            DISCOUNTS,
            "scholarship_assigned",
            "kind <> 'scholarship' OR audience = 'assigned'",
        ),
        _check(DISCOUNTS, "sibling_all", "kind <> 'sibling' OR audience = 'all'"),
        _check(
            DISCOUNTS,
            "promotion_dates",
            "kind <> 'promotion' OR (starts_on IS NOT NULL AND expires_on IS NOT NULL)",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{DISCOUNTS}")),
    )
    op.create_index(
        "uq_discounts_name_lower", DISCOUNTS, [sa.text("lower(name)")], unique=True
    )


def _create_assignments() -> None:
    op.create_table(
        ASSIGNMENTS,
        sa.Column("discount_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=True),
        sa.Column("family_id", sa.UUID(), nullable=True),
        *_standard_columns(versioned=False),
        _check(ASSIGNMENTS, "one_target", "num_nonnulls(student_id, family_id) = 1"),
        _fk(ASSIGNMENTS, "discount_id", DISCOUNTS),
        _fk(ASSIGNMENTS, "student_id", "students"),
        _fk(ASSIGNMENTS, "family_id", "families"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ASSIGNMENTS}")),
    )
    _partial_index(
        "uq_discount_assignments_discount_student",
        ASSIGNMENTS,
        ["discount_id", "student_id"],
        "student_id IS NOT NULL",
        unique=True,
    )
    _partial_index(
        "uq_discount_assignments_discount_family",
        ASSIGNMENTS,
        ["discount_id", "family_id"],
        "family_id IS NOT NULL",
        unique=True,
    )
    _partial_index(
        "ix_discount_assignments_student_id",
        ASSIGNMENTS,
        ["student_id"],
        "student_id IS NOT NULL",
    )
    _partial_index(
        "ix_discount_assignments_family_id",
        ASSIGNMENTS,
        ["family_id"],
        "family_id IS NOT NULL",
    )


def _create_lines() -> None:
    op.create_table(
        LINES,
        sa.Column("charge_id", sa.UUID(), nullable=False),
        sa.Column("discount_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("percent", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("fixed_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        *_standard_columns(versioned=False),
        _check(LINES, "position_positive", "position >= 1"),
        _check(LINES, "kind_valid", KINDS),
        _check(LINES, "one_value", "num_nonnulls(percent, fixed_amount) = 1"),
        _check(LINES, "amount_positive", "amount > 0"),
        _fk(LINES, "charge_id", CHARGES),
        _fk(LINES, "discount_id", DISCOUNTS),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{LINES}")),
        sa.UniqueConstraint(
            "charge_id", "position", name="uq_charge_discounts_charge_id_position"
        ),
    )
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {LINES}_no_update_delete BEFORE UPDATE OR DELETE"
        f" ON {LINES} FOR EACH ROW EXECUTE FUNCTION {LINES}_append_only()"
    )
    op.execute(
        f"CREATE TRIGGER {LINES}_no_truncate BEFORE TRUNCATE ON {LINES}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {LINES}_append_only()"
    )


def _change_charges() -> None:
    op.add_column(CHARGES, sa.Column("custom_price_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_charges_custom_price_id_custom_prices"),
        CHARGES,
        CUSTOM,
        ["custom_price_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    _partial_index(
        "ix_charges_custom_price_id",
        CHARGES,
        ["custom_price_id"],
        "custom_price_id IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_charges_one_price"),
        CHARGES,
        "num_nonnulls(price_version_id, custom_price_id) <= 1",
    )
    op.create_check_constraint(
        op.f("ck_charges_manual_unpriced"),
        CHARGES,
        "kind <> 'manual' OR custom_price_id IS NULL",
    )


def _create_guard() -> None:
    op.execute(GUARD_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {CUSTOM}_no_referenced_change"
        f" BEFORE UPDATE OR DELETE ON {CUSTOM}"
        " FOR EACH ROW EXECUTE FUNCTION custom_prices_guard()"
    )
    op.execute(
        f"CREATE TRIGGER {CUSTOM}_no_truncate BEFORE TRUNCATE ON {CUSTOM}"
        " FOR EACH STATEMENT EXECUTE FUNCTION custom_prices_guard()"
    )


def upgrade() -> None:
    _create_custom_prices()
    _create_discounts()
    _create_assignments()
    _create_lines()
    _change_charges()
    _create_guard()


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {CUSTOM}_no_truncate ON {CUSTOM}")
    op.execute(f"DROP TRIGGER {CUSTOM}_no_referenced_change ON {CUSTOM}")
    op.execute("DROP FUNCTION custom_prices_guard()")
    op.drop_constraint(op.f("ck_charges_manual_unpriced"), CHARGES, type_="check")
    op.drop_constraint(op.f("ck_charges_one_price"), CHARGES, type_="check")
    op.drop_index("ix_charges_custom_price_id", table_name=CHARGES)
    op.drop_constraint(
        op.f("fk_charges_custom_price_id_custom_prices"), CHARGES, type_="foreignkey"
    )
    op.drop_column(CHARGES, "custom_price_id")
    op.execute(f"DROP TRIGGER {LINES}_no_truncate ON {LINES}")
    op.execute(f"DROP TRIGGER {LINES}_no_update_delete ON {LINES}")
    op.execute(f"DROP FUNCTION {LINES}_append_only()")
    op.drop_table(LINES)
    for name in (
        "ix_discount_assignments_family_id",
        "ix_discount_assignments_student_id",
        "uq_discount_assignments_discount_family",
        "uq_discount_assignments_discount_student",
    ):
        op.drop_index(name, table_name=ASSIGNMENTS)
    op.drop_table(ASSIGNMENTS)
    op.drop_index("uq_discounts_name_lower", table_name=DISCOUNTS)
    op.drop_table(DISCOUNTS)
    op.drop_index("ix_custom_prices_family_item", table_name=CUSTOM)
    op.drop_index("ix_custom_prices_student_item", table_name=CUSTOM)
    op.drop_table(CUSTOM)
