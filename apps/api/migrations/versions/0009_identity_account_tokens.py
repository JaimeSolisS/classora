"""Identity: the ``account_tokens`` table (single-use account tokens).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19

M2-11 (#29; ADR-0007 D11). One row per single-use account token: an
invitation today; #30 widens the ``kind`` CHECK for password resets. Only the
SHA-256 hash of the token is stored (``token_hash``, exactly 32 bytes,
unique). ``used_at`` and ``revoked_at`` are never both set. A partial unique
index on ``(user_id, kind)`` where both are ``NULL`` allows at most one live
token per user and kind. ``user_id`` and ``issued_by_user_id`` refer to
``users`` with ``ON DELETE RESTRICT``; ``issued_by_user_id`` is ``NULL`` only
for tokens issued by a shell command (#30). Adds no extension. Downgrading
drops exactly this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by_user_id", sa.UUID(), nullable=True),
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
            "kind IN ('invitation')", name=op.f("ck_account_tokens_kind_valid")
        ),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32",
            name=op.f("ck_account_tokens_token_hash_length"),
        ),
        sa.CheckConstraint(
            "used_at IS NULL OR revoked_at IS NULL",
            name=op.f("ck_account_tokens_used_or_revoked"),
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["users.id"],
            name=op.f("fk_account_tokens_issued_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_account_tokens_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_account_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_account_tokens_user_id"), "account_tokens", ["user_id"], unique=False
    )
    op.create_index(
        "uq_account_tokens_live_user_kind",
        "account_tokens",
        ["user_id", "kind"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_account_tokens_live_user_kind",
        table_name="account_tokens",
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )
    op.drop_index(op.f("ix_account_tokens_user_id"), table_name="account_tokens")
    op.drop_table("account_tokens")
