"""Procrastinate 3.10.0 schema (job queue tables, types, functions, triggers).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17

Alembic owns Procrastinate's schema (ADR-0003, decision (c)): the worker and
the API never apply or change it. This revision applies the full schema of
Procrastinate :data:`PROCRASTINATE_VERSION` from the copy committed next to the
migrations (``migrations/procrastinate/<version>/schema.sql``, byte-for-byte
the package's ``procrastinate/sql/schema.sql``). It never reads the installed
package, so a later upgrade cannot change what this revision creates.

Upgrading Procrastinate: add a new revision that applies that version's
shipped migration SQL (``procrastinate/sql/migrations/``) and sets its own
``PROCRASTINATE_VERSION``. ``tests/unit/test_procrastinate_schema.py`` fails
until that revision exists.

The schema needs no extension beyond ``plpgsql``, which PostgreSQL always has.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROCRASTINATE_VERSION = "3.10.0"

SCHEMA_SQL = (
    Path(__file__).resolve().parents[1]
    / "procrastinate"
    / PROCRASTINATE_VERSION
    / "schema.sql"
)

# Every object the 3.10.0 schema creates, dropped explicitly (no CASCADE), in
# dependency order. Indexes, sequences and the tables' row types go with their
# tables.
DOWNGRADE_SQL = """
DROP TRIGGER procrastinate_jobs_notify_queue_job_inserted_v1 ON procrastinate_jobs;
DROP TRIGGER procrastinate_jobs_notify_queue_job_aborted_v1 ON procrastinate_jobs;
DROP TRIGGER procrastinate_trigger_status_events_update_v1 ON procrastinate_jobs;
DROP TRIGGER procrastinate_trigger_status_events_insert_v1 ON procrastinate_jobs;
DROP TRIGGER procrastinate_trigger_scheduled_events_v1 ON procrastinate_jobs;
DROP TRIGGER procrastinate_trigger_abort_requested_events_v1 ON procrastinate_jobs;
DROP TRIGGER procrastinate_trigger_delete_jobs_v1 ON procrastinate_jobs;

DROP FUNCTION procrastinate_defer_jobs_v1(procrastinate_job_to_defer_v1[]);
DROP FUNCTION procrastinate_defer_periodic_job_v2(
    character varying, character varying, character varying, character varying,
    integer, character varying, bigint, jsonb
);
DROP FUNCTION procrastinate_fetch_job_v2(character varying[], bigint);
DROP FUNCTION procrastinate_finish_job_v1(bigint, procrastinate_job_status, boolean);
DROP FUNCTION procrastinate_cancel_job_v1(bigint, boolean, boolean);
DROP FUNCTION procrastinate_retry_job_v1(
    bigint, timestamp with time zone, integer, character varying, character varying
);
DROP FUNCTION procrastinate_retry_job_v2(
    bigint, timestamp with time zone, integer, character varying, character varying
);
DROP FUNCTION procrastinate_notify_queue_job_inserted_v1();
DROP FUNCTION procrastinate_notify_queue_abort_job_v1();
DROP FUNCTION procrastinate_trigger_function_status_events_insert_v1();
DROP FUNCTION procrastinate_trigger_function_status_events_update_v1();
DROP FUNCTION procrastinate_trigger_function_scheduled_events_v1();
DROP FUNCTION procrastinate_trigger_abort_requested_events_procedure_v1();
DROP FUNCTION procrastinate_unlink_periodic_defers_v1();
DROP FUNCTION procrastinate_register_worker_v1();
DROP FUNCTION procrastinate_unregister_worker_v1(bigint);
DROP FUNCTION procrastinate_update_heartbeat_v1(bigint);
DROP FUNCTION procrastinate_prune_stalled_workers_v1(double precision);

DROP TABLE procrastinate_events;
DROP TABLE procrastinate_periodic_defers;
DROP TABLE procrastinate_jobs;
DROP TABLE procrastinate_workers;

DROP TYPE procrastinate_job_to_defer_v1;
DROP TYPE procrastinate_job_event_type;
DROP TYPE procrastinate_job_status;
"""


def _execute_script(sql: str) -> None:
    """Run a multi-statement SQL script inside Alembic's transaction.

    The SQLAlchemy asyncpg adapter prepares every statement, and PostgreSQL
    cannot prepare several commands at once, so the script goes to the
    driver connection's simple-query ``execute`` instead. Alembic has already
    run its own queries on this connection, so the script joins the
    migration's transaction.
    """
    driver_connection = op.get_bind().connection.driver_connection
    # Type narrowing only: an open Alembic connection always has a driver connection.
    assert driver_connection is not None  # noqa: S101
    await_only(driver_connection.execute(sql))


def upgrade() -> None:
    _execute_script(SCHEMA_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    _execute_script(DOWNGRADE_SQL)
