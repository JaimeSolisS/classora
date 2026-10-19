"""Linking a login account to a student profile (#35 C).

``PUT /api/v1/students/{student_id}/account`` with ``family.manage``. Real
logins through the real app; staff create the profiles over HTTP.
"""

import threading
import uuid
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
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql
from tests.integration.consent_support import account_path, given, put, set_account
from tests.integration.guardian_support import create_guardian, linked
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import create_student, student_path

MINOR = "2014-03-01"
ADULT = "2000-01-01"
MISSING = "00000000-0000-4000-8000-000000000999"


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


def user_id_of_student(url: str, student_id: Any) -> uuid.UUID | None:
    [row] = run_sql(
        url, "SELECT user_id FROM students WHERE id = :id", {"id": student_id}
    )
    return row.user_id  # type: ignore[no-any-return]


def ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_link_a_pending_account_relink_to_another_and_unlink(
    module_seeded_url, client, staff
):
    student = create_student(client, staff, birthdate=MINOR)
    first = create_user(module_seeded_url, roles=(STUDENT,), state="pending")
    second = create_user(module_seeded_url, roles=(), state="active")

    linked_detail = ok(set_account(client, staff, student, first.id))
    assert linked_detail["login"] == {
        "user_id": str(first.id),
        "username": first.username,
        "display_name": linked_detail["login"]["display_name"],
        "state": "pending",
    }
    assert linked_detail["version"] == student["version"] + 1
    assert user_id_of_student(module_seeded_url, student["id"]) == first.id

    # An active account without the student role: nothing to check yet.
    moved = ok(set_account(client, staff, linked_detail, second.id))
    assert moved["login"]["user_id"] == str(second.id)
    assert moved["login"]["state"] == "active"

    cleared = ok(set_account(client, staff, moved, None))
    assert cleared["login"] is None
    assert user_id_of_student(module_seeded_url, student["id"]) is None


def test_the_same_account_is_a_no_op(module_seeded_url, client, staff):
    student = create_student(client, staff)
    person = create_user(module_seeded_url, roles=(STUDENT,), state="pending")
    detail = ok(set_account(client, staff, student, person.id))

    again = ok(set_account(client, staff, detail, person.id))

    assert again["version"] == detail["version"]


@pytest.mark.parametrize("which", ["archived", "unknown"])
def test_an_unavailable_account_is_422(module_seeded_url, client, staff, which):
    student = create_student(client, staff)
    if which == "archived":
        target: Any = create_user(
            module_seeded_url, roles=(STUDENT,), state="archived"
        ).id
    else:
        target = MISSING

    response = set_account(client, staff, student, target)

    assert code_of(response) == (422, "student_account_unavailable")
    assert response.json()["errors"][0]["path"] == ["body", "user_id"]
    assert user_id_of_student(module_seeded_url, student["id"]) is None


def test_an_account_in_use_is_409(module_seeded_url, client, staff):
    person = create_user(module_seeded_url, roles=(STUDENT,), state="pending")
    first = create_student(client, staff)
    second = create_student(client, staff)
    ok(set_account(client, staff, first, person.id))

    assert code_of(set_account(client, staff, second, person.id)) == (
        409,
        "student_account_in_use",
    )
    assert user_id_of_student(module_seeded_url, second["id"]) is None


def test_concurrent_links_of_one_account_give_one_200_and_one_409(
    module_seeded_url, client, staff
):
    person = create_user(module_seeded_url, roles=(STUDENT,), state="pending")
    students = [create_student(client, staff) for _ in range(2)]
    results: list[tuple[int, str | None]] = []
    start = threading.Barrier(2)

    def link(student: dict[str, Any]) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results.append(code_of(set_account(own_client, staff, student, person.id)))

    threads = [threading.Thread(target=link, args=(s,)) for s in students]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == sorted(
        [(200, None), (409, "student_account_in_use")], key=str
    )
    [row] = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM students WHERE user_id = :id",
        {"id": person.id},
    )
    assert row.n == 1


def test_a_stale_version_is_409(module_seeded_url, client, staff):
    student = create_student(client, staff)
    person = create_user(module_seeded_url, roles=(STUDENT,), state="pending")

    response = set_account(
        client, staff, {**student, "version": student["version"] + 5}, person.id
    )

    assert code_of(response) == (409, "version_conflict")


def test_an_unknown_student_is_404(module_seeded_url, client, staff):
    person = create_user(module_seeded_url, roles=(STUDENT,), state="pending")

    response = put(
        client, staff, account_path(MISSING), {"user_id": str(person.id), "version": 1}
    )

    assert code_of(response) == (404, "not_found")


@pytest.mark.parametrize(
    "body",
    [
        {"version": 1},
        {"user_id": None},
        {"user_id": None, "version": 1, "extra": True},
        {"user_id": "nope", "version": 1},
    ],
)
def test_an_invalid_body_is_422(client, staff, body):
    student = create_student(client, staff)

    response = put(client, staff, account_path(student["id"]), body)

    assert code_of(response) == (422, "validation_error")


def test_an_active_minor_student_account_needs_a_consent(
    module_seeded_url, client, staff
):
    student = create_student(client, staff, birthdate=MINOR)
    person = create_user(module_seeded_url, roles=(STUDENT,))

    assert code_of(set_account(client, staff, student, person.id)) == (
        409,
        "guardian_consent_required",
    )
    assert user_id_of_student(module_seeded_url, student["id"]) is None

    guardian = create_guardian(client, staff)
    linked(client, staff, student["id"], guardian["id"])
    given(client, staff, student["id"], guardian["id"])
    detail = ok(set_account(client, staff, student, person.id))
    assert detail["login"]["state"] == "active"


def test_an_active_adult_student_account_links_without_consent(
    module_seeded_url, client, staff
):
    student = create_student(client, staff, birthdate=ADULT)
    person = create_user(module_seeded_url, roles=(STUDENT,))

    assert ok(set_account(client, staff, student, person.id))["login"]["state"] == (
        "active"
    )


def test_login_is_shown_only_to_family_managers(module_seeded_url, client, staff):
    student = create_student(client, staff)
    person = create_user(module_seeded_url, roles=(STUDENT,), state="pending")
    ok(set_account(client, staff, student, person.id))
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))

    for user, expected in ((admin, str(person.id)), (coordinator, None)):
        client.cookies.clear()
        cookie = sign_in(client, user.username)
        client.cookies.clear()
        detail = client.get(
            student_path(student["id"]),
            headers={"Cookie": f"{cookie.name}={cookie.value}"},
        ).json()
        login = detail["login"]
        assert (login["user_id"] if login else None) == expected


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, GUARDIAN, STUDENT]
)
def test_other_roles_are_denied(module_seeded_url, client, staff, role):
    student = create_student(client, staff)
    target = create_user(module_seeded_url, roles=(STUDENT,), state="pending")
    member = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, member.username)

    assert code_of(set_account(client, cookie, student, target.id)) == (
        403,
        "permission_denied",
    )
    assert user_id_of_student(module_seeded_url, student["id"]) is None
