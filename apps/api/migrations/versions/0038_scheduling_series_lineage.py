"""Scheduling: ``class_series.split_from_series_id`` (series lineage).

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-22

M4-09 (#54 A2; ADR-0005 D4, ISSUE-SC2). A "this and following" edit of a
running series ends the old series and creates a new one from the edit
date; the new series points to the old one.

- ``split_from_series_id`` ``UUID NULL``, foreign key to ``class_series``
  ``ON DELETE RESTRICT``, indexed;
- ``ck_class_series_split_from_not_self``: a series never points to itself.

Existing rows get ``NULL`` (no lineage). Downgrading drops the column with
its index, key and check.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SERIES = "class_series"


def upgrade() -> None:
    op.add_column(SERIES, sa.Column("split_from_series_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_class_series_split_from_series_id_class_series"),
        SERIES,
        SERIES,
        ["split_from_series_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_class_series_split_from_series_id"),
        SERIES,
        ["split_from_series_id"],
        unique=False,
    )
    op.create_check_constraint(
        op.f("ck_class_series_split_from_not_self"),
        SERIES,
        "split_from_series_id <> id",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_class_series_split_from_not_self"), SERIES, type_="check"
    )
    op.drop_index(op.f("ix_class_series_split_from_series_id"), table_name=SERIES)
    op.drop_constraint(
        op.f("fk_class_series_split_from_series_id_class_series"),
        SERIES,
        type_="foreignkey",
    )
    op.drop_column(SERIES, "split_from_series_id")
