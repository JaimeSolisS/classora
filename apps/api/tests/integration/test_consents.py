"""Consent records: list, record, revoke (#35 D).

Real logins through the real app. The world: minors S1, S2 and adult SA;
guardian user U1 with profile G1 (linked to S1 and SA), profile G2 without a
login (linked to S1 and SA), an inactive profile G3 (linked to S1 and SA),
profile G4 (not linked); student user USA linked to SA.
"""

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.conftest import CLOCK_START
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql
from tests.integration.consent_support import (
    consent_count,
    consent_rows,
    consents_of,
    give,
    given,
    revoke,
    set_account,
)
from tests.integration.guardian_support import create_guardian, linked, set_active
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import create_student, suffix

MINOR = "2014-03-01"
ADULT = "2000-01-01"
MISSING = "00000000-0000-4000-8000-000000000999"
ACCEPTANCE = "/api/v1/privacy-notice/acceptance"

FORBIDDEN = (403, "permission_denied")
NOT_FOUND = (404, "not_found")
METHOD_INVALID = (422, "consent_method_invalid")
GUARDIAN_INVALID = (422, "consent_guardian_invalid")
NOT_APPLICABLE = (422, "consent_not_applicable")


@dataclass
class World:
    s: dict[str, dict[str, Any]]
    g: dict[str, dict[str, Any]]
    u: dict[str, CreatedUser]


def build_world(url: str, client: TestClient, staff: Any) -> World:
    word = suffix()
    students = {
        name: create_student(
            client,
            staff,
            given_names=name,
            family_names=f"Consent {word}",
            birthdate=ADULT if name == "SA" else MINOR,
        )
        for name in ("S1", "S2", "SA")
    }
    users = {
        "U1": create_user(url, roles=(GUARDIAN,)),
        "USA": create_user(url, roles=(STUDENT,)),
    }
    guardians = {
        "G1": create_guardian(client, staff, user_id=str(users["U1"].id)),
        "G2": create_guardian(client, staff),
        "G3": create_guardian(client, staff),
        "G4": create_guardian(client, staff),
    }
    for student in ("S1", "SA"):
        for guardian in ("G1", "G2", "G3"):
            linked(client, staff, students[student]["id"], guardians[guardian]["id"])
    guardians["G3"] = set_active(client, staff, guardians["G3"], False)
    response = set_account(client, staff, students["SA"], users["USA"].id)
    assert response.status_code == 200, response.text
    students["SA"] = response.json()
    return World(s=students, g=guardians, u=users)


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
def world(module_seeded_url, client, staff) -> World:
    return build_world(module_seeded_url, client, staff)


def sid(world: World, name: str) -> str:
    return str(world.s[name]["id"])


def gid(world: World, name: str) -> str:
    return str(world.g[name]["id"])


# --- D1, D3: the staff row -----------------------------------------------------------


@pytest.mark.parametrize("staff_role", [OWNER, ADMINISTRATOR])
@pytest.mark.parametrize("method", ["paper", "in_person"])
def test_staff_record_both_scopes_on_paper_or_in_person(
    module_seeded_url, client, world, staff_role, method
):
    member = create_user(module_seeded_url, roles=(staff_role,))
    cookie = sign_in(client, member.username)

    login = given(client, cookie, sid(world, "S1"), gid(world, "G2"), method=method)
    access = given(
        client,
        cookie,
        sid(world, "SA"),
        gid(world, "G2"),
        scope="guardian_academic_access",
        method=method,
    )

    assert (login["scope"], login["method"]) == ("student_login", method)
    assert login["guardian_id"] == gid(world, "G2")
    assert login["notice_version"] == 1
    assert login["revoked_at"] is None
    assert login["can_revoke"] is True
    assert access["scope"] == "guardian_academic_access"
    [row] = [
        r
        for r in consent_rows(module_seeded_url, sid(world, "S1"))
        if str(r.id) == login["id"]
    ]
    assert row.method == method


def test_staff_may_not_record_in_app(client, staff, world):
    assert code_of(
        give(client, staff, sid(world, "S1"), gid(world, "G2"), method="in_app")
    ) == (METHOD_INVALID)


@pytest.mark.parametrize("guardian", ["G3", "G4"])
def test_an_inactive_or_unlinked_guardian_is_422(client, staff, world, guardian):
    response = give(client, staff, sid(world, "S1"), gid(world, guardian))

    assert code_of(response) == GUARDIAN_INVALID
    assert response.json()["errors"][0]["path"] == ["body", "guardian_id"]


