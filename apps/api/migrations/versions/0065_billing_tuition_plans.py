"""Billing: recurring tuition plans.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-28

M6-08 (#93 A1-A5; ``_docs/data-model.md`` §3.4, §3.9, §3.17, §3.17a;
ADR-0006 E1, E9, E11).

**K2 (the period length is in the catalog key).** ``service_prices`` gains
``period_frequency VARCHAR(16) NULL`` (``weekly | biweekly | monthly``),
set **if and only if** ``pricing_model = 'per_period'``
(``ck_service_prices_period_frequency``). ``uq_service_prices_catalog_key``
is re-created with the frequency (still ``NULLS NOT DISTINCT`` and ``WHERE
pricing_model <> 'package'``), so a weekly and a monthly price for the same
subject, level and scope are two items. No existing row is ``per_period``
(the #89 routes refused it), so the CHECK holds for every row. A plan pins
its item (``tuition_plans.service_price_id``) and copies its frequency.

**K3 (the period grid).** Periods are anchored at ``starts_on`` and always
whole: weekly ``+7k`` days, biweekly ``+14k`` days, monthly ``+k`` calendar
months from ``starts_on`` with the day clamped to the month's length. All
dates are center-local calendar dates (ADR-0004); nothing stores an instant.
``ends_on`` is a period end (the API checks it); no proration, ever.

**K4 (pauses).** ``tuition_plan_pauses`` (audited, versioned): period-aligned
``[starts_on, ends_on]`` (``ends_on NULL`` = until resumed), never
overlapping for one plan (``EXCLUDE USING gist``, ``btree_gist`` since
0021). A period whose start falls inside a pause is never charged, not even
after a resume. ``ON DELETE CASCADE`` from the plan: a plan can only be
deleted while it has no charge.

**K6 (group per-student pricing).** A plan may be linked to a group
enrollment (``group_enrollment_id``); two linked plans of one enrollment
never overlap, nor do two standalone plans of one student and item (two
partial ``EXCLUDE USING gist`` constraints on ``daterange(starts_on,
ends_on, '[]')``).

**K9 (the billed account).** A plan is bound to the student's family
account at creation. ``UNIQUE (id, billing_account_id, student_id)`` is the
target of ``fk_charges_tuition_plan`` ``(tuition_plan_id,
billing_account_id, student_id)``, so a tuition charge always posts to its
plan's account and student.

The trigger ``tuition_plans_key_immutable`` (``BEFORE UPDATE``, SQLSTATE
42501) refuses changes to ``billing_account_id``, ``student_id``,
``service_price_id``, ``group_enrollment_id``, ``frequency`` and
``starts_on``: they define the period grid and the composite key. Only
``ends_on``, ``due_offset_days`` and the standard columns change.

**Charges (#90 E3 staged column).** ``charges.tuition_plan_id`` with the
composite foreign key; ``ck_charges_kind`` gains ``tuition_period``;
``ck_charges_one_source`` counts ``tuition_plan_id`` and adds
``(kind = 'tuition_period') = (tuition_plan_id IS NOT NULL)`` and ``(kind =
'tuition_period') = (service_period_end IS NOT NULL)``, every earlier
implication kept verbatim; the natural key ``uq_charges_tuition_period``
``(tuition_plan_id, service_date) WHERE kind = 'tuition_period'``. The
append-only row triggers of ``charges`` fire on ``UPDATE``/``DELETE``/
``TRUNCATE`` only, so they don't block the DDL.

Downgrading restores both CHECKs exactly and drops what the upgrade
created, in reverse order. A downgrade with ``tuition_period`` charges
present fails on the restored ``ck_charges_kind`` (charges are append-only,
so they can't be removed first); with ``per_period`` items present the
re-created #89 key may fail when a weekly and a monthly item share a key.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065"
down_revision: str | Sequence[str] | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICES: Final = "service_prices"
PLANS: Final = "tuition_plans"
PAUSES: Final = "tuition_plan_pauses"
CHARGES: Final = "charges"
CATALOG_KEY_INDEX: Final = "uq_service_prices_catalog_key"
CATALOG_KEY_0060: Final = (
    "class_type",
    "pricing_model",
    "subject_id",
    "education_level_id",
    "duration_minutes",
    "cohort_id",
    "group_id",
)
CATALOG_KEY: Final = (*CATALOG_KEY_0060, "period_frequency")
FREQUENCIES: Final = "('weekly', 'biweekly', 'monthly')"
PERIOD_FREQUENCY_CHECK: Final = (
    f"(period_frequency IS NULL OR period_frequency IN {FREQUENCIES})"
    " AND ((pricing_model = 'per_period') = (period_frequency IS NOT NULL))"
)
"""``ck_service_prices_period_frequency`` (K2)."""
RANGE: Final = "daterange(starts_on, ends_on, '[]')"
TRIGGER: Final = "tuition_plans_key_immutable"
CHARGE_FK: Final = "fk_charges_tuition_plan"
TUITION_KEY: Final = "uq_charges_tuition_period"

KIND_0061: Final = (
    "kind IN ('session', 'group_enrollment', 'cohort_enrollment', 'manual')"
)
KIND: Final = (
    "kind IN ('session', 'group_enrollment', 'cohort_enrollment',"
    " 'tuition_period', 'manual')"
)
ONE_SOURCE_0061: Final = (
    "num_nonnulls(class_session_id, group_enrollment_id, cohort_enrollment_id)"
    " = CASE WHEN kind = 'manual' THEN 0 ELSE 1 END"
    " AND (kind = 'session') = (class_session_id IS NOT NULL)"
    " AND (kind = 'session') = (scheduled_minutes IS NOT NULL)"
    " AND (kind = 'group_enrollment') = (group_enrollment_id IS NOT NULL)"
    " AND (kind = 'cohort_enrollment') = (cohort_enrollment_id IS NOT NULL)"
    " AND (kind = 'manual') = (description IS NOT NULL)"
)
"""``ck_charges_one_source`` as revision 0061 created it (#90 A2)."""
ONE_SOURCE: Final = (
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
"""``ck_charges_one_source`` from this revision (#93 A5)."""

KEY_IMMUTABLE_FUNCTION: Final = f"""
CREATE FUNCTION {TRIGGER}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.billing_account_id IS DISTINCT FROM OLD.billing_account_id
        OR NEW.student_id IS DISTINCT FROM OLD.student_id
        OR NEW.service_price_id IS DISTINCT FROM OLD.service_price_id
        OR NEW.group_enrollment_id IS DISTINCT FROM OLD.group_enrollment_id
        OR NEW.frequency IS DISTINCT FROM OLD.frequency
        OR NEW.starts_on IS DISTINCT FROM OLD.starts_on
    THEN
        RAISE EXCEPTION 'the key of a tuition plan never changes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""


def _standard_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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


def _fk(
    table: str, column: str, target: str, *, ondelete: str = "RESTRICT"
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=ondelete,
    )


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _change_service_prices() -> None:
    op.add_column(
        PRICES, sa.Column("period_frequency", sa.String(length=16), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_service_prices_period_frequency"), PRICES, PERIOD_FREQUENCY_CHECK
    )
    op.drop_index(CATALOG_KEY_INDEX, table_name=PRICES)
    op.create_index(
        CATALOG_KEY_INDEX,
        PRICES,
        list(CATALOG_KEY),
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("pricing_model <> 'package'"),
    )


def _create_plans() -> None:
    op.create_table(
        PLANS,
        sa.Column("billing_account_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("service_price_id", sa.UUID(), nullable=False),
        sa.Column("group_enrollment_id", sa.UUID(), nullable=True),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column(
            "due_offset_days",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        *_standard_columns(),
        _check(PLANS, "frequency_valid", f"frequency IN {FREQUENCIES}"),
        _check(PLANS, "dates", "ends_on IS NULL OR ends_on >= starts_on"),
        _check(PLANS, "due_offset_days_range", "due_offset_days BETWEEN 0 AND 60"),
        _fk(PLANS, "billing_account_id", "billing_accounts"),
        _fk(PLANS, "student_id", "students"),
        _fk(PLANS, "service_price_id", "service_prices"),
        _fk(PLANS, "group_enrollment_id", "group_enrollments"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PLANS}")),
        sa.UniqueConstraint(
            "id",
            "billing_account_id",
            "student_id",
            name="uq_tuition_plans_id_account_student",
        ),
        postgresql.ExcludeConstraint(
            (sa.text("group_enrollment_id"), "="),
            (sa.text(RANGE), "&&"),
            name="ex_tuition_plans_enrollment_overlap",
            using="gist",
            where=sa.text("group_enrollment_id IS NOT NULL"),
        ),
        postgresql.ExcludeConstraint(
            (sa.text("student_id"), "="),
            (sa.text("service_price_id"), "="),
            (sa.text(RANGE), "&&"),
            name="ex_tuition_plans_student_item_overlap",
            using="gist",
            where=sa.text("group_enrollment_id IS NULL"),
        ),
    )
    op.create_index("ix_tuition_plans_student_id", PLANS, ["student_id"])
    op.create_index(
        "ix_tuition_plans_billing_account_id", PLANS, ["billing_account_id"]
    )
    op.execute(KEY_IMMUTABLE_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {PLANS}"
        f" FOR EACH ROW EXECUTE FUNCTION {TRIGGER}()"
    )


def _create_pauses() -> None:
    op.create_table(
        PAUSES,
        sa.Column("tuition_plan_id", sa.UUID(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        *_standard_columns(),
        _check(PAUSES, "dates", "ends_on IS NULL OR ends_on >= starts_on"),
        _fk(PAUSES, "tuition_plan_id", PLANS, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PAUSES}")),
        postgresql.ExcludeConstraint(
            (sa.text("tuition_plan_id"), "="),
            (sa.text(RANGE), "&&"),
            name="ex_tuition_plan_pauses_overlap",
            using="gist",
        ),
    )


def _change_charges() -> None:
    op.add_column(CHARGES, sa.Column("tuition_plan_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        CHARGE_FK,
        CHARGES,
        PLANS,
        ["tuition_plan_id", "billing_account_id", "student_id"],
        ["id", "billing_account_id", "student_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("ck_charges_kind"), CHARGES, type_="check")
    op.create_check_constraint(op.f("ck_charges_kind"), CHARGES, KIND)
    op.drop_constraint(op.f("ck_charges_one_source"), CHARGES, type_="check")
    op.create_check_constraint(op.f("ck_charges_one_source"), CHARGES, ONE_SOURCE)
    op.create_index(
        TUITION_KEY,
        CHARGES,
        ["tuition_plan_id", "service_date"],
        unique=True,
        postgresql_where=sa.text("kind = 'tuition_period'"),
    )


def upgrade() -> None:
    _change_service_prices()
    _create_plans()
    _create_pauses()
    _change_charges()


def downgrade() -> None:
    op.drop_index(TUITION_KEY, table_name=CHARGES)
    op.drop_constraint(op.f("ck_charges_one_source"), CHARGES, type_="check")
    op.create_check_constraint(op.f("ck_charges_one_source"), CHARGES, ONE_SOURCE_0061)
    op.drop_constraint(op.f("ck_charges_kind"), CHARGES, type_="check")
    op.create_check_constraint(op.f("ck_charges_kind"), CHARGES, KIND_0061)
    op.drop_constraint(CHARGE_FK, CHARGES, type_="foreignkey")
    op.drop_column(CHARGES, "tuition_plan_id")
    op.drop_table(PAUSES)
    op.execute(f"DROP TRIGGER {TRIGGER} ON {PLANS}")
    op.execute(f"DROP FUNCTION {TRIGGER}()")
    op.drop_index("ix_tuition_plans_billing_account_id", table_name=PLANS)
    op.drop_index("ix_tuition_plans_student_id", table_name=PLANS)
    op.drop_table(PLANS)
    op.drop_index(CATALOG_KEY_INDEX, table_name=PRICES)
    op.create_index(
        CATALOG_KEY_INDEX,
        PRICES,
        list(CATALOG_KEY_0060),
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("pricing_model <> 'package'"),
    )
    op.drop_constraint(
        op.f("ck_service_prices_period_frequency"), PRICES, type_="check"
    )
    op.drop_column(PRICES, "period_frequency")
