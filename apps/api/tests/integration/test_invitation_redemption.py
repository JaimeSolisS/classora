"""Redeeming an invitation on the public routes (#29 E4, F1-F3) with real
logins, the ``clock`` fixture and the #27 rate-limit reset."""

import logging
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ARCHIVED,
    OWNER,
    TUTOR,
)
from tests.authz.csrf import send_without_origin
from tests.conftest import CLOCK_START
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
    login,
)
from tests.integration.conftest import reset_rate_limits, run_sql
from tests.integration.invitation_support import (
    LOOKUP,
    NEW_PASSWORD,
    REDEEM,
    invite,
    list_invitations,
    lookup,
    reauthenticate,
    redeem,
    reissue,
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

NO_CONTENT = (204, None)
OK = (200, None)
GONE = (410, "token_invalid")
REDEEMED_EVENT = "identity.invitation_redeemed"
TOO_SHORT = "tiny-pw-7q"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


class Invitation:
    def __init__(self, response: Any) -> None:
        body = response.json()
        self.user_id = uuid.UUID(body["user_id"])
        self.username: str = body["username"]
        self.display_name: str = body["display_name"]
        self.token = token_of(response)


def invitation(
    client: TestClient, owner: CreatedUser, role: str = TUTOR, **kwargs: Any
) -> Invitation:
    cookie = sign_in(client, owner.username)
    if role != TUTOR:
        reauthenticate(client, cookie)
    response = invite(client, cookie, role=role, **kwargs)
    assert response.status_code == 201, response.text
    return Invitation(response)


def user_row(url: str, user_id: uuid.UUID) -> Any:
    [row] = run_sql(
        url, "SELECT state, password_hash FROM users WHERE id = :id", {"id": user_id}
    )
    return row


def without_request_id(response: Any) -> dict[str, Any]:
    body = dict(response.json())
    body.pop("request_id")
    return body


# --- E1, E2, E4: the happy path ---


def test_lookup_then_redeem_activates_the_user(module_seeded_url, client, clock, owner):
    invited = invitation(
        client, owner, ACADEMIC_COORDINATOR, display_name="Carla Coordinator"
    )

    found = lookup(client, invited.token)
    assert code_of(found) == OK
    assert found.json() == {
        "username": invited.username,
        "display_name": "Carla Coordinator",
        "roles": [ACADEMIC_COORDINATOR],
        "expires_at": "2026-10-19T12:00:00Z",
    }
    assert user_row(module_seeded_url, invited.user_id).state == "pending"

    redeemed = redeem(client, invited.token)

    assert code_of(redeemed) == NO_CONTENT
    assert redeemed.headers.get_list("set-cookie") == []
    row = user_row(module_seeded_url, invited.user_id)
    assert row.state == "active"
    assert row.password_hash.startswith("$argon2id$")
    [event] = events(
        module_seeded_url, record_id=invited.user_id, action=REDEEMED_EVENT
    )
    [link] = run_sql(
        module_seeded_url,
        "SELECT id, used_at, token_hash FROM account_tokens WHERE user_id = :id",
        {"id": invited.user_id},
    )
    assert event.record_type == "users"
    assert event.actor_user_id is None
    assert event.details == {"link_id": str(link.id)}
    assert link.used_at == CLOCK_START
    signed = login(client, invited.username, NEW_PASSWORD)
    assert code_of(signed) == OK
    assert signed.json()["roles"] == [ACADEMIC_COORDINATOR]
    assert signed.json()["display_name"] == "Carla Coordinator"
    no_secret_in_audit(
        module_seeded_url, NEW_PASSWORD, invited.token, bytes(link.token_hash).hex()
    )


def test_a_used_link_is_gone(module_seeded_url, client, clock, owner):
    invited = invitation(client, owner)
    assert code_of(redeem(client, invited.token)) == NO_CONTENT

    assert code_of(redeem(client, invited.token, "another-good-password")) == GONE
    assert code_of(lookup(client, invited.token)) == GONE


def test_an_expired_or_replaced_link_is_gone(module_seeded_url, client, clock, owner):
    expiring = invitation(client, owner)
    replaced = invitation(client, owner)
    cookie = sign_in(client, owner.username)
    assert code_of(reissue(client, cookie, replaced.user_id)) == OK

    assert code_of(lookup(client, replaced.token)) == GONE
    assert code_of(redeem(client, replaced.token)) == GONE
    clock.set(CLOCK_START + timedelta(hours=23, minutes=59, seconds=59))
    assert code_of(lookup(client, expiring.token)) == OK
    clock.set(CLOCK_START + timedelta(hours=24))
    assert code_of(lookup(client, expiring.token)) == GONE
    assert code_of(redeem(client, expiring.token)) == GONE
    assert user_row(module_seeded_url, expiring.user_id).state == "pending"


def test_a_user_archived_after_the_invitation_cannot_redeem(
    module_seeded_url, client, clock, owner
):
    invited = invitation(client, owner)
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = :state WHERE id = :id",
        {"state": ARCHIVED, "id": invited.user_id},
    )

    assert code_of(lookup(client, invited.token)) == GONE
    assert code_of(redeem(client, invited.token)) == GONE
    assert user_row(module_seeded_url, invited.user_id).state == ARCHIVED


