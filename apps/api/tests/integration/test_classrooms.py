"""Classrooms over HTTP and through the service (#39 B, C1).

Most tests share one seeded database and use names of their own
(``unique_name``); the list tests that need exact results use a fresh
database.
"""

import uuid
from collections.abc import Iterator
from datetime import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.core.pagination import OffsetParams
from app.modules.catalog import service
from app.modules.catalog.schemas import AvailabilityWindow, ClassroomUpdate
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import (
    CLASSROOMS,
    classroom_event_count,
    classroom_path,
    count_statements,
    create_classroom,
    get,
    patch,
    post,
    unique_name,
    window,
    window_events,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


@pytest.fixture
def fresh(seeded_url: str) -> Iterator[tuple[str, TestClient, Any]]:
    """A database of this test only, its client and a signed-in Owner."""
    with auth_client(seeded_url) as fresh_client:
        person = create_user(seeded_url, roles=(OWNER,))
        yield seeded_url, fresh_client, sign_in(fresh_client, person.username)


def names(response) -> list[str]:
    assert code_of(response) == (200, None), response.text
    return [item["name"] for item in response.json()["items"]]


def row(url: str, classroom_id: Any) -> Any:
    [found] = run_sql(
        url,
        "SELECT name, capacity, is_active, version FROM classrooms WHERE id = :id",
        {"id": classroom_id},
    )
    return found


def stored_windows(url: str, classroom_id: Any) -> list[tuple[int, str, str]]:
    rows = run_sql(
        url,
        "SELECT weekday, starts_at, ends_at FROM classroom_availability_windows"
        " WHERE classroom_id = :id ORDER BY weekday, starts_at",
        {"id": classroom_id},
    )
    return [
        (r.weekday, r.starts_at.strftime("%H:%M"), r.ends_at.strftime("%H:%M"))
        for r in rows
    ]


MONDAY = [window(1, "09:00", "12:00"), window(1, "12:00", "14:00")]


# --- B1, B2: reads -------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [STUDENT, GUARDIAN, TUTOR, ACADEMIC_COORDINATOR, FINANCE_STAFF, ADMINISTRATOR],
)
def test_every_signed_in_role_can_list_and_read(
    module_seeded_url, client, cookie, role
):
    room = create_classroom(client, cookie, availability=MONDAY)
    reader = create_user(module_seeded_url, roles=(role,))
    reader_cookie = sign_in(client, reader.username)

    listed = get(client, reader_cookie, CLASSROOMS, limit=100)

    assert code_of(listed) == (200, None)
    assert get(client, reader_cookie, classroom_path(room["id"])).json() == room


def test_an_anonymous_caller_gets_401(client):
    client.cookies.clear()

    assert client.get(CLASSROOMS).status_code == 401


def test_the_detail_has_exactly_the_documented_fields(client, cookie):
    room = create_classroom(
        client,
        cookie,
        name="  Aula Detalle " + uuid.uuid4().hex[:6],
        capacity=12,
        availability=[window(3, "16:00", "18:00"), *MONDAY],
    )

    assert set(room) == {
        "id",
        "name",
        "capacity",
        "is_active",
        "availability",
        "version",
        "created_at",
        "updated_at",
    }
    assert room["name"].startswith("Aula Detalle ")
    assert (room["capacity"], room["is_active"], room["version"]) == (12, True, 1)
    # Ordered by weekday, then start; HH:MM.
    assert room["availability"] == [*MONDAY, window(3, "16:00", "18:00")]


