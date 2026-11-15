"""User preferences: ``SessionUser.preferences`` and
``PATCH /api/v1/auth/me/preferences`` (#36 B, C, D), with real logins.

The tests share one database per module and each creates its own users.
"""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.db import create_sessionmaker
from app.modules.identity import service as identity
from app.modules.identity.permissions import GUARDIAN, OWNER, STUDENT, TUTOR
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    ME,
    CreatedUser,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    login,
    only_set_cookie,
)
from tests.integration.catalog_support import get, patch
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import code_of, events, signed_in
from tests.integration.recovery_support import change

PREFERENCES = "/api/v1/auth/me/preferences"
NEVER_STORED = {"language": None, "theme": None}
OK = (200, None)
INVALID = (422, "validation_error")
NEW_PASSWORD = "a-changed-password-36"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def new_user(url: str, *roles: str, **overrides: Any) -> CreatedUser:
    return create_user(
        url,
        roles=roles or (TUTOR,),
        username=f"prefs.{uuid.uuid4().hex[:12]}",
        **overrides,
    )


def save(client: TestClient, cookie: SetCookie, body: Any) -> Any:
    return patch(client, cookie, PREFERENCES, body)


def me_preferences(client: TestClient, cookie: SetCookie) -> dict[str, Any]:
    response = get(client, cookie, ME)
    assert response.status_code == 200, response.text
    preferences: dict[str, Any] = response.json()["preferences"]
    return preferences


def preference_rows(url: str, user_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT language, theme, created_at, updated_at FROM user_preferences"
        " WHERE user_id = :user_id",
        {"user_id": user_id},
    )


def stored(url: str, user_id: uuid.UUID) -> tuple[Any, Any] | None:
    rows = preference_rows(url, user_id)
    assert len(rows) <= 1
    return (rows[0].language, rows[0].theme) if rows else None


def user_row(url: str, user_id: uuid.UUID) -> tuple[Any, Any]:
    [row] = run_sql(
        url, "SELECT version, updated_at FROM users WHERE id = :id", {"id": user_id}
    )
    return row.version, row.updated_at


def audit_count(url: str) -> int:
    [row] = run_sql(url, "SELECT count(*) AS n FROM audit_events")
    return int(row.n)


# --- C3: SessionUser.preferences ------------------------------------------------------


def test_a_user_without_a_row_gets_nulls_on_login_and_me(module_seeded_url, client):
    user = new_user(module_seeded_url)

    response = login(client, user.username)

    assert response.status_code == 200
    assert response.json()["preferences"] == NEVER_STORED
    client.cookies.clear()
    cookie = only_set_cookie(response)
    assert me_preferences(client, cookie) == NEVER_STORED
    assert stored(module_seeded_url, user.id) is None


def test_saved_values_come_back_on_login_me_and_password_change(
    module_seeded_url, client
):
    user = new_user(module_seeded_url, GUARDIAN)
    cookie = signed_in(client, user.username)
    assert code_of(save(client, cookie, {"language": "en", "theme": "dark"})) == OK
    saved = {"language": "en", "theme": "dark"}

    again = login(client, user.username)
    client.cookies.clear()
    assert again.json()["preferences"] == saved
    fresh = only_set_cookie(again)
    assert me_preferences(client, fresh) == saved
    changed = change(client, fresh, TEST_USER_PASSWORD, NEW_PASSWORD)

    assert code_of(changed) == OK
    assert changed.json()["preferences"] == saved
    # The other SessionUser fields are unchanged by #36.
    assert set(changed.json()) == {
        "id",
        "username",
        "display_name",
        "roles",
        "permissions",
        "csrf_token",
        "privacy_notice_required",
        "preferences",
        "center_time_zone",
    }


