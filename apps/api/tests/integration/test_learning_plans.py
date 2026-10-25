"""Learning plans (#81 C, D, E, F, AM1, I2, I3) with real logins through
the real app.

The lifecycle (create → edit → activate → progress → complete; create →
discontinue; create → delete) with audit rows and notifications, every C3,
C4 and D1-D3 order step and every C2 error, the ``PUT`` id semantics, one
active plan per student and subject, progress / ``objectives_complete`` /
``overdue`` (frozen clock, the center zone's boundary), the subject rule,
the former-tutor rule, staff and Finance, the family reads (drafts never,
the consent rule), the create hints (AM1), the recipients and params, the
IDOR cases (the same ``404`` as an unknown id), the B2 drift between the
two assignment seams and the statement budgets. The family leak walk over
every ``GET`` route is ``test_session_notes_leak.py``; the D4 races are
``test_learning_plans_concurrency.py``.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from app.workflows import learning_plans as workflow
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.courses_support import put
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.schedule_support import principal_of, stored
from tests.integration.session_request_support import (
    Family,
    give_academic_consent,
    guardian_with_child,
    revoke_academic_consent,
    student_account,
)
from tests.integration.test_assignment_offers import (
    T0,
    Setup,
    answer,
    new_tutor_user,
    offered,
)
from tests.integration.test_class_session_create import MONDAY as OFFER_MONDAY
from tests.integration.test_class_session_create import (
    at,
    new_room,
    new_student,
    new_subject,
)
from tests.integration.test_session_substitutions import substituted

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
TODAY = date(2031, 3, 5)  # NOW in the center's zone (America/Mexico_City)
STARTED = NOW - timedelta(minutes=30)

OK = (200, None)
CREATED = (201, None)
NO_CONTENT = (204, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
VERSION_CONFLICT = (409, "version_conflict")
SUBJECT_NOT_ASSIGNED = (403, "plan_subject_not_assigned")
READ_ONLY = (403, "plan_read_only")
CONSENT = (403, "student_consent_required")
CLOSED = (409, "plan_closed")
NOT_ACTIVE = (409, "plan_not_active")
NOT_DRAFT = (409, "plan_not_draft")
ACTIVE_EXISTS = (409, "active_plan_exists")

ACTIVATED_TYPE = "academics.learning_plan_activated"
COMPLETED_TYPE = "academics.learning_plan_completed"

SUMMARY_KEYS = {
    "id",
    "version",
    "student_id",
    "subject",
    "title",
    "status",
    "starts_on",
    "target_on",
    "activated_at",
    "closed_at",
    "created_by",
    "progress",
    "objectives_complete",
    "objectives_total",
    "overdue_milestones",
    "updated_at",
}
VIEW_KEYS = SUMMARY_KEYS | {
    "description",
    "objectives",
    "can_edit",
    "can_activate",
    "can_complete",
    "can_discontinue",
    "can_delete",
    "can_update_progress",
}
OBJECTIVE_KEYS = {
    "id",
    "position",
    "title",
    "description",
    "complete",
    "progress",
    "milestones",
}
MILESTONE_KEYS = {
    "id",
    "version",
    "position",
    "title",
    "activities",
    "due_on",
    "status",
    "overdue",
    "completed_at",
    "completed_by",
}
CAN_KEYS = sorted(k for k in VIEW_KEYS if k.startswith("can_"))


@pytest.fixture(scope="module")
def frozen() -> Any:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Any:
    frozen.set(NOW)
    yield frozen
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,), display_name="Dueña Centro")


@pytest.fixture(scope="module")
def coordinator(module_seeded_url: str) -> CreatedUser:
    return create_user(
        module_seeded_url,
        roles=(ACADEMIC_COORDINATOR,),
        display_name="Coordinadora Planes",
    )


@pytest.fixture(scope="module")
def staff(client: TestClient, coordinator: CreatedUser) -> Any:
    """The coordinator's cookie (learning_plan.manage)."""
    return sign_in(client, coordinator.username)


# --- Helpers --------------------------------------------------------------------------


def plans_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/learning-plans"


def plan_path(plan_id: Any, action: str = "") -> str:
    return f"/api/v1/learning-plans/{plan_id}{action}"


def milestone_path(milestone_id: Any) -> str:
    return f"/api/v1/learning-plan-milestones/{milestone_id}"


def ok(response: Response, expected: tuple[int, None] = OK) -> dict[str, Any]:
    assert code_of(response) == expected, response.text
    body: dict[str, Any] = response.json()
    return body


def field_codes(response: Response) -> list[tuple[list[Any], str]]:
    assert code_of(response) == INVALID, response.text
    return [(item["path"], item["code"]) for item in response.json()["errors"]]


def milestone(title: str = "Capítulo", **values: Any) -> dict[str, Any]:
    return {"title": title, **values}


def objective(
    title: str = "Comprensión", *milestones: dict[str, Any], **values: Any
) -> dict[str, Any]:
    return {
        "title": title,
        "milestones": list(milestones) or [milestone("Leer 1"), milestone("Leer 2")],
        **values,
    }


def plan_body(subject_id: Any, *objectives: dict[str, Any], **values: Any) -> Any:
    return {
        "subject_id": str(subject_id),
        "title": "Plan de lectura",
        "objectives": list(objectives) or [objective()],
        **values,
    }


def replace_body(view: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """The view's content as a ``PUT`` body (ids kept)."""
    body = {
        "version": view["version"],
        "title": view["title"],
        "description": view["description"],
        "starts_on": view["starts_on"],
        "target_on": view["target_on"],
        "objectives": [
            {
                "id": o["id"],
                "title": o["title"],
                "description": o["description"],
                "milestones": [
                    {
                        "id": m["id"],
                        "title": m["title"],
                        "activities": m["activities"],
                        "due_on": m["due_on"],
                    }
                    for m in o["milestones"]
                ],
            }
            for o in view["objectives"]
        ],
    }
    return body | changes


def a_tutor(url: str, name: str = "Tutora Planes") -> CreatedUser:
    return create_user(url, roles=(TUTOR,), display_name=name)


def teaching(
    url: str,
    *,
    tutor: CreatedUser | None = None,
    students: tuple[uuid.UUID, ...] | None = None,
    subject: uuid.UUID | None = None,
    state: str = "confirmed",
    starts_at: datetime = STARTED,
) -> tuple[CreatedUser, uuid.UUID, uuid.UUID]:
    """A stored session of ``tutor`` (new by default) with ``students`` (one
    new student by default) in ``subject`` (new by default): ``(tutor,
    first student, subject)``."""
    tutor = tutor or a_tutor(url)
    students = students or (new_student(url),)
    subject = subject or new_subject(url)
    stored(
        url,
        starts_at,
        state=state,
        students=students,
        tutor_user_id=tutor.id,
        subject_id=subject,
    )
    return tutor, students[0], subject


def create(
    client: TestClient, cookie: Any, student_id: Any, subject: Any, **values: Any
) -> Response:
    objectives = values.pop("objectives", None)
    body = plan_body(subject, **values)
    if objectives is not None:
        body["objectives"] = objectives
    return post(client, cookie, plans_path(student_id), body)


def transition(
    client: TestClient, cookie: Any, plan_id: Any, version: int, action: str
) -> Response:
    return post(
        client,
        cookie,
        plan_path(plan_id, "/transition"),
        {"version": version, "action": action},
    )


def progress(
    client: TestClient, cookie: Any, milestone_id: Any, version: int, status: str
) -> Response:
    return patch(
        client,
        cookie,
        milestone_path(milestone_id),
        {"version": version, "status": status},
    )


def delete(client: TestClient, cookie: Any, plan_id: Any, version: int) -> Response:
    client.cookies.clear()
    return client.delete(
        plan_path(plan_id), params={"version": version}, headers=signed_headers(cookie)
    )


def notifications(url: str, notification_type: str, **params: Any) -> list[Any]:
    rows = run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = :t",
        {"t": notification_type},
    )
    return [
        row
        for row in rows
        if all(row.params.get(key) == str(value) for key, value in params.items())
    ]