def test_status_filter_order_and_pagination(fresh):
    url, fresh_client, owner_cookie = fresh
    for name in ("b aula", "Aula C", "A aula"):
        create_classroom(fresh_client, owner_cookie, name=name)
    inactive = create_classroom(fresh_client, owner_cookie, name="Bodega")
    patch(
        fresh_client,
        owner_cookie,
        classroom_path(inactive["id"]),
        {"version": 1, "is_active": False},
    )

    assert names(get(fresh_client, owner_cookie, CLASSROOMS)) == [
        "A aula",
        "Aula C",
        "b aula",
    ]
    assert names(get(fresh_client, owner_cookie, CLASSROOMS, status="inactive")) == [
        "Bodega"
    ]
    assert names(get(fresh_client, owner_cookie, CLASSROOMS, status="all")) == [
        "A aula",
        "Aula C",
        "b aula",
        "Bodega",
    ]
    page = get(fresh_client, owner_cookie, CLASSROOMS, status="all", limit=2, offset=1)
    assert names(page) == ["Aula C", "b aula"]
    assert (page.json()["total"], page.json()["limit"], page.json()["offset"]) == (
        4,
        2,
        1,
    )


@pytest.mark.parametrize(
    "params", [{"status": "deleted"}, {"limit": 0}, {"limit": 101}, {"offset": -1}]
)
def test_invalid_list_queries_are_422(client, cookie, params):
    assert code_of(get(client, cookie, CLASSROOMS, **params)) == (
        422,
        "validation_error",
    )


def test_an_unknown_classroom_is_404_and_an_inactive_one_is_returned(client, cookie):
    room = create_classroom(client, cookie)
    patch(
        client, cookie, classroom_path(room["id"]), {"version": 1, "is_active": False}
    )

    assert code_of(get(client, cookie, classroom_path(uuid.uuid4()))) == (
        404,
        "not_found",
    )
    assert code_of(get(client, cookie, classroom_path("abc")))[0] == 422
    detail = get(client, cookie, classroom_path(room["id"])).json()
    assert (detail["is_active"], detail["version"]) == (False, 2)


def test_a_list_page_takes_at_most_three_statements(fresh):
    url, fresh_client, owner_cookie = fresh
    for index in range(30):
        create_classroom(
            fresh_client,
            owner_cookie,
            availability=[window(1 + index % 7, "09:00", "12:00")],
        )

    statements, page = count_statements(
        url,
        lambda session: service.list_classrooms(
            session, status="all", page=OffsetParams(limit=30, offset=0)
        ),
    )

    assert len(page.items) == 30
    assert all(len(item.availability) == 1 for item in page.items)
    assert statements <= 3


# --- B3: create ----------------------------------------------------------------------


def test_create_with_and_without_windows(module_seeded_url, client, cookie):
    bare = create_classroom(client, cookie, capacity=1)
    windowed = create_classroom(client, cookie, capacity=500, availability=MONDAY)

    assert bare["availability"] == []
    assert stored_windows(module_seeded_url, bare["id"]) == []
    assert stored_windows(module_seeded_url, windowed["id"]) == [
        (1, "09:00", "12:00"),
        (1, "12:00", "14:00"),
    ]
    assert row(module_seeded_url, windowed["id"]).capacity == 500


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({"capacity": 0}, ["body", "capacity"]),
        ({"capacity": 501}, ["body", "capacity"]),
        ({"capacity": "x"}, ["body", "capacity"]),
        ({"name": "   "}, ["body", "name"]),
        ({"name": "x" * 101}, ["body", "name"]),
        ({"is_active": False}, ["body", "is_active"]),
        (
            {"availability": [window(1, "12:00", "09:00")]},
            ["body", "availability", 0, "ends_at"],
        ),
        (
            {
                "availability": [
                    window(1, "09:00", "12:00"),
                    window(1, "11:00", "13:00"),
                ]
            },
            ["body", "availability", 1, "starts_at"],
        ),
        (
            {"availability": [window(1, "09:00", "12:00")] * 2},
            ["body", "availability", 1, "starts_at"],
        ),
        (
            {"availability": [window(0, "09:00", "12:00")]},
            ["body", "availability", 0, "weekday"],
        ),
        (
            {"availability": [window(1, "09:00:00", "12:00")]},
            ["body", "availability", 0, "starts_at"],
        ),
        ({"availability": None}, ["body", "availability"]),
    ],
)
def test_invalid_create_bodies_are_422_and_write_nothing(
    module_seeded_url, client, cookie, body, path
):
    before = classroom_event_count(module_seeded_url)

    response = post(
        client,
        cookie,
        CLASSROOMS,
        {"name": unique_name("Aula"), "capacity": 12, **body},
    )

    assert code_of(response) == (422, "validation_error"), response.text
    assert path in [error["path"] for error in response.json()["errors"]]
    assert classroom_event_count(module_seeded_url) == before


