"""Notifications through the real app (#41 B3, B4, C1-C5, D1, D2).

Notifications are created with ``administration.service.create_notification``
in a committed (or rolled-back) Unit of Work, as a later module will, or
through the dev route ``POST /api/v1/dev/notifications``, which this module
mounts on its client together with the ``dev.demo`` type.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.administration.notifications_dev import (
    DEV_DEMO_TYPE,
    register_dev_notification_type,
)
from app.modules.administration.notifications_dev import (
    router as dev_notifications_router,
)
from app.modules.identity.permissions import OWNER, ROLE_CODES
from tests.authz.assertions import assert_same_not_found
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow, signed_in

NOTIFICATIONS = "/api/v1/notifications"
UNREAD_COUNT = f"{NOTIFICATIONS}/unread-count"
READ_ALL = f"{NOTIFICATIONS}/read-all"
DEV = "/api/v1/dev/notifications"
OK = (200, None)
NOT_FOUND = (404, "not_found")


class Deliberate(Exception):
    pass


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    register_dev_notification_type()
    with auth_client(module_seeded_url, routers=[dev_notifications_router]) as c:
        yield c


@pytest.fixture
def user(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def other(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=("tutor",))


def notify(url: str, recipient: CreatedUser, label: str = "Hello") -> uuid.UUID:
    register_dev_notification_type()
    return in_uow(
        url,
        lambda session: administration.create_notification(
            session,
            recipient_user_id=recipient.id,
            notification_type=DEV_DEMO_TYPE,
            params={"label": label},
        ),
    )


def get(client: TestClient, cookie: SetCookie, path: str, **params: Any) -> Any:
    client.cookies.clear()
    return client.get(path, headers=cookie_header(cookie), params=params)


def post(client: TestClient, cookie: SetCookie, path: str, body: Any = None) -> Any:
    client.cookies.clear()
    return client.post(path, headers=signed_headers(cookie), json=body)


def read(client: TestClient, cookie: SetCookie, notification_id: Any) -> Any:
    return post(client, cookie, f"{NOTIFICATIONS}/{notification_id}/read")


def listed(client: TestClient, cookie: SetCookie, **params: Any) -> list[str]:
    response = get(client, cookie, NOTIFICATIONS, **params)
    assert code_of(response) == OK, response.text
    return [item["id"] for item in response.json()["items"]]


def unread(client: TestClient, cookie: SetCookie) -> int:
    response = get(client, cookie, UNREAD_COUNT)
    assert code_of(response) == OK, response.text
    return int(response.json()["count"])


def stored(url: str, notification_id: Any) -> Any:
    rows = run_sql(
        url,
        "SELECT recipient_user_id, notification_type, params, read_at"
        " FROM notifications WHERE id = :id",
        {"id": notification_id},
    )
    return rows[0] if rows else None


def notifications_of(url: str, user: CreatedUser) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM notifications WHERE recipient_user_id = :id",
        {"id": user.id},
    )
    return int(row.n)


# --- B3, B4: created inside the caller's transaction ---------------------------------


def test_a_created_notification_is_visible_after_the_commit(
    module_seeded_url, client, user
):
    notification_id = notify(module_seeded_url, user, "Committed")
    cookie = signed_in(client, user.username)

    response = get(client, cookie, NOTIFICATIONS)

    assert code_of(response) == OK
    [item] = response.json()["items"]
    assert item["id"] == str(notification_id)
    assert item["notification_type"] == "dev.demo"
    assert item["params"] == {"label": "Committed"}
    assert item["read_at"] is None
    assert set(item) == {"id", "notification_type", "params", "created_at", "read_at"}
    assert response.json()["next_cursor"] is None
    # Not audited (A1): per-user messages.
    assert events(module_seeded_url, record_id=notification_id) == []


def test_a_rolled_back_transaction_leaves_no_notification(module_seeded_url, user):
    register_dev_notification_type()
    created: list[uuid.UUID] = []

    async def _create_then_fail(session: AsyncSession) -> None:
        created.append(
            await administration.create_notification(
                session,
                recipient_user_id=user.id,
                notification_type=DEV_DEMO_TYPE,
                params={"label": "Rolled back"},
            )
        )
        # Flushed inside the transaction: visible to it.
        assert (
            await administration.unread_notification_count(
                session, recipient_user_id=user.id
            )
            == 1
        )
        raise Deliberate()

    with pytest.raises(Deliberate):
        in_uow(module_seeded_url, _create_then_fail)

    assert len(created) == 1
    assert stored(module_seeded_url, created[0]) is None
    assert notifications_of(module_seeded_url, user) == 0


class Secretive(BaseModel):
    api_token: str


@pytest.mark.parametrize(
    ("notification_type", "params"),
    [
        ("dev.demo", {"label": ""}),
        ("dev.demo", {"label": "x", "extra": 1}),
        ("dev.demo", {}),
        ("test.never_registered", {}),
        ("test.secretive", {"api_token": "abc"}),
    ],
)
def test_invalid_params_an_unknown_type_and_a_sensitive_key_are_refused(
    module_seeded_url, user, notification_type, params
):
    register_dev_notification_type()
    administration.register_notification_type("test.secretive", Secretive)
    try:
        with pytest.raises(ValueError):
            in_uow(
                module_seeded_url,
                lambda session: administration.create_notification(
                    session,
                    recipient_user_id=user.id,
                    notification_type=notification_type,
                    params=params,
                ),
            )
    finally:
        administration.unregister_notification_type("test.secretive")

    assert notifications_of(module_seeded_url, user) == 0


def test_a_recipient_in_any_state_can_be_notified(module_seeded_url):
    for state in ("pending", "suspended", "archived"):
        recipient = create_user(module_seeded_url, roles=("guardian",), state=state)
        notification_id = notify(module_seeded_url, recipient)
        assert stored(module_seeded_url, notification_id).recipient_user_id == (
            recipient.id
        )


# --- C1: list and pagination ----------------------------------------------------------


def test_pagination_across_three_pages_with_equal_created_at(
    module_seeded_url, client, user
):
    ids = [notify(module_seeded_url, user, f"N{index}") for index in range(7)]
    same = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    run_sql(
        module_seeded_url,
        "UPDATE notifications SET created_at = :at WHERE recipient_user_id = :id",
        {"at": same, "id": user.id},
    )
    cookie = signed_in(client, user.username)
    expected = sorted((str(i) for i in ids), reverse=True)  # id DESC tiebreak

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        response = get(client, cookie, NOTIFICATIONS, **params)
        assert code_of(response) == OK
        body = response.json()
        seen.extend(item["id"] for item in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert seen == expected


def test_newest_first_and_unread_only(module_seeded_url, client, user):
    first = notify(module_seeded_url, user, "First")
    second = notify(module_seeded_url, user, "Second")
    third = notify(module_seeded_url, user, "Third")
    cookie = signed_in(client, user.username)
    assert code_of(read(client, cookie, second)) == OK

    assert listed(client, cookie) == [str(third), str(second), str(first)]
    assert listed(client, cookie, unread_only=True) == [str(third), str(first)]
    assert unread(client, cookie) == 2


@pytest.mark.parametrize(
    "cursor",
    ["not-base64!", "WzFd", "WyJ4IiwieSJd", "WyIyMDI2LTEwLTIwVDEyOjAwOjAwIiwieCJd"],
)
def test_an_invalid_cursor_is_400(client, user, cursor):
    cookie = signed_in(client, user.username)

    response = get(client, cookie, NOTIFICATIONS, cursor=cursor)

    assert code_of(response) == (400, "invalid_cursor")


def test_the_unread_count_is_one_statement(module_seeded_url, user):
    notify(module_seeded_url, user)

    statements, count = count_statements(
        module_seeded_url,
        lambda session: administration.unread_notification_count(
            session, recipient_user_id=user.id
        ),
    )

    assert (statements, count) == (1, 1)


# --- C3: read -------------------------------------------------------------------------


def test_read_is_idempotent_and_keeps_the_first_read_at(
    module_seeded_url, client, user
):
    notification_id = notify(module_seeded_url, user)
    cookie = signed_in(client, user.username)

    first = read(client, cookie, notification_id)
    second = read(client, cookie, notification_id)

    assert code_of(first) == OK
    assert first.json()["id"] == str(notification_id)
    assert first.json()["read_at"] is not None
    assert code_of(second) == OK
    assert second.json()["read_at"] == first.json()["read_at"]
    assert unread(client, cookie) == 0


# --- C5: isolation --------------------------------------------------------------------


def test_a_user_never_sees_counts_reads_or_bulk_reads_another_users_notifications(
    module_seeded_url, client, user, other
):
    theirs = notify(module_seeded_url, other, "Theirs")
    mine = notify(module_seeded_url, user, "Mine")
    cookie = signed_in(client, user.username)

    assert listed(client, cookie) == [str(mine)]
    assert listed(client, cookie, unread_only=True) == [str(mine)]
    assert unread(client, cookie) == 1
    assert_same_not_found(
        read(client, cookie, theirs), read(client, cookie, uuid.uuid4())
    )
    assert_same_not_found(
        post(client, cookie, READ_ALL, {"up_to": str(theirs)}),
        post(client, cookie, READ_ALL, {"up_to": str(uuid.uuid4())}),
    )
    # Mark all (no up_to) touches only the caller's own.
    assert post(client, cookie, READ_ALL, {"up_to": None}).json() == {"updated": 1}

    assert stored(module_seeded_url, theirs).read_at is None
    other_cookie = signed_in(client, other.username)
    assert unread(client, other_cookie) == 1
    assert listed(client, other_cookie) == [str(theirs)]


# --- C4: read-all ---------------------------------------------------------------------


def test_read_all_up_to_leaves_newer_notifications_unread(
    module_seeded_url, client, user
):
    older = notify(module_seeded_url, user, "Older")
    seen = notify(module_seeded_url, user, "Seen")
    cookie = signed_in(client, user.username)
    # Arrives while the panel is open.
    newer = notify(module_seeded_url, user, "Newer")

    response = post(client, cookie, READ_ALL, {"up_to": str(seen)})

    assert code_of(response) == OK
    assert response.json() == {"updated": 2}
    assert stored(module_seeded_url, older).read_at is not None
    assert stored(module_seeded_url, seen).read_at is not None
    assert stored(module_seeded_url, newer).read_at is None
    assert listed(client, cookie, unread_only=True) == [str(newer)]


def test_read_all_without_up_to_marks_every_unread_one(module_seeded_url, client, user):
    for index in range(3):
        notify(module_seeded_url, user, f"N{index}")
    cookie = signed_in(client, user.username)

    assert post(client, cookie, READ_ALL, {"up_to": None}).json() == {"updated": 3}
    assert post(client, cookie, READ_ALL).json() == {"updated": 0}
    assert unread(client, cookie) == 0


def test_read_all_keeps_the_read_at_of_already_read_ones(
    module_seeded_url, client, user
):
    early = notify(module_seeded_url, user)
    late = notify(module_seeded_url, user)
    cookie = signed_in(client, user.username)
    first_read = read(client, cookie, early).json()["read_at"]

    assert post(client, cookie, READ_ALL, {"up_to": str(late)}).json() == {"updated": 1}
    assert get(client, cookie, NOTIFICATIONS).json()["items"][1]["read_at"] == (
        first_read
    )


def test_read_all_rejects_unknown_fields(client, user):
    cookie = signed_in(client, user.username)

    response = post(client, cookie, READ_ALL, {"up_to": None, "all": True})

    assert code_of(response) == (422, "validation_error")


# --- C5: every role, D1: the dev route ---------------------------------------------


@pytest.mark.parametrize("role", ROLE_CODES)
def test_every_role_uses_the_four_routes_and_the_dev_route(
    module_seeded_url, client, role
):
    member = create_user(module_seeded_url, roles=(role,))
    cookie = signed_in(client, member.username)

    created = post(client, cookie, DEV, {"label": f"Role {role}"})

    assert created.status_code == 201, created.text
    item = created.json()
    assert (item["notification_type"], item["params"], item["read_at"]) == (
        "dev.demo",
        {"label": f"Role {role}"},
        None,
    )
    assert stored(module_seeded_url, item["id"]).recipient_user_id == member.id
    assert listed(client, cookie) == [item["id"]]
    assert unread(client, cookie) == 1
    assert code_of(read(client, cookie, item["id"])) == OK
    assert post(client, cookie, READ_ALL, {"up_to": item["id"]}).json() == {
        "updated": 0
    }
    assert unread(client, cookie) == 0


@pytest.mark.parametrize(
    "body", [{}, {"label": ""}, {"label": "x" * 101}, {"label": "x", "to": "y"}]
)
def test_the_dev_route_validates_the_label(module_seeded_url, client, user, body):
    cookie = signed_in(client, user.username)

    assert code_of(post(client, cookie, DEV, body)) == (422, "validation_error")
    assert notifications_of(module_seeded_url, user) == 0


def test_signed_out_callers_get_401(client):
    client.cookies.clear()

    for response in (
        client.get(NOTIFICATIONS),
        client.get(UNREAD_COUNT),
        client.post(READ_ALL, json={}),
        client.post(f"{NOTIFICATIONS}/{uuid.uuid4()}/read"),
        client.post(DEV, json={"label": "x"}),
    ):
        assert response.status_code == 401, response.text


# --- D2: without the dev routes -------------------------------------------------------


def test_without_dev_routes_the_dev_route_does_not_exist(module_seeded_url, user):
    with auth_client(module_seeded_url) as plain:
        cookie = signed_in(plain, user.username)
        plain.cookies.clear()
        response = plain.post(DEV, headers=signed_headers(cookie), json={"label": "x"})

    assert response.status_code == 404
    assert notifications_of(module_seeded_url, user) == 0
