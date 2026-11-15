"""Export jobs and their files (#124 F1, F2; migration 0090).

``export_jobs``: one row per requested export. ``dataset`` is a registered
dataset's code (no database enum; a CHECK guards its format, so a new dataset
needs no migration). ``status`` moves ``queued → ready | failed`` and ``ready
→ expired`` (#124 E8: there is no ``running`` status). Audited: the row holds
ids, codes, counts and the file name, never a value of the file
(``parameters`` are ids and codes only, E11). Not versioned: only the worker
and the purge change it, under ``FOR UPDATE``.

``export_files``: the finished file's bytes, one row per ``ready`` job
(``job_id`` primary key, ``ON DELETE CASCADE``), held for 24 hours and then
deleted by the purge (#124 E1). **Not** :class:`~app.core.audit.Audited`:
the bytes are personal data that never reach the audit trail (E11).
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
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Base, Model

DATASET_CODE_PATTERN: Final = r"^[a-z_]{3,32}$"
"""A dataset's code (the registry checks it too)."""

QUEUED: Final = "queued"
READY: Final = "ready"
FAILED: Final = "failed"
EXPIRED: Final = "expired"
JOB_STATUSES: Final = (QUEUED, READY, FAILED, EXPIRED)

CSV: Final = "csv"
XLSX: Final = "xlsx"
FORMATS: Final = (CSV, XLSX)

ROW_LIMIT_EXCEEDED: Final = "row_limit_exceeded"
SIZE_LIMIT_EXCEEDED: Final = "size_limit_exceeded"
PERMISSION_LOST: Final = "permission_lost"
ERROR: Final = "error"
ERROR_CODES: Final = (ROW_LIMIT_EXCEEDED, SIZE_LIMIT_EXCEEDED, PERMISSION_LOST, ERROR)

LANGUAGE_CODES: Final = ("es", "en")
FILE_NAME_MAX_LENGTH: Final = 120
DATASET_MAX_LENGTH: Final = 32


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class ExportJob(Audited, Model):
    __tablename__ = "export_jobs"
    __table_args__ = (
        CheckConstraint(f"dataset ~ '{DATASET_CODE_PATTERN}'", name="dataset_format"),
        CheckConstraint(f"format IN ({_quoted(FORMATS)})", name="format_valid"),
        CheckConstraint(f"status IN ({_quoted(JOB_STATUSES)})", name="status_valid"),
        CheckConstraint(
            f"language IN ({_quoted(LANGUAGE_CODES)})", name="language_valid"
        ),
        CheckConstraint(
            "jsonb_typeof(parameters) = 'object'", name="parameters_object"
        ),
        CheckConstraint("row_count >= 0", name="row_count_not_negative"),
        CheckConstraint("byte_size >= 0", name="byte_size_not_negative"),
        CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_quoted(ERROR_CODES)})",
            name="error_code_valid",
        ),
        CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name="error_code_iff_failed",
        ),
        CheckConstraint(
            "status NOT IN ('ready', 'expired')"
            " OR (finished_at IS NOT NULL AND expires_at IS NOT NULL)",
            name="finished_when_ready",
        ),
        CheckConstraint("file_name ~ '[^[:space:]]'", name="file_name_not_blank"),
        # The list: the requester's jobs, newest first.
        Index(
            "ix_export_jobs_requested_by_user_id_created_at",
            "requested_by_user_id",
            text("created_at DESC"),
        ),
        # The task: the queued jobs to build.
        Index(
            "ix_export_jobs_queued",
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
        # The purge: the ready jobs by expiry.
        Index(
            "ix_export_jobs_ready_expires_at",
            "expires_at",
            postgresql_where=text("status = 'ready'"),
        ),
    )

    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    dataset: Mapped[str] = mapped_column(String(DATASET_MAX_LENGTH))
    format: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'queued'"))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    language: Mapped[str] = mapped_column(String(8))
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_name: Mapped[str] = mapped_column(String(FILE_NAME_MAX_LENGTH))
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExportFile(Base):
    __tablename__ = "export_files"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("export_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    content: Mapped[bytes] = mapped_column(LargeBinary)
