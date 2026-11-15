"""Catalog: the ``education_levels`` and ``subjects`` tables.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-19

M2-14 (#32; spec §4, §12). Two audited, versioned tables:

- ``education_levels``: ``name VARCHAR(100)`` (trimmed, never blank; unique
  ignoring case through ``uq_education_levels_name_ci`` on ``lower(name)``),
  ``sort_order SMALLINT`` (0-9999), ``is_active BOOLEAN DEFAULT true`` and
  ``seed_key VARCHAR(40) NULL`` (unique, ``^[a-z][a-z_]*$``; set only on rows
  the reference seed created).
- ``subjects``: ``education_level_id`` (``ON DELETE RESTRICT``, indexed),
  ``name VARCHAR(100)`` with the same two CHECKs (unique per level ignoring
  case through ``uq_subjects_level_name_ci`` on ``(education_level_id,
  lower(name))``) and ``is_active BOOLEAN DEFAULT true``.

Uniqueness doesn't fold accents. Adds no extension. Downgrading drops exactly
these two tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _standard_columns() -> list[sa.Column[object]]:
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    ]


def _name_checks(table: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint("name = btrim(name)", name=op.f(f"ck_{table}_name_trimmed")),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name=op.f(f"ck_{table}_name_not_blank")
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "education_levels",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("seed_key", sa.String(length=40), nullable=True),
        *_standard_columns(),
        *_name_checks("education_levels"),
        sa.CheckConstraint(
            "sort_order BETWEEN 0 AND 9999",
            name=op.f("ck_education_levels_sort_order_range"),
        ),
        sa.CheckConstraint(
            "seed_key ~ '^[a-z][a-z_]*$'",
            name=op.f("ck_education_levels_seed_key_format"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_education_levels")),
        sa.UniqueConstraint("seed_key", name=op.f("uq_education_levels_seed_key")),
    )
    op.create_index(
        "uq_education_levels_name_ci",
        "education_levels",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "subjects",
        sa.Column("education_level_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_standard_columns(),
        *_name_checks("subjects"),
        sa.ForeignKeyConstraint(
            ["education_level_id"],
            ["education_levels.id"],
            name=op.f("fk_subjects_education_level_id_education_levels"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subjects")),
    )
    op.create_index(
        op.f("ix_subjects_education_level_id"),
        "subjects",
        ["education_level_id"],
        unique=False,
    )
    op.create_index(
        "uq_subjects_level_name_ci",
        "subjects",
        [sa.text("education_level_id"), sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_subjects_level_name_ci", table_name="subjects")
    op.drop_index(op.f("ix_subjects_education_level_id"), table_name="subjects")
    op.drop_table("subjects")
    op.drop_index("uq_education_levels_name_ci", table_name="education_levels")
    op.drop_table("education_levels")
