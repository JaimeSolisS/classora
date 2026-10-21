"""Tutor availability (#45 C, D, E1) with real logins through the real app:
weekly windows, exceptions, the check route, the M4 service functions, the
edit rights, the M4-18 impact seam and the audit trail."""

import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.modules.staff import service
from app.modules.staff.assignment_impact import get_availability_impact_source
from app.modules.staff.availability import (
    AvailabilityResult,
    DatedException,
    WeeklyWindow,
)
from tests.authz.assertions import assert_same_not_found
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import (
    create_closure,
    get,
    post,
    unique_year,
    window,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

TUTORS = "/api/v1/tutors"

OK = (200, None)
CREATED = (201, None)
NO_CONTENT = (204, None)
DENIED = (403, "permission_denied")
NOT_FOUND = (404, "not_found")
CONFLICT = (409, "version_conflict")
REQUIRES_APPROVAL = (409, "availability_change_requires_approval")
INVALID = (422, "validation_error")

NO_LIMITS = {
    "daily_limit_minutes": None,
    "weekly_limit_minutes": None,
    "required_break_minutes": None,
}

TABLES = (
    "tutor_availability_settings",
    "tutor_availability_windows",
    "tutor_availability_exceptions",
)


def availability_path(user_id: Any) -> str:
    return f"{TUTORS}/{user_id}/availability"


def exception_path(user_id: Any, exception_id: Any) -> str:
    return f"{availability_path(user_id)}/exceptions/{exception_id}"


def put_weekly(
    client: TestClient, cookie: Any, user_id: Any, version: int, windows: list[Any]
) -> Response:
    client.cookies.clear()
    return client.put(
        f"{availability_path(user_id)}/weekly",
        json={"version": version, "windows": windows},
        headers=signed_headers(cookie),
    )


def add_exception(
    client: TestClient, cookie: Any, user_id: Any, **body: Any
) -> Response:
    payload = {
        "kind": "unavailable",
        "starts_on": "2026-12-21",
        "ends_on": "2026-12-31",
        **body,
    }
    return post(client, cookie, f"{availability_path(user_id)}/exceptions", payload)


def delete_exception(
    client: TestClient, cookie: Any, user_id: Any, exception_id: Any
) -> Response:
    client.cookies.clear()
    return client.delete(
        exception_path(user_id, exception_id), headers=signed_headers(cookie)
    )


def check(client: TestClient, cookie: Any, user_id: Any, **params: Any) -> Response:
    client.cookies.clear()
    return client.get(
        f"{availability_path(user_id)}/check",
        params=params,
        headers=cookie_header(cookie),
    )


def detail(client: TestClient, cookie: Any, user_id: Any) -> dict[str, Any]:
    response = get(client, cookie, availability_path(user_id))
    assert code_of(response) == OK, response.text
    body: dict[str, Any] = response.json()
    return body


def row_counts(url: str, user_id: Any) -> tuple[int, int, int]:
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM tutor_availability_settings"
        " WHERE user_id = :id) AS s,"
        " (SELECT count(*) FROM tutor_availability_windows"
        " WHERE tutor_user_id = :id) AS w,"
        " (SELECT count(*) FROM tutor_availability_exceptions"
        " WHERE tutor_user_id = :id) AS e",
        {"id": user_id},
    )
    return (row.s, row.w, row.e)


def event_count(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE record_type IN"
        " ('tutor_availability_settings', 'tutor_availability_windows',"
        " 'tutor_availability_exceptions')",
    )
    return int(row.n)


def first_monday(year: int) -> date:
    day = date(year, 1, 1)
    return day + timedelta(days=(7 - day.weekday()) % 7)


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


@pytest.fixture
def tutor(module_seeded_url: str, client: TestClient) -> tuple[CreatedUser, Any]:
    """A new tutor and their session cookie."""
    person = create_user(module_seeded_url, roles=(TUTOR,))
    return person, sign_in(client, person.username)


AFTERNOONS = [window(1, "16:00", "20:00"), window(3, "16:00", "18:00")]


# --- C1, C2: reading and replacing the weekly windows --------------------------------


def test_a_tutor_without_availability_reads_version_0(client, tutor):
    person, cookie = tutor

    assert detail(client, cookie, person.id) == {
        "weekly": [],
        "exceptions": [],
        "limits": NO_LIMITS,
        "preferred_classrooms": [],
        "version": 0,
    }


