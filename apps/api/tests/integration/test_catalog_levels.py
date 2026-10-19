"""Education levels over HTTP with real logins (#32 C1-C3, C6, C7, D, F2).

Most tests share one seeded database and scope their rows with unique names;
the tests that need the exact list (filters, order, pagination, default
positions) use a fresh database of their own.
"""

import uuid
from collections.abc import Iterator
from typing import Any

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
    LEVELS,
    catalog_event_count,
    count_statements,
    create_level,
    create_subject,
    get,
    level_path,
    level_row,
    patch,
    post,
    subject_path,
    unique_name,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events

SEEDED_NAMES = ["Primaria", "Secundaria", "Preparatoria", "Universidad"]


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
    """A database of this test only (the four seeded levels), its client and
    a signed-in Owner."""
    with auth_client(seeded_url) as fresh_client:
        person = create_user(seeded_url, roles=(OWNER,))
        yield seeded_url, fresh_client, sign_in(fresh_client, person.username)


def names(response) -> list[str]:
    assert code_of(response) == (200, None), response.text
    return [item["name"] for item in response.json()["items"]]


# --- C: reads -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [STUDENT, GUARDIAN, TUTOR, ACADEMIC_COORDINATOR, FINANCE_STAFF, ADMINISTRATOR],
)
def test_every_signed_in_role_can_list_and_read_levels(module_seeded_url, client, role):
    reader = create_user(module_seeded_url, roles=(role,))
    reader_cookie = sign_in(client, reader.username)

    listed = get(client, reader_cookie, LEVELS, limit=100)

    assert set(SEEDED_NAMES) <= set(names(listed))
    level = next(i for i in listed.json()["items"] if i["name"] == "Primaria")
    assert get(client, reader_cookie, level_path(level["id"])).json() == level


def test_the_detail_has_exactly_the_documented_fields(client, cookie):
    created = create_level(client, cookie, sort_order=9000)

    assert set(created) == {
        "id",
        "name",
        "sort_order",
        "is_active",
        "active_subject_count",
        "version",
        "created_at",
        "updated_at",
    }
    assert created["is_active"] is True
    assert created["version"] == 1
    assert created["active_subject_count"] == 0
    assert "seed_key" not in get(client, cookie, level_path(created["id"])).text


def test_an_anonymous_caller_gets_401(client):
    client.cookies.clear()
    assert code_of(client.get(LEVELS)) == (401, "authentication_required")


def test_status_filter(fresh):
    url, fresh_client, owner_cookie = fresh
    extra = create_level(fresh_client, owner_cookie, "Posgrado")
    patch(
        fresh_client,
        owner_cookie,
        level_path(extra["id"]),
        {"version": 1, "is_active": False},
    )

    assert names(get(fresh_client, owner_cookie, LEVELS)) == SEEDED_NAMES
    assert names(get(fresh_client, owner_cookie, LEVELS, status="active")) == (
        SEEDED_NAMES
    )
    assert names(get(fresh_client, owner_cookie, LEVELS, status="inactive")) == [
        "Posgrado"
    ]
    assert names(get(fresh_client, owner_cookie, LEVELS, status="all")) == [
        *SEEDED_NAMES,
        "Posgrado",
    ]


@pytest.mark.parametrize("status", ["deleted", "ACTIVE", ""])
def test_an_unknown_status_is_422(client, cookie, status):
    response = get(client, cookie, LEVELS, status=status)

    assert code_of(response) == (422, "validation_error")


def test_equal_positions_fall_back_to_the_name_ignoring_case(fresh):
    _, fresh_client, owner_cookie = fresh
    for name in ["beta", "Alfa", "gamma"]:
        create_level(fresh_client, owner_cookie, name, sort_order=20)

    assert names(get(fresh_client, owner_cookie, LEVELS)) == [
        "Primaria",
        "Alfa",
        "beta",
        "gamma",
        "Secundaria",
        "Preparatoria",
        "Universidad",
    ]


def test_limit_offset_and_total(fresh):
    _, fresh_client, owner_cookie = fresh

    response = get(fresh_client, owner_cookie, LEVELS, limit=2, offset=1)

    assert names(response) == ["Secundaria", "Preparatoria"]
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (4, 2, 1)
    beyond = get(fresh_client, owner_cookie, LEVELS, offset=10).json()
    assert (beyond["items"], beyond["total"]) == ([], 4)


