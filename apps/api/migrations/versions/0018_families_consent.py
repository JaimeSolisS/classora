"""Families: student logins and consent records.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-19

M2-17 (#35; ADR-0007 D17, D18; spec §2, §9, §16).

- ``students.user_id``: the student's optional login account, a foreign key
  to ``users`` (``ON DELETE RESTRICT``) with the unique constraint
  ``uq_students_user_id`` (one account per profile, one profile per account).
- ``consent_records``: a consent given for a student and a guardian profile.
  ``scope`` is ``student_login`` (a guardian allows a minor's login) or
  ``guardian_academic_access`` (an adult student shares academic records with
  that guardian); ``method`` is ``in_app``, ``paper`` or ``in_person``. Each
  record stamps the privacy-notice version current when it was given
  (``notice_version_id``, #174), when (``granted_at``) and who recorded it
  (``recorded_by_user_id``). A revocation sets ``revoked_at``,
  ``revoked_by_user_id`` and ``revocation_cause`` together (a CHECK). Records
  are never deleted. The partial unique index ``uq_consent_records_active``
  allows one active record per student, guardian and scope.

Downgrading drops ``consent_records``, then the column and its constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
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


def upgrade() -> None:
    op.add_column("students", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_students_user_id_users"),
        "students",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_students_user_id", "students", ["user_id"])

    op.create_table(
        "consent_records",
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("guardian_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("notice_version_id", sa.UUID(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.UUID(), nullable=True),
        sa.Column("revocation_cause", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "scope IN ('student_login', 'guardian_academic_access')",
            name=op.f("ck_consent_records_scope_valid"),
        ),
        sa.CheckConstraint(
            "method IN ('in_app', 'paper', 'in_person')",
            name=op.f("ck_consent_records_method_valid"),
        ),
        sa.CheckConstraint(
            "revocation_cause IN ('revoked', 'guardian_unlinked')",
            name=op.f("ck_consent_records_revocation_cause_valid"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_user_id IS NULL"
            " AND revocation_cause IS NULL)"
            " OR (revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL"
            " AND revocation_cause IS NOT NULL)",
            name=op.f("ck_consent_records_revocation_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            name=op.f("fk_consent_records_student_id_students"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["guardian_id"],
            ["guardians.id"],
            name=op.f("fk_consent_records_guardian_id_guardians"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["notice_version_id"],
            ["privacy_notice_versions.id"],
            # Named explicitly: the convention's name would pass PostgreSQL's
            # 63-character limit.
            name=op.f("fk_consent_records_notice_version_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name=op.f("fk_consent_records_recorded_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name=op.f("fk_consent_records_revoked_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consent_records")),
    )
    op.create_index(
        op.f("ix_consent_records_student_id"),
        "consent_records",
        ["student_id"],
        unique=False,
    )
    op.create_index(
        "uq_consent_records_active",
        "consent_records",
        ["student_id", "guardian_id", "scope"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_consent_records_active", table_name="consent_records")
    op.drop_index(op.f("ix_consent_records_student_id"), table_name="consent_records")
    op.drop_table("consent_records")
    op.drop_constraint("uq_students_user_id", "students", type_="unique")
    op.drop_constraint(
        op.f("fk_students_user_id_users"), "students", type_="foreignkey"
    )
    op.drop_column("students", "user_id")
