"""Least-privilege runtime role ``classora_app`` for the API and the worker.

Revision ID: 0096
Revises: 0095
Create Date: 2026-11-11

M10-10 (#165; ADR-0003 amendment). Until now the API, the worker and Alembic
all connected as the database owner (a superuser locally, in CI and in the
Compose ``db``), so privileges proved nothing and the runtime could disable or
drop the append-only triggers (#22 E2). From this revision the API (through
PgBouncer and its ``LISTEN`` connection) and the worker log in as
``classora_app``: not a superuser, a member of no role, the owner of nothing.
Alembic, the seeds, the operator CLI and the local factories stay the owner
(``DATABASE_MIGRATION_URL``).

Upgrade:

- creates ``classora_app`` (``NOLOGIN``, no special attribute, ``NOINHERIT``)
  when it is missing. The role is cluster-wide, so another database of the
  same server may already have created it; a concurrent creation is
  tolerated. Running it as ``classora_app`` itself fails with 42501 before
  any change. A migrating user that may not create roles gets a clear error
  (the native shared server's ``make db-create`` creates it first).
  ``python -m app.cli sync-runtime-role`` later gives it ``LOGIN`` and its
  password, so no secret ever appears in a migration;
- in the current database: ``CONNECT``; ``USAGE`` on schema ``public``;
  ``SELECT, INSERT, UPDATE, DELETE`` on every table and ``USAGE, SELECT`` on
  every sequence in ``public`` (the Procrastinate tables included);
  ``TEMPORARY`` is revoked from ``PUBLIC``;
- exceptions: ``alembic_version`` is ``SELECT`` only, and ``audit_events``
  loses ``UPDATE``, ``DELETE`` and ``TRUNCATE`` (``SELECT, INSERT`` remain;
  #165 E2 (a)). The other trigger-guarded tables keep their DML grants and
  rely on their triggers, which ``classora_app`` cannot disable or drop
  because it owns no table. ``TRUNCATE``, ``REFERENCES`` and ``TRIGGER`` are
  never granted;
- default privileges for the migrating role in ``public``: tables it creates
  later get the same DML grants and sequences ``USAGE, SELECT``. A later
  migration that adds an append-only table must ``REVOKE UPDATE, DELETE`` on
  it itself (data-model §5); the privilege-matrix test pins every table.

Downgrade revokes everything above in this database, removes the default
privileges and gives ``TEMPORARY`` back to ``PUBLIC``. It never drops the role:
the role is cluster-wide and may hold grants in other databases of the same
server (the native shared server, the Testcontainers cluster).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0096"
down_revision: str | Sequence[str] | None = "0095"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed, not configurable; the same name as app.core.db.RUNTIME_ROLE. Written
# out in the statements below (no string building).
RUNTIME_ROLE = "classora_app"

CREATE_ROLE = """
DO $$
BEGIN
    IF current_user = 'classora_app' THEN
        RAISE EXCEPTION 'migrations run as the database owner '
            '(DATABASE_MIGRATION_URL), never as classora_app'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'classora_app') THEN
        BEGIN
            CREATE ROLE classora_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                NOREPLICATION NOBYPASSRLS NOINHERIT;
        EXCEPTION
            -- Another database of the same cluster created it concurrently.
            WHEN duplicate_object OR unique_violation THEN NULL;
            WHEN insufficient_privilege THEN
                RAISE EXCEPTION
                    'create the role classora_app first (make db-create does)'
                    USING ERRCODE = 'insufficient_privilege';
        END;
    END IF;
END
$$
"""

DATABASE_GRANTS = """
DO $$
BEGIN
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO classora_app', current_database()
    );
    EXECUTE format(
        'REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database()
    );
END
$$
"""

DATABASE_REVOKES = """
DO $$
BEGIN
    EXECUTE format(
        'REVOKE ALL ON DATABASE %I FROM classora_app', current_database()
    );
    EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO PUBLIC', current_database());
END
$$
"""

UPGRADE = (
    CREATE_ROLE,
    DATABASE_GRANTS,
    "GRANT USAGE ON SCHEMA public TO classora_app",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public"
    " TO classora_app",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO classora_app",
    # SELECT only: the seeds and readiness read the revision.
    "REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM classora_app",
    # Append-only (#22): SELECT, INSERT remain.
    "REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM classora_app",
    # Objects the migrating role creates later (FOR ROLE current_user).
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
    " GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO classora_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
    " GRANT USAGE, SELECT ON SEQUENCES TO classora_app",
)

DOWNGRADE = (
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM classora_app",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
    " REVOKE ALL ON SEQUENCES FROM classora_app",
    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM classora_app",
    "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM classora_app",
    "REVOKE ALL ON SCHEMA public FROM classora_app",
    DATABASE_REVOKES,
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
