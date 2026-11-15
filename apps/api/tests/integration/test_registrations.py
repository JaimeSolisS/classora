"""Submitting a self-registration request (#169 A3, B, D) with the real app,
the ``clock`` fixture and the #27 rate-limit reset; raw SQL checks of the
``registration_requests`` table."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity import registrations
from app.modules.identity.errors import UsernameTakenError
from app.modules.identity.passwords import verify_password
from app.modules.identity.permissions import ARCHIVED, TUTOR
from tests.authz.csrf import send_without_origin
from tests.conftest import CLOCK_START
from tests.integration.auth_support import CreatedUser, auth_client, create_user, login
from tests.integration.conftest import reset_rate_limits, run_sql, short_lived_engine
from tests.integration.lifecycle_support import code_of, events, no_secret_in_audit
from tests.integration.registration_support import (
    REGISTRATIONS,
    REQUEST_PASSWORD,
    register,
    request_row,
    roles_of,
    unique_username,
)

ACCEPTED = (202, None)
REQUESTED_EVENT = "identity.registration_requested"

INSERT = (
    "INSERT INTO registration_requests (user_id, requested_role, status,"
    " decided_at, decided_by_user_id)"
    " VALUES (:user_id, :role, :status, :decided_at, :decided_by)"
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def user_count(url: str, username: str) -> int:
    rows = run_sql(
        url, "SELECT count(*) FROM users WHERE username = :u", {"u": username}
    )
    return int(rows[0][0])


# --- A3: constraints ---------------------------------------------------------------


@pytest.fixture
def pending_user(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(), state="pending")


@pytest.fixture
def reviewer(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=("owner",))


def request_values(user: CreatedUser, **overrides: Any) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "role": "guardian",
        "status": "pending",
        "decided_at": None,
        "decided_by": None,
        **overrides,
    }


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        pytest.param({"role": "tutor"}, "requested_role_valid", id="tutor-role"),
        pytest.param(
            {"status": "other", "decided_at": CLOCK_START, "decided_by": "reviewer"},
            "status_valid",
            id="unknown-status",
        ),
        pytest.param(
            {"status": "approved"}, "decided_iff_not_pending", id="approved-undecided"
        ),
        pytest.param(
            {"status": "rejected", "decided_by": "reviewer"},
            "decided_iff_not_pending",
            id="rejected-without-decided-at",
        ),
        pytest.param(
            {"decided_by": "reviewer"},
            "decided_iff_not_pending",
            id="pending-with-reviewer",
        ),
        pytest.param(
            {"decided_at": CLOCK_START},
            "decided_iff_not_pending",
            id="pending-with-decided-at",
        ),
    ],
)
def test_invalid_rows_are_refused(
    module_seeded_url, pending_user, reviewer, overrides, constraint
):
    if overrides.get("decided_by") == "reviewer":
        overrides = {**overrides, "decided_by": reviewer.id}
    with pytest.raises(IntegrityError, match=constraint):
        run_sql(module_seeded_url, INSERT, request_values(pending_user, **overrides))


def test_a_decided_row_needs_both_the_time_and_the_reviewer(
    module_seeded_url, pending_user, reviewer
):
    with pytest.raises(IntegrityError, match="decided_iff_not_pending"):
        run_sql(
            module_seeded_url,
            INSERT,
            request_values(pending_user, status="approved", decided_at=CLOCK_START),
        )
    run_sql(
        module_seeded_url,
        INSERT,
        request_values(
            pending_user,
            status="approved",
            decided_at=CLOCK_START,
            decided_by=reviewer.id,
        ),
    )


def test_a_second_request_for_the_same_user_is_refused(module_seeded_url, pending_user):
    run_sql(module_seeded_url, INSERT, request_values(pending_user))

    with pytest.raises(IntegrityError, match="uq_registration_requests_user_id"):
        run_sql(module_seeded_url, INSERT, request_values(pending_user, role="student"))


def test_a_user_with_a_request_cannot_be_deleted(module_seeded_url, pending_user):
    run_sql(module_seeded_url, INSERT, request_values(pending_user))

    with pytest.raises(IntegrityError, match="fk_registration_requests_user_id_users"):
        run_sql(
            module_seeded_url,
            "DELETE FROM users WHERE id = :id",
            {"id": pending_user.id},
        )


# --- B: submitting -----------------------------------------------------------------


@pytest.mark.parametrize("role", ["guardian", "student"])
def test_a_request_creates_a_pending_user_without_roles(
    module_seeded_url, client, clock, role
):
    username = unique_username("Mixed.Case")

    response = register(
        client, username=f"  {username}  ", role=role, display_name="  Rosa Díaz  "
    )

    assert code_of(response) == ACCEPTED
    assert response.json() == {"status": "pending"}
    row = request_row(module_seeded_url, username.lower())
    assert row is not None
    assert (row.state, row.status, row.requested_role, row.display_name) == (
        "pending",
        "pending",
        role,
        "Rosa Díaz",
    )
    assert (row.decided_at, row.decided_by_user_id) == (None, None)
    assert roles_of(module_seeded_url, row.user_id) == []
    # Only the Argon2id hash is stored.
    assert row.password_hash.startswith("$argon2id$")
    assert verify_password(row.password_hash, REQUEST_PASSWORD)
    # Pending users cannot sign in (#23 D3).
    assert code_of(login(client, username.lower(), REQUEST_PASSWORD)) == (
        401,
        "invalid_credentials",
    )


@pytest.mark.parametrize(
    "role", ["tutor", "owner", "administrator", "academic_coordinator", "x", ""]
)
def test_any_other_role_is_422_and_writes_nothing(module_seeded_url, client, role):
    username = unique_username()

    response = register(client, username=username, role=role)

    assert code_of(response) == (422, "validation_error")
    assert user_count(module_seeded_url, username) == 0


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"display_name": "   "}, id="blank-name"),
        pytest.param({"display_name": "x" * 201}, id="long-name"),
        pytest.param({"username": "x" * 257}, id="long-username"),
        pytest.param({"username": ""}, id="empty-username"),
        pytest.param({"password": ""}, id="empty-password"),
        pytest.param({"password": "p" * 1025}, id="huge-password"),
        pytest.param({"role": None}, id="no-role"),
    ],
)
def test_an_invalid_body_is_422(module_seeded_url, client, body):
    username = body.get("username", unique_username())
    values = {
        "username": username,
        "display_name": "Person",
        "password": REQUEST_PASSWORD,
        "role": "guardian",
        **body,
    }
    response = client.post(
        REGISTRATIONS, json={k: v for k, v in values.items() if v is not None}
    )

    assert code_of(response) == (422, "validation_error")
    assert user_count(module_seeded_url, username) == 0


@pytest.mark.parametrize("username", ["a b", "ab", "ñandú", "x" * 65])
def test_an_invalid_username_is_422_username_invalid(client, username):
    assert code_of(register(client, username=username)) == (422, "username_invalid")


def test_a_taken_username_is_409_in_any_case_and_state(
    module_seeded_url, client, clock
):
    existing = create_user(module_seeded_url, roles=(TUTOR,))
    archived = create_user(module_seeded_url, roles=(TUTOR,), state=ARCHIVED)
    requested = unique_username()
    assert code_of(register(client, username=requested)) == ACCEPTED

    for username in (
        existing.username,
        existing.username.upper(),
        archived.username,
        requested,
    ):
        response = register(client, username=username, role="student")
        assert code_of(response) == (409, "username_taken"), username
        assert username.lower() not in response.text
    # Nothing new: still exactly one request for the requested username.
    assert user_count(module_seeded_url, requested) == 1
    assert request_row(module_seeded_url, existing.username) is None


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("short-pw-1", "password_too_short"),
        ("p" * 129, "password_too_long"),
        (None, "password_equals_username"),
    ],
)
def test_each_password_policy_code_is_422(module_seeded_url, client, password, code):
    username = unique_username()

    response = register(client, username=username, password=password or username)

    assert code_of(response) == (422, code)
    assert user_count(module_seeded_url, username) == 0


def test_a_concurrent_request_for_the_same_username_is_409(module_seeded_url):
    username = unique_username()

    async def submit(sessionmaker: Any) -> str:
        try:
            async with UnitOfWork(sessionmaker) as uow:
                await registrations.submit_registration(
                    uow.session,
                    username=username,
                    display_name="Twin",
                    password=REQUEST_PASSWORD,
                    role="guardian",
                )
        except UsernameTakenError:
            return "taken"
        return "created"

    async def run() -> list[str]:
        engine = short_lived_engine(module_seeded_url)
        try:
            sessionmaker = create_sessionmaker(engine)
            return list(
                await asyncio.gather(submit(sessionmaker), submit(sessionmaker))
            )
        finally:
            await engine.dispose()

    assert sorted(asyncio.run(run())) == ["created", "taken"]
    assert user_count(module_seeded_url, username) == 1
    rows = run_sql(
        module_seeded_url,
        "SELECT count(*) FROM registration_requests r JOIN users u"
        " ON u.id = r.user_id WHERE u.username = :u",
        {"u": username},
    )
    assert rows[0][0] == 1


# --- B: Origin and rate limit ------------------------------------------------------


def test_a_request_without_origin_is_csrf_failed_and_not_counted(
    module_seeded_url, client
):
    username = unique_username()
    response = send_without_origin(
        client,
        "POST",
        REGISTRATIONS,
        json={
            "username": username,
            "display_name": "Person",
            "password": REQUEST_PASSWORD,
            "role": "guardian",
        },
    )

    assert code_of(response) == (403, "csrf_failed")
    assert user_count(module_seeded_url, username) == 0
    assert (
        run_sql(module_seeded_url, "SELECT count(*) FROM rate_limit_counters")[0][0]
        == 0
    )


def test_the_11th_request_in_an_hour_is_429_and_the_next_hour_accepts(
    module_seeded_url, client, clock
):
    reset_rate_limits(module_seeded_url)
    accepted = unique_username()
    taken = create_user(module_seeded_url, roles=(TUTOR,))

    # Every request counts, whatever its answer (#27): 202, 409, 422.
    assert code_of(register(client, username=accepted)) == ACCEPTED
    assert code_of(register(client, username=taken.username)) == (409, "username_taken")
    for _ in range(8):
        assert code_of(client.post(REGISTRATIONS, json={})) == (422, "validation_error")

    late = unique_username()
    clock.advance(timedelta(minutes=59, seconds=59))
    response = register(client, username=late)
    assert code_of(response) == (429, "rate_limited")
    assert response.headers["Retry-After"] == "1"
    assert user_count(module_seeded_url, late) == 0

    clock.set(CLOCK_START + timedelta(hours=1))
    assert code_of(register(client, username=late)) == ACCEPTED


def test_a_429_reveals_nothing_and_runs_no_check(module_seeded_url, client, clock):
    reset_rate_limits(module_seeded_url)
    for _ in range(10):
        client.post(REGISTRATIONS, json={})
    taken = create_user(module_seeded_url, roles=(TUTOR,))

    first = register(client, username=taken.username)
    second = register(client, username=unique_username())

    assert code_of(first) == code_of(second) == (429, "rate_limited")
    assert int(first.headers["Retry-After"]) == 3600
    strip = lambda r: {k: v for k, v in r.json().items() if k != "request_id"}  # noqa: E731
    assert strip(first) == strip(second)


# --- D: audit ----------------------------------------------------------------------


def test_a_request_writes_the_named_event_without_secrets(
    module_seeded_url, client, clock
):
    username = unique_username()
    assert code_of(register(client, username=username, role="student")) == ACCEPTED
    row = request_row(module_seeded_url, username)

    [event] = events(module_seeded_url, record_id=row.id, action=REQUESTED_EVENT)
    assert event.record_type == "registration_requests"
    assert event.actor_user_id is None
    assert event.details == {"user_id": str(row.user_id), "requested_role": "student"}
    automatic = {
        (e.record_type, e.action)
        for record_id in (row.id, row.user_id)
        for e in events(module_seeded_url, record_id=record_id)
    }
    assert {
        ("users", "create"),
        ("registration_requests", "create"),
        ("registration_requests", REQUESTED_EVENT),
    } <= automatic
    no_secret_in_audit(module_seeded_url, REQUEST_PASSWORD, row.password_hash)


def test_a_request_raises_its_approval_request(module_seeded_url, client, clock):
    """#40 D1, E1: the same transaction creates a pending
    ``identity.registration`` approval request (with its create event)."""
    username = unique_username()
    assert code_of(register(client, username=username)) == ACCEPTED
    row = request_row(module_seeded_url, username)

    assert row.approval_id is not None
    assert row.approval_status == "pending"
    assert row.requester_user_id == row.user_id
    assert row.resolved_at is None
    [created] = events(module_seeded_url, record_id=row.approval_id, action="create")
    assert created.record_type == "approval_requests"
    assert created.actor_user_id is None


def test_failed_requests_write_no_event(module_seeded_url, client, clock):
    taken = create_user(module_seeded_url, roles=(TUTOR,))
    before = len(events(module_seeded_url, action=REQUESTED_EVENT))

    register(client, username=taken.username)  # 409
    register(client, role="tutor")  # 422
    register(client, password="short-pw-1")  # 422
    register(client, username="a b")  # 422
    reset_rate_limits(module_seeded_url)
    for _ in range(10):
        client.post(REGISTRATIONS, json={})
    assert code_of(register(client)) == (429, "rate_limited")

    assert len(events(module_seeded_url, action=REQUESTED_EVENT)) == before


def test_the_password_is_stored_in_no_table(module_seeded_url, client, clock):
    password = "plain-request-password"
    username = unique_username()
    assert code_of(register(client, username=username, password=password)) == ACCEPTED

    tables = run_sql(
        module_seeded_url,
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
    )
    for table in tables:
        rows = run_sql(
            module_seeded_url,
            f'SELECT count(*) FROM "{table.tablename}" t'  # noqa: S608
            " WHERE position(:secret IN t::text) > 0",
            {"secret": password},
        )
        assert rows[0][0] == 0, table.tablename
    row = request_row(module_seeded_url, username)
    assert row.password_hash.startswith("$argon2id$")
