"""Payroll: settings and periods.

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-31

M7-03 (#106 A; ADR-0017 E2, E6; ``_docs/data-model.md`` §11.1, §11.4, §14.3).

- ``payroll_settings`` (audited, versioned): the one settings row (the
  ``billing_settings`` pattern). The upgrade inserts it with
  ``payroll_starts_on = NULL``, so payroll stays off until staff set the date.
  A raw insert writes no audit event (the 0020 precedent).
- ``payroll_periods`` (not audited, status-guarded): a tutor's chain of
  periods, as local dates.

  - **No overlaps (P2):** ``EXCLUDE USING gist``.
  - **No gaps (P3):** the stored generated ``previous_ends_on`` and the
    composite foreign key ``(tutor_user_id, previous_ends_on)`` onto ``UNIQUE
    (tutor_user_id, ends_on)``. Only a row with ``is_first_period`` has no
    previous period, and a partial unique index allows one per tutor.
  - **Guard (§14.3):** a ``finalized`` row can't change or go away (42501),
    and the table is never truncated. The move into ``finalized`` passes,
    because ``OLD.status`` is still ``prepared``.

Every foreign key is ``ON DELETE RESTRICT``. Downgrading drops the triggers,
the function, then the tables.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: str | Sequence[str] | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS: Final = "payroll_settings"
PERIODS: Final = "payroll_periods"

GUARD: Final = """
CREATE FUNCTION payroll_periods_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'payroll_periods is never truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'finalized' THEN
        RAISE EXCEPTION 'a finalized payroll period never changes'
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


def _user_fk(column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        ["users.id"],
        name=op.f(f"fk_{PERIODS}_{column}_users"),
        ondelete="RESTRICT",
    )


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def upgrade() -> None:
    settings = op.create_table(
        SETTINGS,
        sa.Column(
            "singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("payroll_starts_on", sa.Date(), nullable=True),
        sa.Column(
            "biweekly_anchor_on",
            sa.Date(),
            server_default=sa.text("'2026-01-05'"),
            nullable=False,
        ),
        sa.Column(
            "salary_proration_basis",
            sa.String(length=20),
            server_default=sa.text("'calendar_days'"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_standard_columns(),
        _check(SETTINGS, "singleton_true", "singleton"),
        _check(
            SETTINGS,
            "anchor_monday",
            "EXTRACT(ISODOW FROM biweekly_anchor_on) = 1",
        ),
        _check(
            SETTINGS,
            "proration_basis_valid",
            "salary_proration_basis IN ('calendar_days', 'weekdays')",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SETTINGS}")),
        sa.UniqueConstraint("singleton", name=op.f(f"uq_{SETTINGS}_singleton")),
    )
    op.execute(
        sa.insert(settings).values(singleton=True, payroll_starts_on=None, version=1)
    )
    op.create_table(
        PERIODS,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("pay_frequency", sa.String(length=10), nullable=False),
        sa.Column("is_first_period", sa.Boolean(), nullable=False),
        sa.Column(
            "previous_ends_on",
            sa.Date(),
            sa.Computed(
                "CASE WHEN is_first_period THEN NULL ELSE starts_on - 1 END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=10),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prepared_by_user_id", sa.UUID(), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by_user_id", sa.UUID(), nullable=True),
        *_standard_columns(),
        _check(PERIODS, "dates_ordered", "ends_on >= starts_on"),
        _check(
            PERIODS,
            "pay_frequency_valid",
            "pay_frequency IN ('weekly', 'biweekly', 'monthly')",
        ),
        _check(PERIODS, "status_valid", "status IN ('open', 'prepared', 'finalized')"),
        _check(
            PERIODS,
            "prepared_fields",
            "(status IN ('prepared', 'finalized')"
            " AND prepared_at IS NOT NULL AND prepared_by_user_id IS NOT NULL)"
            " OR (status NOT IN ('prepared', 'finalized')"
            " AND prepared_at IS NULL AND prepared_by_user_id IS NULL)",
        ),
        _check(
            PERIODS,
            "finalized_fields",
            "(status = 'finalized' AND total_amount IS NOT NULL"
            " AND finalized_at IS NOT NULL AND finalized_by_user_id IS NOT NULL)"
            " OR (status <> 'finalized' AND total_amount IS NULL"
            " AND finalized_at IS NULL AND finalized_by_user_id IS NULL)",
        ),
        _check(PERIODS, "total_not_negative", "total_amount >= 0"),
        _user_fk("tutor_user_id"),
        _user_fk("prepared_by_user_id"),
        _user_fk("finalized_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PERIODS}")),
        sa.UniqueConstraint(
            "tutor_user_id",
            "starts_on",
            name="uq_payroll_periods_tutor_user_id_starts_on",
        ),
        sa.UniqueConstraint(
            "tutor_user_id",
            "ends_on",
            name="uq_payroll_periods_tutor_user_id_ends_on",
        ),
        sa.UniqueConstraint(
            "id", "tutor_user_id", name="uq_payroll_periods_id_tutor_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["tutor_user_id", "previous_ends_on"],
            [f"{PERIODS}.tutor_user_id", f"{PERIODS}.ends_on"],
            name="fk_payroll_periods_previous_period",
            ondelete="RESTRICT",
        ),
    )
    op.execute(
        f"ALTER TABLE {PERIODS} ADD CONSTRAINT ex_payroll_periods_tutor_overlap"
        " EXCLUDE USING gist"
        " (tutor_user_id WITH =, daterange(starts_on, ends_on, '[]') WITH &&)"
    )
    op.create_index(
        "uq_payroll_periods_first_period",
        PERIODS,
        ["tutor_user_id"],
        unique=True,
        postgresql_where=sa.text("is_first_period"),
    )
    op.create_index("ix_payroll_periods_status_ends_on", PERIODS, ["status", "ends_on"])
    op.execute(GUARD)
    op.execute(
        f"CREATE TRIGGER {PERIODS}_no_finalized_change"
        f" BEFORE UPDATE OR DELETE ON {PERIODS}"
        f" FOR EACH ROW EXECUTE FUNCTION {PERIODS}_guard()"
    )
    op.execute(
        f"CREATE TRIGGER {PERIODS}_no_truncate BEFORE TRUNCATE ON {PERIODS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {PERIODS}_guard()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {PERIODS}_no_truncate ON {PERIODS}")
    op.execute(f"DROP TRIGGER {PERIODS}_no_finalized_change ON {PERIODS}")
    op.execute(f"DROP FUNCTION {PERIODS}_guard()")
    op.drop_table(PERIODS)
    op.drop_table(SETTINGS)
