"""Staff tables (#43; spec §4).

- ``tutor_profiles``: the professional experience of one tutor, at most one
  row per user (``uq_tutor_profiles_user_id``). The row is created lazily by
  the first profile edit (#43 A3): a tutor without one has an empty profile
  at ``version`` 0. Losing the ``tutor`` role hides the profile but keeps the
  row. ``Audited`` with nothing excluded, versioned.
"""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Text
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
