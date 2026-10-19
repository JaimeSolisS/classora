"""Audit events of student profiles (#33 G1, G2; #22 automatic events)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import FINANCE_STAFF, OWNER
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import patch, post
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.student_support import (
    STUDENTS,
    as_update_items,
    contact,
    contacts_of,
    create_student,
    family_event_count,
    student_body,
    student_path,
)


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


def test_a_create_records_every_column_with_the_actor_and_request_id(
    module_seeded_url, client, cookie, owner
):
    response = post(
        client,
        cookie,
        STUDENTS,
        student_body(
            email="a@example.com",
            school_grade="2.º",
            emergency_contacts=[
                contact("Ana"),
                contact("Beto", "Padre", "55 2222 2222"),
            ],
        ),
    )
    assert response.status_code == 201
    detail = response.json()
    request_id = response.headers["X-Request-ID"]

    [student_event] = events(
        module_seeded_url, record_type="students", record_id=uuid.UUID(detail["id"])
    )
    assert student_event.action == "create"
    assert student_event.actor_user_id == owner.id
    assert str(student_event.request_id) == request_id
    assert student_event.changes == {
        "given_names": {"old": None, "new": "José"},
        "family_names": {"old": None, "new": detail["family_names"]},
        "birthdate": {"old": None, "new": "2012-05-04"},
        "email": {"old": None, "new": "a@example.com"},
        "phone": {"old": None, "new": None},
        "school_name": {"old": None, "new": None},
        "school_grade": {"old": None, "new": "2.º"},
        "education_level_id": {"old": None, "new": None},
        "is_active": {"old": None, "new": True},
    }
    contact_events = [
        e
        for c in contacts_of(detail)
        for e in events(
            module_seeded_url,
            record_type="student_emergency_contacts",
            record_id=uuid.UUID(c["id"]),
        )
    ]
    assert [e.action for e in contact_events] == ["create", "create"]
    assert contact_events[1].changes == {
        "student_id": {"old": None, "new": detail["id"]},
        "name": {"old": None, "new": "Beto"},
        "relationship": {"old": None, "new": "Padre"},
        "phone": {"old": None, "new": "55 2222 2222"},
        "position": {"old": None, "new": 2},
    }
    assert {e.actor_user_id for e in contact_events} == {owner.id}
    assert {str(e.request_id) for e in contact_events} == {request_id}


def test_a_rename_records_exactly_the_changed_column(module_seeded_url, client, cookie):
    student = create_student(client, cookie)

    response = patch(
        client,
        cookie,
        student_path(student["id"]),
        {"version": 1, "given_names": "Luis"},
    )

    assert response.status_code == 200
    updates = events(
        module_seeded_url,
        record_type="students",
        record_id=uuid.UUID(student["id"]),
        action="update",
    )
    assert [e.changes for e in updates] == [
        {"given_names": {"old": "José", "new": "Luis"}}
    ]
    assert str(updates[0].request_id) == response.headers["X-Request-ID"]


def test_contact_changes_record_only_contact_events(module_seeded_url, client, cookie):
    student = create_student(
        client,
        cookie,
        emergency_contacts=[
            contact("Ana", "Madre", "55 1111 1111"),
            contact("Beto", "Padre", "55 2222 2222"),
            contact("Carla", "Tía", "55 3333 3333"),
        ],
    )
    ana, beto, carla = as_update_items(contacts_of(student))
    carla["phone"] = "55 3333 0000"

    response = patch(
        client,
        cookie,
        student_path(student["id"]),
        {"version": 1, "emergency_contacts": [carla, ana, contact("Dora")]},
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2
    # No students event: the version bump changes no audited column (D4).
    assert [
        e.action
        for e in events(
            module_seeded_url,
            record_type="students",
            record_id=uuid.UUID(student["id"]),
        )
    ] == ["create"]

    def contact_events(item):
        return events(
            module_seeded_url,
            record_type="student_emergency_contacts",
            record_id=uuid.UUID(item["id"]),
        )

    assert [(e.action, e.changes) for e in contact_events(carla)][1:] == [
        (
            "update",
            {
                "phone": {"old": "55 3333 3333", "new": "55 3333 0000"},
                "position": {"old": 3, "new": 1},
            },
        )
    ]
    assert [(e.action, e.changes) for e in contact_events(ana)][1:] == [
        ("update", {"position": {"old": 1, "new": 2}})
    ]
    [created, removed] = contact_events(beto)
    assert removed.action == "delete"
    assert removed.changes == {
        "student_id": {"old": student["id"], "new": None},
        "name": {"old": "Beto", "new": None},
        "relationship": {"old": "Padre", "new": None},
        "phone": {"old": "55 2222 2222", "new": None},
        "position": {"old": 2, "new": None},
    }
    dora = contacts_of(response.json())[2]
    assert [e.action for e in contact_events(dora)] == ["create"]


def test_rejected_and_no_op_requests_write_nothing(module_seeded_url, client, cookie):
    student = create_student(client, cookie)
    path = student_path(student["id"])
    patch(client, cookie, path, {"version": 1, "given_names": "Nuevo"})
    finance = create_user(module_seeded_url, roles=(FINANCE_STAFF,))
    finance_cookie = sign_in(client, finance.username)
    before = family_event_count(module_seeded_url)

    responses = [
        patch(client, cookie, path, {"version": 2, "given_names": "Nuevo"}),  # no-op
        patch(client, finance_cookie, path, {"version": 2, "given_names": "X"}),  # 403
        patch(
            client,
            cookie,
            student_path("00000000-0000-4000-8000-000000000999"),
            {"version": 1, "given_names": "X"},
        ),  # 404
        patch(client, cookie, path, {"version": 1, "given_names": "X"}),  # 409
        patch(client, cookie, path, {"version": 2, "given_names": " "}),  # 422
        post(client, finance_cookie, STUDENTS, student_body()),  # 403
        post(client, cookie, STUDENTS, student_body(emergency_contacts=[])),  # 422
    ]

    assert [code_of(r) for r in responses] == [
        (200, None),
        (403, "permission_denied"),
        (404, "not_found"),
        (409, "version_conflict"),
        (422, "validation_error"),
        (403, "permission_denied"),
        (422, "validation_error"),
    ]
    assert family_event_count(module_seeded_url) == before