def test_active_subject_count_counts_only_active_subjects(client, cookie):
    level = create_level(client, cookie, sort_order=9000)
    create_subject(client, cookie, level["id"])
    create_subject(client, cookie, level["id"])
    retired = create_subject(client, cookie, level["id"])
    patch(
        client,
        cookie,
        subject_path(retired["id"]),
        {"version": 1, "is_active": False},
    )

    detail = get(client, cookie, level_path(level["id"])).json()
    listed = get(client, cookie, LEVELS, status="all", limit=100).json()["items"]

    assert detail["active_subject_count"] == 2
    assert [i["active_subject_count"] for i in listed if i["id"] == level["id"]] == [2]


def test_an_unknown_level_is_404(client, cookie):
    response = get(client, cookie, level_path(uuid.uuid4()))

    assert code_of(response) == (404, "not_found")
    assert code_of(get(client, cookie, f"{LEVELS}/not-a-uuid")) == (
        422,
        "validation_error",
    )


def test_an_inactive_level_is_still_returned(client, cookie):
    level = create_level(client, cookie)
    patch(client, cookie, level_path(level["id"]), {"version": 1, "is_active": False})

    detail = get(client, cookie, level_path(level["id"])).json()

    assert (detail["is_active"], detail["version"]) == (False, 2)


# --- C6: at most three statements ---------------------------------------------------


def test_a_list_page_takes_at_most_three_statements(fresh):
    url, fresh_client, owner_cookie = fresh
    for _ in range(26):
        level = create_level(fresh_client, owner_cookie)
        create_subject(fresh_client, owner_cookie, level["id"])

    for status in ("active", "all"):
        statements, page = count_statements(
            url,
            lambda session, status=status: service.list_education_levels(
                session, status=status, page=OffsetParams(limit=30, offset=0)
            ),
        )
        assert len(page.items) == 30
        assert sum(item.active_subject_count for item in page.items) == 26
        assert statements <= 3


# --- D: create and update -----------------------------------------------------------


def test_create_trims_the_name_and_uses_the_given_position(
    module_seeded_url, client, cookie
):
    name = unique_name()

    response = post(client, cookie, LEVELS, {"name": f"  {name}  ", "sort_order": 15})

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["name"], body["sort_order"], body["is_active"]) == (name, 15, True)
    row = level_row(module_seeded_url, body["id"])
    assert (row.name, row.sort_order, row.seed_key) == (name, 15, None)


def test_without_a_position_a_level_goes_last(fresh):
    _, fresh_client, owner_cookie = fresh

    first = create_level(fresh_client, owner_cookie)
    second = create_level(fresh_client, owner_cookie)
    create_level(fresh_client, owner_cookie, sort_order=9995)
    capped = create_level(fresh_client, owner_cookie)

    assert (first["sort_order"], second["sort_order"]) == (50, 60)
    assert capped["sort_order"] == 9999


def test_the_first_level_defaults_to_position_10(fresh):
    url, fresh_client, owner_cookie = fresh
    run_sql(url, "DELETE FROM education_levels")

    level = create_level(fresh_client, owner_cookie)

    assert level["sort_order"] == 10


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": None},
        {"name": "Valid", "sort_order": -1},
        {"name": "Valid", "sort_order": 10000},
        {"name": "Valid", "sort_order": "first"},
        {"name": "Valid", "is_active": False},
        {"name": "Valid", "seed_key": "elementary"},
    ],
)
def test_invalid_create_bodies_are_422_and_write_nothing(
    module_seeded_url, client, cookie, body
):
    before = catalog_event_count(module_seeded_url)

    response = post(client, cookie, LEVELS, body)

    assert code_of(response) == (422, "validation_error")
    assert catalog_event_count(module_seeded_url) == before


def test_a_100_character_name_is_accepted(client, cookie):
    name = unique_name() + "x" * 84

    assert len(create_level(client, cookie, name)["name"]) == 100


@pytest.mark.parametrize("variant", ["same", "upper", "inactive"])
def test_a_duplicate_name_is_409_name_taken(module_seeded_url, client, cookie, variant):
    name = unique_name()
    existing = create_level(client, cookie, name)
    if variant == "inactive":
        patch(
            client,
            cookie,
            level_path(existing["id"]),
            {"version": 1, "is_active": False},
        )
    before = catalog_event_count(module_seeded_url)

    taken = name.upper() if variant == "upper" else name
    response = post(client, cookie, LEVELS, {"name": f" {taken} "})

    assert code_of(response) == (409, "name_taken")
    assert catalog_event_count(module_seeded_url) == before


