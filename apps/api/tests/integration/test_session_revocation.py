"""Revocation on role and password changes, ``revoke_user_sessions`` and
delegations (#25 F1, F3, F4, F5, F6)."""

import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.authz import RecordNotFoundError
from app.modules.identity import service as identity
from app.modules.identity.models import User
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PAYROLL_FINALIZE,
    TUTOR,
)
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import auth_client, create_user, login
from tests.integration.lifecycle_support import (
    code_of,
    events,
    in_uow,
    live_count,
    me,
    no_secret_in_audit,
    session_rows,
    signed_in,
)

OK = (200, None)
UNAUTHENTICATED = (401, "authentication_required")
NEW_PASSWORD = "a-new-long-passphrase-7"  # noqa: S105 - test value


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def revocations(url: str, user_id: uuid.UUID) -> list[dict[str, object]]:
    return [
        e.details or {}
        for e in events(url, record_id=user_id, action="identity.sessions_revoked")
    ]


def event_ids(url: str, record_id: uuid.UUID) -> list[uuid.UUID]:
    return [e.id for e in events(url, record_id=record_id)]


def roles_of(url: str, user_id: uuid.UUID) -> set[str]:
    return in_uow(url, lambda s: identity._user_roles(s, user_id))


def password_hash_of(url: str, user_id: uuid.UUID) -> str | None:
    async def _load(session):  # type: ignore[no-untyped-def]
        user = await session.get(User, user_id)
        return user.password_hash

    return in_uow(url, _load)


def two_sessions_and_a_bystander(client, url, *roles):  # type: ignore[no-untyped-def]
    target = create_user(url, roles=roles)
    other = create_user(url, roles=roles)
    cookies = [signed_in(client, target.username) for _ in range(2)]
    bystander = signed_in(client, other.username)
    return target, cookies, bystander


# --- F1: revoke_user_sessions -------------------------------------------------------


def test_revoke_user_sessions_revokes_expired_and_live_sessions_only_of_the_user(
    module_seeded_url, client, clock
):
    target = create_user(module_seeded_url, roles=(OWNER,))
    other = create_user(module_seeded_url, roles=(OWNER,))
    for _ in range(2):
        signed_in(client, target.username)
    signed_in(client, other.username)
    clock.advance(timedelta(hours=13))  # every session has expired

    count = in_uow(
        module_seeded_url,
        lambda s: identity.revoke_user_sessions(
            s, user_id=target.id, reason="archived"
        ),
    )

    assert count == 2
    rows = session_rows(module_seeded_url, target.id)
    assert {row.revoked_at for row in rows} == {clock.now()}
    assert session_rows(module_seeded_url, other.id)[0].revoked_at is None
    assert revocations(module_seeded_url, target.id) == [
        {"reason": "archived", "count": 2}
    ]


def test_revoke_user_sessions_with_nothing_to_revoke_writes_nothing(module_seeded_url):
    target = create_user(module_seeded_url)

    count = in_uow(
        module_seeded_url,
        lambda s: identity.revoke_user_sessions(
            s, user_id=target.id, reason="password_changed"
        ),
    )

    assert count == 0
    assert revocations(module_seeded_url, target.id) == []


# --- F3, F6: roles --------------------------------------------------------------------


def add_role(url: str, user_id: uuid.UUID, role: str) -> bool:
    return in_uow(url, lambda s: identity.add_user_role(s, user_id=user_id, role=role))


def remove_role(url: str, user_id: uuid.UUID, role: str) -> bool:
    return in_uow(
        url, lambda s: identity.remove_user_role(s, user_id=user_id, role=role)
    )