def test_a_tutor_replaces_and_clears_their_weekly_windows(
    module_seeded_url, client, tutor
):
    person, cookie = tutor

    created = put_weekly(client, cookie, person.id, 0, list(reversed(AFTERNOONS)))
    assert code_of(created) == OK, created.text
    assert created.json() == {
        "weekly": AFTERNOONS,
        "exceptions": [],
        "limits": NO_LIMITS,
        "preferred_classrooms": [],
        "version": 1,
    }
    assert detail(client, cookie, person.id) == created.json()

    changed = put_weekly(
        client,
        cookie,
        person.id,
        1,
        [AFTERNOONS[0], window(5, "09:00", "12:00"), window(5, "12:00", "13:00")],
    )
    assert code_of(changed) == OK, changed.text
    assert changed.json()["version"] == 2
    assert changed.json()["weekly"] == [
        AFTERNOONS[0],
        window(5, "09:00", "12:00"),
        window(5, "12:00", "13:00"),
    ]

    cleared = put_weekly(client, cookie, person.id, 2, [])
    assert code_of(cleared) == OK
    assert cleared.json() == {
        "weekly": [],
        "exceptions": [],
        "limits": NO_LIMITS,
        "preferred_classrooms": [],
        "version": 3,
    }
    assert row_counts(module_seeded_url, person.id) == (1, 0, 0)


def test_a_no_op_put_keeps_the_version_and_writes_nothing(
    module_seeded_url, client, tutor
):
    person, cookie = tutor
    assert code_of(put_weekly(client, cookie, person.id, 0, AFTERNOONS)) == OK
    before = event_count(module_seeded_url)

    again = put_weekly(client, cookie, person.id, 1, list(reversed(AFTERNOONS)))

    assert code_of(again) == OK
    assert again.json()["version"] == 1
    assert event_count(module_seeded_url) == before
    # An empty list for a tutor that never had windows: also a no-op.
    other = create_user(module_seeded_url, roles=(TUTOR,))
    empty = put_weekly(client, sign_in(client, other.username), other.id, 0, [])
    assert empty.json()["version"] == 0
    assert row_counts(module_seeded_url, other.id) == (0, 0, 0)


