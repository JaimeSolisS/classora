"""``GET`` and ``PATCH /api/v1/users/{user_id}`` (#31 E, F, H) with real logins."""

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ADMINISTRATOR,
    ARCHIVED,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PENDING,
    SUSPENDED,
    TUTOR,
    USER_ADMIN_ROLE_ASSIGN,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, no_secret_in_audit
from tests.integration.registration_support import register
from tests.integration.user_admin_support import (
    get_user,
    patch_user,
    unique_token,
    user_row,
    version_of,
)

OK = (200, None)
DENIED = (403, "permission_denied")
NOT_FOUND = (404, "not_found")
CONFLICT = (409, "version_conflict")
INVALID = (422, "validation_error")

ALL_FALSE = {
    "edit": False,
    "suspend": False,
    "archive": False,
    "reactivate": False,
    "reset_password": False,
}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def admin(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ADMINISTRATOR,))


def tutor(url: str, **overrides: Any) -> CreatedUser:
    return create_user(url, roles=(TUTOR,), **overrides)


def user_events(url: str, user_id: uuid.UUID) -> list[Any]:
    return events(url, record_type="users", record_id=user_id)


def update_ids(url: str, **filters: Any) -> list[uuid.UUID]:
    """The ids of the ``users`` ``update`` events (logins write other events)."""
    return [
        e.id
        for e in events(url, record_type="users", **filters)
        if e.action == "update"
    ]


# --- E1: detail ---------------------------------------------------------------------


def test_the_detail_of_an_active_tutor(module_seeded_url, client, owner):
    person = tutor(
        module_seeded_url,
        display_name="Ana Tutor",
        email="ana@example.com",
        phone="+52 55 1234 5678",
    )

    response = get_user(client, sign_in(client, owner.username), person.id)

    assert code_of(response) == OK
    body = response.json()
    assert body == {
        "id": str(person.id),
        "username": person.username,
        "display_name": "Ana Tutor",
        "state": "active",
        "roles": [TUTOR],
        "created_at": body["created_at"],
        "updated_at": body["updated_at"],
        "email": "ana@example.com",
        "phone": "+52 55 1234 5678",
        "version": 1,
        "delegations": [],
        "password_set": True,
        "allowed_actions": {
            "edit": True,
            "suspend": True,
            "archive": True,
            "reactivate": False,
            "reset_password": True,
        },
    }


def test_the_detail_lists_delegations_sorted(module_seeded_url, client, owner):
    delegated = create_user(
        module_seeded_url,
        roles=(ADMINISTRATOR,),
        delegations=("schedule.override", USER_ADMIN_ROLE_ASSIGN, "payroll.finalize"),
    )

    body = get_user(client, sign_in(client, owner.username), delegated.id).json()

    assert body["delegations"] == [
        "payroll.finalize",
        "schedule.override",
        USER_ADMIN_ROLE_ASSIGN,
    ]


def test_password_set_tells_an_invitation_from_a_registration_request(
    module_seeded_url, client, owner
):
    invited = tutor(module_seeded_url, state=PENDING, password_hash=None)
    token = unique_token()
    assert register(client, username=f"{token}.req").status_code == 202
    cookie = sign_in(client, owner.username)
    [requested] = [
        item
        for item in client.get(
            "/api/v1/users",
            params={"q": token},
            headers={"Cookie": f"{cookie.name}={cookie.value}"},
        ).json()["items"]
    ]

    invitation = get_user(client, cookie, invited.id).json()
    request = get_user(client, cookie, requested["id"]).json()

    assert (invitation["password_set"], invitation["allowed_actions"]["archive"]) == (
        False,
        True,
    )
    assert (request["password_set"], request["allowed_actions"]["archive"]) == (
        True,
        False,
    )
    for body in (invitation, request):
        assert not any("hash" in key or "token" in key for key in body)


def test_own_record_allows_only_the_profile(module_seeded_url, client, owner):
    body = get_user(client, sign_in(client, owner.username), owner.id).json()

    assert body["allowed_actions"] == {**ALL_FALSE, "edit": True}


def test_an_administrator_sees_an_owner_without_actions(
    module_seeded_url, client, owner, admin
):
    body = get_user(client, sign_in(client, admin.username), owner.id).json()

    assert body["allowed_actions"] == ALL_FALSE


@pytest.mark.parametrize(
    ("state", "suspend", "archive", "reactivate"),
    [(SUSPENDED, False, True, True), (ARCHIVED, False, False, True)],
)
def test_actions_follow_the_state(
    module_seeded_url, client, admin, state, suspend, archive, reactivate
):
    person = create_user(module_seeded_url, roles=(FINANCE_STAFF,), state=state)

    actions = get_user(client, sign_in(client, admin.username), person.id).json()[
        "allowed_actions"
    ]

    assert actions == {
        "edit": True,
        "suspend": suspend,
        "archive": archive,
        "reactivate": reactivate,
        "reset_password": False,  # not active
    }


