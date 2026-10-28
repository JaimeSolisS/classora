"""Billing: the service price catalog.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-27

M6-04 (#89 A1-A5; spec §6 pricing, §16; ``_docs/data-model.md`` §3.4,
§3.5, §5.2; ADR-0006 "Price catalog").

- ``service_prices`` (audited, versioned): a catalog item keyed by class
  type, pricing model, subject, level (``NULL`` = any), duration (``NULL`` =
  any) and an optional cohort or group scope. The CHECKs allow these
  combinations (#89 E5): ``individual`` with ``per_session``,
  ``per_period`` or ``package``; ``group`` with ``per_session``,
  ``full_course`` or ``per_period``; ``course`` with ``full_course``. A
  cohort scope only on ``course``, a group scope only on ``group``, never
  both; ``subject_id`` is ``NULL`` only on ``package`` rows; a duration only
  on ``per_session``, 15 to 480 minutes; a scoped item has no level and no
  duration (E4). The key is unique with ``NULLS NOT DISTINCT``, except for
  ``package`` rows (identified by their product, #94).
- ``price_versions`` (audited): the effective-dated prices of an item;
  ``UNIQUE (service_price_id, effective_from)`` (ranges can't overlap; also
  the lookup index); ``amount >= 0``, ``NULL`` = not offered from that date.
  The API rules (#89 E3): an item's first version may start on any date;
  later ones on or after the center's local today and after the latest;
  versions are never edited; only upcoming ones are deleted.
- **Guard (E2, data-model §5.2):** ``price_version_is_referenced(uuid)``
  is ``SELECT false`` here, because ``charges`` (#90) and ``packages`` (#94)
  don't exist yet; each of those migrations replaces it (``CREATE OR
  REPLACE``) and its downgrade restores the previous body. The row trigger
  ``price_versions_no_referenced_change`` (``price_versions_guard()``)
  raises 42501 on an ``UPDATE`` or ``DELETE`` of a referenced version, and
  ``price_versions_no_truncate`` refuses every ``TRUNCATE`` (42501). An
  unreferenced version may still be corrected or removed at the SQL level.

Every foreign key is ``ON DELETE RESTRICT``. No rows are inserted
(``billing.manage`` is seeded by ``make seed``). Downgrading drops the
triggers, both functions, then the tables.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0060"
down_revision: str | Sequence[str] | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICES: Final = "service_prices"
VERSIONS: Final = "price_versions"
CATALOG_KEY_INDEX: Final = "uq_service_prices_catalog_key"

MODEL_ALLOWED: Final = (
    "(class_type = 'individual'"
    " AND pricing_model IN ('per_session', 'per_period', 'package'))"
    " OR (class_type = 'group'"
    " AND pricing_model IN ('per_session', 'full_course', 'per_period'))"
    " OR (class_type = 'course' AND pricing_model IN ('full_course'))"
)

PREDICATE_FUNCTION: Final = """
CREATE FUNCTION price_version_is_referenced(version_id uuid) RETURNS boolean
LANGUAGE sql VOLATILE AS $$
    -- #89: nothing references a price version yet. #90 (charges) and #94
    -- (packages) replace this body with their EXISTS checks.
    SELECT false
$$
"""

GUARD_FUNCTION: Final = """
CREATE FUNCTION price_versions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'price_versions is never truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF price_version_is_referenced(OLD.id) THEN
        RAISE EXCEPTION 'price_versions row is referenced'
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


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def upgrade() -> None:
    op.create_table(
        PRICES,
        sa.Column("class_type", sa.String(length=16), nullable=False),
        sa.Column("pricing_model", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("education_level_id", sa.UUID(), nullable=True),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=True),
        sa.Column("cohort_id", sa.UUID(), nullable=True),
        sa.Column("group_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_standard_columns(),
        _check(
            PRICES,
            "class_type_valid",
            "class_type IN ('individual', 'group', 'course')",
        ),
        _check(
            PRICES,
            "pricing_model_valid",
            "pricing_model IN ('per_session', 'full_course', 'per_period', 'package')",
        ),
        _check(PRICES, "pricing_model_allowed", MODEL_ALLOWED),
        _check(
            PRICES, "one_scope", "NOT (cohort_id IS NOT NULL AND group_id IS NOT NULL)"
        ),
        _check(PRICES, "cohort_scope", "cohort_id IS NULL OR class_type = 'course'"),
        _check(PRICES, "group_scope", "group_id IS NULL OR class_type = 'group'"),
        _check(
            PRICES,
            "package_individual",
            "pricing_model <> 'package' OR class_type = 'individual'",
        ),
        _check(
            PRICES,
            "subject_required",
            "subject_id IS NOT NULL OR pricing_model = 'package'",
        ),
        _check(
            PRICES,
            "duration_valid",
            "duration_minutes IS NULL OR (pricing_model = 'per_session'"
            " AND duration_minutes BETWEEN 15 AND 480)",
        ),
        _check(
            PRICES,
            "scoped_any_level_duration",
            "(cohort_id IS NULL AND group_id IS NULL)"
            " OR (education_level_id IS NULL AND duration_minutes IS NULL)",
        ),
        _fk(PRICES, "subject_id", "subjects"),
        _fk(PRICES, "education_level_id", "education_levels"),
        _fk(PRICES, "cohort_id", "course_cohorts"),
        _fk(PRICES, "group_id", "class_groups"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PRICES}")),
    )
    op.create_index(
        CATALOG_KEY_INDEX,
        PRICES,
        [
            "class_type",
            "pricing_model",
            "subject_id",
            "education_level_id",
            "duration_minutes",
            "cohort_id",
            "group_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("pricing_model <> 'package'"),
    )
    op.create_table(
        VERSIONS,
        sa.Column("service_price_id", sa.UUID(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        *_standard_columns(),
        _check(VERSIONS, "amount_non_negative", "amount >= 0"),
        _fk(VERSIONS, "service_price_id", PRICES),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{VERSIONS}")),
        sa.UniqueConstraint(
            "service_price_id",
            "effective_from",
            name="uq_price_versions_service_price_id_effective_from",
        ),
    )
    op.execute(PREDICATE_FUNCTION)
    op.execute(GUARD_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {VERSIONS}_no_referenced_change"
        f" BEFORE UPDATE OR DELETE ON {VERSIONS}"
        " FOR EACH ROW EXECUTE FUNCTION price_versions_guard()"
    )
    op.execute(
        f"CREATE TRIGGER {VERSIONS}_no_truncate BEFORE TRUNCATE ON {VERSIONS}"
        " FOR EACH STATEMENT EXECUTE FUNCTION price_versions_guard()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {VERSIONS}_no_truncate ON {VERSIONS}")
    op.execute(f"DROP TRIGGER {VERSIONS}_no_referenced_change ON {VERSIONS}")
    op.execute("DROP FUNCTION price_versions_guard()")
    op.execute("DROP FUNCTION price_version_is_referenced(uuid)")
    op.drop_table(VERSIONS)
    op.drop_index(CATALOG_KEY_INDEX, table_name=PRICES)
    op.drop_table(PRICES)
