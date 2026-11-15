"""Families: name-search extensions, ``students`` and their emergency contacts.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-19

M2-15 (#33; spec §4, §12; ISSUE-D6 names part accepted on #33). In order:

- ``CREATE EXTENSION IF NOT EXISTS pg_trgm`` and ``unaccent`` (both trusted
  and shipped in the ``postgres:18`` image);
- ``public.f_unaccent(text)``: an ``IMMUTABLE`` wrapper of ``unaccent()``
  (which is only ``STABLE`` and can't be used in an index) with the
  dictionary named explicitly. ``EXECUTE`` keeps its default ``PUBLIC``
  grant, so a least-privilege runtime role (#165) can call it;
- ``students``: names (trimmed, never blank), ``birthdate DATE NOT NULL``
  (from 1900-01-01), optional ``email``/``phone`` with the #31 patterns,
  ``school_name``/``school_grade`` (trimmed), ``education_level_id`` (``ON
  DELETE RESTRICT``, indexed), ``is_active``, ``version``, and the GIN
  trigram expression index ``ix_students_search_name_trgm`` on
  ``public.f_unaccent(lower(given_names || ' ' || family_names))``;
- ``student_emergency_contacts``: ``student_id`` (``ON DELETE RESTRICT``,
  indexed), ``name``, ``relationship``, ``phone`` and ``position`` 1-5,
  unique per student ``DEFERRABLE INITIALLY DEFERRED``.

Downgrading drops the tables, then the function, then both extensions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMAIL_PATTERN = r"^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$"
PHONE_PATTERN = r"^\+?[0-9][0-9 ().-]{5,30}[0-9]$"

F_UNACCENT = """
CREATE FUNCTION public.f_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
"""


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


def _trimmed(table: str, column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} = btrim({column})", name=op.f(f"ck_{table}_{column}_trimmed")
    )


def _not_blank(table: str, column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} ~ '[^[:space:]]'", name=op.f(f"ck_{table}_{column}_not_blank")
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(F_UNACCENT)

    op.create_table(
        "students",
        sa.Column("given_names", sa.String(length=100), nullable=False),
        sa.Column("family_names", sa.String(length=100), nullable=False),
        sa.Column("birthdate", sa.Date(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("school_name", sa.String(length=200), nullable=True),
        sa.Column("school_grade", sa.String(length=50), nullable=True),
        sa.Column("education_level_id", sa.UUID(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_timestamps(),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _trimmed("students", "given_names"),
        _not_blank("students", "given_names"),
        _trimmed("students", "family_names"),
        _not_blank("students", "family_names"),
        sa.CheckConstraint(
            "birthdate >= DATE '1900-01-01'", name=op.f("ck_students_birthdate_min")
        ),
        sa.CheckConstraint(
            f"email ~ '{EMAIL_PATTERN}'", name=op.f("ck_students_email_format")
        ),
        sa.CheckConstraint(
            f"phone ~ '{PHONE_PATTERN}'", name=op.f("ck_students_phone_format")
        ),
        _trimmed("students", "school_name"),
        _trimmed("students", "school_grade"),
        sa.ForeignKeyConstraint(
            ["education_level_id"],
            ["education_levels.id"],
            name=op.f("fk_students_education_level_id_education_levels"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_students")),
    )
    op.create_index(
        op.f("ix_students_education_level_id"),
        "students",
        ["education_level_id"],
        unique=False,
    )
    op.create_index(
        "ix_students_search_name_trgm",
        "students",
        [
            sa.text(
                "(public.f_unaccent(lower(given_names || ' ' || family_names)))"
                " gin_trgm_ops"
            )
        ],
        postgresql_using="gin",
    )

    op.create_table(
        "student_emergency_contacts",
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("relationship", sa.String(length=50), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        *_timestamps(),
        _trimmed("student_emergency_contacts", "name"),
        _not_blank("student_emergency_contacts", "name"),
        _trimmed("student_emergency_contacts", "relationship"),
        _not_blank("student_emergency_contacts", "relationship"),
        sa.CheckConstraint(
            f"phone ~ '{PHONE_PATTERN}'",
            name=op.f("ck_student_emergency_contacts_phone_format"),
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 5",
            name=op.f("ck_student_emergency_contacts_position_range"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name=op.f("fk_student_emergency_contacts_student_id_students"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_student_emergency_contacts")),
        sa.UniqueConstraint(
            "student_id",
            "position",
            name="uq_student_emergency_contacts_student_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(
        op.f("ix_student_emergency_contacts_student_id"),
        "student_emergency_contacts",
        ["student_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_student_emergency_contacts_student_id"),
        table_name="student_emergency_contacts",
    )
    op.drop_table("student_emergency_contacts")
    op.drop_index("ix_students_search_name_trgm", table_name="students")
    op.drop_index(op.f("ix_students_education_level_id"), table_name="students")
    op.drop_table("students")
    op.execute("DROP FUNCTION public.f_unaccent(text)")
    op.execute("DROP EXTENSION unaccent")
    op.execute("DROP EXTENSION pg_trgm")
