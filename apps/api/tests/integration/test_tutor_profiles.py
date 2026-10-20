"""Tutor profiles (#43 A3, C1-C4, D1) with real logins through the real app."""

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.authz import Principal
from app.core.models import StaleVersionError
from app.core.pagination import OffsetParams
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
from app.modules.staff import service
from app.modules.staff.models import TutorProfile
from app.modules.staff.service import _flush_profile
from tests.authz.assertions import assert_same_not_found
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.user_admin_support import unique_token

TUTORS = "/api/v1/tutors"

OK = (200, None)
DENIED = (403, "permission_denied")
NOT_FOUND = (404, "not_found")
CONFLICT = (409, "version_conflict")
INVALID = (422, "validation_error")

ALL_FIELDS = ["display_name", "email", "phone", "professional_experience"]
SELF_FIELDS = ["email", "phone", "professional_experience"]


def tutor_path(user_id: Any) -> str:
    return f"{TUTORS}/{user_id}"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(scope="module")
def admin(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ADMINISTRATOR,))


@pytest.fixture(scope="module")
def coordinator(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))


def new_tutor(url: str, **overrides: Any) -> CreatedUser:
    return create_user(url, roles=(TUTOR,), **overrides)


def profile_rows(url: str, user_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT professional_experience, version FROM tutor_profiles"
        " WHERE user_id = :id",
        {"id": user_id},
    )


def account(url: str, user_id: uuid.UUID) -> Any:
    [row] = run_sql(
        url,
        "SELECT display_name, email, phone, version FROM users WHERE id = :id",
        {"id": user_id},
    )
    return row


def profile_events(url: str, user_id: uuid.UUID) -> list[Any]:
    rows = run_sql(
        url, "SELECT id FROM tutor_profiles WHERE user_id = :id", {"id": user_id}
    )
    if not rows:
        return []
    return events(url, record_type="tutor_profiles", record_id=rows[0].id)


def user_updates(url: str, user_id: uuid.UUID) -> list[Any]:
    return [
        e
        for e in events(url, record_type="users", record_id=user_id)
        if e.action == "update"
    ]


def ids(audit_events: list[Any]) -> list[uuid.UUID]:
    return [e.id for e in audit_events]


def detail(client: TestClient, cookie: Any, user_id: Any) -> dict[str, Any]:
    response = get(client, cookie, tutor_path(user_id))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def edit(client: TestClient, cookie: Any, user_id: Any, **body: Any) -> Response:
    return patch(client, cookie, tutor_path(user_id), body)


# --- C1, C2: list and detail --------------------------------------------------------


def test_the_detail_of_a_tutor_without_a_profile(module_seeded_url, client, owner):
    person = new_tutor(
        module_seeded_url,
        display_name="Ana Tutora",
        email="ana@example.com",
        phone="+52 55 1234 5678",
    )

    body = detail(client, sign_in(client, owner.username), person.id)

    assert body == {
        "user_id": str(person.id),
        "username": person.username,
        "display_name": "Ana Tutora",
        "state": "active",
        "email": "ana@example.com",
        "phone": "+52 55 1234 5678",
        "professional_experience": None,
        "version": 0,
        "editable_fields": ALL_FIELDS,
        "teaching_subjects": [],
    }
    assert profile_rows(module_seeded_url, person.id) == []


def test_the_list_holds_tutors_with_contact_fields(module_seeded_url, client, admin):
    token = unique_token("tl")
    one = new_tutor(module_seeded_url, display_name=f"B {token}", email="b@example.com")
    two = new_tutor(module_seeded_url, display_name=f"A {token}", phone="55 1111 2222")
    create_user(module_seeded_url, roles=(FINANCE_STAFF,), display_name=f"C {token}")

    response = get(client, sign_in(client, admin.username), TUTORS, q=token)

    assert code_of(response) == OK
    body = response.json()
    assert body["total"] == 2
    assert body["items"] == [
        {
            "user_id": str(two.id),
            "username": two.username,
            "display_name": f"A {token}",
            "state": "active",
            "email": None,
            "phone": "55 1111 2222",
        },
        {
            "user_id": str(one.id),
            "username": one.username,
            "display_name": f"B {token}",
            "state": "active",
            "email": "b@example.com",
            "phone": None,
        },
    ]