def test_an_overlap_error_has_its_code(client, cookie):
    response = post(
        client,
        cookie,
        CLASSROOMS,
        {
            "name": unique_name("Aula"),
            "capacity": 12,
            "availability": [*MONDAY, window(1, "11:00", "13:00")],
        },
    )

    assert response.json()["errors"] == [
        {
            "path": ["body", "availability", 2, "starts_at"],
            "code": "window_overlap",
            "message": "This window overlaps another window on the same day.",
        }
    ]


@pytest.mark.parametrize("variant", ["upper", "inactive"])
def test_a_duplicate_name_is_409_name_taken(client, cookie, variant):
    name = unique_name("Aula")
    room = create_classroom(client, cookie, name=name)
    if variant == "inactive":
        patch(
            client,
            cookie,
            classroom_path(room["id"]),
            {"version": 1, "is_active": False},
        )

    response = post(client, cookie, CLASSROOMS, {"name": name.upper(), "capacity": 5})

    assert code_of(response) == (409, "name_taken")


# --- B4: update ----------------------------------------------------------------------


def test_rename_capacity_deactivate_and_reactivate(module_seeded_url, client, cookie):
    room = create_classroom(client, cookie, availability=MONDAY)
    new_name = unique_name("Sala")

    renamed = patch(
        client, cookie, classroom_path(room["id"]), {"version": 1, "name": new_name}
    )
    resized = patch(
        client, cookie, classroom_path(room["id"]), {"version": 2, "capacity": 30}
    )
    deactivated = patch(
        client, cookie, classroom_path(room["id"]), {"version": 3, "is_active": False}
    )
    reactivated = patch(
        client, cookie, classroom_path(room["id"]), {"version": 4, "is_active": True}
    )

    assert [r.status_code for r in (renamed, resized, deactivated, reactivated)] == [
        200
    ] * 4
    assert renamed.json()["name"] == new_name
    assert resized.json()["capacity"] == 30
    assert deactivated.json()["is_active"] is False
    final = reactivated.json()
    assert (final["is_active"], final["version"], final["availability"]) == (
        True,
        5,
        MONDAY,
    )
    assert tuple(row(module_seeded_url, room["id"])) == (new_name, 30, True, 5)


def test_a_rename_to_another_case_of_its_own_name_is_allowed(client, cookie):
    name = unique_name("aula")
    room = create_classroom(client, cookie, name=name)

    response = patch(
        client, cookie, classroom_path(room["id"]), {"version": 1, "name": name.upper()}
    )

    assert (response.status_code, response.json()["name"]) == (200, name.upper())


@pytest.mark.parametrize(
    ("availability", "expected"),
    [
        # Add a window.
        (
            [*MONDAY, window(5, "16:00", "20:00")],
            [*MONDAY, window(5, "16:00", "20:00")],
        ),
        # Change one window (keep the other).
        (
            [MONDAY[0], window(1, "12:00", "15:00")],
            [MONDAY[0], window(1, "12:00", "15:00")],
        ),
        # Remove one window.
        ([MONDAY[1]], [MONDAY[1]]),
        # Clear them all: no restriction.
        ([], []),
    ],
)
def test_replace_windows(module_seeded_url, client, cookie, availability, expected):
    room = create_classroom(client, cookie, availability=MONDAY)

    response = patch(
        client,
        cookie,
        classroom_path(room["id"]),
        {"version": 1, "availability": availability},
    )

    assert response.status_code == 200, response.text
    assert (response.json()["availability"], response.json()["version"]) == (
        expected,
        2,
    )
    assert get(client, cookie, classroom_path(room["id"])).json()["availability"] == (
        expected
    )


