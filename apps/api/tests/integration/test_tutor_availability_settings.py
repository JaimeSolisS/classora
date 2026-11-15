"""Tutor teaching limits, required break and preferred classrooms (#197 B)
with real logins through the real app: the settings ``PATCH``, the detail's
``limits`` and ``preferred_classrooms``, the edit rights, the version shared
with the weekly windows, the ``staff.service`` readers and the audit
trail."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    OWNER,
    TUTOR,
)
from app.modules.staff import service
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import (
    classroom_path,
    create_classroom,
    get,
    patch,
    unique_name,
    window,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

TUTORS = "/api/v1/tutors"

OK = (200, None)
DENIED = (403, "permission_denied")
NOT_FOUND = (404, "not_found")
CONFLICT = (409, "version_conflict")
INVALID = (422, "validation_error")

NO_LIMITS = {
    "daily_limit_minutes": None,
    "weekly_limit_minutes": None,
    "required_break_minutes": None,
}


def availability_path(user_id: Any) -> str:
    return f"{TUTORS}/{user_id}/availability"


def settings_path(user_id: Any) -> str:
    return f"{availability_path(user_id)}/settings"


def patch_settings(
    client: TestClient, cookie: Any, user_id: Any, version: int, **body: Any
) -> Response:
    return patch(client, cookie, settings_path(user_id), {"version": version, **body})


def detail(client: TestClient, cookie: Any, user_id: Any) -> dict[str, Any]:
    response = get(client, cookie, availability_path(user_id))
    assert code_of(response) == OK, response.text
    body: dict[str, Any] = response.json()
    return body


def field_errors(response: Response) -> list[tuple[list[Any], str]]:
    return [(e["path"], e["code"]) for e in response.json()["errors"]]


def room_ref(room: dict[str, Any], *, is_active: bool = True) -> dict[str, Any]:
    return {"id": room["id"], "name": room["name"], "is_active": is_active}


def settings_row(url: str, user_id: Any) -> Any:
    rows = run_sql(
        url,
        "SELECT version, daily_limit_minutes AS daily, weekly_limit_minutes AS"
        " weekly, required_break_minutes AS pause"
        " FROM tutor_availability_settings WHERE user_id = :id",
        {"id": user_id},
    )
    return rows[0] if rows else None


def preferred_count(url: str, user_id: Any) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM tutor_preferred_classrooms"
        " WHERE tutor_user_id = :id",
        {"id": user_id},
    )
    return int(row.n)


def event_count(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE record_type IN"
        " ('tutor_availability_settings', 'tutor_preferred_classrooms')",
    )
    return int(row.n)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(scope="module")
def admin(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ADMINISTRATOR,))


@pytest.fixture(scope="module")
def coordinator(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))


@pytest.fixture(scope="module")
def owner_cookie(client: TestClient, owner: CreatedUser) -> Any:
    return sign_in(client, owner.username)


@pytest.fixture
def tutor(module_seeded_url: str, client: TestClient) -> tuple[CreatedUser, Any]:
    """A new tutor and their session cookie."""
    person = create_user(module_seeded_url, roles=(TUTOR,))
    return person, sign_in(client, person.username)


def rooms(client: TestClient, owner_cookie: Any, count: int) -> list[dict[str, Any]]:
    return [create_classroom(client, owner_cookie) for _ in range(count)]


def deactivate(client: TestClient, owner_cookie: Any, room: dict[str, Any]) -> None:
    response = patch(
        client,
        owner_cookie,
        classroom_path(room["id"]),
        {"version": room["version"], "is_active": False},
    )
    assert code_of(response) == OK, response.text


# --- B2: limits -----------------------------------------------------------------------


def test_a_tutor_sets_changes_and_clears_each_limit(module_seeded_url, client, tutor):
    person, cookie = tutor

    created = patch_settings(
        client,
        cookie,
        person.id,
        0,
        daily_limit_minutes=240,
        weekly_limit_minutes=1200,
        required_break_minutes=15,
    )
    assert code_of(created) == OK, created.text
    assert created.json()["limits"] == {
        "daily_limit_minutes": 240,
        "weekly_limit_minutes": 1200,
        "required_break_minutes": 15,
    }
    assert created.json()["version"] == 1
    assert detail(client, cookie, person.id) == created.json()

    # Fields not sent stay; a sent one changes.
    changed = patch_settings(client, cookie, person.id, 1, daily_limit_minutes=300)
    assert code_of(changed) == OK, changed.text
    assert changed.json()["limits"] == {
        "daily_limit_minutes": 300,
        "weekly_limit_minutes": 1200,
        "required_break_minutes": 15,
    }
    assert changed.json()["version"] == 2

    # null clears each limit.
    cleared = patch_settings(
        client,
        cookie,
        person.id,
        2,
        daily_limit_minutes=None,
        weekly_limit_minutes=None,
        required_break_minutes=None,
    )
    assert code_of(cleared) == OK
    assert cleared.json()["limits"] == NO_LIMITS
    assert cleared.json()["version"] == 3
    row = settings_row(module_seeded_url, person.id)
    assert (row.version, row.daily, row.weekly, row.pause) == (3, None, None, None)

    # A zero break is a value, not "no break".
    zero = patch_settings(client, cookie, person.id, 3, required_break_minutes=0)
    assert zero.json()["limits"]["required_break_minutes"] == 0


def test_a_no_op_patch_keeps_the_version_and_writes_nothing(
    module_seeded_url, client, owner_cookie, tutor
):
    person, cookie = tutor
    [room] = rooms(client, owner_cookie, 1)
    first = patch_settings(
        client,
        cookie,
        person.id,
        0,
        daily_limit_minutes=240,
        preferred_classroom_ids=[room["id"]],
    )
    assert code_of(first) == OK, first.text
    before = event_count(module_seeded_url)

    again = patch_settings(
        client,
        cookie,
        person.id,
        1,
        daily_limit_minutes=240,
        preferred_classroom_ids=[room["id"]],
    )
    only_version = patch_settings(client, cookie, person.id, 1)

    assert code_of(again) == OK
    assert again.json()["version"] == 1
    assert only_version.json() == again.json()
    assert event_count(module_seeded_url) == before
    # Clearing what was never set, for a tutor without a row: also a no-op.
    other = create_user(module_seeded_url, roles=(TUTOR,))
    empty = patch_settings(
        client,
        sign_in(client, other.username),
        other.id,
        0,
        daily_limit_minutes=None,
        preferred_classroom_ids=[],
    )
    assert code_of(empty) == OK
    assert empty.json()["version"] == 0
    assert settings_row(module_seeded_url, other.id) is None


@pytest.mark.parametrize(
    ("body", "path", "code"),
    [
        ({"daily_limit_minutes": 14}, "daily_limit_minutes", "greater_than_equal"),
        ({"daily_limit_minutes": 1441}, "daily_limit_minutes", "less_than_equal"),
        ({"weekly_limit_minutes": 14}, "weekly_limit_minutes", "greater_than_equal"),
        ({"weekly_limit_minutes": 10081}, "weekly_limit_minutes", "less_than_equal"),
        (
            {"daily_limit_minutes": 300, "weekly_limit_minutes": 240},
            "weekly_limit_minutes",
            "weekly_below_daily",
        ),
        (
            {"required_break_minutes": -1},
            "required_break_minutes",
            "greater_than_equal",
        ),
        ({"required_break_minutes": 241}, "required_break_minutes", "less_than_equal"),
        ({"daily_limit_minutes": "4h"}, "daily_limit_minutes", "int_parsing"),
        ({"preferred_classroom_ids": None}, "preferred_classroom_ids", "value_error"),
        ({"preferred_classroom_ids": ["x"]}, "preferred_classroom_ids", "uuid_parsing"),
        ({"extra": 1}, "extra", "extra_forbidden"),
    ],
)
def test_invalid_bodies_are_422_before_404(client, owner_cookie, body, path, code):
    # 422 comes first, even for an id that is no tutor.
    response = patch_settings(client, owner_cookie, uuid.uuid4(), 0, **body)

    assert code_of(response) == INVALID
    assert [p[1] for p, _ in field_errors(response)] == [path]
    assert field_errors(response)[0][1] == code


def test_a_weekly_limit_below_the_stored_daily_limit_is_422(
    module_seeded_url, client, tutor
):
    person, cookie = tutor
    patch_settings(client, cookie, person.id, 0, daily_limit_minutes=300)

    response = patch_settings(client, cookie, person.id, 1, weekly_limit_minutes=240)

    assert code_of(response) == INVALID
    assert field_errors(response) == [
        (["body", "weekly_limit_minutes"], "weekly_below_daily")
    ]
    assert settings_row(module_seeded_url, person.id).version == 1
    # Raising the daily limit above the stored weekly one is the same error.
    patch_settings(client, cookie, person.id, 1, weekly_limit_minutes=600)
    higher = patch_settings(client, cookie, person.id, 2, daily_limit_minutes=601)
    assert field_errors(higher) == [
        (["body", "weekly_limit_minutes"], "weekly_below_daily")
    ]
    # Equal limits are fine.
    equal = patch_settings(client, cookie, person.id, 2, daily_limit_minutes=600)
    assert code_of(equal) == OK


# --- B2: preferred classrooms ---------------------------------------------------------


def test_preferred_classrooms_are_set_replaced_and_cleared(
    module_seeded_url, client, owner_cookie, tutor
):
    person, cookie = tutor
    first, second, third = rooms(client, owner_cookie, 3)

    created = patch_settings(
        client,
        cookie,
        person.id,
        0,
        preferred_classroom_ids=[second["id"], first["id"]],
    )
    assert code_of(created) == OK, created.text
    by_name = sorted([first, second], key=lambda room: room["name"].casefold())
    assert created.json()["preferred_classrooms"] == [room_ref(r) for r in by_name]
    assert created.json()["limits"] == NO_LIMITS
    assert created.json()["version"] == 1

    replaced = patch_settings(
        client, cookie, person.id, 1, preferred_classroom_ids=[third["id"], first["id"]]
    )
    assert code_of(replaced) == OK
    assert {r["id"] for r in replaced.json()["preferred_classrooms"]} == {
        first["id"],
        third["id"],
    }
    assert replaced.json()["version"] == 2
    assert in_uow(
        module_seeded_url,
        lambda session: service.preferred_classroom_ids(session, person.id),
    ) == (uuid.UUID(first["id"]), uuid.UUID(third["id"]))

    cleared = patch_settings(client, cookie, person.id, 2, preferred_classroom_ids=[])
    assert code_of(cleared) == OK
    assert cleared.json()["preferred_classrooms"] == []
    assert cleared.json()["version"] == 3
    assert preferred_count(module_seeded_url, person.id) == 0


def test_unknown_inactive_too_many_or_repeated_rooms_are_422(
    module_seeded_url, client, owner_cookie, tutor
):
    person, cookie = tutor
    active, inactive = rooms(client, owner_cookie, 2)
    deactivate(client, owner_cookie, inactive)
    eleven = [room["id"] for room in rooms(client, owner_cookie, 11)]

    responses = {
        "inactive": patch_settings(
            client,
            cookie,
            person.id,
            0,
            preferred_classroom_ids=[active["id"], inactive["id"]],
        ),
        "unknown": patch_settings(
            client, cookie, person.id, 0, preferred_classroom_ids=[str(uuid.uuid4())]
        ),
        "eleven": patch_settings(
            client, cookie, person.id, 0, preferred_classroom_ids=eleven
        ),
        "twice": patch_settings(
            client,
            cookie,
            person.id,
            0,
            preferred_classroom_ids=[active["id"], active["id"]],
        ),
    }

    assert {name: field_errors(r) for name, r in responses.items()} == {
        "inactive": [(["body", "preferred_classroom_ids"], "classroom_unavailable")],
        "unknown": [(["body", "preferred_classroom_ids"], "classroom_unavailable")],
        "eleven": [(["body", "preferred_classroom_ids"], "too_long")],
        "twice": [(["body", "preferred_classroom_ids"], "duplicate_classroom")],
    }
    assert settings_row(module_seeded_url, person.id) is None
    assert preferred_count(module_seeded_url, person.id) == 0
    # Ten is the maximum.
    ten = patch_settings(
        client, cookie, person.id, 0, preferred_classroom_ids=eleven[:10]
    )
    assert code_of(ten) == OK
    assert len(ten.json()["preferred_classrooms"]) == 10


def test_a_room_deactivated_later_stays_until_it_is_removed(
    module_seeded_url, client, owner_cookie, tutor
):
    person, cookie = tutor
    kept, other, added = rooms(client, owner_cookie, 3)
    patch_settings(
        client, cookie, person.id, 0, preferred_classroom_ids=[kept["id"], other["id"]]
    )
    deactivate(client, owner_cookie, kept)

    shown = detail(client, cookie, person.id)["preferred_classrooms"]
    assert room_ref(kept, is_active=False) in shown
    # A limit change doesn't touch the rooms; keeping the inactive room while
    # editing the list is allowed (only new choices must be active).
    assert (
        code_of(patch_settings(client, cookie, person.id, 1, daily_limit_minutes=120))
        == OK
    )
    edited = patch_settings(
        client, cookie, person.id, 2, preferred_classroom_ids=[kept["id"], added["id"]]
    )
    assert code_of(edited) == OK, edited.text
    assert {
        (r["id"], r["is_active"]) for r in edited.json()["preferred_classrooms"]
    } == {
        (kept["id"], False),
        (added["id"], True),
    }
    removed = patch_settings(
        client, cookie, person.id, 3, preferred_classroom_ids=[added["id"]]
    )
    assert removed.json()["preferred_classrooms"] == [room_ref(added)]


# --- B2: edit rights and version ------------------------------------------------------


@pytest.mark.parametrize("editor", ["owner", "admin"])
def test_owners_and_administrators_edit_a_tutors_settings(
    module_seeded_url, client, request, editor
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = sign_in(client, request.getfixturevalue(editor).username)

    response = patch_settings(client, cookie, person.id, 0, daily_limit_minutes=180)

    assert code_of(response) == OK
    assert settings_row(module_seeded_url, person.id).daily == 180


def test_a_coordinator_reads_but_may_not_change(
    module_seeded_url, client, coordinator, owner_cookie
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    patch_settings(client, owner_cookie, person.id, 0, daily_limit_minutes=180)
    before = event_count(module_seeded_url)
    cookie = sign_in(client, coordinator.username)

    assert detail(client, cookie, person.id)["limits"]["daily_limit_minutes"] == 180
    response = patch_settings(client, cookie, person.id, 1, daily_limit_minutes=60)

    assert code_of(response) == DENIED
    assert settings_row(module_seeded_url, person.id).daily == 180
    assert event_count(module_seeded_url) == before


def test_another_tutor_gets_404_like_a_missing_one(
    module_seeded_url, client, tutor, owner_cookie
):
    person, cookie = tutor
    other = create_user(module_seeded_url, roles=(TUTOR,))
    patch_settings(client, owner_cookie, other.id, 0, daily_limit_minutes=180)
    not_a_tutor = create_user(module_seeded_url, roles=(ADMINISTRATOR,))

    responses = [
        patch_settings(client, cookie, other.id, 1, daily_limit_minutes=60),
        patch_settings(client, cookie, uuid.uuid4(), 0, daily_limit_minutes=60),
        patch_settings(client, owner_cookie, not_a_tutor.id, 0, daily_limit_minutes=60),
    ]

    assert [code_of(r) for r in responses] == [NOT_FOUND] * 3
    assert settings_row(module_seeded_url, other.id).daily == 180


def test_the_version_is_shared_with_the_weekly_windows(
    module_seeded_url, client, tutor
):
    person, cookie = tutor
    weekly = client.put(
        f"{availability_path(person.id)}/weekly",
        json={"version": 0, "windows": [window(1, "16:00", "20:00")]},
        headers=signed_headers(cookie),
    )
    assert weekly.json()["version"] == 1

    stale = patch_settings(client, cookie, person.id, 0, daily_limit_minutes=60)
    fresh = patch_settings(client, cookie, person.id, 1, daily_limit_minutes=60)

    assert code_of(stale) == CONFLICT
    assert code_of(fresh) == OK
    assert fresh.json()["version"] == 2
    assert fresh.json()["weekly"] == [window(1, "16:00", "20:00")]
    # The weekly PUT now needs the settings' version too.
    client.cookies.clear()
    old = client.put(
        f"{availability_path(person.id)}/weekly",
        json={"version": 1, "windows": []},
        headers=signed_headers(cookie),
    )
    assert code_of(old) == CONFLICT
    # A first PATCH for a tutor with no row needs version 0.
    other = create_user(module_seeded_url, roles=(TUTOR,))
    assert (
        code_of(
            patch_settings(
                client,
                sign_in(client, other.username),
                other.id,
                1,
                daily_limit_minutes=60,
            )
        )
        == CONFLICT
    )


# --- B3: staff.service readers --------------------------------------------------------


def test_teaching_limits_reads_the_stored_limits(module_seeded_url, client, tutor):
    person, cookie = tutor

    def limits() -> service.TeachingLimits:
        return in_uow(
            module_seeded_url,
            lambda session: service.teaching_limits(session, person.id),
        )

    assert limits() == service.TeachingLimits(None, None, None)
    assert (
        in_uow(
            module_seeded_url,
            lambda session: service.preferred_classroom_ids(session, person.id),
        )
        == ()
    )
    patch_settings(
        client,
        cookie,
        person.id,
        0,
        daily_limit_minutes=240,
        weekly_limit_minutes=1200,
        required_break_minutes=15,
    )
    assert limits() == service.TeachingLimits(240, 1200, 15)


# --- Audit ----------------------------------------------------------------------------


def test_limit_and_room_changes_are_audited(
    module_seeded_url, client, owner_cookie, tutor
):
    person, cookie = tutor
    first, second = rooms(client, owner_cookie, 2)

    created = patch_settings(
        client,
        cookie,
        person.id,
        0,
        daily_limit_minutes=240,
        preferred_classroom_ids=[first["id"]],
    )
    request_id = created.headers["X-Request-ID"]
    [settings] = [
        e
        for e in events(module_seeded_url, record_type="tutor_availability_settings")
        if e.request_id == request_id
    ]
    assert settings.action == "create"
    assert settings.actor_user_id == person.id
    assert settings.changes["daily_limit_minutes"] == {"old": None, "new": 240}
    [room_created] = [
        e
        for e in events(module_seeded_url, record_type="tutor_preferred_classrooms")
        if e.request_id == request_id
    ]
    assert room_created.action == "create"
    assert room_created.changes["classroom_id"] == {"old": None, "new": first["id"]}

    changed = patch_settings(
        client,
        cookie,
        person.id,
        1,
        daily_limit_minutes=300,
        weekly_limit_minutes=1200,
        preferred_classroom_ids=[second["id"]],
    )
    request_id = changed.headers["X-Request-ID"]
    [update] = [
        e
        for e in events(module_seeded_url, record_type="tutor_availability_settings")
        if e.request_id == request_id
    ]
    assert update.action == "update"
    assert update.changes == {
        "daily_limit_minutes": {"old": 240, "new": 300},
        "weekly_limit_minutes": {"old": None, "new": 1200},
    }
    room_events = [
        (e.action, e.changes["classroom_id"])
        for e in events(module_seeded_url, record_type="tutor_preferred_classrooms")
        if e.request_id == request_id
    ]
    assert room_events == [
        ("delete", {"old": first["id"], "new": None}),
        ("create", {"old": None, "new": second["id"]}),
    ]

    # Only the rooms changed: the settings row's version moves without an
    # event (#22), as for the weekly windows.
    rooms_only = patch_settings(
        client, cookie, person.id, 2, preferred_classroom_ids=[first["id"]]
    )
    assert rooms_only.json()["version"] == 3
    assert [
        e
        for e in events(module_seeded_url, record_type="tutor_availability_settings")
        if e.request_id == rooms_only.headers["X-Request-ID"]
    ] == []


def test_classroom_names_come_from_the_catalog(client, owner_cookie, tutor):
    person, cookie = tutor
    room = create_classroom(client, owner_cookie, name=unique_name("Aula"))
    patch_settings(client, cookie, person.id, 0, preferred_classroom_ids=[room["id"]])
    renamed = patch(
        client,
        owner_cookie,
        classroom_path(room["id"]),
        {"version": room["version"], "name": f"{room['name']} B"},
    )
    assert code_of(renamed) == OK

    assert detail(client, cookie, person.id)["preferred_classrooms"] == [
        {"id": room["id"], "name": f"{room['name']} B", "is_active": True}
    ]
