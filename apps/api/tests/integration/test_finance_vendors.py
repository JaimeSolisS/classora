"""Vendors through the routes (#114 A2, C, D; data-model §12.2). The seam and
the races are ``test_finance_seam.py``, the table ``test_migrations.py -k
0083``, the roles ``tests/authz/matrix.toml``."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import OffsetParams
from app.modules.finance import service as finance
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.finance_support import (
    VENDORS,
    audit_events,
    errors_of,
    reset,
    stored,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of

EXISTS = (409, "vendor_exists")
STALE = (409, "version_conflict")
INVALID = (422, "validation_error")
DENIED = (403, "permission_denied")
CONTACT = "Tel. 55 1234 5678\nventas@acme.mx"


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


def add(
    client: TestClient, cookie: SetCookie, name: str, **body: Any
) -> dict[str, Any]:
    response = post(client, cookie, VENDORS, {"name": name, **body})
    assert response.status_code == 201, response.text
    made: dict[str, Any] = response.json()
    return made


def path(vendor_id: Any) -> str:
    return f"{VENDORS}/{vendor_id}"


def names(client: TestClient, cookie: SetCookie, **params: Any) -> list[str]:
    response = get(client, cookie, VENDORS, **params)
    assert response.status_code == 200, response.text
    return [row["name"] for row in response.json()["items"]]


def insert_vendors(url: str, count: int) -> None:
    run_sql(
        url,
        "INSERT INTO vendors (name) SELECT 'Proveedor ' || n"
        " FROM generate_series(1, :count) AS n",
        {"count": count},
    )


# --- C1: list -----


def test_c1_a_page_active_first_then_name_ignoring_case_then_id(client, staff):
    add(client, staff, "zeta")
    alfa = add(client, staff, "Alfa", contact="55")
    patch(client, staff, path(alfa["id"]), {"version": 1, "is_active": False})
    add(client, staff, "beta")

    response = get(client, staff, VENDORS)

    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (3, 25, 0)
    assert [(row["name"], row["is_active"]) for row in body["items"]] == [
        ("beta", True),
        ("zeta", True),
        ("Alfa", False),
    ]
    assert body["items"][2] == {
        "id": alfa["id"],
        "name": "Alfa",
        "contact": "55",
        "is_active": False,
        "version": 2,
    }
    assert names(client, staff, limit=1, offset=1) == ["zeta"]
    past = get(client, staff, VENDORS, offset=10).json()
    assert (past["items"], past["total"]) == ([], 3)


def test_c1_q_matches_part_of_the_name_ignoring_case(client, staff):
    for name in ("Plomería López", "Papelería Centro", "LOPEZ Hermanos"):
        add(client, staff, name)

    assert names(client, staff, q="lópez") == ["Plomería López"]
    assert names(client, staff, q="  LOP ") == ["LOPEZ Hermanos"]
    assert names(client, staff, q="ería") == ["Papelería Centro", "Plomería López"]
    assert get(client, staff, VENDORS, q="lop").json()["total"] == 1


def test_c1_q_matches_percent_underscore_and_backslash_literally(client, staff):
    for name in ("100% Limpio", "Agua_Pura", "Barra\\Sur", "Otro"):
        add(client, staff, name)

    assert names(client, staff, q="%") == ["100% Limpio"]
    assert names(client, staff, q="_") == ["Agua_Pura"]
    assert names(client, staff, q="\\") == ["Barra\\Sur"]


def test_c1_a_blank_q_lists_all_and_a_long_one_is_422(client, staff):
    add(client, staff, "Acme")

    assert names(client, staff, q="   ") == ["Acme"]
    assert code_of(get(client, staff, VENDORS, q="x" * 101)) == INVALID
    assert code_of(get(client, staff, VENDORS, q="x" * 100)) == (200, None)


def test_c1_active_filters(client, staff):
    add(client, staff, "Activo")
    off = add(client, staff, "Inactivo")
    patch(client, staff, path(off["id"]), {"version": 1, "is_active": False})

    assert names(client, staff, active="true") == ["Activo"]
    assert names(client, staff, active="false") == ["Inactivo"]
    assert names(client, staff) == ["Activo", "Inactivo"]
    assert code_of(get(client, staff, VENDORS, active="maybe")) == INVALID


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_c1_limits_out_of_range_are_422(client, staff, params):
    assert code_of(get(client, staff, VENDORS, **params)) == INVALID


def test_c1_one_statement_for_1_and_for_30_vendors(module_seeded_url):
    url = module_seeded_url

    def page(session: Any) -> Any:
        return finance.list_vendors(
            session, q="Proveedor", active=True, page=OffsetParams(limit=25, offset=0)
        )

    insert_vendors(url, 1)
    one, first = count_statements(url, page)
    reset(url)
    insert_vendors(url, 30)
    many, second = count_statements(url, page)

    assert (first.total, len(second.items), second.total) == (1, 25, 30)
    assert one == many == 1


# --- C2: create -----


def test_c2_add_a_vendor_trimmed_and_refuse_its_name_ignoring_case(client, staff):
    response = post(
        client, staff, VENDORS, {"name": "  Acme  ", "contact": f"  {CONTACT}\n"}
    )

    assert code_of(response) == (201, None), response.text
    body = response.json()
    assert (body["name"], body["contact"], body["is_active"], body["version"]) == (
        "Acme",
        CONTACT,
        True,
        1,
    )
    assert code_of(post(client, staff, VENDORS, {"name": "aCME"})) == EXISTS
    no_contact = add(client, staff, "Sin contacto")
    assert no_contact["contact"] is None


def test_c2_an_inactive_name_still_blocks_and_no_label_rule_applies(client, staff):
    off = add(client, staff, "Acme")
    patch(client, staff, path(off["id"]), {"version": 1, "is_active": False})

    assert code_of(post(client, staff, VENDORS, {"name": "ACME"})) == EXISTS
    assert code_of(post(client, staff, VENDORS, {"name": "Rent"})) == (201, None)


@pytest.mark.parametrize(
    ("body", "errors"),
    [
        ({"name": ""}, [(("body", "name"), "text_empty")]),
        ({"name": "x" * 201}, [(("body", "name"), "text_too_long")]),
        ({"name": "Acme\x1b"}, [(("body", "name"), "text_invalid")]),
        ({"name": "Acme", "contact": ""}, [(("body", "contact"), "text_empty")]),
        ({"name": "Acme", "contact": "  "}, [(("body", "contact"), "text_empty")]),
        (
            {"name": "Acme", "contact": "5" * 501},
            [(("body", "contact"), "text_too_long")],
        ),
        (
            {"name": "Acme", "contact": "55\x00"},
            [(("body", "contact"), "text_invalid")],
        ),
        (
            {"name": "Acme", "rfc": "XAXX010101000"},
            [(("body", "rfc"), "extra_forbidden")],
        ),
        ({}, [(("body", "name"), "missing")]),
    ],
)
def test_c2_a_bad_body_is_422(client, staff, body, errors):
    response = post(client, staff, VENDORS, body)

    assert code_of(response) == INVALID, response.text
    assert errors_of(response) == errors


def test_c2_the_longest_name_and_contact_pass(client, staff):
    made = add(client, staff, "x" * 200, contact="5" * 500)

    assert (len(made["name"]), len(made["contact"])) == (200, 500)


# --- C3: change -----


def test_c3_patch_needs_a_change_a_known_id_and_the_current_version(
    module_seeded_url, client, staff
):
    acme = add(client, staff, "Acme", contact="55")
    add(client, staff, "Beta")

    empty = patch(client, staff, path(acme["id"]), {"version": 1})
    assert code_of(empty) == INVALID
    assert errors_of(empty) == [(("body",), "no_changes")]
    assert code_of(
        patch(client, staff, path(uuid.uuid4()), {"version": 1, "name": "X"})
    ) == (404, "not_found")
    assert code_of(
        patch(client, staff, path(acme["id"]), {"version": 2, "name": "X"})
    ) == (STALE)
    assert (
        code_of(patch(client, staff, path(acme["id"]), {"version": 1, "name": "BETA"}))
        == EXISTS
    )
    assert stored(module_seeded_url, "vendors", acme["id"]).name == "Acme"


def test_c3_contact_null_clears_it_and_leaving_it_out_keeps_it(client, staff):
    acme = add(client, staff, "Acme", contact="55")

    renamed = patch(client, staff, path(acme["id"]), {"version": 1, "name": "Acme SA"})
    assert (renamed.json()["name"], renamed.json()["contact"]) == ("Acme SA", "55")
    changed = patch(client, staff, path(acme["id"]), {"version": 2, "contact": " 56 "})
    assert changed.json()["contact"] == "56"
    cleared = patch(client, staff, path(acme["id"]), {"version": 3, "contact": None})
    assert code_of(cleared) == (200, None)
    assert (cleared.json()["contact"], cleared.json()["version"]) == (None, 4)
    assert (
        code_of(patch(client, staff, path(acme["id"]), {"version": 4, "contact": ""}))
        == INVALID
    )


def test_c3_deactivate_and_reactivate(client, staff):
    acme = add(client, staff, "Acme")

    off = patch(client, staff, path(acme["id"]), {"version": 1, "is_active": False})
    on = patch(client, staff, path(acme["id"]), {"version": 2, "is_active": True})

    assert (off.json()["is_active"], on.json()["is_active"]) == (False, True)


def test_c3_a_rename_keeps_the_id_and_the_contact_is_audited_in_clear(
    module_seeded_url, client, staff
):
    acme = add(client, staff, "Acme", contact=CONTACT)

    patch(client, staff, path(acme["id"]), {"version": 1, "name": "Acme SA"})

    events = audit_events(module_seeded_url, "vendors", acme["id"])
    assert [event.action for event in events] == ["create", "update"]
    assert events[0].changes["contact"] == {"old": None, "new": CONTACT}
    assert events[1].changes == {"name": {"old": "Acme", "new": "Acme SA"}}


# --- D: access -----


@pytest.mark.parametrize("who", ["owner", "administrator", "finance"])
def test_d1_the_three_roles_read_and_write(client, cookies, who):
    made = post(client, cookies[who], VENDORS, {"name": f"Hecho por {who}"})

    assert code_of(made) == (201, None)
    assert code_of(get(client, cookies[who], VENDORS)) == (200, None)
    changed = patch(
        client, cookies[who], path(made.json()["id"]), {"version": 1, "contact": "55"}
    )
    assert code_of(changed) == (200, None)


@pytest.mark.parametrize("who", ["coordinator", "tutor", "student", "guardian"])
def test_d1_everyone_else_gets_403_before_the_query_and_the_body(client, cookies, who):
    cookie = cookies[who]

    assert code_of(get(client, cookie, VENDORS, limit=0)) == DENIED
    assert code_of(post(client, cookie, VENDORS, {})) == DENIED
    assert code_of(patch(client, cookie, path(uuid.uuid4()), {})) == DENIED