def test_a_changed_window_that_overlaps_its_old_self_is_saved(client, cookie):
    # Mon 09:00-12:00 becomes Mon 10:00-13:00: the old row is deleted before
    # the new one is inserted, so the exclusion constraint never sees both.
    room = create_classroom(client, cookie, availability=[MONDAY[0]])

    response = patch(
        client,
        cookie,
        classroom_path(room["id"]),
        {"version": 1, "availability": [window(1, "10:00", "13:00")]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["availability"] == [window(1, "10:00", "13:00")]


def test_windows_in_another_order_are_no_change(module_seeded_url, client, cookie):
    room = create_classroom(client, cookie, availability=MONDAY)
    before = classroom_event_count(module_seeded_url)

    response = patch(
        client,
        cookie,
        classroom_path(room["id"]),
        {"version": 1, "availability": list(reversed(MONDAY))},
    )

    assert (response.status_code, response.json()["version"]) == (200, 1)
    assert classroom_event_count(module_seeded_url) == before


def test_a_patch_that_changes_nothing_is_200_without_a_new_version(
    module_seeded_url, client, cookie
):
    room = create_classroom(client, cookie, capacity=12, availability=MONDAY)
    before = classroom_event_count(module_seeded_url)

    response = patch(
        client,
        cookie,
        classroom_path(room["id"]),
        {
            "version": 1,
            "name": room["name"],
            "capacity": 12,
            "is_active": True,
            "availability": MONDAY,
        },
    )

    assert response.status_code == 200
    assert response.json() == room
    assert classroom_event_count(module_seeded_url) == before


def test_a_change_of_fields_and_windows_bumps_the_version_once(
    module_seeded_url, client, cookie
):
    room = create_classroom(client, cookie, availability=MONDAY)

    response = patch(
        client,
        cookie,
        classroom_path(room["id"]),
        {"version": 1, "capacity": 40, "availability": []},
    )

    assert response.json()["version"] == 2
    assert row(module_seeded_url, room["id"]).version == 2


def test_a_stale_version_is_409_and_changes_nothing(module_seeded_url, client, cookie):
    room = create_classroom(client, cookie, availability=MONDAY)
    patch(client, cookie, classroom_path(room["id"]), {"version": 1, "capacity": 20})

    response = patch(
        client,
        cookie,
        classroom_path(room["id"]),
        {"version": 1, "capacity": 30, "availability": []},
    )

    assert code_of(response) == (409, "version_conflict")
    assert tuple(row(module_seeded_url, room["id"]))[1:] == (20, True, 2)
    assert len(stored_windows(module_seeded_url, room["id"])) == 2


@pytest.mark.parametrize("variant", ["upper", "inactive"])
def test_a_rename_to_another_rooms_name_is_409(client, cookie, variant):
    taken = create_classroom(client, cookie)
    if variant == "inactive":
        patch(
            client,
            cookie,
            classroom_path(taken["id"]),
            {"version": 1, "is_active": False},
        )
    room = create_classroom(client, cookie)

    response = patch(
        client,
        cookie,
        classroom_path(room["id"]),
        {"version": 1, "name": taken["name"].upper()},
    )

    assert code_of(response) == (409, "name_taken")


def test_patch_check_order(client, cookie):
    taken = create_classroom(client, cookie)
    room = create_classroom(client, cookie)

    # 422 before 404.
    assert code_of(
        patch(
            client, cookie, classroom_path(uuid.uuid4()), {"version": 1, "capacity": 0}
        )
    ) == (422, "validation_error")
    # 404 before the version.
    assert code_of(
        patch(client, cookie, classroom_path(uuid.uuid4()), {"version": 9, "name": "x"})
    ) == (404, "not_found")
    # The version before the name.
    assert code_of(
        patch(
            client,
            cookie,
            classroom_path(room["id"]),
            {"version": 9, "name": taken["name"]},
        )
    ) == (409, "version_conflict")


@pytest.mark.parametrize(
    "body",
    [
        {"capacity": 5},
        {"version": 1, "name": None},
        {"version": 1, "capacity": None},
        {"version": 1, "is_active": None},
        {"version": 1, "availability": None},
        {"version": 1, "capacity": 501},
        {"version": 1, "availability": [*MONDAY, window(1, "13:00", "15:00")]},
        {"version": 1, "equipment": ["proyector"]},
    ],
)
def test_invalid_patch_bodies_are_422(module_seeded_url, client, cookie, body):
    room = create_classroom(client, cookie, availability=MONDAY)

    response = patch(client, cookie, classroom_path(room["id"]), body)

    assert code_of(response) == (422, "validation_error")
    assert row(module_seeded_url, room["id"]).version == 1


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, GUARDIAN, STUDENT]
)
def test_other_roles_cannot_write(module_seeded_url, client, cookie, role):
    room = create_classroom(client, cookie)
    person = create_user(module_seeded_url, roles=(role,))
    person_cookie = sign_in(client, person.username)

    created = post(
        client, person_cookie, CLASSROOMS, {"name": unique_name("Aula"), "capacity": 5}
    )
    changed = patch(
        client, person_cookie, classroom_path(room["id"]), {"version": 1, "capacity": 5}
    )

    assert code_of(created) == (403, "permission_denied")
    assert code_of(changed) == (403, "permission_denied")


