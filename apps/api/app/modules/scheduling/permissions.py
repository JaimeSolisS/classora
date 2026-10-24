"""The record policies of the role-scoped schedules (#67 B1, C1, C4;
ADR-0005 D9: tutors and families reach their schedules through record
policies, not permissions; ADR-0007 D17).

Pure functions, no I/O. The caller loads the facts on every request (no
cache: an unlink, a deactivated guardian profile or a revoked consent
applies on the next request) and, only when the answer depends on it,
whether the student is a minor today in the center zone.

**Tutor schedule** (``GET /api/v1/tutors/{user_id}/schedule``, behind
``require("tutor.view")``): the caller is the tutor (``user_id`` is their
own id), or holds a staff role together with ``schedule.view`` (staff can
check what a tutor sees). Anyone else gets the ``404`` of an unknown id.

**Family schedule** (``GET /api/v1/students/{student_id}/schedule``, behind
``require("student.view")``):

- **student:** ``student_id`` is the caller's own profile;
- **guardian:** ``student_id`` is linked to the caller's **active** guardian
  profile and the student is a minor, or an adult who gave that guardian a
  ``guardian_academic_access`` consent (the #59 E3 rule, reused from
  :mod:`~app.modules.scheduling.requests_policy`); a linked guardian of an
  adult without it gets :attr:`ScheduleAccess.CONSENT_REQUIRED` (``403
  student_consent_required``);
- **staff:** a staff role together with ``schedule.view``.

Everyone else (an unknown id, an unlinked student, a sibling asked for by a
student, a student a tutor only teaches, an inactive guardian profile) gets
:attr:`ScheduleAccess.HIDDEN`: the ``404`` of an unknown id. With several
roles, any allowing one wins.
"""

import uuid
from enum import Enum
from typing import Final

from app.core.authz import Principal
from app.modules.families.service import StudentAccessFacts
from app.modules.scheduling.requests_policy import (
    FamilyAccess,
    family_access,
    is_linked_guardian,
    is_own_student,
)
from app.modules.scheduling.requests_policy import needs_age as _needs_age

SCHEDULE_VIEW: Final = "schedule.view"
STAFF_ROLES: Final = frozenset(
    {"owner", "administrator", "academic_coordinator", "finance_staff"}
)
"""The staff roles (``families.permissions.STAFF_ROLES``)."""


class ScheduleAccess(Enum):
    """The outcome of a family schedule read for one student."""

    ALLOWED = "allowed"
    CONSENT_REQUIRED = "consent_required"
    HIDDEN = "hidden"


def is_schedule_staff(principal: Principal) -> bool:
    """A staff role together with ``schedule.view`` (#67 B1, C1 choice)."""
    return bool(STAFF_ROLES & principal.roles) and principal.has(SCHEDULE_VIEW)


def can_view_tutor_schedule(principal: Principal, tutor_user_id: uuid.UUID) -> bool:
    """B1: the tutor themselves, or staff with ``schedule.view``. A staff
    caller still gets ``404`` for an id that is not a tutor (the caller
    checks it)."""
    return principal.user_id == tutor_user_id or is_schedule_staff(principal)


def needs_age(
    principal: Principal, student_id: uuid.UUID, facts: StudentAccessFacts
) -> bool:
    """Whether :func:`student_schedule_access` depends on the student being
    a minor: only for a linked guardian without the consent, who is neither
    the student nor schedule staff."""
    return not is_schedule_staff(principal) and _needs_age(principal, student_id, facts)


def is_candidate(
    principal: Principal, student_id: uuid.UUID, facts: StudentAccessFacts
) -> bool:
    """Whether the student is the caller's own profile or linked to their
    active guardian profile (before the adult rule)."""
    return is_own_student(principal, student_id, facts) or is_linked_guardian(
        principal, student_id, facts
    )


def student_schedule_access(
    principal: Principal,
    student_id: uuid.UUID,
    facts: StudentAccessFacts,
    *,
    is_minor: bool | None,
) -> ScheduleAccess:
    """C1. ``is_minor`` is needed only when :func:`needs_age` says so
    (``None`` otherwise); missing when needed, the guardian is refused
    (fails closed)."""
    if is_schedule_staff(principal):
        return ScheduleAccess.ALLOWED
    outcome = family_access(principal, student_id, facts, is_minor=is_minor)
    if outcome is FamilyAccess.ALLOWED:
        return ScheduleAccess.ALLOWED
    if outcome is FamilyAccess.CONSENT_REQUIRED:
        return ScheduleAccess.CONSENT_REQUIRED
    return ScheduleAccess.HIDDEN
