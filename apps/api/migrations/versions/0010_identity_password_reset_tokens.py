"""Identity: allow ``password_reset`` account tokens.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19

M2-12 (#30; ADR-0007 D11, D12). Widens the ``account_tokens.kind`` CHECK to
``kind IN ('invitation', 'password_reset')``. Nothing else changes: the
partial unique index on ``(user_id, kind)`` already keeps at most one live
reset token per user, so a new reset replaces the previous one. Downgrading
deletes the ``password_reset`` rows, then restores the #29 CHECK.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_account_tokens_kind_valid"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "account_tokens", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), "account_tokens", "kind IN ('invitation', 'password_reset')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM account_tokens WHERE kind = 'password_reset'")
    op.drop_constraint(op.f(CONSTRAINT), "account_tokens", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), "account_tokens", "kind IN ('invitation')"
    )