def test_an_unknown_guardian_is_422(client, staff, world):
    assert code_of(give(client, staff, sid(world, "S1"), MISSING)) == GUARDIAN_INVALID


@pytest.mark.parametrize(
    ("student", "scope"),
    [("SA", "student_login"), ("S1", "guardian_academic_access")],
)
def test_a_consent_that_does_not_apply_at_the_age_is_422(
    client, staff, world, student, scope
):
    response = give(client, staff, sid(world, student), gid(world, "G2"), scope=scope)

    assert code_of(response) == NOT_APPLICABLE
    assert response.json()["errors"][0]["path"] == ["body", "scope"]


def test_an_unknown_student_is_404(client, staff, world):
    assert code_of(give(client, staff, MISSING, gid(world, "G2"))) == NOT_FOUND
    assert code_of(consents_of(client, staff, MISSING)) == NOT_FOUND
    assert code_of(revoke(client, staff, MISSING, MISSING)) == NOT_FOUND


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "everything", "guardian_id": MISSING, "method": "paper"},
        {"scope": "student_login", "guardian_id": MISSING, "method": "email"},
        {"scope": "student_login", "method": "paper"},
        {"scope": "student_login", "guardian_id": MISSING, "method": "paper", "x": 1},
    ],
)
def test_an_invalid_body_is_422(module_seeded_url, client, staff, world, body):
    before = consent_count(module_seeded_url)

    response = post(
        client, staff, f"/api/v1/students/{sid(world, 'S1')}/consents", body
    )

    assert code_of(response) == (422, "validation_error")
    assert consent_count(module_seeded_url) == before


# --- D1, D3: the guardian row --------------------------------------------------------


def test_a_guardian_allows_a_login_in_the_app(module_seeded_url, client, world):
    cookie = sign_in(client, world.u["U1"].username)

    record = given(client, cookie, sid(world, "S1"), gid(world, "G1"), method="in_app")

    assert record["method"] == "in_app"
    assert record["can_revoke"] is True
    [row] = consent_rows(module_seeded_url, sid(world, "S1"))
    assert str(row.guardian_id) == gid(world, "G1")


@pytest.mark.parametrize(
    ("student", "guardian", "scope", "method", "expected"),
    [
        ("S1", "G1", "student_login", "paper", METHOD_INVALID),
        ("S1", "G1", "student_login", "in_person", METHOD_INVALID),
        ("S1", "G2", "student_login", "in_app", FORBIDDEN),
        ("SA", "G1", "guardian_academic_access", "in_app", FORBIDDEN),
        ("SA", "G1", "student_login", "in_app", NOT_APPLICABLE),
        ("S2", "G1", "student_login", "in_app", NOT_FOUND),
    ],
)
def test_what_a_guardian_may_not_record(
    module_seeded_url, client, world, student, guardian, scope, method, expected
):
    cookie = sign_in(client, world.u["U1"].username)
    before = consent_count(module_seeded_url)

    response = give(
        client,
        cookie,
        sid(world, student),
        gid(world, guardian),
        scope=scope,
        method=method,
    )

    assert code_of(response) == expected
    assert consent_count(module_seeded_url) == before


def test_a_deactivated_guardian_profile_reaches_nothing(client, staff, world):
    set_active(client, staff, world.g["G1"], False)
    cookie = sign_in(client, world.u["U1"].username)

    assert code_of(consents_of(client, cookie, sid(world, "S1"))) == NOT_FOUND
    assert code_of(
        give(client, cookie, sid(world, "S1"), gid(world, "G1"), method="in_app")
    ) == (NOT_FOUND)


# --- D1, D3: the student row ---------------------------------------------------------


def test_an_adult_student_shares_with_one_guardian_in_the_app(client, world):
    cookie = sign_in(client, world.u["USA"].username)

    record = given(
        client,
        cookie,
        sid(world, "SA"),
        gid(world, "G1"),
        scope="guardian_academic_access",
        method="in_app",
    )

    assert record["scope"] == "guardian_academic_access"
    assert record["can_revoke"] is True


@pytest.mark.parametrize(
    ("guardian", "scope", "method", "expected"),
    [
        ("G1", "guardian_academic_access", "paper", METHOD_INVALID),
        ("G1", "student_login", "in_app", FORBIDDEN),
        ("G3", "guardian_academic_access", "in_app", GUARDIAN_INVALID),
        ("G4", "guardian_academic_access", "in_app", GUARDIAN_INVALID),
    ],
)
def test_what_a_student_may_not_record(
    module_seeded_url, client, world, guardian, scope, method, expected
):
    cookie = sign_in(client, world.u["USA"].username)
    before = consent_count(module_seeded_url)

    response = give(
        client,
        cookie,
        sid(world, "SA"),
        gid(world, guardian),
        scope=scope,
        method=method,
    )

    assert code_of(response) == expected
    assert consent_count(module_seeded_url) == before


