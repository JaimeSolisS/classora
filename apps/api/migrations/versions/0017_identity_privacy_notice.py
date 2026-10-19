"""Identity: privacy-notice versions and acceptances.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-19

M2-17b (#174; spec §9, §16, §18 item 9).

- ``privacy_notice_versions``: ``version INTEGER`` (unique, ``> 0``),
  ``published_at``, ``body_es`` and ``body_en`` (never blank). Immutable: the
  trigger ``privacy_notice_versions_immutable`` rejects ``UPDATE`` and
  ``DELETE`` (the #22 ``audit_events`` pattern). Not audited.
- ``privacy_notice_acceptances``: ``user_id`` and ``notice_version_id``
  (both ``ON DELETE RESTRICT``), ``accepted_at``, unique
  ``(user_id, notice_version_id)``. Audited by the ORM.

A version is published by a migration (owner decision #174 E2 (a)). This
revision publishes **version 1**: a short placeholder in Spanish and English
whose first line says it is a draft pending legal review. M10-07 (#155)
publishes the final wording as a new version.

Version 1's ``published_at`` is the fixed time :data:`VERSION_1_PUBLISHED_AT`
(the day the issue was groomed), not the migration's ``now()``: the current
version is chosen with the application clock (``app.core.clock``), which the
tests freeze at 2026-10-18 12:00 UTC. A ``now()`` publication would be "in the
future" for every frozen-clock test. In every real environment both are in
the past, so version 1 is current as soon as this revision runs.

Downgrading drops the trigger, ``privacy_notice_acceptances``,
``privacy_notice_versions`` and the trigger function, in that order.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERSION_1_PUBLISHED_AT = "2026-10-18 00:00:00+00"

VERSION_1_BODY_ES = (
    "Borrador pendiente de revisión legal.\n"
    "\n"
    "Classora es el sistema que el centro usa para organizar sus clases. En él"
    " se guardan los datos de las cuentas de usuario, los expedientes de"
    " alumnos y familias, los horarios y la facturación.\n"
    "\n"
    "El centro es responsable de estos datos y de su uso. Si tienes preguntas"
    " sobre tus datos, comunícate con el centro.\n"
    "\n"
    "Este texto es provisional. El centro publicará el aviso de privacidad"
    " definitivo como una nueva versión, y se te pedirá aceptarla."
)

VERSION_1_BODY_EN = (
    "Draft pending legal review.\n"
    "\n"
    "Classora is the system the center uses to organize its classes. It stores"
    " user account details, student and family records, schedules and"
    " billing.\n"
    "\n"
    "The center is responsible for this data and how it is used. If you have"
    " questions about your data, contact the center.\n"
    "\n"
    "This text is provisional. The center will publish the final privacy"
    " notice as a new version, and you will be asked to accept it."
)

IMMUTABLE_FUNCTION = """
CREATE FUNCTION privacy_notice_versions_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'privacy_notice_versions rows never change once published'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
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


def upgrade() -> None:
    versions = op.create_table(
        "privacy_notice_versions",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_es", sa.Text(), nullable=False),
        sa.Column("body_en", sa.Text(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "version > 0", name=op.f("ck_privacy_notice_versions_version_positive")
        ),
        sa.CheckConstraint(
            "body_es ~ '[^[:space:]]'",
            name=op.f("ck_privacy_notice_versions_body_es_not_blank"),
        ),
        sa.CheckConstraint(
            "body_en ~ '[^[:space:]]'",
            name=op.f("ck_privacy_notice_versions_body_en_not_blank"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_privacy_notice_versions")),
        sa.UniqueConstraint("version", name=op.f("uq_privacy_notice_versions_version")),
    )
    op.create_table(
        "privacy_notice_acceptances",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("notice_version_id", sa.UUID(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["notice_version_id"],
            ["privacy_notice_versions.id"],
            # Named explicitly: the convention's name would pass PostgreSQL's
            # 63-character limit.
            name=op.f("fk_privacy_notice_acceptances_notice_version_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_privacy_notice_acceptances_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_privacy_notice_acceptances")),
        sa.UniqueConstraint(
            "user_id",
            "notice_version_id",
            name=op.f("uq_privacy_notice_acceptances_user_id"),
        ),
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER privacy_notice_versions_immutable"
        " BEFORE UPDATE OR DELETE ON privacy_notice_versions"
        " FOR EACH ROW EXECUTE FUNCTION privacy_notice_versions_immutable()"
    )
    op.execute(
        sa.insert(versions).values(
            version=1,
            published_at=sa.text(f"TIMESTAMPTZ '{VERSION_1_PUBLISHED_AT}'"),
            body_es=VERSION_1_BODY_ES,
            body_en=VERSION_1_BODY_EN,
        )
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER privacy_notice_versions_immutable ON privacy_notice_versions"
    )
    op.drop_table("privacy_notice_acceptances")
    op.drop_table("privacy_notice_versions")
    op.execute("DROP FUNCTION privacy_notice_versions_immutable()")
