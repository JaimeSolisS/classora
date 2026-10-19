"""Creating student profiles over HTTP with real logins (#33 C1-C5).

The tests share one seeded database and scope their rows with unique family
names.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
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
from tests.integration.catalog_support import (
    LEVELS,
    create_level,
    get,
    level_path,
    patch,
    post,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import (
    STUDENTS,
    contact,
    contact_rows,
    create_student,
    family_event_count,
    student_body,
    student_count,
    student_path,
)

DETAIL_FIELDS = {
    "id",
    "given_names",
    "family_names",
    "is_active",
    "is_minor",
    "school_name",
    "school_grade",
    "education_level",
    "personal",
    "guardians",
    "version",
    "created_at",
    "updated_at",
}


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


@pytest.fixture(scope="module")
def secundaria(client, owner) -> dict[str, Any]:
    cookie = sign_in(client, owner.username)
    items = get(client, cookie, LEVELS, limit=100).json()["items"]
    return next(level for level in items if level["name"] == "Secundaria")


@pytest.fixture
def inactive_level(client, cookie) -> dict[str, Any]:
    level = create_level(client, cookie, sort_order=9000)
    response = patch(
        client, cookie, level_path(level["id"]), {"version": 1, "is_active": False}
    )
    assert response.status_code == 200, response.text
    detail: dict[str, Any] = response.json()
    return detail


def test_create_with_only_the_required_fields(client, cookie):
    body = student_body()

    response = post(client, cookie, STUDENTS, body)

    assert response.status_code == 201, response.text
    detail = response.json()
    assert set(detail) == DETAIL_FIELDS
    assert detail["given_names"] == "José"
    assert detail["family_names"] == body["family_names"]
    assert detail["version"] == 1
    assert detail["is_active"] is True
    assert detail["is_minor"] is True
    assert detail["school_name"] is None
    assert detail["school_grade"] is None
    assert detail["education_level"] is None
    [only] = detail["personal"]["emergency_contacts"]
    assert detail["personal"] == {
        "birthdate": "2012-05-04",
        "email": None,
        "phone": None,
        "emergency_contacts": [
            {
                "id": only["id"],
                "name": "María Pérez",
                "relationship": "Madre",
                "phone": "+52 55 1234 5678",
                "position": 1,
            }
        ],
    }
    assert get(client, cookie, student_path(detail["id"])).json() == detail


def test_create_with_every_field_and_five_contacts(
    module_seeded_url, client, cookie, secundaria
):
    contacts = [
        contact(f"Contacto {n}", f"Relación {n}", f"55 1000 000{n}")
        for n in range(1, 6)
    ]

    detail = create_student(
        client,
        cookie,
        given_names="  José María ",
        family_names=" García López ",
        birthdate="2000-02-29",
        email=" jose@example.com ",
        phone="+52 (55) 1234-5678",
        school_name=" Escuela Secundaria Técnica 1 ",
        school_grade="2.º de secundaria",
        education_level_id=secundaria["id"],
        emergency_contacts=contacts,
    )

    assert detail["given_names"] == "José María"
    assert detail["family_names"] == "García López"
    assert detail["is_minor"] is False
    assert detail["school_name"] == "Escuela Secundaria Técnica 1"
    assert detail["school_grade"] == "2.º de secundaria"
    assert detail["education_level"] == {
        "id": secundaria["id"],
        "name": "Secundaria",
        "is_active": True,
    }
    personal = detail["personal"]
    assert personal["email"] == "jose@example.com"
    assert personal["phone"] == "+52 (55) 1234-5678"
    assert [
        (c["name"], c["relationship"], c["phone"], c["position"])
        for c in personal["emergency_contacts"]
    ] == [(f"Contacto {n}", f"Relación {n}", f"55 1000 000{n}", n) for n in range(1, 6)]
    assert [r.position for r in contact_rows(module_seeded_url, detail["id"])] == [
        1,
        2,
        3,
        4,
        5,
    ]


def test_empty_optional_strings_are_stored_as_null(client, cookie):
    detail = create_student(
        client, cookie, email="", phone="  ", school_name="", school_grade=" "
    )

    assert detail["school_name"] is None
    assert detail["school_grade"] is None
    assert detail["personal"]["email"] is None
    assert detail["personal"]["phone"] is None


def test_duplicates_are_allowed(client, cookie):
    body = student_body()

    first = post(client, cookie, STUDENTS, body)
    second = post(client, cookie, STUDENTS, body)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["id"] != second.json()["id"]


INVALID_BODIES = {
    "no contacts": {"emergency_contacts": []},
    "six contacts": {"emergency_contacts": [contact() for _ in range(6)]},
    "blank given names": {"given_names": "   "},
    "blank family names": {"family_names": ""},
    "long given names": {"given_names": "x" * 101},
    "bad phone": {"phone": "phone-ext"},
    "bad email": {"email": "a b@c.d"},
    "bad contact phone": {"emergency_contacts": [contact(phone="12")]},
    "blank contact name": {"emergency_contacts": [contact(name=" ")]},
    "contact without phone": {
        "emergency_contacts": [{"name": "Ana", "relationship": "Tía"}]
    },
    "contact with an id": {
        "emergency_contacts": [contact(id="00000000-0000-4000-8000-000000000001")]
    },
    "birthdate before 1900": {"birthdate": "1899-12-31"},
    "birthdate as a number": {"birthdate": 1234567890},
    "birthdate with a time": {"birthdate": "2012-05-04T00:00:00"},
    "long school grade": {"school_grade": "x" * 51},
    "unknown field": {"is_active": False},
    "null contacts": {"emergency_contacts": None},
}


@pytest.mark.parametrize("change", INVALID_BODIES.values(), ids=INVALID_BODIES.keys())
def test_invalid_bodies_are_422_and_write_nothing(
    module_seeded_url, client, cookie, change
):
    students, events = (
        student_count(module_seeded_url),
        family_event_count(module_seeded_url),
    )

    response = post(client, cookie, STUDENTS, student_body(**change))

    assert code_of(response) == (422, "validation_error"), response.text
    assert student_count(module_seeded_url) == students
    assert family_event_count(module_seeded_url) == events


def test_a_birthdate_today_in_mexico_city_is_accepted_and_tomorrow_is_not(
    clock, module_seeded_url, client, owner
):
    # 2026-10-19T05:30Z is still 2026-10-18 in Mexico City (UTC-6).
    clock.set(datetime(2026, 10, 19, 5, 30, tzinfo=UTC))
    cookie = sign_in(client, owner.username)
    students = student_count(module_seeded_url)

    tomorrow = post(client, cookie, STUDENTS, student_body(birthdate="2026-10-19"))
    today = post(client, cookie, STUDENTS, student_body(birthdate="2026-10-18"))

    assert code_of(tomorrow) == (422, "validation_error")
    assert tomorrow.json()["errors"] == [
        {
            "path": ["body", "birthdate"],
            "code": "birthdate_in_future",
            "message": "The birthdate can't be in the future.",
        }
    ]
    assert today.status_code == 201, today.text
    assert today.json()["is_minor"] is True
    assert student_count(module_seeded_url) == students + 1


def test_an_unknown_or_inactive_level_is_422_and_writes_nothing(
    module_seeded_url, client, cookie, inactive_level
):
    students = student_count(module_seeded_url)

    for level_id in ("00000000-0000-4000-8000-000000000999", inactive_level["id"]):
        response = post(
            client, cookie, STUDENTS, student_body(education_level_id=level_id)
        )
        assert code_of(response) == (422, "education_level_unavailable")
        assert response.json()["errors"] == [
            {
                "path": ["body", "education_level_id"],
                "code": "education_level_unavailable",
                "message": "Choose an active education level.",
            }
        ]
    assert student_count(module_seeded_url) == students


@pytest.mark.parametrize("role", [FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN])
def test_roles_without_student_manage_get_403(module_seeded_url, client, role):
    caller = create_user(module_seeded_url, roles=(role,))
    caller_cookie = sign_in(client, caller.username)
    students = student_count(module_seeded_url)

    response = post(client, caller_cookie, STUDENTS, student_body())

    assert code_of(response) == (403, "permission_denied")
    assert student_count(module_seeded_url) == students


@pytest.mark.parametrize("role", [ADMINISTRATOR, ACADEMIC_COORDINATOR])
def test_administrators_and_coordinators_may_create(module_seeded_url, client, role):
    caller = create_user(module_seeded_url, roles=(role,))
    caller_cookie = sign_in(client, caller.username)

    detail = create_student(client, caller_cookie)

    assert detail["personal"] is not None
