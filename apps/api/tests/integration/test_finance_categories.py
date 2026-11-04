"""Expense categories through the routes (#114 A2, B, D; ADR-0017 E15). The
seam and the races are ``test_finance_seam.py``, the table
``test_migrations.py -k 0083``, the roles ``tests/authz/matrix.toml``."""

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
from tests.integration.auth_support import (
    SetCookie,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import get, patch, post
from tests.integration.finance_support import (
    CATEGORIES,
    audit_events,
    errors_of,
    reset,
    seeded_id,
    stored,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of

EXISTS = (409, "expense_category_exists")
STALE = (409, "version_conflict")
INVALID = (422, "validation_error")
DENIED = (403, "permission_denied")
SEEDED_ORDER = [
    ("Equipo", "equipment"),
    ("Insumos", "supplies"),
    ("Mantenimiento", "maintenance"),
    ("Renta", "rent"),
    ("Servicios", "utilities"),
]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def cookies(module_seeded_url: str, client: TestClient) -> dict[str, SetCookie]:
    url = module_seeded_url
    roles = {
        "owner": OWNER,
        "administrator": ADMINISTRATOR,
        "finance": FINANCE_STAFF,
        "coordinator": ACADEMIC_COORDINATOR,
        "tutor": TUTOR,
        "student": STUDENT,
        "guardian": GUARDIAN,
    }
    return {
        name: sign_in(client, create_user(url, roles=(role,)).username)
        for name, role in roles.items()
    }


@pytest.fixture
def staff(cookies: dict[str, SetCookie]) -> SetCookie:
    return cookies["finance"]


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    reset(module_seeded_url)


def listed(client: TestClient, cookie: SetCookie) -> list[tuple[Any, ...]]:
    response = get(client, cookie, CATEGORIES)
    assert response.status_code == 200, response.text
    return [
        (row["name"], row["system_key"], row["is_active"]) for row in response.json()
    ]


def add(client: TestClient, cookie: SetCookie, name: str) -> dict[str, Any]:
    response = post(client, cookie, CATEGORIES, {"name": name})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def path(category_id: Any) -> str:
    return f"{CATEGORIES}/{category_id}"


# --- B1: list -----


def test_b1_a_fresh_database_lists_the_five_seeded_categories(client, staff):
    response = get(client, staff, CATEGORIES)

    assert response.status_code == 200
    rows = response.json()
    assert [(row["name"], row["system_key"]) for row in rows] == SEEDED_ORDER
    assert {tuple(sorted(row)) for row in rows} == {
        ("id", "is_active", "name", "system_key", "version")
    }
    assert {(row["is_active"], row["version"]) for row in rows} == {(True, 1)}


def test_b1_active_first_then_name_ignoring_case_then_id(client, staff):
    add(client, staff, "zeta")
    alfa = add(client, staff, "Alfa")
    patch(client, staff, path(alfa["id"]), {"version": 1, "is_active": False})
    add(client, staff, "beta")

    assert [row[0] for row in listed(client, staff)] == [
        "beta",
        "Equipo",
        "Insumos",
        "Mantenimiento",
        "Renta",
        "Servicios",
        "zeta",
        "Alfa",
    ]


# --- B2: create -----


def test_b2_add_a_category_trimmed_and_refuse_its_name_ignoring_case(client, staff):
    response = post(client, staff, CATEGORIES, {"name": "  Luz  "})

    assert code_of(response) == (201, None), response.text
    body = response.json()
    assert (body["name"], body["system_key"], body["is_active"], body["version"]) == (
        "Luz",
        None,
        True,
        1,
    )
    assert code_of(post(client, staff, CATEGORIES, {"name": "lUZ"})) == EXISTS
    assert code_of(post(client, staff, CATEGORIES, {"name": " RENTA "})) == EXISTS


@pytest.mark.parametrize(
    ("body", "errors"),
    [
        ({"name": ""}, [(("body", "name"), "text_empty")]),
        ({"name": "   "}, [(("body", "name"), "text_empty")]),
        ({"name": "x" * 101}, [(("body", "name"), "text_too_long")]),
        ({"name": "Luz\x07"}, [(("body", "name"), "text_invalid")]),
        ({"name": "Luz\nAgua"}, [(("body", "name"), "text_invalid")]),
        ({"name": "Luz\x00"}, [(("body", "name"), "text_invalid")]),
        ({}, [(("body", "name"), "missing")]),
        (
            {"name": "Luz", "system_key": "rent"},
            [(("body", "system_key"), "extra_forbidden")],
        ),
    ],
)
def test_b2_a_bad_body_is_422(client, staff, body, errors):
    response = post(client, staff, CATEGORIES, body)

    assert code_of(response) == INVALID, response.text
    assert errors_of(response) == errors


def test_b2_a_name_of_exactly_100_characters_passes(client, staff):
    assert code_of(post(client, staff, CATEGORIES, {"name": "x" * 100})) == (201, None)


def test_b2_a_lone_surrogate_is_text_invalid(client, staff):
    client.cookies.clear()
    response = client.post(
        CATEGORIES,
        content=b'{"name": "a\\ud800b"}',
        headers={**signed_headers(staff), "Content-Type": "application/json"},
    )

    assert code_of(response) == INVALID
    assert errors_of(response) == [(("body", "name"), "text_invalid")]


@pytest.mark.parametrize(
    "name",
    [
        "renta",
        "RENT ",
        "Maintenance",
        " servicios",
        "UTILITIES",
        "equipment",
        "Insumos",
    ],
)
def test_b2_a_seeded_label_in_either_language_is_taken(client, staff, name):
    assert code_of(post(client, staff, CATEGORIES, {"name": name})) == EXISTS


def test_b2_accents_are_not_folded(client, staff):
    add(client, staff, "Energía")

    assert code_of(post(client, staff, CATEGORIES, {"name": "Energia"})) == (201, None)


def test_b2_an_inactive_name_still_blocks(client, staff):
    luz = add(client, staff, "Luz")
    patch(client, staff, path(luz["id"]), {"version": 1, "is_active": False})

    assert code_of(post(client, staff, CATEGORIES, {"name": "LUZ"})) == EXISTS


# --- B3, B4: change, rename -----


def test_b3_patch_needs_a_change_and_a_known_id_and_the_current_version(
    module_seeded_url, client, staff
):
    luz = add(client, staff, "Luz")
    add(client, staff, "Agua")

    empty = patch(client, staff, path(luz["id"]), {"version": 1})
    assert code_of(empty) == INVALID
    assert errors_of(empty) == [(("body",), "no_changes")]
    nulls = patch(client, staff, path(luz["id"]), {"version": 1, "name": None})
    assert code_of(nulls) == INVALID
    assert (
        code_of(
            patch(client, staff, path(luz["id"]), {"version": 1, "system_key": "rent"})
        )
        == INVALID
    )
    assert code_of(
        patch(client, staff, path(uuid.uuid4()), {"version": 1, "name": "X"})
    ) == (404, "not_found")
    assert code_of(
        patch(client, staff, path(luz["id"]), {"version": 2, "name": "X"})
    ) == (STALE)
    assert (
        code_of(patch(client, staff, path(luz["id"]), {"version": 1, "name": "agua"}))
        == EXISTS
    )
    assert (
        code_of(patch(client, staff, path(luz["id"]), {"version": 1, "name": "Rent"}))
        == EXISTS
    )
    assert stored(module_seeded_url, "expense_categories", luz["id"]).name == "Luz"


def test_b3_the_check_order_is_422_then_404(client, staff):
    response = patch(client, staff, path(uuid.uuid4()), {"version": 1})

    assert code_of(response) == INVALID


def test_b4_a_seeded_row_renamed_to_its_own_label_clears_its_key(
    module_seeded_url, client, staff
):
    rent = seeded_id(module_seeded_url, "rent")

    response = patch(client, staff, path(rent), {"version": 1, "name": "Rent"})

    assert code_of(response) == (200, None), response.text
    body = response.json()
    assert (body["id"], body["name"], body["system_key"], body["version"]) == (
        str(rent),
        "Rent",
        None,
        2,
    )
    back = patch(client, staff, path(rent), {"version": 2, "name": "Renta"})
    assert (back.json()["name"], back.json()["system_key"]) == ("Renta", None)


def test_b4_the_current_name_keeps_the_key(module_seeded_url, client, staff):
    rent = seeded_id(module_seeded_url, "rent")

    same = patch(client, staff, path(rent), {"version": 1, "name": " Renta "})
    off = patch(client, staff, path(rent), {"version": 1, "is_active": False})

    assert (same.json()["system_key"], same.json()["version"]) == ("rent", 1)
    assert (off.json()["system_key"], off.json()["is_active"]) == ("rent", False)


def test_b4_a_case_only_rename_is_a_rename(module_seeded_url, client, staff):
    rent = seeded_id(module_seeded_url, "rent")

    response = patch(client, staff, path(rent), {"version": 1, "name": "RENTA"})

    assert (response.json()["name"], response.json()["system_key"]) == ("RENTA", None)


def test_b4_a_rename_frees_the_labels_for_other_rows(module_seeded_url, client, staff):
    rent = seeded_id(module_seeded_url, "rent")
    assert code_of(post(client, staff, CATEGORIES, {"name": "Rent"})) == EXISTS

    patch(client, staff, path(rent), {"version": 1, "name": "Alquiler"})

    assert code_of(post(client, staff, CATEGORIES, {"name": "Renta"})) == (201, None)
    assert code_of(post(client, staff, CATEGORIES, {"name": "rent"})) == (201, None)
    assert ("Alquiler", None, True) in listed(client, staff)


def test_b4_another_keyed_rows_label_is_refused_on_rename(
    module_seeded_url, client, staff
):
    rent = seeded_id(module_seeded_url, "rent")

    response = patch(client, staff, path(rent), {"version": 1, "name": "Utilities"})

    assert code_of(response) == EXISTS
    assert stored(module_seeded_url, "expense_categories", rent).system_key == "rent"


# --- B5: stable id and the audit trail -----


def test_b5_a_rename_keeps_the_id_and_the_audit_event_holds_the_old_name(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    luz = add(client, staff, "Luz")

    renamed = patch(client, staff, path(luz["id"]), {"version": 1, "name": "Energía"})

    assert renamed.json()["id"] == luz["id"]
    assert len(listed(client, staff)) == 6
    events = audit_events(url, "expense_categories", luz["id"])
    assert [event.action for event in events] == ["create", "update"]
    assert events[1].changes == {"name": {"old": "Luz", "new": "Energía"}}


def test_b5_a_seeded_rename_records_the_key_change(module_seeded_url, client, staff):
    url = module_seeded_url
    rent = seeded_id(url, "rent")

    patch(client, staff, path(rent), {"version": 1, "name": "Alquiler"})

    [event] = audit_events(url, "expense_categories", rent)
    assert event.action == "update"
    assert event.changes == {
        "name": {"old": "Renta", "new": "Alquiler"},
        "system_key": {"old": "rent", "new": None},
    }


def test_b5_an_unchanged_patch_writes_nothing(module_seeded_url, client, staff):
    luz = add(client, staff, "Luz")

    response = patch(client, staff, path(luz["id"]), {"version": 1, "is_active": True})

    assert (response.status_code, response.json()["version"]) == (200, 1)
    assert len(audit_events(module_seeded_url, "expense_categories", luz["id"])) == 1


# --- B6: deactivate, reactivate -----


def test_b6_an_inactive_category_stays_listed_last_and_can_be_renamed_and_reactivated(
    module_seeded_url, client, staff
):
    rent = seeded_id(module_seeded_url, "rent")

    off = patch(client, staff, path(rent), {"version": 1, "is_active": False})
    assert code_of(off) == (200, None)
    assert listed(client, staff)[-1] == ("Renta", "rent", False)
    renamed = patch(client, staff, path(rent), {"version": 2, "name": "Alquiler"})
    assert (renamed.json()["is_active"], renamed.json()["version"]) == (False, 3)
    on = patch(client, staff, path(rent), {"version": 3, "is_active": True})
    assert (on.json()["is_active"], on.json()["name"]) == (True, "Alquiler")


# --- D: access -----


@pytest.mark.parametrize("who", ["owner", "administrator", "finance"])
def test_d1_the_three_roles_read_and_write(client, cookies, who):
    made = post(client, cookies[who], CATEGORIES, {"name": f"Hecha por {who}"})

    assert code_of(made) == (201, None)
    assert code_of(get(client, cookies[who], CATEGORIES)) == (200, None)
    changed = patch(
        client,
        cookies[who],
        path(made.json()["id"]),
        {"version": 1, "is_active": False},
    )
    assert code_of(changed) == (200, None)


@pytest.mark.parametrize("who", ["coordinator", "tutor", "student", "guardian"])
def test_d1_everyone_else_gets_403_before_the_body(client, cookies, who):
    cookie = cookies[who]

    assert code_of(get(client, cookie, CATEGORIES)) == DENIED
    assert code_of(post(client, cookie, CATEGORIES, {})) == DENIED
    assert code_of(patch(client, cookie, path(uuid.uuid4()), {})) == DENIED


def test_d1_anonymous_is_401_and_a_write_without_the_csrf_token_403(client, staff):
    client.cookies.clear()
    assert client.get(CATEGORIES).status_code == 401
    client.cookies.clear()
    response = client.post(
        CATEGORIES,
        json={"name": "Luz"},
        headers={"Cookie": f"{staff.name}={staff.value}"},
    )
    assert code_of(response) == (403, "csrf_failed")