def test_an_administrator_can_write(module_seeded_url, client):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)

    room = create_classroom(client, admin_cookie)
    response = patch(
        client, admin_cookie, classroom_path(room["id"]), {"version": 1, "capacity": 3}
    )

    assert response.status_code == 200


# --- B6: service helpers -------------------------------------------------------------


def _available(url: str, classroom_id: Any, weekday: int, start: str, end: str) -> bool:
    return in_uow(
        url,
        lambda session: service.is_classroom_available(
            session,
            uuid.UUID(str(classroom_id)),
            weekday,
            time.fromisoformat(start),
            time.fromisoformat(end),
        ),
    )


def test_a_room_without_windows_is_always_available(module_seeded_url, client, cookie):
    room = create_classroom(client, cookie)

    assert _available(module_seeded_url, room["id"], 7, "00:00", "23:59") is True


@pytest.mark.parametrize(
    ("weekday", "start", "end", "expected"),
    [
        (1, "10:00", "11:00", True),  # inside one window
        (1, "09:00", "12:00", True),  # exactly one window
        (1, "11:00", "13:00", True),  # across two touching windows
        (1, "09:00", "14:00", True),  # the whole union
        (1, "08:30", "10:00", False),  # starts before the first window
        (1, "13:00", "14:30", False),  # past the last window
        (3, "16:30", "18:00", False),  # crosses into a gap
        (3, "14:00", "15:00", True),
        (2, "10:00", "11:00", False),  # a weekday without windows
    ],
)
def test_availability_is_the_union_of_the_weekdays_windows(
    module_seeded_url, client, cookie, weekday, start, end, expected
):
    room = create_classroom(
        client,
        cookie,
        availability=[
            *MONDAY,
            window(3, "14:00", "16:00"),
            window(3, "17:00", "19:00"),
        ],
    )

    assert _available(module_seeded_url, room["id"], weekday, start, end) is expected


