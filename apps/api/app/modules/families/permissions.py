"""Record policies of student profiles (#33 F; #21 C2 convention).

Pure functions, no I/O: the service loads the facts about the caller
(:class:`StudentAccessFacts`) and passes them in.

**Who sees a student** (:func:`can_view_student`), any matching role allows
(the union of a multi-role user's roles):

- a **staff** role (:data:`STAFF_ROLES`) together with ``student.view``:
  every student;
- ``tutor``: the students in ``assigned_student_ids`` (current assignments;
  empty until M4 replaces the stub source);
- ``guardian``: the students in ``linked_student_ids`` (empty until #34
  loads guardian links);
- ``student``: the profile ``own_student_id`` (``None`` until #35 links a
  login to its profile).

A student that is not visible is ``404 not_found``, the same as a missing
one (``authorize_record``); a list leaves it out.

**How much of it** (:func:`student_access_level`, owner decision #33 E2 (a)):
``full`` (the ``personal`` part: birthdate, contact details, emergency
contacts) for holders of ``student.manage``, a linked guardian and the
student themselves; ``limited`` for every other viewer (Finance Staff,
assigned tutors).

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

type AccessLevel = Literal["full", "limited"]


@dataclass(frozen=True)
class StudentAccessFacts:
    """What the caller is linked to. Each set is loaded only when the caller
    holds the matching role; otherwise it stays empty (or ``None``)."""

    assigned_student_ids: frozenset[uuid.UUID] = frozenset()
    """Tutor: the students of their current assignments (M4)."""
    linked_student_ids: frozenset[uuid.UUID] = frozenset()
    """Guardian: the students they are linked to (#34)."""
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
    principal: Principal, student_id: uuid.UUID, facts: StudentAccessFacts
) -> AccessLevel | None:
    """``"full"``, ``"limited"``, or ``None`` when the student is not visible."""
    if not can_view_student(principal, student_id, facts):
        return None
    if principal.has(STUDENT_MANAGE):
        return "full"
    if GUARDIAN in principal.roles and student_id in facts.linked_student_ids:
        return "full"
    if STUDENT in principal.roles and facts.own_student_id == student_id:
        return "full"
    return "limited"
