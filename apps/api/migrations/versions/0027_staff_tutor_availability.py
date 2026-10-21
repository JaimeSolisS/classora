"""Staff: tutor availability (settings, weekly windows, exceptions).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-20

M3-09 (#45; spec §3; ADR-0004: local ``DATE`` and ``TIME``, never instants).
All three tables are audited by the ORM.

- ``tutor_availability_settings``: one row per tutor at most
  (``uq_tutor_availability_settings_user_id``; ``users`` ``ON DELETE
  RESTRICT``), versioned. Created lazily by the first weekly change; its
  ``version`` covers the weekly windows. M3-09b (#197) adds the limits.
- ``tutor_availability_windows``: ``tutor_user_id`` (``users``, indexed), ISO
  ``weekday`` 1-7, ``starts_at < ends_at`` (local ``TIME``, whole minutes).
  ``ex_tutor_windows_no_overlap`` (GiST, the ``btree_gist`` extension of
  revision 0021): windows of one tutor on one weekday never overlap;
  touching windows are allowed.
- ``tutor_availability_exceptions``: ``tutor_user_id`` (``users``, indexed),
  ``kind`` ``unavailable`` | ``available``, inclusive ``starts_on`` /
  ``ends_on`` (at most 366 days apart), optional ``starts_at`` / ``ends_at``
  (both or neither, ``starts_at < ends_at``, whole minutes; an ``available``
  exception always has them), ``note VARCHAR(200)`` (trimmed). Overlaps are
  allowed.

No rows are inserted. Downgrading drops the three tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS = "tutor_availability_settings"
WINDOWS = "tutor_availability_windows"
EXCEPTIONS = "tutor_availability_exceptions"
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


def _tutor_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tutor_user_id"],
        ["users.id"],
        name=op.f(f"fk_{table}_tutor_user_id_users"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        SETTINGS,
        sa.Column("user_id", sa.UUID(), nullable=False),
        *_standard_columns(versioned=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f(f"fk_{SETTINGS}_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{SETTINGS}")),
        sa.UniqueConstraint("user_id", name=op.f(f"uq_{SETTINGS}_user_id")),
    )
    op.create_table(
        WINDOWS,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
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
        _tutor_fk(WINDOWS),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{WINDOWS}")),
        postgresql.ExcludeConstraint(
            (sa.text("tutor_user_id"), "="),
            (sa.text("weekday"), "="),
            (sa.text(WINDOW_RANGE), "&&"),
            name="ex_tutor_windows_no_overlap",
            using="gist",
        ),
    )
    op.create_index(
        op.f(f"ix_{WINDOWS}_tutor_user_id"), WINDOWS, ["tutor_user_id"], unique=False
    )
    op.create_table(
        EXCEPTIONS,
        sa.Column("tutor_user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=True),
        sa.Column("ends_at", sa.Time(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        *_standard_columns(versioned=False),
        sa.CheckConstraint(
            "kind IN ('unavailable', 'available')",
            name=op.f(f"ck_{EXCEPTIONS}_kind_valid"),
        ),
        sa.CheckConstraint(
            "starts_on <= ends_on", name=op.f(f"ck_{EXCEPTIONS}_starts_before_ends")
        ),
        sa.CheckConstraint(
            "ends_on - starts_on <= 366", name=op.f(f"ck_{EXCEPTIONS}_span_max")
        ),
        sa.CheckConstraint(
            "(starts_at IS NULL AND ends_at IS NULL)"
            " OR (starts_at IS NOT NULL AND ends_at IS NOT NULL"
            " AND starts_at < ends_at)",
            name=op.f(f"ck_{EXCEPTIONS}_hours_valid"),
        ),
        sa.CheckConstraint(
            "starts_at IS NULL OR (EXTRACT(SECOND FROM starts_at) = 0"
            " AND EXTRACT(SECOND FROM ends_at) = 0)",
            name=op.f(f"ck_{EXCEPTIONS}_zero_seconds"),
        ),
        sa.CheckConstraint(
            "kind = 'unavailable' OR starts_at IS NOT NULL",
            name=op.f(f"ck_{EXCEPTIONS}_available_has_hours"),
        ),
        sa.CheckConstraint(
            "note = btrim(note)", name=op.f(f"ck_{EXCEPTIONS}_note_trimmed")
        ),
        _tutor_fk(EXCEPTIONS),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{EXCEPTIONS}")),
    )
    op.create_index(
        op.f(f"ix_{EXCEPTIONS}_tutor_user_id"),
        EXCEPTIONS,
        ["tutor_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table(EXCEPTIONS)
    op.drop_table(WINDOWS)
    op.drop_table(SETTINGS)