def test_a_seeded_name_in_another_case_is_taken(client, cookie):
    response = post(client, cookie, LEVELS, {"name": "PRIMARIA"})

    assert code_of(response) == (409, "name_taken")


def test_rename_reorder_deactivate_and_reactivate(module_seeded_url, client, cookie):
    level = create_level(client, cookie, sort_order=9000)
    path = level_path(level["id"])
    new_name = unique_name("Renombrado")

    renamed = patch(client, cookie, path, {"version": 1, "name": new_name})
    reordered = patch(client, cookie, path, {"version": 2, "sort_order": 9001})
    deactivated = patch(client, cookie, path, {"version": 3, "is_active": False})
    reactivated = patch(client, cookie, path, {"version": 4, "is_active": True})

    assert [r.status_code for r in (renamed, reordered, deactivated, reactivated)] == [
        200
    ] * 4
    assert renamed.json()["name"] == new_name
    assert reordered.json()["sort_order"] == 9001
    assert deactivated.json()["is_active"] is False
    assert reactivated.json()["is_active"] is True
    row = level_row(module_seeded_url, level["id"])
    assert (row.name, row.sort_order, row.is_active, row.version) == (
        new_name,
        9001,
        True,
        5,
    )


def test_a_rename_to_another_case_of_its_own_name_is_allowed(client, cookie):
    name = unique_name("nivel")
    level = create_level(client, cookie, name)

    response = patch(
        client, cookie, level_path(level["id"]), {"version": 1, "name": name.upper()}
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == name.upper()


def test_a_rename_to_another_levels_name_is_409(module_seeded_url, client, cookie):
    taken = create_level(client, cookie)
    level = create_level(client, cookie)

    response = patch(
        client,
        cookie,
        level_path(level["id"]),
        {"version": 1, "name": taken["name"].lower()},
    )

    assert code_of(response) == (409, "name_taken")
    assert level_row(module_seeded_url, level["id"]).version == 1


def test_a_level_with_an_active_subject_cannot_be_deactivated(
    module_seeded_url, client, cookie
):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    path = level_path(level["id"])
    before = catalog_event_count(module_seeded_url)

    refused = patch(
        client, cookie, path, {"version": 1, "is_active": False, "name": unique_name()}
    )

    assert code_of(refused) == (409, "education_level_has_active_subjects")
    row = level_row(module_seeded_url, level["id"])
    assert (row.name, row.is_active, row.version) == (level["name"], True, 1)
    assert catalog_event_count(module_seeded_url) == before
    patch(
        client, cookie, subject_path(subject["id"]), {"version": 1, "is_active": False}
    )
    allowed = patch(client, cookie, path, {"version": 1, "is_active": False})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["active_subject_count"] == 0


def test_reactivating_a_level_changes_no_subject(module_seeded_url, client, cookie):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    patch(
        client, cookie, subject_path(subject["id"]), {"version": 1, "is_active": False}
    )
    path = level_path(level["id"])
    patch(client, cookie, path, {"version": 1, "is_active": False})

    reactivated = patch(client, cookie, path, {"version": 2, "is_active": True})

    assert reactivated.json()["active_subject_count"] == 0
    detail = get(client, cookie, subject_path(subject["id"])).json()
    assert (detail["is_active"], detail["version"]) == (False, 2)


def test_a_stale_version_is_409_and_changes_nothing(module_seeded_url, client, cookie):
    level = create_level(client, cookie)
    path = level_path(level["id"])
    patch(client, cookie, path, {"version": 1, "sort_order": 9100})
    before = catalog_event_count(module_seeded_url)

    response = patch(client, cookie, path, {"version": 1, "name": unique_name()})

    assert code_of(response) == (409, "version_conflict")
    row = level_row(module_seeded_url, level["id"])
    assert (row.name, row.version) == (level["name"], 2)
    assert catalog_event_count(module_seeded_url) == before


def test_a_patch_that_changes_nothing_is_200_without_a_new_version(
    module_seeded_url, client, cookie
):
    level = create_level(client, cookie, sort_order=9200)
    before = catalog_event_count(module_seeded_url)

    response = patch(
        client,
        cookie,
        level_path(level["id"]),
        {
            "version": 1,
            "name": f" {level['name']} ",
            "sort_order": 9200,
            "is_active": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == level
    assert catalog_event_count(module_seeded_url) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": "Nuevo"},
        {"version": 0},
        {"version": 1, "name": None},
        {"version": 1, "sort_order": None},
        {"version": 1, "is_active": None},
        {"version": 1, "name": "  "},
        {"version": 1, "sort_order": 10000},
        {"version": 1, "is_active": "maybe"},
        {"version": 1, "active_subject_count": 3},
    ],
)
def test_invalid_patch_bodies_are_422(module_seeded_url, client, cookie, body):
    level = create_level(client, cookie)

    response = patch(client, cookie, level_path(level["id"]), body)

    assert code_of(response) == (422, "validation_error")
    assert level_row(module_seeded_url, level["id"]).version == 1


def test_patch_check_order_404_before_the_version(client, cookie):
    response = patch(
        client, cookie, level_path(uuid.uuid4()), {"version": 99, "name": "X"}
    )

    assert code_of(response) == (404, "not_found")


@pytest.mark.parametrize("role", [TUTOR, ACADEMIC_COORDINATOR, STUDENT, GUARDIAN])
def test_only_catalog_managers_write(module_seeded_url, client, cookie, role):
    level = create_level(client, cookie)
    other = create_user(module_seeded_url, roles=(role,))
    other_cookie = sign_in(client, other.username)
    before = catalog_event_count(module_seeded_url)

    responses = [
        post(client, other_cookie, LEVELS, {"name": unique_name()}),
        patch(client, other_cookie, level_path(level["id"]), {"version": 1}),
        post(
            client,
            other_cookie,
            f"{level_path(level['id'])}/subjects",
            {"name": unique_name()},
        ),
    ]

    assert [code_of(r) for r in responses] == [(403, "permission_denied")] * 3
    assert catalog_event_count(module_seeded_url) == before


def test_an_administrator_can_write(module_seeded_url, client):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)

    level = create_level(client, admin_cookie)
    response = patch(
        client, admin_cookie, level_path(level["id"]), {"version": 1, "sort_order": 1}
    )

    assert response.status_code == 200, response.text


