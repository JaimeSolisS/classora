"""Redeeming a password reset on the public routes (#30 B2, D, G) with real
logins, the ``clock`` fixture and the #27 rate-limit reset."""

import logging
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.modules.identity import tokens
from app.modules.identity.permissions import OWNER, TUTOR
from tests.authz.csrf import send_without_origin
from tests.conftest import CLOCK_START
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
    login,
)
from tests.integration.conftest import reset_rate_limits, run_sql
from tests.integration.invitation_support import (
    invite,
    lookup,
    redeem,
    sign_in,
    token_of,
)
from tests.integration.lifecycle_support import (
    code_of,
    events,
    me,
    no_secret_in_audit,
    signed_in,
)
from tests.integration.recovery_support import (
    REDEEMED_EVENT,
    RESET_LOOKUP,
    RESET_PASSWORD,
    RESET_REDEEM,
    password_hash_of,
    reset_lookup,
    reset_redeem,
    reset_rows,
    reset_token_of,
    staff_reset,
    without_request_id,
)

NO_CONTENT = (204, None)
OK = (200, None)
GONE = (410, "token_invalid")
UNAUTHENTICATED = (401, "authentication_required")
REVOKED_EVENT = "identity.sessions_revoked"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def person(module_seeded_url: str) -> CreatedUser:
    return create_user(
        module_seeded_url,
        roles=(TUTOR,),
        username=f"reset.person.{uuid.uuid4().hex[:10]}",
        display_name="Rosa Reset",
    )


def reset_token(client: TestClient, owner: CreatedUser, person: CreatedUser) -> str:
    response = staff_reset(client, owner.username, person.username)
    assert response.status_code == 201, response.text
    return reset_token_of(response)


def state_of(url: str, user_id: uuid.UUID) -> str:
    [row] = run_sql(url, "SELECT state FROM users WHERE id = :id", {"id": user_id})
    return str(row.state)


# --- D1, D2, D4: the happy path ---


def test_issue_lookup_redeem_revokes_every_session(
    module_seeded_url, client, clock, owner, person
):
    first = signed_in(client, person.username)
    second = signed_in(client, person.username)
    raw = reset_token(client, owner, person)

    found = reset_lookup(client, raw)
    assert code_of(found) == OK
    assert found.json() == {
        "username": person.username,
        "display_name": "Rosa Reset",
        "expires_at": "2026-10-19T12:00:00Z",
    }
    assert code_of(me(client, first)) == OK

    redeemed = reset_redeem(client, raw)

    assert code_of(redeemed) == NO_CONTENT
    assert redeemed.headers.get_list("set-cookie") == []
    assert code_of(me(client, first)) == UNAUTHENTICATED
    assert code_of(me(client, second)) == UNAUTHENTICATED
    [revoked] = events(module_seeded_url, record_id=person.id, action=REVOKED_EVENT)
    assert revoked.details == {"reason": "password_changed", "count": 2}
    assert code_of(login(client, person.username)) == (401, "invalid_credentials")
    assert code_of(login(client, person.username, RESET_PASSWORD)) == OK
    assert state_of(module_seeded_url, person.id) == "active"
    [row] = reset_rows(module_seeded_url, person.id)
    assert row.used_at == CLOCK_START
    [event] = events(module_seeded_url, record_id=person.id, action=REDEEMED_EVENT)
    assert event.record_type == "users"
    assert event.actor_user_id is None
    assert event.details == {"link_id": str(row.id)}
    no_secret_in_audit(
        module_seeded_url, RESET_PASSWORD, raw, bytes(row.token_hash).hex()
    )


def test_a_used_link_is_gone(module_seeded_url, client, clock, owner, person):
    raw = reset_token(client, owner, person)
    assert code_of(reset_redeem(client, raw)) == NO_CONTENT

    assert code_of(reset_redeem(client, raw, "another-good-password")) == GONE
    assert code_of(reset_lookup(client, raw)) == GONE


# --- B2: lifetime, replacement and kinds ---


def test_a_reset_link_is_valid_until_just_before_24_hours(
    module_seeded_url, client, clock, owner, person
):
    raw = reset_token(client, owner, person)

    clock.set(CLOCK_START + timedelta(hours=23, minutes=59, seconds=59))
    assert code_of(reset_lookup(client, raw)) == OK
    clock.set(CLOCK_START + timedelta(hours=24))
    assert code_of(reset_lookup(client, raw)) == GONE
    assert code_of(reset_redeem(client, raw)) == GONE
    assert code_of(login(client, person.username)) == OK


