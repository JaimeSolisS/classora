"""Billing: due-date changes and charge reminders.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-29

M6-12 (#97 A1-A3; ``_docs/data-model.md`` §3.11, §3.27, §5.1; ADR-0006
E11).

- ``charge_due_date_changes`` (append-only, audited): a staff change of a
  posted charge's due date with a reason. ``previous_due_date`` is the
  effective due date before the change (``CHECK (due_date <>
  previous_due_date)``); the latest row by ``(created_at, id)`` is the
  charge's effective due date. The composite foreign key ``(charge_id,
  billing_account_id)`` → ``charges (id, billing_account_id)`` keeps a row
  on its charge's account (I4 style); ``changed_by_user_id`` references
  ``users`` ``RESTRICT``.
- ``charge_reminders`` (append-only, **not** audited): one row per
  ``(charge_id, stage)`` the reminder job sent
  (``uq_charge_reminders_charge_stage``, the natural key it inserts on with
  ``ON CONFLICT DO NOTHING``); ``stage`` is one of
  ``app.modules.billing.reminders.REMINDER_STAGES``; ``due_date`` is the
  effective due date the stage was computed from.

Both tables get the §5.1 trigger pair (``<table>_append_only()``, SQLSTATE
42501). No time-zone dependency is registered: every date is a
center-local ``DATE`` (#37 E1).

Downgrading drops the triggers, their functions and both tables.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: str | Sequence[str] | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANGES: Final = "charge_due_date_changes"
REMINDERS: Final = "charge_reminders"
APPEND_ONLY: Final = (CHANGES, REMINDERS)
STAGES: Final = "('due_soon', 'overdue', 'overdue_followup')"
"""``app.modules.billing.reminders.REMINDER_STAGES`` (a unit test pins
it)."""
REASON_VALID: Final = (
    "reason IS NULL OR (length(btrim(reason)) BETWEEN 1 AND 500"
    " AND reason = btrim(reason))"
)
"""``staff_text_check("reason", 500)`` (#90 A2)."""


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


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _create_changes() -> None:
    op.create_table(
        CHANGES,
        sa.Column("charge_id", sa.UUID(), nullable=False),
        sa.Column("billing_account_id", sa.UUID(), nullable=False),
        sa.Column("previous_due_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("changed_by_user_id", sa.UUID(), nullable=False),
        *_standard_columns(),
        _check(
            CHANGES,
            "due_date_range",
            "due_date BETWEEN '2000-01-01' AND '2099-12-31'",
        ),
        _check(CHANGES, "due_date_changed", "due_date <> previous_due_date"),
        _check(CHANGES, "reason_valid", REASON_VALID),
        sa.ForeignKeyConstraint(
            ["charge_id", "billing_account_id"],
            ["charges.id", "charges.billing_account_id"],
            name="fk_charge_due_date_changes_charge_id_charges",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{CHANGES}_changed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CHANGES}")),
    )
    op.create_index(
        "ix_charge_due_date_changes_charge_created",
        CHANGES,
        ["charge_id", "created_at", "id"],
    )


def _create_reminders() -> None:
    op.create_table(
        REMINDERS,
        sa.Column("charge_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        *_standard_columns(),
        _check(REMINDERS, "stage", f"stage IN {STAGES}"),
        sa.ForeignKeyConstraint(
            ["charge_id"],
            ["charges.id"],
            name=op.f(f"fk_{REMINDERS}_charge_id_charges"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{REMINDERS}")),
        sa.UniqueConstraint(
            "charge_id", "stage", name="uq_charge_reminders_charge_stage"
        ),
    )


def upgrade() -> None:
    _create_changes()
    _create_reminders()
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


def downgrade() -> None:
    for table in reversed(APPEND_ONLY):
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER {table}_no_update_delete ON {table}")
        op.execute(f"DROP FUNCTION {table}_append_only()")
    op.drop_table(REMINDERS)
    op.drop_index("ix_charge_due_date_changes_charge_created", table_name=CHANGES)
    op.drop_table(CHANGES)