def test_q_matches_the_username_and_state_filters(module_seeded_url, client, owner):
    token = unique_token("tq")
    by_username = new_tutor(module_seeded_url, username=f"{token}.user")
    suspended = new_tutor(
        module_seeded_url, display_name=f"Sus {token}", state="suspended"
    )
    cookie = sign_in(client, owner.username)

    everyone = get(client, cookie, TUTORS, q=token.upper()).json()
    active = get(client, cookie, TUTORS, q=token, state="active").json()
    only_suspended = get(client, cookie, TUTORS, q=token, state="suspended").json()

    assert {i["user_id"] for i in everyone["items"]} == {
        str(by_username.id),
        str(suspended.id),
    }
    assert [i["user_id"] for i in active["items"]] == [str(by_username.id)]
    assert [i["user_id"] for i in only_suspended["items"]] == [str(suspended.id)]


def test_pagination(module_seeded_url, client, owner):
    token = unique_token("tp")
    for n in range(3):
        new_tutor(module_seeded_url, display_name=f"{n} {token}")
    cookie = sign_in(client, owner.username)

    page = get(client, cookie, TUTORS, q=token, limit=2, offset=2).json()

    assert page["total"] == 3
    assert [i["display_name"] for i in page["items"]] == [f"2 {token}"]
    assert page["limit"] == 2
    assert page["offset"] == 2


@pytest.mark.parametrize(
    "params", [{"state": "gone"}, {"q": "x" * 101}, {"limit": 0}, {"offset": -1}]
)
def test_invalid_list_queries(client, owner, params):
    response = get(client, sign_in(client, owner.username), TUTORS, **params)

    assert code_of(response) == INVALID


def test_a_user_without_the_tutor_role_is_not_found(module_seeded_url, client, admin):
    cookie = sign_in(client, admin.username)

    own = get(client, cookie, tutor_path(admin.id))
    missing = get(client, cookie, tutor_path(uuid.uuid4()))

    assert_same_not_found(own, missing)
    assert_same_not_found(
        edit(client, cookie, admin.id, version=0, professional_experience="x"),
        edit(client, cookie, uuid.uuid4(), version=0, professional_experience="x"),
    )


def test_a_tutor_who_lost_the_role_disappears_but_the_row_stays(
    module_seeded_url, client, owner
):
    token = unique_token("tr")
    person = create_user(
        module_seeded_url, roles=(TUTOR, FINANCE_STAFF), display_name=f"R {token}"
    )
    cookie = sign_in(client, owner.username)
    assert (
        code_of(edit(client, cookie, person.id, version=0, professional_experience="E"))
        == OK
    )
    run_sql(
        module_seeded_url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": person.id},
    )

    listed = get(client, cookie, TUTORS, q=token).json()

    assert listed["total"] == 0
    assert code_of(get(client, cookie, tutor_path(person.id))) == NOT_FOUND
    assert len(profile_rows(module_seeded_url, person.id)) == 1


def test_the_list_takes_at_most_four_statements(module_seeded_url):
    token = unique_token("ts")
    for n in range(5):
        new_tutor(module_seeded_url, display_name=f"{n} {token}")
    owner_principal = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({OWNER}),
        permissions=frozenset(ROLE_PERMISSIONS[OWNER]),
    )

    statements, page = count_statements(
        module_seeded_url,
        lambda session: service.list_tutors(
            session,
            principal=owner_principal,
            q=token,
            state=None,
            page=OffsetParams(limit=50, offset=0),
        ),
    )

    assert page.total == 5
    assert statements <= 4


# --- C4: who sees and edits what -----------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR])
def test_a_manager_edits_every_field(module_seeded_url, client, role):
    manager = create_user(module_seeded_url, roles=(role,))
    person = new_tutor(module_seeded_url, display_name="Old Name")

    response = edit(
        client,
        sign_in(client, manager.username),
        person.id,
        version=0,
        display_name="  New Name ",
        email="new@example.com",
        phone="55 9999 0000",
        professional_experience="  Ten years of algebra.  ",
    )

    assert code_of(response) == OK
    body = response.json()
    assert body["display_name"] == "New Name"
    assert body["email"] == "new@example.com"
    assert body["phone"] == "55 9999 0000"
    assert body["professional_experience"] == "Ten years of algebra."
    assert body["version"] == 1
    assert body["editable_fields"] == ALL_FIELDS
    row = account(module_seeded_url, person.id)
    assert (row.display_name, row.email, row.phone) == (
        "New Name",
        "new@example.com",
        "55 9999 0000",
    )
    assert [tuple(r) for r in profile_rows(module_seeded_url, person.id)] == [
        ("Ten years of algebra.", 1)
    ]


