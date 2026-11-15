"""The student record policies (#33 F1, F5): pure functions, no I/O. The
cases here are minors (``is_minor=True``); the adult cases of #34 E3 are in
``test_guardian_policy.py``."""

import uuid

import pytest

from app.core.authz import Principal
from app.modules.families import permissions as policy
from app.modules.families.permissions import (
    StudentAccessFacts,
    can_view_student,
    student_access_level,
    visible_student_ids,
)
from app.modules.identity import permissions as identity

A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
B = uuid.UUID("00000000-0000-4000-8000-00000000000b")

ROLES = identity.ROLE_CODES


def principal(*roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= identity.ROLE_PERMISSIONS[role]
    return Principal(
        user_id=uuid.uuid4(), roles=frozenset(roles), permissions=frozenset(permissions)
    )


NONE = StudentAccessFacts()
ALL_FACTS_FOR_A = StudentAccessFacts(
    assigned_student_ids=frozenset({A}),
    linked_student_ids=frozenset({A}),
    own_student_id=A,
)


def test_the_codes_match_the_identity_catalog():
    assert policy.STAFF_ROLES == identity.STAFF_ROLES
    assert (
        policy.OWNER,
        policy.ADMINISTRATOR,
        policy.ACADEMIC_COORDINATOR,
        policy.FINANCE_STAFF,
        policy.TUTOR,
        policy.STUDENT,
        policy.GUARDIAN,
    ) == ROLES
    assert policy.STUDENT_VIEW == identity.STUDENT_VIEW
    assert policy.STUDENT_MANAGE == identity.STUDENT_MANAGE
    assert policy.GUARDIAN_VIEW == identity.GUARDIAN_VIEW
    assert policy.FAMILY_MANAGE == identity.FAMILY_MANAGE


def test_every_role_holds_student_view_and_only_three_manage():
    assert all(identity.STUDENT_VIEW in identity.ROLE_PERMISSIONS[r] for r in ROLES)
    assert {
        r for r in ROLES if identity.STUDENT_MANAGE in identity.ROLE_PERMISSIONS[r]
    } == {
        identity.OWNER,
        identity.ADMINISTRATOR,
        identity.ACADEMIC_COORDINATOR,
    }


# (role, facts) -> expected access level for student A; B is never linked.
SINGLE_ROLE_CASES = [
    (identity.OWNER, NONE, "full"),
    (identity.ADMINISTRATOR, NONE, "full"),
    (identity.ACADEMIC_COORDINATOR, NONE, "full"),
    (identity.FINANCE_STAFF, NONE, "limited"),
    (identity.TUTOR, NONE, None),
    (
        identity.TUTOR,
        StudentAccessFacts(assigned_student_ids=frozenset({A})),
        "limited",
    ),
    (identity.TUTOR, StudentAccessFacts(linked_student_ids=frozenset({A})), None),
    (identity.GUARDIAN, NONE, None),
    (identity.GUARDIAN, StudentAccessFacts(linked_student_ids=frozenset({A})), "full"),
    (identity.GUARDIAN, StudentAccessFacts(assigned_student_ids=frozenset({A})), None),
    (identity.STUDENT, NONE, None),
    (identity.STUDENT, StudentAccessFacts(own_student_id=A), "full"),
    (identity.STUDENT, StudentAccessFacts(own_student_id=B), None),
    (identity.STUDENT, StudentAccessFacts(linked_student_ids=frozenset({A})), None),
]


@pytest.mark.parametrize(("role", "facts", "expected"), SINGLE_ROLE_CASES)
def test_each_role_alone(role, facts, expected):
    caller = principal(role)

    assert student_access_level(caller, A, facts, is_minor=True) == expected
    assert can_view_student(caller, A, facts) is (expected is not None)


@pytest.mark.parametrize("role", ROLES)
def test_an_unlinked_student_follows_the_staff_rule(role):
    caller = principal(role)
    staff = role in identity.STAFF_ROLES

    assert can_view_student(caller, B, ALL_FACTS_FOR_A) is staff
    assert (
        student_access_level(caller, B, ALL_FACTS_FOR_A, is_minor=True) is None
    ) is not staff


def test_tutor_and_guardian_get_the_union():
    caller = principal(identity.TUTOR, identity.GUARDIAN)
    facts = StudentAccessFacts(
        assigned_student_ids=frozenset({A}), linked_student_ids=frozenset({B})
    )

    assert student_access_level(caller, A, facts, is_minor=True) == "limited"
    assert student_access_level(caller, B, facts, is_minor=True) == "full"
    assert student_access_level(caller, uuid.uuid4(), facts, is_minor=True) is None
    assert visible_student_ids(caller, facts) == {A, B}


def test_finance_staff_with_academic_coordinator_get_full_access():
    caller = principal(identity.FINANCE_STAFF, identity.ACADEMIC_COORDINATOR)

    assert student_access_level(caller, A, NONE, is_minor=True) == "full"


def test_tutor_with_academic_coordinator_see_every_student_in_full():
    caller = principal(identity.TUTOR, identity.ACADEMIC_COORDINATOR)

    assert student_access_level(caller, B, NONE, is_minor=True) == "full"


def test_finance_staff_and_an_assigned_tutor_stay_limited():
    caller = principal(identity.FINANCE_STAFF, identity.TUTOR)
    facts = StudentAccessFacts(assigned_student_ids=frozenset({A}))

    assert student_access_level(caller, A, facts, is_minor=True) == "limited"


def test_a_staff_role_without_student_view_sees_nothing_through_it():
    caller = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({identity.FINANCE_STAFF}),
        permissions=frozenset(),
    )

    assert can_view_student(caller, A, NONE) is False


def test_facts_count_only_through_their_role():
    caller = principal(identity.FINANCE_STAFF)

    assert visible_student_ids(caller, ALL_FACTS_FOR_A) == frozenset()
