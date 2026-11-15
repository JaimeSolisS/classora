"""Record visibility and IDOR for student profiles (#33 E4, F2-F4, F6).

Real logins; tutor assignments are injected through the F3 seam
(``student_support.assigned``). A hidden student must be indistinguishable
from a missing one (``assert_same_not_found``).
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.authz import Principal
from app.core.pagination import OffsetParams
from app.modules.families import service
from app.modules.families.assignments import NoTutorAssignments
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
from tests.authz.assertions import assert_same_not_found
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import (
    STUDENTS,
    assigned,
    create_student,
    student_path,
)

MISSING = "00000000-0000-4000-8000-000000000999"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(scope="module")
def students(client, owner) -> dict[str, dict[str, Any]]:
    """The module's only two students."""
    cookie = sign_in(client, owner.username)
    return {
        "A": create_student(client, cookie, given_names="Alicia", family_names="Uno"),
        "B": create_student(client, cookie, given_names="Bruno", family_names="Dos"),
    }


def user_with(url: str, *roles: str) -> CreatedUser:
    return create_user(url, roles=roles)


def listed(response) -> list[str]:
    assert code_of(response) == (200, None), response.text
    return [item["id"] for item in response.json()["items"]]


def detail_of(client, cookie, student) -> Any:
    return get(client, cookie, student_path(student["id"]))


def missing(client, cookie) -> Any:
    return get(client, cookie, student_path(MISSING))


# --- Tutors -----------------------------------------------------------------------


def test_a_tutor_without_assignments_sees_nothing(module_seeded_url, client, students):
    tutor = user_with(module_seeded_url, TUTOR)
    cookie = sign_in(client, tutor.username)

    for student in students.values():
        assert_same_not_found(
            detail_of(client, cookie, student), missing(client, cookie)
        )
    assert listed(get(client, cookie, STUDENTS)) == []
    assert listed(get(client, cookie, STUDENTS, q="Alicia")) == []
    assert listed(get(client, cookie, STUDENTS, status="all")) == []
    assert get(client, cookie, STUDENTS).json()["total"] == 0


def test_an_assigned_tutor_sees_only_that_student_and_limited(
    module_seeded_url, client, students
):
    tutor = user_with(module_seeded_url, TUTOR)
    cookie = sign_in(client, tutor.username)
    a, b = students["A"], students["B"]

    with assigned(client, {tutor.id: {a["id"]}}):
        shown = detail_of(client, cookie, a)
        hidden = detail_of(client, cookie, b)
        unknown = missing(client, cookie)
        everyone = get(client, cookie, STUDENTS, status="all")
        searched = get(client, cookie, STUDENTS, q="Bruno")
        searched_a = get(client, cookie, STUDENTS, q="Alicia")
        edit = patch(client, cookie, student_path(a["id"]), {"version": 1})

    assert shown.status_code == 200
    body = shown.json()
    assert body["personal"] is None
    assert body["given_names"] == "Alicia"
    assert_same_not_found(hidden, unknown)
    assert listed(everyone) == [a["id"]]
    assert everyone.json()["total"] == 1
    assert listed(searched) == []
    assert listed(searched_a) == [a["id"]]
    assert code_of(edit) == (403, "permission_denied")


def test_an_assignment_only_counts_for_its_tutor(module_seeded_url, client, students):
    tutor = user_with(module_seeded_url, TUTOR)
    other = user_with(module_seeded_url, TUTOR)
    cookie = sign_in(client, other.username)

    with assigned(client, {tutor.id: {students["A"]["id"]}}):
        assert_same_not_found(
            detail_of(client, cookie, students["A"]), missing(client, cookie)
        )
        assert listed(get(client, cookie, STUDENTS)) == []


def test_a_tutor_and_guardian_with_one_assignment_sees_exactly_it(
    module_seeded_url, client, students
):
    person = user_with(module_seeded_url, TUTOR, GUARDIAN)
    cookie = sign_in(client, person.username)

    with assigned(client, {person.id: {students["A"]["id"]}}):
        assert listed(get(client, cookie, STUDENTS, status="all")) == [
            students["A"]["id"]
        ]
        assert detail_of(client, cookie, students["A"]).json()["personal"] is None
        assert_same_not_found(
            detail_of(client, cookie, students["B"]), missing(client, cookie)
        )


# --- Guardians without a profile and students (facts arrive with #35) ------------
# Guardians with linked students: test_guardians_visibility.py (#34).


