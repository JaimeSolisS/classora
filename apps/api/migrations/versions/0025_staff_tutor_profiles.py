"""Staff: ``tutor_profiles``.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-20

M3-07 (#43; spec §4: the tutor profile). Audited by the ORM and versioned.

- ``tutor_profiles``: ``user_id`` (``users``, ``ON DELETE RESTRICT``, unique:
  ``uq_tutor_profiles_user_id``, at most one profile per user) and
  ``professional_experience TEXT NULL`` (at most 4000 characters, trimmed).

Contact details stay on the account (``users.email``/``phone``). Rows are
created lazily by the first profile edit, so no rows are inserted and no
backfill is needed. Downgrading drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tutor_profiles"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("professional_experience", sa.Text(), nullable=True),
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "char_length(professional_experience) <= 4000",
            name=op.f(f"ck_{TABLE}_professional_experience_length"),
        ),
        sa.CheckConstraint(
            "professional_experience = btrim(professional_experience)",
            name=op.f(f"ck_{TABLE}_professional_experience_trimmed"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("user_id", name=op.f(f"uq_{TABLE}_user_id")),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
