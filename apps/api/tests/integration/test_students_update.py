"""Changing student profiles over HTTP with real logins (#33 D1-D6)."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import OWNER
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import (
    LEVELS,
    create_level,
    get,
    level_path,
    patch,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.student_support import (
    as_update_items,
    contact,
    contact_rows,
    contacts_of,
    create_student,
    family_event_count,
    student_path,
    student_row,
)

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


@pytest.fixture(scope="module")
def levels(client, owner) -> dict[str, dict[str, Any]]:
    cookie = sign_in(client, owner.username)
    items = get(client, cookie, LEVELS, limit=100).json()["items"]
    return {level["name"]: level for level in items}


def inactive_level(client, cookie) -> dict[str, Any]:
    level = create_level(client, cookie, sort_order=9000)
    response = patch(
        client, cookie, level_path(level["id"]), {"version": 1, "is_active": False}
    )
    assert response.status_code == 200, response.text
    detail: dict[str, Any] = response.json()
    return detail


def update(client, cookie, student, body) -> Any:
    return patch(client, cookie, student_path(student["id"]), body)


def ok(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    detail: dict[str, Any] = response.json()
    return detail


def three_contacts(client, cookie) -> dict[str, Any]:
    return create_student(
        client,
        cookie,
        emergency_contacts=[
            contact("Ana", "Madre", "55 1111 1111"),
            contact("Beto", "Padre", "55 2222 2222"),
            contact("Carla", "Tía", "55 3333 3333"),
        ],
    )


def summary(detail) -> list[tuple[str, int]]:
    return [(c["name"], c["position"]) for c in contacts_of(detail)]


# --- D1, D5: fields -----------------------------------------------------------


def test_rename(module_seeded_url, client, cookie):
    student = create_student(client, cookie)

    detail = ok(
        update(client, cookie, student, {"version": 1, "given_names": " Luis "})
    )

    assert detail["given_names"] == "Luis"
    assert detail["version"] == 2
    assert detail["updated_at"] > student["updated_at"]
    assert student_row(module_seeded_url, student["id"]).given_names == "Luis"


def test_clear_email_with_null_and_empty_string(client, cookie):
    student = create_student(
        client, cookie, email="a@example.com", phone="55 1234 5678"
    )

    detail = ok(update(client, cookie, student, {"version": 1, "email": None}))
    detail = ok(update(client, cookie, detail, {"version": 2, "phone": ""}))

    assert detail["personal"]["email"] is None
    assert detail["personal"]["phone"] is None
    assert detail["version"] == 3


def test_set_and_clear_the_level(client, cookie, levels):
    student = create_student(client, cookie)
    primaria = levels["Primaria"]

    detail = ok(
        update(
            client,
            cookie,
            student,
            {"version": 1, "education_level_id": primaria["id"]},
        )
    )
    assert detail["education_level"] == {
        "id": primaria["id"],
        "name": "Primaria",
        "is_active": True,
    }
    detail = ok(
        update(client, cookie, detail, {"version": 2, "education_level_id": None})
    )
    assert detail["education_level"] is None


def test_deactivate_and_reactivate_keep_everything_else(
    module_seeded_url, client, cookie
):
    student = three_contacts(client, cookie)

    inactive = ok(update(client, cookie, student, {"version": 1, "is_active": False}))
    assert inactive["is_active"] is False
    assert contacts_of(inactive) == contacts_of(student)
    # Still editable while inactive.
    renamed = ok(
        update(client, cookie, inactive, {"version": 2, "school_grade": "3.º"})
    )
    active = ok(update(client, cookie, renamed, {"version": 3, "is_active": True}))

    assert active["is_active"] is True
    assert active["school_grade"] == "3.º"
    assert active["given_names"] == student["given_names"]
    assert len(contact_rows(module_seeded_url, student["id"])) == 3


NULL_FIELDS = [
    "given_names",
    "family_names",
    "birthdate",
    "is_active",
    "emergency_contacts",
]


@pytest.mark.parametrize("field", NULL_FIELDS)
def test_null_for_a_required_field_is_422(module_seeded_url, client, cookie, field):
    student = create_student(client, cookie)

    response = update(client, cookie, student, {"version": 1, field: None})

    assert code_of(response) == (422, "validation_error")
    assert student_row(module_seeded_url, student["id"]).version == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version": 0},
        {"version": 1, "nickname": "x"},
        {"version": 1, "given_names": " "},
        {"version": 1, "birthdate": "1899-12-31"},
        {"version": 1, "emergency_contacts": []},
        {"version": 1, "emergency_contacts": [contact() for _ in range(6)]},
    ],
    ids=["no version", "version 0", "unknown field", "blank", "1899", "[]", "six"],
)
def test_invalid_bodies_are_422_and_change_nothing(
    module_seeded_url, client, cookie, body
):
    student = create_student(client, cookie)
    events = family_event_count(module_seeded_url)

    response = update(client, cookie, student, body)

    assert code_of(response) == (422, "validation_error")
    assert student_row(module_seeded_url, student["id"]).version == 1
    assert family_event_count(module_seeded_url) == events


def test_a_future_birthdate_is_422(clock, module_seeded_url, client, owner):
    clock.set(datetime(2026, 10, 19, 5, 30, tzinfo=UTC))  # 2026-10-18 in CDMX
    cookie = sign_in(client, owner.username)
    student = create_student(client, cookie)

    future = update(client, cookie, student, {"version": 1, "birthdate": "2026-10-19"})
    today = update(client, cookie, student, {"version": 1, "birthdate": "2026-10-18"})

    assert code_of(future) == (422, "validation_error")
    assert future.json()["errors"][0]["path"] == ["body", "birthdate"]
    assert ok(today)["personal"]["birthdate"] == "2026-10-18"


def test_unknown_student_is_404(client, cookie):
    response = patch(client, cookie, student_path(MISSING), {"version": 1})

    assert code_of(response) == (404, "not_found")


# --- D3: contacts ----------------------------------------------------------------


def test_edit_one_contact_in_place_keeps_its_id(module_seeded_url, client, cookie):
    student = three_contacts(client, cookie)
    items = as_update_items(contacts_of(student))
    items[1]["phone"] = "55 9999 9999"

    detail = ok(
        update(client, cookie, student, {"version": 1, "emergency_contacts": items})
    )

    assert [c["id"] for c in contacts_of(detail)] == [
        c["id"] for c in contacts_of(student)
    ]
    assert contacts_of(detail)[1]["phone"] == "55 9999 9999"
    assert detail["version"] == 2


def test_add_remove_and_reorder(module_seeded_url, client, cookie):
    student = three_contacts(client, cookie)
    ana, beto, carla = as_update_items(contacts_of(student))

    # Remove Beto, swap Ana and Carla, add Dora at the end.
    detail = ok(
        update(
            client,
            cookie,
            student,
            {
                "version": 1,
                "emergency_contacts": [
                    carla,
                    ana,
                    contact("Dora", "Vecina", "55 4444 4444"),
                ],
            },
        )
    )

    assert summary(detail) == [("Carla", 1), ("Ana", 2), ("Dora", 3)]
    assert contacts_of(detail)[0]["id"] == carla["id"]
    assert contacts_of(detail)[1]["id"] == ana["id"]
    rows = contact_rows(module_seeded_url, student["id"])
    assert [(r.name, r.position) for r in rows] == summary(detail)
    assert beto["id"] not in {str(r.id) for r in rows}


def test_a_pure_reorder_swaps_positions(client, cookie):
    student = three_contacts(client, cookie)
    ana, beto, carla = as_update_items(contacts_of(student))

    detail = ok(
        update(
            client,
            cookie,
            student,
            {"version": 1, "emergency_contacts": [beto, ana, carla]},
        )
    )

    assert summary(detail) == [("Beto", 1), ("Ana", 2), ("Carla", 3)]


def test_replace_all(module_seeded_url, client, cookie):
    student = three_contacts(client, cookie)

    detail = ok(
        update(
            client,
            cookie,
            student,
            {
                "version": 1,
                "emergency_contacts": [contact("Eva", "Abuela", "55 5555 5555")],
            },
        )
    )

    assert summary(detail) == [("Eva", 1)]
    assert len(contact_rows(module_seeded_url, student["id"])) == 1


def test_five_new_contacts_replacing_five(client, cookie):
    student = create_student(
        client, cookie, emergency_contacts=[contact(f"Viejo {n}") for n in range(5)]
    )

    detail = ok(
        update(
            client,
            cookie,
            student,
            {
                "version": 1,
                "emergency_contacts": [contact(f"Nuevo {n}") for n in range(5)],
            },
        )
    )

    assert [c["name"] for c in contacts_of(detail)] == [f"Nuevo {n}" for n in range(5)]


def test_foreign_and_duplicate_contact_ids_are_422_and_change_nothing(
    module_seeded_url, client, cookie
):
    student = three_contacts(client, cookie)
    other = create_student(client, cookie)
    items = as_update_items(contacts_of(student))
    foreign = as_update_items(contacts_of(other))[0]
    before = contact_rows(module_seeded_url, student["id"])
    events = family_event_count(module_seeded_url)

    cases = [
        [items[0], foreign],
        [items[0], items[0]],
        [items[0], {**contact("Nuevo"), "id": MISSING}],
    ]
    for contacts in cases:
        response = update(
            client,
            cookie,
            student,
            {"version": 1, "given_names": "X", "emergency_contacts": contacts},
        )
        assert code_of(response) == (422, "emergency_contact_unknown"), response.text
        assert response.json()["errors"] == [
            {
                "path": ["body", "emergency_contacts", 1, "id"],
                "code": "emergency_contact_unknown",
                "message": "Not one of this student's emergency contacts.",
            }
        ]

    assert contact_rows(module_seeded_url, student["id"]) == before
    assert student_row(module_seeded_url, student["id"]).version == 1
    assert family_event_count(module_seeded_url) == events


# --- D2, D4: versions ------------------------------------------------------------


def test_a_stale_version_is_409_and_changes_nothing(module_seeded_url, client, cookie):
    student = three_contacts(client, cookie)
    ok(update(client, cookie, student, {"version": 1, "given_names": "Nuevo"}))
    before = contact_rows(module_seeded_url, student["id"])
    events = family_event_count(module_seeded_url)

    stale = update(
        client,
        cookie,
        student,
        {
            "version": 1,
            "family_names": "Viejo",
            "emergency_contacts": [contact("Solo")],
        },
    )

    assert code_of(stale) == (409, "version_conflict")
    row = student_row(module_seeded_url, student["id"])
    assert (row.given_names, row.version) == ("Nuevo", 2)
    assert contact_rows(module_seeded_url, student["id"]) == before
    assert family_event_count(module_seeded_url) == events


def test_a_stale_version_with_a_contacts_only_change_is_409(
    module_seeded_url, client, cookie
):
    student = three_contacts(client, cookie)
    ok(update(client, cookie, student, {"version": 1, "school_grade": "1.º"}))
    before = contact_rows(module_seeded_url, student["id"])

    stale = update(
        client, cookie, student, {"version": 1, "emergency_contacts": [contact("Solo")]}
    )

    assert code_of(stale) == (409, "version_conflict")
    assert contact_rows(module_seeded_url, student["id"]) == before


def test_a_no_op_patch_changes_nothing(module_seeded_url, client, cookie):
    student = three_contacts(client, cookie)
    events = family_event_count(module_seeded_url)

    detail = ok(
        update(
            client,
            cookie,
            student,
            {
                "version": 1,
                "given_names": student["given_names"],
                "email": None,
                "education_level_id": None,
                "is_active": True,
                "emergency_contacts": as_update_items(contacts_of(student)),
            },
        )
    )

    assert detail == student
    assert student_row(module_seeded_url, student["id"]).version == 1
    assert family_event_count(module_seeded_url) == events


def test_a_contacts_only_patch_increments_the_version_once(
    module_seeded_url, client, cookie
):
    student = three_contacts(client, cookie)
    items = as_update_items(contacts_of(student))

    detail = ok(
        update(
            client,
            cookie,
            student,
            {
                "version": 1,
                "emergency_contacts": [items[2], items[0], contact("Nuevo")],
            },
        )
    )

    assert detail["version"] == 2
    assert detail["updated_at"] > student["updated_at"]
    row = student_row(module_seeded_url, student["id"])
    assert row.version == 2


def test_fields_and_contacts_together_increment_the_version_once(client, cookie):
    student = three_contacts(client, cookie)

    detail = ok(
        update(
            client,
            cookie,
            student,
            {
                "version": 1,
                "given_names": "Otro",
                "emergency_contacts": [contact("Solo")],
            },
        )
    )

    assert detail["version"] == 2


def test_concurrent_patches_with_one_version_give_one_200_and_one_409(
    module_seeded_url, client, owner
):
    cookie = sign_in(client, owner.username)
    student = three_contacts(client, cookie)
    bodies = [
        {"version": 1, "given_names": "Primero"},
        {"version": 1, "emergency_contacts": [contact("Segundo")]},
    ]
    results: list[int] = []
    start = threading.Barrier(2)

    def send(body: dict[str, Any]) -> None:
        with auth_client(module_seeded_url) as own_client:
            own_cookie = sign_in(own_client, owner.username)
            start.wait(timeout=10)
            results.append(update(own_client, own_cookie, student, body).status_code)

    threads = [threading.Thread(target=send, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == [200, 409]
    assert student_row(module_seeded_url, student["id"]).version == 2


# --- D2: levels ------------------------------------------------------------------


def test_keeping_an_inactive_level_is_allowed_and_switching_to_another_is_not(
    module_seeded_url, client, cookie, levels
):
    old = create_level(client, cookie, sort_order=9000)
    student = create_student(client, cookie, education_level_id=old["id"])
    ok(patch(client, cookie, level_path(old["id"]), {"version": 1, "is_active": False}))
    other = inactive_level(client, cookie)

    kept = ok(
        update(
            client,
            cookie,
            student,
            {"version": 1, "education_level_id": old["id"], "school_grade": "4.º"},
        )
    )
    assert kept["education_level"] == {
        "id": old["id"],
        "name": old["name"],
        "is_active": False,
    }
    switched = update(
        client, cookie, kept, {"version": 2, "education_level_id": other["id"]}
    )
    unknown = update(
        client, cookie, kept, {"version": 2, "education_level_id": MISSING}
    )

    assert code_of(switched) == (422, "education_level_unavailable")
    assert code_of(unknown) == (422, "education_level_unavailable")
    assert switched.json()["errors"][0]["path"] == ["body", "education_level_id"]
    assert student_row(module_seeded_url, student["id"]).version == 2
    # An active one is accepted.
    moved = ok(
        update(
            client,
            cookie,
            kept,
            {"version": 2, "education_level_id": levels["Primaria"]["id"]},
        )
    )
    assert moved["education_level"]["name"] == "Primaria"
