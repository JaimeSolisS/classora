"""Reviewing self-registration requests (#169 C1-C3, C5, D) with real logins,
the ``clock`` fixture and the #27 rate-limit reset."""

import logging
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.authz import PermissionDeniedError, Principal
from app.modules.identity import registrations
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    OWNER,
    ROLE_CODES,
    ROLE_PERMISSIONS,
    USER_ROLE_ASSIGN,
)
from tests.conftest import CLOCK_START
from tests.integration.auth_support import CreatedUser, auth_client, create_user, login
from tests.integration.conftest import reset_rate_limits, run_sql
from tests.integration.lifecycle_support import (
    code_of,
    events,
    in_uow,
    no_secret_in_audit,
    signed_in,
)
from tests.integration.registration_support import (
    REQUEST_PASSWORD,
    approve,
    list_requests,
    register,
    reject,
    request_row,
    roles_of,
    unique_username,
)

OK = (200, None)
ACCEPTED = (202, None)
ALREADY_DECIDED = (409, "registration_already_decided")
APPROVED_EVENT = "identity.registration_approved"
REJECTED_EVENT = "identity.registration_rejected"


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


def requested(url: str, client: TestClient, role: str = "guardian") -> Any:
    """A new pending request (its joined row, see ``request_row``)."""
    username = unique_username()
    assert code_of(register(client, username=username, role=role)) == ACCEPTED
    row = request_row(url, username)
    assert row is not None
    return row


def username_of(url: str, user_id: Any) -> str:
    [row] = run_sql(url, "SELECT username FROM users WHERE id = :id", {"id": user_id})
    return str(row.username)


# --- C2: approve -------------------------------------------------------------------


@pytest.mark.parametrize("reviewer_role", [OWNER, ADMINISTRATOR])
@pytest.mark.parametrize("role", ["guardian", "student"])
def test_approval_activates_the_user_with_exactly_the_requested_role(
    module_seeded_url, client, clock, reviewer_role, role
):
    reviewer = create_user(module_seeded_url, roles=(reviewer_role,))
    row = requested(module_seeded_url, client, role)
    username = username_of(module_seeded_url, row.user_id)
    cookie = signed_in(client, reviewer.username)

    response = approve(client, cookie, row.id)

    assert code_of(response) == OK
    body = response.json()
    assert body["id"] == str(row.id)
    assert body["username"] == username
    assert body["requested_role"] == role
    assert body["status"] == "approved"
    assert datetime.fromisoformat(body["decided_at"]) == CLOCK_START
    after = request_row(module_seeded_url, username)
    assert (after.state, after.status) == ("active", "approved")
    assert after.decided_at == CLOCK_START
    assert after.decided_by_user_id == reviewer.id
    assert roles_of(module_seeded_url, row.user_id) == [role]
    signed = login(client, username, REQUEST_PASSWORD)
    assert code_of(signed) == OK
    assert signed.json()["roles"] == [role]


def test_approval_writes_the_named_event(module_seeded_url, client, clock, owner):
    row = requested(module_seeded_url, client, "student")
    cookie = signed_in(client, owner.username)

    assert code_of(approve(client, cookie, row.id)) == OK

    [event] = events(module_seeded_url, record_id=row.id, action=APPROVED_EVENT)
    assert event.record_type == "registration_requests"
    assert event.actor_user_id == owner.id
    assert event.details == {"user_id": str(row.user_id), "requested_role": "student"}
    by_record = {
        (e.record_type, e.action)
        for record_id in (row.id, row.user_id)
        for e in events(module_seeded_url, record_id=record_id)
    }
    assert {
        ("users", "update"),
        ("registration_requests", "update"),
    } <= by_record
    [role_events] = run_sql(
        module_seeded_url,
        "SELECT count(*) FROM audit_events WHERE record_type = 'user_roles'"
        " AND action = 'create' AND position(:user_id IN changes::text) > 0",
        {"user_id": str(row.user_id)},
    )
    assert role_events[0] == 1
    no_secret_in_audit(module_seeded_url, REQUEST_PASSWORD, row.password_hash)


def test_approval_checks_the_role_authority(module_seeded_url, clock):
    """Defence in depth (C2 step 3): a reviewer who may not assign the role."""
    reviewer = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    with auth_client(module_seeded_url) as client:
        row = requested(module_seeded_url, client)
    principal = Principal(
        user_id=reviewer.id,
        roles=frozenset({ADMINISTRATOR}),
        permissions=ROLE_PERMISSIONS[ADMINISTRATOR] - {USER_ROLE_ASSIGN},
    )

    with pytest.raises(PermissionDeniedError):
        in_uow(
            module_seeded_url,
            lambda session: registrations.approve_registration(
                session, principal=principal, request_id=row.id
            ),
            actor_id=reviewer.id,
        )
    after = request_row(module_seeded_url, username_of(module_seeded_url, row.user_id))
    assert (after.state, after.status) == ("pending", "pending")
    assert roles_of(module_seeded_url, row.user_id) == []