def test_an_unknown_user_is_404_and_a_malformed_id_is_422(client, owner):
    cookie = sign_in(client, owner.username)

    assert code_of(get_user(client, cookie, uuid.uuid4())) == NOT_FOUND
    assert code_of(get_user(client, cookie, "not-a-uuid")) == INVALID


# --- F: profile ---------------------------------------------------------------------


def test_editing_every_field_returns_the_new_detail(module_seeded_url, client, owner):
    person = tutor(module_seeded_url, display_name="Old Name")

    response = patch_user(
        client,
        sign_in(client, owner.username),
        person.id,
        {
            "version": 1,
            "display_name": "  New Name  ",
            "email": " new@example.com ",
            "phone": "55 1234 5678",
        },
    )

    assert code_of(response) == OK
    body = response.json()
    assert (body["display_name"], body["email"], body["phone"], body["version"]) == (
        "New Name",
        "new@example.com",
        "55 1234 5678",
        2,
    )
    row = user_row(module_seeded_url, person.id)
    assert (row.display_name, row.email, row.phone, row.version) == (
        "New Name",
        "new@example.com",
        "55 1234 5678",
        2,
    )


def test_fields_not_sent_stay_unchanged(module_seeded_url, client, owner):
    person = tutor(
        module_seeded_url,
        display_name="Keep",
        email="k@example.com",
        phone="5512345678",
    )

    body = patch_user(
        client,
        sign_in(client, owner.username),
        person.id,
        {"version": 1, "phone": "5587654321"},
    ).json()

    assert (body["display_name"], body["email"], body["phone"]) == (
        "Keep",
        "k@example.com",
        "5587654321",
    )


@pytest.mark.parametrize("cleared", ["", None, "   "])
def test_an_empty_string_or_null_clears_a_contact_field(
    module_seeded_url, client, owner, cleared
):
    person = tutor(module_seeded_url, email="c@example.com", phone="5512345678")

    body = patch_user(
        client,
        sign_in(client, owner.username),
        person.id,
        {"version": 1, "email": cleared, "phone": cleared},
    ).json()

    assert (body["email"], body["phone"], body["version"]) == (None, None, 2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "ab"),
        ("email", "a b@c.d"),
        ("phone", "12"),
        ("phone", "phone-ext"),
        ("display_name", "   "),
        ("display_name", None),
        ("display_name", "x" * 201),
        ("email", "a@" + "b" * 250 + ".co"),
    ],
)
def test_an_invalid_field_is_422_with_its_path(
    module_seeded_url, client, owner, field, value
):
    person = tutor(module_seeded_url)

    response = patch_user(
        client, sign_in(client, owner.username), person.id, {"version": 1, field: value}
    )

    assert code_of(response) == INVALID
    assert {tuple(e["path"][:2]) for e in response.json()["errors"]} == {
        ("body", field)
    }
    assert version_of(module_seeded_url, person.id) == 1


@pytest.mark.parametrize("body", [{}, {"version": 0}, {"display_name": "X"}])
def test_a_missing_or_invalid_version_is_422(module_seeded_url, client, owner, body):
    person = tutor(module_seeded_url)

    response = patch_user(client, sign_in(client, owner.username), person.id, body)

    assert code_of(response) == INVALID


def test_the_username_cannot_be_changed(module_seeded_url, client, owner):
    person = tutor(module_seeded_url)

    response = patch_user(
        client,
        sign_in(client, owner.username),
        person.id,
        {"version": 1, "username": "someone.else"},
    )

    # Unknown fields are ignored: nothing changed.
    assert code_of(response) == OK
    assert response.json()["username"] == person.username
    assert response.json()["version"] == 1


def test_order_404_then_403_then_409(module_seeded_url, client, admin, owner):
    cookie = sign_in(client, admin.username)
    other_owner = create_user(module_seeded_url, roles=(OWNER,))

    # Unknown id: 404 whatever the version.
    assert code_of(patch_user(client, cookie, uuid.uuid4(), {"version": 9})) == (
        NOT_FOUND
    )
    # May not manage: 403 before the version check.
    assert code_of(patch_user(client, cookie, other_owner.id, {"version": 9})) == DENIED
    person = tutor(module_seeded_url)
    assert code_of(patch_user(client, cookie, person.id, {"version": 9})) == CONFLICT


@pytest.mark.parametrize(
    ("roles", "delegated", "expected"),
    [
        ((OWNER,), (), DENIED),
        ((ADMINISTRATOR,), (), DENIED),
        ((ADMINISTRATOR,), (USER_ADMIN_ROLE_ASSIGN,), OK),
        ((TUTOR, ADMINISTRATOR), (), DENIED),
        ((TUTOR, GUARDIAN), (), OK),
        ((), (), OK),
    ],
)
def test_who_an_administrator_may_edit(
    module_seeded_url, client, roles, delegated, expected
):
    actor = create_user(
        module_seeded_url, roles=(ADMINISTRATOR,), delegations=delegated
    )
    target = create_user(module_seeded_url, roles=roles)

    response = patch_user(
        client,
        sign_in(client, actor.username),
        target.id,
        {"version": 1, "display_name": "Edited"},
    )

    assert code_of(response) == expected


