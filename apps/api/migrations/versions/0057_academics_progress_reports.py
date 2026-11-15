"""Academics: progress reports with reviewer approval.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-25

M5-16 (#83 A1; spec §2, §4 "progress reports … require admin approval
before family visibility", §9, §16, W5.2, W5.3; architecture §4.1;
ADR-0004).

- ``progress_reports``: the student, the author (a tutor), an optional
  subject, ``kind`` (``weekly`` / ``monthly`` / ``on_demand``) with its
  period (``period_start`` ≤ ``period_end``, at most 366 days; a weekly
  period is a Monday to the next Sunday, a monthly one a calendar month),
  ``status`` (``draft`` / ``submitted`` / ``changes_requested`` /
  ``approved_for_family`` / ``retracted``), the narrative (``summary``
  1–4000, ``strengths``, ``areas_for_improvement``, ``next_steps`` 1–2000,
  stored trimmed), the ``facts`` snapshot (JSONB) and
  ``facts_computed_at`` (together; required once submitted),
  ``submitted_at``, the latest request for changes (``changes_requested_at``,
  ``_by_user_id``, ``_reason``: all or none), the approval (``approved_at``,
  ``approved_by_user_id``: together; only while approved or retracted), the
  retraction (``retracted_at``, ``retracted_by_user_id``, ``retract_reason``:
  all or none, exactly when retracted), the soft delete (``deleted_at``,
  ``deleted_by_user_id``: together, only in ``draft`` or
  ``changes_requested``) and a ``version``.
- Indexes: a student's approved reports, an author's live reports, the
  reviewer queue (``submitted``), and a partial unique index: one live,
  non-retracted weekly or monthly report per student, author, period start
  and subject (``COALESCE`` of the subject with the nil UUID).
- ``progress_reports_immutable()`` (BEFORE UPDATE OR DELETE, the 0017/0051
  pattern, SQLSTATE 42501): once ``approved_at`` is set no change to the
  student, author, subject, kind, period, narrative, facts or approval;
  ``DELETE`` is always refused (reports are soft-deleted). A retraction
  (status, ``retracted_*``, ``version``, ``updated_at``) is still allowed.

Every foreign key is ``ON DELETE RESTRICT``. No time-zone dependency (local
dates and event instants). No rows are inserted. Downgrading drops the
trigger, the table and the function.
"""

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057"
down_revision: str | Sequence[str] | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPORTS: Final = "progress_reports"
APPROVED_INDEX: Final = "ix_progress_reports_student_id_approved"
AUTHOR_INDEX: Final = "ix_progress_reports_author_user_id_created_at"
QUEUE_INDEX: Final = "ix_progress_reports_submitted_at"
PERIOD_UNIQUE: Final = "uq_progress_reports_period"
PROTECTED: Final = (
    "student_id",
    "author_user_id",
    "subject_id",
    "kind",
    "period_start",
    "period_end",
    "summary",
    "strengths",
    "areas_for_improvement",
    "next_steps",
    "facts",
    "facts_computed_at",
    "approved_at",
    "approved_by_user_id",
)

_CHANGED = " OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in PROTECTED)

