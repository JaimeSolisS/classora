"""Reporting: ``export_jobs`` and ``export_files``.

Revision ID: 0090
Revises: 0089
Create Date: 2026-11-05

M8-07 (#124 F1; spec §8 exports; architecture §6, §7.3). A worker builds the
export in the background; the finished file is held in PostgreSQL for 24
hours, downloadable by its requester only, then deleted (#124 E1: no object
storage, no disk file).

- ``export_jobs`` (audited): ``requested_by_user_id`` (``users``, ``ON DELETE
  RESTRICT``), ``dataset VARCHAR(32)`` (CHECK ``^[a-z_]{3,32}$``, so a new
  dataset needs no migration), ``format VARCHAR(8)`` (CHECK ``csv``,
  ``xlsx``), ``status VARCHAR(16)`` (default ``queued``; CHECK ``queued``,
  ``ready``, ``failed``, ``expired``), ``parameters JSONB`` (an object; ids
  and codes only), ``language VARCHAR(8)`` (CHECK ``es``, ``en``),
  ``row_count`` and ``byte_size`` (``INTEGER``, nullable, CHECK ``>= 0``),
  ``file_name VARCHAR(120)`` (not blank), ``error_code VARCHAR(32)`` (set
  exactly when ``failed``; CHECK ``row_limit_exceeded``,
  ``size_limit_exceeded``, ``permission_lost``, ``error``), ``finished_at``
  and ``expires_at`` (required for ``ready`` and ``expired``). Indexes:
  ``(requested_by_user_id, created_at DESC)`` (the list), ``created_at``
  where ``queued`` (the task) and ``expires_at`` where ``ready`` (the purge).
- ``export_files`` (not audited): ``job_id`` primary key referencing
  ``export_jobs`` ``ON DELETE CASCADE``, ``content BYTEA``.

No rows are inserted. Downgrading drops both tables (with any file still
held).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0090"
down_revision: str | Sequence[str] | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOBS: Final = "export_jobs"
FILES: Final = "export_files"


def _timestamp(name: str) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        JOBS,
        sa.Column("requested_by_user_id", sa.UUID(), nullable=False),
        sa.Column("dataset", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(length=120), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "dataset ~ '^[a-z_]{3,32}$'", name=op.f(f"ck_{JOBS}_dataset_format")
        ),
        sa.CheckConstraint(
            "format IN ('csv', 'xlsx')", name=op.f(f"ck_{JOBS}_format_valid")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'ready', 'failed', 'expired')",
            name=op.f(f"ck_{JOBS}_status_valid"),
        ),
        sa.CheckConstraint(
            "language IN ('es', 'en')", name=op.f(f"ck_{JOBS}_language_valid")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'",
            name=op.f(f"ck_{JOBS}_parameters_object"),
        ),
        sa.CheckConstraint(
            "row_count >= 0", name=op.f(f"ck_{JOBS}_row_count_not_negative")
        ),
        sa.CheckConstraint(
            "byte_size >= 0", name=op.f(f"ck_{JOBS}_byte_size_not_negative")
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('row_limit_exceeded',"
            " 'size_limit_exceeded', 'permission_lost', 'error')",
            name=op.f(f"ck_{JOBS}_error_code_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name=op.f(f"ck_{JOBS}_error_code_iff_failed"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('ready', 'expired')"
            " OR (finished_at IS NOT NULL AND expires_at IS NOT NULL)",
            name=op.f(f"ck_{JOBS}_finished_when_ready"),
        ),
        sa.CheckConstraint(
            "file_name ~ '[^[:space:]]'", name=op.f(f"ck_{JOBS}_file_name_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{JOBS}_requested_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{JOBS}")),
    )
    op.create_index(
        "ix_export_jobs_requested_by_user_id_created_at",
        JOBS,
        ["requested_by_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_export_jobs_queued",
        JOBS,
        ["created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_export_jobs_ready_expires_at",
        JOBS,
        ["expires_at"],
        postgresql_where=sa.text("status = 'ready'"),
    )
    op.create_table(
        FILES,
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            [f"{JOBS}.id"],
            name=op.f(f"fk_{FILES}_job_id_{JOBS}"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f(f"pk_{FILES}")),
    )


def downgrade() -> None:
    op.drop_table(FILES)
    op.drop_index("ix_export_jobs_ready_expires_at", table_name=JOBS)
    op.drop_index("ix_export_jobs_queued", table_name=JOBS)
    op.drop_index("ix_export_jobs_requested_by_user_id_created_at", table_name=JOBS)
    op.drop_table(JOBS)
