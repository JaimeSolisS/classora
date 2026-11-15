"""``change_user_state``: the transition table and revocation on suspend and
archive (#25 F1, F2, F6)."""

import itertools
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.authz import RecordNotFoundError
from app.modules.identity import service as identity
from app.modules.identity.permissions import (
    ACTIVE,
    ARCHIVED,
    OWNER,
    PENDING,
    SUSPENDED,
    TUTOR,
    USER_STATES,
)
from tests.integration.auth_support import auth_client, create_user
from tests.integration.lifecycle_support import (
    code_of,
    events,
    in_uow,
    live_count,
    me,
    session_rows,
    signed_in,
)

UNAUTHENTICATED = (401, "authentication_required")

ALLOWED = {
    (PENDING, ACTIVE),
    (PENDING, ARCHIVED),
    (ACTIVE, SUSPENDED),
    (ACTIVE, ARCHIVED),
    (SUSPENDED, ACTIVE),
    (SUSPENDED, ARCHIVED),
    (ARCHIVED, ACTIVE),
}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def change_state(url: str, user_id: uuid.UUID, new_state: str) -> bool:
    return in_uow(
        url,
        lambda s: identity.change_user_state(s, user_id=user_id, new_state=new_state),
    )


def state_of(url: str, user_id: uuid.UUID) -> str:
    return in_uow(url, lambda s: identity._user_state(s, user_id))  # type: ignore[return-value]


def event_ids(url: str, record_id: uuid.UUID) -> list[uuid.UUID]:
    return [e.id for e in events(url, record_id=record_id)]


def test_the_service_table_is_exactly_the_groomed_table():
    assert identity.ALLOWED_STATE_TRANSITIONS == ALLOWED


@pytest.mark.parametrize(
    ("old", "new"),
    list(itertools.product(USER_STATES, USER_STATES)),
    ids=lambda s: s,
)
def test_every_pair_of_states(module_seeded_url, old, new):
    someone = create_user(module_seeded_url, roles=(TUTOR,), state=old)
    before = event_ids(module_seeded_url, someone.id)

    if old == new:
        assert change_state(module_seeded_url, someone.id, new) is False
        assert event_ids(module_seeded_url, someone.id) == before
    elif (old, new) in ALLOWED:
        assert change_state(module_seeded_url, someone.id, new) is True
        updates = [
            e
            for e in events(module_seeded_url, record_id=someone.id)
            if e.action == "update"
        ]
        assert updates[-1].changes["state"] == {"old": old, "new": new}
    else:
        with pytest.raises(identity.InvalidUserStateTransitionError):
            change_state(module_seeded_url, someone.id, new)
        assert event_ids(module_seeded_url, someone.id) == before
    expected = new if (old, new) in ALLOWED or old == new else old
    assert state_of(module_seeded_url, someone.id) == expected


def test_an_unknown_state_is_refused(module_seeded_url):
    someone = create_user(module_seeded_url)

    with pytest.raises(identity.InvalidUserStateTransitionError):
        change_state(module_seeded_url, someone.id, "deleted")

    assert state_of(module_seeded_url, someone.id) == ACTIVE


def test_an_unknown_user_is_not_found(module_seeded_url):
    with pytest.raises(RecordNotFoundError):
        change_state(module_seeded_url, uuid.uuid7(), SUSPENDED)


def test_the_error_has_a_fixed_body():
    error = identity.InvalidUserStateTransitionError()

    assert (error.status, error.code) == (409, "user_state_transition_invalid")


# --- F6: suspend and archive revoke every session of the user -----------------------


@pytest.mark.parametrize(
    ("new_state", "reason"), [(SUSPENDED, "suspended"), (ARCHIVED, "archived")]
)
def test_suspending_or_archiving_revokes_the_users_sessions(
    module_seeded_url, client, new_state, reason
):
    target = create_user(module_seeded_url, roles=(OWNER,))
    other = create_user(module_seeded_url, roles=(OWNER,))
    first, second = (
        signed_in(client, target.username),
        signed_in(client, target.username),
    )
    others = signed_in(client, other.username)

    assert change_state(module_seeded_url, target.id, new_state) is True

    assert code_of(me(client, first)) == UNAUTHENTICATED
    assert code_of(me(client, second)) == UNAUTHENTICATED
    assert code_of(me(client, others)) == (200, None)
    assert live_count(module_seeded_url, target.id) == 0
    assert live_count(module_seeded_url, other.id) == 1
    [revoked] = events(
        module_seeded_url, record_id=target.id, action="identity.sessions_revoked"
    )
    assert revoked.record_type == "users"
    assert revoked.details == {"reason": reason, "count": 2}
    [update] = [
        e
        for e in events(module_seeded_url, record_id=target.id)
        if e.action == "update"
    ]
    assert update.record_type == "users"
    assert update.changes["state"] == {"old": ACTIVE, "new": new_state}


@pytest.mark.parametrize("parked", [SUSPENDED, ARCHIVED])
def test_reactivation_does_not_revive_old_sessions(module_seeded_url, client, parked):
    target = create_user(module_seeded_url, roles=(TUTOR,))
    first, second = (
        signed_in(client, target.username),
        signed_in(client, target.username),
    )

    change_state(module_seeded_url, target.id, parked)
    assert change_state(module_seeded_url, target.id, ACTIVE) is True

    assert code_of(me(client, first)) == UNAUTHENTICATED
    assert code_of(me(client, second)) == UNAUTHENTICATED
    fresh = signed_in(client, target.username)
    assert code_of(me(client, fresh)) == (200, None)


def test_activation_and_reactivation_revoke_nothing(module_seeded_url, client):
    pending = create_user(module_seeded_url, state=PENDING)

    assert change_state(module_seeded_url, pending.id, ACTIVE) is True

    assert (
        events(
            module_seeded_url, record_id=pending.id, action="identity.sessions_revoked"
        )
        == []
    )


def test_suspending_a_user_without_sessions_writes_no_revocation_event(
    module_seeded_url,
):
    target = create_user(module_seeded_url)

    assert change_state(module_seeded_url, target.id, SUSPENDED) is True

    assert (
        events(
            module_seeded_url, record_id=target.id, action="identity.sessions_revoked"
        )
        == []
    )


def test_a_forbidden_transition_leaves_the_sessions_alone(module_seeded_url, client):
    target = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = signed_in(client, target.username)
    change_state(module_seeded_url, target.id, ARCHIVED)
    before = session_rows(module_seeded_url, target.id)

    with pytest.raises(identity.InvalidUserStateTransitionError):
        change_state(module_seeded_url, target.id, SUSPENDED)

    assert session_rows(module_seeded_url, target.id) == before
    assert state_of(module_seeded_url, target.id) == ARCHIVED
    assert code_of(me(client, cookie)) == UNAUTHENTICATED


def test_the_change_and_the_revocation_roll_back_with_the_callers_unit_of_work(
    module_seeded_url, client
):
    target = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = signed_in(client, target.username)
    before = event_ids(module_seeded_url, target.id)

    async def suspend_then_fail(session):  # type: ignore[no-untyped-def]
        await identity.change_user_state(
            session, user_id=target.id, new_state=SUSPENDED
        )
        raise RuntimeError("the caller fails after the change")

    with pytest.raises(RuntimeError):
        in_uow(module_seeded_url, suspend_then_fail)

    assert state_of(module_seeded_url, target.id) == ACTIVE
    assert live_count(module_seeded_url, target.id) == 1
    assert code_of(me(client, cookie)) == (200, None)
    assert event_ids(module_seeded_url, target.id) == before