def test_an_administrator_edits_their_own_profile(module_seeded_url, client, admin):
    response = patch_user(
        client,
        sign_in(client, admin.username),
        admin.id,
        {"version": 1, "email": "me@example.com"},
    )

    assert code_of(response) == OK
    assert response.json()["email"] == "me@example.com"


@pytest.mark.parametrize("state", [PENDING, SUSPENDED, ARCHIVED])
def test_profiles_can_be_edited_in_every_state(module_seeded_url, client, owner, state):
    person = tutor(module_seeded_url, state=state)

    response = patch_user(
        client,
        sign_in(client, owner.username),
        person.id,
        {"version": 1, "display_name": "Edited"},
    )

    assert code_of(response) == OK
    assert response.json()["state"] == state


def test_a_stale_version_is_409_and_changes_nothing(module_seeded_url, client, owner):
    person = tutor(module_seeded_url, display_name="Before")
    cookie = sign_in(client, owner.username)
    assert (
        code_of(
            patch_user(
                client, cookie, person.id, {"version": 1, "display_name": "Once"}
            )
        )
        == OK
    )
    before = update_ids(module_seeded_url, record_id=person.id)

    response = patch_user(
        client, cookie, person.id, {"version": 1, "display_name": "Stale"}
    )

    assert code_of(response) == CONFLICT
    assert user_row(module_seeded_url, person.id).display_name == "Once"
    assert update_ids(module_seeded_url, record_id=person.id) == before


def test_a_no_op_body_returns_200_without_a_version_change_or_event(
    module_seeded_url, client, owner
):
    person = tutor(module_seeded_url, display_name="Same", email="s@example.com")
    before = update_ids(module_seeded_url, record_id=person.id)

    response = patch_user(
        client,
        sign_in(client, owner.username),
        person.id,
        {"version": 1, "display_name": "Same", "email": " s@example.com "},
    )

    assert code_of(response) == OK
    assert response.json()["version"] == 1
    assert update_ids(module_seeded_url, record_id=person.id) == before


def test_concurrent_updates_with_one_version_give_one_200_and_one_409(
    module_seeded_url, client, owner
):
    person = tutor(module_seeded_url)
    cookie = sign_in(client, owner.username)
    results: list[tuple[int, str | None]] = []
    start = threading.Barrier(2)

    def edit(name: str) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results.append(
                code_of(
                    patch_user(
                        own_client,
                        cookie,
                        person.id,
                        {"version": 1, "display_name": name},
                    )
                )
            )

    threads = [threading.Thread(target=edit, args=(n,)) for n in ("One", "Two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == sorted([OK, CONFLICT], key=str)
    assert version_of(module_seeded_url, person.id) == 2


# --- H: audit -----------------------------------------------------------------------


def test_a_profile_edit_records_exactly_the_changed_fields(
    module_seeded_url, client, owner
):
    person = tutor(module_seeded_url, display_name="Old", email="old@example.com")
    before = len(user_events(module_seeded_url, person.id))

    response = patch_user(
        client,
        sign_in(client, owner.username),
        person.id,
        {"version": 1, "display_name": "New", "email": "old@example.com", "phone": ""},
    )

    assert code_of(response) == OK
    new = user_events(module_seeded_url, person.id)[before:]
    assert len(new) == 1
    [event] = new
    assert event.action == "update"
    assert event.actor_user_id == owner.id
    assert event.request_id == response.headers["X-Request-ID"]
    assert event.changes == {"display_name": {"old": "Old", "new": "New"}}
    assert event.details is None or "password" not in str(event.details)
    no_secret_in_audit(module_seeded_url, "password_hash", "$argon2")


@pytest.mark.parametrize("case", ["forbidden", "not_found", "conflict", "invalid"])
def test_failed_edits_write_nothing(module_seeded_url, client, admin, case):
    target = create_user(
        module_seeded_url, roles=(OWNER,) if case == "forbidden" else (TUTOR,)
    )
    cookie = sign_in(client, admin.username)
    before = update_ids(module_seeded_url)
    body: dict[str, Any] = {"version": 1, "display_name": "Changed"}
    target_id: Any = target.id
    if case == "not_found":
        target_id = uuid.uuid4()
    elif case == "conflict":
        body["version"] = 5
    elif case == "invalid":
        body["email"] = "nope"

    response = patch_user(client, cookie, target_id, body)

    assert response.status_code in (403, 404, 409, 422)
    assert update_ids(module_seeded_url) == before
