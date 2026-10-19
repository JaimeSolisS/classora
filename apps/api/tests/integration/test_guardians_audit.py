"""Audit events of guardian profiles and links (#34 G1, G2; #22 automatic
events). No named event and no ``details`` key."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import GUARDIAN, OWNER, TUTOR
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import patch, post
from tests.integration.guardian_support import (
    GUARDIANS,
    create_guardian,
    guardian_body,
    guardian_event_count,
    guardian_path,
    link,
    linked,
    unlink,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, no_secret_in_audit
from tests.integration.student_support import create_student

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


def test_a_create_records_every_column_with_the_actor_and_request_id(
    module_seeded_url, client, cookie, owner
):
    response = post(
        client,
        cookie,
        GUARDIANS,
        guardian_body(email="l@example.com", phone="55 1234 5678"),
    )
    assert response.status_code == 201
    body = response.json()

    [event] = events(
        module_seeded_url, record_type="guardians", record_id=uuid.UUID(body["id"])
    )
    assert event.action == "create"
    assert event.actor_user_id == owner.id
    assert str(event.request_id) == response.headers["X-Request-ID"]
    assert event.details is None or event.details == {}
    assert event.changes == {
        "given_names": {"old": None, "new": "Laura"},
        "family_names": {"old": None, "new": body["family_names"]},
        "email": {"old": None, "new": "l@example.com"},
        "phone": {"old": None, "new": "55 1234 5678"},
        "user_id": {"old": None, "new": None},
        "is_active": {"old": None, "new": True},
    }


def test_an_edit_records_exactly_the_changed_columns(
    module_seeded_url, client, cookie, owner
):
    guardian = create_guardian(client, cookie)

    response = patch(
        client,
        cookie,
        guardian_path(guardian["id"]),
        {"version": 1, "given_names": "Lucía", "phone": "55 0000 1111", "email": None},
    )
    assert response.status_code == 200

    updates = events(
        module_seeded_url,
        record_type="guardians",
        record_id=uuid.UUID(guardian["id"]),
        action="update",
    )
    assert [e.changes for e in updates] == [
        {
            "given_names": {"old": "Laura", "new": "Lucía"},
            "phone": {"old": None, "new": "55 0000 1111"},
        }
    ]
    assert updates[0].actor_user_id == owner.id
    assert str(updates[0].request_id) == response.headers["X-Request-ID"]


def test_an_account_link_is_an_update_of_user_id_and_leaks_no_secret(
    module_seeded_url, client, cookie
):
    first = create_user(module_seeded_url, roles=(GUARDIAN,))
    second = create_user(module_seeded_url, roles=(GUARDIAN,))
    guardian = create_guardian(client, cookie, user_id=str(first.id))

    patch(
        client,
        cookie,
        guardian_path(guardian["id"]),
        {"version": 1, "user_id": str(second.id)},
    )

    [update] = events(
        module_seeded_url,
        record_type="guardians",
        record_id=uuid.UUID(guardian["id"]),
        action="update",
    )
    assert update.changes == {"user_id": {"old": str(first.id), "new": str(second.id)}}
    no_secret_in_audit(module_seeded_url, TEST_USER_PASSWORD)


def test_link_and_unlink_record_the_values(module_seeded_url, client, cookie, owner):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)

    created = link(client, cookie, student["id"], guardian["id"], "grandparent")
    deleted = unlink(client, cookie, student["id"], guardian["id"])

    link_events = [
        e
        for e in events(module_seeded_url, record_type="student_guardians")
        if e.changes.get("student_id", {}).get("new") == student["id"]
        or e.changes.get("student_id", {}).get("old") == student["id"]
    ]
    assert [e.action for e in link_events] == ["create", "delete"]
    expected = {
        "student_id": student["id"],
        "guardian_id": guardian["id"],
        "relationship": "grandparent",
    }
    assert link_events[0].changes == {
        key: {"old": None, "new": value} for key, value in expected.items()
    }
    # The unlink's delete event keeps the relationship.
    assert link_events[1].changes == {
        key: {"old": value, "new": None} for key, value in expected.items()
    }
    assert [e.actor_user_id for e in link_events] == [owner.id, owner.id]
    assert [str(e.request_id) for e in link_events] == [
        created.headers["X-Request-ID"],
        deleted.headers["X-Request-ID"],
    ]
    # A link writes nothing on either profile.
    assert (
        events(
            module_seeded_url,
            record_type="guardians",
            record_id=uuid.UUID(guardian["id"]),
            action="update",
        )
        == []
    )


def test_failures_and_no_ops_write_nothing(module_seeded_url, client, cookie):
    student = create_student(client, cookie)
    guardian = create_guardian(client, cookie)
    linked(client, cookie, student["id"], guardian["id"])
    tutor = sign_in(client, create_user(module_seeded_url, roles=(TUTOR,)).username)
    before = guardian_event_count(module_seeded_url)

    outcomes = [
        # no-op PATCH
        patch(
            client,
            cookie,
            guardian_path(guardian["id"]),
            {"version": 1, "given_names": guardian["given_names"]},
        ),
        # 403
        post(client, tutor, GUARDIANS, guardian_body()),
        # 404
        unlink(client, cookie, student["id"], MISSING),
        # 409 (duplicate link, stale version)
        link(client, cookie, student["id"], guardian["id"]),
        patch(client, cookie, guardian_path(guardian["id"]), {"version": 7}),
        # 422
        post(client, cookie, GUARDIANS, guardian_body(user_id=MISSING)),
        link(client, cookie, student["id"], guardian["id"], "aunt"),
    ]

    assert [code_of(r) for r in outcomes] == [
        (200, None),
        (403, "permission_denied"),
        (404, "not_found"),
        (409, "guardian_already_linked"),
        (409, "version_conflict"),
        (422, "guardian_account_unavailable"),
        (422, "validation_error"),
    ]
    assert guardian_event_count(module_seeded_url) == before
