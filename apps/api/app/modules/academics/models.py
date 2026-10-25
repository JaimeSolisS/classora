"""Academics tables (#77 A2, A3).

- ``session_closeouts``: one tutor closeout per class session
  (``uq_session_closeouts_class_session_id``, the race backstop): the
  submitting tutor (``tutor_user_id``, a snapshot for payroll attribution,
  M7-04), the review ``status`` (a CHECK constraint, not an enum type, so
  #78 widens it with ``changes_requested`` / ``approved``), the structured
  summary (``topics``, ``performance``, ``homework``, ``next_steps``:
  stored trimmed, an empty field stored as ``NULL``, 1–2000 characters),
  ``submitted_at`` and a ``version``. The actual minutes live on
  ``class_sessions.actual_minutes`` (ISSUE-F2), not here.

Audited: the create and update diffs are the closeout's audit record (no
named event). Both foreign keys are ``ON DELETE RESTRICT``.
"""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

SUMMARY_MAX_LENGTH: Final = 2000
SUMMARY_COLUMNS: Final = ("topics", "performance", "homework", "next_steps")
CLOSEOUT_SUBMITTED: Final = "submitted"
CLOSEOUT_STATUSES: Final = (CLOSEOUT_SUBMITTED,)
"""#78 adds ``changes_requested`` and ``approved``."""
CLOSEOUT_SESSION_UNIQUE: Final = "uq_session_closeouts_class_session_id"


def _summary_check(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR char_length({column}) BETWEEN 1 AND {SUMMARY_MAX_LENGTH}",
        name=f"{column}_length",
    )


class SessionCloseout(Audited, VersionedMixin, Model):
    """A tutor's closeout of one class session (#77 A2)."""

    __tablename__ = "session_closeouts"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in CLOSEOUT_STATUSES)),
            name="status_valid",
        ),
        *(_summary_check(column) for column in SUMMARY_COLUMNS),
        # #78 and M7 list a tutor's closeouts by submission time.
        Index(
            "ix_session_closeouts_tutor_user_id_submitted_at",
            "tutor_user_id",
            "submitted_at",
        ),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        unique=True,
    )
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(20))
    topics: Mapped[str | None] = mapped_column(Text, nullable=True)
    performance: Mapped[str | None] = mapped_column(Text, nullable=True)
    homework: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
