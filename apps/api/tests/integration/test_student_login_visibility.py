"""Student logins and adult consent in the record policies, and IDOR (#35 F).

Real logins through the #21/#23 harness; a hidden student or record must be
indistinguishable from a missing one (``assert_same_not_found``). Facts load
on every request, so a consent given or revoked applies on the guardian's
next request in the same session.

The world: minors S1, S2; adult SA; SB, 18 tomorrow; guardian users U1 (G1)
and U2 (G2); student users US1 (S1) and USA (SA). Links G1–S1, G1–SA, G2–S2,
G2–SA, G2–SB. S1's login rests on G1's paper consent.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.authz import Principal
from app.modules.families import service
from app.modules.families.assignments import NoTutorAssignments
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from tests.authz.assertions import assert_same_not_found
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.consent_support import (
    consents_of,
    give,
    given,
    revoke,
    set_account,
)
from tests.integration.guardian_support import GUARDIANS, create_guardian, linked
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import (
    STUDENTS,
    create_student,
    student_path,
    suffix,
)

MISSING = "00000000-0000-4000-8000-000000000999"
BIRTHDATES = {
    "S1": "2014-03-01",
    "S2": "2013-07-15",
    "SA": "2000-01-01",
    "SB": "2008-10-19",  # 18 on 2026-10-19 (the clock starts on the 18th)
}


@dataclass
class World:
    s: dict[str, dict[str, Any]]
    u: dict[str, CreatedUser]
    g: dict[str, dict[str, Any]]
    login_consent: dict[str, Any]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def staff(client, owner):
    return sign_in(client, owner.username)


@pytest.fixture
def world(module_seeded_url, client, staff, clock) -> World:
    word = suffix()
    students = {
        name: create_student(
            client,
            staff,
            given_names=name,
            family_names=f"Login {word}",
            birthdate=birthdate,
        )
        for name, birthdate in BIRTHDATES.items()
    }
    users = {
        "U1": create_user(module_seeded_url, roles=(GUARDIAN,)),
        "U2": create_user(module_seeded_url, roles=(GUARDIAN,)),
        "US1": create_user(module_seeded_url, roles=(STUDENT,)),
        "USA": create_user(module_seeded_url, roles=(STUDENT,)),
    }
    guardians = {
        "G1": create_guardian(client, staff, user_id=str(users["U1"].id)),
        "G2": create_guardian(client, staff, user_id=str(users["U2"].id)),
    }
    for student, guardian in (
        ("S1", "G1"),
        ("SA", "G1"),
        ("S2", "G2"),
        ("SA", "G2"),
        ("SB", "G2"),
    ):
        linked(client, staff, students[student]["id"], guardians[guardian]["id"])
    consent = given(client, staff, students["S1"]["id"], guardians["G1"]["id"])
    for student, user in (("S1", "US1"), ("SA", "USA")):
        response = set_account(client, staff, students[student], users[user].id)
        assert response.status_code == 200, response.text
        students[student] = response.json()
    return World(s=students, u=users, g=guardians, login_consent=consent)


def detail(client: TestClient, cookie: Any, student_id: Any) -> Any:
    return get(client, cookie, student_path(student_id))


def listed(client: TestClient, cookie: Any) -> set[str]:
    response = get(client, cookie, STUDENTS, status="all", limit=100)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def level(client: TestClient, cookie: Any, student_id: Any) -> str:
    response = detail(client, cookie, student_id)
    assert response.status_code == 200, response.text
    return "full" if response.json()["personal"] is not None else "limited"


def test_a_student_sees_only_their_own_record(client, staff, world):
    us1 = sign_in(client, world.u["US1"].username)
    s2 = world.s["S2"]["id"]
    s2_record = given(client, staff, s2, world.g["G2"]["id"])

    assert listed(client, us1) == {world.s["S1"]["id"]}
    own = detail(client, us1, world.s["S1"]["id"]).json()
    assert own["personal"]["birthdate"] == BIRTHDATES["S1"]
    assert own["guardians"] is None
    assert own["login"] is None
    assert own["family"] is None
    assert_same_not_found(detail(client, us1, s2), detail(client, us1, MISSING))
    assert_same_not_found(
        consents_of(client, us1, s2), consents_of(client, us1, MISSING)
    )
    assert_same_not_found(
        revoke(client, us1, s2, s2_record["id"]), revoke(client, us1, MISSING, MISSING)
    )
    assert code_of(get(client, us1, GUARDIANS)) == (403, "permission_denied")


def test_a_student_sees_the_login_consent_of_their_record_but_cannot_revoke_it(
    client, world
):
    us1 = sign_in(client, world.u["US1"].username)

    body = consents_of(client, us1, world.s["S1"]["id"]).json()

    assert [c["id"] for c in body["consents"]] == [world.login_consent["id"]]
    assert body["consents"][0]["can_revoke"] is False
    assert body["can_record"] == []
    assert code_of(
        revoke(client, us1, world.s["S1"]["id"], world.login_consent["id"])
    ) == (403, "permission_denied")


def test_what_a_guardian_may_record_and_see(client, staff, world):
    u1 = sign_in(client, world.u["U1"].username)
    g1, g2 = world.g["G1"]["id"], world.g["G2"]["id"]
    # G2's academic access on SA, which U1 must never see.
    given(client, staff, world.s["SA"]["id"], g2, scope="guardian_academic_access")
    own_sa = given(
        client, staff, world.s["SA"]["id"], g1, scope="guardian_academic_access"
    )

    assert_same_not_found(
        give(client, u1, world.s["S2"]["id"], g1, method="in_app"),
        give(client, u1, MISSING, g1, method="in_app"),
    )
    assert code_of(give(client, u1, world.s["S1"]["id"], g2, method="in_app")) == (
        403,
        "permission_denied",
    )
    assert code_of(give(client, u1, world.s["SA"]["id"], g1, method="in_app")) == (
        422,
        "consent_not_applicable",
    )
    body = consents_of(client, u1, world.s["SA"]["id"]).json()
    assert [c["id"] for c in body["consents"]] == [own_sa["id"]]
    u2 = sign_in(client, world.u["U2"].username)
    assert_same_not_found(
        revoke(client, u2, world.s["SA"]["id"], own_sa["id"]),
        revoke(client, u2, world.s["SA"]["id"], MISSING),
    )


def test_an_adults_consent_opens_full_access_for_that_guardian_only(client, world):
    u1 = sign_in(client, world.u["U1"].username)
    u2 = sign_in(client, world.u["U2"].username)
    usa = sign_in(client, world.u["USA"].username)
    sa = world.s["SA"]["id"]
    assert (level(client, u1, sa), level(client, u2, sa)) == ("limited", "limited")

    record = given(
        client,
        usa,
        sa,
        world.g["G1"]["id"],
        scope="guardian_academic_access",
        method="in_app",
    )

    assert (level(client, u1, sa), level(client, u2, sa)) == ("full", "limited")
    assert revoke(client, usa, sa, record["id"]).status_code == 200
    # The same session sees the change on its next request.
    assert level(client, u1, sa) == "limited"


def test_the_18th_birthday_ends_the_guardians_default_access(client, world, clock):
    u2 = sign_in(client, world.u["U2"].username)
    sb = world.s["SB"]["id"]
    assert level(client, u2, sb) == "full"

    clock.set(datetime(2026, 10, 19, 6, 0, tzinfo=UTC))  # Mexico City midnight
    u2 = sign_in(client, world.u["U2"].username)

    assert level(client, u2, sb) == "limited"
    assert detail(client, u2, sb).json()["is_minor"] is False


@pytest.mark.parametrize("role", [TUTOR, FINANCE_STAFF, ACADEMIC_COORDINATOR])
def test_staff_without_consent_record_and_tutors_get_403(
    module_seeded_url, client, world, role
):
    member = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, member.username)
    s1 = world.s["S1"]["id"]

    for response in (
        consents_of(client, cookie, s1),
        give(client, cookie, s1, world.g["G1"]["id"]),
        revoke(client, cookie, s1, world.login_consent["id"]),
    ):
        assert code_of(response) == (403, "permission_denied")


def principal_of(user: CreatedUser, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user.id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


@pytest.mark.parametrize(
    ("roles", "statements"),
    [((GUARDIAN,), 1), ((STUDENT,), 1), ((GUARDIAN, STUDENT), 2), ((OWNER,), 0)],
)
def test_the_facts_cost_one_statement_per_role(
    module_seeded_url, world, roles, statements
):
    # The academic consents come in the same statement as the links (F1).
    user = world.u["U1"] if GUARDIAN in roles else world.u["USA"]

    count, facts = count_statements(
        module_seeded_url,
        lambda session: service.load_access_facts(
            session, principal_of(user, *roles), NoTutorAssignments()
        ),
    )

    assert count == statements
    if GUARDIAN in roles:
        assert {str(i) for i in facts.linked_student_ids} == {
            world.s["S1"]["id"],
            world.s["SA"]["id"],
        }
        assert facts.academic_consent_student_ids == frozenset()
    if roles == (STUDENT,):
        assert str(facts.own_student_id) == world.s["SA"]["id"]
