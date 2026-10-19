"""Record policies of student profiles (#33 F; #21 C2 convention).

Pure functions, no I/O: the service loads the facts about the caller
(:class:`StudentAccessFacts`) and passes them in.

**Who sees a student** (:func:`can_view_student`), any matching role allows
(the union of a multi-role user's roles):

- a **staff** role (:data:`STAFF_ROLES`) together with ``student.view``:
  every student;
- ``tutor``: the students in ``assigned_student_ids`` (current assignments;
  empty until M4 replaces the stub source);
- ``guardian``: the students in ``linked_student_ids`` (the links of the
  caller's **active** guardian profile, #34);
- ``student``: the profile ``own_student_id`` (``None`` until #35 links a
  login to its profile).

A student that is not visible is ``404 not_found``, the same as a missing
one (``authorize_record``); a list leaves it out.

**How much of it** (:func:`student_access_level`, owner decision #33 E2 (a)):
``full`` (the ``personal`` part: birthdate, contact details, emergency
contacts) for holders of ``student.manage``, a linked guardian of a **minor**
(owner decision #34 E3 (a)) and the student themselves; ``limited`` for every
other viewer (Finance Staff, assigned tutors, a linked guardian of an adult
student until #35 records the student's consent, ADR-0007 D17).

**Guardian profiles** (:func:`can_view_guardian`, #34 F1): a staff role
together with ``guardian.view`` sees every profile; a ``guardian`` sees only
their own (``GuardianAccessFacts.own_guardian_id``, the active profile linked
to their account). The co-guardians of a student are shown only to staff
(:func:`sees_student_guardians`).

The role and permission codes are the #20 catalog's, repeated here because
this module imports no other module's internals (ADR-0002);
``tests/unit/test_student_policy.py`` checks they match
``app.modules.identity.permissions``.
"""

import uuid
from dataclasses import dataclass
from typing import Final, Literal

from app.core.authz import Principal

OWNER: Final = "owner"
ADMINISTRATOR: Final = "administrator"
ACADEMIC_COORDINATOR: Final = "academic_coordinator"
FINANCE_STAFF: Final = "finance_staff"
TUTOR: Final = "tutor"
STUDENT: Final = "student"
GUARDIAN: Final = "guardian"

STAFF_ROLES: Final = frozenset(
    {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF}
)

STUDENT_VIEW: Final = "student.view"
STUDENT_MANAGE: Final = "student.manage"
GUARDIAN_VIEW: Final = "guardian.view"
FAMILY_MANAGE: Final = "family.manage"

type AccessLevel = Literal["full", "limited"]


@dataclass(frozen=True)
class StudentAccessFacts:
    """What the caller is linked to. Each set is loaded only when the caller
    holds the matching role; otherwise it stays empty (or ``None``)."""

    assigned_student_ids: frozenset[uuid.UUID] = frozenset()
    """Tutor: the students of their current assignments (M4)."""
    linked_student_ids: frozenset[uuid.UUID] = frozenset()
    """Guardian: the students linked to their active guardian profile (#34)."""
    own_student_id: uuid.UUID | None = None
    """Student user: their own profile (#35)."""


NO_FACTS: Final = StudentAccessFacts()


def sees_every_student(principal: Principal) -> bool:
    """A staff role together with ``student.view``: every student is visible."""
    return bool(STAFF_ROLES & principal.roles) and principal.has(STUDENT_VIEW)


def visible_student_ids(
    principal: Principal, facts: StudentAccessFacts
) -> frozenset[uuid.UUID]:
    """For a caller who doesn't see every student: the ids they may see
    (``assigned ∪ linked ∪ own``, each only through its role)."""
    ids: set[uuid.UUID] = set()
    if TUTOR in principal.roles:
        ids |= facts.assigned_student_ids
    if GUARDIAN in principal.roles:
        ids |= facts.linked_student_ids
    if STUDENT in principal.roles and facts.own_student_id is not None:
        ids.add(facts.own_student_id)
    return frozenset(ids)


def can_view_student(
    principal: Principal, student_id: uuid.UUID, facts: StudentAccessFacts
) -> bool:
    """Whether the caller may see this student at all."""
    return sees_every_student(principal) or student_id in visible_student_ids(
        principal, facts
    )


def student_access_level(
    principal: Principal,
    student_id: uuid.UUID,
    facts: StudentAccessFacts,
    *,
    is_minor: bool,
) -> AccessLevel | None:
    """``"full"``, ``"limited"``, or ``None`` when the student is not visible.

    ``is_minor`` is the student's status today in the center's time zone
    (``ages.is_minor(birthdate, center_today())``), passed in so the function
    stays pure. With several roles, the highest level wins (``full`` >
    ``limited``).

    A linked guardian gets ``full`` for a minor and ``limited`` for an adult
    (owner decision #34 E3 (a); ADR-0007 D17). #35 adds an adult-consent fact
    (for example ``consented_adult_student_ids``) that upgrades a linked
    guardian of an adult who consented to ``full``.
    """
    if not can_view_student(principal, student_id, facts):
        return None
    if principal.has(STUDENT_MANAGE):
        return "full"
    if (
        GUARDIAN in principal.roles
        and student_id in facts.linked_student_ids
        and is_minor
    ):
        return "full"
    if STUDENT in principal.roles and facts.own_student_id == student_id:
        return "full"
    return "limited"


def sees_student_guardians(principal: Principal) -> bool:
    """Whether ``StudentDetail.guardians`` is shown (#34 E1): a staff role
    together with ``guardian.view``. Tutors, student users and linked
    guardians never see a student's guardians (a guardian never sees the
    co-guardians, which protects separated families). The same rule as
    :func:`sees_every_guardian`."""
    return sees_every_guardian(principal)


@dataclass(frozen=True)
class GuardianAccessFacts:
    """The caller's own guardian profile: loaded only for a caller holding
    ``guardian``; ``None`` without an **active** profile linked to their
    account."""

    own_guardian_id: uuid.UUID | None = None


NO_GUARDIAN_FACTS: Final = GuardianAccessFacts()


def sees_every_guardian(principal: Principal) -> bool:
    """A staff role together with ``guardian.view``: every profile is visible."""
    return bool(STAFF_ROLES & principal.roles) and principal.has(GUARDIAN_VIEW)


def can_view_guardian(
    principal: Principal, guardian_id: uuid.UUID, facts: GuardianAccessFacts
) -> bool:
    """Whether the caller may see this guardian profile (#34 F1)."""
    if sees_every_guardian(principal):
        return True
    return GUARDIAN in principal.roles and (
        facts.own_guardian_id is not None and guardian_id == facts.own_guardian_id
    )