def test_adding_a_role_revokes_and_the_next_login_gets_the_new_class(
    module_seeded_url, client
):
    target, cookies, bystander = two_sessions_and_a_bystander(
        client, module_seeded_url, TUTOR
    )
    assert session_rows(module_seeded_url, target.id)[0].idle_timeout_seconds == 604800

    assert add_role(module_seeded_url, target.id, FINANCE_STAFF) is True

    assert [code_of(me(client, c)) for c in cookies] == [UNAUTHENTICATED] * 2
    assert code_of(me(client, bystander)) == OK
    assert roles_of(module_seeded_url, target.id) == {TUTOR, FINANCE_STAFF}
    assert revocations(module_seeded_url, target.id) == [
        {"reason": "role_added", "count": 2}
    ]
    creates = [
        e
        for e in events(module_seeded_url, record_type="user_roles")
        if e.action == "create" and e.changes["user_id"]["new"] == str(target.id)
    ]
    assert len(creates) == 2  # the factory's tutor row, then finance_staff
    fresh = signed_in(client, target.username)
    assert code_of(me(client, fresh)) == OK
    assert session_rows(module_seeded_url, target.id)[-1].idle_timeout_seconds == 1800


def test_removing_a_role_revokes_and_the_next_login_gets_the_new_class(
    module_seeded_url, client
):
    target, cookies, bystander = two_sessions_and_a_bystander(
        client, module_seeded_url, TUTOR, FINANCE_STAFF
    )

    assert remove_role(module_seeded_url, target.id, FINANCE_STAFF) is True

    assert [code_of(me(client, c)) for c in cookies] == [UNAUTHENTICATED] * 2
    assert code_of(me(client, bystander)) == OK
    assert roles_of(module_seeded_url, target.id) == {TUTOR}
    assert revocations(module_seeded_url, target.id) == [
        {"reason": "role_removed", "count": 2}
    ]
    deletes = [
        e
        for e in events(module_seeded_url, record_type="user_roles")
        if e.action == "delete" and e.changes["user_id"]["old"] == str(target.id)
    ]
    assert len(deletes) == 1
    signed_in(client, target.username)
    assert session_rows(module_seeded_url, target.id)[-1].idle_timeout_seconds == 604800


def test_role_no_ops_write_nothing_and_revoke_nothing(module_seeded_url, client):
    target = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = signed_in(client, target.username)
    before = event_ids(module_seeded_url, target.id)
    role_events = len(events(module_seeded_url, record_type="user_roles"))

    assert add_role(module_seeded_url, target.id, TUTOR) is False
    assert remove_role(module_seeded_url, target.id, GUARDIAN) is False

    assert code_of(me(client, cookie)) == OK
    assert live_count(module_seeded_url, target.id) == 1
    assert event_ids(module_seeded_url, target.id) == before
    assert len(events(module_seeded_url, record_type="user_roles")) == role_events


@pytest.mark.parametrize("change", [add_role, remove_role], ids=["add", "remove"])
def test_an_unknown_role_changes_nothing(module_seeded_url, client, change):
    target = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = signed_in(client, target.username)

    with pytest.raises(identity.UnknownRoleError):
        change(module_seeded_url, target.id, "superuser")

    assert roles_of(module_seeded_url, target.id) == {TUTOR}
    assert code_of(me(client, cookie)) == OK


@pytest.mark.parametrize("change", [add_role, remove_role], ids=["add", "remove"])
def test_an_unknown_user_is_not_found(module_seeded_url, change):
    with pytest.raises(RecordNotFoundError):
        change(module_seeded_url, uuid.uuid7(), TUTOR)


def test_the_unknown_role_error_has_a_fixed_body():
    error = identity.UnknownRoleError()

    assert (error.status, error.code) == (422, "role_unknown")


def test_a_role_change_rolls_back_with_the_callers_unit_of_work(
    module_seeded_url, client
):
    target = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = signed_in(client, target.username)

    async def add_then_fail(session):  # type: ignore[no-untyped-def]
        await identity.add_user_role(session, user_id=target.id, role=GUARDIAN)
        raise RuntimeError("the caller fails after the change")

    with pytest.raises(RuntimeError):
        in_uow(module_seeded_url, add_then_fail)

    assert roles_of(module_seeded_url, target.id) == {TUTOR}
    assert code_of(me(client, cookie)) == OK
    assert revocations(module_seeded_url, target.id) == []


# --- F4, F6: password ---------------------------------------------------------------