def test_a_coordinator_reads_every_tutor_but_edits_none(
    module_seeded_url, client, coordinator
):
    token = unique_token("tc")
    person = new_tutor(
        module_seeded_url, display_name=f"T {token}", phone="55 1234 5678"
    )
    cookie = sign_in(client, coordinator.username)

    listed = get(client, cookie, TUTORS, q=token).json()
    body = detail(client, cookie, person.id)
    response = edit(client, cookie, person.id, version=0, phone="55 0000 0000")

    assert [i["user_id"] for i in listed["items"]] == [str(person.id)]
    assert body["editable_fields"] == []
    assert code_of(response) == DENIED
    assert account(module_seeded_url, person.id).phone == "55 1234 5678"
    assert profile_rows(module_seeded_url, person.id) == []


def test_a_tutor_edits_their_own_contact_and_experience(module_seeded_url, client):
    person = new_tutor(module_seeded_url, display_name="Self", phone="55 1111 1111")
    cookie = sign_in(client, person.username)
    assert detail(client, cookie, person.id)["editable_fields"] == SELF_FIELDS

    response = edit(
        client,
        cookie,
        person.id,
        version=0,
        email="me@example.com",
        phone="55 2222 2222",
        professional_experience="Physics.",
    )

    assert code_of(response) == OK
    body = response.json()
    assert (body["email"], body["phone"], body["professional_experience"]) == (
        "me@example.com",
        "55 2222 2222",
        "Physics.",
    )
    assert body["version"] == 1
    assert body["display_name"] == "Self"


def test_a_tutor_may_not_change_their_display_name(module_seeded_url, client):
    person = new_tutor(module_seeded_url, display_name="Fixed", phone="55 1111 1111")
    cookie = sign_in(client, person.username)
    users_before = ids(user_updates(module_seeded_url, person.id))

    response = edit(
        client,
        cookie,
        person.id,
        version=0,
        display_name="Changed",
        phone="55 3333 3333",
    )

    assert code_of(response) == DENIED
    row = account(module_seeded_url, person.id)
    assert (row.display_name, row.phone) == ("Fixed", "55 1111 1111")
    assert profile_rows(module_seeded_url, person.id) == []
    assert ids(user_updates(module_seeded_url, person.id)) == users_before


def test_idor_another_tutor_is_indistinguishable_from_a_missing_one(
    module_seeded_url, client
):
    token = unique_token("ti")
    tutor_a = new_tutor(module_seeded_url, display_name=f"A {token}")
    tutor_b = new_tutor(
        module_seeded_url, display_name=f"B {token}", phone="55 4444 4444"
    )
    cookie = sign_in(client, tutor_a.username)
    missing = uuid.uuid4()

    assert_same_not_found(
        get(client, cookie, tutor_path(tutor_b.id)),
        get(client, cookie, tutor_path(missing)),
    )
    assert_same_not_found(
        edit(client, cookie, tutor_b.id, version=0, phone="55 0000 0000"),
        edit(client, cookie, missing, version=0, phone="55 0000 0000"),
    )
    listed = get(client, cookie, TUTORS, q=token).json()
    everyone = get(client, cookie, TUTORS).json()

    assert [i["user_id"] for i in listed["items"]] == [str(tutor_a.id)]
    assert [i["user_id"] for i in everyone["items"]] == [str(tutor_a.id)]
    assert everyone["total"] == 1
    assert account(module_seeded_url, tutor_b.id).phone == "55 4444 4444"
    assert profile_rows(module_seeded_url, tutor_b.id) == []


def test_a_tutor_filtered_out_of_their_own_list_sees_an_empty_page(
    module_seeded_url, client
):
    person = new_tutor(module_seeded_url)
    cookie = sign_in(client, person.username)

    page = get(client, cookie, TUTORS, state="archived").json()

    assert page == {"items": [], "total": 0, "limit": page["limit"], "offset": 0}


@pytest.mark.parametrize("role", [FINANCE_STAFF, STUDENT, GUARDIAN])
def test_roles_without_tutor_view_are_denied_everywhere(
    module_seeded_url, client, role
):
    caller = create_user(module_seeded_url, roles=(role,))
    person = new_tutor(module_seeded_url)
    cookie = sign_in(client, caller.username)

    responses = [
        get(client, cookie, TUTORS),
        get(client, cookie, tutor_path(person.id)),
        edit(client, cookie, person.id, version=0, professional_experience="x"),
    ]

    assert [code_of(r) for r in responses] == [DENIED] * 3
    assert profile_rows(module_seeded_url, person.id) == []