# --- C3: reject --------------------------------------------------------------------


def test_rejection_archives_the_user_and_clears_the_hash(
    module_seeded_url, client, clock, administrator
):
    row = requested(module_seeded_url, client, "student")
    username = username_of(module_seeded_url, row.user_id)
    cookie = signed_in(client, administrator.username)

    response = reject(client, cookie, row.id)

    assert code_of(response) == OK
    assert response.json()["status"] == "rejected"
    after = request_row(module_seeded_url, username)
    assert (after.state, after.status, after.password_hash) == (
        "archived",
        "rejected",
        None,
    )
    assert after.decided_at == CLOCK_START
    assert after.decided_by_user_id == administrator.id
    assert roles_of(module_seeded_url, row.user_id) == []
    assert code_of(login(client, username, REQUEST_PASSWORD)) == (
        401,
        "invalid_credentials",
    )
    # Archived users keep their username.
    assert code_of(register(client, username=username)) == (409, "username_taken")

    [event] = events(module_seeded_url, record_id=row.id, action=REJECTED_EVENT)
    assert event.actor_user_id == administrator.id
    assert event.details == {"user_id": str(row.user_id), "requested_role": "student"}
    no_secret_in_audit(module_seeded_url, REQUEST_PASSWORD, row.password_hash)


# --- C5: decided twice, concurrency, unknown ids -----------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [(approve, approve), (approve, reject), (reject, reject), (reject, approve)],
)
def test_deciding_twice_is_409(module_seeded_url, client, clock, owner, first, second):
    row = requested(module_seeded_url, client)
    cookie = signed_in(client, owner.username)
    assert code_of(first(client, cookie, row.id)) == OK
    before = len(events(module_seeded_url, record_id=row.id))

    assert code_of(second(client, cookie, row.id)) == ALREADY_DECIDED
    assert len(events(module_seeded_url, record_id=row.id)) == before


def test_a_request_whose_user_is_no_longer_pending_is_409(
    module_seeded_url, client, clock, owner
):
    row = requested(module_seeded_url, client)
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'archived' WHERE id = :id",
        {"id": row.user_id},
    )
    cookie = signed_in(client, owner.username)

    assert code_of(approve(client, cookie, row.id)) == ALREADY_DECIDED
    assert code_of(reject(client, cookie, row.id)) == ALREADY_DECIDED
    assert roles_of(module_seeded_url, row.user_id) == []


def test_two_concurrent_approvals_give_one_200_and_one_409(
    module_seeded_url, client, clock, owner
):
    row = requested(module_seeded_url, client)
    cookie = signed_in(client, owner.username)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: code_of(approve(client, cookie, row.id)), range(2))
        )

    assert sorted(results) == [OK, ALREADY_DECIDED]
    assert roles_of(module_seeded_url, row.user_id) == ["guardian"]
    assert len(events(module_seeded_url, record_id=row.id, action=APPROVED_EVENT)) == 1


def test_a_concurrent_approval_and_rejection_decide_once(
    module_seeded_url, client, clock, owner
):
    row = requested(module_seeded_url, client)
    cookie = signed_in(client, owner.username)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda call: code_of(call(client, cookie, row.id)), (approve, reject)
            )
        )

    assert sorted(results) == [OK, ALREADY_DECIDED]
    after = request_row(module_seeded_url, username_of(module_seeded_url, row.user_id))
    assert (after.state, after.status) in {
        ("active", "approved"),
        ("archived", "rejected"),
    }


def test_an_unknown_id_is_404(module_seeded_url, client, clock, owner):
    cookie = signed_in(client, owner.username)

    for call in (approve, reject):
        assert code_of(call(client, cookie, uuid.uuid4())) == (404, "not_found")
        assert code_of(call(client, cookie, "not-a-uuid")) == (422, "validation_error")


@pytest.mark.parametrize(
    "role", [r for r in ROLE_CODES if r not in (OWNER, ADMINISTRATOR)]
)
def test_other_roles_are_denied(module_seeded_url, client, clock, role):
    row = requested(module_seeded_url, client)
    member = create_user(module_seeded_url, roles=(role,))
    cookie = signed_in(client, member.username)

    assert code_of(list_requests(client, cookie)) == (403, "permission_denied")
    assert code_of(approve(client, cookie, row.id)) == (403, "permission_denied")
    assert code_of(reject(client, cookie, row.id)) == (403, "permission_denied")
    assert request_row(
        module_seeded_url, username_of(module_seeded_url, row.user_id)
    ).status == ("pending")