def test_a_student_reaches_only_their_own_student(client, world):
    cookie = sign_in(client, world.u["USA"].username)

    for name in ("S1", "S2"):
        assert code_of(consents_of(client, cookie, sid(world, name))) == NOT_FOUND
        assert code_of(
            give(
                client,
                cookie,
                sid(world, name),
                gid(world, "G1"),
                scope="guardian_academic_access",
                method="in_app",
            )
        ) == (NOT_FOUND)


# --- D2: the overview ----------------------------------------------------------------


def test_the_overview_for_staff(client, staff, world):
    first = given(client, staff, sid(world, "S1"), gid(world, "G2"))
    revoked = code_of(revoke(client, staff, sid(world, "S1"), first["id"]))
    assert revoked == (200, None)
    second = given(client, staff, sid(world, "S1"), gid(world, "G2"))

    response = consents_of(client, staff, sid(world, "S1"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["student_id"] == sid(world, "S1")
    assert body["is_minor"] is True
    assert body["login_state"] is None
    assert body["current_notice_version"] == 1
    assert {g["guardian_id"] for g in body["guardians"]} == {
        gid(world, "G1"),
        gid(world, "G2"),
        gid(world, "G3"),
    }
    inactive = [g for g in body["guardians"] if g["guardian_id"] == gid(world, "G3")]
    assert inactive[0]["is_active"] is False
    # Active first, then by granted_at, newest first.
    assert [c["id"] for c in body["consents"]] == [second["id"], first["id"]]
    assert body["consents"][1]["revocation_cause"] == "revoked"
    assert body["consents"][1]["can_revoke"] is False
    assert body["can_record"] == ["student_login"]
    adult = consents_of(client, staff, sid(world, "SA")).json()
    assert adult["can_record"] == ["guardian_academic_access"]
    assert adult["login_state"] == "active"


def test_a_guardian_sees_only_themselves_and_their_own_records(client, staff, world):
    given(client, staff, sid(world, "S1"), gid(world, "G2"))
    mine = given(client, staff, sid(world, "S1"), gid(world, "G1"))
    cookie = sign_in(client, world.u["U1"].username)

    body = consents_of(client, cookie, sid(world, "S1")).json()

    assert [g["guardian_id"] for g in body["guardians"]] == [gid(world, "G1")]
    assert [c["id"] for c in body["consents"]] == [mine["id"]]
    assert body["can_record"] == ["student_login"]
    adult = consents_of(client, cookie, sid(world, "SA")).json()
    assert adult["can_record"] == []


def test_a_student_sees_their_guardians_and_every_record(client, staff, world):
    other = given(
        client,
        staff,
        sid(world, "SA"),
        gid(world, "G2"),
        scope="guardian_academic_access",
    )
    cookie = sign_in(client, world.u["USA"].username)

    body = consents_of(client, cookie, sid(world, "SA")).json()

    assert {g["guardian_id"] for g in body["guardians"]} == {
        gid(world, "G1"),
        gid(world, "G2"),
        gid(world, "G3"),
    }
    assert [c["id"] for c in body["consents"]] == [other["id"]]
    assert body["consents"][0]["can_revoke"] is True
    assert body["can_record"] == ["guardian_academic_access"]


# --- D3 step 7: duplicates -----------------------------------------------------------


def test_a_duplicate_is_409(module_seeded_url, client, staff, world):
    given(client, staff, sid(world, "S1"), gid(world, "G2"))

    assert code_of(give(client, staff, sid(world, "S1"), gid(world, "G2"))) == (
        409,
        "consent_already_given",
    )
    assert len(consent_rows(module_seeded_url, sid(world, "S1"))) == 1


def test_concurrent_duplicates_give_one_201_and_one_409(
    module_seeded_url, client, staff, world
):
    results: list[tuple[int, str | None]] = []
    start = threading.Barrier(2)

    def send() -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results.append(
                code_of(give(own_client, staff, sid(world, "S1"), gid(world, "G2")))
            )

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == sorted(
        [(201, None), (409, "consent_already_given")], key=str
    )
    assert len(consent_rows(module_seeded_url, sid(world, "S1"))) == 1


# --- D4: revoke ----------------------------------------------------------------------


def test_revoke_and_grant_again(module_seeded_url, client, owner, staff, world):
    record = given(client, staff, sid(world, "S1"), gid(world, "G2"))

    response = revoke(client, staff, sid(world, "S1"), record["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["student_login_suspended"] is False
    assert body["consent"]["id"] == record["id"]
    assert body["consent"]["revocation_cause"] == "revoked"
    assert body["consent"]["revoked_at"] is not None
    [row] = consent_rows(module_seeded_url, sid(world, "S1"))
    assert (row.revocation_cause, row.revoked_by_user_id) == ("revoked", owner.id)

    again = given(client, staff, sid(world, "S1"), gid(world, "G2"))
    assert again["id"] != record["id"]
    assert len(consent_rows(module_seeded_url, sid(world, "S1"))) == 2


def test_a_double_revoke_is_409(client, staff, world):
    record = given(client, staff, sid(world, "S1"), gid(world, "G2"))
    assert revoke(client, staff, sid(world, "S1"), record["id"]).status_code == 200

    assert code_of(revoke(client, staff, sid(world, "S1"), record["id"])) == (
        409,
        "consent_already_revoked",
    )


def test_a_record_of_another_student_is_404(client, staff, world):
    record = given(client, staff, sid(world, "S1"), gid(world, "G2"))

    assert code_of(revoke(client, staff, sid(world, "SA"), record["id"])) == NOT_FOUND


def test_who_may_revoke_what(client, staff, world):
    login_by_guardian = given(
        client,
        sign_in(client, world.u["U1"].username),
        sid(world, "S1"),
        gid(world, "G1"),
        method="in_app",
    )
    login_by_staff = given(client, staff, sid(world, "S1"), gid(world, "G2"))
    access_g1 = given(
        client,
        staff,
        sid(world, "SA"),
        gid(world, "G1"),
        scope="guardian_academic_access",
    )
    guardian = sign_in(client, world.u["U1"].username)
    student = sign_in(client, world.u["USA"].username)

    # A guardian can't see another guardian's record (404) nor revoke the
    # academic access given to them (403).
    assert (
        code_of(revoke(client, guardian, sid(world, "S1"), login_by_staff["id"]))
        == NOT_FOUND
    )
    assert (
        code_of(revoke(client, guardian, sid(world, "SA"), access_g1["id"]))
        == FORBIDDEN
    )
    # The student revokes academic access; a login consent isn't theirs (S1
    # is not their student either: 404).
    assert (
        code_of(revoke(client, student, sid(world, "S1"), login_by_guardian["id"]))
        == NOT_FOUND
    )
    assert code_of(revoke(client, student, sid(world, "SA"), access_g1["id"])) == (
        200,
        None,
    )
    assert code_of(
        revoke(client, guardian, sid(world, "S1"), login_by_guardian["id"])
    ) == (200, None)


# --- D3 step 8: the notice version ---------------------------------------------------


def test_a_new_notice_version_stamps_new_records_only(seeded_url, clock):
    with auth_client(seeded_url) as own_client:
        owner = create_user(seeded_url, roles=(OWNER,))
        cookie = sign_in(own_client, owner.username)
        world = build_world(seeded_url, own_client, cookie)
        first = given(own_client, cookie, sid(world, "S1"), gid(world, "G2"))
        run_sql(
            seeded_url,
            "INSERT INTO privacy_notice_versions (version, published_at, body_es,"
            " body_en) VALUES (2, :at, 'Borrador v2', 'Draft v2')",
            {"at": CLOCK_START + timedelta(days=1)},
        )
        clock.advance(timedelta(days=2))
        cookie = sign_in(own_client, owner.username)
        assert post(own_client, cookie, ACCEPTANCE, {"version": 2}).status_code == 200

        second = given(own_client, cookie, sid(world, "S1"), gid(world, "G1"))
        overview = consents_of(own_client, cookie, sid(world, "S1")).json()

    assert first["notice_version"] == 1
    assert second["notice_version"] == 2
    assert overview["current_notice_version"] == 2
    assert {c["id"]: c["notice_version"] for c in overview["consents"]} == {
        first["id"]: 1,
        second["id"]: 2,
    }


# --- Other roles ---------------------------------------------------------------------


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR])
def test_other_roles_are_denied_on_all_three_routes(
    module_seeded_url, client, staff, world, role
):
    record = given(client, staff, sid(world, "S1"), gid(world, "G2"))
    member = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, member.username)

    assert code_of(consents_of(client, cookie, sid(world, "S1"))) == FORBIDDEN
    assert code_of(give(client, cookie, sid(world, "S1"), gid(world, "G2"))) == (
        FORBIDDEN
    )
    assert code_of(revoke(client, cookie, sid(world, "S1"), record["id"])) == (
        FORBIDDEN
    )
