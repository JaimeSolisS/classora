"""Audit events of families and their membership (#172 F1; #22 automatic
events). No named event and no ``details`` key."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import FINANCE_STAFF, OWNER
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import patch, post
from tests.integration.family_support import (
    FAMILIES,
    add,
    added,
    create_family,
    family_event_count,
    family_path,
    remove,
    set_family_active,
)
from tests.integration.guardian_support import create_guardian
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
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


def test_a_create_and_an_update_record_the_columns_actor_and_request_id(
    module_seeded_url, client, cookie, owner
):
    created = post(client, cookie, FAMILIES, {"name": "Familia Auditada"})
    family = created.json()
    updated = patch(
        client,
        cookie,
        family_path(family["id"]),
        {"version": 1, "name": "Familia Renombrada", "is_active": False},
    )
    assert updated.status_code == 200

    create, update = events(
        module_seeded_url, record_type="families", record_id=uuid.UUID(family["id"])
    )
    assert create.action == "create"
    assert create.changes == {
        "name": {"old": None, "new": "Familia Auditada"},
        "is_active": {"old": None, "new": True},
    }
    assert update.action == "update"
    assert update.changes == {
        "name": {"old": "Familia Auditada", "new": "Familia Renombrada"},
        "is_active": {"old": True, "new": False},
    }
    for event, response in ((create, created), (update, updated)):
        assert event.actor_user_id == owner.id
        assert str(event.request_id) == response.headers["X-Request-ID"]
        assert event.details is None or event.details == {}


@pytest.mark.parametrize(
    ("kind", "table", "column"),
    [
        ("students", "family_students", "student_id"),
        ("guardians", "family_guardians", "guardian_id"),
    ],
)
def test_membership_create_and_delete_events_keep_the_ids(
    module_seeded_url, client, cookie, owner, kind, table, column
):
    family = create_family(client, cookie)
    member = (
        create_student(client, cookie)
        if kind == "students"
        else create_guardian(client, cookie)
    )
    joined = add(client, cookie, family["id"], kind, member["id"])
    left = remove(client, cookie, family["id"], kind, member["id"])
    assert (joined.status_code, left.status_code) == (204, 204)

    rows = [
        e
        for e in events(module_seeded_url, record_type=table)
        if (e.changes or {})
        .get(column, {})
        .get("new" if e.action == "create" else "old")
        == member["id"]
    ]
    create, delete = rows
    assert create.action == "create"
    assert create.changes == {
        "family_id": {"old": None, "new": family["id"]},
        column: {"old": None, "new": member["id"]},
    }
    assert delete.action == "delete"
    assert delete.changes == {
        "family_id": {"old": family["id"], "new": None},
        column: {"old": member["id"], "new": None},
    }
    assert delete.record_id == create.record_id
    for event, response in ((create, joined), (delete, left)):
        assert event.actor_user_id == owner.id
        assert str(event.request_id) == response.headers["X-Request-ID"]
        assert event.details is None or event.details == {}


def test_refused_and_no_op_requests_write_nothing(module_seeded_url, client, cookie):
    family = create_family(client, cookie)
    other = create_family(client, cookie)
    inactive = set_family_active(client, cookie, create_family(client, cookie), False)
    student = create_student(client, cookie)
    added(client, cookie, family["id"], "students", student["id"])
    finance = sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )
    before = family_event_count(module_seeded_url)

    outcomes = [
        # An idempotent re-add.
        code_of(add(client, cookie, family["id"], "students", student["id"])),
        # 403.
        code_of(add(client, finance, other["id"], "students", student["id"])),
        code_of(
            patch(
                client, finance, family_path(family["id"]), {"version": 1, "name": "X"}
            )
        ),
        # 404.
        code_of(add(client, cookie, MISSING, "students", student["id"])),
        code_of(remove(client, cookie, other["id"], "students", student["id"])),
        # 409.
        code_of(add(client, cookie, other["id"], "students", student["id"])),
        code_of(add(client, cookie, inactive["id"], "students", student["id"])),
        code_of(
            patch(
                client, cookie, family_path(family["id"]), {"version": 7, "name": "Y"}
            )
        ),
        # A no-op PATCH.
        code_of(
            patch(
                client,
                cookie,
                family_path(family["id"]),
                {"version": 1, "name": family["name"]},
            )
        ),
    ]

    assert outcomes == [
        (204, None),
        (403, "permission_denied"),
        (403, "permission_denied"),
        (404, "not_found"),
        (404, "not_found"),
        (409, "family_member_conflict"),
        (409, "family_inactive"),
        (409, "version_conflict"),
        (200, None),
    ]
    assert family_event_count(module_seeded_url) == before
