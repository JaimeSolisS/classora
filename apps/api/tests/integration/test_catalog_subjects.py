"""Subjects over HTTP with real logins (#32 C4, C5, C6, C7, E1-E5, F2).

The module shares one seeded database; every test creates its own levels
with unique names and filters the subject list by them.
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
    SUBJECTS,
    catalog_event_count,
    count_statements,
    create_level,
    create_subject,
    get,
    level_path,
    patch,
    post,
    subject_path,
    subject_row,
    unique_name,
)
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


def subjects_path(level_id: Any) -> str:
    return f"{level_path(level_id)}/subjects"


def names(response) -> list[str]:
    assert code_of(response) == (200, None), response.text
    return [item["name"] for item in response.json()["items"]]


def deactivate_level(client, cookie, level: dict[str, Any]) -> None:
    response = patch(
        client,
        cookie,
        level_path(level["id"]),
        {"version": level["version"], "is_active": False},
    )
    assert response.status_code == 200, response.text


# --- C: reads -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [STUDENT, GUARDIAN, TUTOR, ACADEMIC_COORDINATOR, FINANCE_STAFF, ADMINISTRATOR],
)
def test_every_signed_in_role_can_list_and_read_subjects(
    module_seeded_url, client, cookie, role
):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    reader = create_user(module_seeded_url, roles=(role,))
    reader_cookie = sign_in(client, reader.username)

    listed = get(client, reader_cookie, SUBJECTS, education_level_id=level["id"])
    detail = get(client, reader_cookie, subject_path(subject["id"]))

    assert listed.json()["items"] == [subject]
    assert detail.json() == subject


def test_the_detail_has_exactly_the_documented_fields(client, cookie):
    level = create_level(client, cookie)

    subject = create_subject(client, cookie, level["id"])

    assert set(subject) == {
        "id",
        "education_level_id",
        "education_level_name",
        "name",
        "is_active",
        "version",
        "created_at",
        "updated_at",
    }
    assert subject["education_level_id"] == level["id"]
    assert subject["education_level_name"] == level["name"]
    assert (subject["is_active"], subject["version"]) == (True, 1)


def test_status_and_level_filters(client, cookie):
    level = create_level(client, cookie)
    other = create_level(client, cookie)
    create_subject(client, cookie, level["id"], "Biología")
    retired = create_subject(client, cookie, level["id"], "Álgebra")
    create_subject(client, cookie, other["id"], "Química")
    patch(
        client, cookie, subject_path(retired["id"]), {"version": 1, "is_active": False}
    )
    scope = {"education_level_id": level["id"]}

    assert names(get(client, cookie, SUBJECTS, **scope)) == ["Biología"]
    assert names(get(client, cookie, SUBJECTS, status="inactive", **scope)) == [
        "Álgebra"
    ]
    assert names(get(client, cookie, SUBJECTS, status="all", **scope)) == [
        "Álgebra",
        "Biología",
    ]
    assert code_of(get(client, cookie, SUBJECTS, status="none")) == (
        422,
        "validation_error",
    )


def test_an_unknown_level_gives_an_empty_page(client, cookie):
    response = get(client, cookie, SUBJECTS, education_level_id=uuid.uuid4())

    assert response.status_code == 200
    assert (response.json()["items"], response.json()["total"]) == ([], 0)


def test_the_list_is_ordered_by_level_then_subject_name(
    module_seeded_url, client, cookie
):
    first = create_level(client, cookie, "a " + unique_name(), sort_order=5)
    second = create_level(client, cookie, "b " + unique_name(), sort_order=5)
    for level in (second, first):
        for name in ("zeta", "Alfa", "beta"):
            create_subject(client, cookie, level["id"], name)

    def page(session):
        return service.list_subjects(
            session, status="all", page=OffsetParams(limit=100, offset=0)
        )

    statements, everything = count_statements(module_seeded_url, page)
    ours = [
        (item.education_level_name, item.name)
        for item in everything.items
        if item.education_level_id in {uuid.UUID(first["id"]), uuid.UUID(second["id"])}
    ]

    assert ours == [
        (first["name"], "Alfa"),
        (first["name"], "beta"),
        (first["name"], "zeta"),
        (second["name"], "Alfa"),
        (second["name"], "beta"),
        (second["name"], "zeta"),
    ]
    assert statements <= 3


def test_limit_offset_and_total(client, cookie):
    level = create_level(client, cookie)
    for name in ("A", "B", "C", "D", "E"):
        create_subject(client, cookie, level["id"], name)

    response = get(
        client, cookie, SUBJECTS, education_level_id=level["id"], limit=2, offset=2
    )

    assert names(response) == ["C", "D"]
    assert (response.json()["total"], response.json()["offset"]) == (5, 2)


def test_a_subject_page_takes_at_most_three_statements(
    module_seeded_url, client, cookie
):
    level = create_level(client, cookie)
    for _ in range(30):
        create_subject(client, cookie, level["id"])

    statements, page = count_statements(
        module_seeded_url,
        lambda session: service.list_subjects(
            session,
            education_level_id=uuid.UUID(level["id"]),
            page=OffsetParams(limit=30, offset=0),
        ),
    )

    assert len(page.items) == 30
    assert statements <= 3


def test_an_unknown_subject_is_404(client, cookie):
    assert code_of(get(client, cookie, subject_path(uuid.uuid4()))) == (
        404,
        "not_found",
    )


def test_a_subject_of_an_inactive_level_is_still_returned(client, cookie):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    patch(
        client, cookie, subject_path(subject["id"]), {"version": 1, "is_active": False}
    )
    deactivate_level(client, cookie, level)

    detail = get(client, cookie, subject_path(subject["id"])).json()

    assert (detail["is_active"], detail["education_level_name"]) == (
        False,
        level["name"],
    )


# --- E: create and update -----------------------------------------------------------


def test_create_trims_the_name(client, cookie):
    level = create_level(client, cookie)

    response = post(client, cookie, subjects_path(level["id"]), {"name": "  Física  "})

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Física"


def test_the_same_name_in_two_levels_is_two_subjects(client, cookie):
    secundaria = create_level(client, cookie)
    preparatoria = create_level(client, cookie)

    first = create_subject(client, cookie, secundaria["id"], "Matemáticas")
    second = create_subject(client, cookie, preparatoria["id"], "Matemáticas")

    assert first["id"] != second["id"]


@pytest.mark.parametrize("variant", ["same", "other_case", "inactive"])
def test_a_duplicate_name_in_the_level_is_409(
    module_seeded_url, client, cookie, variant
):
    level = create_level(client, cookie)
    existing = create_subject(client, cookie, level["id"], "Historia")
    if variant == "inactive":
        patch(
            client,
            cookie,
            subject_path(existing["id"]),
            {"version": 1, "is_active": False},
        )
    before = catalog_event_count(module_seeded_url)

    taken = "HISTORIA" if variant == "other_case" else "Historia"
    response = post(client, cookie, subjects_path(level["id"]), {"name": taken})

    assert code_of(response) == (409, "name_taken")
    assert catalog_event_count(module_seeded_url) == before


def test_a_rename_to_a_name_taken_in_the_level_is_409(
    module_seeded_url, client, cookie
):
    level = create_level(client, cookie)
    create_subject(client, cookie, level["id"], "Geografía")
    subject = create_subject(client, cookie, level["id"], "Historia")

    response = patch(
        client, cookie, subject_path(subject["id"]), {"version": 1, "name": "geografía"}
    )

    assert code_of(response) == (409, "name_taken")
    assert subject_row(module_seeded_url, subject["id"]).name == "Historia"


def test_a_rename_to_another_case_of_its_own_name_is_allowed(client, cookie):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"], "historia")

    response = patch(
        client, cookie, subject_path(subject["id"]), {"version": 1, "name": "Historia"}
    )

    assert (response.status_code, response.json()["name"]) == (200, "Historia")


def test_an_unknown_level_is_404(client, cookie):
    response = post(client, cookie, subjects_path(uuid.uuid4()), {"name": "Arte"})

    assert code_of(response) == (404, "not_found")


def test_create_and_reactivate_under_an_inactive_level_are_409(
    module_seeded_url, client, cookie
):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    patch(
        client, cookie, subject_path(subject["id"]), {"version": 1, "is_active": False}
    )
    deactivate_level(client, cookie, level)
    before = catalog_event_count(module_seeded_url)

    created = post(client, cookie, subjects_path(level["id"]), {"name": "Arte"})
    reactivated = patch(
        client, cookie, subject_path(subject["id"]), {"version": 2, "is_active": True}
    )

    assert code_of(created) == (409, "education_level_inactive")
    assert code_of(reactivated) == (409, "education_level_inactive")
    assert subject_row(module_seeded_url, subject["id"]).is_active is False
    assert catalog_event_count(module_seeded_url) == before


def test_a_subject_of_an_inactive_level_can_be_renamed(client, cookie):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    patch(
        client, cookie, subject_path(subject["id"]), {"version": 1, "is_active": False}
    )
    deactivate_level(client, cookie, level)

    response = patch(
        client,
        cookie,
        subject_path(subject["id"]),
        {"version": 2, "name": "Renombrada", "is_active": False},
    )

    assert response.status_code == 200, response.text
    assert (response.json()["name"], response.json()["version"]) == ("Renombrada", 3)


def test_deactivate_and_reactivate_a_subject(module_seeded_url, client, cookie):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    path = subject_path(subject["id"])

    off = patch(client, cookie, path, {"version": 1, "is_active": False})
    on = patch(client, cookie, path, {"version": 2, "is_active": True})

    assert (off.status_code, off.json()["is_active"]) == (200, False)
    assert (on.status_code, on.json()["is_active"]) == (200, True)
    row = subject_row(module_seeded_url, subject["id"])
    assert (row.is_active, row.version) == (True, 3)


def test_a_stale_version_is_409_and_changes_nothing(module_seeded_url, client, cookie):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    path = subject_path(subject["id"])
    patch(client, cookie, path, {"version": 1, "name": "Nueva"})

    response = patch(client, cookie, path, {"version": 1, "is_active": False})

    assert code_of(response) == (409, "version_conflict")
    row = subject_row(module_seeded_url, subject["id"])
    assert (row.is_active, row.version) == (True, 2)


def test_a_patch_that_changes_nothing_is_200_without_a_new_version(
    module_seeded_url, client, cookie
):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    before = catalog_event_count(module_seeded_url)

    response = patch(
        client,
        cookie,
        subject_path(subject["id"]),
        {"version": 1, "name": f"  {subject['name']}", "is_active": True},
    )

    assert response.status_code == 200, response.text
    assert response.json() == subject
    assert catalog_event_count(module_seeded_url) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version": 1, "education_level_id": "00000000-0000-4000-8000-000000000001"},
        {"version": 1, "name": None},
        {"version": 1, "is_active": None},
        {"version": 1, "name": ""},
        {"version": 1, "name": "y" * 101},
        {"version": 0, "name": "Valid"},
    ],
)
def test_invalid_patch_bodies_are_422(module_seeded_url, client, cookie, body):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])

    response = patch(client, cookie, subject_path(subject["id"]), body)

    assert code_of(response) == (422, "validation_error")
    row = subject_row(module_seeded_url, subject["id"])
    assert (str(row.education_level_id), row.version) == (level["id"], 1)


@pytest.mark.parametrize(
    "body", [{}, {"name": "  "}, {"name": "Arte", "education_level_id": None}]
)
def test_invalid_create_bodies_are_422(client, cookie, body):
    level = create_level(client, cookie)

    response = post(client, cookie, subjects_path(level["id"]), body)

    assert code_of(response) == (422, "validation_error")


def test_patch_on_an_unknown_subject_is_404(client, cookie):
    response = patch(
        client, cookie, subject_path(uuid.uuid4()), {"version": 1, "name": "X"}
    )

    assert code_of(response) == (404, "not_found")


@pytest.mark.parametrize("role", [TUTOR, STUDENT, GUARDIAN, FINANCE_STAFF])
def test_only_catalog_managers_change_a_subject(
    module_seeded_url, client, cookie, role
):
    level = create_level(client, cookie)
    subject = create_subject(client, cookie, level["id"])
    other = create_user(module_seeded_url, roles=(role,))
    other_cookie = sign_in(client, other.username)

    response = patch(
        client, other_cookie, subject_path(subject["id"]), {"version": 1, "name": "X"}
    )

    assert code_of(response) == (403, "permission_denied")
    assert subject_row(module_seeded_url, subject["id"]).version == 1


# --- F2: audit ----------------------------------------------------------------------


def test_subject_create_rename_and_deactivation_are_audited(
    module_seeded_url, client, cookie, owner
):
    level = create_level(client, cookie)
    created = post(client, cookie, subjects_path(level["id"]), {"name": "Lógica"})
    subject_id = created.json()["id"]
    path = subject_path(subject_id)
    renamed = patch(client, cookie, path, {"version": 1, "name": "Lógica I"})
    deactivated = patch(client, cookie, path, {"version": 2, "is_active": False})

    recorded = events(
        module_seeded_url, record_type="subjects", record_id=uuid.UUID(subject_id)
    )

    assert [e.action for e in recorded] == ["create", "update", "update"]
    assert {e.actor_user_id for e in recorded} == {owner.id}
    assert [e.request_id for e in recorded] == [
        r.headers["X-Request-ID"] for r in (created, renamed, deactivated)
    ]
    assert recorded[0].changes == {
        "education_level_id": {"old": None, "new": level["id"]},
        "name": {"old": None, "new": "Lógica"},
        "is_active": {"old": None, "new": True},
    }
    assert recorded[1].changes == {"name": {"old": "Lógica", "new": "Lógica I"}}
    assert recorded[2].changes == {"is_active": {"old": True, "new": False}}
