"""Issuing a password reset (#30 C, G) with real logins and reauthentication."""

import hashlib
import uuid
from collections.abc import Iterator
from datetime import timedelta

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
from app.modules.identity.sessions import decode_token
from tests.conftest import CLOCK_START
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import (
    code_of,
    events,
    me,
    no_secret_in_audit,
    signed_in,
)
from tests.integration.recovery_support import (
    ISSUED_EVENT,
    PASSWORD_RESETS,
    RESET_URL_PREFIX,
    issue_reset,
    reset_lookup,
    reset_rows,
    reset_token_of,
    staff_reset,
)

CREATED = (201, None)
OK = (200, None)
DENIED = (403, "permission_denied")
REAUTH_REQUIRED = (403, "reauth_required")
NOT_FOUND = (404, "not_found")
NOT_ACTIVE = (409, "user_not_active")
GONE = (410, "token_invalid")


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


def target(url: str, role: str, **overrides: object) -> CreatedUser:
    return create_user(url, roles=(role,), **overrides)


# --- C5: who may reset whom ---


@pytest.mark.parametrize("role", [TUTOR, ACADEMIC_COORDINATOR, ADMINISTRATOR, OWNER])
def test_an_owner_resets_any_role_after_reauthenticating(
    module_seeded_url, client, clock, owner, role
):
    person = target(module_seeded_url, role)

    response = staff_reset(client, owner.username, person.username)

    assert code_of(response) == CREATED
    body = response.json()
    assert body["user_id"] == str(person.id)
    assert body["username"] == person.username
    assert body["expires_at"] == "2026-10-19T12:00:00Z"
    assert set(body) == {
        "user_id",
        "username",
        "display_name",
        "expires_at",
        "reset_url",
    }


def test_an_owner_without_reauthentication_gets_reauth_required(
    module_seeded_url, client, clock, owner
):
    person = target(module_seeded_url, TUTOR)
    cookie = sign_in(client, owner.username)

    assert code_of(issue_reset(client, cookie, person.username)) == REAUTH_REQUIRED
    assert reset_rows(module_seeded_url, person.id) == []


@pytest.mark.parametrize(
    "role", [TUTOR, GUARDIAN, STUDENT, ACADEMIC_COORDINATOR, FINANCE_STAFF]
)
def test_an_administrator_resets_the_other_roles(
    module_seeded_url, client, clock, admin, role
):
    person = target(module_seeded_url, role)

    assert code_of(staff_reset(client, admin.username, person.username)) == CREATED


@pytest.mark.parametrize("role", [ADMINISTRATOR, OWNER])
@pytest.mark.parametrize("delegated", [False, True], ids=["plain", "delegated"])
def test_an_administrator_cannot_reset_an_owner_or_administrator(
    module_seeded_url, client, clock, role, delegated
):
    admin = create_user(
        module_seeded_url,
        roles=(ADMINISTRATOR,),
        delegations=("user.admin_role.assign",) if delegated else (),
    )
    person = target(module_seeded_url, role)

    assert code_of(staff_reset(client, admin.username, person.username)) == DENIED
    assert reset_rows(module_seeded_url, person.id) == []
    assert events(module_seeded_url, record_id=person.id, action=ISSUED_EVENT) == []


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN]
)
def test_other_roles_are_denied_without_being_asked_to_reauthenticate(
    module_seeded_url, client, clock, owner, role
):
    caller = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, caller.username)

    assert code_of(issue_reset(client, cookie, owner.username)) == DENIED


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR])
def test_nobody_resets_their_own_account(module_seeded_url, client, clock, role):
    caller = create_user(module_seeded_url, roles=(role,))

    response = staff_reset(client, caller.username, caller.username.upper())

    assert code_of(response) == DENIED
    assert reset_rows(module_seeded_url, caller.id) == []


# --- C2: username and state ---


@pytest.mark.parametrize("username", ["nobody.here.at.all", "a b", "ab", "josé"])
def test_an_unknown_or_invalid_username_is_404(
    module_seeded_url, client, clock, owner, username
):
    assert code_of(staff_reset(client, owner.username, username)) == NOT_FOUND


def test_a_username_differing_only_in_case_finds_the_user(
    module_seeded_url, client, clock, owner
):
    person = target(module_seeded_url, TUTOR)

    response = staff_reset(client, owner.username, f"  {person.username.upper()} ")

    assert code_of(response) == CREATED
    assert response.json()["user_id"] == str(person.id)


@pytest.mark.parametrize(
    ("state", "password_hash"),
    [("pending", None), ("suspended", "keep"), ("archived", "keep")],
)
def test_a_user_who_is_not_active_is_409_with_no_token_and_no_event(
    module_seeded_url, client, clock, owner, state, password_hash
):
    overrides: dict[str, object] = {"state": state}
    if password_hash is None:
        overrides["password_hash"] = None
    person = target(module_seeded_url, TUTOR, **overrides)

    assert code_of(staff_reset(client, owner.username, person.username)) == NOT_ACTIVE
    assert reset_rows(module_seeded_url, person.id) == []
    assert events(module_seeded_url, record_id=person.id, action=ISSUED_EVENT) == []
    [row] = run_sql(
        module_seeded_url, "SELECT state FROM users WHERE id = :id", {"id": person.id}
    )
    assert row.state == state


