"""Core: ``job_failures``.

Revision ID: 0093
Revises: 0092
Create Date: 2026-11-06

M8-11 (#128 B1, B2; architecture ISSUE-J3, §6). The sanitized last error of
each failed background job, written by the worker middleware
``app.core.jobs.record_job_failure`` (Procrastinate stores no error text).

- ``job_failures`` (core infrastructure: not audited, not versioned):
  ``job_id bigint`` primary key and foreign key onto
  ``procrastinate_jobs(id)`` ``ON DELETE CASCADE``; ``task_name
  varchar(128)``; ``error_class`` (at most 200 characters), ``error_code``
  (an application code, ``^[a-z][a-z0-9_]{0,63}$``) and ``sqlstate``
  (``^[0-9A-Z]{5}$``), all nullable; ``failed_at`` (default ``now()``).
  Indexes ``(failed_at DESC, job_id DESC)`` and ``(task_name, failed_at
  DESC)``. Never a message, a traceback or job arguments.

The upgrade backfills one row per existing ``failed`` job, with an unknown
error (``error_class`` ``NULL``) and ``failed_at`` = the job's latest
``failed`` event, or ``now()`` when it has none. Downgrading drops the table
(the jobs themselves are untouched).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0093"
down_revision: str | Sequence[str] | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FAILURES: Final = "job_failures"

BACKFILL: Final = """
INSERT INTO job_failures (job_id, task_name, failed_at)
SELECT j.id,
       j.task_name,
       coalesce(
           (SELECT max(e.at) FROM procrastinate_events e
            WHERE e.job_id = j.id AND e.type = 'failed'),
           now()
       )
FROM procrastinate_jobs j
WHERE j.status = 'failed'
"""


def upgrade() -> None:
    op.create_table(
        FAILURES,
        sa.Column("job_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("sqlstate", sa.Text(), nullable=True),
        sa.Column(
            "failed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(error_class) <= 200",
            name=op.f(f"ck_{FAILURES}_error_class_length"),
        ),
        sa.CheckConstraint(
            "error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name=op.f(f"ck_{FAILURES}_error_code_format"),
        ),
        sa.CheckConstraint(
            "sqlstate ~ '^[0-9A-Z]{5}$'",
            name=op.f(f"ck_{FAILURES}_sqlstate_format"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["procrastinate_jobs.id"],
            name=op.f(f"fk_{FAILURES}_job_id_procrastinate_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f(f"pk_{FAILURES}")),
    )
    op.create_index(
        "ix_job_failures_failed_at_job_id",
        FAILURES,
        [sa.text("failed_at DESC"), sa.text("job_id DESC")],
    )
    op.create_index(
        "ix_job_failures_task_name_failed_at",
        FAILURES,
        ["task_name", sa.text("failed_at DESC")],
    )
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_table(FAILURES)
