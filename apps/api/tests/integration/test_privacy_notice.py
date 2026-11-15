"""Privacy-notice versions, acceptance and the server-side gate (#174 B, D, F).

Real logins through the real app (``auth_support.auth_client``). Tests that
publish a version (raw SQL, as a migration would) get their own database
(``seeded_url``); the others share one per module and create their own users.
Version 1 is published by migration 0017 at 2026-10-18 00:00 UTC, before the
frozen clock's start (``tests.conftest.CLOCK_START``).
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.modules.identity import service as identity
from app.modules.identity.permissions import GUARDIAN, OWNER, STUDENT, TUTOR
from tests.conftest import CLOCK_START
from tests.integration.auth_support import (
    LOGOUT,
    ME,
    CreatedUser,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    login,
    signed_headers,
)
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import code_of, events, in_uow, signed_in

NOTICE = "/api/v1/privacy-notice"
ACCEPTANCE = "/api/v1/privacy-notice/acceptance"
USERS = "/api/v1/users"
STUDENTS = "/api/v1/students"
REQUIRED = (403, "privacy_notice_required")
OUTDATED = (409, "privacy_notice_outdated")
OK = (200, None)
V1_PUBLISHED_AT = datetime(2026, 10, 18, tzinfo=UTC)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def pending_user(url: str, *roles: str) -> CreatedUser:
    """A committed active user who has not accepted the privacy notice."""
    return create_user(url, roles=roles or (OWNER,), privacy_notice_accepted=False)


def accept(client: TestClient, cookie: SetCookie, version: Any) -> Any:
    return post(client, cookie, ACCEPTANCE, {"version": version})


def me_body(client: TestClient, cookie: SetCookie) -> dict[str, Any]:
    response = get(client, cookie, ME)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def acceptance_rows(url: str, user_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT a.accepted_at, v.version FROM privacy_notice_acceptances a"
        " JOIN privacy_notice_versions v ON v.id = a.notice_version_id"
        " WHERE a.user_id = :user_id ORDER BY v.version",
        {"user_id": user_id},
    )


def publish(url: str, version: int, published_at: datetime) -> None:
    """Publish a version with raw SQL, as a migration would."""
    run_sql(
        url,
        "INSERT INTO privacy_notice_versions (version, published_at, body_es,"
        " body_en) VALUES (:version, :at, :es, :en)",
        {
            "version": version,
            "at": published_at,
            "es": f"Borrador v{version}",
            "en": f"Draft v{version}",
        },
    )


def acceptance_events(url: str, user_id: uuid.UUID) -> list[Any]:
    return [
        e
        for e in events(url, record_type="privacy_notice_acceptances")
        if e.changes and e.changes.get("user_id", {}).get("new") == str(user_id)
    ]


# --- D4: a new user ------------------------------------------------------------------


def test_a_new_user_must_accept_before_anything_else(module_seeded_url, client):
    owner = pending_user(module_seeded_url)

    response = login(client, owner.username)
    assert response.status_code == 200
    assert response.json()["privacy_notice_required"] is True
    client.cookies.clear()
    cookie = signed_in(client, owner.username)

    assert code_of(get(client, cookie, USERS)) == REQUIRED
    assert me_body(client, cookie)["privacy_notice_required"] is True
    notice = get(client, cookie, NOTICE)
    assert notice.status_code == 200
    body = notice.json()
    assert set(body) == {"version", "published_at", "body_es", "body_en", "accepted_at"}
    assert body["version"] == 1
    assert datetime.fromisoformat(body["published_at"]) == V1_PUBLISHED_AT
    assert body["body_es"].startswith("Borrador pendiente de revisión legal.")
    assert body["body_en"].startswith("Draft pending legal review.")
    assert body["accepted_at"] is None
    client.cookies.clear()
    assert client.post(LOGOUT, headers=signed_headers(cookie)).status_code == 204


def test_accepting_opens_the_same_session_and_is_idempotent(module_seeded_url, client):
    owner = pending_user(module_seeded_url)
    cookie = signed_in(client, owner.username)
    assert code_of(get(client, cookie, USERS)) == REQUIRED

    first = accept(client, cookie, 1)

    assert code_of(first) == OK
    assert set(first.json()) == {"version", "accepted_at"}
    assert first.json()["version"] == 1
    assert code_of(get(client, cookie, USERS)) == OK
    assert me_body(client, cookie)["privacy_notice_required"] is False
    notice = get(client, cookie, NOTICE).json()
    assert notice["accepted_at"] == first.json()["accepted_at"]

    again = accept(client, cookie, 1)

    assert code_of(again) == OK
    assert again.json() == first.json()
    assert len(acceptance_rows(module_seeded_url, owner.id)) == 1
    assert len(acceptance_events(module_seeded_url, owner.id)) == 1


def test_a_new_login_after_accepting_needs_nothing(module_seeded_url, client):
    owner = pending_user(module_seeded_url)
    assert code_of(accept(client, signed_in(client, owner.username), 1)) == OK

    response = login(client, owner.username)
    client.cookies.clear()

    assert response.json()["privacy_notice_required"] is False


def test_the_password_change_response_carries_the_flag(module_seeded_url, client):
    owner = pending_user(module_seeded_url)
    cookie = signed_in(client, owner.username)
    body = {"current_password": "factory-test-password", "new_password": "x" * 12}

    # Not exempt: the user accepts first (#174 C3).
    client.cookies.clear()
    gated = client.post(
        "/api/v1/auth/password", json=body, headers=signed_headers(cookie)
    )
    assert code_of(gated) == REQUIRED

    assert code_of(accept(client, cookie, 1)) == OK
    client.cookies.clear()
    changed = client.post(
        "/api/v1/auth/password",
        json={**body, "new_password": "a-brand-new-password"},
        headers=signed_headers(cookie),
    )
    client.cookies.clear()

    assert changed.status_code == 200, changed.text
    assert changed.json()["privacy_notice_required"] is False


def test_reauthentication_is_not_exempt(module_seeded_url, client):
    owner = pending_user(module_seeded_url)
    cookie = signed_in(client, owner.username)
    client.cookies.clear()

    response = client.post(
        "/api/v1/auth/reauthenticate",
        json={"password": "factory-test-password"},
        headers=signed_headers(cookie),
    )

    assert code_of(response) == REQUIRED


# --- D2: the acceptance route ------------------------------------------------------


@pytest.mark.parametrize("version", [0, 2, 99, -1])
def test_a_version_that_is_not_current_is_409_and_writes_nothing(
    module_seeded_url, client, version
):
    owner = pending_user(module_seeded_url)
    cookie = signed_in(client, owner.username)

    response = accept(client, cookie, version)

    assert code_of(response) == OUTDATED
    assert acceptance_rows(module_seeded_url, owner.id) == []
    assert acceptance_events(module_seeded_url, owner.id) == []
    assert code_of(get(client, cookie, USERS)) == REQUIRED


@pytest.mark.parametrize(
    "body", [{}, {"version": 1, "extra": True}, {"version": "one"}, {"version": None}]
)
def test_an_invalid_body_is_422(module_seeded_url, client, body):
    owner = pending_user(module_seeded_url)
    cookie = signed_in(client, owner.username)

    response = post(client, cookie, ACCEPTANCE, body)

    assert response.status_code == 422
    assert acceptance_rows(module_seeded_url, owner.id) == []


def test_the_acceptance_needs_the_csrf_token(module_seeded_url, client):
    owner = pending_user(module_seeded_url)
    cookie = signed_in(client, owner.username)
    client.cookies.clear()

    response = client.post(
        ACCEPTANCE, json={"version": 1}, headers=cookie_header(cookie)
    )

    assert code_of(response) == (403, "csrf_failed")
    assert acceptance_rows(module_seeded_url, owner.id) == []


@pytest.mark.parametrize(("method", "path"), [("GET", NOTICE), ("POST", ACCEPTANCE)])
def test_anonymous_calls_are_401(module_seeded_url, client, method, path):
    client.cookies.clear()
    before = len(events(module_seeded_url, record_type="privacy_notice_acceptances"))

    response = client.request(method, path, json={"version": 1})

    assert code_of(response) == (401, "authentication_required")
    after = len(events(module_seeded_url, record_type="privacy_notice_acceptances"))
    assert after == before


# --- C4: every role ------------------------------------------------------------------


@pytest.mark.parametrize("role", [TUTOR, STUDENT, GUARDIAN])
def test_every_role_is_gated_the_same_way(module_seeded_url, client, role):
    someone = pending_user(module_seeded_url, role)
    cookie = signed_in(client, someone.username)

    # Allowed (student.view) and denied (user.view) routes: the gate first.
    assert code_of(get(client, cookie, STUDENTS)) == REQUIRED
    assert code_of(get(client, cookie, USERS)) == REQUIRED
    assert me_body(client, cookie)["privacy_notice_required"] is True

    assert code_of(accept(client, cookie, 1)) == OK

    assert code_of(get(client, cookie, STUDENTS)) == OK
    assert code_of(get(client, cookie, USERS)) == (403, "permission_denied")
    assert me_body(client, cookie)["privacy_notice_required"] is False


# --- F1: audit -----------------------------------------------------------------------


def test_an_acceptance_is_one_create_event_by_the_user(module_seeded_url, client):
    owner = pending_user(module_seeded_url)
    cookie = signed_in(client, owner.username)

    response = accept(client, cookie, 1)

    [event] = acceptance_events(module_seeded_url, owner.id)
    assert event.action == "create"
    assert event.actor_user_id == owner.id
    assert str(event.request_id) == response.headers["X-Request-ID"]
    assert event.details is None or event.details == {}
    version_1 = run_sql(
        module_seeded_url, "SELECT id FROM privacy_notice_versions WHERE version = 1"
    )[0].id
    assert event.changes["notice_version_id"]["new"] == str(version_1)
    assert set(event.changes) <= {"user_id", "notice_version_id", "accepted_at"}


# --- D4: a new version ---------------------------------------------------------------


@pytest.fixture
def own_client(seeded_url: str) -> Iterator[TestClient]:
    with auth_client(seeded_url) as test_client:
        yield test_client


def test_a_new_version_asks_everyone_again_on_the_next_request(
    seeded_url, own_client, clock
):
    client = own_client
    owner = pending_user(seeded_url)
    other = pending_user(seeded_url, TUTOR)
    cookie = signed_in(client, owner.username)
    other_cookie = signed_in(client, other.username)
    for c in (cookie, other_cookie):
        assert code_of(accept(client, c, 1)) == OK
    assert code_of(get(client, cookie, USERS)) == OK

    publish(seeded_url, 2, CLOCK_START - timedelta(minutes=1))

    # The same session, its next request: no sign-out needed.
    assert code_of(get(client, cookie, USERS)) == REQUIRED
    assert me_body(client, cookie)["privacy_notice_required"] is True
    notice = get(client, cookie, NOTICE).json()
    assert (notice["version"], notice["accepted_at"]) == (2, None)
    assert notice["body_en"] == "Draft v2"
    assert code_of(accept(client, cookie, 1)) == OUTDATED
    assert code_of(accept(client, cookie, 2)) == OK
    assert code_of(get(client, cookie, USERS)) == OK
    assert [r.version for r in acceptance_rows(seeded_url, owner.id)] == [1, 2]
    # Another user who accepted version 1 is asked again too.
    assert code_of(get(client, other_cookie, STUDENTS)) == REQUIRED
    assert me_body(client, other_cookie)["privacy_notice_required"] is True


def test_a_future_version_becomes_current_when_its_time_comes(
    seeded_url, own_client, clock
):
    client = own_client
    owner = pending_user(seeded_url)
    publish(seeded_url, 2, CLOCK_START - timedelta(minutes=1))
    publish(seeded_url, 3, CLOCK_START + timedelta(hours=1))
    cookie = signed_in(client, owner.username)

    assert get(client, cookie, NOTICE).json()["version"] == 2
    assert code_of(accept(client, cookie, 3)) == OUTDATED
    assert code_of(accept(client, cookie, 2)) == OK
    assert code_of(get(client, cookie, USERS)) == OK

    clock.advance(timedelta(hours=1))
    cookie = signed_in(client, owner.username)  # the old one may be idle by now

    assert code_of(get(client, cookie, USERS)) == REQUIRED
    assert get(client, cookie, NOTICE).json()["version"] == 3
    assert code_of(accept(client, cookie, 2)) == OUTDATED
    assert code_of(accept(client, cookie, 3)) == OK
    assert code_of(get(client, cookie, USERS)) == OK


def test_with_no_published_version_everyone_is_refused(seeded_url, own_client, clock):
    """A5: fail closed. The clock before version 1's publication stands for a
    database where no version is published (versions can't be deleted)."""
    owner = create_user(seeded_url, roles=(OWNER,))  # accepted version 1
    clock.set(V1_PUBLISHED_AT - timedelta(seconds=1))
    cookie = signed_in(own_client, owner.username)

    assert code_of(get(own_client, cookie, USERS)) == REQUIRED
    assert me_body(own_client, cookie)["privacy_notice_required"] is True
    with pytest.raises(identity.PrivacyNoticeUnavailableError):
        in_uow(seeded_url, identity.current_privacy_notice)
    assert own_client.get(NOTICE, headers=cookie_header(cookie)).status_code == 500


# --- B1: the service -----------------------------------------------------------------


def test_the_service_functions(seeded_url):
    user = pending_user(seeded_url, TUTOR)

    async def _check(session: AsyncSession) -> None:
        current = await identity.current_privacy_notice(session)
        assert (current.version, current.published_at) == (1, V1_PUBLISHED_AT)
        assert not await identity.has_accepted_current_privacy_notice(
            session, user_id=user.id
        )
        with pytest.raises(identity.PrivacyNoticeOutdatedError):
            await identity.accept_privacy_notice(session, user_id=user.id, version=7)
        first = await identity.accept_privacy_notice(
            session, user_id=user.id, version=1
        )
        again = await identity.accept_privacy_notice(
            session, user_id=user.id, version=1
        )
        assert again.id == first.id
        assert first.notice_version_id == current.id
        assert await identity.has_accepted_current_privacy_notice(
            session, user_id=user.id
        )

    in_uow(seeded_url, _check)


def test_concurrent_acceptances_store_one_row(seeded_url):
    """The second transaction waits on the unique constraint, then returns the
    first one's row."""
    user = pending_user(seeded_url, TUTOR)

    async def _race() -> tuple[uuid.UUID, uuid.UUID]:
        engine = short_lived_engine(seeded_url)
        try:
            maker = create_sessionmaker(engine)
            async with maker() as first, maker() as second:
                won = await identity.accept_privacy_notice(
                    first, user_id=user.id, version=1
                )
                waiting = asyncio.create_task(
                    identity.accept_privacy_notice(second, user_id=user.id, version=1)
                )
                await asyncio.sleep(0.5)
                assert not waiting.done()
                await first.commit()
                lost = await waiting
                await second.commit()
                return won.id, lost.id
        finally:
            await engine.dispose()

    won, lost = asyncio.run(_race())

    assert won == lost
    assert len(acceptance_rows(seeded_url, user.id)) == 1


# --- B2: load_principal --------------------------------------------------------------


def test_load_principal_reads_the_flag_in_its_existing_statements(seeded_url):
    accepted = create_user(seeded_url, roles=(TUTOR,))
    pending = pending_user(seeded_url, TUTOR)
    statements: dict[str, int] = {}
    flags: dict[str, bool] = {}
    for name, user in (("accepted", accepted), ("pending", pending)):

        async def _load(session: AsyncSession, user_id=user.id) -> Any:
            return await identity.load_principal(session, user_id)

        count, principal = count_statements(seeded_url, _load)
        statements[name] = count
        flags[name] = principal.privacy_notice_accepted

    # Before #174: 6 statements (state, roles, then effective_permissions:
    # state, roles, role permissions, delegations). At most one more is
    # allowed; the flag is a subquery of the state statement, so none is.
    assert statements == {"accepted": 6, "pending": 6}
    assert flags == {"accepted": True, "pending": False}
