"""The family record policy (#172 D1, D3): a pure function, no I/O."""

import uuid

import pytest

from app.core.authz import Principal
from app.modules.families.permissions import (
    NO_FAMILY_FACTS,
    FamilyAccessFacts,
    StudentAccessFacts,
    can_view_family,
    can_view_student,
    sees_every_family,
)
from app.modules.identity import permissions as identity

A = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
B = uuid.UUID("00000000-0000-4000-8000-0000000000f2")
STUDENT_IN_A = uuid.UUID("00000000-0000-4000-8000-0000000000c1")

STAFF_WITH_GUARDIAN_VIEW = sorted(
    r
    for r in identity.STAFF_ROLES
    if identity.GUARDIAN_VIEW in identity.ROLE_PERMISSIONS[r]
)


def principal(*roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= identity.ROLE_PERMISSIONS[role]
    return Principal(
        user_id=uuid.uuid4(), roles=frozenset(roles), permissions=frozenset(permissions)
    )


OWN_A = FamilyAccessFacts(own_family_id=A)


def test_the_four_staff_roles_hold_guardian_view():
    assert STAFF_WITH_GUARDIAN_VIEW == sorted(identity.STAFF_ROLES)


@pytest.mark.parametrize("role", STAFF_WITH_GUARDIAN_VIEW)
def test_each_staff_role_sees_every_family(role):
    caller = principal(role)

    assert sees_every_family(caller)
    assert can_view_family(caller, A, NO_FAMILY_FACTS)
    assert can_view_family(caller, B, OWN_A)


def test_a_staff_role_without_guardian_view_sees_no_family():
    caller = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({identity.FINANCE_STAFF}),
        permissions=frozenset({identity.STUDENT_VIEW}),
    )

    assert not sees_every_family(caller)
    assert not can_view_family(caller, A, OWN_A)


def test_a_guardian_sees_only_their_own_family():
    caller = principal(identity.GUARDIAN)

    assert can_view_family(caller, A, OWN_A)
    assert not can_view_family(caller, B, OWN_A)
    assert not sees_every_family(caller)


def test_a_guardian_without_a_profile_or_family_sees_none():
    caller = principal(identity.GUARDIAN)

    assert not can_view_family(caller, A, NO_FAMILY_FACTS)
    assert not can_view_family(caller, A, FamilyAccessFacts(own_family_id=None))


def test_a_tutor_who_is_a_guardian_sees_only_their_family():
    caller = principal(identity.TUTOR, identity.GUARDIAN)

    assert can_view_family(caller, A, OWN_A)
    assert not can_view_family(caller, B, OWN_A)


@pytest.mark.parametrize("role", [identity.TUTOR, identity.STUDENT])
def test_tutors_and_students_see_no_family(role):
    # Even with a (stray) fact: only the guardian role uses own_family_id.
    assert not can_view_family(principal(role), A, OWN_A)
    assert not can_view_family(principal(role), A, NO_FAMILY_FACTS)


def test_membership_is_not_student_access():
    # A guardian of family A with no link to a student of A doesn't see it:
    # the student policy has no family fact at all.
    caller = principal(identity.GUARDIAN)

    assert can_view_family(caller, A, OWN_A)
    assert not can_view_student(caller, STUDENT_IN_A, StudentAccessFacts())
    assert can_view_student(
        caller,
        STUDENT_IN_A,
        StudentAccessFacts(linked_student_ids=frozenset({STUDENT_IN_A})),
    )