@pytest.mark.parametrize(
    ("password", "code"),
    [
        pytest.param("short-pass", "password_too_short", id="too-short"),
        pytest.param("x" * 129, "password_too_long", id="too-long"),
        pytest.param(None, "password_equals_username", id="equals-username"),
    ],
)
def test_a_policy_failure_keeps_the_link_usable(
    module_seeded_url, client, clock, owner, password, code
):
    invited = invitation(client, owner, username=f"long.username.{uuid.uuid4().hex}")

    response = redeem(client, invited.token, password or invited.username.upper())

    assert code_of(response) == (422, code)
    row = user_row(module_seeded_url, invited.user_id)
    assert (row.state, row.password_hash) == ("pending", None)
    assert (
        events(module_seeded_url, record_id=invited.user_id, action=REDEEMED_EVENT)
        == []
    )
    assert code_of(lookup(client, invited.token)) == OK
    assert code_of(redeem(client, invited.token)) == NO_CONTENT


def test_every_invalid_link_gets_the_same_410_body(
    module_seeded_url, client, clock, owner
):
    used = invitation(client, owner)
    assert code_of(redeem(client, used.token)) == NO_CONTENT
    expired = invitation(client, owner)
    replaced = invitation(client, owner)
    cookie = sign_in(client, owner.username)
    assert code_of(reissue(client, cookie, replaced.user_id)) == OK
    archived = invitation(client, owner)
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'archived' WHERE id = :id",
        {"id": archived.user_id},
    )
    clock.advance(timedelta(hours=24))
    # Issued after the move, so only `expired` is past its 24 hours.
    fresh = [invitation(client, owner) for _ in range(2)]
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'archived' WHERE id = :id",
        {"id": fresh[0].user_id},
    )
    cases = {
        "unknown": "A" * 42 + "A",
        "malformed": "not a token",
        "used": used.token,
        "expired": expired.token,
        "replaced": replaced.token,
        "archived": fresh[0].token,
    }

    bodies = []
    for name, token in cases.items():
        for call in (lookup, redeem):
            response = call(client, token)
            assert code_of(response) == GONE, (name, call.__name__)
            assert response.headers["content-type"] == "application/problem+json"
            bodies.append(without_request_id(response))
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
    # The archived invitation from before the move is gone too.
    assert code_of(lookup(client, archived.token)) == GONE
    assert code_of(lookup(client, fresh[1].token)) == OK


def test_two_concurrent_redeems_give_one_204_and_one_410(
    module_seeded_url, client, clock, owner
):
    invited = invitation(client, owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: code_of(redeem(client, invited.token)), range(2))
        )

    assert sorted(results, key=lambda r: r[0]) == [NO_CONTENT, GONE]
    assert (
        len(events(module_seeded_url, record_id=invited.user_id, action=REDEEMED_EVENT))
        == 1
    )


