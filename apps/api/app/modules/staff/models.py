"""Staff tables (#43; spec §4).

- ``tutor_profiles``: the professional experience of one tutor, at most one
  row per user (``uq_tutor_profiles_user_id``). The row is created lazily by
  the first profile edit (#43 A3): a tutor without one has an empty profile
  at ``version`` 0. Losing the ``tutor`` role hides the profile but keeps the
  row. ``Audited`` with nothing excluded, versioned.
- ``tutor_qualifications`` (#44): a tutor's request to teach one subject (the
  level follows from the subject, #32). ``status`` is ``pending`` →
  ``approved`` | ``rejected`` | ``cancelled``, and ``approved`` →
  ``revoked`` (#198); ``decided_at`` is set exactly for the four final
  states (a revoked row keeps its approval decision), ``rejection_reason``
  exactly for ``rejected``, and ``revoked_at``, ``revoked_by_user_id`` and
  ``revocation_reason`` exactly for ``revoked``. The partial unique index
  ``uq_tutor_qualifications_active`` allows one active (``pending`` or
  ``approved``) qualification per tutor and subject; rejected and cancelled
  and revoked rows don't block a new request. The row keeps its own status, mirrored
  with its ``staff.qualification`` approval request in one transaction, so
  eligibility never reads ``approval_requests`` (ADR-0002). ``Audited``,
  versioned.
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
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

EXPERIENCE_MAX_LENGTH = 4000

# The unique constraint on user_id (NAMING_CONVENTION): a concurrent first
# edit that loses the race is 409 version_conflict.
USER_ID_UNIQUE = "uq_tutor_profiles_user_id"


class TutorProfile(Audited, VersionedMixin, Model):
    __tablename__ = "tutor_profiles"
    __table_args__ = (
        CheckConstraint(
            f"char_length(professional_experience) <= {EXPERIENCE_MAX_LENGTH}",
            name="professional_experience_length",
        ),
        CheckConstraint(
            "professional_experience = btrim(professional_experience)",
            name="professional_experience_trimmed",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    professional_experience: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Tutor qualifications (#44) ------------------------------------------------------

PENDING: Final = "pending"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"
CANCELLED: Final = "cancelled"
REVOKED: Final = "revoked"

QUALIFICATION_STATUSES: Final = (PENDING, APPROVED, REJECTED, CANCELLED, REVOKED)
FINAL_STATUSES: Final = (APPROVED, REJECTED, CANCELLED, REVOKED)
"""The statuses that keep ``decided_at`` (a revoked row was approved first)."""
ACTIVE_STATUSES: Final = (PENDING, APPROVED)
"""The statuses that block another request for the same tutor and subject."""

REJECTION_REASON_MAX_LENGTH: Final = 1000
REVOCATION_REASON_MAX_LENGTH: Final = 1000

# The "no duplicate active qualification" index (#44 A1): a concurrent
# duplicate request that loses the race is 409 qualification_exists.
ACTIVE_UNIQUE: Final = "uq_tutor_qualifications_active"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class TutorQualification(Audited, VersionedMixin, Model):
    __tablename__ = "tutor_qualifications"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_quoted(QUALIFICATION_STATUSES)})", name="status_valid"
        ),
        CheckConstraint(
            f"(status IN ({_quoted(FINAL_STATUSES)})) = (decided_at IS NOT NULL)",
            name="decided_iff_final",
        ),
        CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="reason_iff_rejected",
        ),
        CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)"
            " AND (status = 'revoked') = (revoked_by_user_id IS NOT NULL)"
            " AND (status = 'revoked') = (revocation_reason IS NOT NULL)",
            name="revocation_iff_revoked",
        ),
        Index(
            ACTIVE_UNIQUE,
            "tutor_user_id",
            "subject_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
        ),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(REJECTION_REASON_MAX_LENGTH), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(
        String(REVOCATION_REASON_MAX_LENGTH), nullable=True
    )
