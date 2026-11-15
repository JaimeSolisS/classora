"""The ``job_failures`` table (#128 B1; architecture ISSUE-J3, §6).

Procrastinate 3.10 stores no error text: ``procrastinate_jobs`` has a status
and an attempt count, and ``procrastinate_events`` only event types and
times. The worker middleware :func:`app.core.jobs.record_job_failure` writes
one row here per failed job (the latest failure wins), so the failed jobs
page (``GET /api/v1/jobs``) can show a **safe** error label.

A row holds only the exception class name, an application error code (an
:class:`app.core.errors.AppError` code) and the database SQLSTATE
(:func:`app.core.jobs.sanitize_job_error`). Never the message, the
traceback or the job arguments: driver messages quote key values (#128 E1,
A2). The traceback stays in the worker logs, keyed by the job id.

``job_id`` is the primary key and a foreign key onto Procrastinate's
``procrastinate_jobs(id)`` ``ON DELETE CASCADE``, so deleting old jobs (for
example Procrastinate's ``builtin:remove_old_jobs``, M10-04/#129) deletes
their rows too. Procrastinate's tables are not models (Alembic creates them
from committed SQL, ADR-0003), so the foreign key points at a one-column
stand-in table kept outside ``Base.metadata``; autogenerate never sees it.

Core infrastructure: not Audited, no version, no ``created_at``/``updated_at``
(``failed_at`` is the only time). No personal data.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base

ERROR_CLASS_MAX_LENGTH: Final = 200
ERROR_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"
SQLSTATE_PATTERN: Final = r"^[0-9A-Z]{5}$"
TASK_NAME_MAX_LENGTH: Final = 128

# Procrastinate's job table, as far as the foreign key needs it. It lives in
# its own MetaData so Alembic's autogenerate and the metadata check never
# compare it (Procrastinate's revisions own it).
procrastinate_jobs: Final = Table(
    "procrastinate_jobs",
    MetaData(),
    Column("id", BigInteger, primary_key=True),
)


class JobFailure(Base):
    """The latest failure of one Procrastinate job (sanitized)."""

    __tablename__ = "job_failures"
    __table_args__ = (
        CheckConstraint(
            f"char_length(error_class) <= {ERROR_CLASS_MAX_LENGTH}",
            name="error_class_length",
        ),
        CheckConstraint(
            f"error_code ~ '{ERROR_CODE_PATTERN}'", name="error_code_format"
        ),
        CheckConstraint(f"sqlstate ~ '{SQLSTATE_PATTERN}'", name="sqlstate_format"),
        # The list's order (newest failure first, ties by job id) and the
        # task filter (#128 D1).
        Index(
            "ix_job_failures_failed_at_job_id",
            text("failed_at DESC"),
            text("job_id DESC"),
        ),
        Index(
            "ix_job_failures_task_name_failed_at",
            "task_name",
            text("failed_at DESC"),
        ),
    )

    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(procrastinate_jobs.c.id, ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    task_name: Mapped[str] = mapped_column(String(TASK_NAME_MAX_LENGTH))
    error_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    sqlstate: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
