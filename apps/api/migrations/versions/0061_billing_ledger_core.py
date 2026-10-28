"""Billing: the family billing account and the ledger core.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-27

M6-05 (#90 A1-A6; spec §6, §16, W4.4; ``_docs/data-model.md`` §3.1, §3.3,
§3.9, §3.10, §3.12-§3.16, §3.26, §5.1, §7; ADR-0006).

- ``billing_accounts`` (audited, versioned): one per family (``family_id``
  UNIQUE), ``due_offset_days`` 0-60.
- ``payment_methods`` (audited, versioned; key ``code``), seeded with
  ``cash``, ``transfer`` and ``other`` here (#90 E2: ``payments`` and
  ``refunds`` reference it; #96 is its first user).
- The append-only ledger: ``charges``, ``charge_adjustments``,
  ``payments``, ``payment_reversals``, ``payment_allocations``,
  ``allocation_releases`` and ``refunds``. Composite foreign keys keep an
  allocation, its payment and its charge in one account (I4), a reversal or
  a refund in its payment's account, and an adjustment on its charge's
  account and student (I5).
- **Staged columns (#90 E3):** ``charges.tuition_plan_id`` (#93),
  ``charges.package_id`` (#94), ``charges.custom_price_id`` (#95) and
  ``refunds.withdrawal_id`` (#101) are not created here: their tables don't
  exist yet. ``ck_charges_kind`` allows ``session``, ``group_enrollment``,
  ``cohort_enrollment`` and ``manual`` only, and ``ck_charges_one_source``
  covers the three existing source columns; #93 and #94 replace both named
  CHECKs.
- **Natural keys (#90 E4):** the partial unique indexes
  ``uq_charges_session_student``, ``uq_charges_group_enrollment``,
  ``uq_charges_cohort_enrollment`` and ``uq_charge_adjustments_source_key``.
  The application checks them under the account lock and inserts through the
  ORM in a savepoint (the audit guard refuses ``ON CONFLICT`` on audited
  tables); a violation of a named key counts as "already posted".
- **Append-only triggers (data-model §5.1, the 0004 pattern):** for each of
  the seven tables, ``<table>_append_only()`` raising ``'<table> is
  append-only'`` with SQLSTATE 42501, and the triggers
  ``<table>_no_update_delete`` (``BEFORE UPDATE OR DELETE``, per row) and
  ``<table>_no_truncate`` (``BEFORE TRUNCATE``, per statement).
- **Price-version predicate (#89 E2):** ``price_version_is_referenced``
  becomes ``EXISTS`` a charge with that ``price_version_id`` (``CREATE OR
  REPLACE``); ``ix_charges_price_version_id`` serves it and the
  ``RESTRICT`` check. #94 adds packages.
- **View ``billing_ledger_entries``** (data-model §3.26): one row per ledger
  row with ``entry_type``, ``entry_id``, ``billing_account_id``, ``kind``,
  ``student_id``, ``charge_id``, ``payment_id``, ``amount``,
  ``balance_effect`` (its effect on the net balance) and ``occurred_at``.

Every foreign key is ``ON DELETE RESTRICT``. Downgrading drops the view,
restores the 0060 predicate (``SELECT false``), drops the triggers and their
functions, then the tables (with the seeded methods) in reverse order.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: str | Sequence[str] | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNTS: Final = "billing_accounts"
METHODS: Final = "payment_methods"
CHARGES: Final = "charges"
ADJUSTMENTS: Final = "charge_adjustments"
PAYMENTS: Final = "payments"
REVERSALS: Final = "payment_reversals"
ALLOCATIONS: Final = "payment_allocations"
RELEASES: Final = "allocation_releases"
REFUNDS: Final = "refunds"

APPEND_ONLY: Final = (
    CHARGES,
    ADJUSTMENTS,
    PAYMENTS,
    REVERSALS,
    ALLOCATIONS,
    RELEASES,
    REFUNDS,
)
SEED_METHODS: Final = (
    "INSERT INTO payment_methods (code) VALUES ('cash'), ('transfer'), ('other')"
)
VIEW: Final = "billing_ledger_entries"

ONE_SOURCE: Final = (
    "num_nonnulls(class_session_id, group_enrollment_id, cohort_enrollment_id)"
    " = CASE WHEN kind = 'manual' THEN 0 ELSE 1 END"
    " AND (kind = 'session') = (class_session_id IS NOT NULL)"
    " AND (kind = 'session') = (scheduled_minutes IS NOT NULL)"
    " AND (kind = 'group_enrollment') = (group_enrollment_id IS NOT NULL)"
    " AND (kind = 'cohort_enrollment') = (cohort_enrollment_id IS NOT NULL)"
    " AND (kind = 'manual') = (description IS NOT NULL)"
)

REFERENCED_PREDICATE: Final = """
CREATE OR REPLACE FUNCTION price_version_is_referenced(version_id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- #90: a version is referenced once a charge uses it. #94 (packages)
    -- adds its EXISTS check here.
    SELECT EXISTS (SELECT 1 FROM charges WHERE price_version_id = version_id)
$$
"""

UNREFERENCED_PREDICATE: Final = """
CREATE OR REPLACE FUNCTION price_version_is_referenced(version_id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- #89: nothing references a price version yet. #90 (charges) and #94
    -- (packages) replace this body with their EXISTS checks.
    SELECT false
$$
"""

LEDGER_VIEW: Final = """
CREATE VIEW billing_ledger_entries AS
SELECT 'charge'::text AS entry_type, c.id AS entry_id,
       c.billing_account_id, c.kind::text AS kind, c.student_id,
       c.id AS charge_id, NULL::uuid AS payment_id,
       c.amount, c.amount AS balance_effect, c.posted_at AS occurred_at
FROM charges c
UNION ALL
SELECT 'adjustment', a.id, a.billing_account_id, a.kind::text, a.student_id,
       a.charge_id, NULL::uuid, a.amount, a.amount, a.posted_at
FROM charge_adjustments a
UNION ALL
SELECT 'payment', p.id, p.billing_account_id, p.payment_method_code::text,
       NULL::uuid, NULL::uuid, p.id, p.amount, -p.amount, p.created_at
FROM payments p
UNION ALL
SELECT 'payment_reversal', r.id, r.billing_account_id, NULL::text, NULL::uuid,
       NULL::uuid, r.payment_id, p.amount, p.amount, r.created_at
FROM payment_reversals r
JOIN payments p ON p.id = r.payment_id
UNION ALL
SELECT 'allocation', pa.id, pa.billing_account_id, NULL::text, c.student_id,
       pa.charge_id, pa.payment_id, pa.amount, 0::numeric(14, 2), pa.created_at
FROM payment_allocations pa
JOIN charges c ON c.id = pa.charge_id
UNION ALL
SELECT 'allocation_release', ar.id, pa.billing_account_id, NULL::text,
       c.student_id, pa.charge_id, pa.payment_id, pa.amount, 0::numeric(14, 2),
       ar.created_at
FROM allocation_releases ar
JOIN payment_allocations pa ON pa.id = ar.allocation_id
JOIN charges c ON c.id = pa.charge_id
UNION ALL
SELECT 'refund', f.id, f.billing_account_id, f.payment_method_code::text,
       NULL::uuid, NULL::uuid, f.payment_id, f.amount, f.amount, f.created_at
FROM refunds f
"""
"""``balance_effect``: a charge and an adjustment add their (signed)
amount, a payment subtracts its amount, a reversal adds the payment's amount
back, a refund adds its amount (the money left the account credit);
allocations and releases move nothing. So the sum over an account is its net
balance (data-model §4.2, with I3: a payment with refunds is never
reversed)."""


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
        columns.append(
            sa.Column(
                "version", sa.Integer(), server_default=sa.text("1"), nullable=False
            )
        )
    return columns


def _money(name: str, **kwargs: object) -> sa.Column[object]:
    return sa.Column(name, sa.Numeric(precision=14, scale=2), nullable=False, **kwargs)


def _uuid(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(name, sa.UUID(), nullable=nullable)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def _method_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["payment_method_code"],
        [f"{METHODS}.code"],
        name=op.f(f"fk_{table}_payment_method_code_{METHODS}"),
        ondelete="RESTRICT",
    )


def _payment_fk(table: str) -> sa.ForeignKeyConstraint:
    """``(payment_id, billing_account_id)`` → ``payments`` (I4)."""
    return sa.ForeignKeyConstraint(
        ["payment_id", "billing_account_id"],
        [f"{PAYMENTS}.id", f"{PAYMENTS}.billing_account_id"],
        name=op.f(f"fk_{table}_payment_id_{PAYMENTS}"),
        ondelete="RESTRICT",
    )


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _text(table: str, column: str, max_length: int) -> sa.CheckConstraint:
    """Staff text: ``NULL``, or trimmed and 1 to ``max_length`` characters."""
    return _check(
        table,
        f"{column}_valid",
        f"{column} IS NULL OR (length(btrim({column})) BETWEEN 1 AND {max_length}"
        f" AND {column} = btrim({column}))",
    )


def _pk(table: str, column: str = "id") -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint(column, name=op.f(f"pk_{table}"))


def _create_accounts_and_methods() -> None:
    op.create_table(
        ACCOUNTS,
        _uuid("family_id"),
        sa.Column(
            "due_offset_days",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        *_standard_columns(versioned=True),
        _check(ACCOUNTS, "due_offset_days_range", "due_offset_days BETWEEN 0 AND 60"),
        _fk(ACCOUNTS, "family_id", "families"),
        _pk(ACCOUNTS),
        sa.UniqueConstraint("family_id", name="uq_billing_accounts_family_id"),
    )
    op.create_table(
        METHODS,
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *[c for c in _standard_columns(versioned=True) if c.name != "id"],
        _check(METHODS, "code_format", "code ~ '^[a-z][a-z0-9_]*$'"),
        _pk(METHODS, "code"),
    )
    op.execute(SEED_METHODS)


def _create_charges() -> None:
    op.create_table(
        CHARGES,
        _uuid("billing_account_id"),
        _uuid("student_id"),
        sa.Column("kind", sa.String(length=24), nullable=False),
        _uuid("class_session_id", nullable=True),
        _uuid("group_enrollment_id", nullable=True),
        _uuid("cohort_enrollment_id", nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("service_period_end", sa.Date(), nullable=True),
        _uuid("price_version_id", nullable=True),
        _uuid("education_level_id", nullable=True),
        sa.Column("scheduled_minutes", sa.SmallInteger(), nullable=True),
        _money("list_amount"),
        _money("discount_amount", server_default=sa.text("0")),
        _money("amount"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        _uuid("posted_by_user_id", nullable=True),
        *_standard_columns(),
        _check(
            CHARGES,
            "kind",
            "kind IN ('session', 'group_enrollment', 'cohort_enrollment', 'manual')",
        ),
        _check(CHARGES, "one_source", ONE_SOURCE),
        _text(CHARGES, "description", 200),
        _check(
            CHARGES,
            "service_period_end_valid",
            "service_period_end IS NULL OR service_period_end >= service_date",
        ),
        _check(
            CHARGES,
            "scheduled_minutes_range",
            "scheduled_minutes IS NULL OR scheduled_minutes BETWEEN 15 AND 480",
        ),
        _check(CHARGES, "list_amount_non_negative", "list_amount >= 0"),
        _check(CHARGES, "discount_amount_non_negative", "discount_amount >= 0"),
        _check(CHARGES, "amount_non_negative", "amount >= 0"),
        _check(CHARGES, "amount_equation", "amount = list_amount - discount_amount"),
        _fk(CHARGES, "billing_account_id", ACCOUNTS),
        _fk(CHARGES, "student_id", "students"),
        _fk(CHARGES, "class_session_id", "class_sessions"),
        _fk(CHARGES, "group_enrollment_id", "group_enrollments"),
        _fk(CHARGES, "cohort_enrollment_id", "cohort_enrollments"),
        _fk(CHARGES, "price_version_id", "price_versions"),
        _fk(CHARGES, "education_level_id", "education_levels"),
        _fk(CHARGES, "posted_by_user_id", "users"),
        _pk(CHARGES),
        sa.UniqueConstraint("id", "billing_account_id", name="uq_charges_id_account"),
        sa.UniqueConstraint(
            "id",
            "billing_account_id",
            "student_id",
            name="uq_charges_id_account_student",
        ),
    )
    op.create_index(
        "uq_charges_session_student",
        CHARGES,
        ["class_session_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'session'"),
    )
    op.create_index(
        "uq_charges_group_enrollment",
        CHARGES,
        ["group_enrollment_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'group_enrollment'"),
    )
    op.create_index(
        "uq_charges_cohort_enrollment",
        CHARGES,
        ["cohort_enrollment_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'cohort_enrollment'"),
    )
    op.create_index(
        "ix_charges_account_posted_at",
        CHARGES,
        ["billing_account_id", "posted_at", "id"],
    )
    op.create_index(
        "ix_charges_student_service_date", CHARGES, ["student_id", "service_date"]
    )
    op.create_index(
        "ix_charges_account_due_date", CHARGES, ["billing_account_id", "due_date"]
    )
    op.create_index(
        "ix_charges_price_version_id",
        CHARGES,
        ["price_version_id"],
        postgresql_where=sa.text("price_version_id IS NOT NULL"),
    )


def _create_adjustments() -> None:
    op.create_table(
        ADJUSTMENTS,
        _uuid("charge_id"),
        _uuid("billing_account_id"),
        _uuid("student_id"),
        sa.Column("kind", sa.String(length=24), nullable=False),
        _money("amount"),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("source_key", sa.String(length=200), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        _uuid("posted_by_user_id", nullable=True),
        *_standard_columns(),
        _check(
            ADJUSTMENTS,
            "kind",
            "kind IN ('duration_change', 'cancellation_credit', 'closure_credit',"
            " 'enrollment_removal', 'withdrawal', 'manual')",
        ),
        _check(ADJUSTMENTS, "amount_non_zero", "amount <> 0"),
        _text(ADJUSTMENTS, "reason", 500),
        _check(
            ADJUSTMENTS,
            "manual_reason_required",
            "kind <> 'manual' OR reason IS NOT NULL",
        ),
        _check(
            ADJUSTMENTS,
            "source_key_required",
            "kind = 'manual' OR source_key IS NOT NULL",
        ),
        sa.ForeignKeyConstraint(
            ["charge_id", "billing_account_id", "student_id"],
            [f"{CHARGES}.id", f"{CHARGES}.billing_account_id", f"{CHARGES}.student_id"],
            name=op.f("fk_charge_adjustments_charge_id_charges"),
            ondelete="RESTRICT",
        ),
        _fk(ADJUSTMENTS, "posted_by_user_id", "users"),
        _pk(ADJUSTMENTS),
    )
    op.create_index(
        "uq_charge_adjustments_source_key",
        ADJUSTMENTS,
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("source_key IS NOT NULL"),
    )
    op.create_index("ix_charge_adjustments_charge_id", ADJUSTMENTS, ["charge_id"])


def _create_payment_side() -> None:
    op.create_table(
        PAYMENTS,
        _uuid("billing_account_id"),
        _money("amount"),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("payment_method_code", sa.String(length=32), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("payer_name", sa.String(length=200), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        _uuid("recorded_by_user_id"),
        *_standard_columns(),
        _check(PAYMENTS, "amount_positive", "amount > 0"),
        _text(PAYMENTS, "reference", 100),
        _text(PAYMENTS, "payer_name", 200),
        _text(PAYMENTS, "note", 500),
        _fk(PAYMENTS, "billing_account_id", ACCOUNTS),
        _method_fk(PAYMENTS),
        _fk(PAYMENTS, "recorded_by_user_id", "users"),
        _pk(PAYMENTS),
        sa.UniqueConstraint("id", "billing_account_id", name="uq_payments_id_account"),
    )
    op.create_index(
        "ix_payments_account_received_on",
        PAYMENTS,
        ["billing_account_id", "received_on", "id"],
    )
    op.create_table(
        REVERSALS,
        _uuid("payment_id"),
        _uuid("billing_account_id"),
        sa.Column("reason", sa.String(length=500), nullable=False),
        _uuid("reversed_by_user_id"),
        *_standard_columns(),
        _text(REVERSALS, "reason", 500),
        _payment_fk(REVERSALS),
        _fk(REVERSALS, "reversed_by_user_id", "users"),
        _pk(REVERSALS),
        sa.UniqueConstraint("payment_id", name="uq_payment_reversals_payment_id"),
    )
    op.create_table(
        ALLOCATIONS,
        _uuid("payment_id"),
        _uuid("charge_id"),
        _uuid("billing_account_id"),
        _money("amount"),
        _uuid("created_by_user_id", nullable=True),
        *_standard_columns(),
        _check(ALLOCATIONS, "amount_positive", "amount > 0"),
        _payment_fk(ALLOCATIONS),
        sa.ForeignKeyConstraint(
            ["charge_id", "billing_account_id"],
            [f"{CHARGES}.id", f"{CHARGES}.billing_account_id"],
            name=op.f("fk_payment_allocations_charge_id_charges"),
            ondelete="RESTRICT",
        ),
        _fk(ALLOCATIONS, "created_by_user_id", "users"),
        _pk(ALLOCATIONS),
    )
    op.create_index("ix_payment_allocations_charge_id", ALLOCATIONS, ["charge_id"])
    op.create_index("ix_payment_allocations_payment_id", ALLOCATIONS, ["payment_id"])
    op.create_table(
        RELEASES,
        _uuid("allocation_id"),
        _uuid("payment_reversal_id", nullable=True),
        _uuid("charge_adjustment_id", nullable=True),
        _uuid("released_by_user_id", nullable=True),
        *_standard_columns(),
        _check(
            RELEASES,
            "one_cause",
            "num_nonnulls(payment_reversal_id, charge_adjustment_id) = 1",
        ),
        _fk(RELEASES, "allocation_id", ALLOCATIONS),
        _fk(RELEASES, "payment_reversal_id", REVERSALS),
        _fk(RELEASES, "charge_adjustment_id", ADJUSTMENTS),
        _fk(RELEASES, "released_by_user_id", "users"),
        _pk(RELEASES),
        sa.UniqueConstraint(
            "allocation_id", name="uq_allocation_releases_allocation_id"
        ),
    )
    op.create_table(
        REFUNDS,
        _uuid("payment_id"),
        _uuid("billing_account_id"),
        _money("amount"),
        sa.Column("refunded_on", sa.Date(), nullable=False),
        sa.Column("payment_method_code", sa.String(length=32), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        _uuid("refunded_by_user_id"),
        *_standard_columns(),
        _check(REFUNDS, "amount_positive", "amount > 0"),
        _text(REFUNDS, "reference", 100),
        _text(REFUNDS, "reason", 500),
        _payment_fk(REFUNDS),
        _method_fk(REFUNDS),
        _fk(REFUNDS, "refunded_by_user_id", "users"),
        _pk(REFUNDS),
    )
    op.create_index("ix_refunds_payment_id", REFUNDS, ["payment_id"])


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


def upgrade() -> None:
    _create_accounts_and_methods()
    _create_charges()
    _create_adjustments()
    _create_payment_side()
    _create_append_only_triggers()
    op.execute(REFERENCED_PREDICATE)
    op.execute(LEDGER_VIEW)


def downgrade() -> None:
    op.execute(f"DROP VIEW {VIEW}")
    op.execute(UNREFERENCED_PREDICATE)
    for table in reversed(APPEND_ONLY):
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER {table}_no_update_delete ON {table}")
        op.execute(f"DROP FUNCTION {table}_append_only()")
    op.drop_table(REFUNDS)
    op.drop_table(RELEASES)
    op.drop_index("ix_payment_allocations_payment_id", table_name=ALLOCATIONS)
    op.drop_index("ix_payment_allocations_charge_id", table_name=ALLOCATIONS)
    op.drop_table(ALLOCATIONS)
    op.drop_table(REVERSALS)
    op.drop_index("ix_payments_account_received_on", table_name=PAYMENTS)
    op.drop_table(PAYMENTS)
    op.drop_index("ix_charge_adjustments_charge_id", table_name=ADJUSTMENTS)
    op.drop_index("uq_charge_adjustments_source_key", table_name=ADJUSTMENTS)
    op.drop_table(ADJUSTMENTS)
    for index in (
        "ix_charges_price_version_id",
        "ix_charges_account_due_date",
        "ix_charges_student_service_date",
        "ix_charges_account_posted_at",
        "uq_charges_cohort_enrollment",
        "uq_charges_group_enrollment",
        "uq_charges_session_student",
    ):
        op.drop_index(index, table_name=CHARGES)
    op.drop_table(CHARGES)
    op.execute("DELETE FROM payment_methods")
    op.drop_table(METHODS)
    op.drop_table(ACCOUNTS)