# --- F2: audit ----------------------------------------------------------------------


def test_create_rename_and_deactivation_are_audited(
    module_seeded_url, client, cookie, owner
):
    name = unique_name()
    created = post(client, cookie, LEVELS, {"name": name, "sort_order": 9300})
    level_id = created.json()["id"]
    renamed_to = unique_name()
    renamed = patch(
        client, cookie, level_path(level_id), {"version": 1, "name": renamed_to}
    )
    deactivated = patch(
        client, cookie, level_path(level_id), {"version": 2, "is_active": False}
    )

    recorded = events(
        module_seeded_url, record_type="education_levels", record_id=uuid.UUID(level_id)
    )

    assert [e.action for e in recorded] == ["create", "update", "update"]
    assert {e.actor_user_id for e in recorded} == {owner.id}
    assert [e.request_id for e in recorded] == [
        r.headers["X-Request-ID"] for r in (created, renamed, deactivated)
    ]
    assert recorded[0].changes == {
        "name": {"old": None, "new": name},
        "sort_order": {"old": None, "new": 9300},
        "is_active": {"old": None, "new": True},
        "seed_key": {"old": None, "new": None},
    }
    assert recorded[1].changes == {"name": {"old": name, "new": renamed_to}}
    assert recorded[2].changes == {"is_active": {"old": True, "new": False}}
    assert all(e.details is None for e in recorded)


@pytest.mark.parametrize(
    "case", ["no_op", "forbidden", "not_found", "conflict", "invalid"]
)
def test_failed_or_empty_changes_write_no_audit_event(
    module_seeded_url, client, cookie, case
):
    level = create_level(client, cookie)
    actor_cookie = cookie
    target: Any = level["id"]
    body: dict[str, Any] = {"version": 1, "name": unique_name()}
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
        body["sort_order"] = -5
    before = catalog_event_count(module_seeded_url)

    response = patch(client, actor_cookie, level_path(target), body)

    assert response.status_code in (200, 403, 404, 409, 422)
    assert catalog_event_count(module_seeded_url) == before
