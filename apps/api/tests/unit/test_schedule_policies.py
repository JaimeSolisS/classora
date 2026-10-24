"""The #67 record policies (``scheduling/permissions.py``): every role ×
relation, the adult with or without consent, and the tutor schedule."""

import uuid

import pytest

from app.core.authz import Principal
from app.modules.families.service import StudentAccessFacts
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from app.modules.scheduling.permissions import (
    ScheduleAccess,
    can_view_tutor_schedule,
    is_candidate,
    needs_age,
    student_schedule_access,
)

ME = uuid.uuid4()
CHILD = uuid.uuid4()
OWN = uuid.uuid4()
OTHER = uuid.uuid4()
STAFF = (OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF)


def principal(*roles: str, user_id: uuid.UUID = ME) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def facts(*, consent: bool = False, assigned: bool = False) -> StudentAccessFacts:
    return StudentAccessFacts(
        assigned_student_ids=frozenset({OTHER}) if assigned else frozenset(),
        linked_student_ids=frozenset({CHILD}),
        own_student_id=OWN,
        academic_consent_student_ids=frozenset({CHILD}) if consent else frozenset(),
    )


# --- C1: the family schedule ---------------------------------------------------------


@pytest.mark.parametrize(
    ("is_minor", "consent", "expected"),
    [
        (True, False, ScheduleAccess.ALLOWED),
        (True, True, ScheduleAccess.ALLOWED),
        (False, True, ScheduleAccess.ALLOWED),
        (False, False, ScheduleAccess.CONSENT_REQUIRED),
    ],
)
def test_a_linked_guardian(is_minor, consent, expected):
    caller = principal(GUARDIAN)
    known = facts(consent=consent)
    minor = is_minor if needs_age(caller, CHILD, known) else None

    assert student_schedule_access(caller, CHILD, known, is_minor=minor) is expected
    assert needs_age(caller, CHILD, known) is (not consent)


def test_a_linked_guardian_without_the_age_is_refused():
    """Fails closed: the age is needed and missing."""
    assert (
        student_schedule_access(principal(GUARDIAN), CHILD, facts(), is_minor=None)
        is ScheduleAccess.CONSENT_REQUIRED
    )


def test_a_student_sees_only_their_own_profile():
    caller = principal(STUDENT)

    assert (
        student_schedule_access(caller, OWN, facts(), is_minor=None)
        is ScheduleAccess.ALLOWED
    )
    for hidden in (CHILD, OTHER):
        assert (
            student_schedule_access(caller, hidden, facts(), is_minor=True)
            is ScheduleAccess.HIDDEN
        )
    assert not needs_age(caller, OWN, facts())


@pytest.mark.parametrize("role", STAFF)
def test_staff_with_schedule_view_see_every_student(role):
    caller = principal(role)

    for student in (CHILD, OWN, OTHER):
        assert (
            student_schedule_access(
                caller, student, StudentAccessFacts(), is_minor=None
            )
            is ScheduleAccess.ALLOWED
        )
        assert not needs_age(caller, student, facts())


def test_a_tutor_never_reads_a_family_schedule_through_an_assignment():
    caller = principal(TUTOR)

    for student in (OTHER, CHILD, OWN):
        assert (
            student_schedule_access(
                caller, student, facts(assigned=True), is_minor=True
            )
            is ScheduleAccess.HIDDEN
        )


def test_links_count_only_through_their_role():
    """A guardian's facts without the guardian role (or a profile without
    the student role) give nothing."""
    assert (
        student_schedule_access(principal(TUTOR), CHILD, facts(), is_minor=True)
        is ScheduleAccess.HIDDEN
    )
    assert (
        student_schedule_access(principal(GUARDIAN), OWN, facts(), is_minor=None)
        is ScheduleAccess.HIDDEN
    )


def test_with_several_roles_any_allowing_one_wins():
    caller = principal(GUARDIAN, STUDENT, TUTOR)

    assert is_candidate(caller, OWN, facts())
    assert is_candidate(caller, CHILD, facts())
    assert not is_candidate(caller, OTHER, facts(assigned=True))
    assert (
        student_schedule_access(caller, OWN, facts(), is_minor=None)
        is ScheduleAccess.ALLOWED
    )
    assert (
        student_schedule_access(caller, CHILD, facts(consent=True), is_minor=None)
        is ScheduleAccess.ALLOWED
    )


# --- B1: the tutor schedule ----------------------------------------------------------


def test_the_tutor_themselves():
    assert can_view_tutor_schedule(principal(TUTOR), ME)
    assert not can_view_tutor_schedule(principal(TUTOR), OTHER)


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR])
def test_staff_with_schedule_view(role):
    assert can_view_tutor_schedule(principal(role), OTHER)


@pytest.mark.parametrize("role", [GUARDIAN, STUDENT])
def test_families_never(role):
    assert not can_view_tutor_schedule(principal(role), OTHER)


def test_schedule_view_without_a_staff_role_is_not_enough():
    caller = Principal(
        user_id=ME, roles=frozenset({TUTOR}), permissions=frozenset({"schedule.view"})
    )

    assert not can_view_tutor_schedule(caller, OTHER)
    assert (
        student_schedule_access(caller, OTHER, StudentAccessFacts(), is_minor=None)
        is ScheduleAccess.HIDDEN
    )
