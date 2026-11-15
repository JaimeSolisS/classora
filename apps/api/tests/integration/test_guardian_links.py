"""Student-guardian links (#34 D) and the guardians on the student detail
(#34 E)."""

import threading
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    TUTOR,
)
from tests.authz.assertions import assert_same_not_found
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import get, patch, post
from tests.integration.guardian_support import (
    create_guardian,
    guardian_event_count,
    guardian_path,
    guardian_row,
    link,
    link_rows,
    linked,
    links_path,
    set_active,
    unlink,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import (
    assigned,
    create_student,
    student_path,
    student_row,
)

MISSING = "00000000-0000-4000-8000-000000000999"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


def guardians_of(client, cookie, student) -> list[dict[str, Any]]:
    response = get(client, cookie, student_path(student["id"]))
    assert response.status_code == 200, response.text
    guardians: list[dict[str, Any]] = response.json()["guardians"]
    return guardians


# --- D1/D2: link and unlink -------------------------------------------------------


def test_two_guardians_on_one_student(module_seeded_url, client, cookie):
    student = create_student(client, cookie)
    user = create_user(module_seeded_url, roles=(GUARDIAN,))
    g1 = create_guardian(
        client, cookie, given_names="Laura", family_names="Zeta", phone="55 1111 1111"
    )
    g2 = create_guardian(
        client, cookie, given_names="Pedro", family_names="Alfa", user_id=str(user.id)
    )

    first = link(client, cookie, student["id"], g1["id"], "mother")
    second = linked(client, cookie, student["id"], g2["id"], "father")

    assert first.status_code == 201, first.text
    assert first.json() == {
        "guardian_id": g1["id"],
        "given_names": "Laura",
        "family_names": "Zeta",
        "relationship": "mother",
        "phone": "55 1111 1111",
        "is_active": True,
        "has_account": False,
    }
    assert second["has_account"] is True
    # The family-name order of #33 E2: Alfa before Zeta.
    assert [g["guardian_id"] for g in guardians_of(client, cookie, student)] == [
        g2["id"],
        g1["id"],
    ]
    # A link changes no version.
    assert student_row(module_seeded_url, student["id"]).version == 1
    assert guardian_row(module_seeded_url, g1["id"]).version == 1


def test_one_guardian_with_two_children(client, cookie):
    s1, s2 = create_student(client, cookie), create_student(client, cookie)
    guardian = create_guardian(client, cookie)

    linked(client, cookie, s1["id"], guardian["id"], "mother")
    linked(client, cookie, s2["id"], guardian["id"], "legal_guardian")

    detail = get(client, cookie, guardian_path(guardian["id"])).json()
    assert {(s["student_id"], s["relationship"]) for s in detail["students"]} == {
        (s1["id"], "mother"),
        (s2["id"], "legal_guardian"),
    }
    assert all(s["is_minor"] and s["is_active"] for s in detail["students"])


def test_a_duplicate_link_is_409_and_writes_nothing(module_seeded_url, client, cookie):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    linked(client, cookie, student["id"], guardian["id"])
    events = guardian_event_count(module_seeded_url)

    response = link(client, cookie, student["id"], guardian["id"], "father")

    assert code_of(response) == (409, "guardian_already_linked")
    assert [r.relationship for r in link_rows(module_seeded_url, student["id"])] == [
        "mother"
    ]
    assert guardian_event_count(module_seeded_url) == events


def test_concurrent_duplicate_links_give_one_201_and_one_409(
    module_seeded_url, client, cookie, owner
):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    results: list[int] = []
    start = threading.Barrier(2)

    def send() -> None:
        with auth_client(module_seeded_url) as own_client:
            own_cookie = sign_in(own_client, owner.username)
            start.wait(timeout=10)
            response = link(own_client, own_cookie, student["id"], guardian["id"])
            results.append(response.status_code)

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == [201, 409]
    assert len(link_rows(module_seeded_url, student["id"])) == 1


def test_an_inactive_or_unknown_guardian_is_422(module_seeded_url, client, cookie):
    student = create_student(client, cookie)
    guardian = set_active(client, cookie, create_guardian(client, cookie), False)

    for guardian_id in (guardian["id"], MISSING):
        response = link(client, cookie, student["id"], guardian_id)
        body = response.json()
        assert (response.status_code, body["code"]) == (422, "guardian_unavailable")
        assert [e["path"] for e in body["errors"]] == [["body", "guardian_id"]]
    assert link_rows(module_seeded_url, student["id"]) == []


def test_an_inactive_student_can_be_linked(client, cookie):
    student = create_student(client, cookie)
    patch(
        client, cookie, student_path(student["id"]), {"version": 1, "is_active": False}
    )
    guardian = create_guardian(client, cookie)

    assert link(client, cookie, student["id"], guardian["id"]).status_code == 201


def test_an_unknown_student_is_404_before_the_guardian(client, cookie):
    response = link(client, cookie, MISSING, MISSING)

    assert code_of(response) == (404, "not_found")


@pytest.mark.parametrize(
    "body",
    [
        {"relationship": "aunt"},
        {"relationship": None},
        {"extra": True},
        {"guardian_id": "not-a-uuid"},
    ],
    ids=str,
)
def test_invalid_link_bodies_are_422(client, cookie, body):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    payload = {"guardian_id": guardian["id"], "relationship": "mother", **body}

    response = post(client, cookie, links_path(student["id"]), payload)

    assert code_of(response) == (422, "validation_error")


def test_unlink_then_unlink_again(module_seeded_url, client, cookie):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    linked(client, cookie, student["id"], guardian["id"])

    first = unlink(client, cookie, student["id"], guardian["id"])
    second = unlink(client, cookie, student["id"], guardian["id"])

    assert first.status_code == 204
    assert first.content == b""
    assert code_of(second) == (404, "not_found")
    assert link_rows(module_seeded_url, student["id"]) == []
    assert guardians_of(client, cookie, student) == []


def test_unknown_student_guardian_or_link_are_the_same_404(client, cookie):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)

    responses = [
        unlink(client, cookie, MISSING, guardian["id"]),
        unlink(client, cookie, student["id"], MISSING),
        unlink(client, cookie, student["id"], guardian["id"]),
    ]

    for response in responses[1:]:
        assert_same_not_found(response, responses[0])


