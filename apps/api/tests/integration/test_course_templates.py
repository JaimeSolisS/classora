"""Course templates over HTTP (#64 B, D, F1).

Most tests share one seeded database and use names and subjects of their
own; the list tests that need exact results use a fresh database.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import OffsetParams
from app.modules.courses import service
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
    count_statements,
    create_level,
    create_subject,
    get,
    level_path,
    patch,
    post,
    subject_path,
)
from tests.integration.conftest import run_sql
from tests.integration.courses_support import (
    TEMPLATES,
    assessment,
    course_event_count,
    create_template,
    field_errors,
    module,
    put,
    subjects,
    template_body,
    template_events,
    template_path,
    write_body,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of


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
def subject_ids(client, cookie) -> list[str]:
    return subjects(client, cookie, 2)


@pytest.fixture
def fresh(seeded_url: str) -> Iterator[tuple[str, TestClient, Any]]:
    """A database of this test only, its client and a signed-in Owner."""
    with auth_client(seeded_url) as fresh_client:
        person = create_user(seeded_url, roles=(OWNER,))
        yield seeded_url, fresh_client, sign_in(fresh_client, person.username)


def ok(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def template_row(url: str, template_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT name, is_active, version FROM course_templates WHERE id = :id",
        {"id": uuid.UUID(str(template_id))},
    )
    return row


DETAIL_FIELDS = {
    "id",
    "name",
    "subjects",
    "session_count",
    "session_minutes",
    "total_minutes",
    "module_count",
    "is_active",
    "version",
    "updated_at",
    "description",
    "min_attendance_percent",
    "modules",
    "assessments",
    "completion",
    "created_at",
}
SUMMARY_FIELDS = {
    "id",
    "name",
    "subjects",
    "session_count",
    "session_minutes",
    "total_minutes",
    "module_count",
    "is_active",
    "version",
    "updated_at",
}


# --- D1, D2: reads -------------------------------------------------------------------


@pytest.mark.parametrize("role", [FINANCE_STAFF, ACADEMIC_COORDINATOR, ADMINISTRATOR])
def test_course_view_holders_list_and_read(
    module_seeded_url, client, cookie, subject_ids, role
):
    template = create_template(client, cookie, subject_ids)
    reader = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)

    assert code_of(get(client, reader, TEMPLATES, limit=100)) == (200, None)
    assert ok(get(client, reader, template_path(template["id"]))) == template


@pytest.mark.parametrize("role", [TUTOR, STUDENT, GUARDIAN])
def test_other_roles_get_403(module_seeded_url, client, cookie, subject_ids, role):
    template = create_template(client, cookie, subject_ids)
    reader = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)

    assert code_of(get(client, reader, TEMPLATES)) == (403, "permission_denied")
    assert code_of(get(client, reader, template_path(template["id"]))) == (
        403,
        "permission_denied",
    )


def test_create_returns_the_whole_detail(client, cookie, subject_ids):
    body = template_body(subject_ids)

    response = post(client, cookie, TEMPLATES, body)

    assert response.status_code == 201, response.text
    detail = response.json()
    assert set(detail) == DETAIL_FIELDS
    assert detail["name"] == body["name"]
    assert detail["description"] == "Un curso de prueba"
    assert (
        detail["session_count"],
        detail["session_minutes"],
        detail["total_minutes"],
    ) == (10, 90, 900)
    assert (detail["module_count"], detail["is_active"], detail["version"]) == (
        3,
        True,
        1,
    )
    assert detail["min_attendance_percent"] == 80
    # Catalog order (level, then name), not the order sent.
    assert sorted(s["id"] for s in detail["subjects"]) == sorted(subject_ids)
    assert set(detail["subjects"][0]) == {"id", "name", "level_name", "is_active"}
    modules = detail["modules"]
    assert [(m["position"], m["title"]) for m in modules] == [
        (1, "Introducción"),
        (2, "Desarrollo"),
        (3, "Cierre"),
    ]
    assert modules[0]["subject_id"] == subject_ids[0]
    assert modules[1]["materials"] == "Libro, capítulo 2"
    [exam] = detail["assessments"]
    assert exam["position"] == 1
    assert exam["module_id"] == modules[1]["id"]
    assert exam["min_score_percent"] == 60
    assert detail["completion"] == {
        "min_attendance_percent": 80,
        "required_module_ids": [modules[0]["id"], modules[1]["id"]],
        "required_assessment_ids": [exam["id"]],
    }


def test_a_template_without_criteria_is_valid(client, cookie, subject_ids):
    detail = create_template(
        client,
        cookie,
        subject_ids,
        min_attendance_percent=None,
        modules=[module("Único", is_required=False)],
        assessments=[],
    )

    assert detail["completion"] == {
        "min_attendance_percent": None,
        "required_module_ids": [],
        "required_assessment_ids": [],
    }


def test_status_filter_order_and_pagination(fresh):
    url, fresh_client, owner_cookie = fresh
    ids = subjects(fresh_client, owner_cookie, 1)
    for name in ("b curso", "Curso C", "A curso"):
        create_template(fresh_client, owner_cookie, ids, name=name)
    inactive = create_template(fresh_client, owner_cookie, ids, name="Bodega")
    ok(
        patch(
            fresh_client,
            owner_cookie,
            template_path(inactive["id"]),
            {"version": 1, "is_active": False},
        )
    )

    def names(**params: Any) -> list[str]:
        page = ok(get(fresh_client, owner_cookie, TEMPLATES, **params))
        return [item["name"] for item in page["items"]]

    assert names() == ["A curso", "b curso", "Curso C"]
    assert names(status="inactive") == ["Bodega"]
    assert names(status="all") == ["A curso", "b curso", "Bodega", "Curso C"]
    page = ok(
        get(fresh_client, owner_cookie, TEMPLATES, status="all", limit=2, offset=1)
    )
    assert [item["name"] for item in page["items"]] == ["b curso", "Bodega"]
    assert (page["total"], page["limit"], page["offset"]) == (4, 2, 1)
    [item] = ok(get(fresh_client, owner_cookie, TEMPLATES, status="inactive"))["items"]
    assert set(item) == SUMMARY_FIELDS
    assert item["module_count"] == 3
    assert item["total_minutes"] == 900
    assert [s["id"] for s in item["subjects"]] == ids


@pytest.mark.parametrize(
    "params", [{"status": "deleted"}, {"limit": 0}, {"limit": 101}, {"offset": -1}]
)
def test_invalid_list_queries_are_422(client, cookie, params):
    assert code_of(get(client, cookie, TEMPLATES, **params)) == (
        422,
        "validation_error",
    )


def test_a_page_of_thirty_templates_takes_three_statements(fresh):
    url, fresh_client, owner_cookie = fresh
    ids = subjects(fresh_client, owner_cookie, 3)
    for index in range(30):
        create_template(
            fresh_client,
            owner_cookie,
            ids[index % 3 :] or ids,
            name=f"Curso {index:02}",
        )

    statements, page = count_statements(
        url,
        lambda session: service.list_course_templates(
            session, status="all", page=OffsetParams(limit=100, offset=0)
        ),
    )

    assert statements <= 3
    assert len(page.items) == 30
    assert all(item.subjects for item in page.items)


def test_an_unknown_template_is_404_and_an_inactive_one_is_returned(
    client, cookie, subject_ids
):
    template = create_template(client, cookie, subject_ids)
    ok(
        patch(
            client,
            cookie,
            template_path(template["id"]),
            {"version": 1, "is_active": False},
        )
    )

    assert code_of(get(client, cookie, template_path(uuid.uuid4()))) == (
        404,
        "not_found",
    )
    detail = ok(get(client, cookie, template_path(template["id"])))
    assert (detail["is_active"], detail["version"]) == (False, 2)


# --- D3: create ----------------------------------------------------------------------


def test_create_records_one_event_per_row(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)

    events = template_events(module_seeded_url, detail["id"])
    assert sorted((table, action) for table, action, _ in events) == sorted(
        [
            ("course_templates", "create"),
            ("course_template_subjects", "create"),
            ("course_template_subjects", "create"),
            ("course_template_modules", "create"),
            ("course_template_modules", "create"),
            ("course_template_modules", "create"),
            ("course_template_assessments", "create"),
        ]
    )


@pytest.mark.parametrize("field", ["modules", "assessments"])
def test_ids_in_a_post_are_422(client, cookie, subject_ids, field):
    body = template_body(subject_ids)
    body[field][0]["id"] = str(uuid.uuid4())

    assert field_errors(post(client, cookie, TEMPLATES, body)) == [
        (["body", field, 0, "id"], "item_unknown")
    ]


def test_a_name_in_another_case_or_of_an_inactive_template_is_taken(
    client, cookie, subject_ids
):
    first = create_template(client, cookie, subject_ids)
    ok(
        patch(
            client,
            cookie,
            template_path(first["id"]),
            {"version": 1, "is_active": False},
        )
    )

    response = post(
        client,
        cookie,
        TEMPLATES,
        template_body(subject_ids, name=first["name"].upper()),
    )

    assert code_of(response) == (409, "name_taken")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            lambda ids: {"subject_ids": [ids[0], ids[1], ids[0]]},
            [(["body", "subject_ids", 2], "duplicate_subject")],
        ),
        (
            lambda ids: {
                "subject_ids": [],
                "modules": [module("Uno")],
                "assessments": [],
            },
            [(["body", "subject_ids"], "too_short")],
        ),
        (
            lambda ids: {"modules": [], "assessments": []},
            [(["body", "modules"], "too_short")],
        ),
        (
            lambda ids: {"modules": [module("Uno"), module("UNO")]},
            [(["body", "modules", 1, "title"], "duplicate_title")],
        ),
        (
            lambda ids: {"subject_ids": [ids[1]]},
            [(["body", "modules", 0, "subject_id"], "module_subject_not_in_template")],
        ),
        (
            lambda ids: {
                "modules": [
                    module("Uno", planned_sessions=6),
                    module("Dos", planned_sessions=5),
                ]
            },
            [(["body", "modules"], "planned_sessions_exceed_total")],
        ),
        (
            lambda ids: {"assessments": [assessment("A"), assessment("a")]},
            [(["body", "assessments", 1, "title"], "duplicate_title")],
        ),
        (
            lambda ids: {"assessments": [assessment("A", module_index=3)]},
            [(["body", "assessments", 0, "module_index"], "module_index_invalid")],
        ),
    ],
)
def test_every_template_rule_is_a_422_with_its_path(
    module_seeded_url, client, cookie, subject_ids, change, expected
):
    before = course_event_count(module_seeded_url)

    response = post(
        client, cookie, TEMPLATES, {**template_body(subject_ids), **change(subject_ids)}
    )

    assert field_errors(response) == expected
    assert course_event_count(module_seeded_url) == before


def test_unknown_and_inactive_subjects_are_422_together(
    module_seeded_url, client, cookie
):
    level = create_level(client, cookie)
    active = create_subject(client, cookie, level["id"])["id"]
    inactive = create_subject(client, cookie, level["id"])["id"]
    ok(
        patch(
            client, cookie, subject_path(inactive), {"version": 1, "is_active": False}
        )
    )
    other_level = create_level(client, cookie)
    under_inactive_level = create_subject(client, cookie, other_level["id"])["id"]
    ok(
        patch(
            client,
            cookie,
            subject_path(under_inactive_level),
            {"version": 1, "is_active": False},
        )
    )
    ok(
        patch(
            client,
            cookie,
            level_path(other_level["id"]),
            {"version": 1, "is_active": False},
        )
    )
    before = course_event_count(module_seeded_url)

    response = post(
        client,
        cookie,
        TEMPLATES,
        template_body(
            [active, str(uuid.uuid4()), inactive, under_inactive_level],
            modules=[module("Uno")],
            assessments=[],
        ),
    )

    assert field_errors(response) == [
        (["body", "subject_ids", 1], "subject_unknown"),
        (["body", "subject_ids", 2], "subject_inactive"),
        (["body", "subject_ids", 3], "subject_inactive"),
    ]
    assert course_event_count(module_seeded_url) == before


# --- D4: replace ---------------------------------------------------------------------


def test_editing_one_module_title_writes_that_change_and_bumps_the_version(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    before = template_events(module_seeded_url, detail["id"])
    body = write_body(detail)
    body["modules"][1]["title"] = "Desarrollo II"

    updated = ok(put(client, cookie, template_path(detail["id"]), body))

    assert updated["version"] == 2
    assert [m["title"] for m in updated["modules"]] == [
        "Introducción",
        "Desarrollo II",
        "Cierre",
    ]
    assert [m["id"] for m in updated["modules"]] == [m["id"] for m in detail["modules"]]
    new_events = template_events(module_seeded_url, detail["id"])[len(before) :]
    # The template's version bump changes no audited column (#22 leaves out
    # version and updated_at), so the module update is the only event.
    assert [(table, action) for table, action, _ in new_events] == [
        ("course_template_modules", "update")
    ]
    assert new_events[0][2] == {"title": {"old": "Desarrollo", "new": "Desarrollo II"}}
    assert template_row(module_seeded_url, detail["id"]).version == 2


def test_a_reorder_swaps_positions_and_records_only_position_changes(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    before = template_events(module_seeded_url, detail["id"])
    body = write_body(detail)
    first, second, third = body["modules"]
    body["modules"] = [third, second, first]
    # The assessment keeps its module (now still at index 1).

    updated = ok(put(client, cookie, template_path(detail["id"]), body))

    assert [m["title"] for m in updated["modules"]] == [
        "Cierre",
        "Desarrollo",
        "Introducción",
    ]
    assert [m["position"] for m in updated["modules"]] == [1, 2, 3]
    assert updated["version"] == 2
    new_events = template_events(module_seeded_url, detail["id"])[len(before) :]
    assert sorted(
        (table, action, list(changes)) for table, action, changes in new_events
    ) == [
        ("course_template_modules", "update", ["position"]),
        ("course_template_modules", "update", ["position"]),
    ]


def test_add_and_remove_a_module_and_an_assessment(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    body = write_body(detail)
    removed_module = body["modules"].pop(2)
    body["modules"].append(module("Proyecto"))
    body["assessments"] = [assessment("Proyecto final", module_index=2)]

    updated = ok(put(client, cookie, template_path(detail["id"]), body))

    titles = [m["title"] for m in updated["modules"]]
    assert titles == ["Introducción", "Desarrollo", "Proyecto"]
    [exam] = updated["assessments"]
    assert exam["title"] == "Proyecto final"
    assert exam["module_id"] == updated["modules"][2]["id"]
    assert exam["id"] != detail["assessments"][0]["id"]
    assert updated["version"] == 2
    [gone] = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM course_template_modules WHERE id = :id",
        {"id": uuid.UUID(removed_module["id"])},
    )
    assert gone.n == 0
    actions = sorted(
        (table, action)
        for table, action, _ in template_events(module_seeded_url, detail["id"])
        if action == "delete"
    )
    assert actions == [
        ("course_template_assessments", "delete"),
        ("course_template_modules", "delete"),
    ]


@pytest.mark.parametrize("repoint", [0, None])
def test_removing_a_module_while_repointing_its_assessment_succeeds(
    client, cookie, subject_ids, repoint
):
    detail = create_template(client, cookie, subject_ids)
    body = write_body(detail)
    # The assessment points at module 2 (index 1); remove that module.
    del body["modules"][1]
    body["assessments"][0]["module_index"] = repoint

    updated = ok(put(client, cookie, template_path(detail["id"]), body))

    assert [m["title"] for m in updated["modules"]] == ["Introducción", "Cierre"]
    [exam] = updated["assessments"]
    assert exam["id"] == detail["assessments"][0]["id"]
    expected = None if repoint is None else updated["modules"][0]["id"]
    assert exam["module_id"] == expected


def test_a_module_index_outside_the_body_is_422(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    body = write_body(detail)
    body["assessments"][0]["module_index"] = 3

    assert field_errors(put(client, cookie, template_path(detail["id"]), body)) == [
        (["body", "assessments", 0, "module_index"], "module_index_invalid")
    ]
    assert template_row(module_seeded_url, detail["id"]).version == 1


def test_an_item_id_of_another_template_or_unknown_is_422(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    other = create_template(client, cookie, subject_ids)
    body = write_body(detail)
    body["modules"][1]["id"] = other["modules"][0]["id"]
    body["assessments"][0]["id"] = str(uuid.uuid4())
    before = course_event_count(module_seeded_url)

    assert field_errors(put(client, cookie, template_path(detail["id"]), body)) == [
        (["body", "modules", 1, "id"], "item_unknown"),
        (["body", "assessments", 0, "id"], "item_unknown"),
    ]
    assert course_event_count(module_seeded_url) == before


def test_a_no_op_put_is_200_without_a_version_change_or_audit(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    before = course_event_count(module_seeded_url)

    same = ok(put(client, cookie, template_path(detail["id"]), write_body(detail)))

    assert same == detail
    assert course_event_count(module_seeded_url) == before


def test_template_fields_and_subjects_change_in_one_version(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    third = subjects(client, cookie, 1)[0]
    body = write_body(detail)
    body.update(
        name=detail["name"] + " v2",
        session_count=12,
        session_minutes=60,
        min_attendance_percent=None,
        subject_ids=[subject_ids[0], third],
    )

    updated = ok(put(client, cookie, template_path(detail["id"]), body))

    assert updated["version"] == 2
    assert (updated["session_count"], updated["total_minutes"]) == (12, 720)
    assert updated["completion"]["min_attendance_percent"] is None
    assert sorted(s["id"] for s in updated["subjects"]) == sorted(
        [subject_ids[0], third]
    )
    events = template_events(module_seeded_url, detail["id"])
    template_updates = [
        c for t, a, c in events if t == "course_templates" and a == "update"
    ]
    assert template_updates == [
        {
            "name": {"old": detail["name"], "new": detail["name"] + " v2"},
            "session_count": {"old": 10, "new": 12},
            "session_minutes": {"old": 90, "new": 60},
            "min_attendance_percent": {"old": 80, "new": None},
        }
    ]
    link_changes = [
        (action, changes["subject_id"])
        for table, action, changes in events
        if table == "course_template_subjects"
    ][2:]
    assert sorted(link_changes, key=str) == sorted(
        [
            ("create", {"old": None, "new": third}),
            ("delete", {"old": subject_ids[1], "new": None}),
        ],
        key=str,
    )


def test_a_linked_subject_deactivated_later_may_stay(client, cookie, subject_ids):
    detail = create_template(client, cookie, subject_ids)
    ok(
        patch(
            client,
            cookie,
            subject_path(subject_ids[1]),
            {"version": 1, "is_active": False},
        )
    )
    body = write_body(detail)
    body["modules"][2]["title"] = "Cierre y repaso"

    updated = ok(put(client, cookie, template_path(detail["id"]), body))

    assert {s["id"]: s["is_active"] for s in updated["subjects"]} == {
        subject_ids[0]: True,
        subject_ids[1]: False,
    }


def test_a_rename_to_a_taken_name_is_409(client, cookie, subject_ids):
    first = create_template(client, cookie, subject_ids)
    second = create_template(client, cookie, subject_ids)
    body = write_body(second)
    body["name"] = first["name"].lower()

    assert code_of(put(client, cookie, template_path(second["id"]), body)) == (
        409,
        "name_taken",
    )


def test_a_stale_version_is_409_before_the_rules(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    body = write_body(detail)
    body["version"] = 7
    body["modules"] = []
    before = course_event_count(module_seeded_url)

    assert code_of(put(client, cookie, template_path(detail["id"]), body)) == (
        409,
        "version_conflict",
    )
    assert course_event_count(module_seeded_url) == before


def test_an_unknown_template_is_404_before_the_rules(client, cookie, subject_ids):
    body = template_body(subject_ids, modules=[])
    body["version"] = 1

    assert code_of(put(client, cookie, template_path(uuid.uuid4()), body)) == (
        404,
        "not_found",
    )


def test_a_put_without_version_is_422(client, cookie, subject_ids):
    detail = create_template(client, cookie, subject_ids)
    body = write_body(detail)
    del body["version"]

    assert code_of(put(client, cookie, template_path(detail["id"]), body)) == (
        422,
        "validation_error",
    )


def test_a_finance_put_is_403_and_writes_nothing(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    finance = sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )
    body = write_body(detail)
    body["name"] = "Otro nombre " + uuid.uuid4().hex[:6]
    before = course_event_count(module_seeded_url)

    assert code_of(put(client, finance, template_path(detail["id"]), body)) == (
        403,
        "permission_denied",
    )
    assert course_event_count(module_seeded_url) == before


def test_a_coordinator_manages_templates(
    module_seeded_url, client, cookie, subject_ids
):
    coordinator = sign_in(
        client, create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,)).username
    )
    response = post(client, coordinator, TEMPLATES, template_body(subject_ids))
    assert response.status_code == 201, response.text
    body = write_body(response.json())
    body["modules"][0]["title"] = "Inicio"

    assert (
        ok(put(client, coordinator, template_path(body_id(response)), body))["version"]
        == 2
    )


def body_id(response) -> str:
    template_id: str = response.json()["id"]
    return template_id


# --- D5: status ----------------------------------------------------------------------


def test_deactivate_and_reactivate_bump_the_version(
    module_seeded_url, client, cookie, subject_ids
):
    detail = create_template(client, cookie, subject_ids)
    path = template_path(detail["id"])

    off = ok(patch(client, cookie, path, {"version": 1, "is_active": False}))
    assert (off["is_active"], off["version"]) == (False, 2)
    before = course_event_count(module_seeded_url)
    same = ok(patch(client, cookie, path, {"version": 2, "is_active": False}))
    assert same["version"] == 2
    assert course_event_count(module_seeded_url) == before
    on = ok(patch(client, cookie, path, {"version": 2, "is_active": True}))
    assert (on["is_active"], on["version"]) == (True, 3)
    assert [
        changes
        for table, action, changes in template_events(module_seeded_url, detail["id"])
        if table == "course_templates" and action == "update"
    ] == [
        {"is_active": {"old": True, "new": False}},
        {"is_active": {"old": False, "new": True}},
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"is_active": False},
        {"version": 1},
        {"version": 1, "is_active": False, "name": "x"},
    ],
)
def test_a_patch_body_is_exactly_version_and_is_active(
    client, cookie, subject_ids, body
):
    detail = create_template(client, cookie, subject_ids)

    assert code_of(patch(client, cookie, template_path(detail["id"]), body)) == (
        422,
        "validation_error",
    )


def test_a_stale_patch_is_409(client, cookie, subject_ids):
    detail = create_template(client, cookie, subject_ids)

    assert code_of(
        patch(
            client,
            cookie,
            template_path(detail["id"]),
            {"version": 2, "is_active": False},
        )
    ) == (409, "version_conflict")
