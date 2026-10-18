"""``audit_events``: append-only triggers, CHECKs and the actor key (M2-04 B5).

Raw SQL, as the role the application uses (the Testcontainers superuser,
like ``POSTGRES_USER`` in Compose), both directly and through the test
PgBouncer (the API's connection path, ADR-0003).
"""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import make_url, pool, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.db import async_database_url, engine_options
from tests.integration.conftest import settings_for, short_lived_engine

pytestmark = pytest.mark.anyio

ROW_ID = "0199a000-0000-7000-8000-0000000000e1"
USER_ID = "0199a000-0000-7000-8000-0000000000e2"
INSERT_EVENT = (
    "INSERT INTO audit_events (id, actor_user_id, action, record_type, record_id,"
    " changes, details, request_id) VALUES (:id, :actor, 'identity.test_event',"
    " 'users', :actor, '{\"a\": {\"old\": null, \"new\": 1}}', NULL, 'req-1')"
)
SELECT_EVENT = (
    "SELECT action, record_type, changes::text, request_id FROM audit_events"
    " WHERE id = :id"
)


def pooled_engine(pgbouncer_url: str, database_url: str) -> AsyncEngine:
    """An engine for ``database_url``'s database through the test PgBouncer.

    Same driver options as the API's request engine (no statement cache).
    """
    url = (
        make_url(pgbouncer_url)
        .set(database=make_url(database_url).database)
        .render_as_string(hide_password=False)
    )
    return create_async_engine(
        async_database_url(settings_for(url)),
        poolclass=pool.NullPool,
        **engine_options(),
    )


@pytest.fixture(params=["direct", "pgbouncer"])
def engine_for(request, pgbouncer_url) -> Callable[[str], AsyncEngine]:
    if request.param == "direct":
        return short_lived_engine
    return lambda dsn: pooled_engine(pgbouncer_url, dsn)


async def execute(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        return list(result.all()) if result.returns_rows else []


def sqlstate(error: DBAPIError) -> str | None:
    orig = error.orig
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if code is None and orig is not None and orig.__cause__ is not None:
        code = getattr(orig.__cause__, "sqlstate", None)
    return code


async def assert_fails(engine: AsyncEngine, sql: str, state: str, **params) -> str:
    with pytest.raises(DBAPIError) as raised:
        await execute(engine, sql, **params)
    assert sqlstate(raised.value) == state, raised.value
    return str(raised.value.orig)


@pytest.fixture
async def engine(head_url, engine_for):
    engine = engine_for(head_url)
    try:
        await execute(
            engine,
            "INSERT INTO users (id, username, display_name, state)"
            " VALUES (:id, 'audit.actor', 'Actor', 'active')",
            id=USER_ID,
        )
        await execute(engine, INSERT_EVENT, id=ROW_ID, actor=USER_ID)
        yield engine
    finally:
        await engine.dispose()


async def test_insert_works(engine):
    rows = await execute(engine, SELECT_EVENT, id=ROW_ID)

    assert [tuple(r) for r in rows] == [
        ("identity.test_event", "users", '{"a": {"new": 1, "old": null}}', "req-1")
    ]
    [occurred] = await execute(
        engine, "SELECT occurred_at IS NOT NULL FROM audit_events"
    )
    assert occurred[0] is True


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_events SET action = 'update'",
        "UPDATE audit_events SET changes = NULL WHERE id = :id",
        "DELETE FROM audit_events",
        "DELETE FROM audit_events WHERE id = :id",
        "TRUNCATE audit_events",
        "TRUNCATE audit_events CASCADE",
    ],
)
async def test_rows_cannot_change(engine, sql):
    params = {"id": ROW_ID} if ":id" in sql else {}

    message = await assert_fails(engine, sql, "42501", **params)

    assert "audit_events is append-only" in message
    rows = await execute(engine, SELECT_EVENT, id=ROW_ID)
    assert [tuple(r) for r in rows] == [
        ("identity.test_event", "users", '{"a": {"new": 1, "old": null}}', "req-1")
    ]


async def test_the_application_role_is_a_superuser(engine):
    # Why the triggers exist (#22 E2): REVOKE would not bind this role.
    [row] = await execute(
        engine, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    )
    assert row[0] is True


@pytest.mark.parametrize("action", ["Create", "identity", "identity.", "x.Y", ""])
async def test_action_check_rejects_invalid_actions(engine, action):
    await assert_fails(
        engine,
        "INSERT INTO audit_events (action) VALUES (:action)",
        "23514",
        action=action,
    )


@pytest.mark.parametrize(
    "action",
    ["create", "update", "delete", "soft_delete", "restore", "identity.login_failed"],
)
async def test_action_check_accepts_valid_actions(engine, action):
    await execute(
        engine, "INSERT INTO audit_events (action) VALUES (:action)", action=action
    )


async def test_record_pair_check(engine):
    await assert_fails(
        engine,
        "INSERT INTO audit_events (action, record_type) VALUES ('a.b', 'users')",
        "23514",
    )
    await assert_fails(
        engine,
        "INSERT INTO audit_events (action, record_id) VALUES ('a.b', :id)",
        "23514",
        id=USER_ID,
    )


async def test_an_actor_cannot_be_deleted(engine):
    # ON DELETE RESTRICT: restrict_violation (23001).
    await assert_fails(engine, "DELETE FROM users WHERE id = :id", "23001", id=USER_ID)

    [count] = await execute(engine, "SELECT count(*) FROM users")
    assert count[0] == 1