@pytest.mark.parametrize("editor", ["owner", "admin"])
def test_owners_and_administrators_edit_a_tutors_availability(
    module_seeded_url, client, request, editor
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = sign_in(client, request.getfixturevalue(editor).username)

    weekly = put_weekly(client, cookie, person.id, 0, AFTERNOONS)
    created = add_exception(client, cookie, person.id)

    assert code_of(weekly) == OK
    assert code_of(created) == CREATED
    assert code_of(
        delete_exception(client, cookie, person.id, created.json()["id"])
    ) == (NO_CONTENT)
    assert row_counts(module_seeded_url, person.id) == (1, 2, 0)


def test_a_coordinator_reads_but_may_not_change(
    module_seeded_url, client, coordinator, owner
):
    person = create_user(module_seeded_url, roles=(TUTOR,))
    owner_cookie = sign_in(client, owner.username)
    put_weekly(client, owner_cookie, person.id, 0, AFTERNOONS)
    existing = add_exception(client, owner_cookie, person.id).json()
    before = event_count(module_seeded_url)
    cookie = sign_in(client, coordinator.username)

    assert detail(client, cookie, person.id)["weekly"] == AFTERNOONS
    assert (
        code_of(
            check(
                client, cookie, person.id, date="2026-10-19", start="17:00", end="18:00"
            )
        )
        == OK
    )
    responses = [
        put_weekly(client, cookie, person.id, 1, []),
        add_exception(client, cookie, person.id),
        delete_exception(client, cookie, person.id, existing["id"]),
    ]

    assert [code_of(r) for r in responses] == [DENIED] * 3
    assert row_counts(module_seeded_url, person.id) == (1, 2, 1)
    assert event_count(module_seeded_url) == before


def test_a_tutor_cannot_see_or_change_another_tutor(
    module_seeded_url, client, tutor, owner
):
    # IDOR: tutor A on tutor B's availability is 404, like a missing tutor.
    person, cookie = tutor
    other = create_user(module_seeded_url, roles=(TUTOR,))
    owner_cookie = sign_in(client, owner.username)
    put_weekly(client, owner_cookie, other.id, 0, AFTERNOONS)
    existing = add_exception(client, owner_cookie, other.id).json()
    missing = uuid.uuid4()
    before = event_count(module_seeded_url)

    pairs = [
        (
            get(client, cookie, availability_path(other.id)),
            get(client, cookie, availability_path(missing)),
        ),
        (
            put_weekly(client, cookie, other.id, 1, []),
            put_weekly(client, cookie, missing, 1, []),
        ),
        (
            add_exception(client, cookie, other.id),
            add_exception(client, cookie, missing),
        ),
        (
            delete_exception(client, cookie, other.id, existing["id"]),
            delete_exception(client, cookie, missing, existing["id"]),
        ),
        (
            check(
                client, cookie, other.id, date="2026-10-19", start="17:00", end="18:00"
            ),
            check(
                client, cookie, missing, date="2026-10-19", start="17:00", end="18:00"
            ),
        ),
    ]

    for hidden, absent in pairs:
        assert_same_not_found(hidden, absent)
    assert row_counts(module_seeded_url, other.id) == (1, 2, 1)
    assert event_count(module_seeded_url) == before


def test_a_user_without_the_tutor_role_has_no_availability(
    module_seeded_url, client, owner
):
    person = create_user(module_seeded_url, roles=(FINANCE_STAFF,))
    cookie = sign_in(client, owner.username)

    assert code_of(get(client, cookie, availability_path(person.id))) == NOT_FOUND
    assert code_of(put_weekly(client, cookie, person.id, 0, AFTERNOONS)) == NOT_FOUND
    assert row_counts(module_seeded_url, person.id) == (0, 0, 0)


@pytest.mark.parametrize("role", [FINANCE_STAFF, STUDENT, GUARDIAN])
def test_roles_without_tutor_view_are_denied_everywhere(
    module_seeded_url, client, role
):
    caller = create_user(module_seeded_url, roles=(role,))
    person = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = sign_in(client, caller.username)

    responses = [
        get(client, cookie, availability_path(person.id)),
        put_weekly(client, cookie, person.id, 0, AFTERNOONS),
        add_exception(client, cookie, person.id),
        delete_exception(client, cookie, person.id, uuid.uuid4()),
        check(client, cookie, person.id, date="2026-10-19", start="17:00", end="18:00"),
    ]

    assert [code_of(r) for r in responses] == [DENIED] * 5
    assert row_counts(module_seeded_url, person.id) == (0, 0, 0)


def test_a_stale_version_is_a_conflict(module_seeded_url, client, tutor):
    person, cookie = tutor

    # No row yet: only version 0 matches.
    assert code_of(put_weekly(client, cookie, person.id, 1, AFTERNOONS)) == CONFLICT
    assert row_counts(module_seeded_url, person.id) == (0, 0, 0)
    assert code_of(put_weekly(client, cookie, person.id, 0, AFTERNOONS)) == OK
    before = event_count(module_seeded_url)

    for stale in (0, 2):
        response = put_weekly(client, cookie, person.id, stale, [])
        assert code_of(response) == CONFLICT
    assert row_counts(module_seeded_url, person.id) == (1, 2, 0)
    assert event_count(module_seeded_url) == before


@pytest.mark.parametrize("first", [True, False])
def test_concurrent_puts_with_one_version_give_one_200_and_one_409(
    module_seeded_url, client, tutor, first
):
    person, cookie = tutor
    version = 0
    if not first:
        put_weekly(client, cookie, person.id, 0, AFTERNOONS)
        version = 1
    results: list[tuple[int, str | None]] = []
    start = threading.Barrier(2)

    def replace(windows: list[Any]) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results.append(
                code_of(put_weekly(own_client, cookie, person.id, version, windows))
            )

    bodies = [[window(2, "09:00", "10:00")], [window(4, "11:00", "12:00")]]
    threads = [threading.Thread(target=replace, args=(b,)) for b in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == sorted([OK, CONFLICT], key=str)
    after = detail(client, cookie, person.id)
    assert after["version"] == version + 1
    assert after["weekly"] in bodies


@pytest.mark.parametrize(
    "windows",
    [
        [window(1, "16:00", "20:00"), window(1, "19:00", "21:00")],
        [window(8, "16:00", "20:00")],
        [window(1, "20:00", "16:00")],
        [window(1, "16:00", "24:00")],
        [{**window(1, "16:00", "20:00"), "room": 1}],
        [
            window(1 + i // 6, f"{8 + i % 6:02d}:00", f"{8 + i % 6:02d}:30")
            for i in range(43)
        ],
    ],
)
def test_invalid_weekly_bodies_are_422_before_404(
    module_seeded_url, client, tutor, windows
):
    person, cookie = tutor
    other = create_user(module_seeded_url, roles=(TUTOR,))

    assert code_of(put_weekly(client, cookie, person.id, 0, windows)) == INVALID
    assert code_of(put_weekly(client, cookie, other.id, 0, windows)) == INVALID
    assert row_counts(module_seeded_url, person.id) == (0, 0, 0)


def test_an_overlap_is_reported_on_the_later_window(client, tutor):
    person, cookie = tutor

    response = put_weekly(
        client,
        cookie,
        person.id,
        0,
        [
            window(1, "16:00", "20:00"),
            window(2, "09:00", "10:00"),
            window(1, "19:00", "21:00"),
        ],
    )

    assert code_of(response) == INVALID
    [error] = response.json()["errors"]
    assert error["path"] == ["body", "windows", 2, "starts_at"]
    assert error["code"] == "window_overlap"


# --- C3: exceptions ------------------------------------------------------------------


def test_exceptions_are_added_listed_in_order_and_deleted(
    module_seeded_url, client, tutor
):
    person, cookie = tutor

    later = add_exception(
        client,
        cookie,
        person.id,
        starts_on="2027-01-04",
        ends_on="2027-01-04",
        kind="available",
        starts_at="10:00",
        ends_at="12:00",
    )
    hours = add_exception(
        client, cookie, person.id, starts_at="09:00", ends_at="11:00", note="  Médico  "
    )
    whole = add_exception(client, cookie, person.id, note="")

    for response in (later, hours, whole):
        assert code_of(response) == CREATED, response.text
    assert set(hours.json()) == {
        "id",
        "kind",
        "starts_on",
        "ends_on",
        "starts_at",
        "ends_at",
        "note",
        "created_at",
    }
    assert hours.json()["note"] == "Médico"
    assert whole.json()["note"] is None
    listed = detail(client, cookie, person.id)
    # By starts_on, then whole days first, then starts_at.
    assert [item["id"] for item in listed["exceptions"]] == [
        whole.json()["id"],
        hours.json()["id"],
        later.json()["id"],
    ]
    assert listed["exceptions"][0] == whole.json()
    assert listed["exceptions"][1]["starts_at"] == "09:00"
    assert listed["version"] == 0  # exceptions don't touch the weekly version

    assert (
        code_of(delete_exception(client, cookie, person.id, hours.json()["id"]))
        == NO_CONTENT
    )
    assert (
        code_of(delete_exception(client, cookie, person.id, hours.json()["id"]))
        == NOT_FOUND
    )
    assert row_counts(module_seeded_url, person.id) == (0, 0, 2)


def test_another_tutors_exception_id_is_404(module_seeded_url, client, owner, tutor):
    person, cookie = tutor
    other = create_user(module_seeded_url, roles=(TUTOR,))
    owner_cookie = sign_in(client, owner.username)
    foreign = add_exception(client, owner_cookie, other.id).json()

    response = delete_exception(client, cookie, person.id, foreign["id"])
    as_owner = delete_exception(client, owner_cookie, person.id, foreign["id"])

    assert_same_not_found(
        response, delete_exception(client, cookie, person.id, uuid.uuid4())
    )
    assert code_of(as_owner) == NOT_FOUND
    assert row_counts(module_seeded_url, other.id) == (0, 0, 1)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"kind": "available"}, "starts_at"),
        ({"starts_at": "10:00"}, "ends_at"),
        ({"starts_at": "11:00", "ends_at": "10:00"}, "ends_at"),
        ({"ends_on": "2026-12-20"}, "ends_on"),
        ({"starts_on": "2027-01-01", "ends_on": "2028-01-03"}, "ends_on"),
        ({"note": "x" * 201}, "note"),
        ({"kind": "holiday"}, "kind"),
        ({"version": 1}, "version"),
    ],
)
def test_invalid_exceptions_are_422(module_seeded_url, client, tutor, body, field):
    person, cookie = tutor
    before = event_count(module_seeded_url)

    response = add_exception(client, cookie, person.id, **body)

    assert code_of(response) == INVALID
    assert field in {error["path"][1] for error in response.json()["errors"]}
    assert row_counts(module_seeded_url, person.id) == (0, 0, 0)
    assert event_count(module_seeded_url) == before