IMMUTABLE_FUNCTION: Final = f"""
CREATE FUNCTION {REPORTS}_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{REPORTS} rows are never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.approved_at IS NOT NULL AND ({_CHANGED}) THEN
        RAISE EXCEPTION 'an approved progress report never changes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""


def _standard() -> list[sa.Column[Any]]:
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


def _timestamp(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _check(name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{REPORTS}_{name}"))


def _trimmed(column: str, maximum: int) -> sa.CheckConstraint:
    return _check(
        f"{column}_valid",
        f"{column} IS NULL OR (char_length({column}) BETWEEN 1 AND {maximum}"
        f" AND {column} = btrim({column}))",
    )


def _reason(column: str) -> sa.CheckConstraint:
    return _check(
        f"{column}_valid",
        f"{column} IS NULL OR (char_length(btrim({column})) BETWEEN 1 AND 1000"
        f" AND {column} = btrim({column}))",
    )


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{REPORTS}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        REPORTS,
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("strengths", sa.Text(), nullable=True),
        sa.Column("areas_for_improvement", sa.Text(), nullable=True),
        sa.Column("next_steps", sa.Text(), nullable=True),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _timestamp("facts_computed_at"),
        _timestamp("submitted_at"),
        _timestamp("changes_requested_at"),
        sa.Column("changes_requested_by_user_id", sa.UUID(), nullable=True),
        sa.Column("changes_requested_reason", sa.Text(), nullable=True),
        _timestamp("approved_at"),
        sa.Column("approved_by_user_id", sa.UUID(), nullable=True),
        _timestamp("retracted_at"),
        sa.Column("retracted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("retract_reason", sa.Text(), nullable=True),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
        _timestamp("deleted_at"),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_standard(),
        _check("kind_valid", "kind IN ('weekly', 'monthly', 'on_demand')"),
        _check(
            "period_valid",
            "period_start <= period_end AND period_end - period_start <= 365",
        ),
        _check(
            "weekly_period",
            "kind <> 'weekly' OR (extract(isodow from period_start) = 1"
            " AND period_end = period_start + 6)",
        ),
        _check(
            "monthly_period",
            "kind <> 'monthly' OR (extract(day from period_start) = 1"
            " AND period_end = (period_start + interval '1 month'"
            " - interval '1 day')::date)",
        ),
        _check(
            "status_valid",
            "status IN ('draft', 'submitted', 'changes_requested',"
            " 'approved_for_family', 'retracted')",
        ),
        _trimmed("summary", 4000),
        _trimmed("strengths", 2000),
        _trimmed("areas_for_improvement", 2000),
        _trimmed("next_steps", 2000),
        _check("facts_together", "(facts IS NULL) = (facts_computed_at IS NULL)"),
        _check("facts_frozen", "status = 'draft' OR facts IS NOT NULL"),
        _check("submitted_set", "status = 'draft' OR submitted_at IS NOT NULL"),
        _check(
            "changes_requested_together",
            "(changes_requested_at IS NULL) = (changes_requested_by_user_id IS NULL)"
            " AND (changes_requested_at IS NULL) = (changes_requested_reason IS NULL)",
        ),
        _reason("changes_requested_reason"),
        _check(
            "changes_requested_set",
            "status <> 'changes_requested' OR changes_requested_at IS NOT NULL",
        ),
        _check(
            "approved_together",
            "(approved_at IS NULL) = (approved_by_user_id IS NULL)",
        ),
        _check(
            "approved_set",
            "(status = 'approved_for_family') <= (approved_at IS NOT NULL)",
        ),
        _check(
            "approved_status",
            "approved_at IS NULL OR status IN ('approved_for_family', 'retracted')",
        ),
        _check(
            "retracted_together",
            "(retracted_at IS NULL) = (retracted_by_user_id IS NULL)"
            " AND (retracted_at IS NULL) = (retract_reason IS NULL)",
        ),
        _reason("retract_reason"),
        _check("retracted_set", "(status = 'retracted') = (retracted_at IS NOT NULL)"),
        _check(
            "deleted_together",
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
        ),
        _check(
            "deleted_editable",
            "deleted_at IS NULL OR status IN ('draft', 'changes_requested')",
        ),
        _fk("student_id", "students"),
        _fk("author_user_id", "users"),
        _fk("subject_id", "subjects"),
        _fk("changes_requested_by_user_id", "users"),
        _fk("approved_by_user_id", "users"),
        _fk("retracted_by_user_id", "users"),
        _fk("deleted_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{REPORTS}")),
    )
    op.create_index(
        APPROVED_INDEX,
        REPORTS,
        ["student_id", "period_end", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'approved_for_family'"),
    )
    op.create_index(
        AUTHOR_INDEX,
        REPORTS,
        ["author_user_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        QUEUE_INDEX,
        REPORTS,
        ["submitted_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'submitted'"),
    )
    op.create_index(
        PERIOD_UNIQUE,
        REPORTS,
        [
            sa.text("student_id"),
            sa.text("author_user_id"),
            sa.text("kind"),
            sa.text("period_start"),
            sa.text(
                "COALESCE(subject_id, '00000000-0000-0000-0000-000000000000'::uuid)"
            ),
        ],
        unique=True,
        postgresql_where=sa.text(
            "kind <> 'on_demand' AND deleted_at IS NULL AND status <> 'retracted'"
        ),
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {REPORTS}_immutable BEFORE UPDATE OR DELETE ON {REPORTS}"
        f" FOR EACH ROW EXECUTE FUNCTION {REPORTS}_immutable()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {REPORTS}_immutable ON {REPORTS}")
    op.drop_index(PERIOD_UNIQUE, table_name=REPORTS)
    op.drop_index(QUEUE_INDEX, table_name=REPORTS)
    op.drop_index(AUTHOR_INDEX, table_name=REPORTS)
    op.drop_index(APPROVED_INDEX, table_name=REPORTS)
    op.drop_table(REPORTS)
    op.execute(f"DROP FUNCTION {REPORTS}_immutable()")