# --- C1: the list --------------------------------------------------------------------


def ids_in(client: TestClient, cookie: Any, **params: Any) -> list[str]:
    response = list_requests(client, cookie, limit=100, **params)
    assert code_of(response) == OK
    return [item["id"] for item in response.json()["items"]]


def test_the_list_filters_by_status_oldest_first(
    module_seeded_url, client, clock, owner
):
    run_sql(
        module_seeded_url, "DELETE FROM registration_requests WHERE status = 'pending'"
    )
    first = requested(module_seeded_url, client, "guardian")
    second = requested(module_seeded_url, client, "student")
    third = requested(module_seeded_url, client, "guardian")
    reset_rate_limits(module_seeded_url)
    cookie = signed_in(client, owner.username)
    assert code_of(approve(client, cookie, second.id)) == OK
    assert code_of(reject(client, cookie, third.id)) == OK

    pending = list_requests(client, cookie, limit=100)
    assert code_of(pending) == OK
    assert [i["id"] for i in pending.json()["items"]] == [str(first.id)]
    assert ids_in(client, cookie, status="pending") == [str(first.id)]
    assert str(second.id) in ids_in(client, cookie, status="approved")
    assert str(third.id) in ids_in(client, cookie, status="rejected")
    assert code_of(list_requests(client, cookie, status="other")) == (
        422,
        "validation_error",
    )

    [item] = pending.json()["items"]
    submitted_at = item.pop("submitted_at")
    assert datetime.fromisoformat(submitted_at).tzinfo is not None
    assert item == {
        "id": str(first.id),
        "username": username_of(module_seeded_url, first.user_id),
        "display_name": "Requesting Person",
        "requested_role": "guardian",
        "status": "pending",
        "decided_at": None,
    }
    [approved] = [
        i
        for i in list_requests(client, cookie, status="approved", limit=100).json()[
            "items"
        ]
        if i["id"] == str(second.id)
    ]
    assert datetime.fromisoformat(approved["decided_at"]) == CLOCK_START
    assert "password" not in pending.text
    assert "argon2" not in pending.text


def test_the_list_is_oldest_first_and_paginated(
    module_seeded_url, client, clock, owner
):
    run_sql(
        module_seeded_url, "DELETE FROM registration_requests WHERE status = 'pending'"
    )
    rows = [requested(module_seeded_url, client) for _ in range(3)]
    cookie = signed_in(client, owner.username)

    first_page = list_requests(client, cookie, limit=2).json()
    second_page = list_requests(client, cookie, limit=2, offset=2).json()

    assert (first_page["total"], first_page["limit"], first_page["offset"]) == (3, 2, 0)
    assert [i["id"] for i in first_page["items"]] == [str(r.id) for r in rows[:2]]
    assert (second_page["total"], second_page["offset"]) == (3, 2)
    assert [i["id"] for i in second_page["items"]] == [str(rows[2].id)]


# --- D3: no password in any log ----------------------------------------------------


def test_no_password_reaches_a_log(
    module_seeded_url, client, clock, owner, caplog, capsys
):
    caplog.set_level(logging.DEBUG)
    approved_password = "approved-request-password"
    rejected_password = "rejected-request-password"
    short_password = "short-pw-1"
    taken = unique_username()
    cookie = signed_in(client, owner.username)

    assert (
        code_of(register(client, username=taken, password=approved_password))
        == ACCEPTED
    )
    assert (
        code_of(register(client, password=rejected_password, role="student"))
        == ACCEPTED
    )
    register(client, username=taken, password=approved_password)  # 409
    register(client, password=short_password)  # 422
    register(client, role="tutor", password=approved_password)  # 422
    pending = list_requests(client, cookie, limit=100).json()["items"]
    by_name = {i["username"]: i["id"] for i in pending}
    rejected_id = next(i["id"] for i in pending if i["username"] != taken)
    assert code_of(approve(client, cookie, by_name[taken])) == OK
    assert code_of(approve(client, cookie, by_name[taken])) == ALREADY_DECIDED
    assert code_of(reject(client, cookie, rejected_id)) == OK
    assert code_of(reject(client, cookie, uuid.uuid4())) == (404, "not_found")
    reset_rate_limits(module_seeded_url)
    for _ in range(11):
        last = register(client, password=rejected_password)
    assert code_of(last) == (429, "rate_limited")

    captured = capsys.readouterr()
    texts = [captured.out, captured.err]
    for record in caplog.records:
        texts.append(record.getMessage())
        texts.append(repr(record.__dict__))
    for secret in (approved_password, rejected_password, short_password):
        for text in texts:
            assert secret not in text
    assert any("Rate limit exceeded" in text for text in texts)