def test_an_administrator_may_not_change_an_owner_tutors_account(
    module_seeded_url, client, admin
):
    # Decision D1: the #31 who-may-manage-whom rule also holds here.
    person = create_user(
        module_seeded_url, roles=(OWNER, TUTOR), display_name="Owner Tutor"
    )
    cookie = sign_in(client, admin.username)

    body = detail(client, cookie, person.id)
    denied = edit(client, cookie, person.id, version=0, phone="55 0000 0000")
    allowed = edit(client, cookie, person.id, version=0, professional_experience="E")

    assert body["editable_fields"] == ["professional_experience"]
    assert code_of(denied) == DENIED
    assert code_of(allowed) == OK
    assert account(module_seeded_url, person.id).phone is None


# --- A3, C3: versions, the lazy row and no-ops ---------------------------------------


def test_the_first_edit_creates_the_row_and_version_zero_is_then_stale(
    module_seeded_url, client, owner
):
    person = new_tutor(module_seeded_url)
    cookie = sign_in(client, owner.username)

    first = edit(client, cookie, person.id, version=0, professional_experience="One")
    second = edit(client, cookie, person.id, version=0, professional_experience="Two")
    third = edit(client, cookie, person.id, version=1, professional_experience="Two")

    assert code_of(first) == OK
    assert first.json()["version"] == 1
    assert code_of(second) == CONFLICT
    assert code_of(third) == OK
    assert third.json()["version"] == 2
    assert [tuple(r) for r in profile_rows(module_seeded_url, person.id)] == [
        ("Two", 2)
    ]


def test_a_stale_version_on_a_tutor_without_a_profile(module_seeded_url, client, owner):
    person = new_tutor(module_seeded_url)

    response = edit(
        client,
        sign_in(client, owner.username),
        person.id,
        version=3,
        professional_experience="x",
    )

    assert code_of(response) == CONFLICT
    assert profile_rows(module_seeded_url, person.id) == []


def test_an_account_only_change_also_versions_the_profile(
    module_seeded_url, client, owner
):
    person = new_tutor(module_seeded_url, phone="55 1111 1111")
    cookie = sign_in(client, owner.username)

    created = edit(client, cookie, person.id, version=0, phone="55 2222 2222")
    bumped = edit(client, cookie, person.id, version=1, phone="55 3333 3333")
    stale = edit(client, cookie, person.id, version=1, phone="55 4444 4444")

    assert created.json()["version"] == 1
    assert bumped.json()["version"] == 2
    assert code_of(stale) == CONFLICT
    assert account(module_seeded_url, person.id).phone == "55 3333 3333"
    assert [tuple(r) for r in profile_rows(module_seeded_url, person.id)] == [(None, 2)]


def test_a_body_that_changes_nothing(module_seeded_url, client, owner):
    person = new_tutor(module_seeded_url, display_name="Same", email="s@example.com")
    cookie = sign_in(client, owner.username)
    users_before = ids(user_updates(module_seeded_url, person.id))
    users_version = account(module_seeded_url, person.id).version

    empty = edit(client, cookie, person.id, version=0)
    same = edit(
        client,
        cookie,
        person.id,
        version=0,
        display_name="Same",
        email=" s@example.com ",
        phone="",
        professional_experience="  ",
    )

    assert code_of(empty) == OK
    assert code_of(same) == OK
    assert same.json()["version"] == 0
    assert profile_rows(module_seeded_url, person.id) == []
    assert ids(user_updates(module_seeded_url, person.id)) == users_before
    assert account(module_seeded_url, person.id).version == users_version


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version": -1},
        {"version": 0, "display_name": None},
        {"version": 0, "email": "nope"},
        {"version": 0, "professional_experience": "x" * 4001},
        {"version": 0, "username": "taken"},
    ],
)
def test_invalid_bodies(module_seeded_url, client, owner, body):
    person = new_tutor(module_seeded_url)

    response = patch(
        client, sign_in(client, owner.username), tutor_path(person.id), body
    )

    assert code_of(response) == INVALID
    assert profile_rows(module_seeded_url, person.id) == []


