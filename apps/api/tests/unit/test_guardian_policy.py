"""The guardian record policies (#34 F1, F4): pure functions, no I/O."""

import uuid

import pytest

from app.core.authz import Principal
from app.modules.families.permissions import (
    GuardianAccessFacts,
    StudentAccessFacts,
    can_view_guardian,
    can_view_student,
    sees_every_guardian,
    sees_student_guardians,
    student_access_level,
)
from app.modules.identity import permissions as identity

G1 = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
G2 = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
S1 = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
S3 = uuid.UUID("00000000-0000-4000-8000-0000000000b3")
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000b9")

ROLES = identity.ROLE_CODES
STAFF_WITH_GUARDIAN_VIEW = [
    r
    for r in identity.STAFF_ROLES
    if identity.GUARDIAN_VIEW in identity.ROLE_PERMISSIONS[r]
]


def principal(*roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= identity.ROLE_PERMISSIONS[role]
    return Principal(
        user_id=uuid.uuid4(), roles=frozenset(roles), permissions=frozenset(permissions)
    )


OWN_G1 = GuardianAccessFacts(own_guardian_id=G1)
NO_PROFILE = GuardianAccessFacts()


# --- can_view_guardian -------------------------------------------------------------


def test_every_staff_role_holds_guardian_view():
    # The approved #20 catalog: guardian.view for the four staff roles and
    # Guardian; tutors and students don't hold it.
    assert sorted(STAFF_WITH_GUARDIAN_VIEW) == sorted(identity.STAFF_ROLES)
    holders = {
        r for r in ROLES if identity.GUARDIAN_VIEW in identity.ROLE_PERMISSIONS[r]
    }
    assert holders == set(identity.STAFF_ROLES) | {identity.GUARDIAN}
    managers = {
        r for r in ROLES if identity.FAMILY_MANAGE in identity.ROLE_PERMISSIONS[r]
    }
    assert managers == {identity.OWNER, identity.ADMINISTRATOR}


@pytest.mark.parametrize("role", sorted(identity.STAFF_ROLES))
def test_staff_with_guardian_view_see_every_profile(role):
    caller = principal(role)

    assert sees_every_guardian(caller)
    assert can_view_guardian(caller, G1, NO_PROFILE)
    assert can_view_guardian(caller, G2, OWN_G1)
    assert sees_student_guardians(caller)


def test_a_guardian_sees_only_their_own_profile():
    caller = principal(identity.GUARDIAN)

    assert not sees_every_guardian(caller)
    assert can_view_guardian(caller, G1, OWN_G1)
    assert not can_view_guardian(caller, G2, OWN_G1)
    assert not sees_student_guardians(caller)


def test_a_guardian_without_a_profile_sees_none():
    caller = principal(identity.GUARDIAN)

    assert not can_view_guardian(caller, G1, NO_PROFILE)
    assert not can_view_guardian(caller, G2, NO_PROFILE)


@pytest.mark.parametrize("role", [identity.TUTOR, identity.STUDENT])
def test_tutors_and_students_see_no_profile(role):
    caller = principal(role)

    assert identity.GUARDIAN_VIEW not in caller.permissions
    assert not can_view_guardian(caller, G1, OWN_G1)
    assert not sees_student_guardians(caller)


def test_the_own_profile_counts_only_through_the_guardian_role():
    caller = principal(identity.TUTOR)

    assert not can_view_guardian(caller, G1, OWN_G1)


def test_a_staff_role_without_guardian_view_sees_no_profile():
    caller = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({identity.FINANCE_STAFF}),
        permissions=frozenset({identity.STUDENT_VIEW}),
    )

    assert not can_view_guardian(caller, G1, NO_PROFILE)
    assert not sees_student_guardians(caller)


# --- student_access_level with links (E3) ----------------------------------------


LINKED_S1 = StudentAccessFacts(linked_student_ids=frozenset({S1}))


def test_a_linked_guardian_of_a_minor_gets_full_access():
    caller = principal(identity.GUARDIAN)

    assert student_access_level(caller, S1, LINKED_S1, is_minor=True) == "full"


def test_a_linked_guardian_of_an_adult_gets_limited_access():
    caller = principal(identity.GUARDIAN)

    assert can_view_student(caller, S1, LINKED_S1)
    assert student_access_level(caller, S1, LINKED_S1, is_minor=False) == "limited"


@pytest.mark.parametrize("is_minor", [True, False])
def test_an_unlinked_student_is_hidden_from_a_guardian(is_minor):
    caller = principal(identity.GUARDIAN)

    assert student_access_level(caller, OTHER, LINKED_S1, is_minor=is_minor) is None


def test_tutor_and_guardian_linked_to_one_and_assigned_to_another():
    caller = principal(identity.TUTOR, identity.GUARDIAN)
    facts = StudentAccessFacts(
        linked_student_ids=frozenset({S1}), assigned_student_ids=frozenset({S3})
    )

    assert student_access_level(caller, S1, facts, is_minor=True) == "full"
    assert student_access_level(caller, S3, facts, is_minor=True) == "limited"
    # An adult linked student: the guardian clause gives limited, like the
    # tutor clause; the highest level wins.
    assert student_access_level(caller, S1, facts, is_minor=False) == "limited"
    assert student_access_level(caller, OTHER, facts, is_minor=True) is None


@pytest.mark.parametrize("is_minor", [True, False])
def test_guardian_and_academic_coordinator_get_full_access_everywhere(is_minor):
    caller = principal(identity.GUARDIAN, identity.ACADEMIC_COORDINATOR)

    for student in (S1, S3, OTHER):
        assert student_access_level(caller, student, LINKED_S1, is_minor=is_minor) == (
            "full"
        )
    assert sees_student_guardians(caller)


def test_a_guardian_with_finance_staff_sees_the_linked_minor_in_full():
    caller = principal(identity.GUARDIAN, identity.FINANCE_STAFF)

    assert student_access_level(caller, S1, LINKED_S1, is_minor=True) == "full"
    assert student_access_level(caller, S3, LINKED_S1, is_minor=True) == "limited"
    assert student_access_level(caller, S1, LINKED_S1, is_minor=False) == "limited"
