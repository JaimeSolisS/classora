"""``GET /api/v1/audit-events`` (M2-04 F).

The real app on a migrated, seeded Testcontainers database; principals come
from real users through the #21 harness (``principal_for``).
"""

import asyncio
import base64
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.audit import record_audit_event
from app.core.authz import Principal, get_current_principal
from app.core.config import get_settings
from app.core.db import create_sessionmaker
from app.core.errors import PROBLEM_JSON
from app.core.uow import UnitOfWork
from app.main import build_app, lifespan
from app.modules.identity.permissions import ADMINISTRATOR, OWNER, TUTOR
from tests.authz.harness import Actor, principal_for
from tests.factories.identity import factory_password_hash
from tests.integration.conftest import run_sql, settings_for, short_lived_engine

PATH = "/api/v1/audit-events"


def run[T](coroutine: Any) -> T:
    return asyncio.run(coroutine)


async def _principal(url: str, role: str) -> Principal:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await principal_for(uow.session, Actor(role))
    finally:
        await engine.dispose()


async def _write(
    url: str, actions: list[str], *, actor: uuid.UUID | None = None, **record: Any
) -> None:
    """One transaction with one explicit event per action."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine), actor_id=actor) as uow:
            for action in actions:
                record_audit_event(uow.session, action=action, **record)
    finally:
        await engine.dispose()


def write(url: str, actions: list[str], **kwargs: Any) -> None:
    asyncio.run(_write(url, actions, **kwargs))


@pytest.fixture
def admin(seeded_url: str) -> Principal:
    return run(_principal(seeded_url, ADMINISTRATOR))


@pytest.fixture
def client(seeded_url: str, admin: Principal) -> Iterator[TestClient]:
    app = build_app(lifespan=lifespan)
    app.dependency_overrides[get_settings] = lambda: settings_for(seeded_url)
    app.dependency_overrides[get_current_principal] = lambda: admin
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def sign_in(client: TestClient, principal: Principal) -> None:
    app = client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[get_current_principal] = lambda: principal


def stored_order(url: str, where: str = "true", **params: Any) -> list[str]:
    """Event ids newest first, straight from the database."""
    rows = run_sql(
        url,
        f"SELECT id FROM audit_events WHERE {where}"  # noqa: S608 (test constants)
        " ORDER BY occurred_at DESC, id DESC",
        params,
    )
    return [str(row[0]) for row in rows]


def get_all(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        query = params | ({"cursor": cursor} if cursor else {})
        response = client.get(PATH, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= params.get("limit", 25)
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return items


def assert_problem(response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    body: dict[str, Any] = response.json()
    assert body["code"] == code
    return body


# --- Access -------------------------------------------------------------------


def test_an_administrator_reads_the_whole_history(client, seeded_url, admin):
    response = client.get(PATH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    # The administrator's own user and role assignment (created by the harness).
    assert [item["id"] for item in body["items"]] == stored_order(seeded_url)
    item = body["items"][-1]
    assert set(item) == {
        "id",
        "occurred_at",
        "actor_id",
        "action",
        "record_type",
        "record_id",
        "changes",
        "details",
        "request_id",
    }
    assert item["action"] == "create"
    assert item["record_type"] == "users"
    assert item["record_id"] == str(admin.user_id)
    assert item["changes"]["state"] == {"old": None, "new": "active"}
    assert body["next_cursor"] is None


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR])
def test_audit_view_holders_are_allowed(client, seeded_url, role):
    sign_in(client, run(_principal(seeded_url, role)))

    assert client.get(PATH).status_code == 200


def test_a_tutor_is_denied(client, seeded_url):
    sign_in(client, run(_principal(seeded_url, TUTOR)))

    body = assert_problem(client.get(PATH), 403, "permission_denied")
    assert "audit" not in body.get("detail", "")


def test_anonymous_gets_401_before_validation(client):
    app = client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides.pop(get_current_principal)

    assert_problem(
        client.get(PATH, params={"limit": 0}), 401, "authentication_required"
    )


def test_the_password_hash_never_appears(client, seeded_url):
    run(_principal(seeded_url, TUTOR))  # another user with the factory hash

    text = client.get(PATH, params={"limit": 100}).text

    assert "password_hash" not in text
    assert factory_password_hash() not in text
    assert "argon2" not in text


# --- Filters ------------------------------------------------------------------


def test_filter_by_record(client, seeded_url):
    record = uuid.uuid7()
    write(
        seeded_url, ["sample.one", "sample.two"], record_type="users", record_id=record
    )
    write(seeded_url, ["sample.other"], record_type="users", record_id=uuid.uuid7())
    write(seeded_url, ["sample.other"], record_type="user_roles", record_id=record)

    items = get_all(client, record_type="users", record_id=str(record))

    assert [i["action"] for i in items] == ["sample.two", "sample.one"]
    assert {(i["record_type"], i["record_id"]) for i in items} == {
        ("users", str(record))
    }


def test_filter_by_actor(client, seeded_url, admin):
    write(seeded_url, ["sample.by_admin"], actor=admin.user_id)
    write(seeded_url, ["sample.by_nobody"])

    items = get_all(client, actor_id=str(admin.user_id))

    assert [i["action"] for i in items] == ["sample.by_admin"]
    assert items[0]["actor_id"] == str(admin.user_id)


def event_times(url: str, action: str) -> datetime:
    [row] = run_sql(
        url,
        "SELECT DISTINCT occurred_at FROM audit_events WHERE action = :a",
        {"a": action},
    )
    when: datetime = row[0]
    return when


def test_time_range_includes_from_and_excludes_to(client, seeded_url):
    for action in ("sample.first", "sample.second", "sample.third"):
        write(seeded_url, [action])
    second = event_times(seeded_url, "sample.second")
    third = event_times(seeded_url, "sample.third")

    items = get_all(
        client, occurred_from=second.isoformat(), occurred_to=third.isoformat()
    )

    assert [i["action"] for i in items] == ["sample.second"]
    after = get_all(client, occurred_from=third.isoformat())
    assert [i["action"] for i in after] == ["sample.third"]
    before = get_all(client, occurred_to=second.isoformat())
    assert "sample.second" not in {i["action"] for i in before}
    assert "sample.first" in {i["action"] for i in before}


def test_combined_filters(client, seeded_url, admin):
    record = uuid.uuid7()
    write(
        seeded_url,
        ["sample.early"],
        actor=admin.user_id,
        record_type="users",
        record_id=record,
    )
    write(
        seeded_url,
        ["sample.match"],
        actor=admin.user_id,
        record_type="users",
        record_id=record,
    )
    write(seeded_url, ["sample.no_actor"], record_type="users", record_id=record)
    start = event_times(seeded_url, "sample.match")

    items = get_all(
        client,
        record_type="users",
        record_id=str(record),
        actor_id=str(admin.user_id),
        occurred_from=start.isoformat(),
        occurred_to=(start + timedelta(days=1)).isoformat(),
    )

    assert [i["action"] for i in items] == ["sample.match"]


def test_unknown_record_or_actor_gives_an_empty_page(client):
    for params in (
        {"record_type": "users", "record_id": str(uuid.uuid7())},
        {"actor_id": str(uuid.uuid7())},
        {"record_type": "no_such_table", "record_id": str(uuid.uuid7())},
    ):
        response = client.get(PATH, params=params)
        assert response.status_code == 200
        assert response.json() == {"items": [], "next_cursor": None}


# --- Paging -------------------------------------------------------------------


def test_paging_through_one_transaction_has_no_duplicates_or_gaps(client, seeded_url):
    write(seeded_url, [f"sample.event_{c}" for c in "abcdefghijk"])
    expected = stored_order(seeded_url)
    assert len(expected) > 11

    items = get_all(client, limit=3)

    assert [i["id"] for i in items] == expected
    assert len({i["id"] for i in items}) == len(items)
    # Newest first; inside one transaction (one occurred_at), id descending.
    batch = [i for i in items if i["action"].startswith("sample.event_")]
    assert len({i["occurred_at"] for i in batch}) == 1
    assert [i["action"] for i in batch] == [f"sample.event_{c}" for c in "kjihgfedcba"]
    # The batch crosses page boundaries (limit 3) at one shared occurred_at.
    assert len(batch) > 3


def test_new_events_do_not_shift_later_pages(client, seeded_url):
    write(seeded_url, [f"sample.event_{c}" for c in "abcdefgh"])
    expected = stored_order(seeded_url)

    first = client.get(PATH, params={"limit": 4}).json()
    write(seeded_url, ["sample.late_one", "sample.late_two"])
    rest = get_all(client, limit=4, cursor=first["next_cursor"])

    assert [i["id"] for i in first["items"]] == expected[:4]
    assert [i["id"] for i in rest] == expected[4:]


# --- Invalid input ------------------------------------------------------------


def b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "not a cursor!",
        b64("[1, 2]"),
        b64('["2026-10-18T00:00:00+00:00"]'),
        b64('["yesterday", "0199a000-0000-7000-8000-000000000001"]'),
        b64('["2026-10-18T00:00:00+00:00", "not-a-uuid"]'),
        b64('["2026-10-18T00:00:00", "0199a000-0000-7000-8000-000000000001"]'),
        "x" * 2000,
    ],
)
def test_an_invalid_cursor_is_400(client, cursor):
    body = assert_problem(
        client.get(PATH, params={"cursor": cursor}), 400, "invalid_cursor"
    )
    assert cursor not in str(body)


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"record_type": "users"}, "record_id"),
        ({"record_id": "0199a000-0000-7000-8000-000000000001"}, "record_type"),
        (
            {
                "record_type": "Users",
                "record_id": "0199a000-0000-7000-8000-000000000001",
            },
            "record_type",
        ),
        (
            {"record_type": "a-b", "record_id": "0199a000-0000-7000-8000-000000000001"},
            "record_type",
        ),
        (
            {
                "record_type": "x" * 64,
                "record_id": "0199a000-0000-7000-8000-000000000001",
            },
            "record_type",
        ),
        ({"record_id": "nope", "record_type": "users"}, "record_id"),
        ({"actor_id": "nope"}, "actor_id"),
        ({"occurred_from": "2026-10-18T10:00:00"}, "occurred_from"),
        ({"occurred_to": "2026-10-18"}, "occurred_to"),
        (
            {
                "occurred_from": "2026-10-18T10:00:00+00:00",
                "occurred_to": "2026-10-18T10:00:00+00:00",
            },
            "occurred_to",
        ),
        (
            {
                "occurred_from": "2026-10-18T11:00:00+00:00",
                "occurred_to": "2026-10-18T10:00:00+00:00",
            },
            "occurred_to",
        ),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
    ],
)
def test_invalid_filters_are_422(client, params, field):
    body = assert_problem(client.get(PATH, params=params), 422, "validation_error")

    assert ["query", field] in [error["path"] for error in body["errors"]]