def test_concurrent_first_edits_give_one_200_and_one_409(
    module_seeded_url, client, owner
):
    person = new_tutor(module_seeded_url)
    cookie = sign_in(client, owner.username)
    results: list[tuple[int, str | None]] = []
    start = threading.Barrier(2)

    def first_edit(text: str) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results.append(
                code_of(
                    edit(
                        own_client,
                        cookie,
                        person.id,
                        version=0,
                        professional_experience=text,
                    )
                )
            )

    threads = [threading.Thread(target=first_edit, args=(t,)) for t in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == sorted([OK, CONFLICT], key=str)
    assert len(profile_rows(module_seeded_url, person.id)) == 1


def test_a_unique_violation_on_the_first_insert_is_a_version_conflict(
    module_seeded_url,
):
    # The race the threads above may or may not hit: a row created after the
    # version check.
    person = new_tutor(module_seeded_url)
    run_sql(
        module_seeded_url,
        "INSERT INTO tutor_profiles (user_id) VALUES (:id)",
        {"id": person.id},
    )

    async def insert_again(session: Any) -> None:
        session.add(TutorProfile(user_id=person.id, professional_experience="x"))
        await _flush_profile(session)

    with pytest.raises(StaleVersionError):
        in_uow(module_seeded_url, insert_again)


# --- D1: audit -----------------------------------------------------------------------


def test_the_create_and_update_events(module_seeded_url, client, owner):
    person = new_tutor(module_seeded_url)
    cookie = sign_in(client, owner.username)

    created = edit(client, cookie, person.id, version=0, professional_experience="One")
    updated = edit(client, cookie, person.id, version=1, professional_experience="Two")

    create_event, update_event = profile_events(module_seeded_url, person.id)
    assert create_event.action == "create"
    assert create_event.actor_user_id == owner.id
    assert create_event.request_id == created.headers["X-Request-ID"]
    assert create_event.changes["professional_experience"] == {
        "old": None,
        "new": "One",
    }
    assert create_event.changes["user_id"] == {"old": None, "new": str(person.id)}
    assert update_event.action == "update"
    assert update_event.actor_user_id == owner.id
    assert update_event.request_id == updated.headers["X-Request-ID"]
    assert update_event.changes == {
        "professional_experience": {"old": "One", "new": "Two"}
    }


def test_a_phone_change_records_a_users_update(module_seeded_url, client):
    person = new_tutor(module_seeded_url, phone="55 1111 1111")
    cookie = sign_in(client, person.username)
    before = user_updates(module_seeded_url, person.id)

    created = edit(client, cookie, person.id, version=0, phone="55 2222 2222")
    bumped = edit(client, cookie, person.id, version=1, phone="55 3333 3333")

    new_events = user_updates(module_seeded_url, person.id)[len(before) :]
    assert [e.changes for e in new_events] == [
        {"phone": {"old": "55 1111 1111", "new": "55 2222 2222"}},
        {"phone": {"old": "55 2222 2222", "new": "55 3333 3333"}},
    ]
    assert {e.actor_user_id for e in new_events} == {person.id}
    assert [e.request_id for e in new_events] == [
        created.headers["X-Request-ID"],
        bumped.headers["X-Request-ID"],
    ]
    # The lazy row was created by the first edit; the version-only bump of
    # the second writes no profile event.
    assert [e.action for e in profile_events(module_seeded_url, person.id)] == [
        "create"
    ]


def test_refused_requests_write_nothing(module_seeded_url, client, coordinator):
    person = new_tutor(module_seeded_url)
    other = new_tutor(module_seeded_url)
    owner_cookie = sign_in(
        client, create_user(module_seeded_url, roles=(OWNER,)).username
    )
    assert (
        code_of(
            edit(
                client, owner_cookie, person.id, version=0, professional_experience="A"
            )
        )
        == OK
    )
    before_profile = ids(profile_events(module_seeded_url, person.id))
    before_users = ids(user_updates(module_seeded_url, person.id))

    refused = [
        edit(
            client,
            sign_in(client, coordinator.username),
            person.id,
            version=1,
            phone="55 1234 5678",
        ),
        edit(
            client,
            sign_in(client, other.username),
            person.id,
            version=1,
            phone="55 1234 5678",
        ),
        edit(client, owner_cookie, person.id, version=0, professional_experience="B"),
        edit(
            client,
            owner_cookie,
            person.id,
            version=1,
            professional_experience="x" * 4001,
        ),
    ]

    assert [code_of(r) for r in refused] == [DENIED, NOT_FOUND, CONFLICT, INVALID]
    assert ids(profile_events(module_seeded_url, person.id)) == before_profile
    assert ids(user_updates(module_seeded_url, person.id)) == before_users
