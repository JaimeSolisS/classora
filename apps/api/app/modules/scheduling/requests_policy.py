"""The family record policy of session requests and available slots (#59 S1,
S2; ADR-0005 D9 "students and guardians reach their schedules through
record-level policies, not permissions"; ADR-0007 D17).

Pure functions, no I/O: the caller loads the facts once per request
(``families.service.load_access_facts``) and, only when the answer depends
on it, whether the student is a minor today in the center zone.

A caller may **act for** ``student_id`` when either holds:

- **guardian:** they hold the ``guardian`` role and the student is linked to
  their **active** guardian profile (``linked_student_ids``, #34). For an
  **adult** student the guardian also needs that student's
  ``guardian_academic_access`` consent naming their profile
  (``families.service.guardian_has_academic_access``, #35 E4 (a), #59 E3);
  without it the answer is :attr:`FamilyAccess.CONSENT_REQUIRED` (``403
  student_consent_required``: they already see the student's limited
  profile);
- **student:** they hold the ``student`` role and the student is their own
  profile (``own_student_id``, #35).

Everyone else (staff without one of those roles, tutors, an Owner, an
unlinked guardian, a guardian whose profile is inactive) gets
:attr:`FamilyAccess.HIDDEN`: the same ``404`` as an unknown id. Family
membership (#172) grants nothing. With several roles any allowing one wins.
"""

import uuid
from enum import Enum
from typing import Final

from app.core.authz import Principal
from app.modules.families import service as families
from app.modules.families.service import StudentAccessFacts

GUARDIAN: Final = "guardian"
STUDENT: Final = "student"
FAMILY_ROLES: Final = frozenset({GUARDIAN, STUDENT})


class FamilyAccess(Enum):
    """The S1/S2 outcome for one student."""

    ALLOWED = "allowed"
    CONSENT_REQUIRED = "consent_required"
    HIDDEN = "hidden"


def has_family_role(principal: Principal) -> bool:
    """Whether the caller holds ``guardian`` or ``student`` (only then are
    facts worth loading)."""
    return bool(FAMILY_ROLES & principal.roles)


def is_own_student(
    principal: Principal, student_id: uuid.UUID, facts: StudentAccessFacts
) -> bool:
    """The student is the caller's own profile (``student`` role)."""
    return STUDENT in principal.roles and facts.own_student_id == student_id


def is_linked_guardian(
    principal: Principal, student_id: uuid.UUID, facts: StudentAccessFacts
) -> bool:
    """The student is linked to the caller's active guardian profile."""
    return GUARDIAN in principal.roles and student_id in facts.linked_student_ids


def needs_age(
    principal: Principal, student_id: uuid.UUID, facts: StudentAccessFacts
) -> bool:
    """Whether :func:`family_access` depends on the student being a minor:
    only for a linked guardian without the consent who is not the student
    themselves. A hidden student never needs it (no extra read for the
    ``404``)."""
    return (
        not is_own_student(principal, student_id, facts)
        and is_linked_guardian(principal, student_id, facts)
        and student_id not in facts.academic_consent_student_ids
    )


def family_access(
    principal: Principal,
    student_id: uuid.UUID,
    facts: StudentAccessFacts,
    *,
    is_minor: bool | None,
) -> FamilyAccess:
    """The S1 outcome. ``is_minor`` is needed only when :func:`needs_age`
    says so (``None`` otherwise); missing when needed, the guardian is
    refused (fails closed)."""
    if is_own_student(principal, student_id, facts):
        return FamilyAccess.ALLOWED
    if not is_linked_guardian(principal, student_id, facts):
        return FamilyAccess.HIDDEN
    if families.guardian_has_academic_access(
        principal, student_id, is_minor=bool(is_minor), facts=facts
    ):
        return FamilyAccess.ALLOWED
    return FamilyAccess.CONSENT_REQUIRED


def candidate_student_ids(
    principal: Principal, facts: StudentAccessFacts
) -> frozenset[uuid.UUID]:
    """The students the caller may act for before the adult rule: their own
    profile and their linked students, each only through its role (the
    lists filter these further with :func:`family_access`)."""
    ids: set[uuid.UUID] = set()
    if GUARDIAN in principal.roles:
        ids |= facts.linked_student_ids
    if STUDENT in principal.roles and facts.own_student_id is not None:
        ids.add(facts.own_student_id)
    return frozenset(ids)
