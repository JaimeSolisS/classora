"""Student profiles and their emergency contacts (spec §4, §12; #33).

- ``students``: one profile per student, with or without a login (#35 links
  a login later). Personal details (``given_names``, ``family_names`` and a
  required ``birthdate``, ADR-0007 D17), optional contact details (``email``,
  ``phone``; the profile's own, never a login's), school information and an
  optional education level (a foreign key to the #32 catalog, ``ON DELETE
  RESTRICT``; ADR-0002 allows cross-module foreign keys).
- ``student_emergency_contacts``: one to five contacts per student, ordered
  by ``position`` (1 is called first). The database caps a student at five:
  ``position`` is 1-5 and unique per student. The unique constraint is
  ``DEFERRABLE INITIALLY DEFERRED``, so a reorder can flush in any order.

Name search (ISSUE-D6, accepted on #33) uses the GIN trigram expression
index :data:`SEARCH_INDEX` on ``public.f_unaccent(lower(given_names || ' ' ||
family_names))``: no stored search column, so audit diffs have no derived
value. ``public.f_unaccent`` is the ``IMMUTABLE`` wrapper the migration
creates (``unaccent()`` itself is only ``STABLE``).

Profiles are never deleted by the application: they are deactivated
(``is_active``). Both tables are :class:`~app.core.audit.Audited` with
nothing excluded or masked (#22; personal fields are audited unmasked, spec
§9). Only ``students`` is versioned: its ``version`` covers its contacts too
(any contact change increments it).
"""

import uuid
from datetime import date
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

NAME_MAX_LENGTH: Final = 100
EMAIL_MAX_LENGTH: Final = 254
PHONE_MAX_LENGTH: Final = 32
SCHOOL_NAME_MAX_LENGTH: Final = 200
SCHOOL_GRADE_MAX_LENGTH: Final = 50
CONTACT_NAME_MAX_LENGTH: Final = 200
RELATIONSHIP_MAX_LENGTH: Final = 50
MAX_CONTACTS: Final = 5
EARLIEST_BIRTHDATE: Final = date(1900, 1, 1)

# The #31 A1 contact patterns, copied verbatim (ADR-0002: no import of
# identity's models). tests/unit/test_student_schemas.py checks they stay
# equal to app.modules.identity.models.EMAIL_PATTERN / PHONE_PATTERN.
EMAIL_PATTERN: Final = r"^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$"
PHONE_PATTERN: Final = r"^\+?[0-9][0-9 ().-]{5,30}[0-9]$"

SEARCH_INDEX: Final = "ix_students_search_name_trgm"
SEARCH_EXPRESSION: Final = (
    "public.f_unaccent(lower(given_names || ' ' || family_names))"
)
"""The indexed name expression (the migration creates the index with it); the
search predicate builds the same expression."""
INDEX_EXPRESSION: Final = "f_unaccent(lower((given_names || ' ') || family_names))"
""":data:`SEARCH_EXPRESSION` as PostgreSQL prints it back (``public`` is on
the search path; ``||`` groups to the left). The model declares this form so
the metadata/migration comparison checks the index expression instead of
skipping it (Alembic doesn't compare operator classes; the migration test
checks ``gin_trgm_ops``)."""

CONTACT_POSITION_UNIQUE: Final = "uq_student_emergency_contacts_student_id_position"


def _trimmed(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} = btrim({column})", name=f"{column}_trimmed")


def _not_blank(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '[^[:space:]]'", name=f"{column}_not_blank")


class Student(Audited, VersionedMixin, Model):
    __tablename__ = "students"
    __table_args__ = (
        _trimmed("given_names"),
        _not_blank("given_names"),
        _trimmed("family_names"),
        _not_blank("family_names"),
        CheckConstraint("birthdate >= DATE '1900-01-01'", name="birthdate_min"),
        CheckConstraint(f"email ~ '{EMAIL_PATTERN}'", name="email_format"),
        CheckConstraint(f"phone ~ '{PHONE_PATTERN}'", name="phone_format"),
        _trimmed("school_name"),
        _trimmed("school_grade"),
        Index(
            SEARCH_INDEX,
            literal_column(INDEX_EXPRESSION).label("search_name"),
            postgresql_using="gin",
            postgresql_ops={"search_name": "gin_trgm_ops"},
        ),
    )

    given_names: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    family_names: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    birthdate: Mapped[date] = mapped_column(Date)
    email: Mapped[str | None] = mapped_column(String(EMAIL_MAX_LENGTH), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(PHONE_MAX_LENGTH), nullable=True)
    school_name: Mapped[str | None] = mapped_column(
        String(SCHOOL_NAME_MAX_LENGTH), nullable=True
    )
    school_grade: Mapped[str | None] = mapped_column(
        String(SCHOOL_GRADE_MAX_LENGTH), nullable=True
    )
    education_level_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("education_levels.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


class StudentEmergencyContact(Audited, Model):
    __tablename__ = "student_emergency_contacts"
    __table_args__ = (
        _trimmed("name"),
        _not_blank("name"),
        _trimmed("relationship"),
        _not_blank("relationship"),
        CheckConstraint(f"phone ~ '{PHONE_PATTERN}'", name="phone_format"),
        CheckConstraint(
            f"position BETWEEN 1 AND {MAX_CONTACTS}", name="position_range"
        ),
        UniqueConstraint(
            "student_id",
            "position",
            name=CONTACT_POSITION_UNIQUE,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="RESTRICT"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(CONTACT_NAME_MAX_LENGTH))
    relationship: Mapped[str] = mapped_column(String(RELATIONSHIP_MAX_LENGTH))
    phone: Mapped[str] = mapped_column(String(PHONE_MAX_LENGTH))
    position: Mapped[int] = mapped_column(SmallInteger)
