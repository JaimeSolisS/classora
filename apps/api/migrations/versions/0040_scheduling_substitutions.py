"""Scheduling: substitution offers and ``class_session_substitutions``.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-22

M4-10 (#55 A1, A2; ADR-0005 D3 amendment).

- ``assignment_offers.kind`` ``VARCHAR(20) NOT NULL DEFAULT 'assignment'``:
  ``assignment`` | ``substitution`` (``ck_assignment_offers_kind_valid``),
  and a substitution always covers one session
  (``ck_assignment_offers_substitution_session_scope``: ``kind =
  'assignment' OR scope = 'session'``). Existing rows become
  ``assignment`` through the default.
- ``class_session_substitutions`` (``Audited``, not versioned,
  append-only): the session (indexed), the ``substitution`` offer
  (``UNIQUE``), the original tutor and the substitute (never the same), the
  trimmed, non-empty reason (at most 1000 characters), the requester and
  the session's times when it was offered. Every foreign key is ``ON DELETE
  RESTRICT``.

**Downgrade:** drops the table, deletes the ``substitution`` offers and
their covered-session rows, then drops the column and its CHECKs.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OFFERS: Final = "assignment_offers"
TABLE: Final = "class_session_substitutions"
OFFER_CHECKS: Final = {
    "kind_valid": "kind IN ('assignment', 'substitution')",
    "substitution_session_scope": "kind = 'assignment' OR scope = 'session'",
}


def _user_fk(column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        ["users.id"],
        name=op.f(f"fk_{TABLE}_{column}_users"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.add_column(
        OFFERS,
        sa.Column(
            "kind",
            sa.String(length=20),
            server_default=sa.text("'assignment'"),
            nullable=False,
        ),
    )
    for name, condition in OFFER_CHECKS.items():
        op.create_check_constraint(op.f(f"ck_{OFFERS}_{name}"), OFFERS, condition)

    op.create_table(
        TABLE,
        sa.Column("class_session_id", sa.UUID(), nullable=False),
        sa.Column("offer_id", sa.UUID(), nullable=False),
        sa.Column("original_tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("substitute_tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=False),
        sa.Column("slot_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_ends_at", sa.DateTime(timezone=True), nullable=False),
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
            "original_tutor_user_id <> substitute_tutor_user_id",
            name=op.f(f"ck_{TABLE}_substitute_differs"),
        ),
        sa.CheckConstraint(
            "reason = btrim(reason) AND reason <> ''",
            name=op.f(f"ck_{TABLE}_reason_trimmed"),
        ),
        sa.ForeignKeyConstraint(
            ["class_session_id"],
            ["class_sessions.id"],
            name=op.f(f"fk_{TABLE}_class_session_id_class_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["assignment_offers.id"],
            name=op.f(f"fk_{TABLE}_offer_id_assignment_offers"),
            ondelete="RESTRICT",
        ),
        _user_fk("original_tutor_user_id"),
        _user_fk("substitute_tutor_user_id"),
        _user_fk("requested_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("offer_id", name=op.f(f"uq_{TABLE}_offer_id")),
    )
    op.create_index(
        op.f(f"ix_{TABLE}_class_session_id"), TABLE, ["class_session_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f(f"ix_{TABLE}_class_session_id"), table_name=TABLE)
    op.drop_table(TABLE)
    op.execute(
        "DELETE FROM assignment_offer_sessions WHERE offer_id IN"
        " (SELECT id FROM assignment_offers WHERE kind = 'substitution')"
    )
    op.execute("DELETE FROM assignment_offers WHERE kind = 'substitution'")
    for name in reversed(OFFER_CHECKS):
        op.drop_constraint(op.f(f"ck_{OFFERS}_{name}"), OFFERS, type_="check")
    op.drop_column(OFFERS, "kind")
