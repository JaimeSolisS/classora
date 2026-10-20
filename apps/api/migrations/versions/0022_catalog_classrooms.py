"""Catalog: ``classrooms`` and ``classroom_availability_windows``.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-20

M3-03 (#39; spec §3; ADR-0004). Both tables are audited by the ORM;
``classrooms`` is versioned (its ``version`` also covers its windows).

- ``classrooms``: ``name VARCHAR(100)`` (trimmed, never blank; unique ignoring
  case through ``uq_classrooms_name_ci``, inactive rows count), ``capacity
  SMALLINT`` between 1 and 500, ``is_active``.
- ``classroom_availability_windows``: ``classroom_id`` (``ON DELETE
  RESTRICT``, indexed), ISO ``weekday`` 1-7, ``starts_at < ends_at`` (local
  ``TIME``, whole minutes). ``ex_classroom_windows_no_overlap`` (GiST, the
  ``btree_gist`` extension of revision 0021): windows of one room on one
  weekday never overlap; touching windows are allowed.

A room without windows has no time restriction. No rows are inserted.
Downgrading drops both tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WINDOWS = "classroom_availability_windows"
WINDOW_RANGE = (
    "tsrange(DATE '2000-01-03' + starts_at, DATE '2000-01-03' + ends_at, '[)')"
)


def _standard_columns(*, versioned: bool) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [
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
    if versioned:
        columns.append(
            sa.Column(
                "version", sa.Integer(), server_default=sa.text("1"), nullable=False
            )
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "classrooms",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("capacity", sa.SmallInteger(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_standard_columns(versioned=True),
        sa.CheckConstraint(
            "name = btrim(name)", name=op.f("ck_classrooms_name_trimmed")
        ),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name=op.f("ck_classrooms_name_not_blank")
        ),
        sa.CheckConstraint(
            "capacity BETWEEN 1 AND 500", name=op.f("ck_classrooms_capacity_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classrooms")),
    )
    op.create_index(
        "uq_classrooms_name_ci",
        "classrooms",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        WINDOWS,
        sa.Column("classroom_id", sa.UUID(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        *_standard_columns(versioned=False),
        sa.CheckConstraint(
            "weekday BETWEEN 1 AND 7", name=op.f(f"ck_{WINDOWS}_weekday_range")
        ),
        sa.CheckConstraint(
            "starts_at < ends_at", name=op.f(f"ck_{WINDOWS}_starts_before_ends")
        ),
        sa.CheckConstraint(
            "EXTRACT(SECOND FROM starts_at) = 0 AND EXTRACT(SECOND FROM ends_at) = 0",
            name=op.f(f"ck_{WINDOWS}_zero_seconds"),
        ),
        sa.ForeignKeyConstraint(
            ["classroom_id"],
            ["classrooms.id"],
            name=op.f(f"fk_{WINDOWS}_classroom_id_classrooms"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{WINDOWS}")),
        postgresql.ExcludeConstraint(
            (sa.text("classroom_id"), "="),
            (sa.text("weekday"), "="),
            (sa.text(WINDOW_RANGE), "&&"),
            name="ex_classroom_windows_no_overlap",
            using="gist",
        ),
    )
    op.create_index(
        op.f(f"ix_{WINDOWS}_classroom_id"), WINDOWS, ["classroom_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f(f"ix_{WINDOWS}_classroom_id"), table_name=WINDOWS)
    op.drop_table(WINDOWS)
    op.drop_index("uq_classrooms_name_ci", table_name="classrooms")
    op.drop_table("classrooms")
