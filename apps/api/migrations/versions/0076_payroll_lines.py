"""Payroll: session-sourced payroll lines.

Revision ID: 0076
Revises: 0075
Create Date: 2026-11-01

M7-04 (#107 A; ADR-0017 E2, E3, E4, E9; ``_docs/data-model.md`` §11.6, §14.3,
§16).

- ``payroll_lines`` (not audited: working data until finalized): the four
  session kinds. ``kind`` and the per-kind source CHECK name them only, and
  ``tutor_pay_terms_id``, the proration columns, ``pay_item_id`` and
  ``payroll_adjustment_id`` are left out: #108, #109 and #111 add them and
  widen the CHECKs. The partial unique index allows one session-sourced line
  per session.
- **Lock (§14.3):** ``payroll_lines_guard()`` (trigger
  ``payroll_lines_no_finalized_change``) refuses ``INSERT``, ``UPDATE`` and
  ``DELETE`` with SQLSTATE 42501 when the period is ``finalized``, reading the
  period ``FOR SHARE``, both periods on an ``UPDATE``. The trigger
  ``payroll_lines_no_truncate`` refuses every ``TRUNCATE``.
- ``compensation_rate_is_referenced(uuid)`` gets its real body (#105's staged
  guard). ``CREATE OR REPLACE`` can't rename the parameter ``id``, and inside
  a SQL function a column named ``id`` wins over a parameter, so the body
  qualifies it with the function's name. ``tutor_pay_terms_is_referenced``
  stays ``SELECT false`` (#108).
- ``ix_class_sessions_cancelled_tutor_user_id_starts_at``: the calculation's
  read of a tutor's cancelled sessions.

Every foreign key is ``ON DELETE RESTRICT``. No rows are inserted
(``payroll.prepare`` is seeded by ``make seed``). Downgrading restores the
``SELECT false`` body, drops the index, the triggers, the function, then the
table with its rows.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: str | Sequence[str] | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINES: Final = "payroll_lines"
SESSION_KINDS_SQL: Final = "'session', 'late_cancellation', 'no_show', 'makeup'"
EVENT_KINDS_SQL: Final = "'late_cancellation', 'no_show', 'makeup'"

SOURCE_CHECK: Final = (
    "(kind IN ('session', 'makeup')"
    " AND class_session_id IS NOT NULL AND approval_id IS NOT NULL)"
    " OR (kind = 'no_show' AND class_session_id IS NOT NULL)"
    " OR (kind = 'late_cancellation'"
    " AND class_session_id IS NOT NULL AND approval_id IS NULL)"
)
SESSION_FIELDS_CHECK: Final = (
    "(class_session_id IS NULL AND service_date IS NULL"
    " AND class_kind IS NULL AND minutes IS NULL)"
    " OR (class_session_id IS NOT NULL AND service_date IS NOT NULL"
    " AND class_kind IS NOT NULL AND minutes IS NOT NULL)"
)
RATE_SNAPSHOT_CHECK: Final = (
    "(rate_model IS NULL) = (rate IS NULL)"
    " AND (rate IS NULL) = (compensation_rate_id IS NULL)"
)
RULE_CHECK: Final = f"(session_pay_rule_id IS NOT NULL) = (kind IN ({EVENT_KINDS_SQL}))"
COURSE_SHARE_CHECK: Final = (
    "(cumulative_sessions IS NOT NULL) = (cohort_sessions IS NOT NULL)"
    " AND (cumulative_sessions IS NOT NULL)"
    " = COALESCE(rate_model = 'per_course', false)"
)

GUARD_FUNCTION: Final = """
CREATE FUNCTION payroll_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    period_ids uuid[];
    period_status text;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'payroll_lines is never truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'INSERT' THEN
        period_ids := ARRAY[NEW.payroll_period_id];
    ELSIF TG_OP = 'DELETE' THEN
        period_ids := ARRAY[OLD.payroll_period_id];
    ELSE
        period_ids := ARRAY[OLD.payroll_period_id, NEW.payroll_period_id];
    END IF;
    FOR period_status IN
        SELECT status FROM payroll_periods
        WHERE id = ANY (period_ids) ORDER BY id FOR SHARE
    LOOP
        IF period_status = 'finalized' THEN
            RAISE EXCEPTION 'a line of a finalized payroll period never changes'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END LOOP;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$
