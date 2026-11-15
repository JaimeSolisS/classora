"""Suspend, archive and reactivate (#31 G, H) with real logins and the #26
reauthentication."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ACTIVE,
    ADMINISTRATOR,
    ARCHIVED,
    GUARDIAN,
    OWNER,
    PENDING,
    STUDENT,
    SUSPENDED,
    TUTOR,
    USER_ADMIN_ROLE_ASSIGN,
)
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    create_user,
    login,
    signed_headers,
)
from tests.integration.invitation_support import (
    invite,
    lookup,
    reauthenticate,
    sign_in,
    token_of,
)
from tests.integration.lifecycle_support import (
    code_of,
    events,
    live_count,
    me,
    no_secret_in_audit,
    signed_in,
)
from tests.integration.registration_support import register
from tests.integration.user_admin_support import (
    list_users,
    state_action,
    unique_token,
    user_row,
    version_of,
)

OK = (200, None)
UNAUTHENTICATED = (401, "authentication_required")
INVALID_CREDENTIALS = (401, "invalid_credentials")
DENIED = (403, "permission_denied")
REAUTH_REQUIRED = (403, "reauth_required")
NOT_FOUND = (404, "not_found")
CONFLICT = (409, "version_conflict")
TRANSITION = (409, "user_state_transition_invalid")
INVALID = (422, "validation_error")
GONE = (410, "token_invalid")

REVOKED_EVENT = "identity.sessions_revoked"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


def staff(client: TestClient, username: str) -> SetCookie:
    """Sign in and reauthenticate."""
    cookie = sign_in(client, username)
    reauthenticate(client, cookie)
    return cookie


def act(
    client: TestClient,
    cookie: SetCookie,
    user_id: Any,
    action: str,
    url: str | None = None,
    version: int | None = None,
) -> tuple[int, str | None]:
    if version is None:
        assert url is not None
        version = version_of(url, user_id)
    return code_of(state_action(client, cookie, user_id, action, version))


def state_of(url: str, user_id: Any) -> str:
    state: str = user_row(url, user_id).state
    return state


# --- G4: the suspension round trip --------------------------------------------------


def test_suspend_ends_sessions_then_reactivate_allows_login_but_not_old_cookies(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    first = signed_in(client, person.username)
    second = signed_in(client, person.username)
    cookie = staff(client, owner.username)

    response = state_action(client, cookie, person.id, "suspend", 1)

    assert code_of(response) == OK
    body = response.json()
    assert (body["state"], body["version"]) == (SUSPENDED, 2)
    assert body["allowed_actions"]["suspend"] is False
    assert body["allowed_actions"]["reactivate"] is True
    assert code_of(me(client, first)) == UNAUTHENTICATED
    assert code_of(me(client, second)) == UNAUTHENTICATED
    assert live_count(module_seeded_url, person.id) == 0
    client.cookies.clear()
    assert code_of(login(client, person.username)) == INVALID_CREDENTIALS

    reactivated = state_action(client, cookie, person.id, "reactivate", 2)

    assert code_of(reactivated) == OK
    assert reactivated.json()["state"] == ACTIVE
    client.cookies.clear()
    assert code_of(login(client, person.username, TEST_USER_PASSWORD)) == OK
    assert code_of(me(client, first)) == UNAUTHENTICATED
    assert code_of(me(client, second)) == UNAUTHENTICATED
    # The password was not touched.
    assert user_row(module_seeded_url, person.id).password_hash is not None


# --- G2: archive --------------------------------------------------------------------


def test_archive_a_suspended_user(module_seeded_url, client, clock, owner):
    person = create_user(module_seeded_url, roles=(GUARDIAN,), state=SUSPENDED)

    response = state_action(
        client, staff(client, owner.username), person.id, "archive", 1
    )

    assert code_of(response) == OK
    assert response.json()["state"] == ARCHIVED


def test_archive_an_active_user_ends_their_sessions(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(STUDENT,))
    session = signed_in(client, person.username)

    assert (
        act(
            client,
            staff(client, owner.username),
            person.id,
            "archive",
            module_seeded_url,
        )
        == OK
    )
    assert code_of(me(client, session)) == UNAUTHENTICATED


def test_archiving_an_open_invitation_cancels_its_link(
    module_seeded_url, client, clock, owner
):
    cookie = staff(client, owner.username)
    issued = invite(client, cookie, role=TUTOR)
    assert issued.status_code == 201
    token = token_of(issued)
    user_id = issued.json()["user_id"]
    assert code_of(lookup(client, token)) == OK

    response = state_action(client, cookie, user_id, "archive", 1)

    assert code_of(response) == OK
    assert response.json()["state"] == ARCHIVED
    assert response.json()["password_set"] is False
    assert code_of(lookup(client, token)) == GONE


def test_archiving_a_registration_request_is_refused(
    module_seeded_url, client, clock, owner
):
    token = unique_token()
    assert register(client, username=f"{token}.req").status_code == 202
    cookie = staff(client, owner.username)
    [item] = list_users(client, cookie, q=token).json()["items"]

    assert act(client, cookie, item["id"], "archive", module_seeded_url) == TRANSITION
    assert state_of(module_seeded_url, item["id"]) == PENDING


# --- G2: refused transitions --------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "action", "password"),
    [
        (ACTIVE, "reactivate", True),
        (PENDING, "reactivate", True),
        (PENDING, "reactivate", False),
        (SUSPENDED, "suspend", True),
        (ARCHIVED, "suspend", True),
        (PENDING, "suspend", False),
        (ARCHIVED, "archive", True),
    ],
)
def test_refused_transitions_are_409(
    module_seeded_url, client, clock, owner, state, action, password
):
    overrides: dict[str, Any] = {} if password else {"password_hash": None}
    person = create_user(module_seeded_url, roles=(TUTOR,), state=state, **overrides)

    result = act(
        client, staff(client, owner.username), person.id, action, module_seeded_url
    )

    assert result == TRANSITION
    assert state_of(module_seeded_url, person.id) == state
    assert version_of(module_seeded_url, person.id) == 1


def test_reactivating_an_archived_invitation_leaves_it_without_a_password(
    module_seeded_url, client, clock, owner
):
    person = create_user(
        module_seeded_url, roles=(TUTOR,), state=ARCHIVED, password_hash=None
    )

    response = state_action(
        client, staff(client, owner.username), person.id, "reactivate", 1
    )

    assert code_of(response) == OK
    assert (response.json()["state"], response.json()["password_set"]) == (
        ACTIVE,
        False,
    )
    assert response.json()["allowed_actions"]["reset_password"] is True


# --- G1: reauthentication -----------------------------------------------------------


@pytest.mark.parametrize("action", ["suspend", "archive"])
def test_suspend_and_archive_need_a_recent_reauthentication(
    module_seeded_url, client, clock, owner, action
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = sign_in(client, owner.username)

    assert act(client, cookie, person.id, action, module_seeded_url) == REAUTH_REQUIRED
    assert state_of(module_seeded_url, person.id) == ACTIVE


def test_reactivate_needs_no_reauthentication(module_seeded_url, client, clock, owner):
    person = create_user(module_seeded_url, roles=(TUTOR,), state=SUSPENDED)

    assert (
        act(
            client,
            sign_in(client, owner.username),
            person.id,
            "reactivate",
            module_seeded_url,
        )
        == OK
    )


@pytest.mark.parametrize("action", ["suspend", "archive", "reactivate"])
def test_check_order_reauth_then_422_then_404_then_403_then_409(
    module_seeded_url, client, clock, action
):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    an_owner = create_user(module_seeded_url, roles=(OWNER,))
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = staff(client, admin.username)

    client.cookies.clear()
    empty = client.post(
        f"/api/v1/users/{uuid.uuid4()}/{action}",
        json={},
        headers=signed_headers(cookie),
    )
    assert code_of(empty) == INVALID
    assert act(client, cookie, uuid.uuid4(), action, version=7) == NOT_FOUND
    assert act(client, cookie, an_owner.id, action, version=7) == DENIED
    assert act(client, cookie, person.id, action, version=7) == CONFLICT


# --- B1, B2: who may change whose state [E2] ----------------------------------------


@pytest.mark.parametrize("action", ["suspend", "archive"])
def test_an_administrator_cannot_suspend_or_archive_an_owner(
    module_seeded_url, client, clock, action
):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    an_owner = create_user(module_seeded_url, roles=(OWNER,))

    assert (
        act(
            client,
            staff(client, admin.username),
            an_owner.id,
            action,
            module_seeded_url,
        )
        == DENIED
    )
    assert state_of(module_seeded_url, an_owner.id) == ACTIVE


def test_an_administrator_needs_the_delegation_to_suspend_an_administrator(
    module_seeded_url, client, clock
):
    plain = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    delegated = create_user(
        module_seeded_url, roles=(ADMINISTRATOR,), delegations=(USER_ADMIN_ROLE_ASSIGN,)
    )
    target = create_user(module_seeded_url, roles=(ADMINISTRATOR,))

    assert (
        act(
            client,
            staff(client, plain.username),
            target.id,
            "suspend",
            module_seeded_url,
        )
        == DENIED
    )
    assert (
        act(
            client,
            staff(client, delegated.username),
            target.id,
            "suspend",
            module_seeded_url,
        )
        == OK
    )
    assert state_of(module_seeded_url, target.id) == SUSPENDED


def test_an_administrator_suspends_a_tutor(module_seeded_url, client, clock):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    person = create_user(module_seeded_url, roles=(TUTOR, GUARDIAN))

    assert (
        act(
            client,
            staff(client, admin.username),
            person.id,
            "suspend",
            module_seeded_url,
        )
        == OK
    )


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR])
@pytest.mark.parametrize("action", ["suspend", "archive", "reactivate"])
def test_nobody_changes_their_own_state(module_seeded_url, client, clock, role, action):
    me_user = create_user(module_seeded_url, roles=(role,))
    # A second Owner, so the last-Owner guard is not what refuses it.
    create_user(module_seeded_url, roles=(OWNER,))

    assert (
        act(
            client,
            staff(client, me_user.username),
            me_user.id,
            action,
            module_seeded_url,
        )
        == DENIED
    )
    assert state_of(module_seeded_url, me_user.id) == ACTIVE


def test_a_tutor_gets_permission_denied_before_reauth(module_seeded_url, client, clock):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    other = create_user(module_seeded_url, roles=(TUTOR,))

    assert (
        act(
            client,
            sign_in(client, person.username),
            other.id,
            "suspend",
            module_seeded_url,
        )
        == DENIED
    )


# --- G2, G4: versions and the guard over HTTP ---------------------------------------


def test_a_stale_version_is_409_and_nothing_changes(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    session = signed_in(client, person.username)
    cookie = staff(client, owner.username)
    before = [e.id for e in events(module_seeded_url, record_id=person.id)]

    assert act(client, cookie, person.id, "suspend", version=2) == CONFLICT

    assert state_of(module_seeded_url, person.id) == ACTIVE
    assert code_of(me(client, session)) == OK
    assert [e.id for e in events(module_seeded_url, record_id=person.id)] == before


def test_with_two_active_owners_one_suspends_the_other(
    module_seeded_url, client, clock
):
    first = create_user(module_seeded_url, roles=(OWNER,))
    second = create_user(module_seeded_url, roles=(OWNER,))

    assert (
        act(
            client,
            staff(client, first.username),
            second.id,
            "suspend",
            module_seeded_url,
        )
        == OK
    )
    assert state_of(module_seeded_url, second.id) == SUSPENDED


# --- H: audit -----------------------------------------------------------------------


def test_a_suspension_records_the_state_change_and_the_revocation(
    module_seeded_url, client, clock, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    signed_in(client, person.username)
    signed_in(client, person.username)
    cookie = staff(client, owner.username)
    before = {e.id for e in events(module_seeded_url, record_id=person.id)}

    response = state_action(client, cookie, person.id, "suspend", 1)

    assert code_of(response) == OK
    new = [
        e for e in events(module_seeded_url, record_id=person.id) if e.id not in before
    ]
    assert [(e.action, e.record_type) for e in new] == [
        ("update", "users"),
        (REVOKED_EVENT, "users"),
    ]
    update, revoked = new
    assert update.changes == {"state": {"old": ACTIVE, "new": SUSPENDED}}
    assert revoked.details == {"reason": "suspended", "count": 2}
    request_id = response.headers["X-Request-ID"]
    for event in new:
        assert event.actor_user_id == owner.id
        assert event.request_id == request_id
    no_secret_in_audit(module_seeded_url, "$argon2", TEST_USER_PASSWORD)


@pytest.mark.parametrize(
    "case", ["forbidden", "not_found", "conflict", "invalid", "transition"]
)
def test_failed_state_changes_write_nothing(module_seeded_url, client, clock, case):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    target = create_user(
        module_seeded_url,
        roles=(OWNER,) if case == "forbidden" else (TUTOR,),
        state=SUSPENDED if case == "transition" else ACTIVE,
    )
    cookie = staff(client, admin.username)
    before = [
        e.id
        for e in events(module_seeded_url, record_type="users")
        if e.action != "identity.reauthenticated"
    ]

    target_id: Any = uuid.uuid4() if case == "not_found" else target.id
    version: Any = {"conflict": 4, "invalid": "x"}.get(case, 1)
    response = state_action(client, cookie, target_id, "suspend", version)

    assert response.status_code in (403, 404, 409, 422)
    after = [
        e.id
        for e in events(module_seeded_url, record_type="users")
        if e.action != "identity.reauthenticated"
    ]
    assert after == before
