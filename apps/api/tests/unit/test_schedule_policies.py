"""The #67 record policies (``scheduling/permissions.py``): every role ×
relation, the adult with or without consent, and the tutor schedule; and
the #246 group and cohort policies (A1-A3)."""

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
    STAFF_VIEWER,
    TAUGHT_ASSIGNMENT_STATUSES,
    RecordViewer,
    ScheduleAccess,
    ages_needed,
    can_view_tutor_schedule,
    is_candidate,
    needs_age,
    student_schedule_access,
    teaches,
    teaching_tutor_id,
    visible_family_students,
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


# --- #246 A1: the visible students of a group or cohort read -------------------------

ADULT = uuid.uuid4()
CONSENTING = uuid.uuid4()


def group_facts(*, own: bool = True) -> StudentAccessFacts:
    """CHILD (a minor), ADULT (no consent) and CONSENTING (consent) are
    linked; OWN is the caller's profile."""
    return StudentAccessFacts(
        assigned_student_ids=frozenset({OTHER}),
        linked_student_ids=frozenset({CHILD, ADULT, CONSENTING}),
        own_student_id=OWN if own else None,
        academic_consent_student_ids=frozenset({CONSENTING}),
    )


MINORS = frozenset({CHILD})


def test_a1_a_guardian_sees_minors_and_consenting_adults_only():
    caller = principal(GUARDIAN)

    assert visible_family_students(caller, group_facts(), minors=MINORS) == {
        CHILD,
        CONSENTING,
    }
    assert ages_needed(caller, group_facts()) == {CHILD, ADULT}


def test_a1_without_the_age_an_adult_without_consent_stays_hidden():
    """Fails closed: no minors known, only the consenting adult."""
    assert visible_family_students(
        principal(GUARDIAN), group_facts(), minors=frozenset()
    ) == {CONSENTING}


def test_a1_a_student_sees_only_their_own_profile():
    caller = principal(STUDENT)

    assert visible_family_students(caller, group_facts(), minors=MINORS) == {OWN}
    assert ages_needed(caller, group_facts()) == frozenset()
    assert visible_family_students(caller, group_facts(own=False), minors=MINORS) == (
        frozenset()
    )


def test_a1_both_family_roles_see_the_union():
    caller = principal(GUARDIAN, STUDENT)

    assert visible_family_students(caller, group_facts(), minors=MINORS) == {
        OWN,
        CHILD,
        CONSENTING,
    }


@pytest.mark.parametrize("role", [TUTOR, *STAFF])
def test_a1_links_count_only_through_a_family_role(role):
    """A tutor's assignments never make a student visible here."""
    caller = principal(role)

    assert visible_family_students(caller, group_facts(), minors=MINORS) == (
        frozenset()
    )
    assert ages_needed(caller, group_facts()) == frozenset()


# --- #246 A2: taught series -----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [("pending", True), ("accepted", True), (None, False), ("declined", False)],
)
def test_a2_the_current_tutor_with_a_pending_or_accepted_assignment(status, expected):
    assert teaches(ME, series_tutor_id=ME, assignment_status=status) is expected
    assert TAUGHT_ASSIGNMENT_STATUSES == {"pending", "accepted"}


def test_a2_another_tutor_or_no_tutor_role_never_teaches():
    assert not teaches(ME, series_tutor_id=OTHER, assignment_status="accepted")
    assert not teaches(ME, series_tutor_id=None, assignment_status=None)
    assert not teaches(None, series_tutor_id=ME, assignment_status="accepted")
    assert teaching_tutor_id(principal(TUTOR)) == ME
    assert teaching_tutor_id(principal(TUTOR, GUARDIAN)) == ME
    for role in (GUARDIAN, STUDENT, *STAFF):
        assert teaching_tutor_id(principal(role)) is None


# --- #246 A3: who reads as what -------------------------------------------------------


def test_a3_the_viewer_facts():
    """Staff see everything; anyone else needs a visible student or the
    tutor role (the SQL of A3 is tested in the integration tests)."""
    assert not STAFF_VIEWER.sees_nothing
    assert RecordViewer(staff=False).sees_nothing
    assert not RecordViewer(staff=False, students=frozenset({CHILD})).sees_nothing
    assert not RecordViewer(staff=False, tutor_id=ME).sees_nothing
