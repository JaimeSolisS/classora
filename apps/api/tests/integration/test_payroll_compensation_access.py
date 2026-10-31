"""Who reads and writes tutor compensation (#105 D, E5, E6, J5).

Owner and Finance Staff read any tutor. A tutor reads their own, and gets the
identical ``404`` body for another tutor's id and for a random id. Everyone
else gets that ``404`` on the read and ``403`` on the list. Writes need
``payroll.manage``, and only an Owner may write their own pay.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PAYROLL_FINALIZE,
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    create_user,
)
from tests.integration.catalog_support import get, post
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of

ROOT = "/api/v1/payroll/tutors"
DENIED = (403, "permission_denied")
OWN = (403, "payroll_own_compensation")
RATE = {
    "class_kind": "individual",
    "effective_from": "2051-01-01",
    "rate_model": "per_hour",
    "rate": "250.0000",
}
TERMS = {
    "effective_from": "2051-01-01",
    "pay_frequency": "weekly",
    "salary_amount": None,
}
NO_READ = ("administrator", "delegated", "coordinator", "guardian", "student")


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def users(module_seeded_url: str) -> dict[str, CreatedUser]:
    url = module_seeded_url
    return {
        "owner": create_user(url, roles=(OWNER,)),
        "finance": create_user(url, roles=(FINANCE_STAFF,)),
        "tutor": create_user(url, roles=(TUTOR,)),
        "other_tutor": create_user(url, roles=(TUTOR,)),
        "administrator": create_user(url, roles=(ADMINISTRATOR,)),
        "delegated": create_user(
            url, roles=(ADMINISTRATOR,), delegations=[PAYROLL_FINALIZE]
        ),
        "coordinator": create_user(url, roles=(ACADEMIC_COORDINATOR,)),
        "guardian": create_user(url, roles=(GUARDIAN,)),
        "student": create_user(url, roles=(STUDENT,)),
        "finance_tutor": create_user(url, roles=(FINANCE_STAFF, TUTOR)),
        "owner_tutor": create_user(url, roles=(OWNER, TUTOR)),
    }


@pytest.fixture(scope="module")
def cookies(client: TestClient, users: dict[str, CreatedUser]) -> dict[str, SetCookie]:
    return {name: sign_in(client, user.username) for name, user in users.items()}


def read(client: TestClient, cookie: SetCookie, user: uuid.UUID) -> Response:
    return get(client, cookie, f"{ROOT}/{user}/compensation")


def without_request_id(response: Response) -> dict[str, Any]:
    return {
        key: value
        for key, value in response.json().items()
        if key not in ("request_id", "instance")
    }


@pytest.mark.parametrize("name", ["owner", "finance", "finance_tutor", "owner_tutor"])
def test_payroll_view_holders_read_any_tutor(client, users, cookies, name):
    assert read(client, cookies[name], users["tutor"].id).status_code == 200
    assert get(client, cookies[name], ROOT).status_code == 200


def test_a_tutor_reads_their_own(client, users, cookies):
    assert read(client, cookies["tutor"], users["tutor"].id).status_code == 200


def test_a_tutor_gets_the_same_404_for_anyone_else(client, users, cookies):
    cookie = cookies["tutor"]
    other = read(client, cookie, users["other_tutor"].id)
    missing = read(client, cookie, uuid.uuid4())

    assert code_of(other) == (404, "not_found")
    assert without_request_id(other) == without_request_id(missing)


@pytest.mark.parametrize("name", NO_READ)
def test_no_read_for_everyone_else(client, users, cookies, name):
    cookie = cookies[name]
    known = read(client, cookie, users["tutor"].id)
    missing = read(client, cookie, uuid.uuid4())

    assert code_of(known) == (404, "not_found")
    assert without_request_id(known) == without_request_id(missing)
    assert code_of(get(client, cookie, ROOT)) == DENIED


def test_a_tutor_has_no_list(client, cookies):
    assert code_of(get(client, cookies["tutor"], ROOT)) == DENIED


@pytest.mark.parametrize(
    "name",
    ["tutor", "administrator", "delegated", "coordinator", "guardian", "student"],
)
def test_writes_need_payroll_manage(client, users, cookies, name):
    cookie = cookies[name]
    target = users["other_tutor"].id
    row = uuid.uuid4()

    assert code_of(post(client, cookie, f"{ROOT}/{target}/rates", RATE)) == DENIED
    assert code_of(post(client, cookie, f"{ROOT}/{target}/pay-terms", TERMS)) == DENIED
    assert code_of(delete(client, cookie, f"{ROOT}/{target}/rates/{row}")) == DENIED
    assert code_of(delete(client, cookie, f"{ROOT}/{target}/pay-terms/{row}")) == DENIED


def test_a_tutor_cannot_write_their_own_either(client, users, cookies):
    target = users["tutor"].id
    response = post(client, cookies["tutor"], f"{ROOT}/{target}/rates", RATE)
    assert code_of(response) == DENIED


def test_finance_plus_tutor_writes_others_but_not_their_own(client, users, cookies):
    cookie = cookies["finance_tutor"]
    me = users["finance_tutor"].id
    other = users["other_tutor"].id
    row = uuid.uuid4()

    assert code_of(post(client, cookie, f"{ROOT}/{me}/rates", RATE)) == OWN
    assert code_of(post(client, cookie, f"{ROOT}/{me}/pay-terms", TERMS)) == OWN
    assert code_of(delete(client, cookie, f"{ROOT}/{me}/rates/{row}")) == OWN
    assert code_of(delete(client, cookie, f"{ROOT}/{me}/pay-terms/{row}")) == OWN
    assert read(client, cookie, me).status_code == 200
    made = post(client, cookie, f"{ROOT}/{other}/pay-terms", TERMS)
    assert made.status_code == 201


def test_an_owner_who_tutors_writes_their_own(client, users, cookies):
    cookie = cookies["owner_tutor"]
    me = users["owner_tutor"].id

    made = post(client, cookie, f"{ROOT}/{me}/rates", RATE)

    assert made.status_code == 201
    gone = delete(client, cookie, f"{ROOT}/{me}/rates/{made.json()['id']}")
    assert gone.status_code == 204


def test_a_tutor_sees_every_version_of_their_own(client, users, cookies):
    target = users["tutor"].id
    for on in ("2020-01-01", "2090-01-01"):
        body = {**RATE, "effective_from": on}
        post(client, cookies["finance"], f"{ROOT}/{target}/rates", body)

    rates = read(client, cookies["tutor"], target).json()["rates"]

    assert [row["status"] for row in rates] == ["upcoming", "current"]