# --- C4: the check route -------------------------------------------------------------


def test_the_check_route_gives_every_reason(module_seeded_url, client, owner, tutor):
    person, cookie = tutor
    monday = first_monday(unique_year())
    owner_cookie = sign_in(client, owner.username)
    put_weekly(client, cookie, person.id, 0, AFTERNOONS)

    def answer(on: date, start: str, end: str) -> Any:
        response = check(
            client, cookie, person.id, date=on.isoformat(), start=start, end=end
        )
        assert code_of(response) == OK, response.text
        return response.json()

    assert answer(monday, "17:00", "19:00") == {"available": True, "reasons": []}
    assert answer(monday, "15:00", "17:00") == {
        "available": False,
        "reasons": ["outside_weekly_availability"],
    }
    # Time off on the next Monday, extra hours on its Tuesday.
    next_monday = monday + timedelta(days=7)
    add_exception(
        client,
        cookie,
        person.id,
        starts_on=next_monday.isoformat(),
        ends_on=next_monday.isoformat(),
        starts_at="18:00",
        ends_at="19:00",
    )
    add_exception(
        client,
        cookie,
        person.id,
        kind="available",
        starts_on=(next_monday + timedelta(days=1)).isoformat(),
        ends_on=(next_monday + timedelta(days=1)).isoformat(),
        starts_at="10:00",
        ends_at="12:00",
    )
    assert answer(next_monday, "18:30", "19:30") == {
        "available": False,
        "reasons": ["time_off"],
    }
    assert answer(next_monday, "16:00", "18:00") == {"available": True, "reasons": []}
    assert answer(next_monday + timedelta(days=1), "10:00", "12:00") == {
        "available": True,
        "reasons": [],
    }
    # A closure created through #38's API wins over everything.
    create_closure(client, owner_cookie, monday.isoformat())
    assert answer(monday, "17:00", "19:00") == {
        "available": False,
        "reasons": ["center_closed"],
    }
    create_closure(client, owner_cookie, next_monday.isoformat())
    assert answer(next_monday, "18:30", "21:00") == {
        "available": False,
        "reasons": ["center_closed", "time_off", "outside_weekly_availability"],
    }


