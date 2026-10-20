"""Approval requests (#40; spec §9 "central administrative approval inbox").

``approval_requests``: one row per item that waits for a reviewer, whatever
module raised it. ``request_type`` is a dotted code that a module registers
in code with a handler (:mod:`app.modules.administration.approvals`), never a
database enum; a CHECK guards its format. ``subject_id`` is the record in the
owning module (no foreign key: subjects live in different tables), and
``(request_type, subject_id)`` is unique: one approval request per subject. A
resubmission after ``changes_requested`` reuses the request.

- ``requester_user_id`` is ``NULL`` for system-raised items (for example
  expired offers, M4-07); ``assignee_user_id`` is ``NULL`` while unassigned.
- ``status`` (``pending``, ``approved``, ``rejected``, ``changes_requested``,
  ``cancelled``): ``resolved_at`` is set exactly for the three final states,
  and ``resolved_by_user_id`` may stay ``NULL`` only for ``cancelled`` (a
  system cancel). ``changes_requested`` always carries a ``reason``.
- ``priority``: ``low``, ``normal`` (default) or ``high``.

Versioned (each transition increments ``version``) and
:class:`~app.core.audit.Audited` with nothing excluded or masked: the row
holds ids, codes and the reviewer's reason, never a secret.
"""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

PENDING: Final = "pending"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"
CHANGES_REQUESTED: Final = "changes_requested"
CANCELLED: Final = "cancelled"

APPROVAL_STATUSES: Final = (PENDING, APPROVED, REJECTED, CHANGES_REQUESTED, CANCELLED)
FINAL_STATUSES: Final = frozenset({APPROVED, REJECTED, CANCELLED})

LOW: Final = "low"
NORMAL: Final = "normal"
HIGH: Final = "high"

APPROVAL_PRIORITIES: Final = (LOW, NORMAL, HIGH)

REQUEST_TYPE_MAX_LENGTH: Final = 64
REQUEST_TYPE_PATTERN: Final = r"^[a-z_]+(\.[a-z_]+)+$"
REASON_MAX_LENGTH: Final = 1000


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class ApprovalRequest(Audited, VersionedMixin, Model):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            f"request_type ~ '{REQUEST_TYPE_PATTERN}'", name="request_type_format"
        ),
        CheckConstraint(
            f"status IN ({_quoted(APPROVAL_STATUSES)})", name="status_valid"
        ),
        CheckConstraint(
            f"priority IN ({_quoted(APPROVAL_PRIORITIES)})", name="priority_valid"
        ),
        CheckConstraint(
            "(status IN ('approved', 'rejected', 'cancelled'))"
            " = (resolved_at IS NOT NULL)",
            name="resolved_iff_final",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR resolved_by_user_id IS NOT NULL",
            name="resolver_unless_cancelled",
        ),
        CheckConstraint(
            "status <> 'changes_requested' OR reason IS NOT NULL",
            name="reason_for_changes",
        ),
        UniqueConstraint("request_type", "subject_id"),
        # The inbox: by status and priority, oldest first.
        Index(
            "ix_approval_requests_status_priority_created_at_id",
            "status",
            "priority",
            "created_at",
            "id",
        ),
        Index(
            "ix_approval_requests_assignee_user_id_status",
            "assignee_user_id",
            "status",
        ),
        Index("ix_approval_requests_requester_user_id", "requester_user_id"),
    )

    request_type: Mapped[str] = mapped_column(String(REQUEST_TYPE_MAX_LENGTH))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    priority: Mapped[str] = mapped_column(String(10), server_default=text("'normal'"))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(REASON_MAX_LENGTH), nullable=True)
