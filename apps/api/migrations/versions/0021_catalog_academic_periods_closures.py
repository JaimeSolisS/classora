"""Catalog: ``btree_gist``, ``academic_periods`` and ``closures``.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-20

M3-02 (#38; spec §3, §4; ADR-0004). Inclusive ``DATE`` ranges (local
calendar days); both tables are audited and versioned by the ORM.

- ``CREATE EXTENSION IF NOT EXISTS btree_gist`` (architecture ISSUE-D6, owner
  decision #38 E1 (a)): trusted and shipped with PostgreSQL 18. It lets a GiST
  exclusion constraint compare ``period_type`` with ``=``. M4-02 reuses it.
- ``academic_periods``: ``name VARCHAR(100)`` (trimmed, never blank; unique
  ignoring case through ``uq_academic_periods_name_ci``), ``period_type``
  (``school_year``, ``semester``, ``term``), ``starts_on <= ends_on`` (at
  most 730 days apart), ``is_active``. ``ex_academic_periods_no_overlap``:
  active periods of one type never share a day.
- ``closures``: ``name VARCHAR(100)`` (trimmed, never blank, not unique),
  ``closure_type`` (``holiday``, ``closure``, ``vacation``), ``starts_on <=
  ends_on`` (at most 366 days apart), ``is_active``; the GiST index
  ``ix_closures_active_range`` on the active ranges. Closures may overlap.

No rows are inserted. Downgrading drops both tables, then the extension.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATE_RANGE = "daterange(starts_on, ends_on, '[]')"


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


def _range_table_columns(type_column: str) -> list[sa.Column[object]]:
    return [
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(type_column, sa.String(length=20), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_standard_columns(),
    ]


def _checks(
    table: str, type_column: str, types: tuple[str, ...], max_span: int
) -> list[sa.CheckConstraint]:
    quoted = ", ".join(f"'{value}'" for value in types)
    return [
        sa.CheckConstraint("name = btrim(name)", name=op.f(f"ck_{table}_name_trimmed")),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name=op.f(f"ck_{table}_name_not_blank")
        ),
        sa.CheckConstraint(
            f"{type_column} IN ({quoted})",
            name=op.f(f"ck_{table}_{type_column}_known"),
        ),
        sa.CheckConstraint(
            "starts_on <= ends_on", name=op.f(f"ck_{table}_starts_before_ends")
        ),
        sa.CheckConstraint(
            f"ends_on - starts_on <= {max_span}", name=op.f(f"ck_{table}_span_max")
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "academic_periods",
        *_range_table_columns("period_type"),
        *_checks(
            "academic_periods",
            "period_type",
            ("school_year", "semester", "term"),
            730,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academic_periods")),
        postgresql.ExcludeConstraint(
            (sa.text("period_type"), "="),
            (sa.text(DATE_RANGE), "&&"),
            name="ex_academic_periods_no_overlap",
            using="gist",
            where=sa.text("is_active"),
        ),
    )
    op.create_index(
        "uq_academic_periods_name_ci",
        "academic_periods",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "closures",
        *_range_table_columns("closure_type"),
        *_checks("closures", "closure_type", ("holiday", "closure", "vacation"), 366),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_closures")),
    )
    op.create_index(
        "ix_closures_active_range",
        "closures",
        [sa.text(DATE_RANGE)],
        postgresql_using="gist",
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("ix_closures_active_range", table_name="closures")
    op.drop_table("closures")
    op.drop_index("uq_academic_periods_name_ci", table_name="academic_periods")
    op.drop_table("academic_periods")
    op.execute("DROP EXTENSION btree_gist")