def set_password(url: str, user_id: uuid.UUID, password: str) -> None:
    in_uow(
        url,
        lambda s: identity.set_password(s, user_id=user_id, new_password=password),
    )


def test_setting_a_password_revokes_and_only_the_new_password_works(
    module_seeded_url, client
):
    target, cookies, bystander = two_sessions_and_a_bystander(
        client, module_seeded_url, OWNER
    )
    old_hash = password_hash_of(module_seeded_url, target.id)

    set_password(module_seeded_url, target.id, NEW_PASSWORD)

    assert [code_of(me(client, c)) for c in cookies] == [UNAUTHENTICATED] * 2
    assert code_of(me(client, bystander)) == OK
    assert revocations(module_seeded_url, target.id) == [
        {"reason": "password_changed", "count": 2}
    ]
    client.cookies.clear()
    assert login(client, target.username, TEST_USER_PASSWORD).status_code == 401
    client.cookies.clear()
    assert login(client, target.username, NEW_PASSWORD).status_code == 200
    new_hash = password_hash_of(module_seeded_url, target.id)
    assert new_hash != old_hash
    assert old_hash is not None and new_hash is not None
    no_secret_in_audit(
        module_seeded_url, NEW_PASSWORD, TEST_USER_PASSWORD, new_hash, old_hash
    )


@pytest.mark.parametrize(
    ("password", "error"),
    [
        ("short", identity.PasswordTooShortError),
        ("x" * 129, identity.PasswordTooLongError),
        (None, identity.PasswordEqualsUsernameError),
    ],
    ids=["too-short", "too-long", "equals-username"],
)
def test_a_policy_failure_changes_nothing(module_seeded_url, client, password, error):
    # A username long enough to be a valid password on its own.
    target = create_user(
        module_seeded_url,
        roles=(TUTOR,),
        username=f"long.username.{uuid.uuid4().hex[:8]}",
    )
    cookie = signed_in(client, target.username)
    old_hash = password_hash_of(module_seeded_url, target.id)
    before = event_ids(module_seeded_url, target.id)

    with pytest.raises(error):
        set_password(module_seeded_url, target.id, password or target.username.upper())

    assert password_hash_of(module_seeded_url, target.id) == old_hash
    assert code_of(me(client, cookie)) == OK
    assert event_ids(module_seeded_url, target.id) == before


def test_setting_a_password_for_a_user_without_sessions_writes_no_event(
    module_seeded_url,
):
    # The invitation-redemption case (#29): no session exists yet.
    target = create_user(module_seeded_url, password_hash=None)

    set_password(module_seeded_url, target.id, NEW_PASSWORD)

    assert revocations(module_seeded_url, target.id) == []
    assert password_hash_of(module_seeded_url, target.id) is not None


def test_an_unknown_user_has_no_password_to_set(module_seeded_url):
    with pytest.raises(RecordNotFoundError):
        set_password(module_seeded_url, uuid.uuid7(), NEW_PASSWORD)


# --- F5: delegations do not revoke --------------------------------------------------


def test_a_delegation_takes_effect_on_the_next_request_without_revoking(
    module_seeded_url, client
):
    owner = create_user(module_seeded_url, roles=(OWNER,))
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    cookie = signed_in(client, admin.username)
    assert PAYROLL_FINALIZE not in me(client, cookie).json()["permissions"]

    in_uow(
        module_seeded_url,
        lambda s: identity.grant_delegation(
            s, actor_id=owner.id, user_id=admin.id, permission=PAYROLL_FINALIZE
        ),
    )

    response = me(client, cookie)
    assert response.status_code == 200
    assert PAYROLL_FINALIZE in response.json()["permissions"]

    in_uow(
        module_seeded_url,
        lambda s: identity.revoke_delegation(
            s, actor_id=owner.id, user_id=admin.id, permission=PAYROLL_FINALIZE
        ),
    )

    response = me(client, cookie)
    assert response.status_code == 200
    assert PAYROLL_FINALIZE not in response.json()["permissions"]
    assert live_count(module_seeded_url, admin.id) == 1
    assert revocations(module_seeded_url, admin.id) == []
