"""Administration: ``import_jobs`` and ``import_rows``.

Revision ID: 0088
Revises: 0087
Create Date: 2026-11-05

M8-05 (#122 F1; spec §9 imports; architecture ISSUE-L1). The CSV text is
parsed in the request and never stored (#122 E1); these tables hold the job
and its parsed rows while they are reviewed and committed.

- ``import_jobs`` (audited, versioned): ``kind VARCHAR(32)`` (CHECK
  ``^[a-z_]{3,32}$``), ``status VARCHAR(20)`` (CHECK ``staged``,
  ``committing``, ``committed``, ``failed``, ``discarded``, ``expired``),
  ``file_name VARCHAR(255)`` (nullable; trimmed, not blank),
  ``ignored_columns TEXT[]`` (default empty), seven row counts
  (``total_rows``, ``ready_rows``, ``duplicate_rows``, ``error_rows``,
  ``created_rows``, ``skipped_rows``, ``failed_rows``; ``INTEGER``, default
  0, CHECK ``>= 0``), ``created_by_user_id`` and ``committed_by_user_id``
  (``users``, ``ON DELETE RESTRICT``; the committer is required from
  ``committing`` on), ``committed_at`` (set exactly when ``committed``).
  Indexes: ``(kind, created_at DESC)`` (the list) and ``status`` where it is
  ``staged``, ``committing`` or ``failed`` (the task).
- ``import_rows`` (not audited, #122 E9): primary key ``(job_id,
  row_number)``, ``job_id`` references ``import_jobs`` ``ON DELETE
  CASCADE``; ``status VARCHAR(16)`` (CHECK ``ready``, ``duplicate``,
  ``error``, ``created``, ``skipped``, ``failed``); ``values JSONB`` (an
  object, or ``NULL`` once the job is committed); ``errors`` and ``warnings``
  (``JSONB`` arrays, default empty); ``record_id UUID`` (set exactly when
  ``created``).

No rows are inserted. Downgrading drops both tables (open imports with them;
the records an import created stay).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0088"
down_revision: str | Sequence[str] | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOBS: Final = "import_jobs"
ROWS: Final = "import_rows"
JOB_STATUSES: Final = (
    "staged",
    "committing",
    "committed",
    "failed",
    "discarded",
    "expired",
)
ROW_STATUSES: Final = ("ready", "duplicate", "error", "created", "skipped", "failed")
COUNTS: Final = (
    "total_rows",
    "ready_rows",
    "duplicate_rows",
    "error_rows",
    "created_rows",
    "skipped_rows",
    "failed_rows",
)


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


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
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'staged'"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column(
            "ignored_columns",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        *(
            sa.Column(name, sa.Integer(), server_default=sa.text("0"), nullable=False)
            for name in COUNTS
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("committed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "kind ~ '^[a-z_]{3,32}$'", name=op.f(f"ck_{JOBS}_kind_format")
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(JOB_STATUSES)})", name=op.f(f"ck_{JOBS}_status_valid")
        ),
        sa.CheckConstraint(
            "file_name = btrim(file_name)", name=op.f(f"ck_{JOBS}_file_name_trimmed")
        ),
        sa.CheckConstraint(
            "file_name ~ '[^[:space:]]'", name=op.f(f"ck_{JOBS}_file_name_not_blank")
        ),
        *(
            sa.CheckConstraint(
                f"{name} >= 0", name=op.f(f"ck_{JOBS}_{name}_not_negative")
            )
            for name in COUNTS
        ),
        sa.CheckConstraint(
            "(status = 'committed') = (committed_at IS NOT NULL)",
            name=op.f(f"ck_{JOBS}_committed_at_iff_committed"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('committing', 'committed', 'failed')"
            " OR committed_by_user_id IS NOT NULL",
            name=op.f(f"ck_{JOBS}_committer_when_committing"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{JOBS}_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["committed_by_user_id"],
            ["users.id"],
            name=op.f(f"fk_{JOBS}_committed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{JOBS}")),
    )
    op.create_index(
        "ix_import_jobs_kind_created_at",
        JOBS,
        ["kind", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_import_jobs_open_status",
        JOBS,
        ["status"],
        postgresql_where=sa.text("status IN ('staged', 'committing', 'failed')"),
    )
    op.create_table(
        ROWS,
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=True),
        sa.Column(
            "errors",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("record_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "row_number >= 1", name=op.f(f"ck_{ROWS}_row_number_positive")
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(ROW_STATUSES)})", name=op.f(f"ck_{ROWS}_status_valid")
        ),
        sa.CheckConstraint(
            '"values" IS NULL OR jsonb_typeof("values") = \'object\'',
            name=op.f(f"ck_{ROWS}_values_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(errors) = 'array'", name=op.f(f"ck_{ROWS}_errors_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(warnings) = 'array'", name=op.f(f"ck_{ROWS}_warnings_array")
        ),
        sa.CheckConstraint(
            "(status = 'created') = (record_id IS NOT NULL)",
            name=op.f(f"ck_{ROWS}_record_iff_created"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            [f"{JOBS}.id"],
            name=op.f(f"fk_{ROWS}_job_id_{JOBS}"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", "row_number", name=op.f(f"pk_{ROWS}")),
    )


def downgrade() -> None:
    op.drop_table(ROWS)
    op.drop_index("ix_import_jobs_open_status", table_name=JOBS)
    op.drop_index("ix_import_jobs_kind_created_at", table_name=JOBS)
    op.drop_table(JOBS)
