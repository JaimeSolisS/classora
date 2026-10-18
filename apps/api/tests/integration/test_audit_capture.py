"""Automatic and explicit audit events in the Unit of Work's transaction (M2-04 C, D2).

The model is the test-only :class:`~tests.integration.models.AuditedThing` on a
database migrated to head (so ``audit_events`` and its triggers exist) plus
the test-only tables.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import audit
from app.core.audit import AuditBypassError, AuditEvent, record_audit_event
from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    get_current_principal,
    public,
    require,
)
from app.core.config import get_settings
from app.core.conventions import install_conventions
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork, UnitOfWorkDep
from app.main import lifespan
from tests.audit.assertions import assert_no_secret_in_audit, audit_events_for
from tests.integration.conftest import (
    run_sql,
    run_sql_async,
    settings_for,
    short_lived_engine,
)
from tests.integration.models import AuditedThing, TestBase

OWNER_REF = uuid.UUID("0199a000-0000-7000-8000-0000000000c1")
TOKEN_HASH = "$argon2id$secret-token-hash"
PIN = "4321"


@pytest.fixture
def audited_url(head_url: str) -> str:
    """At head, plus the test-only tables (``audited_things``)."""

    async def _create() -> None:
        engine = short_lived_engine(head_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(TestBase.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_create())
    return head_url


@pytest.fixture
async def sessionmaker(
    audited_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = short_lived_engine(audited_url)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


def new_thing(**overrides: Any) -> AuditedThing:
    values: dict[str, Any] = {
        "name": "Cuaderno",
        "amount": Decimal("1250.00"),
        "due_on": date(2026, 11, 30),
        "owner_ref": OWNER_REF,
        "api_token_hash": TOKEN_HASH,
        "pin_secret": PIN,
    }
    return AuditedThing(**(values | overrides))


async def events(sessionmaker, **filters: Any) -> list[AuditEvent]:
    async with sessionmaker() as session:
        return await audit_events_for(session, **filters)


async def create_thing(sessionmaker, **overrides: Any) -> uuid.UUID:
    async with UnitOfWork(sessionmaker) as uow:
        thing = new_thing(**overrides)
        uow.session.add(thing)
        await uow.session.flush()
        return thing.id


async def count(url: str, table: str) -> int:
    rows = await run_sql_async(url, f"SELECT count(*) FROM {table}")  # noqa: S608
    return int(rows[0][0])


pytestmark = pytest.mark.anyio

# --- Automatic events ---------------------------------------------------------

CREATE_CHANGES = {
    "name": {"old": None, "new": "Cuaderno"},
    "amount": {"old": None, "new": "1250.00"},
    "due_on": {"old": None, "new": "2026-11-30"},
    "owner_ref": {"old": None, "new": str(OWNER_REF)},
    "pin_secret": {"old": "[masked]", "new": "[masked]"},
    "deleted_at": {"old": None, "new": None},
}


async def test_create(sessionmaker, audited_url):
    thing_id = await create_thing(sessionmaker)

    [event] = await events(sessionmaker)
    assert event.action == "create"
    assert (event.record_type, event.record_id) == ("audited_things", thing_id)
    assert event.changes == CREATE_CHANGES
    assert (event.actor_user_id, event.request_id, event.details) == (None, None, None)
    assert event.id.version == 7
    # The excluded column is absent even by name; the masked value never appears.
    [stored] = await run_sql_async(
        audited_url, "SELECT changes::text, details::text FROM audit_events"
    )
    assert "api_token_hash" not in stored[0]
    assert stored[1] is None
    async with sessionmaker() as session:
        await assert_no_secret_in_audit(session, TOKEN_HASH, PIN, "argon2")


async def test_update_lists_only_changed_columns(sessionmaker):
    thing_id = await create_thing(sessionmaker)

    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(AuditedThing, thing_id)
        thing.name = "Libreta"
        thing.amount = Decimal("99.50")
        thing.due_on = None

    [event] = await events(sessionmaker, action="update")
    assert event.record_id == thing_id
    # version and updated_at changed too; they are always left out.
    assert event.changes == {
        "name": {"old": "Cuaderno", "new": "Libreta"},
        "amount": {"old": "1250.00", "new": "99.50"},
        "due_on": {"old": "2026-11-30", "new": None},
    }


async def test_update_of_a_masked_column(sessionmaker):
    thing_id = await create_thing(sessionmaker)

    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(AuditedThing, thing_id)
        thing.pin_secret = "9999"

    [event] = await events(sessionmaker, action="update")
    assert event.changes == {"pin_secret": {"old": "[masked]", "new": "[masked]"}}


async def test_no_op_and_excluded_only_updates_write_nothing(sessionmaker):
    thing_id = await create_thing(sessionmaker)

    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(AuditedThing, thing_id)
        thing.name = "Cuaderno"  # its current value
        thing.amount = Decimal("1250.00")
        await uow.session.flush()
        thing.api_token_hash = "$argon2id$another-hash"  # excluded
        await uow.session.flush()

    assert [e.action for e in await events(sessionmaker)] == ["create"]
    async with sessionmaker() as session:
        await assert_no_secret_in_audit(session, "another-hash")


async def test_delete_lists_every_included_column(sessionmaker):
    thing_id = await create_thing(sessionmaker)

    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(AuditedThing, thing_id)
        await uow.session.delete(thing)

    [event] = await events(sessionmaker, action="delete")
    assert event.record_id == thing_id
    assert event.changes == {
        "name": {"old": "Cuaderno", "new": None},
        "amount": {"old": "1250.00", "new": None},
        "due_on": {"old": "2026-11-30", "new": None},
        "owner_ref": {"old": str(OWNER_REF), "new": None},
        "pin_secret": {"old": "[masked]", "new": "[masked]"},
        "deleted_at": {"old": None, "new": None},
    }


async def test_delete_of_an_expired_instance_still_lists_old_values(sessionmaker):
    thing_id = await create_thing(sessionmaker)

    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(AuditedThing, thing_id)
        uow.session.expire(thing)
        await uow.session.delete(thing)

    [event] = await events(sessionmaker, action="delete")
    assert event.changes is not None
    assert event.changes["name"] == {"old": "Cuaderno", "new": None}


async def test_soft_delete_and_restore(sessionmaker):
    thing_id = await create_thing(sessionmaker)

    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(AuditedThing, thing_id)
        thing.deleted_at = await uow.session.scalar(select(text("now()")))
        await uow.session.flush()
        deleted_at = thing.deleted_at.isoformat()
    async with UnitOfWork(sessionmaker) as uow:
        thing = await uow.session.get_one(AuditedThing, thing_id)
        thing.deleted_at = None
        thing.name = "Restaurado"

    _create, soft, restore = await events(sessionmaker, record_id=thing_id)
    assert soft.action == "soft_delete"
    assert soft.changes == {"deleted_at": {"old": None, "new": deleted_at}}
    assert restore.action == "restore"
    assert restore.changes == {
        "deleted_at": {"old": deleted_at, "new": None},
        "name": {"old": "Cuaderno", "new": "Restaurado"},
    }


async def test_several_flushes_give_one_event_per_change(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        first = new_thing(name="Uno")
        uow.session.add(first)
        await uow.session.flush()
        first.name = "Uno bis"
        await uow.session.flush()
        second = new_thing(name="Dos")
        uow.session.add(second)
        await uow.session.flush()
        second.amount = Decimal("1.00")
        first.amount = Decimal("2.00")
        # The last change is flushed by the commit.

    found = await events(sessionmaker)
    assert [(e.action, e.record_id, e.changes) for e in found[:3]] == [
        ("create", first.id, CREATE_CHANGES | {"name": {"old": None, "new": "Uno"}}),
        ("update", first.id, {"name": {"old": "Uno", "new": "Uno bis"}}),
        ("create", second.id, CREATE_CHANGES | {"name": {"old": None, "new": "Dos"}}),
    ]
    # The commit's flush changed both rows (their order within it is not fixed).
    assert [e.action for e in found[3:]] == ["update", "update"]
    assert {e.record_id: e.changes for e in found[3:]} == {
        first.id: {"amount": {"old": "1250.00", "new": "2.00"}},
        second.id: {"amount": {"old": "1250.00", "new": "1.00"}},
    }
    # One transaction: one occurred_at; ids (UUIDv7) give the order.
    assert len({e.occurred_at for e in found}) == 1
    assert [e.id for e in found] == sorted(e.id for e in found)


# --- Same transaction ---------------------------------------------------------


class Boom(Exception):
    pass


async def test_rollback_removes_the_change_and_its_events(sessionmaker, audited_url):
    with pytest.raises(Boom):
        async with UnitOfWork(sessionmaker) as uow:
            uow.session.add(new_thing())
            await uow.session.flush()
            record_audit_event(uow.session, action="sample.explicit")
            await uow.session.flush()
            written = await uow.session.scalar(
                text("SELECT count(*) FROM audit_events")
            )
            assert written == 2
            raise Boom()

    assert await count(audited_url, "audited_things") == 0
    assert await count(audited_url, "audit_events") == 0


async def test_a_failing_audit_write_rolls_back_the_change(
    sessionmaker, audited_url, monkeypatch
):
    def broken(value: Any) -> Any:
        raise TypeError("cannot serialize")

    monkeypatch.setattr(audit, "to_json_value", broken)

    with pytest.raises(TypeError, match="cannot serialize"):
        async with UnitOfWork(sessionmaker) as uow:
            uow.session.add(new_thing())

    assert await count(audited_url, "audited_things") == 0
    assert await count(audited_url, "audit_events") == 0


async def test_an_unsupported_value_fails_the_flush(sessionmaker, audited_url):
    with pytest.raises(TypeError):
        async with UnitOfWork(sessionmaker) as uow:
            # A float is not an audit value (money is Decimal, ADR-0004).
            uow.session.add(new_thing(amount=1.5))

    assert await count(audited_url, "audited_things") == 0


# --- Bypass guard (C7) --------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        update(AuditedThing).values(name="x"),
        update(AuditedThing).where(AuditedThing.name == "Cuaderno").values(name="x"),
        delete(AuditedThing),
        insert(AuditedThing).values(name="x", amount=Decimal("1.00")),
        update(AuditedThing.__table__).values(name="x"),
        delete(AuditedThing.__table__),
    ],
    ids=["update", "update-where", "delete", "insert", "core-update", "core-delete"],
)
async def test_bulk_statements_on_audited_tables_are_refused(
    sessionmaker, audited_url, statement
):
    await create_thing(sessionmaker)

    with pytest.raises(AuditBypassError, match="audited_things"):
        async with UnitOfWork(sessionmaker) as uow:
            await uow.session.execute(statement)

    [row] = await run_sql_async(audited_url, "SELECT name FROM audited_things")
    assert row[0] == "Cuaderno"
    assert [e.action for e in await events(sessionmaker)] == ["create"]


async def test_bulk_statements_on_other_tables_still_work(sessionmaker, audited_url):
    from tests.integration.models import Widget

    async with UnitOfWork(sessionmaker) as uow:
        uow.session.add(Widget(name="a"))
        await uow.session.flush()
        await uow.session.execute(update(Widget).values(name="b"))
        await uow.session.execute(delete(Widget))

    assert await count(audited_url, "widgets") == 0
    assert await count(audited_url, "audit_events") == 0


# --- Explicit events (D2) -----------------------------------------------------


async def test_explicit_events_with_and_without_a_record(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        record_audit_event(uow.session, action="sample.happened", details={"n": 1})
        record_audit_event(
            uow.session,
            action="sample.record_touched",
            record_type="audited_things",
            record_id=OWNER_REF,
            details={"amount": Decimal("3.50")},
        )

    first, second = await events(sessionmaker)
    assert (first.action, first.record_type, first.record_id) == (
        "sample.happened",
        None,
        None,
    )
    assert first.details == {"n": 1}
    assert first.changes is None
    assert (second.record_type, second.record_id) == ("audited_things", OWNER_REF)
    assert second.details == {"amount": "3.50"}


async def test_a_rejected_explicit_event_writes_nothing(sessionmaker, audited_url):
    async with UnitOfWork(sessionmaker) as uow:
        with pytest.raises(ValueError):
            record_audit_event(
                uow.session,
                action="identity.login_failed",
                details={"attempt": {"password": "hunter2-hunter2"}},
            )
        with pytest.raises(ValueError):
            record_audit_event(uow.session, action="update")

    assert await count(audited_url, "audit_events") == 0


async def test_an_explicit_event_rolls_back_with_its_unit_of_work(
    sessionmaker, audited_url
):
    with pytest.raises(Boom):
        async with UnitOfWork(sessionmaker) as uow:
            record_audit_event(uow.session, action="sample.happened")
            raise Boom()

    assert await count(audited_url, "audit_events") == 0


# --- Actor outside HTTP (C6) --------------------------------------------------


async def insert_user(url: str, username: str = "audit.actor") -> uuid.UUID:
    [row] = await run_sql_async(
        url,
        "INSERT INTO users (username, display_name, state)"
        " VALUES (:username, 'Actor', 'active') RETURNING id",
        {"username": username},
    )
    return uuid.UUID(str(row[0]))


async def test_unit_of_work_actor_id(sessionmaker, audited_url):
    actor = await insert_user(audited_url)

    async with UnitOfWork(sessionmaker, actor_id=actor) as uow:
        uow.session.add(new_thing())
        await uow.session.flush()
        record_audit_event(uow.session, action="sample.happened")
    async with UnitOfWork(sessionmaker) as uow:
        uow.session.add(new_thing(name="Sin actor"))

    found = await events(sessionmaker)
    assert [(e.action, e.actor_user_id) for e in found] == [
        ("create", actor),
        ("sample.happened", actor),
        ("create", None),
    ]


async def test_a_plain_session_has_no_actor(sessionmaker):
    async with sessionmaker() as session:
        session.add(new_thing())
        await session.flush()
        [event] = await audit_events_for(session)
        assert event.actor_user_id is None
        await session.rollback()


# --- Actor and request ID over HTTP (C6, C8) ----------------------------------

router = APIRouter()


@router.post("/things/require-first", responses=PERMISSION_RESPONSES)
async def require_first(
    principal: Annotated[Principal, require("audit.view")], uow: UnitOfWorkDep
) -> dict[str, str]:
    thing = new_thing(name="require-first")
    uow.session.add(thing)
    await uow.session.flush()
    record_audit_event(uow.session, action="sample.http")
    return {"id": str(thing.id)}


@router.post("/things/uow-first", responses=PERMISSION_RESPONSES)
async def uow_first(
    uow: UnitOfWorkDep, principal: Annotated[Principal, require("audit.view")]
) -> dict[str, str]:
    thing = new_thing(name="uow-first")
    uow.session.add(thing)
    await uow.session.flush()
    return {"id": str(thing.id)}


@router.post(
    "/things/listed",
    dependencies=[require("audit.view")],
    responses=PERMISSION_RESPONSES,
)
async def listed(uow: UnitOfWorkDep) -> dict[str, str]:
    uow.session.add(new_thing(name="listed"))
    return {}  # flushed by the commit, after the route returns


@router.post("/things/public", dependencies=[public(reason="test")])
async def public_route(uow: UnitOfWorkDep) -> dict[str, str]:
    thing = new_thing(name="public")
    uow.session.add(thing)
    await uow.session.flush()
    return {"id": str(thing.id)}


@pytest.fixture
def client(audited_url: str) -> Iterator[TestClient]:
    app = FastAPI(lifespan=lifespan)
    install_conventions(app)
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings_for(audited_url)
    with TestClient(app) as test_client:
        yield test_client


def sign_in(client: TestClient, user_id: uuid.UUID | None) -> None:
    app = client.app
    assert isinstance(app, FastAPI)
    if user_id is None:
        app.dependency_overrides.pop(get_current_principal, None)
        return
    who = Principal(
        user_id=user_id,
        roles=frozenset({"owner"}),
        permissions=frozenset({"audit.view"}),
    )
    app.dependency_overrides[get_current_principal] = lambda: who


def http_events(url: str) -> list[tuple[str, str | None, uuid.UUID | None, str | None]]:
    rows = run_sql(
        url,
        "SELECT action, record_type, actor_user_id, request_id FROM audit_events"
        " ORDER BY occurred_at, id",
    )
    return [(r[0], r[1], r[2], r[3]) for r in rows]


@pytest.mark.parametrize("path", ["require-first", "uow-first", "listed"])
def test_the_principal_is_the_actor_in_any_dependency_order(client, audited_url, path):
    actor = asyncio.run(insert_user(audited_url))
    sign_in(client, actor)

    response = client.post(f"/things/{path}")

    assert response.status_code == 200, response.text
    request_id = response.headers["X-Request-ID"]
    found = http_events(audited_url)
    assert found[0] == ("create", "audited_things", actor, request_id)
    assert all(event[2:] == (actor, request_id) for event in found)


def test_an_inbound_request_id_is_recorded(client, audited_url):
    actor = asyncio.run(insert_user(audited_url))
    sign_in(client, actor)

    response = client.post(
        "/things/require-first", headers={"X-Request-ID": "inbound-req-0001"}
    )

    assert response.headers["X-Request-ID"] == "inbound-req-0001"
    assert http_events(audited_url) == [
        ("create", "audited_things", actor, "inbound-req-0001"),
        ("sample.http", None, actor, "inbound-req-0001"),
    ]


def test_a_public_route_records_no_actor_even_after_a_signed_in_request(
    client, audited_url
):
    actor = asyncio.run(insert_user(audited_url))
    sign_in(client, actor)
    assert client.post("/things/uow-first").status_code == 200

    # Same app and worker; the next request is anonymous and public.
    sign_in(client, None)
    response = client.post("/things/public")

    assert response.status_code == 200
    first, second = http_events(audited_url)
    assert first[2] == actor
    assert second == (
        "create",
        "audited_things",
        None,
        response.headers["X-Request-ID"],
    )
    assert first[3] != second[3]


def test_a_denied_request_writes_nothing(client, audited_url):
    sign_in(client, None)

    assert client.post("/things/require-first").status_code == 401
    assert http_events(audited_url) == []
