"""Student profiles, their emergency contacts, guardian profiles, the
student-guardian links and family groupings (spec §4, §12; #33, #34, #172).

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

- ``guardians`` (#34): a parent or guardian, with or without a login
  (ADR-0007 D18: staff record paper consent for guardians without one).
  Names, the profile's **own** optional ``email``/``phone`` (never a
  login's), ``is_active`` and an optional ``user_id`` (a foreign key to
  ``users``, ``ON DELETE RESTRICT``, unique: one account is linked to at most
  one profile). That account sees the profile's linked students while it
  holds the ``guardian`` role and the profile is active.
- ``student_guardians`` (#34): one row per link, with a ``relationship``
  code (:data:`~app.modules.families.constants.GUARDIAN_RELATIONSHIPS`).
  Unique per ``(student_id, guardian_id)``; created and hard-deleted, never
  edited (to change the relationship, unlink and link again). A link never
  changes either profile's ``version``.

- ``families`` (#172): a named family grouping (spec §2 "family account
  with multiple children"), which M6-05 attaches a billing account to.
  Versioned; deactivated, never deleted. Names aren't unique.
- ``family_students`` / ``family_guardians`` (#172): membership rows, created
  and hard-deleted (the audit ``delete`` event keeps them). A student and a
  guardian profile each belong to **at most one** family (owner decision #172
  E2 (a)): ``uq_family_students_student_id`` / ``uq_family_guardians_guardian_id``.
  Membership never changes a family's, student's or guardian's ``version``,
  and it is **not** student access (the #34 links are).

Profiles are never deleted by the application: they are deactivated
(``is_active``). Both tables are :class:`~app.core.audit.Audited` with
nothing excluded or masked (#22; personal fields are audited unmasked, spec
§9). ``students`` and ``guardians`` are versioned; a student's ``version``
covers its contacts too (any contact change increments it).
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
    Text,
    UniqueConstraint,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin
from app.modules.families.constants import GUARDIAN_RELATIONSHIPS

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

GUARDIAN_SEARCH_INDEX: Final = "ix_guardians_search_name_trgm"
GUARDIAN_USER_UNIQUE: Final = "uq_guardians_user_id"
"""One account per guardian profile (#34 A2); the service maps a violation of
exactly this constraint to ``409 guardian_account_in_use``."""
LINK_UNIQUE: Final = "uq_student_guardians_student_guardian"
"""One link per student and guardian (#34 A3); a violation of exactly this
constraint is ``409 guardian_already_linked``."""


FAMILY_NAME_MAX_LENGTH: Final = 200
FAMILY_SEARCH_INDEX: Final = "ix_families_search_name_trgm"
FAMILY_INDEX_EXPRESSION: Final = "f_unaccent(lower(name::text))"
"""``public.f_unaccent(lower(name))`` as PostgreSQL prints it back (see
:data:`INDEX_EXPRESSION`)."""
FAMILY_STUDENT_UNIQUE: Final = "uq_family_students_student_id"
"""One family per student (#172 E2 (a)); a violation of exactly this
constraint is ``409 family_member_conflict``."""
FAMILY_GUARDIAN_UNIQUE: Final = "uq_family_guardians_guardian_id"
"""One family per guardian profile (#172 E2 (a))."""


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


class Guardian(Audited, VersionedMixin, Model):
    """A parent or guardian profile (#34 A2)."""

    __tablename__ = "guardians"
    __table_args__ = (
        _trimmed("given_names"),
        _not_blank("given_names"),
        _trimmed("family_names"),
        _not_blank("family_names"),
        CheckConstraint(f"email ~ '{EMAIL_PATTERN}'", name="email_format"),
        CheckConstraint(f"phone ~ '{PHONE_PATTERN}'", name="phone_format"),
        UniqueConstraint("user_id", name=GUARDIAN_USER_UNIQUE),
        Index(
            GUARDIAN_SEARCH_INDEX,
            literal_column(INDEX_EXPRESSION).label("search_name"),
            postgresql_using="gin",
            postgresql_ops={"search_name": "gin_trgm_ops"},
        ),
    )

    given_names: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    family_names: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    email: Mapped[str | None] = mapped_column(String(EMAIL_MAX_LENGTH), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(PHONE_MAX_LENGTH), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


class StudentGuardian(Audited, Model):
    """A link between a student and a guardian profile (#34 A3)."""

    __tablename__ = "student_guardians"
    __table_args__ = (
        CheckConstraint(
            "relationship IN ({})".format(
                ", ".join(f"'{code}'" for code in GUARDIAN_RELATIONSHIPS)
            ),
            name="relationship_valid",
        ),
        # Its index also serves lookups by student (the leading column).
        UniqueConstraint("student_id", "guardian_id", name=LINK_UNIQUE),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT")
    )
    guardian_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardians.id", ondelete="RESTRICT"),
        index=True,
    )
    relationship: Mapped[str] = mapped_column(Text)


class Family(Audited, VersionedMixin, Model):
    """A family grouping (#172 A1): the future owner of a billing account
    (M6-05). Membership is in :class:`FamilyStudent` and
    :class:`FamilyGuardian`."""

    __tablename__ = "families"
    __table_args__ = (
        _trimmed("name"),
        _not_blank("name"),
        Index(
            FAMILY_SEARCH_INDEX,
            literal_column(FAMILY_INDEX_EXPRESSION).label("search_name"),
            postgresql_using="gin",
            postgresql_ops={"search_name": "gin_trgm_ops"},
        ),
    )

    name: Mapped[str] = mapped_column(String(FAMILY_NAME_MAX_LENGTH))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


class FamilyStudent(Audited, Model):
    """A student's membership of a family (#172 A1): at most one per student."""

    __tablename__ = "family_students"
    __table_args__ = (UniqueConstraint("student_id", name=FAMILY_STUDENT_UNIQUE),)

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("families.id", ondelete="RESTRICT"),
        index=True,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT")
    )


class FamilyGuardian(Audited, Model):
    """A guardian profile's membership of a family (#172 A1): at most one per
    profile."""

    __tablename__ = "family_guardians"
    __table_args__ = (UniqueConstraint("guardian_id", name=FAMILY_GUARDIAN_UNIQUE),)

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("families.id", ondelete="RESTRICT"),
        index=True,
    )
    guardian_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guardians.id", ondelete="RESTRICT")
    )
