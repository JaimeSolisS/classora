"""Code constants of the families module (#34).

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
