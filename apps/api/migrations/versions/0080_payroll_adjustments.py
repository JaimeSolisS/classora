"""Payroll: adjustments after lock.

Revision ID: 0080
Revises: 0079
Create Date: 2026-11-03

M7-08 (#111 A; ADR-0017 E11 and its #111 notes; ``_docs/data-model.md`` §11.6,
§11.9, §13, §14.1).

- ``payroll_adjustments``: a correction to finalized work, posted into an open
  period. Four kinds, each with its own CHECK: ``late_approval`` and
  ``hours_correction`` (the ``payroll:post_adjustments`` job), ``manual`` and
  ``carry_forward`` (M7-08c's routes, defined here so M7-08c needs no
  migration). ``pay_event_change`` is not a kind (#111 E3). The pricing snapshot
  is what an automatic adjustment was priced on. Append-only, the migration 0004
  pattern: ``payroll_adjustments_append_only()`` refuses ``UPDATE``, ``DELETE``
  and ``TRUNCATE`` with 42501. ``uq_payroll_adjustments_first_payment`` allows
  one first payment per session (#111 E7). Every foreign key is ``ON DELETE
  RESTRICT``; the target and the origin line are composite with the tutor.
- ``payroll_lines`` gains the ``adjustment`` kind and ``payroll_adjustment_id``,
  whose composite foreign key onto ``(id, target_period_id, tutor_user_id)``
  puts the line in its adjustment's target period by construction.
  ``amount_sign`` lets an adjustment line be negative, never ``0``.

No rows are inserted. Downgrading refuses while an adjustment exists (money rows
are never dropped silently); otherwise it restores the 0079 shape.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0080"
down_revision: str | Sequence[str] | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINES: Final = "payroll_lines"
ADJUSTMENTS: Final = "payroll_adjustments"
LINE_FK: Final = "fk_payroll_lines_adjustment"
LINE_UNIQUE: Final = "uq_payroll_lines_adjustment"
ORIGIN_FK: Final = "fk_payroll_adjustments_origin_line"
TARGET_FK: Final = "fk_payroll_adjustments_target_period"
TARGET_KEY: Final = "uq_payroll_adjustments_id_target"
SOURCE_KEY: Final = "uq_payroll_adjustments_source_key"
FIRST_PAYMENT: Final = "uq_payroll_adjustments_first_payment"

SESSION_KINDS_SQL: Final = "'session', 'late_cancellation', 'no_show', 'makeup'"
EVENT_KINDS_SQL: Final = "'late_cancellation', 'no_show', 'makeup'"
SOURCE_CHECK_0078: Final = (
    "(kind IN ('session', 'makeup')"
    " AND class_session_id IS NOT NULL AND approval_id IS NOT NULL)"
    " OR (kind = 'no_show' AND class_session_id IS NOT NULL)"
    " OR (kind = 'late_cancellation'"
    " AND class_session_id IS NOT NULL AND approval_id IS NULL)"
    " OR (kind = 'salary' AND class_session_id IS NULL AND approval_id IS NULL)"
    " OR (kind = 'pay_item' AND class_session_id IS NULL AND approval_id IS NULL)"
)
SOURCE_CHECK: Final = (
    f"{SOURCE_CHECK_0078}"
    " OR (kind = 'adjustment' AND class_session_id IS NULL"
    " AND approval_id IS NULL AND rate_model IS NULL)"
)
AMOUNT_SIGN_0078: Final = (
    "(kind = 'pay_item' AND amount <> 0) OR (kind <> 'pay_item' AND amount >= 0)"
)
AMOUNT_SIGN: Final = (
    "(kind IN ('pay_item', 'adjustment') AND amount <> 0)"
    " OR (kind NOT IN ('pay_item', 'adjustment') AND amount >= 0)"
)
ADJUSTMENT_FIELDS: Final = "(payroll_adjustment_id IS NOT NULL) = (kind = 'adjustment')"

SNAPSHOT: Final = (
    "line_kind, service_date, class_kind, minutes, rate_model, rate,"
    " compensation_rate_id, session_pay_rule_id, cumulative_sessions,"
    " cohort_sessions"
)
SESSION_SNAPSHOT: Final = "line_kind, service_date, class_kind, minutes"
KIND_CHECKS: Final = {
    "late_approval_fields": (
        "kind <> 'late_approval' OR (num_nonnulls(class_session_id,"
        f" origin_period_id, source_key, {SESSION_SNAPSHOT}) = 7"
        " AND num_nonnulls(origin_line_id, reason, created_by_user_id,"
        " paired_adjustment_id) = 0"
        " AND (approval_id IS NOT NULL) = (source_key LIKE 'approval:%'))"
    ),
    "hours_correction_fields": (
        "kind <> 'hours_correction' OR (num_nonnulls(origin_line_id,"
        f" class_session_id, approval_id, source_key, {SESSION_SNAPSHOT}) = 8"
        " AND num_nonnulls(origin_period_id, reason, paired_adjustment_id) = 0)"
    ),
    "manual_fields": (
        "kind <> 'manual' OR (num_nonnulls(reason, created_by_user_id) = 2"
        f" AND num_nonnulls(source_key, {SNAPSHOT}, paired_adjustment_id,"
        " approval_id, origin_period_id) = 0"
        " AND num_nonnulls(origin_line_id, class_session_id) <= 1)"
    ),
    "carry_forward_fields": (
        "kind <> 'carry_forward' OR (num_nonnulls(origin_period_id,"
        " created_by_user_id) = 2"
        f" AND num_nonnulls(source_key, {SNAPSHOT}, origin_line_id,"
        " class_session_id, approval_id) = 0)"
    ),
}
CHECKS: Final = {
    "kind_valid": (
        "kind IN ('late_approval', 'hours_correction', 'manual', 'carry_forward')"
    ),
    "amount_non_zero": "amount <> 0",
    "reason_trimmed": (
        "char_length(reason) BETWEEN 1 AND 1000 AND reason = btrim(reason)"
    ),
    "line_kind_valid": f"line_kind IN ({SESSION_KINDS_SQL})",
    "class_kind_valid": "class_kind IN ('individual', 'group', 'course')",
    "rate_model_valid": "rate_model IN ('per_hour', 'per_session', 'per_course')",
    "minutes_non_negative": "minutes >= 0",
    "rate_snapshot": (
        "(rate_model IS NULL) = (rate IS NULL)"
        " AND (rate IS NULL) = (compensation_rate_id IS NULL)"
    ),
    "pay_rule_with_event": (
        "(session_pay_rule_id IS NOT NULL)"
        f" = COALESCE(line_kind IN ({EVENT_KINDS_SQL}), false)"
    ),
    "course_share": (
        "(cumulative_sessions IS NOT NULL) = (cohort_sessions IS NOT NULL)"
        " AND (cumulative_sessions IS NOT NULL)"
        " = COALESCE(rate_model = 'per_course', false)"
    ),
    **KIND_CHECKS,
}

APPEND_ONLY: Final = f"""
CREATE FUNCTION {ADJUSTMENTS}_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{ADJUSTMENTS} is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

