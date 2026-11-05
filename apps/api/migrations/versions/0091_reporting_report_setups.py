"""Reporting: ``report_setups``.

Revision ID: 0091
Revises: 0090
Create Date: 2026-11-05

M8-08 (#125 A1; spec §8 saved report setups; architecture ISSUE-A2). A saved
export setup is a name and one #124 ``ExportParameters``; running it posts
the stored parameters to ``POST /api/v1/exports`` (no file, no rows here).

- ``report_setups`` (audited, versioned): ``created_by_user_id`` (``users``,
  ``ON DELETE RESTRICT``), ``name TEXT`` (trimmed, 1-80 characters),
  ``dataset TEXT`` (CHECK ``^[a-z_]{3,32}$``, the #124 rule, so a new
  dataset needs no migration), ``parameters JSONB`` (an object whose
  ``dataset`` equals the column), ``shared_role TEXT`` (null or one of the
  four staff role codes), ``version``. Indexes: unique ``(created_by_user_id,
  lower(name))``; ``shared_role`` where it is set.

No rows are inserted. Downgrading drops the table (with every saved setup).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0091"
down_revision: str | Sequence[str] | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETUPS: Final = "report_setups"
NAME_UNIQUE: Final = "uq_report_setups_creator_name"
SHARED_INDEX: Final = "ix_report_setups_shared_role"


def _timestamp(name: str) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        SETUPS,
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("shared_role", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(btrim(name)) BETWEEN 1 AND 80",
            name=op.f(f"ck_{SETUPS}_name_trimmed"),
        ),
        sa.CheckConstraint(
            "dataset ~ '^[a-z_]{3,32}$'", name=op.f(f"ck_{SETUPS}_dataset_format")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'"
            " AND coalesce(parameters->>'dataset', '') = dataset",
            name=op.f(f"ck_{SETUPS}_parameters_dataset"),
        ),
        sa.CheckConstraint(
            "shared_role IS NULL OR shared_role IN ('owner', 'administrator',"
            " 'academic_coordinator', 'finance_staff')",
            name=op.f(f"ck_{SETUPS}_shared_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{SETUPS}_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SETUPS}")),
    )
    op.create_index(
        NAME_UNIQUE,
        SETUPS,
        ["created_by_user_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index(
        SHARED_INDEX,
        SETUPS,
        ["shared_role"],
        postgresql_where=sa.text("shared_role IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(SHARED_INDEX, table_name=SETUPS)
    op.drop_index(NAME_UNIQUE, table_name=SETUPS)
    op.drop_table(SETUPS)
