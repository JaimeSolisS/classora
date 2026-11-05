"""Import jobs and their staged rows (#122 F1, F2; migration 0088).

``import_jobs``: one row per uploaded CSV. ``kind`` is a registered import
kind's code (no database enum; a CHECK guards its format). ``status`` moves
``staged → committing → committed``, ``committing → failed → committing``
(a retry), ``staged | failed → discarded | expired``. The counts are a
snapshot of the rows' statuses, refreshed by every change. Audited and
versioned: the row holds ids, codes, counts, the file name and the ignored
header names, never a staged value.

``import_rows``: the parsed values of one data row while the job is open
(#122 E9): ``values`` is a ``{column: text}`` object until the job is
committed (then ``NULL``); discarding or expiring a job deletes its rows.
Primary key ``(job_id, row_number)``, ``ON DELETE CASCADE``. **Not**
:class:`~app.core.audit.Audited` (personal data that is purged; the records
an import creates are audited as usual) and not versioned (only the job is
locked).
"""

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Base, Model, VersionedMixin

KIND_CODE_PATTERN: Final = r"^[a-z_]{3,32}$"
"""An import kind's code (the registry checks it too)."""

STAGED: Final = "staged"
COMMITTING: Final = "committing"
COMMITTED: Final = "committed"
FAILED: Final = "failed"
DISCARDED: Final = "discarded"
EXPIRED: Final = "expired"

JOB_STATUSES: Final = (STAGED, COMMITTING, COMMITTED, FAILED, DISCARDED, EXPIRED)
OPEN_STATUSES: Final = (STAGED, COMMITTING, FAILED)
"""The statuses with values on their rows (and the partial index)."""
EXPIRABLE_STATUSES: Final = (STAGED, FAILED)

READY: Final = "ready"
DUPLICATE: Final = "duplicate"
ERROR: Final = "error"
CREATED: Final = "created"
SKIPPED: Final = "skipped"
ROW_FAILED: Final = "failed"

ROW_STATUSES: Final = (READY, DUPLICATE, ERROR, CREATED, SKIPPED, ROW_FAILED)

FILE_NAME_MAX_LENGTH: Final = 255
KIND_MAX_LENGTH: Final = 32

COUNT_COLUMNS: Final = (
    "total_rows",
    "ready_rows",
    "duplicate_rows",
    "error_rows",
    "created_rows",
    "skipped_rows",
    "failed_rows",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class ImportJob(Audited, VersionedMixin, Model):
    __tablename__ = "import_jobs"
    __table_args__ = (
        CheckConstraint(f"kind ~ '{KIND_CODE_PATTERN}'", name="kind_format"),
        CheckConstraint(f"status IN ({_quoted(JOB_STATUSES)})", name="status_valid"),
        CheckConstraint("file_name = btrim(file_name)", name="file_name_trimmed"),
        CheckConstraint("file_name ~ '[^[:space:]]'", name="file_name_not_blank"),
        *(
            CheckConstraint(f"{column} >= 0", name=f"{column}_not_negative")
            for column in COUNT_COLUMNS
        ),
        CheckConstraint(
            "(status = 'committed') = (committed_at IS NOT NULL)",
            name="committed_at_iff_committed",
        ),
        CheckConstraint(
            "status NOT IN ('committing', 'committed', 'failed')"
            " OR committed_by_user_id IS NOT NULL",
            name="committer_when_committing",
        ),
        # The list: one kind's jobs, newest first.
        Index("ix_import_jobs_kind_created_at", "kind", text("created_at DESC")),
        # The task: open jobs to commit or expire.
        Index(
            "ix_import_jobs_open_status",
            "status",
            postgresql_where=text("status IN ('staged', 'committing', 'failed')"),
        ),
    )

    kind: Mapped[str] = mapped_column(String(KIND_MAX_LENGTH))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'staged'"))
    file_name: Mapped[str | None] = mapped_column(
        String(FILE_NAME_MAX_LENGTH), nullable=True
    )
    ignored_columns: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    total_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    ready_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duplicate_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skipped_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    committed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        CheckConstraint("row_number >= 1", name="row_number_positive"),
        CheckConstraint(f"status IN ({_quoted(ROW_STATUSES)})", name="status_valid"),
        CheckConstraint(
            '"values" IS NULL OR jsonb_typeof("values") = \'object\'',
            name="values_object",
        ),
        CheckConstraint("jsonb_typeof(errors) = 'array'", name="errors_array"),
        CheckConstraint("jsonb_typeof(warnings) = 'array'", name="warnings_array"),
        CheckConstraint(
            "(status = 'created') = (record_id IS NOT NULL)",
            name="record_iff_created",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("import_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    row_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    values: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    errors: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    warnings: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