@pytest.mark.parametrize(
    "body", [{}, {"username": ""}, {"username": "x" * 257}, {"username": 7}]
)
def test_an_invalid_body_is_422(module_seeded_url, client, clock, owner, body):
    cookie = sign_in(client, owner.username)
    reauthenticate(client, cookie)
    client.cookies.clear()

    response = client.post(PASSWORD_RESETS, json=body, headers=signed_headers(cookie))

    assert code_of(response) == (422, "validation_error")


# --- C3: the link, replacement, sessions untouched ---


def test_the_link_holds_a_43_character_token_whose_hash_is_stored(
    module_seeded_url, client, clock, owner
):
    person = target(module_seeded_url, TUTOR)

    response = staff_reset(client, owner.username, person.username)

    assert response.json()["reset_url"].startswith(RESET_URL_PREFIX)
    raw = reset_token_of(response)
    token = decode_token(raw)
    assert token is not None
    [row] = reset_rows(module_seeded_url, person.id)
    assert row.kind == "password_reset"
    assert bytes(row.token_hash) == hashlib.sha256(token).digest()
    assert row.issued_by_user_id == owner.id
    assert (row.used_at, row.revoked_at) == (None, None)


def test_a_second_reset_replaces_the_first(module_seeded_url, client, clock, owner):
    person = target(module_seeded_url, TUTOR)
    first = reset_token_of(staff_reset(client, owner.username, person.username))

    second = reset_token_of(staff_reset(client, owner.username, person.username))

    assert code_of(reset_lookup(client, first)) == GONE
    assert code_of(reset_lookup(client, second)) == OK
    rows = reset_rows(module_seeded_url, person.id)
    assert [(r.revoked_at is not None) for r in rows] == [True, False]


def test_the_targets_session_still_works_after_issue(
    module_seeded_url, client, clock, owner
):
    person = target(module_seeded_url, TUTOR)
    session = signed_in(client, person.username)

    assert code_of(staff_reset(client, owner.username, person.username)) == CREATED

    assert code_of(me(client, session)) == OK


def test_an_open_invitation_link_is_not_touched_by_a_reset(
    module_seeded_url, client, clock, owner
):
    # The two kinds are independent (one live token per user and kind).
    person = target(module_seeded_url, TUTOR)
    run_sql(
        module_seeded_url,
        "INSERT INTO account_tokens (user_id, kind, token_hash, expires_at)"
        " VALUES (:id, 'invitation', :hash, :expires)",
        {
            "id": person.id,
            "hash": uuid.uuid4().bytes * 2,
            "expires": CLOCK_START + timedelta(hours=24),
        },
    )

    assert code_of(staff_reset(client, owner.username, person.username)) == CREATED

    rows = run_sql(
        module_seeded_url,
        "SELECT kind, revoked_at FROM account_tokens WHERE user_id = :id ORDER BY kind",
        {"id": person.id},
    )
    assert [(r.kind, r.revoked_at) for r in rows] == [
        ("invitation", None),
        ("password_reset", None),
    ]


# --- G1, G2: audit ---


def test_issue_writes_one_named_event_without_secrets(
    module_seeded_url, client, clock, owner
):
    person = target(module_seeded_url, TUTOR)

    raw = reset_token_of(staff_reset(client, owner.username, person.username))

    [event] = events(module_seeded_url, record_id=person.id, action=ISSUED_EVENT)
    [row] = reset_rows(module_seeded_url, person.id)
    assert event.record_type == "users"
    assert event.actor_user_id == owner.id
    assert event.details == {
        "link_id": str(row.id),
        "expires_at": "2026-10-19T12:00:00+00:00",
        "source": "staff",
    }
    no_secret_in_audit(module_seeded_url, raw, bytes(row.token_hash).hex())
    # The issue writes no session revocation (sessions end at redemption).
    assert (
        events(
            module_seeded_url, record_id=person.id, action="identity.sessions_revoked"
        )
        == []
    )


def test_refused_issues_write_no_event(module_seeded_url, client, clock, owner, admin):
    other_owner = target(module_seeded_url, OWNER)
    suspended = target(module_seeded_url, TUTOR, state="suspended")
    before = len(events(module_seeded_url, action=ISSUED_EVENT))

    staff_reset(client, owner.username, "nobody.at.all")  # 404
    staff_reset(client, owner.username, suspended.username)  # 409
    staff_reset(client, owner.username, owner.username)  # 403 self
    staff_reset(client, admin.username, other_owner.username)  # 403 owner target
    cookie = sign_in(client, owner.username)
    issue_reset(client, cookie, other_owner.username)  # 403 reauth_required

    assert len(events(module_seeded_url, action=ISSUED_EVENT)) == before
