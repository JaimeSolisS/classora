"""Billing: schedule reconciliation (cancellation rules, decisions, repricings).

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-30

M6-14 (#99 A1-A4; ``_docs/data-model.md`` §3.10, §3.22, §3.22a, §3.22b,
§5.1, §5.2; ADR-0006 E2, E3).

- ``cancellation_billing_rules`` (billing, audited, **guarded**): one
  version of the outcome (``credit`` | ``charge``) per classification
  (``excused``, ``on_time``, ``late``, ``no_show``), in force from its
  unique ``effective_from``. Version 1, effective 2000-01-01T00:00:00Z, is
  seeded: credit / credit / charge / charge, no author. The guard
  function ``cancellation_billing_rules_guard()`` (trigger
  ``cancellation_billing_rules_no_change``, ``BEFORE UPDATE OR DELETE``)
  refuses every ``UPDATE`` and the ``DELETE`` of a version whose
  ``effective_from <= now()`` (SQLSTATE 42501); the trigger
  ``cancellation_billing_rules_no_truncate`` refuses every ``TRUNCATE``.
- ``session_cancellation_decisions`` (billing, append-only, audited): the
  billing outcome of one cancelled session for one participant, ``UNIQUE
  (class_session_id, student_id)``.
- ``charge_duration_repricings`` (billing, append-only, audited): one
  repricing of a session charge per ``(charge_id, session_version)``, with
  index ``(charge_id, created_at DESC, id DESC)``.

Both append-only tables get the §5.1 trigger pair
(``<table>_append_only()``, SQLSTATE 42501). ``charges`` and
``charge_adjustments`` are unchanged: the kinds ``duration_change``,
``cancellation_credit`` and ``closure_credit`` exist since 0061 (a positive
``closure_credit`` is a restoration). Every foreign key is ``ON DELETE
RESTRICT``. No time-zone dependency: every stored instant is absolute.

Downgrading drops the triggers, their functions and the three tables.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0069"
down_revision: str | Sequence[str] | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULES: Final = "cancellation_billing_rules"
DECISIONS: Final = "session_cancellation_decisions"
REPRICINGS: Final = "charge_duration_repricings"
APPEND_ONLY: Final = (DECISIONS, REPRICINGS)
REPRICING_INDEX: Final = "ix_charge_duration_repricings_charge_latest"
OUTCOMES: Final = "IN ('credit', 'charge')"
RULE_FIELDS: Final = (
    "excused_outcome",
    "on_time_outcome",
    "late_outcome",
    "no_show_outcome",
)
CREDIT_RESULTS: Final = "('credited', 'no_charge', 'zero_charge', 'already_zero')"
CHARGE_RESULTS: Final = "('charge_stands', 'charged', 'package_consumed')"
ALL_RESULTS: Final = (
    "('credited', 'no_charge', 'zero_charge', 'already_zero', 'charge_stands',"
    " 'charged', 'package_consumed')"
)

GUARD_FUNCTION: Final = f"""
CREATE FUNCTION {RULES}_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION '{RULES} can''t be truncated'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'a cancellation billing rule version never changes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.effective_from <= now() THEN
        RAISE EXCEPTION 'only an upcoming cancellation billing rule can be deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN OLD;
END;
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


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _fk(table: str, column: str, target: str, name: str | None = None) -> sa.Column:
    return sa.Column(
        column,
        sa.UUID(),
        sa.ForeignKey(
            f"{target}.id",
            ondelete="RESTRICT",
            name=name or op.f(f"fk_{table}_{column}_{target}"),
        ),
        nullable=False,
    )


def _standard() -> list[sa.Column]:
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


def _nullable(column: sa.Column) -> sa.Column:
    column.nullable = True
    return column


