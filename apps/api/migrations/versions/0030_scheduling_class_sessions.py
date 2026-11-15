"""Scheduling: ``class_sessions`` and ``class_session_participants``.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-20

M4-02 (#47; spec §3, §16, W1; ADR-0005 D1, D2, D6; ADR-0004: instants as
``TIMESTAMPTZ``). Both tables are audited by the ORM. It does **not** create
``btree_gist``: revision 0021 (#38) enabled it.

- ``class_sessions`` (versioned): ``kind`` ``individual`` | ``group`` |
  ``course``; ``state`` one of the ADR-0005 D2 states (no ``draft``);
  ``subject_id`` (``subjects``, indexed), nullable ``tutor_user_id``
  (``users``, indexed) and ``classroom_id`` (``classrooms``, indexed), all
  ``ON DELETE RESTRICT``; ``starts_at < ends_at`` (``TIMESTAMPTZ``, indexed
  start), whole minutes (epoch-based), 15–480 minutes apart;
  ``scheduled_minutes`` equal to that duration; ``actual_minutes`` 0–1440 or
  ``NULL``; no tutor when ``unassigned``, a tutor when ``offered`` /
  ``confirmed`` / ``completed``; ``cancelled_at`` and ``cancellation_reason``
  (trimmed, not blank) exactly when ``cancelled``.
  ``ex_class_sessions_tutor_overlap`` and ``ex_class_sessions_room_overlap``:
  partial GiST exclusions over ``tstzrange(starts_at, ends_at, '[)')`` for
  one tutor (``offered``, ``confirmed``, ``completed``) and one room (also
  ``unassigned``). The state lists match
  ``scheduling.states.TUTOR_OCCUPYING_STATES`` / ``ROOM_OCCUPYING_STATES``.
- ``class_session_participants``: ``class_session_id`` (``class_sessions``)
  and ``student_id`` (``students``, indexed), ``ON DELETE RESTRICT``; one row
  per session and student (``uq_class_session_participants_session_student``).

No rows are inserted. Downgrading drops both tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SESSIONS = "class_sessions"
PARTICIPANTS = "class_session_participants"
SESSION_RANGE = "tstzrange(starts_at, ends_at, '[)')"
TUTOR_STATES = "'completed', 'confirmed', 'offered'"

CHECKS = {
    "kind_valid": "kind IN ('course', 'group', 'individual')",
    "state_valid": (
        "state IN ('cancelled', 'completed', 'confirmed', 'conflict', 'offered',"
        " 'skipped_closure', 'unassigned')"
    ),
    "starts_before_ends": "starts_at < ends_at",
    "whole_minutes": (
        "mod(EXTRACT(EPOCH FROM starts_at), 60) = 0"
        " AND mod(EXTRACT(EPOCH FROM ends_at), 60) = 0"
    ),
    "duration_range": (
        "ends_at - starts_at BETWEEN interval '15 minutes' AND interval '480 minutes'"
    ),
    "scheduled_minutes_match": (
        "scheduled_minutes * interval '1 minute' = ends_at - starts_at"
    ),
    "actual_minutes_range": "actual_minutes BETWEEN 0 AND 1440",
    "unassigned_has_no_tutor": "state <> 'unassigned' OR tutor_user_id IS NULL",
    "assigned_has_tutor": (
        f"state NOT IN ({TUTOR_STATES}) OR tutor_user_id IS NOT NULL"
    ),
    "cancellation_iff_cancelled": (
        "(cancelled_at IS NULL) = (cancellation_reason IS NULL)"
        " AND (state = 'cancelled') = (cancelled_at IS NOT NULL)"
    ),
    "cancellation_reason_trimmed": (
        "cancellation_reason = btrim(cancellation_reason) AND cancellation_reason <> ''"
    ),
}


def _standard_columns(*, versioned: bool) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [
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
    if versioned:
        columns.append(
            sa.Column(
                "version", sa.Integer(), server_default=sa.text("1"), nullable=False
            )
        )
    return columns


def _fk(table: str, column: str, referred: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{referred}.id"],
        name=op.f(f"fk_{table}_{column}_{referred}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        SESSIONS,
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("tutor_user_id", sa.UUID(), nullable=True),
        sa.Column("classroom_id", sa.UUID(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("actual_minutes", sa.SmallInteger(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=1000), nullable=True),
        *_standard_columns(versioned=True),
        *(
            sa.CheckConstraint(condition, name=op.f(f"ck_{SESSIONS}_{name}"))
            for name, condition in CHECKS.items()
        ),
        _fk(SESSIONS, "subject_id", "subjects"),
        _fk(SESSIONS, "tutor_user_id", "users"),
        _fk(SESSIONS, "classroom_id", "classrooms"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SESSIONS}")),
        postgresql.ExcludeConstraint(
            (sa.text("tutor_user_id"), "="),
            (sa.text(SESSION_RANGE), "&&"),
            name="ex_class_sessions_tutor_overlap",
            using="gist",
            where=sa.text(
                "tutor_user_id IS NOT NULL"
                " AND state IN ('completed', 'confirmed', 'offered')"
            ),
        ),
        postgresql.ExcludeConstraint(
            (sa.text("classroom_id"), "="),
            (sa.text(SESSION_RANGE), "&&"),
            name="ex_class_sessions_room_overlap",
            using="gist",
            where=sa.text(
                "classroom_id IS NOT NULL"
                " AND state IN ('completed', 'confirmed', 'offered', 'unassigned')"
            ),
        ),
    )
    for column in ("subject_id", "tutor_user_id", "classroom_id", "starts_at"):
        op.create_index(
            op.f(f"ix_{SESSIONS}_{column}"), SESSIONS, [column], unique=False
        )

    op.create_table(
        PARTICIPANTS,
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        *_standard_columns(versioned=False),
        _fk(PARTICIPANTS, "class_session_id", "class_sessions"),
        _fk(PARTICIPANTS, "student_id", "students"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PARTICIPANTS}")),
        sa.UniqueConstraint(
            "class_session_id",
            "student_id",
            name="uq_class_session_participants_session_student",
        ),
    )
    op.create_index(
        op.f(f"ix_{PARTICIPANTS}_student_id"),
        PARTICIPANTS,
        ["student_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table(PARTICIPANTS)
    op.drop_table(SESSIONS)
