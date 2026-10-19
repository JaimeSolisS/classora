"""Families: guardian profiles and student-guardian links.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-19

M2-16 (#34; spec §2, §12, §16). The extensions and ``public.f_unaccent``
already exist (0014), so this revision doesn't touch them.

- ``guardians``: names (trimmed, never blank), the profile's own optional
  ``email``/``phone`` with the #31 patterns, ``user_id`` (a foreign key to
  ``users``, ``ON DELETE RESTRICT``, unique ``uq_guardians_user_id``: one
  account per profile), ``is_active``, ``version`` and the GIN trigram
  expression index ``ix_guardians_search_name_trgm`` on the same expression
  as ``ix_students_search_name_trgm``;
- ``student_guardians``: ``student_id`` (``ON DELETE RESTRICT``),
  ``guardian_id`` (``ON DELETE RESTRICT``, indexed), ``relationship`` (one of
  eight codes, a CHECK) and the unique constraint
  ``uq_student_guardians_student_guardian (student_id, guardian_id)``, whose
  index also serves lookups by student.

Downgrading drops ``student_guardians``, then ``guardians``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMAIL_PATTERN = r"^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$"
PHONE_PATTERN = r"^\+?[0-9][0-9 ().-]{5,30}[0-9]$"
RELATIONSHIPS = (
    "mother",
    "father",
    "grandparent",
    "stepparent",
    "sibling",
    "other_relative",
    "legal_guardian",
    "other",
)


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
    op.create_table(
        "guardians",
        sa.Column("given_names", sa.String(length=100), nullable=False),
        sa.Column("family_names", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_timestamps(),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _trimmed("guardians", "given_names"),
        _not_blank("guardians", "given_names"),
        _trimmed("guardians", "family_names"),
        _not_blank("guardians", "family_names"),
        sa.CheckConstraint(
            f"email ~ '{EMAIL_PATTERN}'", name=op.f("ck_guardians_email_format")
        ),
        sa.CheckConstraint(
            f"phone ~ '{PHONE_PATTERN}'", name=op.f("ck_guardians_phone_format")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_guardians_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guardians")),
        sa.UniqueConstraint("user_id", name="uq_guardians_user_id"),
    )
    op.create_index(
        "ix_guardians_search_name_trgm",
        "guardians",
        [
            sa.text(
                "(public.f_unaccent(lower(given_names || ' ' || family_names)))"
                " gin_trgm_ops"
            )
        ],
        postgresql_using="gin",
    )

    op.create_table(
        "student_guardians",
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("guardian_id", sa.UUID(), nullable=False),
        sa.Column("relationship", sa.Text(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "relationship IN ({})".format(", ".join(f"'{c}'" for c in RELATIONSHIPS)),
            name=op.f("ck_student_guardians_relationship_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name=op.f("fk_student_guardians_student_id_students"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["guardian_id"],
            ["guardians.id"],
            name=op.f("fk_student_guardians_guardian_id_guardians"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_student_guardians")),
        sa.UniqueConstraint(
            "student_id", "guardian_id", name="uq_student_guardians_student_guardian"
        ),
    )
    op.create_index(
        op.f("ix_student_guardians_guardian_id"),
        "student_guardians",
        ["guardian_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_student_guardians_guardian_id"), table_name="student_guardians"
    )
    op.drop_table("student_guardians")
    op.drop_index("ix_guardians_search_name_trgm", table_name="guardians")
    op.drop_table("guardians")
