"""Courses: cohort enrollment periods.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-23

M4-21 (#66 A; spec §1, §3, §6, §12 ``CohortEnrollment``; ADR-0005 D7).

- ``cohort_enrollments``: one row per enrollment period: ``cohort_id``
  (``course_cohorts``) and ``student_id`` (``students``), both indexed;
  ``enrolled_at`` / ``enrolled_by_user_id``; the write-once withdrawal
  ``withdrawn_at`` / ``withdrawn_by_user_id`` / ``withdrawal_reason``
  (CHECKs: after the enrollment, set together, the reason trimmed and only
  with a withdrawal), versioned. ``uq_cohort_enrollments_active`` is a
  partial unique index on ``(cohort_id, student_id) WHERE withdrawn_at IS
  NULL``. The trigger ``cohort_enrollments_write_once`` rejects every
  ``DELETE`` and every ``UPDATE`` that changes the enrollment columns, or
  the withdrawal columns once they are set (SQLSTATE 42501, the 0017 / 0041
  pattern): the periods are the billing record (spec §6, W4).
- ``class_session_overrides.cohort_enrollment_id``: ``UUID NULL`` to
  ``cohort_enrollments`` (``RESTRICT``, indexed; the cross-module key is
  allowed by ADR-0002). ``ck_class_session_overrides_one_subject`` now
  requires exactly one of ``class_session_id``, ``group_enrollment_id``
  and ``cohort_enrollment_id`` (``class_series_id`` stays context, #236).
  The codes CHECK and the append-only trigger are unchanged.

No time-zone dependency: an enrollment stores only the request instant
(#66 A5). Downgrading removes the cohort enrollment overrides (with the
append-only trigger disabled for that statement only), restores the
two-subject CHECK and drops the column, the trigger, the function and the
table.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | Sequence[str] | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENROLLMENTS: Final = "cohort_enrollments"
OVERRIDES: Final = "class_session_overrides"
OVERRIDES_TRIGGER: Final = "class_session_overrides_immutable"
TRIGGER: Final = "cohort_enrollments_write_once"
OVERRIDE_FK: Final = "fk_class_session_overrides_cohort_enrollment_id"
ONE_SUBJECT: Final = (
    "num_nonnulls(class_session_id, group_enrollment_id, cohort_enrollment_id) = 1"
)
"""``ck_class_session_overrides_one_subject`` (the model's CHECK)."""
ONE_SUBJECT_0041: Final = "num_nonnulls(class_session_id, group_enrollment_id) = 1"

WRITE_ONCE_FUNCTION: Final = f"""
CREATE FUNCTION {TRIGGER}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'cohort_enrollments rows are never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
        OR NEW.student_id IS DISTINCT FROM OLD.student_id
        OR NEW.enrolled_at IS DISTINCT FROM OLD.enrolled_at
        OR NEW.enrolled_by_user_id IS DISTINCT FROM OLD.enrolled_by_user_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR (OLD.withdrawn_at IS NOT NULL AND (
            NEW.withdrawn_at IS DISTINCT FROM OLD.withdrawn_at
            OR NEW.withdrawn_by_user_id IS DISTINCT FROM OLD.withdrawn_by_user_id
            OR NEW.withdrawal_reason IS DISTINCT FROM OLD.withdrawal_reason))
    THEN
        RAISE EXCEPTION 'cohort_enrollments rows change only once, when withdrawn'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        ENROLLMENTS,
        sa.Column("cohort_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enrolled_by_user_id", sa.UUID(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by_user_id", sa.UUID(), nullable=True),
        sa.Column("withdrawal_reason", sa.String(length=1000), nullable=True),
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
        sa.CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= enrolled_at",
            name=op.f("ck_cohort_enrollments_withdrawn_after_enrolled"),
        ),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_by_user_id IS NULL)",
            name=op.f("ck_cohort_enrollments_withdrawn_together"),
        ),
        sa.CheckConstraint(
            "withdrawal_reason IS NULL OR (withdrawn_at IS NOT NULL"
            " AND withdrawal_reason = btrim(withdrawal_reason)"
            " AND withdrawal_reason <> '')",
            name=op.f("ck_cohort_enrollments_withdrawal_reason_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["course_cohorts.id"],
            name=op.f("fk_cohort_enrollments_cohort_id_course_cohorts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name=op.f("fk_cohort_enrollments_student_id_students"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enrolled_by_user_id"],
            ["users.id"],
            name=op.f("fk_cohort_enrollments_enrolled_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by_user_id"],
            ["users.id"],
            name=op.f("fk_cohort_enrollments_withdrawn_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cohort_enrollments")),
    )
    op.create_index(
        op.f("ix_cohort_enrollments_cohort_id"),
        ENROLLMENTS,
        ["cohort_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cohort_enrollments_student_id"),
        ENROLLMENTS,
        ["student_id"],
        unique=False,
    )
    op.create_index(
        "uq_cohort_enrollments_active",
        ENROLLMENTS,
        ["cohort_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("withdrawn_at IS NULL"),
    )
    op.execute(WRITE_ONCE_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE OR DELETE ON {ENROLLMENTS}"
        f" FOR EACH ROW EXECUTE FUNCTION {TRIGGER}()"
    )

    op.add_column(
        OVERRIDES, sa.Column("cohort_enrollment_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        OVERRIDE_FK,
        OVERRIDES,
        ENROLLMENTS,
        ["cohort_enrollment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_class_session_overrides_cohort_enrollment_id"),
        OVERRIDES,
        ["cohort_enrollment_id"],
        unique=False,
    )
    op.drop_constraint(
        op.f("ck_class_session_overrides_one_subject"), OVERRIDES, type_="check"
    )
    op.create_check_constraint(
        op.f("ck_class_session_overrides_one_subject"), OVERRIDES, ONE_SUBJECT
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_class_session_overrides_one_subject"), OVERRIDES, type_="check"
    )
    # The cohort enrollment overrides can't outlive their enrollments; the
    # append-only trigger is off for this one statement only.
    op.execute(f"ALTER TABLE {OVERRIDES} DISABLE TRIGGER {OVERRIDES_TRIGGER}")
    op.execute(
        "DELETE FROM class_session_overrides WHERE cohort_enrollment_id IS NOT NULL"
    )
    op.execute(f"ALTER TABLE {OVERRIDES} ENABLE TRIGGER {OVERRIDES_TRIGGER}")
    op.create_check_constraint(
        op.f("ck_class_session_overrides_one_subject"), OVERRIDES, ONE_SUBJECT_0041
    )
    op.drop_index(
        op.f("ix_class_session_overrides_cohort_enrollment_id"), table_name=OVERRIDES
    )
    op.drop_constraint(OVERRIDE_FK, OVERRIDES, type_="foreignkey")
    op.drop_column(OVERRIDES, "cohort_enrollment_id")

    op.execute(f"DROP TRIGGER {TRIGGER} ON {ENROLLMENTS}")
    op.execute(f"DROP FUNCTION {TRIGGER}()")
    op.drop_table(ENROLLMENTS)
