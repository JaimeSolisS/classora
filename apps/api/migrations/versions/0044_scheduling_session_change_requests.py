"""Scheduling: cancellation and reschedule requests by families and tutors.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-23

M4-17 (#62 A; spec §2, §3 "user requests and admin approvals, exception
handling, and audit trails"; ADR-0005 D2, D6, D9).

- ``session_change_requests``: ``class_session_id`` (``class_sessions``),
  ``requested_by_user_id`` and ``decided_by_user_id`` (``users``),
  ``student_id`` (``students``, the participant a family requester acts
  for; ``NULL`` for a tutor) and ``policy_id`` (``scheduling_policies``),
  all ``RESTRICT``; ``kind`` ``cancellation`` | ``reschedule``;
  ``initiated_by`` ``family`` | ``tutor``; ``note`` ``VARCHAR(500)``
  (trimmed, non-empty when set); the session's times at request time
  (``session_starts_at`` / ``session_ends_at``); a reschedule's
  ``proposed_starts_at`` / ``proposed_ends_at`` (whole minutes); the
  request-time outcome (``classification``, ``makeup_allowed``,
  ``makeup_until``: never recomputed); ``status`` (default ``pending``);
  the decision (``exception`` default false, ``decision_reason``
  ``VARCHAR(1000)``, ``decided_by_user_id``, ``resolved_at``) and the
  effective outcome (``final_classification``, ``final_makeup_allowed``,
  ``final_makeup_until``, set exactly when ``approved``); versioned. The
  CHECKs are ``scheduling.models.CHANGE_REQUEST_CHECKS`` (A1, A2), copied
  here so that later edits of the model never change this revision.
- ``uq_session_change_requests_pending_session``: unique on
  ``class_session_id`` ``WHERE status = 'pending'`` (A3: one pending
  request per session; several decided ones are allowed).
- ``ix_session_change_requests_class_session_id`` and the two
  ``created_at DESC`` list indexes (by student, by requester).

Pending reschedule rows are a time-zone dependency
(``scheduling.change_requests``, registered in ``app/wiring.py``).
Downgrading drops the table.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "session_change_requests"
_CLASSIFICATIONS: Final = "'excused', 'late', 'no_show', 'on_time'"

CHECKS: Final = {
    "kind_valid": "kind IN ('cancellation', 'reschedule')",
    "initiated_by_valid": "initiated_by IN ('family', 'tutor')",
    "student_iff_family": "(initiated_by = 'family') = (student_id IS NOT NULL)",
    "note_trimmed": "note IS NULL OR (note = btrim(note) AND note <> '')",
    "session_times_order": "session_ends_at > session_starts_at",
    "proposal_both_or_none": (
        "(proposed_starts_at IS NULL) = (proposed_ends_at IS NULL)"
    ),
    "proposal_iff_reschedule": (
        "(kind = 'reschedule') = (proposed_starts_at IS NOT NULL)"
    ),
    "proposal_order": (
        "proposed_starts_at IS NULL OR proposed_ends_at > proposed_starts_at"
    ),
    "proposal_whole_minutes": (
        "proposed_starts_at IS NULL"
        " OR (mod(EXTRACT(EPOCH FROM proposed_starts_at), 60) = 0"
        " AND mod(EXTRACT(EPOCH FROM proposed_ends_at), 60) = 0)"
    ),
    "classification_valid": f"classification IN ({_CLASSIFICATIONS})",
    "makeup_iff_until": "makeup_allowed = (makeup_until IS NOT NULL)",
    "no_makeup_on_reschedule": "kind = 'cancellation' OR NOT makeup_allowed",
    "status_valid": ("status IN ('approved', 'pending', 'rejected', 'withdrawn')"),
    "resolved_iff_not_pending": "(status = 'pending') = (resolved_at IS NULL)",
    "final_iff_approved": "(status = 'approved') = (final_classification IS NOT NULL)",
    "final_classification_valid": (
        f"final_classification IS NULL OR final_classification IN ({_CLASSIFICATIONS})"
    ),
    "final_makeup_with_final": (
        "(final_classification IS NULL) = (final_makeup_allowed IS NULL)"
    ),
    "final_makeup_iff_until": (
        "(final_makeup_until IS NOT NULL) = (final_makeup_allowed IS TRUE)"
    ),
    "no_final_makeup_on_reschedule": (
        "kind = 'cancellation' OR final_makeup_allowed IS NOT TRUE"
    ),
    "rejected_needs_reason": "status <> 'rejected' OR decision_reason IS NOT NULL",
    "reason_only_when_decided": (
        "decision_reason IS NULL OR status IN ('approved', 'rejected')"
    ),
    "reason_trimmed": (
        "decision_reason IS NULL"
        " OR (decision_reason = btrim(decision_reason) AND decision_reason <> '')"
    ),
    "exception_rule": (
        "NOT exception OR (status = 'approved' AND decision_reason IS NOT NULL"
        " AND final_classification = 'excused')"
    ),
    "decided_by_iff_decided": (
        "(decided_by_user_id IS NOT NULL) = (status IN ('approved', 'rejected'))"
    ),
}


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{TABLE}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def _instant(name: str, *, nullable: bool) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("initiated_by", sa.String(length=10), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        _instant("session_starts_at", nullable=False),
        _instant("session_ends_at", nullable=False),
        _instant("proposed_starts_at", nullable=True),
        _instant("proposed_ends_at", nullable=True),
        sa.Column("policy_id", sa.UUID(), nullable=False),
        sa.Column("classification", sa.String(length=10), nullable=False),
        sa.Column("makeup_allowed", sa.Boolean(), nullable=False),
        _instant("makeup_until", nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "exception",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("decision_reason", sa.String(length=1000), nullable=True),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
        _instant("resolved_at", nullable=True),
        sa.Column("final_classification", sa.String(length=10), nullable=True),
        sa.Column("final_makeup_allowed", sa.Boolean(), nullable=True),
        _instant("final_makeup_until", nullable=True),
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
        *(
            sa.CheckConstraint(condition, name=op.f(f"ck_{TABLE}_{name}"))
            for name, condition in CHECKS.items()
        ),
        _fk("class_session_id", "class_sessions"),
        _fk("requested_by_user_id", "users"),
        _fk("student_id", "students"),
        _fk("policy_id", "scheduling_policies"),
        _fk("decided_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    op.create_index(op.f(f"ix_{TABLE}_class_session_id"), TABLE, ["class_session_id"])
    op.create_index(
        "uq_session_change_requests_pending_session",
        TABLE,
        ["class_session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_session_change_requests_student_id_created_at",
        TABLE,
        ["student_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_session_change_requests_requested_by_user_id_created_at",
        TABLE,
        ["requested_by_user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table(TABLE)