@pytest.mark.parametrize(
    "roles",
    [(ACADEMIC_COORDINATOR,), (FINANCE_STAFF,), (TUTOR,), (GUARDIAN,)],
    ids=lambda r: "+".join(r),
)
def test_only_family_managers_link_and_unlink(module_seeded_url, client, cookie, roles):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    other = create_guardian(client, cookie)
    linked(client, cookie, student["id"], other["id"])
    person = sign_in(client, create_user(module_seeded_url, roles=roles).username)

    assert code_of(link(client, person, student["id"], guardian["id"])) == (
        403,
        "permission_denied",
    )
    assert code_of(unlink(client, person, student["id"], other["id"])) == (
        403,
        "permission_denied",
    )
    assert len(link_rows(module_seeded_url, student["id"])) == 1


def test_an_administrator_links_and_unlinks(module_seeded_url, client, cookie):
    admin = sign_in(
        client, create_user(module_seeded_url, roles=(ADMINISTRATOR,)).username
    )
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)

    assert link(client, admin, student["id"], guardian["id"]).status_code == 201
    assert unlink(client, admin, student["id"], guardian["id"]).status_code == 204


# --- E: the student detail's guardians --------------------------------------------


@pytest.fixture(scope="module")
def family(client, owner) -> dict[str, Any]:
    cookie = sign_in(client, owner.username)
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie, phone="55 2222 3333")
    linked(client, cookie, student["id"], guardian["id"], "grandparent")
    return {"student": student, "guardian": guardian}


@pytest.mark.parametrize(
    ("roles", "personal"),
    [
        ((OWNER,), True),
        ((ADMINISTRATOR,), True),
        ((ACADEMIC_COORDINATOR,), True),
        ((FINANCE_STAFF,), False),
    ],
    ids=lambda v: "+".join(v) if isinstance(v, tuple) else str(v),
)
def test_staff_see_the_guardians(module_seeded_url, client, family, roles, personal):
    person = sign_in(client, create_user(module_seeded_url, roles=roles).username)

    body = get(client, person, student_path(family["student"]["id"])).json()

    assert (body["personal"] is not None) is personal
    assert body["guardians"] == [
        {
            "guardian_id": family["guardian"]["id"],
            "given_names": family["guardian"]["given_names"],
            "family_names": family["guardian"]["family_names"],
            "relationship": "grandparent",
            "phone": "55 2222 3333",
            "is_active": True,
            "has_account": False,
        }
    ]


def test_an_assigned_tutor_gets_no_guardians(module_seeded_url, client, family):
    tutor = create_user(module_seeded_url, roles=(TUTOR,))
    person = sign_in(client, tutor.username)

    with assigned(client, {tutor.id: {family["student"]["id"]}}):
        body = get(client, person, student_path(family["student"]["id"])).json()

    assert (body["personal"], body["guardians"]) == (None, None)


def test_create_and_edit_responses_carry_the_guardians(client, cookie, family):
    created = create_student(client, cookie)
    assert created["guardians"] == []

    edited = patch(
        client,
        cookie,
        student_path(family["student"]["id"]),
        {"version": family["student"]["version"]},
    ).json()
    assert [g["guardian_id"] for g in edited["guardians"]] == [family["guardian"]["id"]]


def test_an_inactive_guardian_still_shows_on_the_student(client, cookie):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    linked(client, cookie, student["id"], guardian["id"])
    set_active(client, cookie, guardian, False)

    [item] = guardians_of(client, cookie, student)

    assert item["is_active"] is False