def test_one_users_save_never_shows_in_anothers_session(module_seeded_url, client):
    first = new_user(module_seeded_url)
    second = new_user(module_seeded_url)
    first_cookie = signed_in(client, first.username)
    second_cookie = signed_in(client, second.username)

    assert code_of(save(client, first_cookie, {"theme": "dark"})) == OK

    assert me_preferences(client, first_cookie) == {"language": None, "theme": "dark"}
    assert me_preferences(client, second_cookie) == NEVER_STORED
    assert login(client, second.username).json()["preferences"] == NEVER_STORED
    assert stored(module_seeded_url, second.id) is None


# --- C2: at most one more statement ---------------------------------------------------


@pytest.fixture
def statements(client: TestClient) -> Iterator[list[str]]:
    """Every SQL statement the app's request engine runs during the test."""
    engine = client.app.state.engine.sync_engine  # type: ignore[attr-defined]
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_me_reads_the_preferences_in_one_statement(
    module_seeded_url, client, statements
):
    user = new_user(module_seeded_url)
    cookie = signed_in(client, user.username)
    assert code_of(save(client, cookie, {"language": "en"})) == OK

    statements.clear()
    assert me_preferences(client, cookie) == {"language": "en", "theme": None}

    # Exactly one statement more than before #36: the read of the row.
    reads = [s for s in statements if "user_preferences" in s]
    assert len(reads) == 1, statements
    assert reads[0].lstrip().upper().startswith("SELECT")


def test_get_user_preferences_is_one_statement(module_seeded_url):
    user = new_user(module_seeded_url)
    seen: list[str] = []

    async def _run() -> Any:
        engine = short_lived_engine(module_seeded_url)

        def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            async with create_sessionmaker(engine)() as session:
                return await identity.get_user_preferences(session, user.id)
        finally:
            await engine.dispose()

    result = asyncio.run(_run())

    assert result.model_dump() == NEVER_STORED
    assert len([s for s in seen if "user_preferences" in s]) == 1


# --- D5: PATCH /auth/me/preferences ---------------------------------------------------


def test_partial_saves_build_up_one_row(module_seeded_url, client):
    user = new_user(module_seeded_url)
    cookie = signed_in(client, user.username)
    steps = [
        ({"language": "en"}, {"language": "en", "theme": None}),
        ({"theme": "dark"}, {"language": "en", "theme": "dark"}),
        ({"language": "es", "theme": "system"}, {"language": "es", "theme": "system"}),
    ]

    for body, expected in steps:
        response = save(client, cookie, body)

        assert code_of(response) == OK
        assert response.json() == expected
        assert me_preferences(client, cookie) == expected
        assert len(preference_rows(module_seeded_url, user.id)) == 1


def test_repeating_a_save_keeps_one_row_with_the_same_values(module_seeded_url, client):
    user = new_user(module_seeded_url)
    cookie = signed_in(client, user.username)
    body = {"language": "en", "theme": "light"}

    first = save(client, cookie, body)
    second = save(client, cookie, body)

    assert code_of(first) == code_of(second) == OK
    assert first.json() == second.json() == body
    assert stored(module_seeded_url, user.id) == ("en", "light")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"language": "fr"},
        {"language": "EN"},
        {"theme": "blue"},
        {"language": None},
        {"theme": None},
        {"language": "en", "theme": None},
        {"language": "en", "user_id": "00000000-0000-4000-8000-000000000001"},
        {"user_id": "00000000-0000-4000-8000-000000000001"},
    ],
)
def test_invalid_bodies_are_422_and_change_nothing(module_seeded_url, client, body):
    user = new_user(module_seeded_url)
    cookie = signed_in(client, user.username)
    assert code_of(save(client, cookie, {"language": "es", "theme": "dark"})) == OK

    assert code_of(save(client, cookie, body)) == INVALID

    assert stored(module_seeded_url, user.id) == ("es", "dark")


def test_an_invalid_first_save_writes_no_row(module_seeded_url, client):
    user = new_user(module_seeded_url)
    cookie = signed_in(client, user.username)

    assert code_of(save(client, cookie, {"theme": None})) == INVALID

    assert stored(module_seeded_url, user.id) is None


