"""Creating, listing and reissuing invitations (#29 D6, F1, F3) with real
logins, the real reauthentication route and the ``clock`` fixture."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.reauth import RecentAuthGuard
from app.core.uow import UnitOfWork
from app.modules.identity import invitations
from app.modules.identity.errors import UsernameTakenError
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    ARCHIVED,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_CODES,
    ROLE_PERMISSIONS,
    STAFF_ROLES,
    STUDENT,
    TUTOR,
    USER_ADMIN_ROLE_ASSIGN,
)
from tests.conftest import CLOCK_START
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    create_user,
    login,
    signed_headers,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.invitation_support import (
    INVITE_URL_PREFIX,
    NEW_PASSWORD,
    TOKEN_PATTERN,
    invite,
    list_invitations,
    lookup,
    reauthenticate,
    redeem,
    reissue,
    sign_in,
    token_of,
    unique_username,
)
from tests.integration.lifecycle_support import code_of, events, no_secret_in_audit

CREATED = (201, None)
OK = (200, None)
REAUTH_REQUIRED = (403, "reauth_required")
PERMISSION_DENIED = (403, "permission_denied")
NON_STAFF = [r for r in ROLE_CODES if r not in STAFF_ROLES]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def administrator(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ADMINISTRATOR,))


def user_row(url: str, user_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT username, display_name, state, password_hash FROM users WHERE id = :id",
        {"id": uuid.UUID(str(user_id))},
    )
    return row


def roles_of(url: str, user_id: Any) -> list[str]:
    rows = run_sql(
        url,
        "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
        " WHERE ur.user_id = :id ORDER BY r.code",
        {"id": uuid.UUID(str(user_id))},
    )
    return [row.code for row in rows]


# --- D6: who may invite which role ---


def test_owner_invites_every_role_reauthenticating_for_the_staff_roles(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)

    for role in STAFF_ROLES:
        assert code_of(invite(client, cookie, role=role)) == REAUTH_REQUIRED, role
    for role in NON_STAFF:
        assert code_of(invite(client, cookie, role=role)) == CREATED, role

    reauthenticate(client, cookie)
    for role in STAFF_ROLES:
        response = invite(client, cookie, role=role)
        assert code_of(response) == CREATED, role
        assert response.json()["roles"] == [role]


def test_administrator_invites_the_roles_it_may_assign(
    module_seeded_url, client, clock, administrator
):
    cookie = sign_in(client, administrator.username)

    for role in (TUTOR, GUARDIAN, STUDENT):
        assert code_of(invite(client, cookie, role=role)) == CREATED, role
    for role in (ACADEMIC_COORDINATOR, FINANCE_STAFF):
        assert code_of(invite(client, cookie, role=role)) == REAUTH_REQUIRED, role
    # Never asked for a password for a role it cannot assign.
    for role in (ADMINISTRATOR, OWNER):
        assert code_of(invite(client, cookie, role=role)) == PERMISSION_DENIED, role

    reauthenticate(client, cookie)
    for role in (ACADEMIC_COORDINATOR, FINANCE_STAFF):
        assert code_of(invite(client, cookie, role=role)) == CREATED, role
    for role in (ADMINISTRATOR, OWNER):
        assert code_of(invite(client, cookie, role=role)) == PERMISSION_DENIED, role


def test_delegated_administrator_invites_an_administrator_with_reauth(
    module_seeded_url, client, clock
):
    delegated = create_user(
        module_seeded_url,
        roles=(ADMINISTRATOR,),
        delegations=(USER_ADMIN_ROLE_ASSIGN,),
    )
    cookie = sign_in(client, delegated.username)

    assert code_of(invite(client, cookie, role=ADMINISTRATOR)) == REAUTH_REQUIRED
    reauthenticate(client, cookie)
    assert code_of(invite(client, cookie, role=ADMINISTRATOR)) == CREATED
    assert code_of(invite(client, cookie, role=OWNER)) == PERMISSION_DENIED


@pytest.mark.parametrize(
    "role", [r for r in ROLE_CODES if r not in (OWNER, ADMINISTRATOR)]
)
def test_roles_without_user_invite_are_denied(module_seeded_url, client, role):
    member = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, member.username)

    assert code_of(invite(client, cookie, role=TUTOR)) == PERMISSION_DENIED
    assert code_of(list_invitations(client, cookie)) == PERMISSION_DENIED


def test_the_invited_user_is_pending_without_a_password_and_has_the_role(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)
    username = unique_username("Mixed.Case")

    response = invite(
        client, cookie, role=TUTOR, username=f"  {username}  ", display_name="  Ana  "
    )

    assert code_of(response) == CREATED
    body = response.json()
    assert set(body) == {
        "user_id",
        "username",
        "display_name",
        "roles",
        "expires_at",
        "invitation_url",
    }
    assert body["username"] == username.lower()
    assert body["display_name"] == "Ana"
    assert body["roles"] == [TUTOR]
    assert datetime.fromisoformat(body["expires_at"]) == CLOCK_START + timedelta(
        hours=24
    )
    assert body["invitation_url"].startswith(INVITE_URL_PREFIX)
    assert TOKEN_PATTERN.fullmatch(token_of(response))
    row = user_row(module_seeded_url, body["user_id"])
    assert (row.username, row.display_name, row.state, row.password_hash) == (
        username.lower(),
        "Ana",
        "pending",
        None,
    )
    assert roles_of(module_seeded_url, body["user_id"]) == [TUTOR]
    for password in ("x", TEST_USER_PASSWORD, NEW_PASSWORD):
        response = login(client, username.lower(), password)
        assert code_of(response) == (401, "invalid_credentials")


# --- D6: username errors ---


def test_a_taken_username_is_409_in_any_case_and_state(
    module_seeded_url, client, clock, owner
):
    existing = create_user(module_seeded_url, roles=(TUTOR,))
    archived = create_user(module_seeded_url, roles=(TUTOR,), state=ARCHIVED)
    cookie = sign_in(client, owner.username)

    for username in (existing.username, existing.username.upper(), archived.username):
        response = invite(client, cookie, role=TUTOR, username=username)
        assert code_of(response) == (409, "username_taken"), username
        assert username.lower() not in response.text
    invited = invite(client, cookie, role=TUTOR)
    again = invite(client, cookie, role=TUTOR, username=invited.json()["username"])
    assert code_of(again) == (409, "username_taken")


@pytest.mark.parametrize("username", ["a b", "ab", "ñandú", "x" * 65])
def test_an_invalid_username_is_422_username_invalid(
    module_seeded_url, client, owner, username
):
    cookie = sign_in(client, owner.username)

    assert code_of(invite(client, cookie, role=TUTOR, username=username)) == (
        422,
        "username_invalid",
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"username": "valid.name", "display_name": "   ", "role": TUTOR}),
        pytest.param(
            {"username": "valid.name", "display_name": "x" * 201, "role": TUTOR}
        ),
        pytest.param(
            {"username": "valid.name", "display_name": "A", "role": "janitor"}
        ),
        pytest.param({"username": "", "display_name": "A", "role": TUTOR}),
        pytest.param({"display_name": "A", "role": TUTOR}),
    ],
)
def test_an_invalid_body_is_422(module_seeded_url, client, owner, body):
    cookie = sign_in(client, owner.username)
    client.cookies.clear()
    response = client.post(
        "/api/v1/invitations", json=body, headers=signed_headers(cookie)
    )

    assert code_of(response) == (422, "validation_error")


def test_a_concurrent_invitation_for_the_same_username_is_409(module_seeded_url, owner):
    principal = Principal(
        user_id=owner.id,
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )
    username = unique_username()

    async def create(sessionmaker: Any) -> str:
        try:
            async with UnitOfWork(sessionmaker, owner.id) as uow:
                await invitations.create_invitation(
                    uow.session,
                    principal=principal,
                    guard=RecentAuthGuard("user.invitation.staff_role", None),
                    username=username,
                    display_name="Twin",
                    role=TUTOR,
                    app_origin="http://127.0.0.1:5173",
                )
        except UsernameTakenError:
            return "taken"
        return "created"

    async def run() -> list[str]:
        engine = short_lived_engine(module_seeded_url)
        try:
            sessionmaker = create_sessionmaker(engine)
            return list(
                await asyncio.gather(create(sessionmaker), create(sessionmaker))
            )
        finally:
            await engine.dispose()

    assert sorted(asyncio.run(run())) == ["created", "taken"]
    rows = run_sql(
        module_seeded_url,
        "SELECT count(*) FROM users WHERE username = :u",
        {"u": username},
    )
    assert rows[0][0] == 1


# --- D4, D6: the list ---


def listed(
    client: TestClient, cookie: SetCookie, user_id: str
) -> dict[str, Any] | None:
    response = list_invitations(client, cookie, limit=100)
    assert code_of(response) == OK
    return next(
        (item for item in response.json()["items"] if item["user_id"] == user_id),
        None,
    )


def test_the_list_shows_active_then_expired_then_drops_redeemed(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)
    issued = invite(client, cookie, role=GUARDIAN, display_name="Gloria")
    user_id = issued.json()["user_id"]
    token = token_of(issued)

    item = listed(client, cookie, user_id)
    assert item is not None
    issued_at = item.pop("issued_at")
    assert datetime.fromisoformat(issued_at).tzinfo is not None
    assert item == {
        "user_id": user_id,
        "username": issued.json()["username"],
        "display_name": "Gloria",
        "roles": [GUARDIAN],
        "status": "active",
        "expires_at": issued.json()["expires_at"],
    }
    page = list_invitations(client, cookie, limit=100)
    assert token not in page.text

    clock.set(CLOCK_START + timedelta(hours=24))
    cookie = sign_in(client, owner.username)
    assert (listed(client, cookie, user_id) or {})["status"] == "expired"

    clock.set(CLOCK_START)
    assert code_of(redeem(client, token)) == (204, None)
    assert listed(client, cookie, user_id) is None


def test_the_list_is_newest_first_and_paginated(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)
    first = invite(client, cookie, role=TUTOR).json()["user_id"]
    second = invite(client, cookie, role=TUTOR).json()["user_id"]

    page = list_invitations(client, cookie, limit=1).json()
    assert page["limit"] == 1
    assert page["offset"] == 0
    assert page["total"] >= 2
    assert page["items"][0]["user_id"] == second
    ids = [
        i["user_id"]
        for i in list_invitations(client, cookie, limit=100).json()["items"]
    ]
    assert ids.index(second) < ids.index(first)


def test_archived_and_active_users_are_not_listed(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)
    archived = invite(client, cookie, role=TUTOR).json()["user_id"]
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'archived' WHERE id = :id",
        {"id": uuid.UUID(archived)},
    )
    plain = create_user(module_seeded_url, roles=(TUTOR,), state="pending")

    assert listed(client, cookie, archived) is None
    assert listed(client, cookie, str(plain.id)) is None


# --- D5, D6: reissue ---


def test_reissue_replaces_the_link(module_seeded_url, client, clock, owner):
    cookie = sign_in(client, owner.username)
    issued = invite(client, cookie, role=TUTOR)
    old = token_of(issued)
    # Within the staff idle timeout, so the owner's session stays valid.
    clock.advance(timedelta(minutes=10))

    again = reissue(client, cookie, issued.json()["user_id"])

    assert code_of(again) == OK
    new = token_of(again)
    assert new != old
    assert again.json()["user_id"] == issued.json()["user_id"]
    assert datetime.fromisoformat(
        again.json()["expires_at"]
    ) == CLOCK_START + timedelta(hours=24, minutes=10)
    assert code_of(lookup(client, old)) == (410, "token_invalid")
    assert code_of(lookup(client, new)) == OK


def test_reissue_needs_reauth_for_a_staff_role(module_seeded_url, client, clock, owner):
    cookie = sign_in(client, owner.username)
    reauthenticate(client, cookie)
    issued = invite(client, cookie, role=FINANCE_STAFF)
    tutor = invite(client, cookie, role=TUTOR)
    clock.advance(timedelta(minutes=5))

    assert code_of(reissue(client, cookie, issued.json()["user_id"])) == REAUTH_REQUIRED
    assert code_of(reissue(client, cookie, tutor.json()["user_id"])) == OK
    reauthenticate(client, cookie)
    assert code_of(reissue(client, cookie, issued.json()["user_id"])) == OK


def test_reissue_of_anything_but_an_open_invitation_is_404(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)
    redeemed = invite(client, cookie, role=TUTOR)
    assert code_of(redeem(client, token_of(redeemed))) == (204, None)
    archived = invite(client, cookie, role=TUTOR).json()["user_id"]
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'archived' WHERE id = :id",
        {"id": uuid.UUID(archived)},
    )
    no_token = create_user(
        module_seeded_url, roles=(TUTOR,), state="pending", password_hash=None
    )

    for user_id in (
        redeemed.json()["user_id"],
        archived,
        no_token.id,
        owner.id,
        uuid.uuid4(),
    ):
        assert code_of(reissue(client, cookie, user_id)) == (404, "not_found"), user_id


def test_an_administrator_cannot_reissue_an_owner_invitation(
    module_seeded_url, client, clock, owner, administrator
):
    owner_cookie = sign_in(client, owner.username)
    reauthenticate(client, owner_cookie)
    issued = invite(client, owner_cookie, role=OWNER)
    admin_cookie = sign_in(client, administrator.username)

    assert code_of(reissue(client, admin_cookie, issued.json()["user_id"])) == (
        PERMISSION_DENIED
    )


# --- F1, F3: audit ---


def test_issue_and_reissue_write_named_events_without_secrets(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)
    issued = invite(client, cookie, role=GUARDIAN)
    user_id = uuid.UUID(issued.json()["user_id"])
    again = reissue(client, cookie, user_id)

    found = events(
        module_seeded_url, record_id=user_id, action="identity.invitation_issued"
    )
    assert len(found) == 2
    links = run_sql(
        module_seeded_url,
        "SELECT id, token_hash FROM account_tokens WHERE user_id = :id"
        " ORDER BY created_at, id",
        {"id": user_id},
    )
    for event, link, response, reissued in zip(
        found, links, (issued, again), (False, True), strict=True
    ):
        assert event.record_type == "users"
        assert event.actor_user_id == owner.id
        assert event.details == {
            "link_id": str(link.id),
            "roles": [GUARDIAN],
            "expires_at": datetime.fromisoformat(
                response.json()["expires_at"]
            ).isoformat(),
            "reissued": reissued,
        }
    automatic = {
        (e.record_type, e.action) for e in events(module_seeded_url, record_id=user_id)
    }
    assert ("users", "create") in automatic
    no_secret_in_audit(
        module_seeded_url,
        token_of(issued),
        token_of(again),
        *(bytes(link.token_hash).hex() for link in links),
    )


def test_failed_requests_write_no_invitation_event(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)
    before = len(events(module_seeded_url, action="identity.invitation_issued"))

    invite(client, cookie, role=OWNER)  # reauth_required
    invite(client, cookie, role=TUTOR, username="a b")  # username_invalid
    invite(client, cookie, role=TUTOR, username=owner.username)  # username_taken
    reissue(client, cookie, uuid.uuid4())  # not_found

    assert len(events(module_seeded_url, action="identity.invitation_issued")) == before