def test_an_inactive_or_unknown_room_is_never_available(
    module_seeded_url, client, cookie
):
    room = create_classroom(client, cookie)
    patch(
        client, cookie, classroom_path(room["id"]), {"version": 1, "is_active": False}
    )

    assert _available(module_seeded_url, room["id"], 1, "10:00", "11:00") is False
    assert _available(module_seeded_url, uuid.uuid4(), 1, "10:00", "11:00") is False


@pytest.mark.parametrize(
    ("weekday", "start", "end"),
    [(0, "10:00", "11:00"), (8, "10:00", "11:00"), (1, "11:00", "11:00")],
)
def test_invalid_availability_questions_raise(
    module_seeded_url, client, cookie, weekday, start, end
):
    room = create_classroom(client, cookie)

    with pytest.raises(ValueError):
        _available(module_seeded_url, room["id"], weekday, start, end)


def test_active_classroom_ids_and_get_classroom(module_seeded_url, client, cookie):
    active = create_classroom(client, cookie, availability=MONDAY)
    inactive = create_classroom(client, cookie)
    patch(
        client,
        cookie,
        classroom_path(inactive["id"]),
        {"version": 1, "is_active": False},
    )
    unknown = uuid.uuid4()
    ids = [uuid.UUID(active["id"]), uuid.UUID(inactive["id"]), unknown]

    found = in_uow(module_seeded_url, lambda s: service.active_classroom_ids(s, ids))
    empty = in_uow(module_seeded_url, lambda s: service.active_classroom_ids(s, []))
    detail = in_uow(
        module_seeded_url, lambda s: service.get_classroom(s, uuid.UUID(active["id"]))
    )

    assert found == {uuid.UUID(active["id"])}
    assert empty == set()
    assert detail.model_dump(mode="json") == active


# --- C1: audit -----------------------------------------------------------------------


def test_create_records_the_room_and_one_create_per_window(
    module_seeded_url, client, cookie, owner
):
    created = post(
        client,
        cookie,
        CLASSROOMS,
        {"name": unique_name("Aula"), "capacity": 12, "availability": MONDAY},
    )
    room = created.json()

    recorded = events(
        module_seeded_url, record_type="classrooms", record_id=uuid.UUID(room["id"])
    )
    windows = window_events(module_seeded_url, room["id"])

    assert [e.action for e in recorded] == ["create"]
    assert recorded[0].actor_user_id == owner.id
    assert recorded[0].request_id == created.headers["X-Request-ID"]
    assert recorded[0].changes == {
        "name": {"old": None, "new": room["name"]},
        "capacity": {"old": None, "new": 12},
        "is_active": {"old": None, "new": True},
    }
    assert sorted(changes["starts_at"]["new"] for _, changes in windows) == [
        "09:00:00",
        "12:00:00",
    ]
    assert [action for action, _ in windows] == ["create", "create"]
    assert windows[0][1]["weekday"] == {"old": None, "new": 1}


def test_a_capacity_change_records_exactly_capacity(module_seeded_url, client, cookie):
    room = create_classroom(client, cookie, capacity=12, availability=MONDAY)

    patch(client, cookie, classroom_path(room["id"]), {"version": 1, "capacity": 15})

    recorded = events(
        module_seeded_url, record_type="classrooms", record_id=uuid.UUID(room["id"])
    )
    assert [e.action for e in recorded] == ["create", "update"]
    assert recorded[1].changes == {"capacity": {"old": 12, "new": 15}}
    assert len(window_events(module_seeded_url, room["id"])) == 2


def test_replacing_one_window_records_one_delete_and_one_create(
    module_seeded_url, client, cookie
):
    room = create_classroom(client, cookie, availability=MONDAY)

    response = patch(
        client,
        cookie,
        classroom_path(room["id"]),
        {"version": 1, "availability": [MONDAY[0], window(1, "12:00", "15:00")]},
    )

    assert response.json()["version"] == 2
    windows = window_events(module_seeded_url, room["id"])[2:]
    assert [action for action, _ in windows] == ["delete", "create"]
    assert windows[0][1]["ends_at"] == {"old": "14:00:00", "new": None}
    assert windows[1][1]["ends_at"] == {"old": None, "new": "15:00:00"}
    # The version bump of a windows-only change writes no classrooms event.
    recorded = events(
        module_seeded_url, record_type="classrooms", record_id=uuid.UUID(room["id"])
    )
    assert [e.action for e in recorded] == ["create"]


