"""Academics tables (#77 A2, A3; #78 A1, A2).

- ``session_closeouts``: one tutor closeout per class session
  (``uq_session_closeouts_class_session_id``, the race backstop): the
  submitting tutor (``tutor_user_id``, a snapshot for payroll attribution,
  M7-04), the review ``status`` (a CHECK constraint, not an enum type:
  ``submitted``, ``changes_requested``, ``approved``, #78), the structured
  summary (``topics``, ``performance``, ``homework``, ``next_steps``:
  stored trimmed, an empty field stored as ``NULL``, 1–2000 characters),
  ``submitted_at`` (the first submission), ``last_submitted_at`` (the
  latest submission, resubmission or correction request), the latest
  request-changes reason (``review_reason``, required while
  ``changes_requested``), the tutor's note on a resubmission or correction
  request (``tutor_note``), the current approval revision
  (``current_approval_id``, required while ``approved``), the reviewer
  who made the latest submission on the tutor's behalf with the reason
  (``on_behalf_by_user_id``, ``on_behalf_reason``: both or neither, never
  the tutor; #301) and a ``version``. The actual minutes live on
  ``class_sessions.actual_minutes`` (ISSUE-F2), not here.
- ``session_closeout_approvals`` (#78 E1): append-only approval revisions.
  A re-approval never edits a revision: it inserts the next one, naming the
  one it supersedes (``supersedes_approval_id``, unique: never two
  revisions superseding the same one). Each revision snapshots what payroll
  reads (M7-04): the tutor, the session start and kind, the scheduled and
  approved minutes and the per-student attendance statuses.
- ``session_closeout_corrections`` (#78 E5): append-only staff corrections
  (the fields changed, the minutes before and after, the reason, the
  revision it created when the closeout was approved).

``session_closeouts`` is audited (the create and update diffs are its
record). The two append-only tables are not: a trigger rejects ``UPDATE``,
``DELETE`` and ``TRUNCATE`` (42501), and their named events
(``academics.closeout_approved``, ``academics.closeout_corrected``) are
their audit record. Every foreign key is ``ON DELETE RESTRICT``.
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

SUMMARY_MAX_LENGTH: Final = 2000
REASON_MAX_LENGTH: Final = 1000
SUMMARY_COLUMNS: Final = ("topics", "performance", "homework", "next_steps")
CLOSEOUT_SUBMITTED: Final = "submitted"
CLOSEOUT_CHANGES_REQUESTED: Final = "changes_requested"
CLOSEOUT_APPROVED: Final = "approved"
CLOSEOUT_STATUSES: Final = (
    CLOSEOUT_SUBMITTED,
    CLOSEOUT_CHANGES_REQUESTED,
    CLOSEOUT_APPROVED,
)
CLOSEOUT_SESSION_UNIQUE: Final = "uq_session_closeouts_class_session_id"

APPROVAL_SOURCES: Final = ("review", "staff_correction")
CORRECTABLE_FIELDS: Final = ("actual_minutes", *SUMMARY_COLUMNS)
APPROVED_MINUTES_MAX: Final = 720
CURRENT_APPROVAL_FK: Final = "fk_session_closeouts_current_approval_id"
SUPERSEDES_UNIQUE: Final = "uq_session_closeout_approvals_supersedes_approval_id"


def _summary_check(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR char_length({column}) BETWEEN 1 AND {SUMMARY_MAX_LENGTH}",
        name=f"{column}_length",
    )


def _reason_check(column: str, *, nullable: bool) -> CheckConstraint:
    valid = (
        f"char_length(btrim({column})) BETWEEN 1 AND {REASON_MAX_LENGTH}"
        f" AND {column} = btrim({column})"
    )
    return CheckConstraint(
        f"{column} IS NULL OR ({valid})" if nullable else valid,
        name=f"{column}_valid",
    )


def _in(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in values))


class SessionCloseout(Audited, VersionedMixin, Model):
    """A tutor's closeout of one class session (#77 A2, #78 A1)."""

    __tablename__ = "session_closeouts"
    __table_args__ = (
        CheckConstraint(_in("status", CLOSEOUT_STATUSES), name="status_valid"),
        *(_summary_check(column) for column in SUMMARY_COLUMNS),
        _reason_check("review_reason", nullable=True),
        _reason_check("tutor_note", nullable=True),
        CheckConstraint(
            "status <> 'changes_requested' OR review_reason IS NOT NULL",
            name="changes_requested_reason",
        ),
        CheckConstraint(
            "status <> 'approved' OR current_approval_id IS NOT NULL",
            name="approved_has_approval",
        ),
        # #301 A1: the latest submission made on the tutor's behalf.
        _reason_check("on_behalf_reason", nullable=True),
        CheckConstraint(
            "(on_behalf_by_user_id IS NULL) = (on_behalf_reason IS NULL)",
            name="on_behalf_together",
        ),
        CheckConstraint(
            "on_behalf_by_user_id IS NULL OR on_behalf_by_user_id <> tutor_user_id",
            name="on_behalf_not_tutor",
        ),
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
    last_submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    tutor_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "session_closeout_approvals.id",
            ondelete="RESTRICT",
            name=CURRENT_APPROVAL_FK,
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    on_behalf_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """#301: the reviewer who made the latest submission on the tutor's
    behalf (``NULL`` when the tutor made it)."""
    on_behalf_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """#301: why (trimmed, 1–1000; with ``on_behalf_by_user_id``)."""


class SessionCloseoutApproval(Model):
    """One approval revision of a closeout (#78 E1). Append-only; revision
    1 supersedes nothing, revision ``n + 1`` supersedes revision ``n``."""

    __tablename__ = "session_closeout_approvals"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "(revision = 1) = (supersedes_approval_id IS NULL)",
            name="revision_supersedes",
        ),
        CheckConstraint(_in("source", APPROVAL_SOURCES), name="source_valid"),
        CheckConstraint(
            f"approved_minutes BETWEEN 0 AND {APPROVED_MINUTES_MAX}",
            name="approved_minutes_range",
        ),
        UniqueConstraint(
            "closeout_id", "revision", name="uq_session_closeout_approvals_revision"
        ),
        # M7-04 reads a tutor's approved closeouts by service date.
        Index(
            "ix_session_closeout_approvals_tutor_user_id_session_starts_at",
            "tutor_user_id",
            "session_starts_at",
        ),
    )

    closeout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_closeouts.id", ondelete="RESTRICT"),
    )
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "session_closeout_approvals.id",
            ondelete="RESTRICT",
            name="fk_session_closeout_approvals_supersedes_approval_id",
        ),
        nullable=True,
        unique=True,
    )
    source: Mapped[str] = mapped_column(String(20))
    class_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
    )
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    session_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_kind: Mapped[str] = mapped_column(String(20))
    scheduled_minutes: Mapped[int] = mapped_column(Integer)
    approved_minutes: Mapped[int] = mapped_column(Integer)
    attendance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """``{student_id: status}`` when approved."""
    approved_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionCloseoutCorrection(Model):
    """One staff correction of a closeout (#78 E5). Append-only."""

    __tablename__ = "session_closeout_corrections"
    __table_args__ = (
        CheckConstraint(
            "fields <@ ARRAY[{}]::text[]".format(
                ", ".join(f"'{f}'" for f in CORRECTABLE_FIELDS)
            ),
            name="fields_valid",
        ),
        CheckConstraint(
            "cardinality(fields) > 0 OR approval_id IS NOT NULL",
            name="fields_or_reapproval",
        ),
        CheckConstraint(
            "(new_minutes IS NOT NULL) = ('actual_minutes' = ANY (fields))",
            name="minutes_with_field",
        ),
        CheckConstraint(
            f"new_minutes IS NULL OR new_minutes BETWEEN 0 AND {APPROVED_MINUTES_MAX}",
            name="new_minutes_range",
        ),
        _reason_check("reason", nullable=False),
        CheckConstraint(
            _in("closeout_status", (CLOSEOUT_SUBMITTED, CLOSEOUT_APPROVED)),
            name="closeout_status_valid",
        ),
        CheckConstraint(
            "(closeout_status = 'approved') = (approval_id IS NOT NULL)",
            name="approval_when_approved",
        ),
    )

    closeout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_closeouts.id", ondelete="RESTRICT"),
        index=True,
    )
    fields: Mapped[list[str]] = mapped_column(ARRAY(Text))
    previous_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    closeout_status: Mapped[str] = mapped_column(String(20))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "session_closeout_approvals.id",
            ondelete="RESTRICT",
            name="fk_session_closeout_corrections_approval_id",
        ),
        nullable=True,
    )
    corrected_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
