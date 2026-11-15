"""The family record policy of session requests (#59 S1, S2, E3)."""

import uuid

import pytest

from app.core.authz import Principal
from app.modules.families.service import StudentAccessFacts
from app.modules.scheduling.requests_policy import (
    FamilyAccess,
    candidate_student_ids,
    family_access,
    has_family_role,
    needs_age,
)

CHILD = uuid.uuid4()
OTHER = uuid.uuid4()
OWN = uuid.uuid4()


def principal(*roles: str, permissions: frozenset[str] = frozenset()) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), roles=frozenset(roles), permissions=permissions
    )


def facts(
    *,
    linked: frozenset[uuid.UUID] = frozenset(),
    consented: frozenset[uuid.UUID] = frozenset(),
    own: uuid.UUID | None = None,
) -> StudentAccessFacts:
    return StudentAccessFacts(
        linked_student_ids=linked,
        academic_consent_student_ids=consented,
        own_student_id=own,
    )


def access(
    caller: Principal, student: uuid.UUID, known: StudentAccessFacts, minor: bool | None
) -> FamilyAccess:
    return family_access(caller, student, known, is_minor=minor)


def test_a_guardian_acts_for_a_linked_minor_child():
    guardian = principal("guardian")
    known = facts(linked=frozenset({CHILD}))

    assert needs_age(guardian, CHILD, known)
    assert access(guardian, CHILD, known, True) is FamilyAccess.ALLOWED


def test_a_guardian_of_an_adult_needs_the_academic_consent():
    guardian = principal("guardian")
    without = facts(linked=frozenset({CHILD}))
    with_consent = facts(linked=frozenset({CHILD}), consented=frozenset({CHILD}))

    assert access(guardian, CHILD, without, False) is FamilyAccess.CONSENT_REQUIRED
    assert not needs_age(guardian, CHILD, with_consent)
    assert access(guardian, CHILD, with_consent, None) is FamilyAccess.ALLOWED


def test_a_missing_age_fails_closed():
    guardian = principal("guardian")

    assert (
        access(guardian, CHILD, facts(linked=frozenset({CHILD})), None)
        is FamilyAccess.CONSENT_REQUIRED
    )


def test_an_unlinked_student_is_hidden_without_reading_the_age():
    guardian = principal("guardian")
    known = facts(linked=frozenset({CHILD}))

    assert not needs_age(guardian, OTHER, known)
    assert access(guardian, OTHER, known, None) is FamilyAccess.HIDDEN


def test_a_student_acts_only_for_their_own_profile():
    student = principal("student")
    known = facts(own=OWN)

    assert not needs_age(student, OWN, known)
    assert access(student, OWN, known, None) is FamilyAccess.ALLOWED
    assert access(student, OTHER, known, None) is FamilyAccess.HIDDEN


def test_links_count_only_through_the_role():
    # Facts without the role (a removed guardian role, a login linked to a
    # profile without the student role) grant nothing.
    known = facts(linked=frozenset({CHILD}), own=OWN)
    tutor = principal("tutor")

    assert access(tutor, CHILD, known, True) is FamilyAccess.HIDDEN
    assert access(tutor, OWN, known, None) is FamilyAccess.HIDDEN
    assert candidate_student_ids(tutor, known) == frozenset()


@pytest.mark.parametrize(
    "roles",
    [
        ("owner",),
        ("administrator",),
        ("academic_coordinator",),
        ("finance_staff",),
        ("tutor",),
    ],
)
def test_staff_and_tutors_act_for_no_student(roles):
    caller = principal(
        *roles, permissions=frozenset({"student.view", "schedule.manage"})
    )

    assert not has_family_role(caller)
    assert access(caller, CHILD, facts(), True) is FamilyAccess.HIDDEN


def test_an_inactive_guardian_profile_has_no_links():
    # load_access_facts reads only the active profile's links.
    guardian = principal("guardian")

    assert access(guardian, CHILD, facts(), True) is FamilyAccess.HIDDEN


def test_a_multi_role_user_gets_the_union():
    both = principal("guardian", "student")
    known = facts(linked=frozenset({CHILD}), own=OWN)

    assert has_family_role(both)
    assert candidate_student_ids(both, known) == {CHILD, OWN}
    assert access(both, OWN, known, None) is FamilyAccess.ALLOWED
    assert access(both, CHILD, known, True) is FamilyAccess.ALLOWED
    assert access(both, CHILD, known, False) is FamilyAccess.CONSENT_REQUIRED


def test_an_adult_student_linked_to_their_own_guardian_login_acts_for_themselves():
    # The own-profile rule wins over the guardian consent rule.
    both = principal("guardian", "student")
    known = facts(linked=frozenset({OWN}), own=OWN)

    assert not needs_age(both, OWN, known)
    assert access(both, OWN, known, False) is FamilyAccess.ALLOWED
