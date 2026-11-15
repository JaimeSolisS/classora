"""Payroll: session pay rules.

Revision ID: 0075
Revises: 0074
Create Date: 2026-11-01

M7-04c (#441 A; ADR-0017 E4, #107 E9; ``_docs/data-model.md`` §11.5, §14.2).

- ``session_pay_rules`` (audited, **guarded by time**): the center-wide
  late-cancellation, no-show and makeup pay rules, effective-dated by the
  service date, ``UNIQUE (event, effective_from)``. ``percent`` is set exactly
  when ``mode = 'percent'`` (0 to 1000, so ``Rate.to_money`` stays in range)
  and ``amount`` exactly when ``mode = 'fixed'``.
- **Seed, effective 2000-01-01, no author:** ``late_cancellation`` ``none``,
  ``no_show`` ``percent`` 100, ``makeup`` ``percent`` 100, so a version is in
  force on every date. The owner should review these defaults (ADR-0017).
- **Guard:** ``session_pay_rules_guard()`` (trigger
  ``session_pay_rules_no_change``, ``BEFORE UPDATE OR DELETE``) refuses every
  ``UPDATE`` and the ``DELETE`` of a version with ``effective_from`` on or
  before the center's today, read from ``business_settings.time_zone``
  (SQLSTATE 42501). It refuses too when it can't tell the date. The trigger
  ``session_pay_rules_no_truncate`` refuses every ``TRUNCATE``.

Every foreign key is ``ON DELETE RESTRICT``. No permission is added.
Downgrading drops the triggers, the function, then the table with its rows.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0075"
down_revision: str | Sequence[str] | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULES: Final = "session_pay_rules"

GUARD_FUNCTION: Final = """
CREATE FUNCTION session_pay_rules_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    center_today date;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'session_pay_rules can''t be truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'a session pay rule version never changes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT (now() AT TIME ZONE time_zone)::date INTO center_today
        FROM business_settings;
    IF (OLD.effective_from > center_today) IS NOT TRUE THEN
        RAISE EXCEPTION 'only an upcoming session pay rule can be deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN OLD;
END;
$$
"""


def _check(name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{RULES}_{name}"))


def upgrade() -> None:
    op.create_table(
        RULES,
        sa.Column("event", sa.String(length=20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("percent", sa.Numeric(14, 4), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey(
                "users.id",
                ondelete="RESTRICT",
                name=op.f(f"fk_{RULES}_created_by_user_id_users"),
            ),
            nullable=True,
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
        _check("event_valid", "event IN ('late_cancellation', 'no_show', 'makeup')"),
        _check("mode_valid", "mode IN ('none', 'percent', 'fixed')"),
        _check("percent_with_mode", "(mode = 'percent') = (percent IS NOT NULL)"),
        _check("amount_with_mode", "(mode = 'fixed') = (amount IS NOT NULL)"),
        _check("percent_range", "percent BETWEEN 0 AND 1000"),
        _check("amount_non_negative", "amount >= 0"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{RULES}")),
        sa.UniqueConstraint(
            "event", "effective_from", name=op.f(f"uq_{RULES}_event_effective_from")
        ),
    )
    op.execute(
        "INSERT INTO session_pay_rules (event, effective_from, mode, percent) VALUES"
        " ('late_cancellation', '2000-01-01', 'none', NULL),"
        " ('no_show', '2000-01-01', 'percent', 100),"
        " ('makeup', '2000-01-01', 'percent', 100)"
    )
    op.execute(GUARD_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {RULES}_no_change BEFORE UPDATE OR DELETE ON {RULES}"
        f" FOR EACH ROW EXECUTE FUNCTION {RULES}_guard()"
    )
    op.execute(
        f"CREATE TRIGGER {RULES}_no_truncate BEFORE TRUNCATE ON {RULES}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {RULES}_guard()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {RULES}_no_truncate ON {RULES}")
    op.execute(f"DROP TRIGGER {RULES}_no_change ON {RULES}")
    op.execute(f"DROP FUNCTION {RULES}_guard()")
    op.drop_table(RULES)
