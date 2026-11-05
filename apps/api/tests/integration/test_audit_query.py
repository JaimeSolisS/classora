"""``GET /api/v1/audit-events`` (M2-04 F) and the #120 names and record types.

The real app on a migrated, seeded Testcontainers database; principals come
from real users through the #21 harness (``principal_for``).
"""

import asyncio
import base64
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.audit import record_audit_event
from app.core.authz import Principal, get_current_principal
from app.core.config import get_settings
from app.core.db import create_sessionmaker
from app.core.errors import PROBLEM_JSON
from app.core.money import Money, Rate
from app.core.pagination import CursorParams
from app.core.uow import UnitOfWork
from app.main import build_app, lifespan
from app.modules.administration import service as administration
from app.modules.finance.models import Expense
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    ROLE_PERMISSIONS,
    TUTOR,
)
from app.modules.identity.service import get_account_references
from app.modules.payroll.models import CompensationRate
from tests.authz.harness import Actor, principal_for
from tests.factories.identity import factory_password_hash
from tests.integration.auth_support import CreatedUser, create_user
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.expense_support import category_of
from tests.integration.lifecycle_support import in_uow

PATH = "/api/v1/audit-events"
RECORD_TYPES = "/api/v1/audit-events/record-types"


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
    # The oldest events are the reference seed's education levels (#32 B2).
    [item] = [i for i in body["items"] if i["record_type"] == "users"]
    assert set(item) == {
        "id",
        "occurred_at",
        "actor_id",
        "actor",
        "action",
        "record_type",
        "record_id",
        "changes",
        "details",
        "request_id",
        "accounts",
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
    assert client.get(RECORD_TYPES).status_code == 200


@pytest.mark.parametrize("role", [TUTOR, FINANCE_STAFF])
@pytest.mark.parametrize("path", [PATH, RECORD_TYPES])
def test_other_roles_are_denied(client, seeded_url, role, path):
    sign_in(client, run(_principal(seeded_url, role)))

    body = assert_problem(client.get(path), 403, "permission_denied")
    assert "audit" not in body.get("detail", "")


def test_anonymous_gets_401_before_validation(client):
    app = client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides.pop(get_current_principal)

    assert_problem(
        client.get(PATH, params={"limit": 0}), 401, "authentication_required"
    )
    assert_problem(client.get(RECORD_TYPES), 401, "authentication_required")


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


# --- #120 B4: record types ------------------------------------------------------


def test_record_types_are_the_audited_tables_sorted(client):
    response = client.get(RECORD_TYPES)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items"}
    items = body["items"]
    assert items == sorted(items)
    assert len(items) == len(set(items))
    assert {"users", "approval_requests", "compensation_rates", "expenses"} <= set(
        items
    )
    assert not {"roles", "notifications", "audit_events"} & set(items)


# --- #120 B1-B3: names ------------------------------------------------------------


def display_name(url: str, user_id: Any) -> str:
    [row] = run_sql(
        url, "SELECT display_name FROM users WHERE id = :id", {"id": user_id}
    )
    name: str = row.display_name
    return name


def account(url: str, user_id: Any) -> dict[str, str]:
    return {"id": str(user_id), "display_name": display_name(url, user_id)}


def test_the_actor_is_named_and_null_without_one(client, seeded_url, admin):
    write(seeded_url, ["sample.by_admin"], actor=admin.user_id)
    write(seeded_url, ["sample.by_nobody"])

    items = {
        i["action"]: i
        for i in get_all(client)
        if i["action"]
        in {
            "sample.by_admin",
            "sample.by_nobody",
        }
    }

    assert items["sample.by_admin"]["actor"] == account(seeded_url, admin.user_id)
    assert items["sample.by_admin"]["actor_id"] == str(admin.user_id)
    assert items["sample.by_nobody"]["actor"] is None
    assert items["sample.by_nobody"]["actor_id"] is None
    # Named events' details are never resolved (B2).
    assert items["sample.by_admin"]["accounts"] == []


def test_an_unknown_id_is_left_out(client, seeded_url, admin):
    # The actor column is a foreign key, so an unknown actor only happens when
    # the resolver does not know the id: the item keeps the id, no name.
    write(seeded_url, ["sample.by_admin"], actor=admin.user_id)

    async def nobody(session: Any, user_ids: Any) -> dict[Any, Any]:
        return {}

    administration.register_account_reference_resolver(nobody)
    try:
        items = get_all(client, actor_id=str(admin.user_id))
        users = get_all(client, record_type="users", record_id=str(admin.user_id))
    finally:
        administration.register_account_reference_resolver(get_account_references)

    [item] = items
    assert item["actor_id"] == str(admin.user_id)
    assert item["actor"] is None
    assert users
    assert all(i["accounts"] == [] for i in users)


def test_a_missing_resolver_fails_closed(client, seeded_url):
    administration.register_account_reference_resolver(None)
    try:
        response = client.get(PATH)
    finally:
        administration.register_account_reference_resolver(get_account_references)

    assert response.status_code == 500


def test_a_users_event_names_its_record(client, seeded_url, admin):
    items = get_all(client, record_type="users", record_id=str(admin.user_id))

    assert items
    for item in items:
        assert item["accounts"] == [account(seeded_url, admin.user_id)]


def owner_principal(user: CreatedUser) -> Principal:
    return Principal(
        user_id=user.id,
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )


def new_approval(url: str, requester: CreatedUser) -> uuid.UUID:
    async def _create(session: Any) -> uuid.UUID:
        request = await administration.create_approval_request(
            session,
            request_type="identity.registration",
            subject_id=uuid.uuid7(),
            requester_user_id=requester.id,
        )
        return request.id

    return in_uow(url, _create)


def assign_to(
    url: str, by: CreatedUser, request_id: uuid.UUID, to: Any, version: int
) -> None:
    async def _assign(session: Any) -> None:
        await administration.assign_approval_request(
            session,
            principal=owner_principal(by),
            request_id=request_id,
            assignee_user_id=to,
            version=version,
        )

    in_uow(url, _assign, actor_id=by.id)


def test_an_assignment_names_old_and_new_assignee(client, seeded_url):
    url = seeded_url
    owner = create_user(url, roles=(OWNER,))
    first = create_user(url, roles=(OWNER,))
    second = create_user(url, roles=(ADMINISTRATOR,))
    request_id = new_approval(url, owner)

    assign_to(url, owner, request_id, first.id, 1)
    assign_to(url, owner, request_id, second.id, 2)
    assign_to(url, owner, request_id, None, 3)

    items = get_all(client, record_type="approval_requests", record_id=str(request_id))
    updates = [i for i in items if i["action"] == "update"]
    assert [i["changes"]["assignee_user_id"] for i in updates] == [
        {"old": str(second.id), "new": None},
        {"old": str(first.id), "new": str(second.id)},
        {"old": None, "new": str(first.id)},
    ]
    by_id = lambda *users: sorted(  # noqa: E731
        (account(url, user.id) for user in users), key=lambda a: a["id"]
    )
    assert [i["accounts"] for i in updates] == [
        by_id(second),
        by_id(first, second),
        by_id(first),
    ]
    assert all(i["actor"] == account(url, owner.id) for i in updates)
    [created] = [i for i in items if i["action"] == "create"]
    assert created["accounts"] == [account(url, owner.id)]


def masked_records(url: str) -> tuple[uuid.UUID, uuid.UUID, CreatedUser, CreatedUser]:
    """A compensation rate and an expense (masked: ``rate``; ``amount`` and
    ``description``), written through the ORM by a signed-in Owner."""
    owner = create_user(url, roles=(OWNER,))
    tutor = create_user(url, roles=(TUTOR,))
    category = uuid.UUID(category_of(url))

    async def _write(session: Any) -> tuple[uuid.UUID, uuid.UUID]:
        rate = CompensationRate(
            id=uuid.uuid7(),
            tutor_user_id=tutor.id,
            class_kind="individual",
            effective_from=date(2026, 1, 1),
            rate_model="per_hour",
            rate=Rate("8246.1937"),
        )
        expense = Expense(
            id=uuid.uuid7(),
            category_id=category,
            description="Secret rent 73951",
            amount=Money("73951.37"),
            incurred_on=date(2026, 1, 1),
            due_on=date(2026, 1, 1),
            created_by_user_id=owner.id,
        )
        session.add_all([rate, expense])
        await session.flush()
        return rate.id, expense.id

    rate_id, expense_id = in_uow(url, _write, actor_id=owner.id)
    return rate_id, expense_id, owner, tutor


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR])
def test_masked_values_stay_masked_for_every_reader(client, seeded_url, role):
    url = seeded_url
    rate_id, expense_id, owner, tutor = masked_records(url)
    sign_in(client, run(_principal(url, role)))

    for record_type, record_id, masked, user_column, user in (
        ("compensation_rates", rate_id, ("rate",), "tutor_user_id", tutor),
        (
            "expenses",
            expense_id,
            ("amount", "description"),
            "created_by_user_id",
            owner,
        ),
    ):
        response = client.get(
            PATH, params={"record_type": record_type, "record_id": str(record_id)}
        )
        assert response.status_code == 200, response.text
        for secret in ("8246.1937", "73951.37", "Secret rent"):
            assert secret not in response.text
        [item] = response.json()["items"]
        for column in masked:
            # Masked on both sides, even the create's empty "old".
            assert item["changes"][column] == {"old": "[masked]", "new": "[masked]"}
        assert item["changes"][user_column]["new"] == str(user.id)
        # Only the user-reference column is named, never a masked one.
        assert item["accounts"] == [account(url, user.id)]
        assert item["actor"] == account(url, owner.id)


def test_a_page_takes_one_event_select_and_one_resolver_statement(
    client, seeded_url, admin
):
    users = [create_user(seeded_url, roles=(OWNER,)) for _ in range(3)]
    for user in users:
        write(seeded_url, [f"sample.event_{c}" for c in "abcdefghij"], actor=user.id)

    def count(limit: int) -> int:
        statements, page = count_statements(
            seeded_url,
            lambda session: administration.list_audit_events(
                session,
                administration.AuditEventFilters(),
                CursorParams(limit=limit, cursor=None),
            ),
        )
        assert len(page.items) == limit
        return statements

    one, many = count(1), count(25)
    assert one == many == 2


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
