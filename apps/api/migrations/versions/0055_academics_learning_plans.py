"""Academics: learning plans, their objectives and milestones.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-25

M5-14 (#81 A1; spec §4 "structured learning plans with objectives,
milestones, due dates, activities and progress visible to
students/guardians", §9, §16; architecture §4.1; ADR-0004).

- ``learning_plans``: the student, the subject and the creator, ``title``
  (1–120, trimmed), an optional goal ``description`` (1–2000),
  ``starts_on`` / ``target_on`` local dates (``target_on`` not before
  ``starts_on``), ``status`` (``draft`` / ``active`` / ``completed`` /
  ``discontinued``), ``activated_at`` (never on a draft; always on an
  active or completed plan; a discontinued plan may have it), ``closed_at``
  (exactly when completed or discontinued), the soft delete
  (``deleted_at``, ``deleted_by_user_id``: together, drafts only) and a
  ``version``. A partial unique index keeps at most one live ``active``
  plan per student and subject; a partial index serves the student's
  list.
- ``learning_plan_objectives``: ``plan_id``, ``position`` (1–10, unique per
  plan, deferrable: the #64 pattern), ``title`` (1–200, trimmed), an
  optional ``description`` (1–1000).
- ``learning_plan_milestones``: ``plan_id``, ``objective_id``, ``position``
  (1–10, unique per objective, deferrable), ``title`` (1–200, trimmed),
  optional ``activities`` (1–2000) and ``due_on``, the progress ``status``
  (``pending`` / ``in_progress`` / ``done``) with ``completed_at`` and
  ``completed_by_user_id`` exactly when ``done``, and a ``version``.

Every foreign key is ``ON DELETE RESTRICT``. No time-zone dependency (the
dates are calendar dates; ``completed_at`` is an event instant). No rows
are inserted. Downgrading drops the three tables.
"""

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: str | Sequence[str] | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLANS: Final = "learning_plans"
OBJECTIVES: Final = "learning_plan_objectives"
MILESTONES: Final = "learning_plan_milestones"
ACTIVE_INDEX: Final = "uq_learning_plans_student_id_subject_id_active"
LIST_INDEX: Final = "ix_learning_plans_student_id_created_at"
OBJECTIVES_PLAN_INDEX: Final = "ix_learning_plan_objectives_plan_id"
MILESTONES_PLAN_INDEX: Final = "ix_learning_plan_milestones_plan_id"
MILESTONES_OBJECTIVE_INDEX: Final = "ix_learning_plan_milestones_objective_id"


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


def _version() -> sa.Column[Any]:
    return sa.Column(
        "version", sa.Integer(), server_default=sa.text("1"), nullable=False
    )


def _timestamp(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _check(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _trimmed(table: str, column: str, maximum: int) -> sa.CheckConstraint:
    return _check(
        table,
        f"{column}_valid",
        f"char_length({column}) BETWEEN 1 AND {maximum} AND {column} = btrim({column})",
    )


def _length(table: str, column: str, maximum: int) -> sa.CheckConstraint:
    return _check(
        table,
        f"{column}_valid",
        f"{column} IS NULL OR char_length({column}) BETWEEN 1 AND {maximum}",
    )


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        PLANS,
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("target_on", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        _timestamp("activated_at"),
        _timestamp("closed_at"),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
        _timestamp("deleted_at"),
        _version(),
        *_standard(),
        _trimmed(PLANS, "title", 120),
        _length(PLANS, "description", 2000),
        _check(
            PLANS, "target_after_start", "target_on IS NULL OR target_on >= starts_on"
        ),
        _check(
            PLANS,
            "status_valid",
            "status IN ('draft', 'active', 'completed', 'discontinued')",
        ),
        _check(
            PLANS, "draft_not_activated", "status <> 'draft' OR activated_at IS NULL"
        ),
        _check(
            PLANS,
            "active_activated",
            "status NOT IN ('active', 'completed') OR activated_at IS NOT NULL",
        ),
        _check(
            PLANS,
            "closed_together",
            "(status IN ('completed', 'discontinued')) = (closed_at IS NOT NULL)",
        ),
        _check(
            PLANS,
            "deleted_together",
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
        ),
        _check(PLANS, "deleted_draft", "deleted_at IS NULL OR status = 'draft'"),
        _fk(PLANS, "student_id", "students"),
        _fk(PLANS, "subject_id", "subjects"),
        _fk(PLANS, "created_by_user_id", "users"),
        _fk(PLANS, "deleted_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PLANS}")),
    )
    op.create_index(
        ACTIVE_INDEX,
        PLANS,
        ["student_id", "subject_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND deleted_at IS NULL"),
    )
    op.create_index(
        LIST_INDEX,
        PLANS,
        ["student_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        OBJECTIVES,
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_standard(),
        _check(OBJECTIVES, "position_range", "position BETWEEN 1 AND 10"),
        _trimmed(OBJECTIVES, "title", 200),
        _length(OBJECTIVES, "description", 1000),
        _fk(OBJECTIVES, "plan_id", PLANS),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{OBJECTIVES}")),
        sa.UniqueConstraint(
            "plan_id",
            "position",
            name="uq_learning_plan_objectives_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(OBJECTIVES_PLAN_INDEX, OBJECTIVES, ["plan_id"], unique=False)

    op.create_table(
        MILESTONES,
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("objective_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("activities", sa.Text(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        _timestamp("completed_at"),
        sa.Column("completed_by_user_id", sa.UUID(), nullable=True),
        _version(),
        *_standard(),
        _check(MILESTONES, "position_range", "position BETWEEN 1 AND 10"),
        _trimmed(MILESTONES, "title", 200),
        _length(MILESTONES, "activities", 2000),
        _check(
            MILESTONES,
            "status_valid",
            "status IN ('pending', 'in_progress', 'done')",
        ),
        _check(
            MILESTONES,
            "done_together",
            "(status = 'done') = (completed_at IS NOT NULL"
            " AND completed_by_user_id IS NOT NULL)",
        ),
        _fk(MILESTONES, "plan_id", PLANS),
        sa.ForeignKeyConstraint(
            ["objective_id"],
            [f"{OBJECTIVES}.id"],
            name=op.f("fk_learning_plan_milestones_objective_id"),
            ondelete="RESTRICT",
        ),
        _fk(MILESTONES, "completed_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{MILESTONES}")),
        sa.UniqueConstraint(
            "objective_id",
            "position",
            name="uq_learning_plan_milestones_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(MILESTONES_PLAN_INDEX, MILESTONES, ["plan_id"], unique=False)
    op.create_index(
        MILESTONES_OBJECTIVE_INDEX, MILESTONES, ["objective_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(MILESTONES_OBJECTIVE_INDEX, table_name=MILESTONES)
    op.drop_index(MILESTONES_PLAN_INDEX, table_name=MILESTONES)
    op.drop_table(MILESTONES)
    op.drop_index(OBJECTIVES_PLAN_INDEX, table_name=OBJECTIVES)
    op.drop_table(OBJECTIVES)
    op.drop_index(LIST_INDEX, table_name=PLANS)
    op.drop_index(ACTIVE_INDEX, table_name=PLANS)
    op.drop_table(PLANS)