@pytest.mark.parametrize(
    "case", ["noop", "forbidden", "not_found", "conflict", "invalid"]
)
def test_failed_or_empty_changes_write_no_audit_event(
    module_seeded_url, client, cookie, case
):
    room = create_classroom(client, cookie, availability=MONDAY)
    actor_cookie = cookie
    target: Any = room["id"]
    body: dict[str, Any] = {"version": 1, "capacity": 99, "availability": []}
    if case == "noop":
        body = {"version": 1, "capacity": 12, "availability": MONDAY}
    elif case == "forbidden":
        coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
        actor_cookie = sign_in(client, coordinator.username)
    elif case == "not_found":
        target = uuid.uuid4()
    elif case == "conflict":
        body["version"] = 7
    elif case == "invalid":
        body["availability"] = [MONDAY[0], MONDAY[0]]
    before = classroom_event_count(module_seeded_url)

    response = patch(client, actor_cookie, classroom_path(target), body)

    assert response.status_code in (200, 403, 404, 409, 422)
    assert classroom_event_count(module_seeded_url) == before


def test_a_name_conflict_on_create_writes_no_audit_event(
    module_seeded_url, client, cookie
):
    room = create_classroom(client, cookie)
    before = classroom_event_count(module_seeded_url)

    response = post(
        client,
        cookie,
        CLASSROOMS,
        {"name": room["name"], "capacity": 5, "availability": MONDAY},
    )

    assert code_of(response) == (409, "name_taken")
    assert classroom_event_count(module_seeded_url) == before


def test_the_exclusion_constraint_backs_the_body_check(
    module_seeded_url, client, cookie
):
    # A body built without validation (model_construct) reaches the flush:
    # the 23P01 maps to 422 validation_error and nothing is kept.
    room = create_classroom(client, cookie)
    body = ClassroomUpdate.model_construct(
        version=1,
        availability=[
            AvailabilityWindow(weekday=1, starts_at="09:00", ends_at="12:00"),
            AvailabilityWindow(weekday=1, starts_at="11:00", ends_at="13:00"),
        ],
    )

    with pytest.raises(AppError) as exc:
        in_uow(
            module_seeded_url,
            lambda s: service.update_classroom(s, uuid.UUID(room["id"]), body),
        )

    assert (exc.value.status, exc.value.code) == (422, "validation_error")
    assert stored_windows(module_seeded_url, room["id"]) == []
    assert row(module_seeded_url, room["id"]).version == 1


# --- #50 B9: the windows for the slot search -----------------------------------------


def test_classroom_weekly_windows_reads_them_in_one_statement(
    module_seeded_url, client, cookie
):
    unrestricted = create_classroom(client, cookie)
    restricted = create_classroom(
        client, cookie, availability=[window(3, "14:00", "16:00"), *MONDAY]
    )

    def windows(room: dict[str, Any]) -> tuple[int, Any]:
        return count_statements(
            module_seeded_url,
            lambda session: service.classroom_weekly_windows(
                session, uuid.UUID(room["id"])
            ),
        )

    assert windows(unrestricted) == (1, ())
    count, found = windows(restricted)
    assert count == 1
    assert found == (
        (1, time(9), time(12)),
        (1, time(12), time(14)),
        (3, time(14), time(16)),
    )
    assert service.windows_cover(found, 1, time(11), time(13)) is True
    assert service.windows_cover(found, 2, time(11), time(13)) is False
    assert service.windows_cover((), 2, time(11), time(13)) is True
