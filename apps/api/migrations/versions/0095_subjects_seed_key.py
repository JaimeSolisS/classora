"""Catalog: ``subjects.seed_key`` for the built-in "General" subject.

Revision ID: 0095
Revises: 0094
Create Date: 2026-11-10

HF-12 (#597, E2 (a)). Adds ``subjects.seed_key VARCHAR(40) NULL``, unique
(``uq_subjects_seed_key``) and in the same format as
``education_levels.seed_key`` (CHECK ``ck_subjects_seed_key_format``,
``^[a-z][a-z_]*$``). It is set only on the subject the
``catalog.general_subject`` reference seed created or claimed, so the seed
and the qualification exemption recognize it after a rename; it never
appears in the API. Existing rows keep ``NULL``; no NOT NULL column changes
and no rows are inserted. Downgrading drops exactly the column (its
constraint and index go with it).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0095"
down_revision: str | Sequence[str] | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subjects", sa.Column("seed_key", sa.String(length=40), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_subjects_seed_key_format"),
        "subjects",
        "seed_key ~ '^[a-z][a-z_]*$'",
    )
    op.create_unique_constraint(op.f("uq_subjects_seed_key"), "subjects", ["seed_key"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_subjects_seed_key"), "subjects", type_="unique")
    op.drop_constraint(op.f("ck_subjects_seed_key_format"), "subjects", type_="check")
    op.drop_column("subjects", "seed_key")
