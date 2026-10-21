"""Scheduling: ``class_session_overrides``.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-21

M4-04 (#49 D1; spec §3, W1.6; ADR-0005 "Conflict wire format and
overrides"). Not audited by the ORM: the named audit event
``scheduling.session_override`` is the record of each override.

- ``class_session_overrides``: ``class_session_id`` (``class_sessions``,
  indexed) and ``actor_user_id`` (``users``), both ``ON DELETE RESTRICT``;
  ``codes`` ``TEXT[]``, non-empty and every element one of the seven soft
  codes (``ck_class_session_overrides_codes_soft``; the list equals
  ``scheduling.conflicts.SOFT``, a unit test guards the drift); ``conflicts``
  ``JSONB`` (the overridden conflicts as computed); ``reason`` ``TEXT``,
  1-1000 characters once trimmed (``ck_class_session_overrides_reason_length``).
- Append-only: the trigger ``class_session_overrides_immutable`` rejects
  ``UPDATE`` and ``DELETE`` with SQLSTATE 42501 (the 0017 pattern).

No rows are inserted. Downgrading drops the trigger, the table and the
function.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "class_session_overrides"
TRIGGER: Final = "class_session_overrides_immutable"

SOFT_CODES: Final = (
    "tutor_unavailable",
    "room_unavailable",
    "center_closed",
    "outside_operating_hours",
    "tutor_not_qualified",
    "group_capacity_exceeded",
    "notice_window",
)
"""The ADR-0005 soft codes in rule order (``scheduling.conflicts.SOFT``)."""

CHECKS: Final = {
    "codes_soft": "cardinality(codes) > 0 AND codes <@ ARRAY[{}]::text[]".format(
        ", ".join(f"'{code}'" for code in SOFT_CODES)
    ),
    "reason_length": "char_length(btrim(reason)) BETWEEN 1 AND 1000",
}

IMMUTABLE_FUNCTION: Final = f"""
CREATE FUNCTION {TRIGGER}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'class_session_overrides rows never change'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("conflicts", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["class_session_id"],
            ["class_sessions.id"],
            name=op.f(f"fk_{TABLE}_class_session_id_class_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    op.create_index(
        op.f(f"ix_{TABLE}_class_session_id"), TABLE, ["class_session_id"], unique=False
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE OR DELETE ON {TABLE}"
        f" FOR EACH ROW EXECUTE FUNCTION {TRIGGER}()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {TRIGGER} ON {TABLE}")
    op.drop_table(TABLE)
    op.execute(f"DROP FUNCTION {TRIGGER}()")
