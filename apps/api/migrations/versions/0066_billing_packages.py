"""Billing: prepaid packages and their credit.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-29

M6-09 (#94 A1-A7; ``_docs/data-model.md`` §3.9, §3.18-§3.20, §4.1 I6/I8/I9,
§5.1, §5.2; ADR-0006 E1, E3, E10).

- ``package_products`` (mutable config, versioned, audited): a product owns
  one ``service_prices`` row (``pricing_model = 'package'``, UNIQUE) whose
  versions are its price; ``unit`` (``sessions | minutes``), ``quantity``,
  ``validity_days`` and the optional ``subject_id`` are fixed after creation
  (the API refuses changes, #94 L7).
- ``packages`` (append-only, audited): one purchase, for one billing
  account and one student (never shared, spec §6), snapshotting the
  product's unit, quantity (``units_granted``) and subject, the price
  version paid and the purchase and expiry dates (center-local ``DATE``s).
  ``UNIQUE (id, billing_account_id, student_id)`` is the target of the
  composite foreign key from ``charges``.
- ``package_credit_transactions`` (append-only, audited): the signed unit
  movements. **Staged (#94 L10, #90 E3):** created without
  ``withdrawal_id`` ("column added by #101"); ``ck_package_credit_
  transactions_kind`` allows ``grant``, ``consume``, ``expire`` and
  ``reversal``; #101 adds ``refund`` with its column and replaces the CHECK.
  Natural keys (partial unique indexes): one ``grant`` and one ``expire`` per
  package, one ``consume`` per ``(package_id, class_session_id)``, one
  ``reversal`` per reversed transaction.
- ``charges.package_id`` with the composite foreign key
  ``fk_charges_package`` ``(package_id, billing_account_id, student_id)``
  and the natural key ``uq_charges_package_purchase``; ``ck_charges_kind``
  gains ``package_purchase`` and ``ck_charges_one_source`` counts
  ``package_id`` and adds ``(kind = 'package_purchase') = (package_id IS NOT
  NULL)``, **every #90/#93 part kept verbatim** (``tuition_period``,
  ``tuition_plan_id``, ``service_period_end``).
- ``price_version_is_referenced`` (#89 E2) also answers true for a version a
  package was bought at (§5.2).

The append-only tables get the §5.1 trigger pair (``<table>_append_only()``,
SQLSTATE 42501). No time-zone dependency is registered: every date is a
center-local ``DATE`` (#37 E1).

Downgrading restores both charge CHECKs and the predicate exactly as 0065
left them, then drops what the upgrade created, in reverse order. A
downgrade with ``package_purchase`` charges present fails on the restored
``ck_charges_kind`` (charges are append-only, so they can't be removed
first).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: str | Sequence[str] | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRODUCTS: Final = "package_products"
PACKAGES: Final = "packages"
TRANSACTIONS: Final = "package_credit_transactions"
CHARGES: Final = "charges"
APPEND_ONLY: Final = (PACKAGES, TRANSACTIONS)
CHARGE_FK: Final = "fk_charges_package"
PURCHASE_KEY: Final = "uq_charges_package_purchase"
UNITS: Final = "('sessions', 'minutes')"

KIND_0065: Final = (
    "kind IN ('session', 'group_enrollment', 'cohort_enrollment',"
    " 'tuition_period', 'manual')"
)
KIND: Final = (
    "kind IN ('session', 'group_enrollment', 'cohort_enrollment',"
    " 'tuition_period', 'package_purchase', 'manual')"
)
ONE_SOURCE_0065: Final = (
    "num_nonnulls(class_session_id, group_enrollment_id, cohort_enrollment_id,"
    " tuition_plan_id) = CASE WHEN kind = 'manual' THEN 0 ELSE 1 END"
    " AND (kind = 'session') = (class_session_id IS NOT NULL)"
    " AND (kind = 'session') = (scheduled_minutes IS NOT NULL)"
    " AND (kind = 'group_enrollment') = (group_enrollment_id IS NOT NULL)"
    " AND (kind = 'cohort_enrollment') = (cohort_enrollment_id IS NOT NULL)"
    " AND (kind = 'tuition_period') = (tuition_plan_id IS NOT NULL)"
    " AND (kind = 'tuition_period') = (service_period_end IS NOT NULL)"
    " AND (kind = 'manual') = (description IS NOT NULL)"
)
"""``ck_charges_one_source`` as revision 0065 left it (#93 A5)."""
ONE_SOURCE: Final = (
    "num_nonnulls(class_session_id, group_enrollment_id, cohort_enrollment_id,"
    " tuition_plan_id, package_id) = CASE WHEN kind = 'manual' THEN 0 ELSE 1 END"
    " AND (kind = 'session') = (class_session_id IS NOT NULL)"
    " AND (kind = 'session') = (scheduled_minutes IS NOT NULL)"
    " AND (kind = 'group_enrollment') = (group_enrollment_id IS NOT NULL)"
    " AND (kind = 'cohort_enrollment') = (cohort_enrollment_id IS NOT NULL)"
    " AND (kind = 'tuition_period') = (tuition_plan_id IS NOT NULL)"
    " AND (kind = 'tuition_period') = (service_period_end IS NOT NULL)"
    " AND (kind = 'package_purchase') = (package_id IS NOT NULL)"
    " AND (kind = 'manual') = (description IS NOT NULL)"
)
"""``ck_charges_one_source`` from this revision (#94 A5)."""

TRANSACTION_KIND: Final = "kind IN ('grant', 'consume', 'expire', 'reversal')"
"""``ck_package_credit_transactions_kind`` (#94 L10: ``refund`` is #101's)."""
TRANSACTION_SIGN: Final = (
    "(kind = 'grant' AND units > 0)"
    " OR (kind IN ('consume', 'expire') AND units < 0)"
    " OR (kind = 'reversal' AND units <> 0)"
)
"""``ck_package_credit_transactions_units_sign`` (I6)."""

REFERENCED_PREDICATE_0061: Final = """
CREATE OR REPLACE FUNCTION price_version_is_referenced(version_id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- #90: a version is referenced once a charge uses it. #94 (packages)
    -- adds its EXISTS check here.
    SELECT EXISTS (SELECT 1 FROM charges WHERE price_version_id = version_id)
$$
"""
"""The predicate body revision 0061 created (restored by the downgrade)."""

REFERENCED_PREDICATE: Final = """
CREATE OR REPLACE FUNCTION price_version_is_referenced(version_id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- #90: a version is referenced once a charge uses it; #94: or once a
    -- package was bought at it.
    SELECT EXISTS (SELECT 1 FROM charges WHERE price_version_id = version_id)
        OR EXISTS (SELECT 1 FROM packages WHERE price_version_id = version_id)
$$
"""


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


def _standard_columns(*, versioned: bool = False) -> list[sa.Column[object]]:
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


def _create_products() -> None:
    op.create_table(
        PRODUCTS,
        sa.Column("service_price_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("validity_days", sa.SmallInteger(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_standard_columns(versioned=True),
        _check(
            PRODUCTS,
            "name_valid",
            "length(btrim(name)) BETWEEN 1 AND 120 AND name = btrim(name)",
        ),
        _check(PRODUCTS, "unit_valid", f"unit IN {UNITS}"),
        _check(PRODUCTS, "quantity_positive", "quantity > 0"),
        _check(PRODUCTS, "validity_days_positive", "validity_days > 0"),
        _fk(PRODUCTS, "service_price_id", "service_prices"),
        _fk(PRODUCTS, "subject_id", "subjects"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PRODUCTS}")),
        sa.UniqueConstraint(
            "service_price_id", name=op.f(f"uq_{PRODUCTS}_service_price_id")
        ),
    )


def _create_packages() -> None:
    op.create_table(
        PACKAGES,
        sa.Column("billing_account_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("package_product_id", sa.UUID(), nullable=False),
        sa.Column("price_version_id", sa.UUID(), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("units_granted", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("purchased_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        *_standard_columns(),
        _check(PACKAGES, "unit_valid", f"unit IN {UNITS}"),
        _check(PACKAGES, "units_granted_positive", "units_granted > 0"),
        _check(PACKAGES, "dates", "expires_on > purchased_on"),
        _fk(PACKAGES, "billing_account_id", "billing_accounts"),
        _fk(PACKAGES, "student_id", "students"),
        _fk(PACKAGES, "package_product_id", PRODUCTS),
        _fk(PACKAGES, "price_version_id", "price_versions"),
        _fk(PACKAGES, "subject_id", "subjects"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PACKAGES}")),
        sa.UniqueConstraint(
            "id",
            "billing_account_id",
            "student_id",
            name="uq_packages_id_account_student",
        ),
    )
    op.create_index(
        "ix_packages_account_student_expires_on",
        PACKAGES,
        ["billing_account_id", "student_id", "expires_on"],
    )
    op.create_index("ix_packages_price_version_id", PACKAGES, ["price_version_id"])


def _create_transactions() -> None:
    op.create_table(
        TRANSACTIONS,
        sa.Column("package_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("class_session_id", sa.UUID(), nullable=True),
        sa.Column("student_id", sa.UUID(), nullable=True),
        sa.Column("reverses_transaction_id", sa.UUID(), nullable=True),
        *_standard_columns(),
        _check(TRANSACTIONS, "kind", TRANSACTION_KIND),
        _check(TRANSACTIONS, "units_sign", TRANSACTION_SIGN),
        _check(
            TRANSACTIONS,
            "consume_session",
            "(class_session_id IS NOT NULL) = (kind = 'consume')",
        ),
        _check(
            TRANSACTIONS,
            "consume_student",
            "(student_id IS NOT NULL) = (kind = 'consume')",
        ),
        _check(
            TRANSACTIONS,
            "reversal_target",
            "(reverses_transaction_id IS NOT NULL) = (kind = 'reversal')",
        ),
        _fk(TRANSACTIONS, "package_id", PACKAGES),
        _fk(TRANSACTIONS, "class_session_id", "class_sessions"),
        _fk(TRANSACTIONS, "student_id", "students"),
        sa.ForeignKeyConstraint(
            ["reverses_transaction_id"],
            [f"{TRANSACTIONS}.id"],
            name="fk_package_credit_transactions_reverses_transaction_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TRANSACTIONS}")),
    )
    for name, columns, where in (
        ("uq_package_credit_transactions_grant", ["package_id"], "kind = 'grant'"),
        ("uq_package_credit_transactions_expire", ["package_id"], "kind = 'expire'"),
        (
            "uq_package_credit_transactions_consume",
            ["package_id", "class_session_id"],
            "kind = 'consume'",
        ),
        (
            "uq_package_credit_transactions_reversal",
            ["reverses_transaction_id"],
            "kind = 'reversal'",
        ),
    ):
        op.create_index(
            name,
            TRANSACTIONS,
            columns,
            unique=True,
            postgresql_where=sa.text(where),
        )
    op.create_index(
        "ix_package_credit_transactions_package_id", TRANSACTIONS, ["package_id"]
    )
    op.create_index(
        "ix_package_credit_transactions_session_student",
        TRANSACTIONS,
        ["class_session_id", "student_id"],
        postgresql_where=sa.text("kind = 'consume'"),
    )


def _create_append_only_triggers() -> None:
    for table in APPEND_ONLY:
        op.execute(_append_only_function(table))
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE"
            f" ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}"
            f" FOR EACH STATEMENT EXECUTE FUNCTION {table}_append_only()"
        )


def _change_charges() -> None:
    op.add_column(CHARGES, sa.Column("package_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        CHARGE_FK,
        CHARGES,
        PACKAGES,
        ["package_id", "billing_account_id", "student_id"],
        ["id", "billing_account_id", "student_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("ck_charges_kind"), CHARGES, type_="check")
    op.create_check_constraint(op.f("ck_charges_kind"), CHARGES, KIND)
    op.drop_constraint(op.f("ck_charges_one_source"), CHARGES, type_="check")
    op.create_check_constraint(op.f("ck_charges_one_source"), CHARGES, ONE_SOURCE)
    op.create_index(
        PURCHASE_KEY,
        CHARGES,
        ["package_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'package_purchase'"),
    )


def upgrade() -> None:
    _create_products()
    _create_packages()
    _create_transactions()
    _create_append_only_triggers()
    _change_charges()
    op.execute(REFERENCED_PREDICATE)


def downgrade() -> None:
    op.execute(REFERENCED_PREDICATE_0061)
    op.drop_index(PURCHASE_KEY, table_name=CHARGES)
    op.drop_constraint(op.f("ck_charges_one_source"), CHARGES, type_="check")
    op.create_check_constraint(op.f("ck_charges_one_source"), CHARGES, ONE_SOURCE_0065)
    op.drop_constraint(op.f("ck_charges_kind"), CHARGES, type_="check")
    op.create_check_constraint(op.f("ck_charges_kind"), CHARGES, KIND_0065)
    op.drop_constraint(CHARGE_FK, CHARGES, type_="foreignkey")
    op.drop_column(CHARGES, "package_id")
    for table in reversed(APPEND_ONLY):
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER {table}_no_update_delete ON {table}")
        op.execute(f"DROP FUNCTION {table}_append_only()")
    op.drop_index(
        "ix_package_credit_transactions_session_student", table_name=TRANSACTIONS
    )
    op.drop_index("ix_package_credit_transactions_package_id", table_name=TRANSACTIONS)
    for name in (
        "uq_package_credit_transactions_reversal",
        "uq_package_credit_transactions_consume",
        "uq_package_credit_transactions_expire",
        "uq_package_credit_transactions_grant",
    ):
        op.drop_index(name, table_name=TRANSACTIONS)
    op.drop_table(TRANSACTIONS)
    op.drop_index("ix_packages_price_version_id", table_name=PACKAGES)
    op.drop_index("ix_packages_account_student_expires_on", table_name=PACKAGES)
    op.drop_table(PACKAGES)
    op.drop_table(PRODUCTS)