REFUSE_DOWNGRADE: Final = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM payroll_adjustments) THEN
        RAISE EXCEPTION 'payroll_adjustments has rows: money is never dropped'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
END;
$$
"""


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{ADJUSTMENTS}_{column}_{table}"),
        ondelete="RESTRICT",
    )


def _create_adjustments() -> None:
    op.create_table(
        ADJUSTMENTS,
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("target_period_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("origin_line_id", sa.UUID(), nullable=True),
        sa.Column("origin_period_id", sa.UUID(), nullable=True),
        sa.Column("class_session_id", sa.UUID(), nullable=True),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("paired_adjustment_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source_key", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("line_kind", sa.String(length=20), nullable=True),
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
        *(
            sa.CheckConstraint(condition, name=op.f(f"ck_{ADJUSTMENTS}_{name}"))
            for name, condition in CHECKS.items()
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ADJUSTMENTS}")),
        sa.ForeignKeyConstraint(
            ["target_period_id", "tutor_user_id"],
            ["payroll_periods.id", "payroll_periods.tutor_user_id"],
            name=TARGET_FK,
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["origin_line_id", "tutor_user_id"],
            [f"{LINES}.id", f"{LINES}.tutor_user_id"],
            name=ORIGIN_FK,
            ondelete="RESTRICT",
        ),
        _fk("tutor_user_id", "users.id"),
        _fk("origin_period_id", "payroll_periods.id"),
        _fk("class_session_id", "class_sessions.id"),
        _fk("approval_id", "session_closeout_approvals.id"),
        _fk("paired_adjustment_id", f"{ADJUSTMENTS}.id"),
        _fk("created_by_user_id", "users.id"),
        _fk("compensation_rate_id", "compensation_rates.id"),
        _fk("session_pay_rule_id", "session_pay_rules.id"),
        sa.UniqueConstraint("source_key", name=SOURCE_KEY),
        sa.UniqueConstraint("id", "target_period_id", "tutor_user_id", name=TARGET_KEY),
    )
    op.create_index(
        FIRST_PAYMENT,
        ADJUSTMENTS,
        ["class_session_id"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'late_approval'"
            " OR (kind = 'manual' AND class_session_id IS NOT NULL)"
        ),
    )
    op.create_index(
        op.f(f"ix_{ADJUSTMENTS}_target_period_id"), ADJUSTMENTS, ["target_period_id"]
    )
    op.create_index(
        op.f(f"ix_{ADJUSTMENTS}_origin_line_id"), ADJUSTMENTS, ["origin_line_id"]
    )
    op.create_index(
        op.f(f"ix_{ADJUSTMENTS}_class_session_id"),
        ADJUSTMENTS,
        ["class_session_id"],
        postgresql_where=sa.text("class_session_id IS NOT NULL"),
    )
    op.execute(APPEND_ONLY)
    op.execute(
        f"CREATE TRIGGER {ADJUSTMENTS}_no_update_delete BEFORE UPDATE OR DELETE"
        f" ON {ADJUSTMENTS} FOR EACH ROW EXECUTE FUNCTION {ADJUSTMENTS}_append_only()"
    )
    op.execute(
        f"CREATE TRIGGER {ADJUSTMENTS}_no_truncate BEFORE TRUNCATE ON {ADJUSTMENTS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {ADJUSTMENTS}_append_only()"
    )


def _replace_check(old: str, new: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{LINES}_{old}"), LINES, type_="check")
    op.create_check_constraint(op.f(f"ck_{LINES}_{new}"), LINES, condition)


def _change_lines() -> None:
    op.add_column(LINES, sa.Column("payroll_adjustment_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        LINE_FK,
        LINES,
        ADJUSTMENTS,
        ["payroll_adjustment_id", "payroll_period_id", "tutor_user_id"],
        ["id", "target_period_id", "tutor_user_id"],
        ondelete="RESTRICT",
    )
    _replace_check(
        "kind_valid",
        "kind_valid",
        f"kind IN ({SESSION_KINDS_SQL}, 'salary', 'pay_item', 'adjustment')",
    )
    _replace_check("source_per_kind", "source_per_kind", SOURCE_CHECK)
    _replace_check("amount_sign", "amount_sign", AMOUNT_SIGN)
    op.create_check_constraint(
        op.f(f"ck_{LINES}_adjustment_fields"), LINES, ADJUSTMENT_FIELDS
    )
    op.create_index(
        LINE_UNIQUE,
        LINES,
        ["payroll_adjustment_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'adjustment'"),
    )


def upgrade() -> None:
    _create_adjustments()
    _change_lines()


def downgrade() -> None:
    op.execute(REFUSE_DOWNGRADE)
    op.drop_index(LINE_UNIQUE, table_name=LINES)
    op.drop_constraint(op.f(f"ck_{LINES}_adjustment_fields"), LINES, type_="check")
    _replace_check("amount_sign", "amount_sign", AMOUNT_SIGN_0078)
    _replace_check("source_per_kind", "source_per_kind", SOURCE_CHECK_0078)
    _replace_check(
        "kind_valid",
        "kind_valid",
        f"kind IN ({SESSION_KINDS_SQL}, 'salary', 'pay_item')",
    )
    op.drop_constraint(LINE_FK, LINES, type_="foreignkey")
    op.drop_column(LINES, "payroll_adjustment_id")
    op.execute(f"DROP TRIGGER {ADJUSTMENTS}_no_truncate ON {ADJUSTMENTS}")
    op.execute(f"DROP TRIGGER {ADJUSTMENTS}_no_update_delete ON {ADJUSTMENTS}")
    op.execute(f"DROP FUNCTION {ADJUSTMENTS}_append_only()")
    op.drop_table(ADJUSTMENTS)