@pytest.mark.parametrize(
    "params",
    [
        {"date": "2026-10-19", "start": "18:00", "end": "18:00"},
        {"date": "2026-10-19", "start": "19:00", "end": "18:00"},
        {"date": "2026-10-19", "start": "8:00", "end": "18:00"},
        {"date": "2026-10-19", "start": "18:00", "end": "24:00"},
        {"date": "19/10/2026", "start": "17:00", "end": "18:00"},
        {"start": "17:00", "end": "18:00"},
        {"date": "2026-10-19", "start": "17:00"},
    ],
)
def test_an_invalid_check_is_422_before_404(module_seeded_url, client, tutor, params):
    person, cookie = tutor

    assert code_of(check(client, cookie, person.id, **params)) == INVALID
    assert code_of(check(client, cookie, uuid.uuid4(), **params)) == INVALID


# --- C5: the service functions for M4 ------------------------------------------------


def set_zone(url: str, zone: str) -> None:
    run_sql(url, "UPDATE business_settings SET time_zone = :zone", {"zone": zone})


@contextmanager
def center_zone(url: str, zone: str) -> Iterator[None]:
    [row] = run_sql(url, "SELECT time_zone FROM business_settings")
    set_zone(url, zone)
    try:
        yield
    finally:
        set_zone(url, row.time_zone)