def test_without_a_csrf_token_it_is_403(module_seeded_url, client):
    user = new_user(module_seeded_url)
    cookie = signed_in(client, user.username)
    client.cookies.clear()

    response = client.patch(
        PREFERENCES, json={"theme": "dark"}, headers=cookie_header(cookie)
    )

    assert code_of(response) == (403, "csrf_failed")
    assert stored(module_seeded_url, user.id) is None


def test_without_a_session_it_is_401(module_seeded_url, client):
    client.cookies.clear()

    response = client.patch(PREFERENCES, json={"theme": "dark"})

    assert code_of(response) == (401, "authentication_required")


def test_the_order_is_401_then_csrf_then_422(module_seeded_url, client):
    user = new_user(module_seeded_url)
    cookie = signed_in(client, user.username)
    client.cookies.clear()

    anonymous_invalid = client.patch(PREFERENCES, json={"theme": "blue"})
    no_token_invalid = client.patch(
        PREFERENCES, json={"theme": "blue"}, headers=cookie_header(cookie)
    )

    assert code_of(anonymous_invalid) == (401, "authentication_required")
    assert code_of(no_token_invalid) == (403, "csrf_failed")
    assert code_of(save(client, cookie, {"theme": "blue"})) == INVALID


def test_a_user_who_has_not_accepted_the_notice_can_save(module_seeded_url, client):
    user = new_user(module_seeded_url, OWNER, privacy_notice_accepted=False)
    cookie = signed_in(client, user.username)
    assert get(client, cookie, "/api/v1/users").status_code == 403

    response = save(client, cookie, {"language": "en"})

    assert code_of(response) == OK
    assert me_preferences(client, cookie) == {"language": "en", "theme": None}


@pytest.mark.parametrize("role", [TUTOR, STUDENT, GUARDIAN])
def test_any_signed_in_role_saves_its_own(module_seeded_url, client, role):
    user = new_user(module_seeded_url, role)
    cookie = signed_in(client, user.username)

    response = save(client, cookie, {"theme": "dark"})

    assert code_of(response) == OK
    assert stored(module_seeded_url, user.id) == (None, "dark")


def test_saving_never_touches_the_users_row_or_the_audit_trail(
    module_seeded_url, client
):
    user = new_user(module_seeded_url)
    cookie = signed_in(client, user.username)
    before_user = user_row(module_seeded_url, user.id)
    before_events = audit_count(module_seeded_url)
    before_user_events = len(events(module_seeded_url, record_id=user.id))

    for body in ({"language": "en"}, {"theme": "dark"}, {"theme": "light"}):
        assert code_of(save(client, cookie, body)) == OK

    assert user_row(module_seeded_url, user.id) == before_user
    assert audit_count(module_seeded_url) == before_events
    assert len(events(module_seeded_url, record_id=user.id)) == before_user_events
    assert events(module_seeded_url, record_type="user_preferences") == []


def test_concurrent_first_saves_end_with_one_row_holding_both(module_seeded_url):
    """Two connections save one user's first language and first theme at
    once (the #31 C3 barrier): both succeed, one row holds both values."""
    user = new_user(module_seeded_url)

    async def _race() -> list[Any]:
        engine = short_lived_engine(module_seeded_url)
        barrier = asyncio.Barrier(2)
        try:
            maker = create_sessionmaker(engine)

            async def _save(**fields: Any) -> Any:
                async with maker() as session:
                    await session.connection()  # both transactions open first
                    await barrier.wait()
                    result = await identity.update_user_preferences(
                        session, user.id, **fields
                    )
                    await session.commit()
                    return result

            return list(await asyncio.gather(_save(language="en"), _save(theme="dark")))
        finally:
            await engine.dispose()

    results = asyncio.run(_race())

    assert {r.language for r in results} >= {"en"}
    assert {r.theme for r in results} >= {"dark"}
    assert stored(module_seeded_url, user.id) == ("en", "dark")