def test_a_replaced_link_is_gone(module_seeded_url, client, clock, owner, person):
    first = reset_token(client, owner, person)
    second = reset_token(client, owner, person)

    assert code_of(reset_lookup(client, first)) == GONE
    assert code_of(reset_redeem(client, first)) == GONE
    first_row, second_row = reset_rows(module_seeded_url, person.id)
    assert first_row.revoked_at is not None
    assert code_of(reset_redeem(client, second)) == NO_CONTENT


def test_an_invitation_token_is_gone_on_the_reset_routes_and_vice_versa(
    module_seeded_url, client, clock, owner, person
):
    cookie = sign_in(client, owner.username)
    invited = invite(client, cookie, role=TUTOR)
    assert invited.status_code == 201, invited.text
    invitation_token = token_of(invited)
    raw = reset_token(client, owner, person)

    assert code_of(reset_lookup(client, invitation_token)) == GONE
    assert code_of(reset_redeem(client, invitation_token)) == GONE
    assert code_of(lookup(client, raw)) == GONE
    assert code_of(redeem(client, raw)) == GONE
    # Both still work on their own routes.
    assert code_of(lookup(client, invitation_token)) == OK
    assert code_of(reset_lookup(client, raw)) == OK


# --- D2, D4: state, policy, concurrency ---


@pytest.mark.parametrize("state", ["suspended", "archived"])
def test_a_user_suspended_or_archived_after_issue_cannot_redeem(
    module_seeded_url, client, clock, owner, person, state
):
    raw = reset_token(client, owner, person)
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = :state WHERE id = :id",
        {"state": state, "id": person.id},
    )
    before = password_hash_of(module_seeded_url, person.id)

    assert code_of(reset_lookup(client, raw)) == GONE
    assert code_of(reset_redeem(client, raw)) == GONE
    assert state_of(module_seeded_url, person.id) == state
    assert password_hash_of(module_seeded_url, person.id) == before


@pytest.mark.parametrize(
    ("password", "code"),
    [
        pytest.param("short-pass", "password_too_short", id="too-short"),
        pytest.param("x" * 129, "password_too_long", id="too-long"),
        pytest.param(None, "password_equals_username", id="equals-username"),
    ],
)
def test_a_policy_failure_keeps_the_link_and_the_sessions(
    module_seeded_url, client, clock, owner, person, password, code
):
    session = signed_in(client, person.username)
    raw = reset_token(client, owner, person)
    before = password_hash_of(module_seeded_url, person.id)

    response = reset_redeem(client, raw, password or person.username.upper())

    assert code_of(response) == (422, code)
    assert password_hash_of(module_seeded_url, person.id) == before
    assert code_of(me(client, session)) == OK
    assert events(module_seeded_url, record_id=person.id, action=REVOKED_EVENT) == []
    assert events(module_seeded_url, record_id=person.id, action=REDEEMED_EVENT) == []
    assert code_of(login(client, person.username, TEST_USER_PASSWORD)) == OK
    assert code_of(reset_lookup(client, raw)) == OK
    assert code_of(reset_redeem(client, raw)) == NO_CONTENT


def test_every_invalid_link_gets_the_same_410_body(
    module_seeded_url, client, clock, owner
):
    people = [create_user(module_seeded_url, roles=(TUTOR,)) for _ in range(5)]
    used = reset_token(client, owner, people[0])
    assert code_of(reset_redeem(client, used)) == NO_CONTENT
    expired = reset_token(client, owner, people[1])
    replaced = reset_token(client, owner, people[2])
    reset_token(client, owner, people[2])
    cookie = sign_in(client, owner.username)
    invited = invite(client, cookie, role=TUTOR)
    wrong_kind = token_of(invited)
    clock.advance(timedelta(hours=24))
    suspended = reset_token(client, owner, people[3])
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'suspended' WHERE id = :id",
        {"id": people[3].id},
    )
    fresh = reset_token(client, owner, people[4])
    cases = {
        "unknown": "A" * 43,
        "malformed": "not a token",
        "used": used,
        "expired": expired,
        "replaced": replaced,
        "suspended": suspended,
    }

    bodies = []
    for name, token in cases.items():
        for call in (reset_lookup, reset_redeem):
            response = call(client, token)
            assert code_of(response) == GONE, (name, call.__name__)
            assert response.headers["content-type"] == "application/problem+json"
            bodies.append(without_request_id(response))
    # The invitation token is past its 24 hours too; it is a wrong kind either way.
    for call in (reset_lookup, reset_redeem):
        bodies.append(without_request_id(call(client, wrong_kind)))
    first = bodies[0]
    for body in bodies[1:]:
        assert body == first
    assert first == {
        "type": "about:blank",
        "title": "Gone",
        "status": 410,
        "code": "token_invalid",
        "detail": "This link is no longer valid. Ask the center for a new one.",
    }
    assert code_of(reset_lookup(client, fresh)) == OK