def test_is_tutor_available_at_splits_at_local_midnight_in_the_center_zone(
    module_seeded_url, client, owner, tutor
):
    person, cookie = tutor
    monday = first_monday(unique_year())
    # Monday 22:00-23:59 and Tuesday 00:00-02:00, local time.
    put_weekly(
        client,
        cookie,
        person.id,
        0,
        [window(1, "22:00", "23:59"), window(2, "00:00", "02:00")],
    )

    def at(start: datetime, end: datetime) -> AvailabilityResult:
        return in_uow(
            module_seeded_url,
            lambda session: service.is_tutor_available_at(
                session, person.id, start, end
            ),
        )

    # Pacific/Kiritimati is UTC+14: Monday 22:30 local is 08:30Z on Monday.
    starts = datetime.combine(monday, time(8, 30), tzinfo=UTC)
    with center_zone(module_seeded_url, "Pacific/Kiritimati"):
        assert at(starts, starts + timedelta(minutes=60)) == AvailabilityResult(
            True, ()
        )
        # Monday 23:30 to Tuesday 00:30: the piece to midnight is never
        # covered (windows end by 23:59).
        assert at(starts + timedelta(hours=1), starts + timedelta(hours=2)) == (
            AvailabilityResult(False, ("outside_weekly_availability",))
        )
        # Tuesday 00:00-01:00 local is Monday 10:00Z.
        tuesday = datetime.combine(monday, time(10), tzinfo=UTC)
        assert at(tuesday, tuesday + timedelta(hours=1)) == AvailabilityResult(True, ())
        # A closure on the local Tuesday only blocks the Tuesday piece.
        create_closure(
            client,
            sign_in(client, owner.username),
            (monday + timedelta(days=1)).isoformat(),
        )
        assert at(tuesday, tuesday + timedelta(hours=1)) == AvailabilityResult(
            False, ("center_closed",)
        )
        assert at(starts, starts + timedelta(minutes=60)) == AvailabilityResult(
            True, ()
        )
    # In UTC the same instants are Monday 08:30: outside the windows.
    assert at(starts, starts + timedelta(minutes=60)) == AvailabilityResult(
        False, ("outside_weekly_availability",)
    )


def test_is_tutor_available_reads_local_dates(module_seeded_url, client, tutor):
    person, cookie = tutor
    monday = first_monday(unique_year())
    put_weekly(client, cookie, person.id, 0, AFTERNOONS)
    add_exception(
        client,
        cookie,
        person.id,
        starts_on=monday.isoformat(),
        ends_on=monday.isoformat(),
        starts_at="16:00",
        ends_at="17:00",
    )

    def local(on: date, start: str, end: str) -> AvailabilityResult:
        return in_uow(
            module_seeded_url,
            lambda session: service.is_tutor_available(
                session,
                person.id,
                on,
                time.fromisoformat(start),
                time.fromisoformat(end),
            ),
        )

    assert local(monday, "17:00", "20:00") == AvailabilityResult(True, ())
    assert local(monday, "16:30", "17:30") == AvailabilityResult(False, ("time_off",))
    assert local(monday + timedelta(days=7), "16:30", "17:30") == AvailabilityResult(
        True, ()
    )
    with pytest.raises(ValueError, match="start must be before end"):
        local(monday, "17:00", "17:00")


# --- D: the impact seam --------------------------------------------------------------


class FixedImpact:
    """An injected source that reports ``affected`` for every question."""

    def __init__(self, affected: list[uuid.UUID]) -> None:
        self.affected = affected
        self.calls: list[tuple[list[WeeklyWindow], list[DatedException]]] = []

    async def affected_session_ids(
        self,
        session: Any,
        tutor_user_id: uuid.UUID,
        *,
        removed_windows: Sequence[WeeklyWindow],
        added_unavailability: Sequence[DatedException],
    ) -> list[uuid.UUID]:
        self.calls.append((list(removed_windows), list(added_unavailability)))
        return self.affected


@contextmanager
def impact(client: TestClient, affected: list[uuid.UUID]) -> Iterator[FixedImpact]:
    source = FixedImpact(affected)
    app = client.app
    app.dependency_overrides[get_availability_impact_source] = lambda: source  # type: ignore[attr-defined]
    try:
        yield source
    finally:
        app.dependency_overrides.pop(get_availability_impact_source, None)  # type: ignore[attr-defined]


def test_a_change_that_affects_sessions_needs_approval_and_writes_nothing(
    module_seeded_url, client, tutor
):
    person, cookie = tutor
    put_weekly(client, cookie, person.id, 0, AFTERNOONS)
    extra = add_exception(
        client, cookie, person.id, kind="available", starts_at="10:00", ends_at="12:00"
    ).json()
    before = (row_counts(module_seeded_url, person.id), event_count(module_seeded_url))

    with impact(client, [uuid.uuid4()]) as source:
        shrunk = put_weekly(client, cookie, person.id, 1, [window(1, "17:00", "20:00")])
        time_off = add_exception(
            client, cookie, person.id, starts_at="09:00", ends_at="10:00"
        )
        removed = delete_exception(client, cookie, person.id, extra["id"])

    assert [code_of(r) for r in (shrunk, time_off, removed)] == [REQUIRES_APPROVAL] * 3
    assert (
        row_counts(module_seeded_url, person.id),
        event_count(module_seeded_url),
    ) == before
    assert detail(client, cookie, person.id)["version"] == 1
    # What the source was asked.
    [weekly_call, off_call, delete_call] = source.calls
    assert weekly_call == (
        [
            WeeklyWindow(1, time(16), time(17)),
            WeeklyWindow(3, time(16), time(18)),
        ],
        [],
    )
    assert off_call[0] == []
    assert [(e.kind, e.starts_at, e.ends_at) for e in off_call[1]] == [
        ("unavailable", time(9), time(10))
    ]
    assert [(e.kind, e.starts_at, e.ends_at) for e in delete_call[1]] == [
        ("available", time(10), time(12))
    ]


