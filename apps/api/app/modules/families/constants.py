"""Code constants of the families module (#34, #35).

:data:`GUARDIAN_RELATIONSHIPS` are the relationship codes of a
student-guardian link (``student_guardians.relationship``), checked by the
database (the ``USER_STATES`` pattern of #20) and exposed in OpenAPI as a
string enum (``schemas.GuardianRelationship``; a unit test keeps both equal).
The codes are gender-neutral where the language allows; the UI translates
them (``relationships.*``).
"""

from typing import Final

GUARDIAN_RELATIONSHIPS: Final = (
    "mother",
    "father",
    "grandparent",
    "stepparent",
    "sibling",
    "other_relative",
    "legal_guardian",
    "other",
)

CONSENT_SCOPES: Final = ("student_login", "guardian_academic_access")
"""The scopes of a consent record (#35; ``consent_records.scope``):

- ``student_login``: a linked guardian allows a **minor** student's login
  (ADR-0007 D18); ``guardian_id`` is the guardian who consents;
- ``guardian_academic_access``: an **adult** student shares their academic
  records with one linked guardian (D17, owner decision #35 E4 (a));
  ``guardian_id`` is the guardian who receives access.

Checked by the database and exposed as a string enum
(``schemas.ConsentScope``; a unit test keeps both equal)."""

CONSENT_METHODS: Final = ("in_app", "paper", "in_person")
"""How a consent was given (``consent_records.method``): in the app by the
guardian or the student themselves, or recorded by staff from paper or in
person (D18)."""

REVOCATION_CAUSES: Final = ("revoked", "guardian_unlinked")
"""Why a consent record ended (``consent_records.revocation_cause``): an
explicit revocation, or the guardian was unlinked from the student (#35 E3)."""
