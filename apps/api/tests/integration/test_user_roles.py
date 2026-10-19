"""Role assignment (#170 A, B, D2) through ``PUT /api/v1/users/{user_id}/roles``
with real logins and the #26 reauthentication."""

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    ARCHIVED,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PENDING,
    STUDENT,
    SUSPENDED,
    TUTOR,
    USER_ADMIN_ROLE_ASSIGN,
)
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import (
    code_of,
    events,
    live_count,
    me,
    no_secret_in_audit,
    session_rows,
    signed_in,
)
from tests.integration.user_admin_support import (
    delegations_of,
    put_roles,
    roles_of,
    version_of,
)

OK = (200, None)
UNAUTHENTICATED = (401, "authentication_required")
DENIED = (403, "permission_denied")
REAUTH_REQUIRED = (403, "reauth_required")
NOT_FOUND = (404, "not_found")
CONFLICT = (409, "version_conflict")
LAST_OWNER = (409, "last_active_owner")
INVALID = (422, "validation_error")

REVOKED_EVENT = "identity.sessions_revoked"
STAFF_IDLE_SECONDS = 30 * 60
NOT_PRIVILEGED = [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


def staff(client: TestClient, username: str) -> Any:
    """Sign in and reauthenticate."""
    cookie = sign_in(client, username)
    reauthenticate(client, cookie)
    return cookie


def change(
    client: TestClient,
    cookie: Any,
    url: str,
    user_id: Any,
    roles: list[str],
    version: int | None = None,
) -> tuple[int, str | None]:
    if version is None:
        version = version_of(url, user_id)
    return code_of(put_roles(client, cookie, user_id, roles, version))


def role_ids(url: str) -> dict[str, str]:
    return {row.code: str(row.id) for row in run_sql(url, "SELECT id, code FROM roles")}


def audit_ids(url: str) -> list[uuid.UUID]:
    """Every audit event except reauthentications (the test's own setup)."""
    return [
        event.id for event in events(url) if event.action != "identity.reauthenticated"
    ]


def new_events(url: str, before: set[uuid.UUID]) -> list[Any]:
    """The events written since ``before`` was taken (:func:`audit_ids`)."""
    return [
        e
        for e in events(url)
        if e.id not in before and e.action != "identity.reauthenticated"
    ]


# --- A7: a role change ends the sessions -------------------------------------------


def test_adding_finance_staff_to_a_tutor_ends_their_sessions(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    first = signed_in(client, person.username)
    second = signed_in(client, person.username)
    cookie = staff(client, owner.username)

    response = put_roles(client, cookie, person.id, [TUTOR, FINANCE_STAFF], 1)

    assert code_of(response) == OK
    body = response.json()
    assert (body["roles"], body["version"]) == ([FINANCE_STAFF, TUTOR], 2)
    assert body["allowed_actions"]["assignable_roles"][0] == OWNER
    assert code_of(me(client, first)) == UNAUTHENTICATED
    assert code_of(me(client, second)) == UNAUTHENTICATED
    assert live_count(module_seeded_url, person.id) == 0
    # A new login gets the staff session class (#25 B4).
    fresh = signed_in(client, person.username)
    assert me(client, fresh).json()["roles"] == [FINANCE_STAFF, TUTOR]
    newest = session_rows(module_seeded_url, person.id)[-1]
    assert newest.revoked_at is None
    assert newest.idle_timeout_seconds == STAFF_IDLE_SECONDS


def test_without_a_reauthentication_reauth_required(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))

    result = change(
        client,
        sign_in(client, owner.username),
        module_seeded_url,
        person.id,
        [TUTOR, GUARDIAN],
    )

    assert result == REAUTH_REQUIRED
    assert roles_of(module_seeded_url, person.id) == [TUTOR]


def test_an_academic_coordinator_is_denied_without_being_asked(
    module_seeded_url, client, clock
):
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
    person = create_user(module_seeded_url, roles=(STUDENT,))

    result = change(
        client,
        sign_in(client, coordinator.username),
        module_seeded_url,
        person.id,
        [STUDENT, GUARDIAN],
    )

    assert result == DENIED


# --- A2, A7: who may assign which role (ADR-0007 D4) [E2] --------------------------


@pytest.mark.parametrize("role", NOT_PRIVILEGED)
def test_an_administrator_adds_and_removes_the_other_roles(
    module_seeded_url, client, clock, role
):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    base = GUARDIAN if role != GUARDIAN else TUTOR
    person = create_user(module_seeded_url, roles=(base,))
    cookie = staff(client, admin.username)

    added = change(client, cookie, module_seeded_url, person.id, [base, role])
    assert added == OK
    assert roles_of(module_seeded_url, person.id) == sorted([base, role])

    removed = change(client, cookie, module_seeded_url, person.id, [base])
    assert removed == OK
    assert roles_of(module_seeded_url, person.id) == [base]
    assert version_of(module_seeded_url, person.id) == 3


def test_administrator_needs_the_delegation_to_add_or_remove_administrator(
    module_seeded_url, client, clock
):
    plain = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    delegated = create_user(
        module_seeded_url,
        roles=(ADMINISTRATOR,),
        delegations=(USER_ADMIN_ROLE_ASSIGN,),
    )
    person = create_user(module_seeded_url, roles=(TUTOR,))
    plain_cookie = staff(client, plain.username)
    delegated_cookie = staff(client, delegated.username)
    url = module_seeded_url

    assert change(client, plain_cookie, url, person.id, [ADMINISTRATOR, TUTOR]) == (
        DENIED
    )
    assert change(client, delegated_cookie, url, person.id, [ADMINISTRATOR, TUTOR]) == (
        OK
    )
    assert roles_of(url, person.id) == [ADMINISTRATOR, TUTOR]
    # Removing needs the same authority as adding (D4).
    assert change(client, plain_cookie, url, person.id, [TUTOR]) == DENIED
    assert change(client, delegated_cookie, url, person.id, [TUTOR]) == OK
    assert roles_of(url, person.id) == [TUTOR]


def test_an_administrator_never_adds_owner_even_with_the_delegation(
    module_seeded_url, client, clock
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    for delegations in ((), (USER_ADMIN_ROLE_ASSIGN,)):
        admin = create_user(
            module_seeded_url, roles=(ADMINISTRATOR,), delegations=delegations
        )
        result = change(
            client,
            staff(client, admin.username),
            module_seeded_url,
            person.id,
            [OWNER, TUTOR],
        )
        assert result == DENIED
    assert roles_of(module_seeded_url, person.id) == [TUTOR]


@pytest.mark.parametrize(
    ("delegations", "roles"),
    [
        ((), [TUTOR]),  # removes owner
        ((), [OWNER, TUTOR]),  # only adds tutor [E2]
        ((USER_ADMIN_ROLE_ASSIGN,), [OWNER, TUTOR]),
        ((USER_ADMIN_ROLE_ASSIGN,), [OWNER, ADMINISTRATOR]),
    ],
)
def test_an_administrator_changes_no_role_of_an_owner(
    module_seeded_url, client, clock, delegations, roles
):
    target = create_user(module_seeded_url, roles=(OWNER,))
    admin = create_user(
        module_seeded_url, roles=(ADMINISTRATOR,), delegations=delegations
    )

    result = change(
        client, staff(client, admin.username), module_seeded_url, target.id, roles
    )

    assert result == DENIED
    assert roles_of(module_seeded_url, target.id) == [OWNER]


def test_an_owner_adds_and_removes_owner(module_seeded_url, client, clock, owner):
    person = create_user(module_seeded_url, roles=(FINANCE_STAFF,))
    cookie = staff(client, owner.username)

    assert change(client, cookie, module_seeded_url, person.id, [OWNER]) == OK
    assert roles_of(module_seeded_url, person.id) == [OWNER]
    assert change(client, cookie, module_seeded_url, person.id, [FINANCE_STAFF]) == OK
    assert roles_of(module_seeded_url, person.id) == [FINANCE_STAFF]


# --- A4: every state ----------------------------------------------------------------


@pytest.mark.parametrize("state", [PENDING, SUSPENDED, ARCHIVED])
def test_roles_change_in_every_state(module_seeded_url, client, clock, owner, state):
    person = create_user(
        module_seeded_url, roles=(TUTOR,), state=state, password_hash=None
    )

    result = change(
        client,
        staff(client, owner.username),
        module_seeded_url,
        person.id,
        [GUARDIAN],
    )

    assert result == OK
    assert roles_of(module_seeded_url, person.id) == [GUARDIAN]


# --- A2, A5: the last active Owner and the caller's own roles ----------------------


@pytest.fixture
def fresh_client(seeded_url: str) -> Iterator[TestClient]:
    """A client on a fresh database, so the active Owners are exactly the
    ones the test creates."""
    with auth_client(seeded_url) as test_client:
        yield test_client


def test_the_only_active_owner_cannot_remove_their_own_owner_role(
    seeded_url, fresh_client, clock
):
    only = create_user(seeded_url, roles=(OWNER, TUTOR))
    # An Owner who is not active does not count.
    create_user(seeded_url, roles=(OWNER,), state=SUSPENDED)
    cookie = staff(fresh_client, only.username)
    before = audit_ids(seeded_url)

    result = change(fresh_client, cookie, seeded_url, only.id, [TUTOR])

    assert result == LAST_OWNER
    assert roles_of(seeded_url, only.id) == [OWNER, TUTOR]
    assert version_of(seeded_url, only.id) == 1
    assert code_of(me(fresh_client, cookie)) == OK
    assert audit_ids(seeded_url) == before


def test_the_last_owner_guard_rolls_back_the_added_roles_too(
    seeded_url, fresh_client, clock
):
    only = create_user(seeded_url, roles=(OWNER,))
    cookie = staff(fresh_client, only.username)

    result = change(fresh_client, cookie, seeded_url, only.id, [ADMINISTRATOR, TUTOR])

    assert result == LAST_OWNER
    assert roles_of(seeded_url, only.id) == [OWNER]
    assert version_of(seeded_url, only.id) == 1
    assert code_of(me(fresh_client, cookie)) == OK


def test_with_a_second_active_owner_removing_your_own_owner_role_signs_you_out(
    seeded_url, fresh_client, clock
):
    me_user = create_user(seeded_url, roles=(OWNER, TUTOR))
    create_user(seeded_url, roles=(OWNER,))
    cookie = staff(fresh_client, me_user.username)

    response = put_roles(fresh_client, cookie, me_user.id, [TUTOR], 1)

    assert code_of(response) == OK
    assert response.json()["roles"] == [TUTOR]
    assert code_of(me(fresh_client, cookie)) == UNAUTHENTICATED
    assert roles_of(seeded_url, me_user.id) == [TUTOR]


def test_an_owner_adding_a_role_to_themselves_is_signed_out(
    module_seeded_url, client, clock, owner
):
    cookie = staff(client, owner.username)

    response = put_roles(client, cookie, owner.id, [OWNER, TUTOR], 1)

    assert code_of(response) == OK
    assert response.json()["roles"] == [OWNER, TUTOR]
    assert code_of(me(client, cookie)) == UNAUTHENTICATED


# --- A1, A2: bodies, order, versions -----------------------------------------------


@pytest.mark.parametrize(
    "roles", [[], [TUTOR, TUTOR], ["janitor"], None, [TUTOR, *NOT_PRIVILEGED]]
)
def test_invalid_bodies_are_422(module_seeded_url, client, clock, owner, roles):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = staff(client, owner.username)

    assert change(client, cookie, module_seeded_url, person.id, roles) == INVALID
    assert roles_of(module_seeded_url, person.id) == [TUTOR]


def test_check_order_422_then_404_then_403_then_409(module_seeded_url, client, clock):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    an_owner = create_user(module_seeded_url, roles=(OWNER,))
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = staff(client, admin.username)

    assert code_of(put_roles(client, cookie, uuid.uuid4(), [], 7)) == INVALID
    assert code_of(put_roles(client, cookie, uuid.uuid4(), [TUTOR], 7)) == NOT_FOUND
    # May not manage the user.
    assert code_of(put_roles(client, cookie, an_owner.id, [OWNER], 7)) == DENIED
    # May manage the user, but not assign this role.
    assert code_of(put_roles(client, cookie, person.id, [ADMINISTRATOR, TUTOR], 7)) == (
        DENIED
    )
    assert code_of(put_roles(client, cookie, person.id, [GUARDIAN], 7)) == CONFLICT
    assert roles_of(module_seeded_url, person.id) == [TUTOR]


def test_a_stale_version_is_409_and_changes_nothing(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    session = signed_in(client, person.username)
    cookie = staff(client, owner.username)

    assert change(client, cookie, module_seeded_url, person.id, [GUARDIAN], 2) == (
        CONFLICT
    )
    assert roles_of(module_seeded_url, person.id) == [TUTOR]
    assert code_of(me(client, session)) == OK


def test_concurrent_changes_with_one_version_give_one_200_and_one_409(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = staff(client, owner.username)
    results: list[tuple[int, str | None]] = []
    start = threading.Barrier(2)

    def put(roles: list[str]) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results.append(code_of(put_roles(own_client, cookie, person.id, roles, 1)))

    # Both add the same role, so without the version bump both would insert
    # it (a unique violation) or both succeed.
    threads = [
        threading.Thread(target=put, args=([TUTOR, GUARDIAN],)),
        threading.Thread(target=put, args=([TUTOR, GUARDIAN, STUDENT],)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == sorted([OK, CONFLICT], key=str)
    assert version_of(module_seeded_url, person.id) == 2


def test_the_same_set_is_a_no_op(module_seeded_url, client, clock, owner):
    person = create_user(module_seeded_url, roles=(TUTOR, GUARDIAN))
    session = signed_in(client, person.username)
    cookie = staff(client, owner.username)
    before = audit_ids(module_seeded_url)

    response = put_roles(client, cookie, person.id, [GUARDIAN, TUTOR], 1)

    assert code_of(response) == OK
    assert response.json()["version"] == 1
    assert version_of(module_seeded_url, person.id) == 1
    assert audit_ids(module_seeded_url) == before
    assert code_of(me(client, session)) == OK


# --- B: no leftover delegations -----------------------------------------------------


def test_removing_administrator_removes_the_delegations(
    module_seeded_url, client, clock, owner
):
    admin = create_user(
        module_seeded_url,
        roles=(ADMINISTRATOR, TUTOR),
        delegations=("payroll.finalize", USER_ADMIN_ROLE_ASSIGN),
    )
    signed_in(client, admin.username)
    cookie = staff(client, owner.username)
    before = set(audit_ids(module_seeded_url))

    response = put_roles(client, cookie, admin.id, [TUTOR], 1)

    assert code_of(response) == OK
    assert response.json()["delegations"] == []
    assert response.json()["allowed_actions"]["manage_delegations"] is False
    assert delegations_of(module_seeded_url, admin.id) == []
    new = new_events(module_seeded_url, before)
    assert sorted((e.record_type, e.action) for e in new) == [
        ("user_delegations", "delete"),
        ("user_delegations", "delete"),
        ("user_roles", "delete"),
        ("users", REVOKED_EVENT),
    ]
    [revoked] = [e for e in new if e.action == REVOKED_EVENT]
    assert revoked.details == {"reason": "role_removed", "count": 1}


# --- D2: audit ----------------------------------------------------------------------


def test_a_mixed_change_records_exactly_its_rows(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR, STUDENT))
    signed_in(client, person.username)
    signed_in(client, person.username)
    cookie = staff(client, owner.username)
    ids = role_ids(module_seeded_url)
    before = set(audit_ids(module_seeded_url))

    response = put_roles(client, cookie, person.id, [GUARDIAN, FINANCE_STAFF, TUTOR], 1)

    assert code_of(response) == OK
    new = new_events(module_seeded_url, before)
    # Added in catalog order, then removed; one revocation, from the first
    # change (role_added); the version bump writes no users update.
    assert [(e.record_type, e.action) for e in new] == [
        ("user_roles", "create"),
        ("users", REVOKED_EVENT),
        ("user_roles", "create"),
        ("user_roles", "delete"),
    ]
    first, revoked, second, removed = new
    assert first.changes["role_id"]["new"] == ids[FINANCE_STAFF]
    assert second.changes["role_id"]["new"] == ids[GUARDIAN]
    assert removed.changes["role_id"]["old"] == ids[STUDENT]
    assert {first.changes["user_id"]["new"], removed.changes["user_id"]["old"]} == {
        str(person.id)
    }
    assert revoked.record_id == person.id
    assert revoked.details == {"reason": "role_added", "count": 2}
    request_id = response.headers["X-Request-ID"]
    for event in new:
        assert event.actor_user_id == owner.id
        assert event.request_id == request_id
    no_secret_in_audit(module_seeded_url, "$argon2", TEST_USER_PASSWORD)


@pytest.mark.parametrize(
    "case", ["forbidden", "not_found", "conflict", "invalid", "last_owner_denied"]
)
def test_failed_changes_write_nothing(module_seeded_url, client, clock, case):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    target = create_user(
        module_seeded_url,
        roles=(OWNER,) if case == "last_owner_denied" else (TUTOR,),
    )
    cookie = staff(client, admin.username)
    before = audit_ids(module_seeded_url)

    target_id: Any = uuid.uuid4() if case == "not_found" else target.id
    roles: Any = {
        "forbidden": [ADMINISTRATOR, TUTOR],
        "invalid": [],
        "last_owner_denied": [TUTOR],
    }.get(case, [GUARDIAN])
    version = 4 if case == "conflict" else 1
    response = put_roles(client, cookie, target_id, roles, version)

    assert response.status_code in (403, 404, 409, 422)
    assert audit_ids(module_seeded_url) == before