@pytest.mark.parametrize("role", [GUARDIAN, STUDENT])
def test_unlinked_guardians_and_student_users_see_nothing(
    module_seeded_url, client, students, role
):
    person = user_with(module_seeded_url, role)
    cookie = sign_in(client, person.username)

    for student in students.values():
        assert_same_not_found(
            detail_of(client, cookie, student), missing(client, cookie)
        )
    assert listed(get(client, cookie, STUDENTS, status="all")) == []


# --- Staff -------------------------------------------------------------------------


def test_finance_staff_see_every_student_with_limited_detail(
    module_seeded_url, client, owner, students
):
    finance = user_with(module_seeded_url, FINANCE_STAFF)
    cookie = sign_in(client, finance.username)
    owner_cookie = sign_in(client, owner.username)

    for student in students.values():
        response = detail_of(client, cookie, student)
        full = detail_of(client, owner_cookie, student).json()
        assert response.status_code == 200
        assert response.json() == {**full, "personal": None}
    assert {s["id"] for s in students.values()} <= set(
        listed(get(client, cookie, STUDENTS, limit=100))
    )
    assert code_of(missing(client, cookie)) == (404, "not_found")


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, ADMINISTRATOR, OWNER])
def test_managers_get_the_personal_part_and_may_edit(
    module_seeded_url, client, students, role
):
    manager = user_with(module_seeded_url, role)
    cookie = sign_in(client, manager.username)

    response = detail_of(client, cookie, students["A"])

    assert response.status_code == 200
    personal = response.json()["personal"]
    assert personal["birthdate"] == "2012-05-04"
    assert personal["emergency_contacts"][0]["name"] == "María Pérez"
    # Edit a student of this test only (the shared two stay unchanged).
    own = create_student(client, cookie, given_names="Zeta", family_names=role)
    edited = patch(
        client, cookie, student_path(own["id"]), {"version": 1, "school_grade": "1.º"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["personal"] is not None


def test_finance_staff_with_academic_coordinator_get_full_access(
    module_seeded_url, client, students
):
    person = user_with(module_seeded_url, FINANCE_STAFF, ACADEMIC_COORDINATOR)
    cookie = sign_in(client, person.username)

    assert detail_of(client, cookie, students["A"]).json()["personal"] is not None


def test_a_tutor_who_is_also_a_coordinator_sees_everything(
    module_seeded_url, client, students
):
    person = user_with(module_seeded_url, TUTOR, ACADEMIC_COORDINATOR)
    cookie = sign_in(client, person.username)

    with assigned(client, {}):
        assert {s["id"] for s in students.values()} <= set(
            listed(get(client, cookie, STUDENTS, limit=100))
        )
        assert detail_of(client, cookie, students["B"]).json()["personal"] is not None


# --- F3: facts are loaded only for the matching role -----------------------------


def test_staff_requests_never_query_assignments(module_seeded_url, client, students):
    finance = user_with(module_seeded_url, FINANCE_STAFF)
    tutor = user_with(module_seeded_url, TUTOR)
    finance_cookie = sign_in(client, finance.username)
    tutor_cookie = sign_in(client, tutor.username)

    with assigned(client, {}) as source:
        get(client, finance_cookie, STUDENTS)
        detail_of(client, finance_cookie, students["A"])
        assert source.calls == 0
        get(client, tutor_cookie, STUDENTS)
        assert source.calls == 1


def principal(*roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=uuid.uuid4(), roles=frozenset(roles), permissions=frozenset(permissions)
    )


@pytest.mark.parametrize(
    "roles", [(TUTOR,), (GUARDIAN,), (STUDENT,), (TUTOR, GUARDIAN)]
)
def test_an_empty_visible_set_returns_an_empty_page_without_more_sql(
    module_seeded_url, students, roles
):
    # A guardian's links are one statement (#34 F2, with the academic consents
    # in the same one, #35 F1), a student's own profile one more (#35 F1);
    # nothing else is sent.
    statements, page = count_statements(
        module_seeded_url,
        lambda session: service.list_students(
            session,
            principal=principal(*roles),
            source=NoTutorAssignments(),
            q="Alicia",
            status="all",
            education_level_id=None,
            page=OffsetParams(limit=25, offset=0),
        ),
    )

    assert statements == (GUARDIAN in roles) + (STUDENT in roles)
    assert (page.items, page.total) == ([], 0)