"""

RATE_PREDICATE: Final = """
CREATE OR REPLACE FUNCTION compensation_rate_is_referenced(id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- #107: qualified, because the columns named id would win over the
    -- parameter. Only a finalized line freezes a version (#107 E8).
    SELECT EXISTS (
        SELECT 1
        FROM payroll_lines AS l
        JOIN payroll_periods AS p ON p.id = l.payroll_period_id
        WHERE l.compensation_rate_id = compensation_rate_is_referenced.id
            AND p.status = 'finalized'
    )
$$
"""

STAGED_RATE_PREDICATE: Final = """
CREATE OR REPLACE FUNCTION compensation_rate_is_referenced(id uuid)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    -- #105: payroll_lines doesn't exist yet. #107 replaces this body with
    -- its EXISTS over the lines of a finalized period.
    SELECT false
$$
"""


def _check(name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{LINES}_{name}"))


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{LINES}_{column}_{table}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        LINES,
        sa.Column("payroll_period_id", sa.UUID(), nullable=False),
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("class_session_id", sa.UUID(), nullable=True),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=True),
        sa.Column("class_kind", sa.String(length=20), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=True),
        sa.Column("rate_model", sa.String(length=20), nullable=True),
        sa.Column("rate", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("compensation_rate_id", sa.UUID(), nullable=True),
        sa.Column("session_pay_rule_id", sa.UUID(), nullable=True),
        sa.Column("cumulative_sessions", sa.Integer(), nullable=True),
        sa.Column("cohort_sessions", sa.Integer(), nullable=True),
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
        _check("kind_valid", f"kind IN ({SESSION_KINDS_SQL})"),
        _check("source_per_kind", SOURCE_CHECK),
        _check("session_fields", SESSION_FIELDS_CHECK),
        _check("class_kind_valid", "class_kind IN ('individual', 'group', 'course')"),
        _check(
            "rate_model_valid",
            "rate_model IN ('per_hour', 'per_session', 'per_course')",
        ),
        _check("amount_non_negative", "amount >= 0"),
        _check("minutes_non_negative", "minutes >= 0"),
        _check("rate_snapshot", RATE_SNAPSHOT_CHECK),
        _check("pay_rule_with_event", RULE_CHECK),
        _check("course_share", COURSE_SHARE_CHECK),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{LINES}")),
        sa.ForeignKeyConstraint(
            ["payroll_period_id", "tutor_user_id"],
            ["payroll_periods.id", "payroll_periods.tutor_user_id"],
            name="fk_payroll_lines_period_tutor",
            ondelete="RESTRICT",
        ),
        _fk("tutor_user_id", "users.id"),
        _fk("class_session_id", "class_sessions.id"),
        _fk("approval_id", "session_closeout_approvals.id"),
        _fk("compensation_rate_id", "compensation_rates.id"),
        _fk("session_pay_rule_id", "session_pay_rules.id"),
        sa.UniqueConstraint("id", "tutor_user_id", name=op.f(f"uq_{LINES}_id_tutor")),
    )
    op.create_index(
        "uq_payroll_lines_session_sourced",
        LINES,
        ["class_session_id"],
        unique=True,
        postgresql_where=sa.text(
            "kind IN ('session', 'late_cancellation', 'no_show', 'makeup')"
        ),
    )
    op.create_index(op.f(f"ix_{LINES}_payroll_period_id"), LINES, ["payroll_period_id"])
    op.create_index(
        op.f(f"ix_{LINES}_class_session_id"),
        LINES,
        ["class_session_id"],
        postgresql_where=sa.text("class_session_id IS NOT NULL"),
    )
    op.create_index(
        op.f(f"ix_{LINES}_compensation_rate_id"),
        LINES,
        ["compensation_rate_id"],
        postgresql_where=sa.text("compensation_rate_id IS NOT NULL"),
    )
    op.create_index(
        op.f(f"ix_{LINES}_session_pay_rule_id"),
        LINES,
        ["session_pay_rule_id"],
        postgresql_where=sa.text("session_pay_rule_id IS NOT NULL"),
    )
    op.create_index(
        "ix_class_sessions_cancelled_tutor_user_id_starts_at",
        "class_sessions",
        ["tutor_user_id", "starts_at"],
        postgresql_where=sa.text("state = 'cancelled'"),
    )
    op.execute(GUARD_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {LINES}_no_finalized_change"
        f" BEFORE INSERT OR UPDATE OR DELETE ON {LINES}"
        f" FOR EACH ROW EXECUTE FUNCTION {LINES}_guard()"
    )
    op.execute(
        f"CREATE TRIGGER {LINES}_no_truncate BEFORE TRUNCATE ON {LINES}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {LINES}_guard()"
    )
    op.execute(RATE_PREDICATE)


def downgrade() -> None:
    op.execute(STAGED_RATE_PREDICATE)
    op.execute(f"DROP TRIGGER {LINES}_no_truncate ON {LINES}")
    op.execute(f"DROP TRIGGER {LINES}_no_finalized_change ON {LINES}")
    op.execute(f"DROP FUNCTION {LINES}_guard()")
    op.drop_index(
        "ix_class_sessions_cancelled_tutor_user_id_starts_at",
        table_name="class_sessions",
    )
    op.drop_table(LINES)