def test_redeem_leaves_a_session_on_the_request_alone(
    module_seeded_url, client, clock, owner
):
    invited = invitation(client, owner)
    bystander = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = signed_in(client, bystander.username)

    client.cookies.clear()
    response = client.post(
        REDEEM,
        json={"token": invited.token, "password": NEW_PASSWORD},
        headers=cookie_header(cookie),
    )

    assert code_of(response) == NO_CONTENT
    assert response.headers.get_list("set-cookie") == []
    assert code_of(me(client, cookie)) == OK
    assert me(client, cookie).json()["username"] == bystander.username


# --- E3, E4: Origin and rate limit ---


@pytest.mark.parametrize("path", [LOOKUP, REDEEM])
def test_a_request_without_origin_is_csrf_failed(module_seeded_url, client, path):
    response = send_without_origin(
        client, "POST", path, json={"token": "x", "password": "y"}
    )

    assert code_of(response) == (403, "csrf_failed")


def test_the_21st_redemption_call_from_one_ip_is_429_without_a_lookup(
    module_seeded_url, client, clock, owner
):
    invited = invitation(client, owner)
    reset_rate_limits(module_seeded_url)

    for _ in range(10):
        assert code_of(lookup(client, "unknown")) == GONE
    for _ in range(10):
        assert code_of(redeem(client, "unknown")) == GONE

    for call in (lookup, redeem):
        response = call(client, invited.token)
        assert code_of(response) == (429, "rate_limited")
        retry_after = int(response.headers["Retry-After"])
        assert 1 <= retry_after <= 15 * 60
    # Neither a lookup nor a redemption ran: the link still works later.
    assert user_row(module_seeded_url, invited.user_id).state == "pending"
    reset_rate_limits(module_seeded_url)
    assert code_of(lookup(client, invited.token)) == OK


def test_an_invalid_body_is_counted_and_is_422(module_seeded_url, client):
    for body in ({}, {"token": ""}, {"token": "x" * 257}):
        response = client.post(LOOKUP, json=body)
        assert code_of(response) == (422, "validation_error")
    response = client.post(REDEEM, json={"token": "x"})
    assert code_of(response) == (422, "validation_error")
    [row] = run_sql(
        module_seeded_url, "SELECT sum(count) AS used FROM rate_limit_counters"
    )
    assert row.used == 4


# --- F2, F3: no event on failure; no secret in any log ---


def test_failed_redemptions_write_no_event(module_seeded_url, client, clock, owner):
    invited = invitation(client, owner)
    before = len(events(module_seeded_url, action=REDEEMED_EVENT))

    redeem(client, invited.token, "short")  # 422
    redeem(client, "unknown")  # 410
    lookup(client, invited.token)  # 200, writes nothing

    assert len(events(module_seeded_url, action=REDEEMED_EVENT)) == before
    assert (
        events(module_seeded_url, record_id=invited.user_id, action=REDEEMED_EVENT)
        == []
    )


def test_no_password_or_token_reaches_a_log(
    module_seeded_url, client, clock, owner, caplog, capsys
):
    caplog.set_level(logging.DEBUG)
    cookie = sign_in(client, owner.username)
    reauthenticate(client, cookie)
    issued = invite(client, cookie, role=ACADEMIC_COORDINATOR)
    first = token_of(issued)
    again = reissue(client, cookie, issued.json()["user_id"])
    token = token_of(again)
    list_invitations(client, cookie)
    invite(client, cookie, role=TUTOR, username=issued.json()["username"])  # 409
    lookup(client, first)  # 410
    lookup(client, token)
    redeem(client, token, TOO_SHORT)  # 422
    redeem(client, token, NEW_PASSWORD)
    redeem(client, token, NEW_PASSWORD)  # 410
    reset_rate_limits(module_seeded_url)
    for _ in range(21):
        last = lookup(client, token)
    assert code_of(last) == (429, "rate_limited")

    captured = capsys.readouterr()
    texts = [captured.out, captured.err]
    for record in caplog.records:
        texts.append(record.getMessage())
        texts.append(repr(record.__dict__))
    for secret in (first, token, NEW_PASSWORD, TOO_SHORT):
        for text in texts:
            assert secret not in text
    assert any("Rate limit exceeded" in text for text in texts)
