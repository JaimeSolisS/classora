"""Finance: expense categories and vendors.

Revision ID: 0083
Revises: 0082
Create Date: 2026-11-03

M7-11 (#114 A; ADR-0017 E12, E15, its #114 notes; ``_docs/data-model.md``
§12.1, §12.2).

- ``expense_categories`` (audited, no mask): a staff-named category, unique
  ignoring case, with an optional ``system_key`` (``rent``, ``utilities``,
  ``supplies``, ``equipment``, ``maintenance``; unique). While a row holds its
  key, clients show the catalog label of the key; a rename clears it for good
  (#114 E3).
- ``vendors`` (audited, no mask): a name unique ignoring case (#114 E4) and an
  optional free-text ``contact``.
- **Seed, no author, not audited** (the ``0075`` precedent): the five keyed
  categories with their Spanish names.
- Neither table has a delete route (#114 E5): ``is_active`` retires a row. A
  ``BEFORE TRUNCATE`` trigger refuses ``TRUNCATE`` on both (SQLSTATE 42501).

No foreign key reaches an existing table, and no permission is added here
(``expense.configure`` is seeded by ``make seed``). Downgrading drops the
triggers, the function and both tables with their rows: nothing references them
before #115.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0083"
down_revision: str | Sequence[str] | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES: Final = "expense_categories"
VENDORS: Final = "vendors"
TABLES: Final = (CATEGORIES, VENDORS)
GUARD: Final = "finance_config_no_truncate"

SEED: Final = (
    ("Renta", "rent"),
    ("Servicios", "utilities"),
    ("Insumos", "supplies"),
    ("Equipo", "equipment"),
    ("Mantenimiento", "maintenance"),
)

GUARD_FUNCTION: Final = f"""
CREATE FUNCTION {GUARD}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- #114 E5: categories and vendors are retired with is_active, never removed
    -- wholesale.
    RAISE EXCEPTION '% is never truncated', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def _common_columns() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
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


def _trimmed(column: str, maximum: int) -> str:
    return (
        f"char_length({column}) BETWEEN 1 AND {maximum} AND {column} = btrim({column})"
    )


def upgrade() -> None:
    op.create_table(
        CATEGORIES,
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("system_key", sa.String(length=20), nullable=True),
        *_common_columns(),
        sa.CheckConstraint(
            _trimmed("name", 100), name=op.f(f"ck_{CATEGORIES}_name_trimmed")
        ),
        sa.CheckConstraint(
            "system_key IN (" + ", ".join(f"'{key}'" for _, key in SEED) + ")",
            name=op.f(f"ck_{CATEGORIES}_system_key_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CATEGORIES}")),
        sa.UniqueConstraint("system_key", name=op.f(f"uq_{CATEGORIES}_system_key")),
    )
    op.create_index(
        f"uq_{CATEGORIES}_lower_name",
        CATEGORIES,
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        VENDORS,
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("contact", sa.Text(), nullable=True),
        *_common_columns(),
        sa.CheckConstraint(
            _trimmed("name", 200), name=op.f(f"ck_{VENDORS}_name_trimmed")
        ),
        sa.CheckConstraint(
            _trimmed("contact", 500), name=op.f(f"ck_{VENDORS}_contact_trimmed")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{VENDORS}")),
    )
    op.create_index(
        f"uq_{VENDORS}_lower_name", VENDORS, [sa.text("lower(name)")], unique=True
    )
    op.execute(
        f"INSERT INTO {CATEGORIES} (name, system_key) VALUES "  # noqa: S608
        + ", ".join(f"('{name}', '{key}')" for name, key in SEED)
    )
    op.execute(GUARD_FUNCTION)
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}"
            f" FOR EACH STATEMENT EXECUTE FUNCTION {GUARD}()"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
    op.execute(f"DROP FUNCTION {GUARD}()")
    op.drop_table(VENDORS)
    op.drop_table(CATEGORIES)
