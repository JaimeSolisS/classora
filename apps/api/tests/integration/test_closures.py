"""Center closures over HTTP and through the service (#38 C, D1).

Most tests share one seeded database and use a decade of their own
(``unique_year``); the time-zone tests and the exact-list tests use a fresh
database.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import OffsetParams
from app.modules.catalog import service
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
    CLOSURE_CHECK,
    CLOSURES,
    closure_path,
    count_statements,
    create_closure,
    get,
    patch,
    post,
    range_event_count,
    unique_year,
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


def ids(response) -> list[str]:
    assert code_of(response) == (200, None), response.text
    return [item["id"] for item in response.json()["items"]]


def check(client: TestClient, cookie: Any, day: str) -> dict[str, Any]:
    response = get(client, cookie, CLOSURE_CHECK, date=day)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- C1, C2: reads -------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [STUDENT, GUARDIAN, TUTOR, ACADEMIC_COORDINATOR, FINANCE_STAFF, ADMINISTRATOR],
)
def test_every_signed_in_role_can_list_read_and_check(
    module_seeded_url, client, cookie, role
):
    year = unique_year()
    closure = create_closure(client, cookie, f"{year}-12-25", name="Navidad")
    reader = create_user(module_seeded_url, roles=(role,))
    reader_cookie = sign_in(client, reader.username)

    listed = get(
        client,
        reader_cookie,
        CLOSURES,
        **{"from": f"{year}-01-01", "to": f"{year}-12-31"},
    )

    assert listed.json()["items"] == [closure]
    assert get(client, reader_cookie, closure_path(closure["id"])).json() == closure
    assert check(client, reader_cookie, f"{year}-12-25") == {
        "date": f"{year}-12-25",
        "closed": True,
        "closures": [closure],
    }


def test_the_detail_has_exactly_the_documented_fields(client, cookie):
    year = unique_year()

    created = create_closure(
        client, cookie, f"{year}-12-20", f"{year + 1}-01-06", "vacation", "Invierno"
    )

    assert set(created) == {
        "id",
        "name",
        "closure_type",
        "starts_on",
        "ends_on",
        "is_active",
        "version",
        "created_at",
        "updated_at",
    }
    assert (
        created["name"],
        created["closure_type"],
        created["starts_on"],
        created["ends_on"],
        created["is_active"],
        created["version"],
    ) == ("Invierno", "vacation", f"{year}-12-20", f"{year + 1}-01-06", True, 1)


def test_an_anonymous_caller_gets_401(client):
    client.cookies.clear()
    assert code_of(client.get(CLOSURES)) == (401, "authentication_required")
    assert code_of(client.get(CLOSURE_CHECK, params={"date": "2026-12-25"})) == (
        401,
        "authentication_required",
    )


def test_an_unknown_closure_is_404_and_an_inactive_one_is_returned(client, cookie):
    closure = create_closure(client, cookie, f"{unique_year()}-05-01")
    patch(
        client, cookie, closure_path(closure["id"]), {"version": 1, "is_active": False}
    )

    assert code_of(get(client, cookie, closure_path(uuid.uuid4()))) == (
        404,
        "not_found",
    )
    detail = get(client, cookie, closure_path(closure["id"])).json()
    assert (detail["is_active"], detail["version"]) == (False, 2)


def test_filters_and_order(fresh):
    _, fresh_client, owner_cookie = fresh
    winter = create_closure(
        fresh_client, owner_cookie, "2026-12-20", "2027-01-06", "vacation", "Invierno"
    )
    christmas = create_closure(fresh_client, owner_cookie, "2026-12-25", name="Navidad")
    long_close = create_closure(
        fresh_client, owner_cookie, "2026-12-25", "2026-12-31", "closure", "Obra"
    )
    may = create_closure(fresh_client, owner_cookie, "2027-05-01", name="Trabajo")
    retired = create_closure(fresh_client, owner_cookie, "2026-09-16", name="Viejo")
    patch(
        fresh_client,
        owner_cookie,
        closure_path(retired["id"]),
        {"version": 1, "is_active": False},
    )

    def listed(**params: Any) -> list[str]:
        return ids(get(fresh_client, owner_cookie, CLOSURES, **params))

    # starts_on, ends_on, id.
    in_order = [winter["id"], christmas["id"], long_close["id"], may["id"]]
    assert listed() == in_order
    assert listed(status="inactive") == [retired["id"]]
    assert listed(status="all") == [retired["id"], *in_order]
    assert listed(closure_type="holiday") == [christmas["id"], may["id"]]
    assert listed(closure_type="vacation", status="all") == [winter["id"]]
    # from/to: overlap with [from, to], inclusive at both edges.
    assert listed(**{"from": "2027-01-06"}) == [winter["id"], may["id"]]
    assert listed(**{"from": "2027-01-07"}) == [may["id"]]
    assert listed(to="2026-12-20") == [winter["id"]]
    assert listed(to="2026-12-19") == []
    assert listed(**{"from": "2026-12-31", "to": "2026-12-31"}) == [
        winter["id"],
        long_close["id"],
    ]
    assert listed(**{"from": "2027-01-01", "to": "2027-12-31"}) == [
        winter["id"],
        may["id"],
    ]
    page = get(fresh_client, owner_cookie, CLOSURES, limit=2, offset=1).json()
    assert [item["id"] for item in page["items"]] == in_order[1:3]
    assert (page["total"], page["limit"], page["offset"]) == (4, 2, 1)


@pytest.mark.parametrize(
    "params",
    [
        {"status": "deleted"},
        {"closure_type": "bridge"},
        {"from": "2026-02-30"},
        {"to": "26-12-31"},
        {"from": "2026-12-31", "to": "2026-12-30"},
        {"offset": -1},
    ],
)
def test_invalid_list_queries_are_422(client, cookie, params):
    assert code_of(get(client, cookie, CLOSURES, **params)) == (422, "validation_error")


def test_from_after_to_names_the_to_parameter(client, cookie):
    response = get(
        client, cookie, CLOSURES, **{"from": "2026-12-31", "to": "2026-01-01"}
    )

    assert [e["path"] for e in response.json()["errors"]] == [["query", "to"]]


# --- C2: create and update -----------------------------------------------------------


def _body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Navidad",
        "closure_type": "holiday",
        "starts_on": "2026-12-25",
        "ends_on": "2026-12-25",
    }
    body.update(changes)
    return {key: value for key, value in body.items() if value is not ...}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (_body(name=...), "name"),
        (_body(name=" "), "name"),
        (_body(name="x" * 101), "name"),
        (_body(closure_type="bridge"), "closure_type"),
        (_body(starts_on="2026-12-32"), "starts_on"),
        (_body(ends_on=...), "ends_on"),
        (_body(ends_on="2026-12-24"), "ends_on"),
        # 367 days apart.
        (_body(starts_on="2026-01-01", ends_on="2027-01-03"), "ends_on"),
        (_body(is_active=True), "is_active"),
    ],
)
def test_invalid_create_bodies_are_422_and_write_nothing(
    module_seeded_url, client, cookie, body, field
):
    before = range_event_count(module_seeded_url)

    response = post(client, cookie, CLOSURES, body)

    assert code_of(response) == (422, "validation_error")
    assert field in {str(error["path"][1]) for error in response.json()["errors"]}
    assert range_event_count(module_seeded_url) == before


def test_names_repeat_and_closures_overlap(client, cookie):
    year = unique_year()

    first = create_closure(client, cookie, f"{year}-12-25", name="Navidad")
    second = create_closure(client, cookie, f"{year}-12-25", name="Navidad")
    vacation = create_closure(
        client, cookie, f"{year}-12-20", f"{year + 1}-12-21", "vacation", "Año"
    )

    assert len({first["id"], second["id"], vacation["id"]}) == 3


def test_edit_deactivate_and_reactivate(module_seeded_url, client, cookie):
    year = unique_year()
    closure = create_closure(client, cookie, f"{year}-09-16")
    path = closure_path(closure["id"])

    edited = patch(
        client,
        cookie,
        path,
        {
            "version": 1,
            "name": "Independencia",
            "closure_type": "closure",
            "starts_on": f"{year}-09-15",
            "ends_on": f"{year}-09-16",
        },
    )
    deactivated = patch(client, cookie, path, {"version": 2, "is_active": False})
    reactivated = patch(client, cookie, path, {"version": 3, "is_active": True})

    assert [r.status_code for r in (edited, deactivated, reactivated)] == [200] * 3
    body = reactivated.json()
    assert (
        body["name"],
        body["closure_type"],
        body["starts_on"],
        body["ends_on"],
        body["is_active"],
        body["version"],
    ) == ("Independencia", "closure", f"{year}-09-15", f"{year}-09-16", True, 4)


def test_check_order_and_no_op(module_seeded_url, client, cookie):
    closure = create_closure(client, cookie, f"{unique_year()}-05-01")
    path = closure_path(closure["id"])
    before = range_event_count(module_seeded_url)

    unknown = patch(client, cookie, closure_path(uuid.uuid4()), {"version": 9})
    stale = patch(client, cookie, path, {"version": 9, "ends_on": "1999-01-01"})
    broken = patch(client, cookie, path, {"version": 1, "ends_on": "1999-01-01"})
    same = patch(
        client,
        cookie,
        path,
        {
            "version": 1,
            "name": closure["name"],
            "closure_type": closure["closure_type"],
            "starts_on": closure["starts_on"],
            "is_active": True,
        },
    )

    assert code_of(unknown) == (404, "not_found")
    assert code_of(stale) == (409, "version_conflict")
    assert code_of(broken) == (422, "validation_error")
    assert broken.json()["errors"][0]["path"] == ["body", "ends_on"]
    assert same.status_code == 200
    assert same.json()["version"] == 1
    assert range_event_count(module_seeded_url) == before


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x"},
        {"version": 1, "closure_type": None},
        {"version": 1, "closure_type": "bridge"},
        {"version": 1, "starts_on": "2026-12-26", "ends_on": "2026-12-25"},
        {"version": 1, "id": "x"},
    ],
)
def test_invalid_update_bodies_are_422(client, cookie, body):
    closure = create_closure(client, cookie, f"{unique_year()}-05-01")

    response = patch(client, cookie, closure_path(closure["id"]), body)

    assert code_of(response) == (422, "validation_error")


# --- C3, C6: check -------------------------------------------------------------------


def test_check_with_zero_one_and_two_closures(client, cookie):
    year = unique_year()
    vacation = create_closure(
        client, cookie, f"{year}-12-20", f"{year + 1}-01-06", "vacation", "Invierno"
    )
    christmas = create_closure(client, cookie, f"{year}-12-25", name="Navidad")

    assert check(client, cookie, f"{year}-12-19") == {
        "date": f"{year}-12-19",
        "closed": False,
        "closures": [],
    }
    assert check(client, cookie, f"{year}-12-24")["closures"] == [vacation]
    two = check(client, cookie, f"{year}-12-25")
    assert (two["closed"], two["closures"]) == (True, [vacation, christmas])
    assert check(client, cookie, f"{year + 1}-01-06")["closed"] is True
    assert check(client, cookie, f"{year + 1}-01-07")["closed"] is False


@pytest.mark.parametrize("params", [{}, {"date": "2026-02-29"}, {"date": "tomorrow"}])
def test_check_needs_a_valid_date(client, cookie, params):
    response = get(client, cookie, CLOSURE_CHECK, **params)

    assert code_of(response) == (422, "validation_error")


def test_an_inactive_closure_never_counts(module_seeded_url, client, cookie):
    year = unique_year()
    closure = create_closure(client, cookie, f"{year}-12-25")
    patch(
        client, cookie, closure_path(closure["id"]), {"version": 1, "is_active": False}
    )
    day = date(year, 12, 25)

    assert check(client, cookie, f"{year}-12-25")["closed"] is False
    assert (
        in_uow(module_seeded_url, lambda s: service.is_center_closed_on(s, day))
        is False
    )
    assert (
        in_uow(
            module_seeded_url,
            lambda s: service.is_center_closed_at(
                s, datetime(year, 12, 25, 18, 0, tzinfo=UTC)
            ),
        )
        is False
    )
    assert (
        in_uow(module_seeded_url, lambda s: service.closures_between(s, day, day)) == []
    )


# --- C4, C5: the service and local-date boundaries -----------------------------------


def test_closures_between_is_inclusive_and_active_only(
    module_seeded_url, client, cookie
):
    year = unique_year()
    first = create_closure(client, cookie, f"{year}-03-01", f"{year}-03-03")
    second = create_closure(client, cookie, f"{year}-03-05")
    retired = create_closure(client, cookie, f"{year}-03-04")
    patch(
        client, cookie, closure_path(retired["id"]), {"version": 1, "is_active": False}
    )

    def between(start: date, end: date) -> list[str]:
        found = in_uow(
            module_seeded_url, lambda s: service.closures_between(s, start, end)
        )
        return [str(closure.id) for closure in found]

    assert between(date(year, 3, 3), date(year, 3, 5)) == [first["id"], second["id"]]
    assert between(date(year, 3, 4), date(year, 3, 4)) == []
    assert between(date(year, 2, 1), date(year, 3, 1)) == [first["id"]]
    assert between(date(year, 3, 6), date(year, 12, 31)) == []
    with pytest.raises(ValueError):
        between(date(year, 3, 5), date(year, 3, 4))


def test_closures_between_takes_one_statement(module_seeded_url, client, cookie):
    year = unique_year()
    create_closure(client, cookie, f"{year}-03-01")

    statements, found = count_statements(
        module_seeded_url,
        lambda s: service.closures_between(s, date(year, 1, 1), date(year, 12, 31)),
    )

    assert (statements, len(found)) == (1, 1)


def test_the_list_takes_two_statements(module_seeded_url):
    statements, _ = count_statements(
        module_seeded_url,
        lambda s: service.list_closures(s, page=OffsetParams(limit=25, offset=0)),
    )

    assert statements == 2


def test_a_naive_instant_is_refused(module_seeded_url):
    with pytest.raises(ValueError):
        in_uow(
            module_seeded_url,
            lambda s: service.is_center_closed_at(s, datetime(2026, 12, 25, 12, 0)),
        )


def _closed_at(url: str, instant: str) -> bool:
    moment = datetime.fromisoformat(instant)
    return in_uow(url, lambda s: service.is_center_closed_at(s, moment))


def test_local_date_boundaries_in_the_center_time_zone(fresh):
    # Default zone America/Mexico_City (UTC-6 all year): a closure on
    # 2026-12-25 covers 2026-12-25T06:00Z up to 2026-12-26T06:00Z.
    url, fresh_client, owner_cookie = fresh
    create_closure(fresh_client, owner_cookie, "2026-12-25", name="Navidad")
    create_closure(fresh_client, owner_cookie, "2026-12-30", "2027-01-01", "vacation")

    assert _closed_at(url, "2026-12-25T05:59:59+00:00") is False
    assert _closed_at(url, "2026-12-25T06:00:00+00:00") is True
    assert _closed_at(url, "2026-12-26T05:59:59+00:00") is True
    assert _closed_at(url, "2026-12-26T06:00:00+00:00") is False
    # Another offset for the same instant gives the same answer.
    assert _closed_at(url, "2026-12-25T00:00:00-06:00") is True
    assert check(fresh_client, owner_cookie, "2027-01-01")["closed"] is True
    assert check(fresh_client, owner_cookie, "2027-01-02")["closed"] is False
    assert in_uow(url, lambda s: service.is_center_closed_on(s, date(2027, 1, 1)))


def test_the_center_time_zone_decides_the_local_date(fresh):
    # Pacific/Kiritimati is UTC+14: 2026-12-24T10:00Z is already the 25th.
    url, fresh_client, owner_cookie = fresh
    create_closure(fresh_client, owner_cookie, "2026-12-25", name="Navidad")
    instant = "2026-12-24T10:00:00+00:00"
    assert _closed_at(url, instant) is False

    run_sql(url, "UPDATE business_settings SET time_zone = 'Pacific/Kiritimati'")

    assert (
        datetime.fromisoformat(instant).astimezone(ZoneInfo("Pacific/Kiritimati")).day
        == 25
    )
    assert _closed_at(url, instant) is True
    assert _closed_at(url, "2026-12-25T10:00:00+00:00") is False


# --- D1: audit -----------------------------------------------------------------------


def test_create_date_change_and_deactivation_are_audited(
    module_seeded_url, client, cookie, owner
):
    year = unique_year()
    created = post(
        client,
        cookie,
        CLOSURES,
        {
            "name": "Navidad",
            "closure_type": "holiday",
            "starts_on": f"{year}-12-25",
            "ends_on": f"{year}-12-25",
        },
    )
    closure_id = created.json()["id"]
    moved = patch(
        client,
        cookie,
        closure_path(closure_id),
        {"version": 1, "starts_on": f"{year}-12-24", "ends_on": f"{year}-12-26"},
    )
    deactivated = patch(
        client, cookie, closure_path(closure_id), {"version": 2, "is_active": False}
    )

    recorded = events(
        module_seeded_url, record_type="closures", record_id=uuid.UUID(closure_id)
    )

    assert [e.action for e in recorded] == ["create", "update", "update"]
    assert {e.actor_user_id for e in recorded} == {owner.id}
    assert [e.request_id for e in recorded] == [
        r.headers["X-Request-ID"] for r in (created, moved, deactivated)
    ]
    assert recorded[0].changes == {
        "name": {"old": None, "new": "Navidad"},
        "closure_type": {"old": None, "new": "holiday"},
        "starts_on": {"old": None, "new": f"{year}-12-25"},
        "ends_on": {"old": None, "new": f"{year}-12-25"},
        "is_active": {"old": None, "new": True},
    }
    assert recorded[1].changes == {
        "starts_on": {"old": f"{year}-12-25", "new": f"{year}-12-24"},
        "ends_on": {"old": f"{year}-12-25", "new": f"{year}-12-26"},
    }
    assert recorded[2].changes == {"is_active": {"old": True, "new": False}}


@pytest.mark.parametrize("case", ["forbidden", "not_found", "conflict", "invalid"])
def test_failed_changes_write_no_audit_event(module_seeded_url, client, cookie, case):
    closure = create_closure(client, cookie, f"{unique_year()}-05-01")
    actor_cookie = cookie
    target: Any = closure["id"]
    body: dict[str, Any] = {"version": 1, "name": "Otro"}
    if case == "forbidden":
        coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
        actor_cookie = sign_in(client, coordinator.username)
    elif case == "not_found":
        target = uuid.uuid4()
    elif case == "conflict":
        body["version"] = 7
    elif case == "invalid":
        body["closure_type"] = "bridge"
    before = range_event_count(module_seeded_url)

    response = patch(client, actor_cookie, closure_path(target), body)

    assert response.status_code in (403, 404, 409, 422)
    assert range_event_count(module_seeded_url) == before


@pytest.mark.parametrize("role", [TUTOR, GUARDIAN, FINANCE_STAFF])
def test_other_roles_cannot_create(module_seeded_url, client, role):
    person = create_user(module_seeded_url, roles=(role,))
    person_cookie = sign_in(client, person.username)

    response = post(client, person_cookie, CLOSURES, _body())

    assert code_of(response) == (403, "permission_denied")
