"""Audit events: the append-only ``audit_events`` table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18

M2-04 (#22; architecture ISSUE-A3, accepted). One row per audit event, written
by the application in the same transaction as the change it records
(``app.core.audit``). ``actor_user_id`` refers to ``users`` with ``ON DELETE
RESTRICT``: a user who acted keeps their audit references. Three indexes serve
the query shapes of ``GET /api/v1/audit-events`` (by record, by actor, whole
log), each ending in ``(occurred_at, id)`` for keyset pagination.

Append-only is enforced with triggers, not only with privileges: the local,
test and CI database role is a PostgreSQL superuser (the ``postgres`` image
creates ``POSTGRES_USER`` as one, and so does Testcontainers), and privilege
checks do not apply to a superuser, so ``REVOKE UPDATE, DELETE`` would prove
nothing. The triggers fire for every role, on the direct and the PgBouncer
path, and raise SQLSTATE 42501 (insufficient_privilege). A superuser can still
``ALTER TABLE ... DISABLE TRIGGER``; follow-up issue #165 adds a least-privilege
runtime role (API and worker log in as a non-owner, non-superuser role; Alembic
stays the owner) so that a ``REVOKE`` applies as well. Retention (M10-07) must
design a controlled path around these triggers.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_FUNCTION = """
CREATE FUNCTION audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only'
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("record_type", sa.Text(), nullable=True),
        sa.Column("record_id", sa.UUID(), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'delete', 'soft_delete', 'restore')"
            " OR action ~ '^[a-z_]+(\\.[a-z_]+)+$'",
            name=op.f("ck_audit_events_action_valid"),
        ),
        sa.CheckConstraint(
            "(record_type IS NULL) = (record_id IS NULL)",
            name=op.f("ck_audit_events_record_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_record",
        "audit_events",
        ["record_type", "record_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_actor",
        "audit_events",
        ["actor_user_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_occurred_at",
        "audit_events",
        ["occurred_at", "id"],
        unique=False,
    )
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_events_no_update_delete"
        " BEFORE UPDATE OR DELETE ON audit_events"
        " FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate"
        " BEFORE TRUNCATE ON audit_events"
        " FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER audit_events_no_update_delete ON audit_events")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_record", table_name="audit_events")
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION audit_events_append_only()")
