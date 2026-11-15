"""Delegated permissions (#170 C, D2) through ``PUT
/api/v1/users/{user_id}/delegations`` with real logins and the #26
reauthentication."""

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ADMINISTRATOR,
    ARCHIVED,
    DELEGABLE_PERMISSIONS,
    OWNER,
    SUSPENDED,
    TUTOR,
    USER_ADMIN_ROLE_ASSIGN,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, events, live_count, me
from tests.integration.user_admin_support import (
    delegations_of,
    put_delegations,
    put_roles,
    roles_of,
    version_of,
)

OK = (200, None)
DENIED = (403, "permission_denied")
REAUTH_REQUIRED = (403, "reauth_required")
NOT_FOUND = (404, "not_found")
CONFLICT = (409, "version_conflict")
INVALID = (422, "validation_error")
TARGET_INVALID = (422, "delegation_target_invalid")

REFUNDS = "finance.refund.approve"
PAYROLL = "payroll.finalize"


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
    permissions: Any,
    version: int | None = None,
) -> tuple[int, str | None]:
    if version is None:
        version = version_of(url, user_id)
    return code_of(put_delegations(client, cookie, user_id, permissions, version))


def audit_ids(url: str) -> set[uuid.UUID]:
    return {
        event.id for event in events(url) if event.action != "identity.reauthenticated"
    }


# --- C3, C4: a delegation takes effect without a sign-out --------------------------


def test_a_delegation_reaches_the_administrators_existing_session(
    module_seeded_url, client, clock, owner, admin
):
    admin_cookie = staff(client, admin.username)
    person = create_user(module_seeded_url, roles=(TUTOR,))
    assert USER_ADMIN_ROLE_ASSIGN not in me(client, admin_cookie).json()["permissions"]
    owner_cookie = staff(client, owner.username)

    response = put_delegations(
        client, owner_cookie, admin.id, [USER_ADMIN_ROLE_ASSIGN], 1
    )

    assert code_of(response) == OK
    body = response.json()
    assert (body["delegations"], body["version"]) == ([USER_ADMIN_ROLE_ASSIGN], 2)
    assert body["allowed_actions"]["manage_delegations"] is True
    # The same session, no new login (C3).
    assert live_count(module_seeded_url, admin.id) == 1
    assert USER_ADMIN_ROLE_ASSIGN in me(client, admin_cookie).json()["permissions"]
    added = put_roles(client, admin_cookie, person.id, [ADMINISTRATOR, TUTOR], 1)
    assert code_of(added) == OK
    assert roles_of(module_seeded_url, person.id) == [ADMINISTRATOR, TUTOR]

    revoked = put_delegations(client, owner_cookie, admin.id, [], 2)

    assert code_of(revoked) == OK
    assert revoked.json()["delegations"] == []
    assert live_count(module_seeded_url, admin.id) == 1
    assert USER_ADMIN_ROLE_ASSIGN not in me(client, admin_cookie).json()["permissions"]
    removed = put_roles(client, admin_cookie, person.id, [TUTOR], 2)
    assert code_of(removed) == DENIED


def test_grant_and_revoke_several_at_once(
    module_seeded_url, client, clock, owner, admin
):
    cookie = staff(client, owner.username)

    every = sorted(DELEGABLE_PERMISSIONS)
    assert change(client, cookie, module_seeded_url, admin.id, every) == OK
    assert delegations_of(module_seeded_url, admin.id) == every
    assert change(client, cookie, module_seeded_url, admin.id, [PAYROLL]) == OK
    assert delegations_of(module_seeded_url, admin.id) == [PAYROLL]


# --- C2, B2: valid targets ----------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "state"),
    [((TUTOR,), "active"), ((OWNER,), "active"), ((ADMINISTRATOR,), ARCHIVED)],
)
def test_granting_to_a_non_administrator_or_an_archived_one_is_422(
    module_seeded_url, client, clock, owner, roles, state
):
    target = create_user(module_seeded_url, roles=roles, state=state)
    cookie = staff(client, owner.username)
    before = audit_ids(module_seeded_url)

    result = change(client, cookie, module_seeded_url, target.id, [REFUNDS])

    assert result == TARGET_INVALID
    assert delegations_of(module_seeded_url, target.id) == []
    assert version_of(module_seeded_url, target.id) == 1
    assert audit_ids(module_seeded_url) == before


def test_a_suspended_administrator_can_receive_a_delegation(
    module_seeded_url, client, clock, owner
):
    target = create_user(module_seeded_url, roles=(ADMINISTRATOR,), state=SUSPENDED)

    result = change(
        client, staff(client, owner.username), module_seeded_url, target.id, [REFUNDS]
    )

    assert result == OK


@pytest.mark.parametrize(
    ("roles", "state"), [((ADMINISTRATOR,), ARCHIVED), ((TUTOR,), "active")]
)
def test_revoking_only_works_for_any_user(
    module_seeded_url, client, clock, owner, roles, state
):
    target = create_user(
        module_seeded_url, roles=roles, state=state, delegations=(REFUNDS, PAYROLL)
    )
    cookie = staff(client, owner.username)

    assert change(client, cookie, module_seeded_url, target.id, [PAYROLL]) == OK
    assert delegations_of(module_seeded_url, target.id) == [PAYROLL]
    # Adding back is a grant again: refused.
    assert change(client, cookie, module_seeded_url, target.id, [PAYROLL, REFUNDS]) == (
        TARGET_INVALID
    )
    assert change(client, cookie, module_seeded_url, target.id, []) == OK
    assert delegations_of(module_seeded_url, target.id) == []