def test_changes_that_take_nothing_away_never_ask_the_source(
    module_seeded_url, client, tutor
):
    person, cookie = tutor
    put_weekly(client, cookie, person.id, 0, AFTERNOONS)
    off = add_exception(client, cookie, person.id).json()

    with impact(client, [uuid.uuid4()]) as source:
        grown = put_weekly(
            client, cookie, person.id, 1, [*AFTERNOONS, window(5, "09:00", "12:00")]
        )
        extra = add_exception(
            client,
            cookie,
            person.id,
            kind="available",
            starts_at="10:00",
            ends_at="12:00",
        )
        removed_off = delete_exception(client, cookie, person.id, off["id"])

    assert code_of(grown) == OK
    assert code_of(extra) == CREATED
    assert code_of(removed_off) == NO_CONTENT
    assert source.calls == []


def test_with_the_default_source_changes_apply(module_seeded_url, client, tutor):
    person, cookie = tutor
    put_weekly(client, cookie, person.id, 0, AFTERNOONS)
    extra = add_exception(
        client, cookie, person.id, kind="available", starts_at="10:00", ends_at="12:00"
    ).json()

    assert code_of(put_weekly(client, cookie, person.id, 1, [])) == OK
    assert code_of(add_exception(client, cookie, person.id)) == CREATED
    assert (
        code_of(delete_exception(client, cookie, person.id, extra["id"])) == NO_CONTENT
    )
    assert row_counts(module_seeded_url, person.id) == (1, 0, 1)


# --- E1: audit -----------------------------------------------------------------------


def test_weekly_changes_and_exceptions_are_audited(module_seeded_url, client, tutor):
    person, cookie = tutor

    first = put_weekly(client, cookie, person.id, 0, AFTERNOONS)
    [settings] = events(module_seeded_url, record_type="tutor_availability_settings")[
        -1:
    ]
    assert settings.action == "create"
    assert settings.actor_user_id == person.id
    assert settings.request_id == first.headers["X-Request-ID"]
    created_windows = [
        e
        for e in events(module_seeded_url, record_type="tutor_availability_windows")
        if e.request_id == first.headers["X-Request-ID"]
    ]
    assert [e.action for e in created_windows] == ["create", "create"]
    assert {e.actor_user_id for e in created_windows} == {person.id}

    # One window changed: a delete plus a create; the unchanged one stays.
    second = put_weekly(
        client, cookie, person.id, 1, [AFTERNOONS[0], window(3, "16:00", "19:00")]
    )
    changed = [
        e
        for e in events(module_seeded_url, record_type="tutor_availability_windows")
        if e.request_id == second.headers["X-Request-ID"]
    ]
    assert [e.action for e in changed] == ["delete", "create"]
    assert changed[0].changes["ends_at"] == {"old": "18:00:00", "new": None}
    assert changed[1].changes["ends_at"] == {"old": None, "new": "19:00:00"}
    # The settings row only had its version bumped: no event (#22).
    assert [
        e
        for e in events(module_seeded_url, record_type="tutor_availability_settings")
        if e.request_id == second.headers["X-Request-ID"]
    ] == []

    added = add_exception(client, cookie, person.id, note="Vacaciones")
    removed = delete_exception(client, cookie, person.id, added.json()["id"])
    exception_events = events(
        module_seeded_url,
        record_type="tutor_availability_exceptions",
        record_id=uuid.UUID(added.json()["id"]),
    )
    assert [e.action for e in exception_events] == ["create", "delete"]
    assert exception_events[0].changes["note"] == {"old": None, "new": "Vacaciones"}
    assert [e.request_id for e in exception_events] == [
        added.headers["X-Request-ID"],
        removed.headers["X-Request-ID"],
    ]
    assert {e.actor_user_id for e in exception_events} == {person.id}
