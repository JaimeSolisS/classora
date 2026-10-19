"""Families: create, edit, read and list (#172 B).

Real logins through the real app.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.authz import Principal
from app.core.pagination import OffsetParams
from app.modules.families import service
from app.modules.families.models import Family, FamilyStudent
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from tests.factories.families import persist_student
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.family_support import (
    FAMILIES,
    added,
    create_family,
    family_body,
    family_count,
    family_event_count,
    family_path,
    family_row,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.student_support import create_student, suffix

MISSING = "00000000-0000-4000-8000-000000000999"


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


def update(client, cookie, family: dict[str, Any], body: dict[str, Any]):
    return patch(client, cookie, family_path(family["id"]), body)


def principal_for(user_id: uuid.UUID, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


# --- B1: create ---------------------------------------------------------------------


def test_create_trims_the_name_and_starts_at_version_1(
    module_seeded_url, client, cookie
):
    response = post(client, cookie, FAMILIES, {"name": "  Familia García López "})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Familia García López"
    assert (body["version"], body["is_active"]) == (1, True)
    assert (body["students"], body["guardians"]) == ([], [])
    assert set(body) == {
        "id",
        "name",
        "is_active",
        "students",
        "guardians",
        "version",
        "created_at",
        "updated_at",
    }
    row = family_row(module_seeded_url, body["id"])
    assert (row.name, row.version, row.is_active) == ("Familia García López", 1, True)


def test_duplicate_names_are_allowed(client, cookie):
    first = create_family(client, cookie, name="Familia Igual")
    second = create_family(client, cookie, name="Familia Igual")

    assert first["id"] != second["id"]


@pytest.mark.parametrize(
    "body",
    [
        {"name": "   "},
        {"name": ""},
        {"name": None},
        {"name": "F" * 201},
        {},
        {"name": "Familia", "is_active": False},
        {"name": "Familia", "students": []},
    ],
    ids=str,
)
def test_invalid_bodies_are_422_and_write_nothing(
    module_seeded_url, client, cookie, body
):
    before = (family_count(module_seeded_url), family_event_count(module_seeded_url))

    response = post(client, cookie, FAMILIES, body)

    assert code_of(response) == (422, "validation_error")
    assert (
        family_count(module_seeded_url),
        family_event_count(module_seeded_url),
    ) == before


def test_a_200_character_name_is_accepted(client, cookie):
    assert create_family(client, cookie, name="F" * 200)["name"] == "F" * 200


# --- B2: edit -----------------------------------------------------------------------


def test_rename(module_seeded_url, client, cookie):
    family = create_family(client, cookie)

    response = update(client, cookie, family, {"version": 1, "name": " Familia Nueva "})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["version"]) == ("Familia Nueva", 2)
    assert family_row(module_seeded_url, family["id"]).name == "Familia Nueva"


def test_deactivate_and_reactivate_keep_the_members(module_seeded_url, client, cookie):
    family = create_family(client, cookie)
    student = create_student(client, cookie)
    added(client, cookie, family["id"], "students", student["id"])

    off = update(client, cookie, family, {"version": 1, "is_active": False})
    assert off.status_code == 200, off.text
    assert (off.json()["is_active"], off.json()["version"]) == (False, 2)
    assert [s["student_id"] for s in off.json()["students"]] == [student["id"]]

    on = update(client, cookie, family, {"version": 2, "is_active": True})
    assert (on.json()["is_active"], on.json()["version"]) == (True, 3)
    assert [s["student_id"] for s in on.json()["students"]] == [student["id"]]


def test_a_stale_version_is_409_and_changes_nothing(module_seeded_url, client, cookie):
    family = create_family(client, cookie)
    update(client, cookie, family, {"version": 1, "name": "Familia Dos"})
    events = family_event_count(module_seeded_url)

    response = update(client, cookie, family, {"version": 1, "name": "Familia Tres"})

    assert code_of(response) == (409, "version_conflict")
    row = family_row(module_seeded_url, family["id"])
    assert (row.name, row.version) == ("Familia Dos", 2)
    assert family_event_count(module_seeded_url) == events


def test_a_no_op_is_200_without_a_version_change(module_seeded_url, client, cookie):
    family = create_family(client, cookie)
    before = family_row(module_seeded_url, family["id"])
    events = family_event_count(module_seeded_url)

    for body in (
        {"version": 1},
        {"version": 1, "name": family["name"]},
        {"version": 1, "name": f" {family['name']} ", "is_active": True},
    ):
        response = update(client, cookie, family, body)
        assert response.status_code == 200, response.text
        assert response.json()["version"] == 1

    after = family_row(module_seeded_url, family["id"])
    assert (after.version, after.updated_at) == (1, before.updated_at)
    assert family_event_count(module_seeded_url) == events


@pytest.mark.parametrize(
    "body",
    [
        {"version": 1, "name": None},
        {"version": 1, "is_active": None},
        {"version": 1, "name": "  "},
        {"name": "Sin versión"},
        {"version": 0, "name": "Cero"},
        {"version": 1, "unknown": 1},
    ],
    ids=str,
)
def test_invalid_edits_are_422(module_seeded_url, client, cookie, body):
    family = create_family(client, cookie)

    assert code_of(update(client, cookie, family, body)) == (422, "validation_error")
    assert family_row(module_seeded_url, family["id"]).version == 1


def test_the_body_is_checked_before_existence(client, cookie):
    # 422 → 404 → 409.
    missing = {"id": MISSING}
    assert code_of(update(client, cookie, missing, {"version": 1, "name": None})) == (
        422,
        "validation_error",
    )
    assert code_of(update(client, cookie, missing, {"version": 9, "name": "X"})) == (
        404,
        "not_found",
    )


# --- Only family.manage writes ------------------------------------------------------


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN]
)
def test_only_family_managers_may_create_or_edit(
    module_seeded_url, client, cookie, role
):
    family = create_family(client, cookie)
    person = create_user(module_seeded_url, roles=(role,))
    other = sign_in(client, person.username)
    before = family_count(module_seeded_url)

    created = post(client, other, FAMILIES, family_body())
    edited = update(client, other, family, {"version": 1, "name": "X"})

    assert code_of(created) == (403, "permission_denied")
    assert code_of(edited) == (403, "permission_denied")
    assert family_count(module_seeded_url) == before
    assert family_row(module_seeded_url, family["id"]).version == 1


def test_an_administrator_may_create_and_edit(client, module_seeded_url):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)

    family = create_family(client, admin_cookie)
    edited = update(client, admin_cookie, family, {"version": 1, "name": "Familia A"})

    assert edited.status_code == 200


# --- B3/B4: list and detail ---------------------------------------------------------


@pytest.fixture(scope="module")
def dataset(module_seeded_url, client, owner) -> dict[str, dict[str, Any]]:
    staff = sign_in(client, owner.username)
    word = suffix()
    names = {
        "garcia": f"Familia García López {word}",
        "nunez": f"Familia Ñúñez {word}",
        "perez": f"Familia Pérez {word}",
        "old": f"Familia Antigua {word}",
    }
    families = {
        key: create_family(client, staff, name=name) for key, name in names.items()
    }
    families["old"] = update(
        client, staff, families["old"], {"version": 1, "is_active": False}
    ).json()
    for _ in range(2):
        student = create_student(client, staff)
        added(client, staff, families["garcia"]["id"], "students", student["id"])
    return {"word": {"value": word}, **families}


def listed(client, cookie, **params: Any) -> list[str]:
    response = get(client, cookie, FAMILIES, **params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


@pytest.mark.parametrize(
    ("q", "found"),
    [
        ("garcia", ["garcia"]),
        ("GARCÍA lópez", ["garcia"]),
        ("lopez garcia", ["garcia"]),
        ("nunez", ["nunez"]),
        ("Ñúñez", ["nunez"]),
    ],
)
def test_search(client, cookie, dataset, q, found):
    word = dataset["word"]["value"]
    ids = listed(client, cookie, q=f"{q} {word}", status="all")

    # The shared word makes every dataset family similar: the match ranks
    # first (relevance, then name).
    assert ids[: len(found)] == [dataset[key]["id"] for key in found]


@pytest.mark.parametrize("q", ["garcia", "Garcias"])
def test_search_without_accents_or_with_a_typo(client, cookie, dataset, q):
    ids = listed(client, cookie, q=q, status="all", limit=100)

    assert dataset["garcia"]["id"] in ids
    assert dataset["perez"]["id"] not in ids


def test_status_filter_order_and_pagination(client, cookie, dataset):
    word = dataset["word"]["value"]
    order = ["garcia", "nunez", "perez"]

    active = get(client, cookie, FAMILIES, q=word).json()
    inactive = listed(client, cookie, q=word, status="inactive")
    every = listed(client, cookie, q=word, status="all")
    page = get(client, cookie, FAMILIES, q=word, limit=1, offset=1).json()

    assert {item["id"] for item in active["items"]} == {dataset[k]["id"] for k in order}
    assert active["total"] == 3
    assert inactive == [dataset["old"]["id"]]
    assert set(every) == {dataset[k]["id"] for k in [*order, "old"]}
    assert page["total"] == 3
    assert (page["limit"], page["offset"], len(page["items"])) == (1, 1, 1)


def test_the_default_order_is_by_normalized_name(client, cookie, dataset):
    word = dataset["word"]["value"]
    response = get(client, cookie, FAMILIES, status="all", limit=100)
    names = [i["name"] for i in response.json()["items"] if word in i["name"]]

    assert names == [dataset[k]["name"] for k in ("old", "garcia", "nunez", "perez")]


def test_list_items_carry_the_student_count_and_no_guardian_count(
    client, cookie, dataset
):
    response = get(client, cookie, FAMILIES, q=dataset["garcia"]["name"])
    [item] = [i for i in response.json()["items"] if i["id"] == dataset["garcia"]["id"]]

    assert item == {
        "id": dataset["garcia"]["id"],
        "name": dataset["garcia"]["name"],
        "is_active": True,
        "student_count": 2,
    }


@pytest.mark.parametrize("q", ["", "   ", "a" * 101, "uno dos tres cuatro cinco seis"])
def test_invalid_searches_are_422(client, cookie, q):
    assert code_of(get(client, cookie, FAMILIES, q=q)) == (422, "validation_error")


@pytest.mark.parametrize("params", [{"status": "gone"}, {"limit": 0}, {"offset": -1}])
def test_invalid_parameters_are_422(client, cookie, params):
    assert code_of(get(client, cookie, FAMILIES, **params)) == (
        422,
        "validation_error",
    )


def test_the_list_makes_two_statements(module_seeded_url, owner):
    word = suffix()

    async def thirty(session) -> None:
        for n in range(30):
            family = Family(name=f"Familia Cuenta {word} {n:02d}")
            session.add(family)
            await session.flush()
            student = await persist_student(session)
            session.add(FamilyStudent(family_id=family.id, student_id=student.id))
        await session.flush()

    in_uow(module_seeded_url, thirty)
    caller = principal_for(owner.id, OWNER)

    for q in (None, f"cuenta {word}"):
        statements, page = count_statements(
            module_seeded_url,
            lambda session, q=q: service.list_families(
                session,
                principal=caller,
                q=q,
                status="all",
                page=OffsetParams(limit=30, offset=0),
            ),
        )
        assert statements == 2, q
        assert len(page.items) == 30
    assert all(item.student_count == 1 for item in page.items)


def test_a_missing_family_is_404(client, cookie):
    assert code_of(get(client, cookie, family_path(MISSING))) == (404, "not_found")
    assert code_of(get(client, cookie, f"{FAMILIES}/not-a-uuid")) == (
        422,
        "validation_error",
    )


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, ADMINISTRATOR])
def test_staff_with_guardian_view_read_every_family(
    module_seeded_url, client, dataset, role
):
    person = create_user(module_seeded_url, roles=(role,))
    other = sign_in(client, person.username)

    detail = get(client, other, family_path(dataset["garcia"]["id"]))
    ids = listed(client, other, q=dataset["word"]["value"], status="all")

    assert detail.status_code == 200
    assert len(detail.json()["students"]) == 2
    assert len(ids) == 4


@pytest.mark.parametrize("role", [TUTOR, STUDENT])
def test_roles_without_guardian_view_get_403(module_seeded_url, client, dataset, role):
    person = create_user(module_seeded_url, roles=(role,))
    other = sign_in(client, person.username)

    assert code_of(get(client, other, FAMILIES)) == (403, "permission_denied")
    assert code_of(get(client, other, family_path(dataset["garcia"]["id"]))) == (
        403,
        "permission_denied",
    )
