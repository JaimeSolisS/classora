"""Families: family groupings and their membership.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-19

M2-16b (#172; spec §2, §12, §16). The extensions and ``public.f_unaccent``
already exist (0014), so this revision doesn't touch them.

- ``families``: ``name`` (trimmed, never blank, not unique), ``is_active``,
  ``version`` and the GIN trigram expression index
  ``ix_families_search_name_trgm`` on ``public.f_unaccent(lower(name))``;
- ``family_students``: ``family_id`` (``ON DELETE RESTRICT``, indexed) and
  ``student_id`` (``ON DELETE RESTRICT``) with the unique constraint
  ``uq_family_students_student_id``: one family per student (owner decision
  #172 E2 (a));
- ``family_guardians``: the same for guardian profiles, with
  ``uq_family_guardians_guardian_id``.

Downgrading drops ``family_guardians``, ``family_students``, then
``families``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column[object]]:
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
    ]


def _membership(table: str, member: str, target: str) -> None:
    op.create_table(
        table,
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column(member, sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["families.id"],
            name=op.f(f"fk_{table}_family_id_families"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [member],
            [f"{target}.id"],
            name=op.f(f"fk_{table}_{member}_{target}"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        sa.UniqueConstraint(member, name=f"uq_{table}_{member}"),
    )
    op.create_index(op.f(f"ix_{table}_family_id"), table, ["family_id"], unique=False)


def upgrade() -> None:
    op.create_table(
        "families",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_timestamps(),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("name = btrim(name)", name=op.f("ck_families_name_trimmed")),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name=op.f("ck_families_name_not_blank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_families")),
    )
    op.create_index(
        "ix_families_search_name_trgm",
        "families",
        [sa.text("(public.f_unaccent(lower(name))) gin_trgm_ops")],
        postgresql_using="gin",
    )
    _membership("family_students", "student_id", "students")
    _membership("family_guardians", "guardian_id", "guardians")


def downgrade() -> None:
    for table in ("family_guardians", "family_students"):
        op.drop_index(op.f(f"ix_{table}_family_id"), table_name=table)
        op.drop_table(table)
    op.drop_index("ix_families_search_name_trgm", table_name="families")
    op.drop_table("families")
