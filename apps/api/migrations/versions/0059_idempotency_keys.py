"""API idempotency: the ``idempotency_keys`` table.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-27

M6-03 (#88; ADR-0006 E6, ISSUE-F4). One row per user, endpoint (method and
route template) and ``Idempotency-Key`` of a financial ``POST``: the request's
SHA-256 ``request_hash`` (exactly 32 bytes) and its ``2xx`` response
(``response_status`` ``200``-``299``, ``response_body`` JSONB). Both response
columns are nullable: they are ``NULL`` only inside the claiming transaction
(and the body for a ``204``). ``key`` is 16-128 characters of
``[A-Za-z0-9_-]``; ``endpoint`` is 1-300 characters.
``(user_id, endpoint, key)`` is unique (the claim's upsert target);
``expires_at`` is indexed for the hourly purge. ``user_id`` references
``users`` ``ON DELETE RESTRICT`` (no foreign key to ``users`` cascades).
Not audited. Downgrading drops exactly this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059"
down_revision: str | Sequence[str] | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "idempotency_keys"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "char_length(endpoint) BETWEEN 1 AND 300",
            name=op.f(f"ck_{TABLE}_endpoint_length"),
        ),
        sa.CheckConstraint(
            "key ~ '^[A-Za-z0-9_-]{16,128}$'", name=op.f(f"ck_{TABLE}_key_format")
        ),
        sa.CheckConstraint(
            "octet_length(request_hash) = 32",
            name=op.f(f"ck_{TABLE}_request_hash_length"),
        ),
        sa.CheckConstraint(
            "response_status BETWEEN 200 AND 299",
            name=op.f(f"ck_{TABLE}_response_status_2xx"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint(
            "user_id", "endpoint", "key", name="uq_idempotency_keys_user_endpoint_key"
        ),
    )
    op.create_index("ix_idempotency_keys_expires_at", TABLE, ["expires_at"])


def downgrade() -> None:
    op.drop_table(TABLE)
