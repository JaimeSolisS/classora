"""Approval requests (#40; spec §9 "central administrative approval inbox")
and notifications (#41, :class:`Notification` below).

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
from typing import Any, Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


# --- Notifications (#41) -------------------------------------------------------------

NOTIFICATION_TYPE_MAX_LENGTH: Final = 64
NOTIFICATION_TYPE_PATTERN: Final = r"^[a-z_]+(\.[a-z_]+)+$"


class Notification(Model):
    """One in-app notification for one recipient (#41; spec §1, §9).

    Stored as ``{notification_type, params}`` (ISSUE-X1): the client renders
    the message from an i18n key, so a language switch applies to old
    notifications too. ``params`` is a flat JSON object of data (names,
    dates, codes), checked by
    :func:`app.modules.administration.notifications.create_notification`.
    ``read_at`` is ``NULL`` while unread and keeps its first value.

    Neither :class:`~app.core.audit.Audited` nor versioned: these are
    per-user messages, and the action that caused one is audited where it
    happens.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            f"notification_type ~ '{NOTIFICATION_TYPE_PATTERN}'",
            name="notification_type_format",
        ),
        CheckConstraint("jsonb_typeof(params) = 'object'", name="params_object"),
        # The list: one recipient's notifications, newest first.
        Index(
            "ix_notifications_recipient_created",
            "recipient_user_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        # The unread count.
        Index(
            "ix_notifications_recipient_unread",
            "recipient_user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    notification_type: Mapped[str] = mapped_column(String(NOTIFICATION_TYPE_MAX_LENGTH))
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