# --- C1, C2: access, bodies, order, versions ----------------------------------------


def test_an_administrator_holding_every_delegation_is_denied(
    module_seeded_url, client, clock
):
    delegated = create_user(
        module_seeded_url,
        roles=(ADMINISTRATOR,),
        delegations=tuple(sorted(DELEGABLE_PERMISSIONS)),
    )
    other = create_user(module_seeded_url, roles=(ADMINISTRATOR,))

    result = change(
        client,
        staff(client, delegated.username),
        module_seeded_url,
        other.id,
        [PAYROLL],
    )

    assert result == DENIED
    assert delegations_of(module_seeded_url, other.id) == []


def test_without_a_reauthentication_reauth_required(
    module_seeded_url, client, clock, owner, admin
):
    result = change(
        client, sign_in(client, owner.username), module_seeded_url, admin.id, [PAYROLL]
    )

    assert result == REAUTH_REQUIRED
    assert delegations_of(module_seeded_url, admin.id) == []


@pytest.mark.parametrize(
    "permissions",
    [
        [PAYROLL, PAYROLL],
        ["user.delegation.manage"],
        ["user.view"],
        [*sorted(DELEGABLE_PERMISSIONS), PAYROLL],
        None,
    ],
)
def test_invalid_bodies_are_422(
    module_seeded_url, client, clock, owner, admin, permissions
):
    cookie = staff(client, owner.username)

    assert change(client, cookie, module_seeded_url, admin.id, permissions) == INVALID
    assert delegations_of(module_seeded_url, admin.id) == []


def test_check_order_422_then_404_then_409_then_target(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = staff(client, owner.username)

    assert code_of(put_delegations(client, cookie, uuid.uuid4(), ["x"], 7)) == INVALID
    assert code_of(put_delegations(client, cookie, uuid.uuid4(), [], 7)) == NOT_FOUND
    assert code_of(put_delegations(client, cookie, person.id, [PAYROLL], 7)) == (
        CONFLICT
    )
    assert code_of(put_delegations(client, cookie, person.id, [PAYROLL], 1)) == (
        TARGET_INVALID
    )


def test_the_same_set_is_a_no_op(module_seeded_url, client, clock, owner):
    target = create_user(
        module_seeded_url, roles=(ADMINISTRATOR,), delegations=(PAYROLL,)
    )
    cookie = staff(client, owner.username)
    before = audit_ids(module_seeded_url)

    response = put_delegations(client, cookie, target.id, [PAYROLL], 1)

    assert code_of(response) == OK
    assert response.json()["version"] == 1
    assert audit_ids(module_seeded_url) == before


def test_concurrent_changes_with_one_version_give_one_200_and_one_409(
    module_seeded_url, client, clock, owner, admin
):
    cookie = staff(client, owner.username)
    results: list[tuple[int, str | None]] = []
    start = threading.Barrier(2)

    def put(permissions: list[str]) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results.append(
                code_of(put_delegations(own_client, cookie, admin.id, permissions, 1))
            )

    threads = [
        threading.Thread(target=put, args=([PAYROLL],)),
        threading.Thread(target=put, args=([PAYROLL, REFUNDS],)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == sorted([OK, CONFLICT], key=str)
    assert version_of(module_seeded_url, admin.id) == 2


# --- D2: audit ----------------------------------------------------------------------


def test_a_change_records_exactly_its_rows(module_seeded_url, client, clock, owner):
    target = create_user(
        module_seeded_url, roles=(ADMINISTRATOR,), delegations=(PAYROLL,)
    )
    cookie = staff(client, owner.username)
    before = audit_ids(module_seeded_url)

    response = put_delegations(client, cookie, target.id, [REFUNDS], 1)

    assert code_of(response) == OK
    new = [
        e
        for e in events(module_seeded_url)
        if e.id not in before and e.action != "identity.reauthenticated"
    ]
    # Granted, then revoked; no session revocation and no users update.
    assert [(e.record_type, e.action) for e in new] == [
        ("user_delegations", "create"),
        ("user_delegations", "delete"),
    ]
    granted, revoked = new
    assert granted.changes["user_id"]["new"] == str(target.id)
    assert granted.changes["granted_by_user_id"]["new"] == str(owner.id)
    assert revoked.changes["user_id"]["old"] == str(target.id)
    request_id = response.headers["X-Request-ID"]
    for event in new:
        assert event.actor_user_id == owner.id
        assert event.request_id == request_id
        for key in (event.details or {}) | (event.changes or {}):
            assert not any(
                word in key for word in ("token", "password", "secret", "hash")
            )


@pytest.mark.parametrize("case", ["forbidden", "not_found", "conflict", "invalid"])
def test_failed_changes_write_nothing(module_seeded_url, client, clock, owner, case):
    target = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    caller = (
        create_user(module_seeded_url, roles=(ADMINISTRATOR,))
        if case == "forbidden"
        else owner
    )
    cookie = staff(client, caller.username)
    before = audit_ids(module_seeded_url)

    target_id: Any = uuid.uuid4() if case == "not_found" else target.id
    permissions = ["user.view"] if case == "invalid" else [PAYROLL]
    version = 4 if case == "conflict" else 1
    response = put_delegations(client, cookie, target_id, permissions, version)

    assert response.status_code in (403, 404, 409, 422)
    assert audit_ids(module_seeded_url) == before