def first_milestone(view: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = view["objectives"][0]["milestones"][0]
    return found


def drafted(
    client: TestClient,
    url: str,
    *,
    family: Family | None = None,
    tutor: CreatedUser | None = None,
    active: bool = False,
    **values: Any,
) -> tuple[CreatedUser, Any, uuid.UUID, uuid.UUID, dict[str, Any]]:
    """A plan by a tutor teaching one student (``family``'s child when given)
    in a new subject: ``(tutor, tutor cookie, student, subject, view)``;
    ``active`` activates it."""
    students = None if family is None else (family.student_id,)
    tutor, student, subject = teaching(url, tutor=tutor, students=students)
    cookie = sign_in(client, tutor.username)
    view = ok(create(client, cookie, student, subject, **values), CREATED)
    if active:
        view = ok(transition(client, cookie, view["id"], view["version"], "activate"))
    return tutor, cookie, student, subject, view


# --- The lifecycle (C, D) -------------------------------------------------------------


def test_the_lifecycle(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    tutor, student, subject = teaching(url, students=(family.student_id,))
    cookie = sign_in(client, tutor.username)
    guardian = sign_in(client, family.user.username)

    created = ok(
        create(
            client,
            cookie,
            student,
            subject,
            title="  Plan de lectura  ",
            description="Leer con fluidez",
            target_on="2031-06-30",
            objectives=[
                objective(
                    "Comprensión",
                    milestone("Cuento corto", activities="Leer en voz alta"),
                    milestone("Resumen", due_on="2031-03-20"),
                ),
                objective("Vocabulario", milestone("Diez palabras")),
            ],
        ),
        CREATED,
    )
    assert set(created) == VIEW_KEYS
    assert set(created["objectives"][0]) == OBJECTIVE_KEYS
    assert set(first_milestone(created)) == MILESTONE_KEYS
    assert set(created["progress"]) == {"done", "total", "percent"}
    assert set(created["created_by"]) == {"display_name"}
    assert set(created["subject"]) == {"id", "name"}
    assert (created["title"], created["status"], created["version"]) == (
        "Plan de lectura",
        "draft",
        1,
    )
    assert created["starts_on"] == TODAY.isoformat()
    assert created["created_by"] == {"display_name": "Tutora Planes"}
    assert created["progress"] == {"done": 0, "total": 3, "percent": "0"}
    assert (created["objectives_complete"], created["objectives_total"]) == (0, 2)
    assert [o["position"] for o in created["objectives"]] == [1, 2]
    assert first_milestone(created)["status"] == "pending"
    assert first_milestone(created)["activities"] == "Leer en voz alta"
    assert {k: created[k] for k in CAN_KEYS} == {
        "can_activate": True,
        "can_complete": False,
        "can_delete": True,
        "can_discontinue": True,
        "can_edit": True,
        "can_update_progress": False,
    }
    plan_id = created["id"]
    assert [
        e.action
        for e in events(url, record_type="learning_plans", record_id=uuid.UUID(plan_id))
    ] == ["create"]
    # A draft never reaches the family; creating it notified nobody.
    assert ok(get(client, guardian, plans_path(student)))["items"] == []
    assert_same_not_found(
        get(client, guardian, plan_path(plan_id)),
        get(client, guardian, plan_path(uuid.uuid4())),
    )
    assert notifications(url, ACTIVATED_TYPE, plan_id=plan_id) == []

    # Edit: rename the plan and add a milestone (one version step).
    body = replace_body(created, title="Plan de lectura 2")
    body["objectives"][1]["milestones"].append(milestone("Veinte palabras"))
    edited = ok(put(client, cookie, plan_path(plan_id), body))
    assert (edited["title"], edited["version"]) == ("Plan de lectura 2", 2)
    assert edited["progress"]["total"] == 4

    # Activate: the family sees it and is notified (ids only).
    active = ok(transition(client, cookie, plan_id, 2, "activate"))
    assert (active["status"], active["version"]) == ("active", 3)
    assert active["activated_at"] is not None
    assert active["can_update_progress"] is True
    assert active["can_activate"] is False
    assert active["can_delete"] is False
    sent = notifications(url, ACTIVATED_TYPE, plan_id=plan_id)
    assert {row.recipient_user_id for row in sent} == {family.user.id}
    for row in sent:
        assert row.params == {
            "student_id": str(student),
            "plan_id": plan_id,
            "subject_id": str(subject),
        }
    family_view = ok(get(client, guardian, plan_path(plan_id)))
    assert set(family_view) == VIEW_KEYS
    assert not any(family_view[k] for k in CAN_KEYS)
    [listed] = ok(get(client, guardian, plans_path(student)))["items"]
    assert set(listed) == SUMMARY_KEYS
    assert listed["id"] == plan_id

    # Progress: one milestone done.
    first = first_milestone(active)
    done = ok(progress(client, cookie, first["id"], first["version"], "done"))
    marked = first_milestone(done)
    assert (marked["status"], marked["version"]) == ("done", 2)
    assert marked["completed_by"] == {"display_name": "Tutora Planes"}
    assert marked["completed_at"] is not None
    assert done["progress"] == {"done": 1, "total": 4, "percent": "25"}
    assert done["version"] == 3  # progress never bumps the plan
    # Progress and edits notify nobody.
    assert len(notifications(url, ACTIVATED_TYPE, plan_id=plan_id)) == 1

    # Complete: notified again; the plan is read-only.
    completed = ok(transition(client, cookie, plan_id, 3, "complete"))
    assert (completed["status"], completed["version"]) == ("completed", 4)
    assert completed["closed_at"] is not None
    assert not any(completed[k] for k in CAN_KEYS)
    assert {
        row.recipient_user_id
        for row in notifications(url, COMPLETED_TYPE, plan_id=plan_id)
    } == {family.user.id}
    assert code_of(put(client, cookie, plan_path(plan_id), body | {"version": 4})) == (
        CLOSED
    )
    assert code_of(progress(client, cookie, first["id"], 2, "pending")) == CLOSED
    for action in ("activate", "complete", "discontinue"):
        assert code_of(transition(client, cookie, plan_id, 4, action)) == CLOSED
    assert code_of(delete(client, cookie, plan_id, 4)) == NOT_DRAFT
    assert ok(get(client, guardian, plan_path(plan_id)))["status"] == "completed"

    actions = [
        e.action for e in events(url, record_type="learning_plans", record_id=plan_id)
    ]
    assert actions == ["create", "update", "update", "update"]
    milestone_events = events(
        url, record_type="learning_plan_milestones", record_id=uuid.UUID(first["id"])
    )
    assert [e.action for e in milestone_events] == ["create", "update"]
    assert milestone_events[-1].changes["status"] == {"old": "pending", "new": "done"}


def test_a_draft_is_discontinued_or_deleted(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    guardian = sign_in(client, family.user.username)
    _, cookie, student, subject, draft = drafted(client, url, family=family)

    stopped = ok(transition(client, cookie, draft["id"], 1, "discontinue"))
    assert (stopped["status"], stopped["activated_at"]) == ("discontinued", None)
    assert stopped["closed_at"] is not None
    # A never-activated discontinued plan never reaches the family.
    assert ok(get(client, guardian, plans_path(student)))["items"] == []
    assert_same_not_found(
        get(client, guardian, plan_path(draft["id"])),
        get(client, guardian, plan_path(uuid.uuid4())),
    )
    assert notifications(url, ACTIVATED_TYPE, plan_id=draft["id"]) == []
    assert code_of(delete(client, cookie, draft["id"], 2)) == NOT_DRAFT

    gone = ok(create(client, cookie, student, subject), CREATED)
    assert code_of(delete(client, cookie, gone["id"], 1)) == NO_CONTENT
    for response in (
        get(client, cookie, plan_path(gone["id"])),
        put(client, cookie, plan_path(gone["id"]), replace_body(gone)),
        transition(client, cookie, gone["id"], 1, "activate"),
        progress(client, cookie, first_milestone(gone)["id"], 1, "done"),
        delete(client, cookie, gone["id"], 1),
    ):
        assert_same_not_found(response, get(client, cookie, plan_path(uuid.uuid4())))
    listed = ok(get(client, cookie, plans_path(student)))["items"]
    assert [item["id"] for item in listed] == [draft["id"]]
    [deleted] = events(
        url,
        record_type="learning_plans",
        record_id=uuid.UUID(gone["id"]),
        action="soft_delete",
    )
    assert deleted

    # An active plan discontinued after its activation stays visible.
    shown = ok(create(client, cookie, student, subject), CREATED)
    ok(transition(client, cookie, shown["id"], 1, "activate"))
    ok(transition(client, cookie, shown["id"], 2, "discontinue"))
    assert code_of(delete(client, cookie, shown["id"], 3)) == NOT_DRAFT
    family_items = ok(get(client, guardian, plans_path(student)))["items"]
    assert [(i["id"], i["status"]) for i in family_items] == [
        (shown["id"], "discontinued")
    ]
    # Discontinuing notified nobody beyond the activation.
    assert notifications(url, COMPLETED_TYPE, plan_id=shown["id"]) == []


# --- C3: the create order -------------------------------------------------------------


def test_the_create_order(module_seeded_url, client, staff):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    cookie = sign_in(client, tutor.username)
    other_subject = new_subject(url)
    unknown = uuid.uuid4()

    # 1. 401.
    client.cookies.clear()
    assert client.post(plans_path(student), json=plan_body(subject)).status_code == 401
    # 2. 422 schema (before anything else, even for an unknown student).
    assert code_of(post(client, cookie, plans_path(unknown), {"title": "x"})) == (
        INVALID
    )
    assert code_of(
        post(client, cookie, plans_path(student), plan_body(subject, extra=1))
    ) == (INVALID)
    # 3. 404: an unknown student, a student the caller doesn't teach.
    assert_same_not_found(
        create(client, cookie, new_student(url), subject),
        create(client, cookie, unknown, subject),
    )
    for caller in (
        sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username),
        sign_in(client, a_tutor(url).username),
        sign_in(client, create_user(url, roles=(GUARDIAN,)).username),
    ):
        assert_same_not_found(
            create(client, caller, student, subject),
            create(client, caller, unknown, subject),
        )
    # 4. 422 subject_unknown (unknown, inactive, inactive level), before 403.
    for bad in (
        uuid.uuid4(),
        new_subject(url, is_active=False),
        new_subject(url, level_active=False),
    ):
        assert field_codes(create(client, cookie, student, bad, objectives=[])) == [
            (["body", "subject_id"], "subject_unknown")
        ]
    # 5. 403 for a tutor who doesn't teach the student this subject, before
    # the content checks.
    assert (
        code_of(create(client, cookie, student, other_subject, objectives=[]))
        == SUBJECT_NOT_ASSIGNED
    )
    # 6. Every content error, with its path.
    many = [milestone(f"M{i}") for i in range(11)]
    cases: list[tuple[dict[str, Any], list[tuple[list[Any], str]]]] = [
        ({"title": "   "}, [(["body", "title"], "title_required")]),
        ({"title": None}, [(["body", "title"], "title_required")]),
        ({"title": "x" * 121}, [(["body", "title"], "too_long")]),
        ({"description": "x" * 2001}, [(["body", "description"], "too_long")]),
        (
            {"starts_on": "2031-03-10", "target_on": "2031-03-09"},
            [(["body", "target_on"], "target_before_start")],
        ),
        ({"target_on": "2031-03-04"}, [(["body", "target_on"], "target_before_start")]),
        ({"objectives": []}, [(["body", "objectives"], "objectives_required")]),
        (
            {"objectives": [objective(f"O{i}") for i in range(11)]},
            [(["body", "objectives"], "objectives_too_many")],
        ),
        (
            {"objectives": [{"title": "O", "milestones": []}]},
            [(["body", "objectives", 0, "milestones"], "milestones_required")],
        ),
        (
            {"objectives": [objective("O", *many)]},
            [(["body", "objectives", 0, "milestones"], "milestones_too_many")],
        ),
        (
            {"objectives": [objective(f"O{i}", *many[:9]) for i in range(6)]},
            [(["body", "objectives"], "milestones_too_many")],
        ),
        (
            {"objectives": [objective(" ", milestone(""))]},
            [
                (["body", "objectives", 0, "title"], "title_required"),
                (["body", "objectives", 0, "milestones", 0, "title"], "title_required"),
            ],
        ),
        (
            {
                "objectives": [
                    objective(
                        "x" * 201,
                        milestone(
                            "y" * 201, activities="z" * 2001, due_on="1999-12-31"
                        ),
                        description="d" * 1001,
                    )
                ]
            },
            [
                (["body", "objectives", 0, "title"], "too_long"),
                (["body", "objectives", 0, "description"], "too_long"),
                (["body", "objectives", 0, "milestones", 0, "title"], "too_long"),
                (["body", "objectives", 0, "milestones", 0, "activities"], "too_long"),
                (
                    ["body", "objectives", 0, "milestones", 0, "due_on"],
                    "due_on_invalid",
                ),
            ],
        ),
    ]
    for values, expected in cases:
        assert field_codes(create(client, cookie, student, subject, **values)) == (
            expected
        ), values
    # The limits themselves are allowed: 10 objectives, 10 milestones in one,
    # 50 in all; a due date outside the plan's dates.
    largest = [objective("O0", *many[:10])] + [
        objective(f"O{i}", *many[:5]) for i in range(1, 9)
    ]
    view = ok(
        create(
            client,
            cookie,
            student,
            subject,
            objectives=largest,
            target_on="2031-04-01",
        ),
        CREATED,
    )
    assert view["progress"]["total"] == 50
    edge = ok(
        create(
            client,
            cookie,
            student,
            subject,
            objectives=[objective("O", milestone("M", due_on="2032-01-01"))],
            starts_on="2031-01-01",
            target_on="2031-02-01",
        ),
        CREATED,
    )
    assert first_milestone(edge)["due_on"] == "2032-01-01"
    # Staff with learning_plan.manage create in any active subject.
    by_staff = ok(create(client, staff, student, other_subject), CREATED)
    assert by_staff["created_by"]["display_name"] == "Coordinadora Planes"
    assert by_staff["can_edit"] is True
    assert notifications(url, ACTIVATED_TYPE, student_id=student) == []


# --- C4: the PUT order and its id semantics ------------------------------------------


def test_the_put_order(module_seeded_url, client, staff):
    url = module_seeded_url
    _, cookie, student, subject, view = drafted(client, url)
    plan_id = view["id"]

    client.cookies.clear()
    assert client.put(plan_path(plan_id), json=replace_body(view)).status_code == 401
    # 422 schema: no version, an immutable subject_id, an unknown field.
    body = replace_body(view)
    del body["version"]
    assert code_of(put(client, cookie, plan_path(plan_id), body)) == INVALID
    invalid = put(
        client, cookie, plan_path(plan_id), replace_body(view, subject_id=str(subject))
    )
    assert code_of(invalid) == INVALID
    assert invalid.json()["errors"][0]["code"] == "extra_forbidden"
    # 404 before the version and the content.
    outsider = sign_in(client, a_tutor(url).username)
    assert_same_not_found(
        put(client, outsider, plan_path(plan_id), replace_body(view, version=9)),
        put(client, outsider, plan_path(uuid.uuid4()), replace_body(view, version=9)),
    )
    # 403 before the version: a tutor teaching the student another subject.
    colleague, _, _ = teaching(url, students=(student,))
    colleague_cookie = sign_in(client, colleague.username)
    assert (
        code_of(
            put(
                client,
                colleague_cookie,
                plan_path(plan_id),
                replace_body(view, version=9, objectives=[]),
            )
        )
        == SUBJECT_NOT_ASSIGNED
    )
    # 409 version_conflict before the content.
    assert (
        code_of(
            put(
                client,
                cookie,
                plan_path(plan_id),
                replace_body(view, version=9, objectives=[]),
            )
        )
        == VERSION_CONFLICT
    )
    # 422 content.
    assert field_codes(
        put(client, cookie, plan_path(plan_id), replace_body(view, objectives=[]))
    ) == [(["body", "objectives"], "objectives_required")]
    assert field_codes(
        put(
            client,
            cookie,
            plan_path(plan_id),
            replace_body(view, target_on="2000-01-01"),
        )
    ) == [(["body", "target_on"], "target_before_start")]
    # Staff write any plan.
    staff_edit = ok(
        put(client, staff, plan_path(plan_id), replace_body(view, title="Por staff"))
    )
    assert staff_edit["version"] == 2


def test_the_put_id_semantics(module_seeded_url, client):
    url = module_seeded_url
    _, cookie, _, _, view = drafted(
        client,
        url,
        objectives=[
            objective("A", milestone("A1"), milestone("A2")),
            objective("B", milestone("B1"), milestone("B2")),
        ],
    )
    plan_id = view["id"]
    view = ok(transition(client, cookie, plan_id, 1, "activate"))
    a1 = view["objectives"][0]["milestones"][0]
    view = ok(progress(client, cookie, a1["id"], 1, "done"))
    [a, b] = view["objectives"]

    # Unchanged: 200, no write, no version step.
    before = len(
        events(url, record_type="learning_plans", record_id=uuid.UUID(plan_id))
    )
    same = ok(put(client, cookie, plan_path(plan_id), replace_body(view)))
    assert same["version"] == view["version"] == 2
    assert same["updated_at"] == view["updated_at"]
    assert (
        len(events(url, record_type="learning_plans", record_id=uuid.UUID(plan_id)))
        == before
    )

    # Rename the done milestone in place (progress kept), drop A2, add A3,
    # swap the objectives, drop B2, add a new objective C.
    body = replace_body(view)
    body["objectives"] = [
        {
            "id": b["id"],
            "title": "B",
            "milestones": [{"id": b["milestones"][0]["id"], "title": "B1"}],
        },
        {
            "id": a["id"],
            "title": "A renombrado",
            "milestones": [
                {"title": "A3"},
                {"id": a1["id"], "title": "A1 renombrado", "activities": "Leer"},
            ],
        },
        {"title": "C", "milestones": [{"title": "C1"}]},
    ]
    changed = ok(put(client, cookie, plan_path(plan_id), body))
    assert changed["version"] == 3
    titles = [
        (
            o["title"],
            [(m["title"], m["status"], m["position"]) for m in o["milestones"]],
        )
        for o in changed["objectives"]
    ]
    assert titles == [
        ("B", [("B1", "pending", 1)]),
        ("A renombrado", [("A3", "pending", 1), ("A1 renombrado", "done", 2)]),
        ("C", [("C1", "pending", 1)]),
    ]
    kept = changed["objectives"][1]["milestones"][1]
    assert kept["id"] == a1["id"]
    assert kept["completed_by"] is not None
    assert changed["objectives"][0]["id"] == b["id"]
    assert changed["progress"] == {"done": 1, "total": 4, "percent": "25"}
    assert changed["objectives_complete"] == 0
    deleted = {
        e.record_id
        for e in events(url, record_type="learning_plan_milestones", action="delete")
    }
    assert {
        uuid.UUID(a["milestones"][1]["id"]),
        uuid.UUID(b["milestones"][1]["id"]),
    } <= deleted

    # Deleting the done milestone recomputes the progress.
    body = replace_body(changed)
    body["objectives"][1]["milestones"] = body["objectives"][1]["milestones"][:1]
    dropped = ok(put(client, cookie, plan_path(plan_id), body))
    assert dropped["progress"] == {"done": 0, "total": 3, "percent": "0"}

    # Unknown, foreign, repeated and moved ids.
    _, _, _, _, foreign = drafted(client, url)
    current = replace_body(dropped)
    moved = replace_body(dropped)
    moved["objectives"][0]["milestones"].append(
        {"id": dropped["objectives"][1]["milestones"][0]["id"], "title": "Movido"}
    )
    del moved["objectives"][1]["milestones"][0]
    moved["objectives"][1]["milestones"].append({"title": "Nuevo"})
    new_objective_with_old_milestone = replace_body(dropped)
    new_objective_with_old_milestone["objectives"].append(
        {
            "title": "D",
            "milestones": [
                {"id": dropped["objectives"][2]["milestones"][0]["id"], "title": "x"}
            ],
        }
    )
    repeated = replace_body(dropped)
    repeated["objectives"][1]["id"] = repeated["objectives"][0]["id"]
    cases = [
        (
            current
            | {
                "objectives": [
                    {
                        "id": str(uuid.uuid4()),
                        "title": "X",
                        "milestones": [{"title": "Y"}],
                    }
                ]
            },
            [(["body", "objectives", 0, "id"], "objective_unknown")],
        ),
        (
            current
            | {
                "objectives": [
                    {
                        "id": foreign["objectives"][0]["id"],
                        "title": "X",
                        "milestones": [{"title": "Y"}],
                    }
                ]
            },
            [(["body", "objectives", 0, "id"], "objective_unknown")],
        ),
        (
            moved,
            [(["body", "objectives", 0, "milestones", 1, "id"], "milestone_unknown")],
        ),
        (
            new_objective_with_old_milestone,
            [(["body", "objectives", 3, "milestones", 0, "id"], "milestone_unknown")],
        ),
        (
            repeated,
            [
                (["body", "objectives", 1, "id"], "objective_unknown"),
                (["body", "objectives", 1, "milestones", 0, "id"], "milestone_unknown"),
            ],
        ),
    ]
    for body, expected in cases:
        assert field_codes(put(client, cookie, plan_path(plan_id), body)) == expected


# --- D1: transitions ------------------------------------------------------------------


def test_the_transition_order_and_one_active_plan(module_seeded_url, client):
    url = module_seeded_url
    tutor, cookie, student, subject, first = drafted(client, url)
    second = ok(create(client, cookie, student, subject), CREATED)
    plan_id = first["id"]

    client.cookies.clear()
    assert (
        client.post(
            plan_path(plan_id, "/transition"), json={"version": 1, "action": "activate"}
        ).status_code
        == 401
    )
    assert code_of(transition(client, cookie, plan_id, 1, "reopen")) == INVALID
    outsider = sign_in(client, a_tutor(url).username)
    assert_same_not_found(
        transition(client, outsider, plan_id, 1, "activate"),
        transition(client, outsider, uuid.uuid4(), 1, "activate"),
    )
    assert code_of(transition(client, cookie, plan_id, 9, "activate")) == (
        VERSION_CONFLICT
    )
    assert code_of(transition(client, cookie, plan_id, 1, "complete")) == NOT_ACTIVE
    ok(transition(client, cookie, plan_id, 1, "activate"))
    assert code_of(transition(client, cookie, plan_id, 2, "activate")) == NOT_DRAFT
    # One active plan per student and subject.
    assert code_of(transition(client, cookie, second["id"], 1, "activate")) == (
        ACTIVE_EXISTS
    )
    # Another subject of the same student is fine.
    _, _, other_subject = teaching(
        url, tutor=tutor, students=(student,), starts_at=STARTED + timedelta(days=1)
    )
    third = ok(create(client, cookie, student, other_subject), CREATED)
    ok(transition(client, cookie, third["id"], 1, "activate"))
    # Once the first is completed, the second may be activated.
    ok(transition(client, cookie, plan_id, 2, "complete"))
    assert ok(transition(client, cookie, second["id"], 1, "activate"))["status"] == (
        "active"
    )


# --- D2: progress ---------------------------------------------------------------------


def test_the_progress_order(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    guardian = sign_in(client, family.user.username)
    tutor, cookie, student, subject, draft = drafted(client, url, family=family)
    target = first_milestone(draft)

    client.cookies.clear()
    assert (
        client.patch(
            milestone_path(target["id"]), json={"version": 1, "status": "done"}
        ).status_code
        == 401
    )
    assert code_of(progress(client, cookie, target["id"], 1, "late")) == INVALID
    # A family member on a draft's milestone: the unknown-id 404.
    assert_same_not_found(
        progress(client, guardian, target["id"], 1, "done"),
        progress(client, guardian, uuid.uuid4(), 1, "done"),
    )
    # A draft tracks no progress.
    assert code_of(progress(client, cookie, target["id"], 1, "done")) == NOT_ACTIVE
    view = ok(transition(client, cookie, draft["id"], 1, "activate"))
    # The family reads it now, but never writes.
    assert_same_not_found(
        progress(client, guardian, target["id"], 1, "done"),
        progress(client, guardian, uuid.uuid4(), 1, "done"),
    )
    colleague, _, _ = teaching(url, students=(student,))
    assert (
        code_of(
            progress(client, sign_in(client, colleague.username), target["id"], 9, "x")
        )
        == INVALID
    )
    assert (
        code_of(
            progress(
                client, sign_in(client, colleague.username), target["id"], 9, "done"
            )
        )
        == SUBJECT_NOT_ASSIGNED
    )
    assert code_of(progress(client, cookie, target["id"], 9, "done")) == (
        VERSION_CONFLICT
    )
    started = first_milestone(
        ok(progress(client, cookie, target["id"], 1, "in_progress"))
    )
    assert (started["status"], started["version"], started["completed_at"]) == (
        "in_progress",
        2,
        None,
    )
    # The same status writes nothing.
    again = first_milestone(
        ok(progress(client, cookie, target["id"], 2, "in_progress"))
    )
    assert again["version"] == 2
    done = first_milestone(ok(progress(client, cookie, target["id"], 2, "done")))
    assert done["completed_at"] is not None
    undone = first_milestone(ok(progress(client, cookie, target["id"], 3, "pending")))
    assert (undone["completed_at"], undone["completed_by"]) == (None, None)
    assert view and subject


def test_progress_and_overdue_values(module_seeded_url, client, at_now):
    """Progress, objective completion and overdue at the center zone's day
    boundary (America/Mexico_City, UTC-6 in March 2031)."""
    url = module_seeded_url
    tutor, cookie, _, _, view = drafted(
        client,
        url,
        active=True,
        objectives=[
            objective(
                "A",
                milestone("A1", due_on="2031-03-04"),
                milestone("A2", due_on="2031-03-05"),
            ),
            objective("B", milestone("B1", due_on="2031-03-01")),
        ],
    )
    assert view["overdue_milestones"] == 2
    a1, a2 = view["objectives"][0]["milestones"]
    assert (a1["overdue"], a2["overdue"]) == (True, False)
    b1 = view["objectives"][1]["milestones"][0]
    view = ok(progress(client, cookie, b1["id"], 1, "done"))
    assert view["objectives"][1]["complete"] is True
    assert view["objectives"][1]["milestones"][0]["overdue"] is False
    assert view["objectives"][1]["progress"] == {
        "done": 1,
        "total": 1,
        "percent": "100",
    }
    assert view["objectives"][0]["complete"] is False
    assert (view["objectives_complete"], view["overdue_milestones"]) == (1, 1)
    assert view["progress"] == {"done": 1, "total": 3, "percent": "33"}

    # 23:00 local on Mar 5: A2 (due Mar 5) isn't overdue yet; 00:30 on Mar 6 it is.
    at_now.set(datetime(2031, 3, 6, 5, 0, tzinfo=UTC))
    cookie = sign_in(client, tutor.username)
    assert ok(get(client, cookie, plan_path(view["id"])))["overdue_milestones"] == 1
    at_now.set(datetime(2031, 3, 6, 6, 30, tzinfo=UTC))
    cookie = sign_in(client, tutor.username)
    late = ok(get(client, cookie, plan_path(view["id"])))
    assert late["overdue_milestones"] == 2
    assert late["objectives"][0]["milestones"][1]["overdue"] is True
    [summary] = [
        item
        for item in ok(get(client, cookie, plans_path(view["student_id"])))["items"]
        if item["id"] == view["id"]
    ]
    assert (summary["overdue_milestones"], summary["objectives_complete"]) == (2, 1)
    assert summary["progress"] == {"done": 1, "total": 3, "percent": "33"}


# --- E4: the subject rule, the former tutor, staff ------------------------------------


def test_the_subject_rule_and_the_create_hints(module_seeded_url, client, staff):
    url = module_seeded_url
    maths = new_subject(url)
    english = new_subject(url)
    tutor, student, _ = teaching(url, subject=maths)
    cookie = sign_in(client, tutor.username)
    # An English plan by staff.
    english_plan = ok(create(client, staff, student, english), CREATED)

    assert code_of(create(client, cookie, student, english)) == SUBJECT_NOT_ASSIGNED
    read = ok(get(client, cookie, plan_path(english_plan["id"])))
    assert not any(read[k] for k in CAN_KEYS)
    for response in (
        put(client, cookie, plan_path(english_plan["id"]), replace_body(english_plan)),
        transition(client, cookie, english_plan["id"], 1, "activate"),
        delete(client, cookie, english_plan["id"], 1),
    ):
        assert code_of(response) == SUBJECT_NOT_ASSIGNED
    maths_plan = ok(create(client, cookie, student, maths), CREATED)
    assert maths_plan["can_edit"] is True
    listed = ok(get(client, cookie, plans_path(student)))
    assert {item["id"] for item in listed["items"]} == {
        english_plan["id"],
        maths_plan["id"],
    }
    assert set(listed) == {"items", "truncated", "can_create", "creatable_subjects"}

    # AM1: the tutor may create in maths only; an inactive subject they
    # still teach is left out; the filters don't change the hints.
    assert (listed["can_create"], listed["creatable_subjects"]) == (
        True,
        [{"id": str(maths), "name": listed["creatable_subjects"][0]["name"]}],
    )
    filtered = ok(get(client, cookie, plans_path(student), status="completed"))
    assert filtered["items"] == []
    assert filtered["creatable_subjects"] == listed["creatable_subjects"]
    retired = new_subject(url)
    teaching(
        url,
        tutor=tutor,
        students=(student,),
        subject=retired,
        starts_at=STARTED + timedelta(days=1),
    )
    run_sql(
        url, "UPDATE subjects SET is_active = false WHERE id = :id", {"id": retired}
    )
    assert [
        s["id"]
        for s in ok(get(client, cookie, plans_path(student)))["creatable_subjects"]
    ] == [str(maths)]
    assert code_of(create(client, cookie, student, retired)) == INVALID
    staff_list = ok(get(client, staff, plans_path(student)))
    assert (staff_list["can_create"], staff_list["creatable_subjects"]) == (True, None)
    # A holder who is also a tutor gets the holder's answer.
    both = create_user(url, roles=(TUTOR, ACADEMIC_COORDINATOR))
    teaching(url, tutor=both, students=(student,), subject=maths)
    both_list = ok(get(client, sign_in(client, both.username), plans_path(student)))
    assert (both_list["can_create"], both_list["creatable_subjects"]) == (True, None)


def test_a_former_tutor_reads_their_plans_read_only(
    module_seeded_url, client, staff, at_now
):
    """W5.4: the last class ended 31+ days ago and nothing is upcoming: the
    creator still reads their plans, read-only; others' are hidden."""
    url = module_seeded_url
    long_ago = NOW - timedelta(days=40)
    tutor, student, subject = teaching(url, state="completed", starts_at=long_ago)
    at_now.set(long_ago + timedelta(hours=2))
    cookie = sign_in(client, tutor.username)
    own = ok(create(client, cookie, student, subject), CREATED)
    at_now.set(NOW)
    theirs = ok(create(client, staff, student, subject), CREATED)
    cookie = sign_in(client, tutor.username)

    read = ok(get(client, cookie, plan_path(own["id"])))
    assert not any(read[k] for k in CAN_KEYS)
    for response in (
        put(client, cookie, plan_path(own["id"]), replace_body(own, title="x")),
        transition(client, cookie, own["id"], 1, "activate"),
        delete(client, cookie, own["id"], 1),
        progress(client, cookie, first_milestone(own)["id"], 1, "done"),
    ):
        assert code_of(response) == READ_ONLY
    assert code_of(create(client, cookie, student, subject)) == NOT_FOUND
    assert_same_not_found(
        get(client, cookie, plan_path(theirs["id"])),
        get(client, cookie, plan_path(uuid.uuid4())),
    )
    listed = ok(get(client, cookie, plans_path(student)))
    assert [item["id"] for item in listed["items"]] == [own["id"]]
    assert (listed["can_create"], listed["creatable_subjects"]) == (False, [])
    # Without any plan of theirs left, the list is the unknown-id 404.
    other = new_student(url)
    teaching(
        url,
        tutor=tutor,
        students=(other,),
        state="completed",
        starts_at=long_ago + timedelta(days=1),
    )
    assert_same_not_found(
        get(client, cookie, plans_path(other)),
        get(client, cookie, plans_path(uuid.uuid4())),
    )


def test_staff_write_and_finance_gets_404(module_seeded_url, client, owner):
    url = module_seeded_url
    _, _, student, subject, view = drafted(client, url)
    administrator = create_user(url, roles=(ADMINISTRATOR,))
    version = 1
    for user in (owner, administrator):
        cookie = sign_in(client, user.username)
        edited = ok(
            put(
                client,
                cookie,
                plan_path(view["id"]),
                replace_body(view, version=version, title=f"Por {user.username}"),
            )
        )
        version = edited["version"]
        assert edited["can_activate"] is True
    finance = sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username)
    unknown = uuid.uuid4()
    assert_same_not_found(
        get(client, finance, plans_path(student)),
        get(client, finance, plans_path(unknown)),
    )
    assert_same_not_found(
        get(client, finance, plan_path(view["id"])),
        get(client, finance, plan_path(unknown)),
    )
    assert_same_not_found(
        create(client, finance, student, subject),
        create(client, finance, unknown, subject),
    )


# --- E1: family reads -----------------------------------------------------------------


def test_the_family_reads(module_seeded_url, client, staff):
    url = module_seeded_url
    family = guardian_with_child(url)
    guardian = sign_in(client, family.user.username)
    tutor, cookie, student, subject, active = drafted(
        client, url, family=family, active=True
    )
    draft = ok(create(client, cookie, student, subject), CREATED)
    stopped = ok(create(client, cookie, student, subject), CREATED)
    ok(transition(client, cookie, stopped["id"], 1, "discontinue"))
    _, _, other_subject = teaching(
        url, tutor=tutor, students=(student,), starts_at=STARTED + timedelta(days=1)
    )
    done = ok(create(client, cookie, student, other_subject), CREATED)
    ok(transition(client, cookie, done["id"], 1, "activate"))
    ok(transition(client, cookie, done["id"], 2, "complete"))

    listed = ok(get(client, guardian, plans_path(student)))
    assert [(i["id"], i["status"]) for i in listed["items"]] == [
        (active["id"], "active"),
        (done["id"], "completed"),
    ]
    assert (listed["can_create"], listed["creatable_subjects"]) == (False, [])
    # A status filter never widens past the family's rows.
    assert (
        ok(
            get(client, guardian, plans_path(student), status=["draft", "discontinued"])
        )["items"]
        == []
    )
    assert [
        i["id"]
        for i in ok(get(client, guardian, plans_path(student), status="completed"))[
            "items"
        ]
    ] == [done["id"]]
    assert [
        i["id"]
        for i in ok(
            get(client, guardian, plans_path(student), subject_id=str(other_subject))
        )["items"]
    ] == [done["id"]]
    # Staff see everything, active first, then drafts, then closed.
    staff_items = ok(get(client, staff, plans_path(student)))["items"]
    # The closed ones by starts_on (the same), then id (creation order).
    assert [i["status"] for i in staff_items] == [
        "active",
        "draft",
        "discontinued",
        "completed",
    ]
    assert {i["id"] for i in staff_items} == {
        active["id"],
        draft["id"],
        stopped["id"],
        done["id"],
    }
    for hidden in (draft["id"], stopped["id"]):
        assert_same_not_found(
            get(client, guardian, plan_path(hidden)),
            get(client, guardian, plan_path(uuid.uuid4())),
        )
    assert ok(get(client, guardian, plan_path(done["id"])))["status"] == "completed"

    # The student themself.
    me, mine = student_account(url)
    _, my_cookie, _, _, my_plan = drafted(
        client, url, family=Family(me, uuid.uuid4(), mine), active=True
    )
    mine_cookie = sign_in(client, me.username)
    assert [
        i["id"] for i in ok(get(client, mine_cookie, plans_path(mine)))["items"]
    ] == [my_plan["id"]]
    assert ok(get(client, mine_cookie, plan_path(my_plan["id"])))["id"] == my_plan["id"]
    # Anyone else: the same 404 as an unknown id.
    unknown = uuid.uuid4()
    for caller in (
        mine_cookie,
        sign_in(client, create_user(url, roles=(GUARDIAN,)).username),
        sign_in(client, a_tutor(url).username),
    ):
        assert_same_not_found(
            get(client, caller, plans_path(student)),
            get(client, caller, plans_path(unknown)),
        )
        assert_same_not_found(
            get(client, caller, plan_path(active["id"])),
            get(client, caller, plan_path(unknown)),
        )
    assert my_cookie


def test_an_adult_needs_the_consent_and_a_revocation_applies_at_once(
    module_seeded_url, client
):
    url = module_seeded_url
    family = guardian_with_child(url, adult=True)
    _, cookie, student, _, view = drafted(client, url, family=family, active=True)
    draft = ok(create(client, cookie, student, view["subject"]["id"]), CREATED)
    guardian = sign_in(client, family.user.username)
    # Not notified without the consent.
    assert notifications(url, ACTIVATED_TYPE, plan_id=view["id"]) == []

    assert code_of(get(client, guardian, plans_path(student))) == CONSENT
    assert code_of(get(client, guardian, plan_path(view["id"]))) == CONSENT
    assert code_of(get(client, guardian, plan_path(draft["id"]))) == CONSENT
    # A family member never writes: every write is the unknown-id 404, never
    # 403 student_consent_required (the matrix; #81 QA D1).

    def writes_are_hidden() -> None:
        unknown = uuid.uuid4()
        for plan in (view, draft):
            assert_same_not_found(
                put(client, guardian, plan_path(plan["id"]), replace_body(plan)),
                put(client, guardian, plan_path(unknown), replace_body(plan)),
            )
            assert_same_not_found(
                transition(client, guardian, plan["id"], 1, "discontinue"),
                transition(client, guardian, unknown, 1, "discontinue"),
            )
            assert_same_not_found(
                progress(client, guardian, first_milestone(plan)["id"], 1, "done"),
                progress(client, guardian, unknown, 1, "done"),
            )
            assert_same_not_found(
                delete(client, guardian, plan["id"], 1),
                delete(client, guardian, unknown, 1),
            )
        assert_same_not_found(
            create(client, guardian, student, view["subject"]["id"]),
            create(client, guardian, unknown, view["subject"]["id"]),
        )

    writes_are_hidden()
    give_academic_consent(url, family)
    assert [
        i["id"] for i in ok(get(client, guardian, plans_path(student)))["items"]
    ] == [view["id"]]
    assert ok(get(client, guardian, plan_path(view["id"])))["id"] == view["id"]
    writes_are_hidden()
    revoke_academic_consent(url, family)
    assert code_of(get(client, guardian, plans_path(student))) == CONSENT
    assert code_of(get(client, guardian, plan_path(view["id"]))) == CONSENT
    writes_are_hidden()


def test_the_recipients_and_params(module_seeded_url, client):
    """E6: the student's login and the guardians with academic access; the
    params carry ids only."""
    url = module_seeded_url
    me, mine = student_account(url)
    _, cookie, _, subject, view = drafted(
        client,
        url,
        family=Family(me, uuid.uuid4(), mine),
        title="Nunca en la notificación",
        description="Tampoco esto",
    )
    ok(transition(client, cookie, view["id"], 1, "activate"))
    [sent] = notifications(url, ACTIVATED_TYPE, plan_id=view["id"])
    assert sent.recipient_user_id == me.id
    assert sent.params == {
        "student_id": str(mine),
        "plan_id": view["id"],
        "subject_id": str(subject),
    }
    assert "Nunca" not in str(sent.params)
    ok(transition(client, cookie, view["id"], 2, "complete"))
    [closed] = notifications(url, COMPLETED_TYPE, plan_id=view["id"])
    assert closed.recipient_user_id == me.id
    assert set(closed.params) == {"student_id", "plan_id", "subject_id"}


# --- I3: IDOR -------------------------------------------------------------------------


def test_the_hidden_plans_get_the_same_404(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    tutor, cookie, student, subject, draft = drafted(client, url, family=family)
    active = ok(create(client, cookie, student, subject), CREATED)
    active = ok(transition(client, cookie, active["id"], 1, "activate"))
    lost = create_user(url, roles=(TUTOR, GUARDIAN))
    teaching(url, tutor=lost, students=(student,), subject=subject)
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": lost.id},
    )
    other_student, _ = student_account(url)
    # A tutor of another student, whose own plan's milestone is foreign here.
    _, foreign_cookie, _, _, foreign = drafted(client, url)
    unknown = uuid.uuid4()

    def same(caller: Any, view: dict[str, Any]) -> None:
        plan_id = view["id"]
        milestone_id = first_milestone(view)["id"]
        body = replace_body(view)
        assert_same_not_found(
            get(client, caller, plan_path(plan_id)),
            get(client, caller, plan_path(unknown)),
        )
        assert_same_not_found(
            put(client, caller, plan_path(plan_id), body),
            put(client, caller, plan_path(unknown), body),
        )
        assert_same_not_found(
            transition(client, caller, plan_id, 1, "discontinue"),
            transition(client, caller, unknown, 1, "discontinue"),
        )
        assert_same_not_found(
            delete(client, caller, plan_id, 1), delete(client, caller, unknown, 1)
        )
        assert_same_not_found(
            progress(client, caller, milestone_id, 1, "done"),
            progress(client, caller, unknown, 1, "done"),
        )

    outsiders = [
        sign_in(client, a_tutor(url).username),
        sign_in(client, lost.username),
        sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username),
        sign_in(client, create_user(url, roles=(GUARDIAN,)).username),
        sign_in(client, other_student.username),
        foreign_cookie,
    ]
    for caller in outsiders:
        same(caller, draft)
        same(caller, active)
        assert_same_not_found(
            get(client, caller, plans_path(student)),
            get(client, caller, plans_path(unknown)),
        )
        assert_same_not_found(
            create(client, caller, student, subject),
            create(client, caller, unknown, subject),
        )
    # The family: a draft's detail and milestone are the unknown-id 404; an
    # active plan is readable, never writable.
    guardian = sign_in(client, family.user.username)
    same_draft = draft
    assert_same_not_found(
        get(client, guardian, plan_path(same_draft["id"])),
        get(client, guardian, plan_path(unknown)),
    )
    assert_same_not_found(
        progress(client, guardian, first_milestone(same_draft)["id"], 1, "done"),
        progress(client, guardian, unknown, 1, "done"),
    )
    assert ok(get(client, guardian, plan_path(active["id"])))["id"] == active["id"]
    for response in (
        put(client, guardian, plan_path(active["id"]), replace_body(active)),
        transition(client, guardian, active["id"], 2, "complete"),
        delete(client, guardian, active["id"], 2),
        progress(client, guardian, first_milestone(active)["id"], 1, "done"),
        create(client, guardian, student, subject),
    ):
        assert_same_not_found(response, get(client, guardian, plan_path(unknown)))
    # The tutor on another student's milestone.
    assert_same_not_found(
        progress(client, cookie, first_milestone(foreign)["id"], 1, "done"),
        progress(client, cookie, unknown, 1, "done"),
    )
    assert tutor


def test_the_original_tutor_after_a_real_substitution(
    module_seeded_url, client, owner, at_now
):
    """#55: once the substitute accepted, the original tutor (the student
    not otherwise assigned) is a former creator: their plan is read-only;
    the substitute (current tutor in the subject) edits it."""
    url = module_seeded_url
    at_now.set(T0)
    subject = new_subject(url)
    setup = Setup(
        student=new_student(url),
        subject=subject,
        tutor=new_tutor_user(url, qualified_for=[subject]),
        room=new_room(url),
    )
    substitute = new_tutor_user(url, qualified_for=[subject])
    boss = sign_in(client, owner.username)
    starts_at = at(OFFER_MONDAY, "16:00")
    created, offer_id = offered(client, boss, setup, starts_at=starts_at)
    original = sign_in(client, setup.tutor.username)
    assert code_of(answer(client, original, offer_id, "accept")) == OK
    own = ok(create(client, original, setup.student, subject), CREATED)
    others = ok(create(client, boss, setup.student, subject), CREATED)
    detail = get(client, boss, f"/api/v1/class-sessions/{created['id']}").json()
    item = substituted(client, boss, detail, substitute.id)
    taker = sign_in(client, substitute.username)
    assert code_of(answer(client, taker, item["offer_id"], "accept")) == OK

    original = sign_in(client, setup.tutor.username)
    assert (
        code_of(put(client, original, plan_path(own["id"]), replace_body(own)))
        == READ_ONLY
    )
    assert ok(get(client, original, plan_path(own["id"])))["id"] == own["id"]
    assert_same_not_found(
        get(client, original, plan_path(others["id"])),
        get(client, original, plan_path(uuid.uuid4())),
    )
    assert code_of(create(client, original, setup.student, subject)) == NOT_FOUND
    taker = sign_in(client, substitute.username)
    edited = ok(
        put(client, taker, plan_path(own["id"]), replace_body(own, title="Sustituta"))
    )
    assert edited["title"] == "Sustituta"
    hints = ok(get(client, taker, plans_path(setup.student)))
    assert [s["id"] for s in hints["creatable_subjects"]] == [str(subject)]


# --- B2: the seams agree --------------------------------------------------------------


def test_the_assignment_seams_agree_on_the_window(module_seeded_url):
    """B2 drift test: a student is one of the tutor's current assignments
    exactly when the tutor currently teaches them some subject, over the
    same states and lookback window."""
    url = module_seeded_url
    tutor = a_tutor(url)
    window = scheduling.TUTOR_ASSIGNMENT_LOOKBACK_DAYS
    cases = {
        "confirmed now": ("confirmed", STARTED, True),
        "offered later": ("offered", NOW + timedelta(days=3), True),
        "completed in the window": (
            "completed",
            NOW - timedelta(days=window) + timedelta(hours=1),
            True,
        ),
        "completed past the window": (
            "completed",
            NOW - timedelta(days=window) - timedelta(hours=2),
            False,
        ),
        "cancelled": ("cancelled", STARTED, False),
        "unassigned": ("unassigned", STARTED, False),
    }
    expected_subjects: dict[uuid.UUID, set[uuid.UUID]] = {}
    for state, starts_at, counts in cases.values():
        student = new_student(url)
        subject = new_subject(url)
        stored(
            url,
            starts_at,
            state=state,
            students=(student,),
            tutor_user_id=None if state == "unassigned" else tutor.id,
            subject_id=subject,
        )
        expected_subjects[student] = {subject} if counts else set()
    # Two subjects for one student.
    student = new_student(url)
    subjects = {new_subject(url), new_subject(url)}
    for day, subject in enumerate(subjects, start=5):
        stored(
            url,
            STARTED + timedelta(days=day),
            students=(student,),
            tutor_user_id=tutor.id,
            subject_id=subject,
        )
    expected_subjects[student] = subjects

    async def _check(session: AsyncSession) -> None:
        now = clock.now()
        assigned = await scheduling.student_ids_for_tutor(session, tutor.id, now=now)
        for student_id, wanted in expected_subjects.items():
            found = await scheduling.subject_ids_for_tutor_student(
                session, tutor.id, student_id, now=now
            )
            assert found == wanted, student_id
            assert (student_id in assigned) is bool(found), student_id

    in_uow(url, _check)


# --- Budgets (E2, E3) -----------------------------------------------------------------


def _many(
    url: str, client: TestClient, count: int
) -> tuple[CreatedUser, Family, uuid.UUID]:
    family = guardian_with_child(url, adult=True, consent=True)
    tutor, student, subject = teaching(url, students=(family.student_id,))
    cookie = sign_in(client, tutor.username)
    for _ in range(count):
        view = ok(create(client, cookie, student, subject), CREATED)
        ok(transition(client, cookie, view["id"], 1, "activate"))
        ok(transition(client, cookie, view["id"], 2, "complete"))
    return tutor, family, student


@pytest.mark.parametrize("count", [1, 20])
def test_the_list_statement_budget(module_seeded_url, client, coordinator, count):
    url = module_seeded_url
    tutor, family, student = _many(url, client, count)
    for principal in (
        principal_of(tutor, TUTOR),
        principal_of(coordinator, ACADEMIC_COORDINATOR),
        principal_of(family.user, GUARDIAN),
    ):
        statements, listed = count_statements(
            url,
            lambda s, p=principal: workflow.list_student_plans(
                s, p, student, statuses=[], subject_id=None
            ),
        )
        assert statements <= 7, (principal.roles, statements)
        assert len(listed.items) == count


@pytest.mark.parametrize("shape", [(1, 1), (5, 10)])
def test_the_detail_statement_budget(module_seeded_url, client, coordinator, shape):
    url = module_seeded_url
    objectives, milestones = shape
    family = guardian_with_child(url, adult=True, consent=True)
    _, cookie, _, _, view = drafted(
        client,
        url,
        family=family,
        active=True,
        objectives=[
            objective(f"O{i}", *[milestone(f"M{j}") for j in range(milestones)])
            for i in range(objectives)
        ],
    )
    ok(progress(client, cookie, first_milestone(view)["id"], 1, "done"))
    tutor_id = run_sql(
        url,
        "SELECT created_by_user_id FROM learning_plans WHERE id = :id",
        {"id": view["id"]},
    )[0].created_by_user_id
    for principal in (
        principal_of(tutor_id, TUTOR),
        principal_of(coordinator, ACADEMIC_COORDINATOR),
        principal_of(family.user, GUARDIAN),
    ):
        statements, detail = count_statements(
            url,
            lambda s, p=principal: workflow.get_plan(s, p, uuid.UUID(view["id"])),
        )
        assert statements <= 6, (principal.roles, statements)
        assert detail.progress.total == objectives * milestones
