"""Academic periods over HTTP with real logins (#38 B, D1).

Most tests share one seeded database and use a year of their own
(``unique_year``), so their overlap checks and ``on`` filters never meet
another test's rows; the tests that need the exact list use a fresh database.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

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
    PERIODS,
    create_period,
    get,
    patch,
    period_path,
    post,
    range_event_count,
    unique_name,
    unique_year,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events


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


def school_year(year: int) -> tuple[str, str]:
    """Aug 1 of ``year`` to Jul 15 of the next year."""
    return f"{year}-08-01", f"{year + 1}-07-15"


def names(response) -> list[str]:
    assert code_of(response) == (200, None), response.text
    return [item["name"] for item in response.json()["items"]]


def period_row(url: str, period_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT name, period_type, starts_on, ends_on, is_active, version"
        " FROM academic_periods WHERE id = :id",
        {"id": period_id},
    )
    return row


# --- B1, B2: reads --------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [STUDENT, GUARDIAN, TUTOR, ACADEMIC_COORDINATOR, FINANCE_STAFF, ADMINISTRATOR],
)
def test_every_signed_in_role_can_list_and_read_periods(
    module_seeded_url, client, cookie, role
):
    year = unique_year()
    period = create_period(client, cookie, *school_year(year))
    reader = create_user(module_seeded_url, roles=(role,))
    reader_cookie = sign_in(client, reader.username)

    listed = get(client, reader_cookie, PERIODS, on=f"{year}-09-01")

    assert listed.json()["items"] == [period]
    assert get(client, reader_cookie, period_path(period["id"])).json() == period


def test_the_detail_has_exactly_the_documented_fields(client, cookie):
    starts_on, ends_on = school_year(unique_year())
    name = unique_name("Ciclo")

    created = create_period(client, cookie, starts_on, ends_on, name=name)

    assert set(created) == {
        "id",
        "name",
        "period_type",
        "starts_on",
        "ends_on",
        "is_active",
        "version",
        "created_at",
        "updated_at",
    }
    assert (
        created["name"],
        created["period_type"],
        created["starts_on"],
        created["ends_on"],
        created["is_active"],
        created["version"],
    ) == (name, "school_year", starts_on, ends_on, True, 1)


def test_an_anonymous_caller_gets_401(client):
    client.cookies.clear()
    assert code_of(client.get(PERIODS)) == (401, "authentication_required")


def test_an_unknown_period_is_404_and_an_inactive_one_is_returned(client, cookie):
    period = create_period(client, cookie, *school_year(unique_year()))
    patch(client, cookie, period_path(period["id"]), {"version": 1, "is_active": False})

    assert code_of(get(client, cookie, period_path(uuid.uuid4()))) == (
        404,
        "not_found",
    )
    assert code_of(get(client, cookie, f"{PERIODS}/not-a-uuid")) == (
        422,
        "validation_error",
    )
    detail = get(client, cookie, period_path(period["id"])).json()
    assert (detail["is_active"], detail["version"]) == (False, 2)


def test_filters_and_order(fresh):
    _, fresh_client, owner_cookie = fresh
    create_period(fresh_client, owner_cookie, "2026-08-01", "2027-07-15", name="b 2026")
    create_period(fresh_client, owner_cookie, "2027-08-01", "2028-07-15", name="C 2027")
    create_period(
        fresh_client, owner_cookie, "2026-08-01", "2026-12-20", "semester", "a sem"
    )
    create_period(
        fresh_client, owner_cookie, "2027-01-10", "2027-03-31", "term", "Trimestre"
    )
    retired = create_period(
        fresh_client, owner_cookie, "2025-08-01", "2026-07-15", name="Viejo"
    )
    patch(
        fresh_client,
        owner_cookie,
        period_path(retired["id"]),
        {"version": 1, "is_active": False},
    )

    def listed(**params: Any) -> list[str]:
        return names(get(fresh_client, owner_cookie, PERIODS, **params))

    # starts_on DESC, then lower(name), then id.
    assert listed() == ["C 2027", "Trimestre", "a sem", "b 2026"]
    assert listed(status="active") == listed()
    assert listed(status="inactive") == ["Viejo"]
    assert listed(status="all") == ["C 2027", "Trimestre", "a sem", "b 2026", "Viejo"]
    assert listed(period_type="school_year") == ["C 2027", "b 2026"]
    assert listed(period_type="semester") == ["a sem"]
    assert listed(period_type="term", status="all") == ["Trimestre"]
    # on: the first day, the last day and the day after (inclusive).
    assert listed(on="2026-08-01") == ["a sem", "b 2026"]
    assert listed(on="2027-07-15", period_type="school_year") == ["b 2026"]
    assert listed(on="2027-07-16") == []
    assert listed(on="2026-07-15", status="all") == ["Viejo"]
    page = get(fresh_client, owner_cookie, PERIODS, limit=2, offset=1).json()
    assert [item["name"] for item in page["items"]] == ["Trimestre", "a sem"]
    assert (page["total"], page["limit"], page["offset"]) == (4, 2, 1)


@pytest.mark.parametrize(
    "params",
    [
        {"status": "deleted"},
        {"period_type": "quarter"},
        {"on": "2026-13-01"},
        {"on": "2026-1-1"},
        {"on": "2026-01-01T00:00:00"},
        {"on": "1767225600"},
        {"limit": 0},
    ],
)
def test_invalid_list_queries_are_422(client, cookie, params):
    assert code_of(get(client, cookie, PERIODS, **params)) == (422, "validation_error")


# --- B3, B4: create and update ------------------------------------------------------


def test_create_trims_the_name_and_stores_the_dates(module_seeded_url, client, cookie):
    name = unique_name("Ciclo")
    year = unique_year()

    response = post(
        client,
        cookie,
        PERIODS,
        {
            "name": f"  {name} ",
            "period_type": "term",
            "starts_on": f"{year}-01-01",
            "ends_on": f"{year}-01-01",
        },
    )

    assert response.status_code == 201, response.text
    row = period_row(module_seeded_url, response.json()["id"])
    assert (row.name, row.period_type, str(row.starts_on), str(row.ends_on)) == (
        name,
        "term",
        f"{year}-01-01",
        f"{year}-01-01",
    )
    assert (row.is_active, row.version) == (True, 1)


def _body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Válido",
        "period_type": "school_year",
        "starts_on": "2026-08-01",
        "ends_on": "2027-07-15",
    }
    body.update(changes)
    return {key: value for key, value in body.items() if value is not ...}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (_body(name=...), "name"),
        (_body(name="   "), "name"),
        (_body(name="x" * 101), "name"),
        (_body(period_type="quarter"), "period_type"),
        (_body(period_type=...), "period_type"),
        (_body(starts_on="2026-02-30"), "starts_on"),
        (_body(starts_on="01/08/2026"), "starts_on"),
        (_body(ends_on=None), "ends_on"),
        (_body(ends_on="2026-07-31"), "ends_on"),
        # 731 days apart.
        (_body(starts_on="2026-01-01", ends_on="2028-01-02"), "ends_on"),
        (_body(is_active=False), "is_active"),
    ],
)
def test_invalid_create_bodies_are_422_and_write_nothing(
    module_seeded_url, client, cookie, body, field
):
    before = range_event_count(module_seeded_url)

    response = post(client, cookie, PERIODS, body)

    assert code_of(response) == (422, "validation_error")
    assert field in {str(error["path"][1]) for error in response.json()["errors"]}
    assert range_event_count(module_seeded_url) == before


def test_a_730_day_period_is_accepted(client, cookie):
    year = unique_year()
    period = create_period(client, cookie, f"{year}-01-01", f"{year + 1}-12-31")

    assert period["ends_on"] == f"{year + 1}-12-31"


def test_rename_change_dates_deactivate_and_reactivate(
    module_seeded_url, client, cookie
):
    year = unique_year()
    period = create_period(client, cookie, *school_year(year))
    path = period_path(period["id"])
    new_name = unique_name("Ciclo")

    renamed = patch(client, cookie, path, {"version": 1, "name": new_name})
    moved = patch(
        client,
        cookie,
        path,
        {"version": 2, "starts_on": f"{year}-08-15", "ends_on": f"{year + 1}-07-01"},
    )
    only_end = patch(
        client, cookie, path, {"version": 3, "ends_on": f"{year + 1}-06-30"}
    )
    deactivated = patch(client, cookie, path, {"version": 4, "is_active": False})
    reactivated = patch(client, cookie, path, {"version": 5, "is_active": True})

    assert [
        r.status_code for r in (renamed, moved, only_end, deactivated, reactivated)
    ] == [200] * 5
    assert renamed.json()["name"] == new_name
    assert (moved.json()["starts_on"], moved.json()["ends_on"]) == (
        f"{year}-08-15",
        f"{year + 1}-07-01",
    )
    assert only_end.json()["ends_on"] == f"{year + 1}-06-30"
    assert deactivated.json()["is_active"] is False
    body = reactivated.json()
    assert (body["is_active"], body["version"]) == (True, 6)
    row = period_row(module_seeded_url, period["id"])
    assert (row.name, str(row.starts_on), str(row.ends_on), row.version) == (
        new_name,
        f"{year}-08-15",
        f"{year + 1}-06-30",
        6,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"version": 1},
        {"version": 1, "starts_on": "SAME", "ends_on": "SAME"},
        {"version": 1, "is_active": True},
        {"version": 1, "name": "SAME"},
    ],
)
def test_a_patch_that_changes_nothing_keeps_the_version(
    module_seeded_url, client, cookie, body
):
    period = create_period(client, cookie, *school_year(unique_year()))
    for key in ("starts_on", "ends_on", "name"):
        if body.get(key) == "SAME":
            body = {**body, key: period[key]}
    before = range_event_count(module_seeded_url)

    response = patch(client, cookie, period_path(period["id"]), body)

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert range_event_count(module_seeded_url) == before


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x"},
        {"version": 0},
        {"version": 1, "name": None},
        {"version": 1, "starts_on": None},
        {"version": 1, "is_active": None},
        {"version": 1, "period_type": "semester"},
        {"version": 1, "starts_on": "2026-08-01", "ends_on": "2026-07-31"},
    ],
)
def test_invalid_update_bodies_are_422(client, cookie, body):
    period = create_period(client, cookie, *school_year(unique_year()))

    response = patch(client, cookie, period_path(period["id"]), body)

    assert code_of(response) == (422, "validation_error")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("ends_before_start", "end before the stored start"),
        ("starts_after_end", "start after the stored end"),
        ("too_long", "stored start plus 731 days"),
    ],
)
def test_one_date_that_breaks_the_stored_range_is_422_on_ends_on(
    module_seeded_url, client, cookie, change, reason
):
    year = unique_year()
    period = create_period(client, cookie, *school_year(year))
    body: dict[str, Any] = {"version": 1}
    if change == "ends_before_start":
        body["ends_on"] = f"{year}-07-31"
    elif change == "starts_after_end":
        body["starts_on"] = f"{year + 1}-07-16"
    else:
        body["ends_on"] = f"{year + 2}-08-02"
    before = range_event_count(module_seeded_url)

    response = patch(client, cookie, period_path(period["id"]), body)

    assert code_of(response) == (422, "validation_error"), reason
    [error] = response.json()["errors"]
    assert (error["path"], error["code"]) == (["body", "ends_on"], "date_range_invalid")
    assert period_row(module_seeded_url, period["id"]).version == 1
    assert range_event_count(module_seeded_url) == before


def test_check_order_404_then_version_then_name_then_overlap(client, cookie):
    year = unique_year()
    taken = create_period(client, cookie, *school_year(year))
    other = create_period(client, cookie, *school_year(year + 1))
    path = period_path(other["id"])
    overlapping = {"starts_on": f"{year}-09-01"}

    unknown = patch(
        client,
        cookie,
        period_path(uuid.uuid4()),
        {"version": 7, "name": taken["name"], **overlapping},
    )
    stale = patch(client, cookie, path, {"version": 7, "name": taken["name"]})
    named = patch(
        client, cookie, path, {"version": 1, "name": taken["name"], **overlapping}
    )
    overlap = patch(client, cookie, path, {"version": 1, **overlapping})

    assert code_of(unknown) == (404, "not_found")
    assert code_of(stale) == (409, "version_conflict")
    assert code_of(named) == (409, "name_taken")
    assert code_of(overlap) == (409, "period_overlap")


@pytest.mark.parametrize("variant", ["upper", "inactive"])
def test_a_duplicate_name_is_409_name_taken(module_seeded_url, client, cookie, variant):
    year = unique_year()
    name = unique_name("Ciclo")
    existing = create_period(client, cookie, *school_year(year), name=name)
    if variant == "inactive":
        patch(
            client,
            cookie,
            period_path(existing["id"]),
            {"version": 1, "is_active": False},
        )
    before = range_event_count(module_seeded_url)

    response = post(
        client,
        cookie,
        PERIODS,
        {
            "name": name.upper(),
            "period_type": "term",
            "starts_on": f"{year + 3}-01-01",
            "ends_on": f"{year + 3}-03-01",
        },
    )

    assert code_of(response) == (409, "name_taken")
    assert range_event_count(module_seeded_url) == before


# --- B5: overlap ---------------------------------------------------------------------


def test_overlap_on_create_and_the_rules_scope(module_seeded_url, client, cookie):
    year = unique_year()
    create_period(client, cookie, *school_year(year))
    before = range_event_count(module_seeded_url)

    def attempt(starts_on: str, ends_on: str, period_type: str = "school_year"):
        return post(
            client,
            cookie,
            PERIODS,
            {
                "name": unique_name("Ciclo"),
                "period_type": period_type,
                "starts_on": starts_on,
                "ends_on": ends_on,
            },
        )

    # Sharing only the last day (inclusive bounds).
    assert code_of(attempt(f"{year + 1}-07-15", f"{year + 2}-07-01")) == (
        409,
        "period_overlap",
    )
    assert code_of(attempt(f"{year}-01-01", f"{year}-08-01")) == (
        409,
        "period_overlap",
    )
    assert range_event_count(module_seeded_url) == before
    # Touching ranges and other types are accepted.
    assert attempt(f"{year + 1}-07-16", f"{year + 2}-07-15").status_code == 201
    assert attempt(f"{year}-08-01", f"{year}-12-20", "semester").status_code == 201


def test_overlap_on_a_date_change_and_on_reactivation(client, cookie):
    year = unique_year()
    first = create_period(client, cookie, *school_year(year))
    second = create_period(client, cookie, *school_year(year + 1))

    moved = patch(
        client,
        cookie,
        period_path(second["id"]),
        {"version": 1, "starts_on": f"{year + 1}-07-15"},
    )
    assert code_of(moved) == (409, "period_overlap")

    # A deactivated period can be replaced; reactivating it then overlaps.
    patch(client, cookie, period_path(first["id"]), {"version": 1, "is_active": False})
    replacement = create_period(client, cookie, f"{year}-08-10", f"{year + 1}-07-10")
    reactivated = patch(
        client, cookie, period_path(first["id"]), {"version": 2, "is_active": True}
    )
    assert code_of(reactivated) == (409, "period_overlap")
    # An inactive period's dates may overlap an active one.
    edited = patch(
        client,
        cookie,
        period_path(first["id"]),
        {"version": 2, "name": unique_name("Borrador"), "ends_on": f"{year + 1}-07-12"},
    )
    assert edited.status_code == 200, edited.text
    assert replacement["is_active"] is True


def test_a_period_may_keep_its_own_days_when_its_dates_change(client, cookie):
    year = unique_year()
    period = create_period(client, cookie, *school_year(year))

    response = patch(
        client,
        cookie,
        period_path(period["id"]),
        {"version": 1, "starts_on": f"{year}-07-01"},
    )

    assert response.status_code == 200, response.text


# --- D1: audit -----------------------------------------------------------------------


def test_create_date_change_and_deactivation_are_audited(
    module_seeded_url, client, cookie, owner
):
    year = unique_year()
    name = unique_name("Ciclo")
    created = post(
        client,
        cookie,
        PERIODS,
        {
            "name": name,
            "period_type": "school_year",
            "starts_on": f"{year}-08-01",
            "ends_on": f"{year + 1}-07-15",
        },
    )
    period_id = created.json()["id"]
    moved = patch(
        client,
        cookie,
        period_path(period_id),
        {"version": 1, "ends_on": f"{year + 1}-07-10"},
    )
    deactivated = patch(
        client, cookie, period_path(period_id), {"version": 2, "is_active": False}
    )

    recorded = events(
        module_seeded_url,
        record_type="academic_periods",
        record_id=uuid.UUID(period_id),
    )

    assert [e.action for e in recorded] == ["create", "update", "update"]
    assert {e.actor_user_id for e in recorded} == {owner.id}
    assert [e.request_id for e in recorded] == [
        r.headers["X-Request-ID"] for r in (created, moved, deactivated)
    ]
    assert recorded[0].changes == {
        "name": {"old": None, "new": name},
        "period_type": {"old": None, "new": "school_year"},
        "starts_on": {"old": None, "new": f"{year}-08-01"},
        "ends_on": {"old": None, "new": f"{year + 1}-07-15"},
        "is_active": {"old": None, "new": True},
    }
    assert recorded[1].changes == {
        "ends_on": {"old": f"{year + 1}-07-15", "new": f"{year + 1}-07-10"}
    }
    assert recorded[2].changes == {"is_active": {"old": True, "new": False}}
    assert all(e.details is None for e in recorded)


@pytest.mark.parametrize(
    "case", ["no_op", "forbidden", "not_found", "conflict", "invalid", "overlap"]
)
def test_failed_or_empty_changes_write_no_audit_event(
    module_seeded_url, client, cookie, case
):
    year = unique_year()
    create_period(client, cookie, *school_year(year))
    period = create_period(client, cookie, *school_year(year + 1))
    actor_cookie = cookie
    target: Any = period["id"]
    body: dict[str, Any] = {"version": 1, "name": unique_name("Ciclo")}
    if case == "no_op":
        body = {"version": 1}
    elif case == "forbidden":
        tutor = create_user(module_seeded_url, roles=(TUTOR,))
        actor_cookie = sign_in(client, tutor.username)
    elif case == "not_found":
        target = uuid.uuid4()
    elif case == "conflict":
        body["version"] = 7
    elif case == "invalid":
        body["name"] = ""
    elif case == "overlap":
        body["starts_on"] = f"{year + 1}-07-01"
    before = range_event_count(module_seeded_url)

    response = patch(client, actor_cookie, period_path(target), body)

    assert response.status_code in (200, 403, 404, 409, 422)
    assert range_event_count(module_seeded_url) == before


@pytest.mark.parametrize("role", [TUTOR, ACADEMIC_COORDINATOR, FINANCE_STAFF])
def test_other_roles_cannot_create(module_seeded_url, client, role):
    person = create_user(module_seeded_url, roles=(role,))
    person_cookie = sign_in(client, person.username)

    response = post(client, person_cookie, PERIODS, _body(name=unique_name()))

    assert code_of(response) == (403, "permission_denied")


def test_an_administrator_can_create_and_edit(module_seeded_url, client):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)
    period = create_period(client, admin_cookie, *school_year(unique_year()))

    response = patch(
        client,
        admin_cookie,
        period_path(period["id"]),
        {"version": 1, "name": unique_name()},
    )

    assert response.status_code == 200, response.text