def _create_rules() -> None:
    op.create_table(
        RULES,
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        *(sa.Column(f, sa.String(length=8), nullable=False) for f in RULE_FIELDS),
        _nullable(_fk(RULES, "created_by_user_id", "users")),
        *_standard(),
        *(_check(RULES, f, f"{f} {OUTCOMES}") for f in RULE_FIELDS),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{RULES}")),
        sa.UniqueConstraint("effective_from", name=op.f(f"uq_{RULES}_effective_from")),
    )
    op.execute(
        "INSERT INTO cancellation_billing_rules (effective_from, excused_outcome,"
        " on_time_outcome, late_outcome, no_show_outcome)"
        " VALUES ('2000-01-01T00:00:00Z', 'credit', 'credit', 'charge', 'charge')"
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


def _create_decisions() -> None:
    op.create_table(
        DECISIONS,
        _fk(
            DECISIONS,
            "class_session_id",
            "class_sessions",
            "fk_session_cancellation_decisions_class_session_id",
        ),
        _fk(DECISIONS, "student_id", "students"),
        sa.Column("initiated_by", sa.String(length=8), nullable=False),
        _nullable(
            _fk(
                DECISIONS,
                "change_request_id",
                "session_change_requests",
                "fk_session_cancellation_decisions_change_request_id",
            )
        ),
        sa.Column("classification", sa.String(length=8), nullable=False),
        sa.Column("makeup_allowed", sa.Boolean(), nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
        _fk(
            DECISIONS,
            "rule_id",
            RULES,
            "fk_session_cancellation_decisions_rule_id",
        ),
        sa.Column("outcome", sa.String(length=8), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        _nullable(_fk(DECISIONS, "charge_id", "charges")),
        _nullable(
            _fk(
                DECISIONS,
                "charge_adjustment_id",
                "charge_adjustments",
                "fk_session_cancellation_decisions_charge_adjustment_id",
            )
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *_standard(),
        _check(
            DECISIONS, "initiated_by", "initiated_by IN ('center', 'family', 'tutor')"
        ),
        _check(
            DECISIONS,
            "classification",
            "classification IN ('on_time', 'late', 'no_show', 'excused')",
        ),
        _check(DECISIONS, "outcome", f"outcome {OUTCOMES}"),
        _check(DECISIONS, "result", f"result IN {ALL_RESULTS}"),
        _check(
            DECISIONS,
            "credited_iff_adjustment",
            "(result = 'credited') = (charge_adjustment_id IS NOT NULL)",
        ),
        _check(
            DECISIONS,
            "credit_results",
            f"outcome <> 'credit' OR result IN {CREDIT_RESULTS}",
        ),
        _check(
            DECISIONS,
            "charge_results",
            f"outcome <> 'charge' OR result IN {CHARGE_RESULTS}",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{DECISIONS}")),
        sa.UniqueConstraint(
            "class_session_id",
            "student_id",
            name="uq_session_cancellation_decisions_key",
        ),
    )


def _create_repricings() -> None:
    op.create_table(
        REPRICINGS,
        _fk(REPRICINGS, "charge_id", "charges"),
        _fk(REPRICINGS, "class_session_id", "class_sessions"),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("previous_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("scheduled_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("target_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("delta_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("clamped", sa.Boolean(), nullable=False),
        _nullable(
            _fk(
                REPRICINGS,
                "charge_adjustment_id",
                "charge_adjustments",
                "fk_charge_duration_repricings_charge_adjustment_id",
            )
        ),
        *_standard(),
        _check(REPRICINGS, "method", "method IN ('original', 'catalog', 'pro_rata')"),
        _check(
            REPRICINGS,
            "minutes_range",
            "scheduled_minutes BETWEEN 15 AND 480"
            " AND previous_minutes BETWEEN 15 AND 480",
        ),
        _check(REPRICINGS, "minutes_changed", "scheduled_minutes <> previous_minutes"),
        _check(REPRICINGS, "target_non_negative", "target_amount >= 0"),
        _check(
            REPRICINGS,
            "delta_iff_adjustment",
            "(delta_amount <> 0) = (charge_adjustment_id IS NOT NULL)",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{REPRICINGS}")),
        sa.UniqueConstraint(
            "charge_id",
            "session_version",
            name="uq_charge_duration_repricings_charge_version",
        ),
    )
    op.create_index(
        REPRICING_INDEX,
        REPRICINGS,
        ["charge_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def upgrade() -> None:
    _create_rules()
    _create_decisions()
    _create_repricings()
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
    op.drop_index(REPRICING_INDEX, table_name=REPRICINGS)
    op.drop_table(REPRICINGS)
    op.drop_table(DECISIONS)
    op.execute(f"DROP TRIGGER {RULES}_no_truncate ON {RULES}")
    op.execute(f"DROP TRIGGER {RULES}_no_change ON {RULES}")
    op.execute(f"DROP FUNCTION {RULES}_guard()")
    op.drop_table(RULES)
