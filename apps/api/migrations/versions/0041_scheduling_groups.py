"""Scheduling: ongoing tutoring groups and their enrollment periods.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-22

M4-11 (#56 A; spec §1, §3, §6; ADR-0005 D1, D7).

- ``class_groups``: ``name`` ``VARCHAR(120)`` (trimmed, 1-120,
  ``ck_class_groups_name_trimmed``; unique case-insensitively through the
  expression index ``uq_class_groups_name`` on ``lower(name)``),
  ``subject_id`` (``subjects``, ``RESTRICT``, indexed) and ``max_students``
  ``SMALLINT`` 1-500 (``ck_class_groups_max_students_range``), versioned.
- ``group_enrollments``: one row per enrollment period: ``group_id``
  (``class_groups``) and ``student_id`` (``students``), both indexed;
  ``enrolled_at`` / ``enrolled_by_user_id``; the write-once withdrawal
  ``withdrawn_at`` / ``withdrawn_by_user_id`` / ``withdrawal_reason``
  (CHECKs: after the enrollment, set together, the reason trimmed and only
  with a withdrawal), versioned. ``uq_group_enrollments_active`` is a
  partial unique index on ``(group_id, student_id) WHERE withdrawn_at IS
  NULL``. The trigger ``group_enrollments_write_once`` rejects every
  ``DELETE`` and every ``UPDATE`` that changes the enrollment columns, or
  the withdrawal columns once they are set (SQLSTATE 42501, the 0017
  pattern): the periods are the billing record (spec §6, W4).
- ``class_series.group_id`` and ``class_sessions.group_id``: ``UUID NULL``
  to ``class_groups`` (``RESTRICT``), with ``(kind = 'group') = (group_id IS
  NOT NULL)`` (``ck_<table>_group_iff_kind``; existing rows are
  ``individual``, so the CHECK holds); ``ix_class_series_group_id`` and
  ``ix_class_sessions_group_id_starts_at``.
- ``class_session_overrides``: ``group_enrollment_id`` ``UUID NULL`` (to
  ``group_enrollments``, ``RESTRICT``, indexed); ``class_session_id``
  becomes nullable; ``ck_class_session_overrides_one_subject`` requires
  exactly one of the two (``class_series_id`` is context, #236). The codes
  CHECK and the append-only trigger are unchanged (DDL doesn't fire it).

No time-zone dependency: nothing here stores an instant computed from the
center's local time (#56 A8). Downgrading removes the enrollment overrides
(with the append-only trigger disabled for that statement only), makes
``class_session_id`` ``NOT NULL`` again and drops the columns, the trigger,
the function and both tables. Sessions and series of ``kind = 'group'``
keep their kind without a group; upgrading again needs them removed first
(the CHECK would refuse them).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GROUPS: Final = "class_groups"
ENROLLMENTS: Final = "group_enrollments"
OVERRIDES: Final = "class_session_overrides"
OVERRIDES_TRIGGER: Final = "class_session_overrides_immutable"
TRIGGER: Final = "group_enrollments_write_once"
ONE_SUBJECT: Final = "num_nonnulls(class_session_id, group_enrollment_id) = 1"
"""``ck_class_session_overrides_one_subject`` (the model's CHECK)."""

WRITE_ONCE_FUNCTION: Final = f"""
CREATE FUNCTION {TRIGGER}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'group_enrollments rows are never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.group_id IS DISTINCT FROM OLD.group_id
        OR NEW.student_id IS DISTINCT FROM OLD.student_id
        OR NEW.enrolled_at IS DISTINCT FROM OLD.enrolled_at
        OR NEW.enrolled_by_user_id IS DISTINCT FROM OLD.enrolled_by_user_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR (OLD.withdrawn_at IS NOT NULL AND (
            NEW.withdrawn_at IS DISTINCT FROM OLD.withdrawn_at
            OR NEW.withdrawn_by_user_id IS DISTINCT FROM OLD.withdrawn_by_user_id
            OR NEW.withdrawal_reason IS DISTINCT FROM OLD.withdrawal_reason))
    THEN
        RAISE EXCEPTION 'group_enrollments rows change only once, when withdrawn'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""


def _standard_columns() -> list[sa.Column[object]]:
    return [
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
    ]


def _group_column(table: str) -> None:
    op.add_column(table, sa.Column("group_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f(f"fk_{table}_group_id_class_groups"),
        table,
        GROUPS,
        ["group_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f(f"ck_{table}_group_iff_kind"),
        table,
        "(kind = 'group') = (group_id IS NOT NULL)",
    )


def upgrade() -> None:
    op.create_table(
        GROUPS,
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("max_students", sa.SmallInteger(), nullable=False),
        *_standard_columns(),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 120",
            name=op.f("ck_class_groups_name_trimmed"),
        ),
        sa.CheckConstraint(
            "max_students BETWEEN 1 AND 500",
            name=op.f("ck_class_groups_max_students_range"),
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_class_groups_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_class_groups")),
    )
    op.create_index(
        op.f("ix_class_groups_subject_id"), GROUPS, ["subject_id"], unique=False
    )
    op.create_index(
        "uq_class_groups_name", GROUPS, [sa.text("lower(name)")], unique=True
    )

    op.create_table(
        ENROLLMENTS,
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enrolled_by_user_id", sa.UUID(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by_user_id", sa.UUID(), nullable=True),
        sa.Column("withdrawal_reason", sa.String(length=1000), nullable=True),
        *_standard_columns(),
        sa.CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= enrolled_at",
            name=op.f("ck_group_enrollments_withdrawn_after_enrolled"),
        ),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_by_user_id IS NULL)",
            name=op.f("ck_group_enrollments_withdrawn_together"),
        ),
        sa.CheckConstraint(
            "withdrawal_reason IS NULL OR (withdrawn_at IS NOT NULL"
            " AND withdrawal_reason = btrim(withdrawal_reason)"
            " AND withdrawal_reason <> '')",
            name=op.f("ck_group_enrollments_withdrawal_reason_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            [f"{GROUPS}.id"],
            name=op.f("fk_group_enrollments_group_id_class_groups"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name=op.f("fk_group_enrollments_student_id_students"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enrolled_by_user_id"],
            ["users.id"],
            name=op.f("fk_group_enrollments_enrolled_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by_user_id"],
            ["users.id"],
            name=op.f("fk_group_enrollments_withdrawn_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_enrollments")),
    )
    op.create_index(
        op.f("ix_group_enrollments_group_id"), ENROLLMENTS, ["group_id"], unique=False
    )
    op.create_index(
        op.f("ix_group_enrollments_student_id"),
        ENROLLMENTS,
        ["student_id"],
        unique=False,
    )
    op.create_index(
        "uq_group_enrollments_active",
        ENROLLMENTS,
        ["group_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("withdrawn_at IS NULL"),
    )
    op.execute(WRITE_ONCE_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE OR DELETE ON {ENROLLMENTS}"
        f" FOR EACH ROW EXECUTE FUNCTION {TRIGGER}()"
    )

    _group_column("class_series")
    op.create_index(
        op.f("ix_class_series_group_id"), "class_series", ["group_id"], unique=False
    )
    _group_column("class_sessions")
    op.create_index(
        op.f("ix_class_sessions_group_id_starts_at"),
        "class_sessions",
        ["group_id", "starts_at"],
        unique=False,
    )

    op.add_column(OVERRIDES, sa.Column("group_enrollment_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_class_session_overrides_group_enrollment_id_group_enrollments"),
        OVERRIDES,
        ENROLLMENTS,
        ["group_enrollment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_class_session_overrides_group_enrollment_id"),
        OVERRIDES,
        ["group_enrollment_id"],
        unique=False,
    )
    op.alter_column(
        OVERRIDES, "class_session_id", existing_type=sa.UUID(), nullable=True
    )
    op.create_check_constraint(
        op.f("ck_class_session_overrides_one_subject"),
        OVERRIDES,
        ONE_SUBJECT,
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_class_session_overrides_one_subject"), OVERRIDES, type_="check"
    )
    # The enrollment overrides can't outlive their enrollments; the
    # append-only trigger is off for this one statement only.
    op.execute(f"ALTER TABLE {OVERRIDES} DISABLE TRIGGER {OVERRIDES_TRIGGER}")
    op.execute(
        "DELETE FROM class_session_overrides WHERE group_enrollment_id IS NOT NULL"
    )
    op.execute(f"ALTER TABLE {OVERRIDES} ENABLE TRIGGER {OVERRIDES_TRIGGER}")
    op.alter_column(
        OVERRIDES, "class_session_id", existing_type=sa.UUID(), nullable=False
    )
    op.drop_index(
        op.f("ix_class_session_overrides_group_enrollment_id"), table_name=OVERRIDES
    )
    op.drop_constraint(
        op.f("fk_class_session_overrides_group_enrollment_id_group_enrollments"),
        OVERRIDES,
        type_="foreignkey",
    )
    op.drop_column(OVERRIDES, "group_enrollment_id")

    op.drop_index(
        op.f("ix_class_sessions_group_id_starts_at"), table_name="class_sessions"
    )
    op.drop_index(op.f("ix_class_series_group_id"), table_name="class_series")
    for table in ("class_sessions", "class_series"):
        op.drop_constraint(op.f(f"ck_{table}_group_iff_kind"), table, type_="check")
        op.drop_constraint(
            op.f(f"fk_{table}_group_id_class_groups"), table, type_="foreignkey"
        )
        op.drop_column(table, "group_id")

    op.execute(f"DROP TRIGGER {TRIGGER} ON {ENROLLMENTS}")
    op.execute(f"DROP FUNCTION {TRIGGER}()")
    op.drop_table(ENROLLMENTS)
    op.drop_table(GROUPS)
