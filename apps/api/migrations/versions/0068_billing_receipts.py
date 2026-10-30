"""Billing: receipts and the gap-free number series.

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-30

M6-13 (#98 A1-A3; ``_docs/data-model.md`` §3.21, §3.25, §5.1, §6;
ADR-0006 E13).

- ``number_series`` (core, **not** audited, no ``id``): one gap-free
  counter per ``(code, year)`` (``PRIMARY KEY``); ``code`` is ``R``
  (receipts) or ``P`` (payslips, reserved for M7); ``year`` is the
  center-local year; ``last_value >= 0``. No seed rows:
  ``app.core.numbering.next_number`` creates a year's row on first use
  (``ON CONFLICT DO NOTHING``) and increments it under its row lock.
- ``receipts`` (billing, append-only, audited): one per payment
  (``payment_id`` UNIQUE), numbered ``R-YYYY-NNNNNN`` (``number`` UNIQUE,
  CHECKed), with the composite foreign key ``(payment_id,
  billing_account_id)`` → ``payments (id, billing_account_id)`` (I4),
  ``issued_at``, ``language`` (``es``/``en``), ``template_version > 0`` and
  the frozen ``snapshot`` (a JSON object). Index ``(billing_account_id,
  issued_at)``. The §5.1 trigger pair (``receipts_append_only()``, SQLSTATE
  42501) refuses ``UPDATE``, ``DELETE`` and ``TRUNCATE``.

Payments recorded before this revision get no receipt (no backfill, E13).
No time-zone dependency is registered: ``issued_at`` is an instant, and
the number's year was fixed when it was issued.

Downgrading drops the triggers, their function and both tables.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0068"
down_revision: str | Sequence[str] | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SERIES: Final = "number_series"
RECEIPTS: Final = "receipts"
INDEX: Final = "ix_receipts_billing_account_id_issued_at"

APPEND_ONLY_FUNCTION: Final = f"""
CREATE FUNCTION {RECEIPTS}_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{RECEIPTS} is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _create_series() -> None:
    op.create_table(
        SERIES,
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column(
            "last_value", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        _check(SERIES, "code", "code IN ('R', 'P')"),
        _check(SERIES, "year_range", "year BETWEEN 2000 AND 9999"),
        _check(SERIES, "last_value_non_negative", "last_value >= 0"),
        sa.PrimaryKeyConstraint("code", "year", name=op.f(f"pk_{SERIES}")),
    )


def _create_receipts() -> None:
    op.create_table(
        RECEIPTS,
        sa.Column("number", sa.String(length=16), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("billing_account_id", sa.UUID(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("template_version", sa.SmallInteger(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
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
        _check(RECEIPTS, "number_format", "number ~ '^R-[0-9]{4}-[0-9]{6}$'"),
        _check(RECEIPTS, "language", "language IN ('es', 'en')"),
        _check(RECEIPTS, "template_version_positive", "template_version > 0"),
        _check(RECEIPTS, "snapshot_object", "jsonb_typeof(snapshot) = 'object'"),
        sa.ForeignKeyConstraint(
            ["payment_id", "billing_account_id"],
            ["payments.id", "payments.billing_account_id"],
            name="fk_receipts_payment_id_payments",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{RECEIPTS}")),
        sa.UniqueConstraint("number", name="uq_receipts_number"),
        sa.UniqueConstraint("payment_id", name="uq_receipts_payment_id"),
    )
    op.create_index(INDEX, RECEIPTS, ["billing_account_id", "issued_at"])


def upgrade() -> None:
    _create_series()
    _create_receipts()
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {RECEIPTS}_no_update_delete BEFORE UPDATE OR DELETE"
        f" ON {RECEIPTS} FOR EACH ROW EXECUTE FUNCTION {RECEIPTS}_append_only()"
    )
    op.execute(
        f"CREATE TRIGGER {RECEIPTS}_no_truncate BEFORE TRUNCATE ON {RECEIPTS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {RECEIPTS}_append_only()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {RECEIPTS}_no_truncate ON {RECEIPTS}")
    op.execute(f"DROP TRIGGER {RECEIPTS}_no_update_delete ON {RECEIPTS}")
    op.execute(f"DROP FUNCTION {RECEIPTS}_append_only()")
    op.drop_index(INDEX, table_name=RECEIPTS)
    op.drop_table(RECEIPTS)
    op.drop_table(SERIES)
