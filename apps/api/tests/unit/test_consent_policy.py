"""The consent record policy and the guardian academic-access fact (#35 D1,
F2, F3): pure functions, no I/O."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import get_args

import pytest

from app.core.authz import Principal
from app.core.clock import center_today
from app.modules.families import permissions as policy
from app.modules.families import schemas
from app.modules.families.ages import is_minor
from app.modules.families.constants import (
    CONSENT_METHODS,
    CONSENT_SCOPES,
    REVOCATION_CAUSES,
)
from app.modules.families.permissions import (
    GUARDIAN_ACADEMIC_ACCESS,
    STUDENT_LOGIN,
    ConsentContext,
    StudentAccessFacts,
    applies_at_age,
    can_revoke_consent,
    can_see_consent,
    can_see_consents_of,
    guardian_has_academic_access,
    recordable_scopes,
    rule_for,
    student_access_level,
)
from app.modules.identity import permissions as identity
from tests.conftest import frozen_clock

S = uuid.UUID("00000000-0000-4000-8000-00000000005a")
G1 = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
G2 = uuid.UUID("00000000-0000-4000-8000-0000000000a2")


def principal(*roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= identity.ROLE_PERMISSIONS[role]
    return Principal(
        user_id=uuid.uuid4(), roles=frozenset(roles), permissions=frozenset(permissions)
    )


OWNER = principal(identity.OWNER)
ADMIN = principal(identity.ADMINISTRATOR)
GUARDIAN = principal(identity.GUARDIAN)
STUDENT = principal(identity.STUDENT)
NOTHING = ConsentContext()
AS_GUARDIAN = ConsentContext(guardian_id=G1)
AS_STUDENT = ConsentContext(own_student=True)


# --- Catalog sync --------------------------------------------------------------------


def test_the_codes_match_the_catalog_and_the_schemas():
    assert policy.CONSENT_RECORD == identity.CONSENT_RECORD
    assert {
        role
        for role in identity.STAFF_ROLES
        if identity.CONSENT_RECORD in identity.ROLE_PERMISSIONS[role]
    } == policy.CONSENT_STAFF_ROLES
    assert CONSENT_SCOPES == get_args(schemas.ConsentScope.__value__)
    assert CONSENT_METHODS == get_args(schemas.ConsentMethod.__value__)
    assert REVOCATION_CAUSES == get_args(schemas.RevocationCause.__value__)
    assert (STUDENT_LOGIN, GUARDIAN_ACADEMIC_ACCESS) == CONSENT_SCOPES
    assert (policy.IN_APP, policy.PAPER, policy.IN_PERSON) == CONSENT_METHODS


# --- D1: who reaches a student's consents ---------------------------------------------


@pytest.mark.parametrize("who", [OWNER, ADMIN])
def test_staff_reach_every_student(who):
    assert can_see_consents_of(who, NOTHING)
    assert recordable_scopes(who, NOTHING) == set(CONSENT_SCOPES)


@pytest.mark.parametrize(
    "role",
    [identity.ACADEMIC_COORDINATOR, identity.FINANCE_STAFF, identity.TUTOR],
)
def test_other_staff_and_tutors_reach_nothing(role):
    who = principal(role)
    full = ConsentContext(guardian_id=G1, own_student=True)
    assert not can_see_consents_of(who, full)
    assert recordable_scopes(who, full) == frozenset()


def test_a_guardian_reaches_only_linked_students_and_a_student_only_their_own():
    assert can_see_consents_of(GUARDIAN, AS_GUARDIAN)
    assert not can_see_consents_of(GUARDIAN, NOTHING)
    assert not can_see_consents_of(GUARDIAN, AS_STUDENT)
    assert can_see_consents_of(STUDENT, AS_STUDENT)
    assert not can_see_consents_of(STUDENT, NOTHING)
    assert not can_see_consents_of(STUDENT, AS_GUARDIAN)


def test_an_academic_coordinator_who_is_also_a_guardian_is_not_staff_here():
    who = principal(identity.ACADEMIC_COORDINATOR, identity.GUARDIAN)
    assert not policy.is_consent_staff(who)
    assert not can_see_consents_of(who, NOTHING)
    assert recordable_scopes(who, AS_GUARDIAN) == {STUDENT_LOGIN}


# --- D1: scopes and methods ----------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "context", "scope", "method", "clause"),
    [
        (OWNER, NOTHING, STUDENT_LOGIN, "paper", "staff"),
        (OWNER, NOTHING, STUDENT_LOGIN, "in_person", "staff"),
        (ADMIN, NOTHING, GUARDIAN_ACADEMIC_ACCESS, "paper", "staff"),
        (ADMIN, NOTHING, GUARDIAN_ACADEMIC_ACCESS, "in_person", "staff"),
        (GUARDIAN, AS_GUARDIAN, STUDENT_LOGIN, "in_app", "guardian"),
        (STUDENT, AS_STUDENT, GUARDIAN_ACADEMIC_ACCESS, "in_app", "student"),
    ],
)
def test_allowed_scope_and_method(who, context, scope, method, clause):
    rule = rule_for(who, context, scope=scope, method=method)
    assert rule is not None
    assert rule.clause == clause


@pytest.mark.parametrize(
    ("who", "context", "scope", "method"),
    [
        (OWNER, NOTHING, STUDENT_LOGIN, "in_app"),
        (ADMIN, NOTHING, GUARDIAN_ACADEMIC_ACCESS, "in_app"),
        (GUARDIAN, AS_GUARDIAN, STUDENT_LOGIN, "paper"),
        (GUARDIAN, AS_GUARDIAN, STUDENT_LOGIN, "in_person"),
        (GUARDIAN, AS_GUARDIAN, GUARDIAN_ACADEMIC_ACCESS, "in_app"),
        (STUDENT, AS_STUDENT, GUARDIAN_ACADEMIC_ACCESS, "paper"),
        (STUDENT, AS_STUDENT, STUDENT_LOGIN, "in_app"),
    ],
)
def test_refused_scope_or_method(who, context, scope, method):
    assert rule_for(who, context, scope=scope, method=method) is None


def test_the_scopes_by_role():
    assert recordable_scopes(GUARDIAN, AS_GUARDIAN) == {STUDENT_LOGIN}
    assert recordable_scopes(STUDENT, AS_STUDENT) == {GUARDIAN_ACADEMIC_ACCESS}
    # A guardian who is also the student (an odd account) gets the union.
    both = principal(identity.GUARDIAN, identity.STUDENT)
    full = ConsentContext(guardian_id=G1, own_student=True)
    assert recordable_scopes(both, full) == set(CONSENT_SCOPES)


def test_a_multi_role_owner_and_guardian_may_also_use_in_app():
    who = principal(identity.OWNER, identity.GUARDIAN)
    rule = rule_for(who, AS_GUARDIAN, scope=STUDENT_LOGIN, method="in_app")
    assert rule is not None and rule.clause == "guardian"
    rule = rule_for(who, AS_GUARDIAN, scope=STUDENT_LOGIN, method="paper")
    assert rule is not None and rule.clause == "staff"


def test_the_age_rule():
    assert applies_at_age(STUDENT_LOGIN, is_minor=True)
    assert not applies_at_age(STUDENT_LOGIN, is_minor=False)
    assert applies_at_age(GUARDIAN_ACADEMIC_ACCESS, is_minor=False)
    assert not applies_at_age(GUARDIAN_ACADEMIC_ACCESS, is_minor=True)


# --- D1: which records are visible and revocable ------------------------------------


def test_record_visibility():
    for who, context in ((OWNER, NOTHING), (STUDENT, AS_STUDENT)):
        assert can_see_consent(who, context, guardian_id=G1)
        assert can_see_consent(who, context, guardian_id=G2)
    assert can_see_consent(GUARDIAN, AS_GUARDIAN, guardian_id=G1)
    assert not can_see_consent(GUARDIAN, AS_GUARDIAN, guardian_id=G2)
    assert not can_see_consent(GUARDIAN, NOTHING, guardian_id=G1)


@pytest.mark.parametrize(
    ("who", "context", "scope", "guardian_id", "allowed"),
    [
        (OWNER, NOTHING, STUDENT_LOGIN, G2, True),
        (ADMIN, NOTHING, GUARDIAN_ACADEMIC_ACCESS, G2, True),
        (GUARDIAN, AS_GUARDIAN, STUDENT_LOGIN, G1, True),
        (GUARDIAN, AS_GUARDIAN, STUDENT_LOGIN, G2, False),
        (GUARDIAN, AS_GUARDIAN, GUARDIAN_ACADEMIC_ACCESS, G1, False),
        (STUDENT, AS_STUDENT, GUARDIAN_ACADEMIC_ACCESS, G1, True),
        (STUDENT, AS_STUDENT, STUDENT_LOGIN, G1, False),
        (STUDENT, NOTHING, GUARDIAN_ACADEMIC_ACCESS, G1, False),
    ],
)
def test_who_revokes(who, context, scope, guardian_id, allowed):
    assert (
        can_revoke_consent(who, context, scope=scope, guardian_id=guardian_id)
        is allowed
    )


# --- F2, F3: the access level and the academic-access fact --------------------------

LINKED = StudentAccessFacts(linked_student_ids=frozenset({S}))
CONSENTED = StudentAccessFacts(
    linked_student_ids=frozenset({S}), academic_consent_student_ids=frozenset({S})
)


def test_a_linked_guardian_of_a_minor_gets_full_access():
    assert student_access_level(GUARDIAN, S, LINKED, is_minor=True) == "full"
    assert guardian_has_academic_access(GUARDIAN, S, is_minor=True, facts=LINKED)


def test_a_linked_guardian_of_an_adult_gets_full_only_with_their_consent():
    assert student_access_level(GUARDIAN, S, LINKED, is_minor=False) == "limited"
    assert not guardian_has_academic_access(GUARDIAN, S, is_minor=False, facts=LINKED)
    assert student_access_level(GUARDIAN, S, CONSENTED, is_minor=False) == "full"
    assert guardian_has_academic_access(GUARDIAN, S, is_minor=False, facts=CONSENTED)


def test_a_consent_for_another_guardian_only_leaves_this_one_limited():
    # The facts are per caller: a consent naming another guardian's profile
    # never reaches academic_consent_student_ids (E4 (a)).
    assert student_access_level(GUARDIAN, S, LINKED, is_minor=False) == "limited"


def test_a_consent_fact_without_the_link_grants_nothing():
    stale = StudentAccessFacts(academic_consent_student_ids=frozenset({S}))
    assert student_access_level(GUARDIAN, S, stale, is_minor=False) is None
    assert not guardian_has_academic_access(GUARDIAN, S, is_minor=False, facts=stale)


def test_only_the_guardian_role_uses_the_fact():
    tutor = principal(identity.TUTOR)
    assert not guardian_has_academic_access(tutor, S, is_minor=True, facts=CONSENTED)
    assert not guardian_has_academic_access(STUDENT, S, is_minor=True, facts=CONSENTED)


def test_the_student_themselves_gets_full_whatever_the_age():
    own = StudentAccessFacts(own_student_id=S)
    for minor in (True, False):
        assert student_access_level(STUDENT, S, own, is_minor=minor) == "full"
    other = StudentAccessFacts(own_student_id=G1)
    assert student_access_level(STUDENT, S, other, is_minor=False) is None


def test_a_guardian_who_is_also_a_student_gets_the_highest_level():
    both = principal(identity.GUARDIAN, identity.STUDENT)
    facts = StudentAccessFacts(linked_student_ids=frozenset({S}), own_student_id=G1)
    assert student_access_level(both, S, facts, is_minor=False) == "limited"
    assert student_access_level(both, G1, facts, is_minor=False) == "full"


def test_the_18th_birthday_at_mexico_city_midnight_ends_default_access():
    birthdate = date(2008, 10, 19)
    # 2026-10-19 00:00 in Mexico City is 06:00 UTC (no DST).
    with frozen_clock(datetime(2026, 10, 19, 5, 59, tzinfo=UTC)) as clock:
        assert is_minor(birthdate, center_today())
        assert student_access_level(GUARDIAN, S, LINKED, is_minor=True) == "full"
        clock.advance(timedelta(minutes=1))
        minor = is_minor(birthdate, center_today())
        assert not minor
        assert student_access_level(GUARDIAN, S, LINKED, is_minor=minor) == "limited"