def test_a_wrong_kind_token_that_is_live_is_gone(
    module_seeded_url, client, clock, owner
):
    cookie = sign_in(client, owner.username)
    invited = invite(client, cookie, role=TUTOR)
    wrong_kind = token_of(invited)
    reference = reset_lookup(client, "A" * 43)

    for call in (reset_lookup, reset_redeem):
        response = call(client, wrong_kind)
        assert code_of(response) == GONE
        assert without_request_id(response) == without_request_id(reference)


def test_two_concurrent_redeems_give_one_204_and_one_410(
    module_seeded_url, client, clock, owner, person
):
    raw = reset_token(client, owner, person)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: code_of(reset_redeem(client, raw)), range(2)))

    assert sorted(results, key=lambda r: r[0]) == [NO_CONTENT, GONE]
    redeemed = events(module_seeded_url, record_id=person.id, action=REDEEMED_EVENT)
    assert len(redeemed) == 1


def test_redeem_leaves_a_session_on_the_request_alone(
    module_seeded_url, client, clock, owner, person
):
    raw = reset_token(client, owner, person)
    bystander = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = signed_in(client, bystander.username)

    client.cookies.clear()
    response = client.post(
        RESET_REDEEM,
        json={"token": raw, "password": RESET_PASSWORD},
        headers=cookie_header(cookie),
    )

    assert code_of(response) == NO_CONTENT
    assert response.headers.get_list("set-cookie") == []
    assert me(client, cookie).json()["username"] == bystander.username


# --- D3: Origin and the shared rate limit ---


@pytest.mark.parametrize("path", [RESET_LOOKUP, RESET_REDEEM])
def test_a_request_without_origin_is_csrf_failed(module_seeded_url, client, path):
    response = send_without_origin(
        client, "POST", path, json={"token": "x", "password": "y"}
    )

    assert code_of(response) == (403, "csrf_failed")


def test_the_21st_call_mixing_invitation_and_reset_routes_is_429_without_a_lookup(
    module_seeded_url, client, clock, owner, person, monkeypatch
):
    raw = reset_token(client, owner, person)
    reset_rate_limits(module_seeded_url)
    for call in (lookup, redeem, reset_lookup, reset_redeem):
        for _ in range(5):
            assert code_of(call(client, "unknown")) == GONE

    def no_lookup(*args: object, **kwargs: object) -> None:
        raise AssertionError("no token lookup may run over the limit")

    monkeypatch.setattr(tokens, "_find", no_lookup)

    for call in (reset_lookup, reset_redeem, lookup):
        response = call(client, raw)
        assert code_of(response) == (429, "rate_limited")
        assert 1 <= int(response.headers["Retry-After"]) <= 15 * 60
    monkeypatch.undo()
    reset_rate_limits(module_seeded_url)
    assert code_of(reset_lookup(client, raw)) == OK


# --- G2, G3: no event on failure; no secret in any log ---


def test_failed_redemptions_write_no_event(
    module_seeded_url, client, clock, owner, person
):
    raw = reset_token(client, owner, person)
    before = len(events(module_seeded_url, action=REDEEMED_EVENT))

    reset_redeem(client, raw, "short")  # 422
    reset_redeem(client, "unknown")  # 410
    reset_lookup(client, raw)  # 200, writes nothing

    assert len(events(module_seeded_url, action=REDEEMED_EVENT)) == before


def test_no_password_or_token_reaches_a_log(
    module_seeded_url, client, clock, owner, person, caplog, capsys
):
    caplog.set_level(logging.DEBUG)
    first = reset_token(client, owner, person)
    raw = reset_token(client, owner, person)
    too_short = "tiny-pw-7q"
    reset_lookup(client, first)  # 410
    reset_lookup(client, raw)
    reset_redeem(client, raw, too_short)  # 422
    reset_redeem(client, raw)
    reset_redeem(client, raw)  # 410
    reset_rate_limits(module_seeded_url)
    for _ in range(21):
        last = reset_lookup(client, raw)
    assert code_of(last) == (429, "rate_limited")

    captured = capsys.readouterr()
    texts = [captured.out, captured.err]
    for record in caplog.records:
        texts.append(record.getMessage())
        texts.append(repr(record.__dict__))
    for secret in (first, raw, RESET_PASSWORD, too_short):
        for text in texts:
            assert secret not in text
    assert any("Rate limit exceeded" in text for text in texts)
