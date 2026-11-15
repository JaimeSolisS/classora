"""Evaluations and the unified academic history (#80 C, D, E, F, AM1, I2,
I3) with real logins through the real app.

Templates (create, replace, deactivate, list filters, the C2 order), the D2
create order step by step (template, subject, session link, course link,
date, scores), the lifecycle (draft → edit → finalize → amend → void,
draft → delete) with its audit rows and notifications, the D3 write order,
the criteria copy (C4), the former-student read-only rule (frozen clock),
the working list, the history (several tutors, grouping by period type,
filters, the 500 cap, averages), the B3 recipients and params, the IDOR
cases (the same ``404`` as an unknown id), the adult consent rule, the
evaluation context of a class (AM1) and the statement budgets. The family
leak walk over every ``GET`` route is ``test_session_notes_leak.py``; the
D9 races are ``test_evaluations_concurrency.py``.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    TUTOR,
)
from app.workflows import evaluations as workflow
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.courses import persist_course_cohort
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
TOMORROW = NOW + timedelta(days=1)

OK = (200, None)
CREATED = (201, None)
NO_CONTENT = (204, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
DENIED = (403, "permission_denied")
VERSION_CONFLICT = (409, "version_conflict")
TUTOR_ONLY = (403, "evaluation_tutor_only")
AUTHOR_ONLY = (403, "evaluation_author_only")
READ_ONLY = (403, "evaluation_read_only")
CONSENT = (403, "student_consent_required")

FINALIZED_TYPE = "academics.evaluation_finalized"
VOIDED_TYPE = "academics.evaluation_voided"

VIEW_KEYS = {
    "id",
    "version",
    "status",
    "student",
    "subject",
    "author",
    "is_own",
    "template",
    "kind",
    "evaluated_on",
    "class_session",
    "course",
    "scores",
    "feedback",
    "score_total",
    "max_total",
    "score_percent",
    "finalized_at",
    "amended_at",
    "last_change_reason",
    "voided",
    "created_at",
    "can_edit",
    "can_finalize",
    "can_amend",
    "can_delete",
    "can_void",
}


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
        display_name="Coordinadora Académica",
    )


@pytest.fixture(scope="module")
def staff(client: TestClient, coordinator: CreatedUser) -> Any:
    """The coordinator's cookie (evaluation.manage)."""
    return sign_in(client, coordinator.username)


# --- Helpers --------------------------------------------------------------------------

TEMPLATES = "/api/v1/evaluation-templates"
EVALUATIONS = "/api/v1/evaluations"


def template_path(template_id: Any) -> str:
    return f"{TEMPLATES}/{template_id}"


def evaluation_path(evaluation_id: Any, action: str = "") -> str:
    return f"{EVALUATIONS}/{evaluation_id}{action}"


def working_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/evaluations"


def history_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/academic-history"


def context_path(session_id: Any) -> str:
    return f"/api/v1/class-sessions/{session_id}/evaluation-context"


def ok(response: Response, expected: tuple[int, None] = OK) -> dict[str, Any]:
    assert code_of(response) == expected, response.text
    body: dict[str, Any] = response.json()
    return body


def field_codes(response: Response) -> list[str]:
    assert code_of(response) == INVALID, response.text
    return [item["code"] for item in response.json()["errors"]]


def template_body(
    *,
    name: str | None = None,
    kind: str = "ongoing",
    subject_id: Any = None,
    criteria: tuple[tuple[str, str], ...] = (("Lectura", "10"), ("Escritura", "5")),
    **values: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name or f"Plantilla {uuid.uuid4().hex[:8]}",
        "kind": kind,
        "criteria": [
            {"title": title, "max_score": max_score} for title, max_score in criteria
        ],
        **values,
    }
    if subject_id is not None:
        body["subject_id"] = str(subject_id)
    return body


def new_template(client: TestClient, staff: Any, **values: Any) -> dict[str, Any]:
    return ok(post(client, staff, TEMPLATES, template_body(**values)), CREATED)


def replace_body(view: dict[str, Any], **changes: Any) -> dict[str, Any]:
    body = {
        "version": view["version"],
        "name": view["name"],
        "kind": view["kind"],
        "description": view["description"],
        "is_active": view["is_active"],
        "criteria": [
            {
                "title": c["title"],
                "description": c["description"],
                "max_score": c["max_score"],
            }
            for c in view["criteria"]
        ],
    }
    if view["subject"] is not None:
        body["subject_id"] = view["subject"]["id"]
    return body | changes


def a_tutor(url: str, name: str = "Tutora Evalúa") -> CreatedUser:
    return create_user(url, roles=(TUTOR,), display_name=name)


def teaching(
    url: str,
    *,
    tutor: CreatedUser | None = None,
    students: tuple[uuid.UUID, ...] | None = None,
    state: str = "confirmed",
    starts_at: datetime = STARTED,
    **overrides: Any,
) -> tuple[str, CreatedUser, tuple[uuid.UUID, ...]]:
    """A stored session of ``tutor`` (new by default) with ``students`` (one
    new student by default): the students are its current assignments."""
    tutor = tutor or a_tutor(url)
    students = students or (new_student(url),)
    session_id = stored(
        url,
        starts_at,
        state=state,
        students=students,
        tutor_user_id=tutor.id,
        **overrides,
    )
    return session_id, tutor, students


def create(
    client: TestClient,
    cookie: Any,
    student_id: Any,
    template: dict[str, Any],
    **values: Any,
) -> Response:
    body: dict[str, Any] = {
        "student_id": str(student_id),
        "template_id": str(template["id"]),
    }
    if template.get("subject") is None and "subject_id" not in values:
        values["subject_id"] = str(SUBJECT)
    return post(client, cookie, EVALUATIONS, body | values)


def full_scores(template: dict[str, Any], score: str = "4") -> list[dict[str, Any]]:
    return [{"position": c["position"], "score": score} for c in template["criteria"]]


def edit(client: TestClient, cookie: Any, evaluation_id: Any, body: Any) -> Response:
    return patch(client, cookie, evaluation_path(evaluation_id), body)


def finalize(
    client: TestClient, cookie: Any, evaluation_id: Any, version: int
) -> Response:
    return post(
        client,
        cookie,
        evaluation_path(evaluation_id, "/finalize"),
        {"version": version},
    )


def void(
    client: TestClient, cookie: Any, evaluation_id: Any, version: int, reason: Any = "x"
) -> Response:
    return post(
        client,
        cookie,
        evaluation_path(evaluation_id, "/void"),
        {"version": version, "reason": reason},
    )


def delete(
    client: TestClient, cookie: Any, evaluation_id: Any, version: int
) -> Response:
    client.cookies.clear()
    return client.delete(
        evaluation_path(evaluation_id),
        params={"version": version},
        headers=signed_headers(cookie),
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


SUBJECT: uuid.UUID = uuid.UUID(int=0)
"""The subject of an any-subject template's evaluations (set once)."""


@pytest.fixture(scope="module", autouse=True)
def _subject(module_seeded_url: str) -> None:
    global SUBJECT
    SUBJECT = new_subject(module_seeded_url)


def written(
    client: TestClient,
    url: str,
    staff: Any,
    *,
    family: Family | None = None,
    tutor: CreatedUser | None = None,
    final: bool = False,
    feedback: str | None = None,
    template: dict[str, Any] | None = None,
) -> tuple[CreatedUser, Any, uuid.UUID, dict[str, Any]]:
    """An evaluation by a tutor teaching one student (``family``'s child when
    given): ``(tutor, tutor cookie, student id, view)``; ``final``
    finalizes it with every criterion at 4."""
    students = None if family is None else (family.student_id,)
    _, tutor, (student,) = teaching(url, tutor=tutor, students=students)
    template = template or new_template(client, staff)
    cookie = sign_in(client, tutor.username)
    values: dict[str, Any] = {"scores": full_scores(template)}
    if feedback is not None:
        values["feedback"] = feedback
    view = ok(create(client, cookie, student, template, **values), CREATED)
    if final:
        view = ok(finalize(client, cookie, view["id"], view["version"]))
    return tutor, cookie, student, view


# --- C: templates ---------------------------------------------------------------------


def test_templates_create_replace_deactivate_and_list(module_seeded_url, client, staff):
    url = module_seeded_url
    subject = new_subject(url)
    name = f"Diagnóstico {uuid.uuid4().hex[:6]}"

    created = ok(
        post(
            client,
            staff,
            TEMPLATES,
            template_body(
                name=f"  {name}  ",
                kind="diagnostic",
                subject_id=subject,
                description="Inicio de curso",
                criteria=(("Lectura", "10"), ("Cálculo", "7.5")),
            ),
        ),
        CREATED,
    )
    assert set(created) == {
        "id",
        "version",
        "name",
        "kind",
        "subject",
        "description",
        "is_active",
        "criteria",
        "created_at",
        "updated_at",
    }
    assert (created["name"], created["kind"], created["version"]) == (
        name,
        "diagnostic",
        1,
    )
    assert created["subject"]["id"] == str(subject)
    assert created["criteria"] == [
        {"position": 1, "title": "Lectura", "description": None, "max_score": "10.00"},
        {"position": 2, "title": "Cálculo", "description": None, "max_score": "7.50"},
    ]
    assert [
        str(e.record_id)
        for e in events(url, action="create", record_type="evaluation_templates")
        if str(e.record_id) == created["id"]
    ] == [created["id"]]

    # A criteria-only change bumps the version once; the same body again
    # changes nothing.
    body = replace_body(created)
    body["criteria"] = [
        {"title": "Lectura", "max_score": "20"},
        {"title": "Cálculo", "max_score": "7.5"},
        {"title": "Oral", "max_score": "5"},
    ]
    replaced = ok(put(client, staff, template_path(created["id"]), body))
    assert replaced["version"] == 2
    assert [c["max_score"] for c in replaced["criteria"]] == ["20.00", "7.50", "5.00"]
    same = ok(put(client, staff, template_path(created["id"]), body | {"version": 2}))
    assert same["version"] == 2
    # Remove a criterion and deactivate.
    body["criteria"] = body["criteria"][:1]
    body |= {"version": 2, "is_active": False}
    inactive = ok(put(client, staff, template_path(created["id"]), body))
    assert (inactive["version"], inactive["is_active"]) == (3, False)
    assert [c["title"] for c in inactive["criteria"]] == ["Lectura"]

    other = new_template(client, staff, kind="ongoing")
    anyone = new_template(client, staff, kind="diagnostic", subject_id=new_subject(url))

    def names(cookie: Any, **params: Any) -> set[str]:
        return {
            item["name"]
            for item in ok(get(client, cookie, TEMPLATES, **params))["items"]
        }

    assert name not in names(staff)
    assert name in names(staff, status="inactive")
    assert name in names(staff, status="all")
    assert other["name"] not in names(staff, status="inactive")
    assert other["name"] in names(staff, kind="ongoing")
    assert other["name"] not in names(staff, kind="diagnostic")
    # subject_id matches that subject or any-subject templates.
    by_subject = names(staff, subject_id=str(subject), status="all")
    assert {name, other["name"]} <= by_subject
    assert anyone["name"] not in by_subject
    assert names(staff, q=name.upper()[:12], status="all") == {name}
    # A tutor never sees inactive templates.
    tutor = sign_in(client, a_tutor(url).username)
    assert name not in names(tutor, status="all")
    assert name not in names(tutor, status="inactive")
    assert other["name"] in names(tutor)
    assert code_of(get(client, tutor, template_path(created["id"]))) == NOT_FOUND
    assert ok(get(client, tutor, template_path(other["id"])))["id"] == other["id"]


def test_the_template_order(module_seeded_url, client, staff, owner):
    url = module_seeded_url
    view = new_template(client, staff)
    unknown = uuid.uuid4()
    # 401.
    client.cookies.clear()
    assert code_of(client.get(TEMPLATES))[0] == 401
    # 422 schema.
    assert field_codes(post(client, staff, TEMPLATES, template_body(criteria=()))) == [
        "criteria_required"
    ]
    # 403 for writes without evaluation.manage, and for reads without it or
    # the tutor role.
    tutor = sign_in(client, a_tutor(url).username)
    assert code_of(post(client, tutor, TEMPLATES, template_body())) == DENIED
    for roles in ((FINANCE_STAFF,), (GUARDIAN,)):
        caller = sign_in(client, create_user(url, roles=roles).username)
        assert code_of(get(client, caller, TEMPLATES)) == DENIED
        assert code_of(get(client, caller, template_path(view["id"]))) == DENIED
        assert code_of(get(client, caller, template_path(unknown))) == DENIED
    student, _ = student_account(url)
    caller = sign_in(client, student.username)
    assert code_of(get(client, caller, TEMPLATES)) == DENIED
    # 404.
    assert code_of(get(client, staff, template_path(unknown))) == NOT_FOUND
    assert (
        code_of(put(client, staff, template_path(unknown), replace_body(view)))
        == NOT_FOUND
    )
    # 409 version_conflict, then the content checks.
    stale = replace_body(view, version=7, kind="diagnostic")
    assert code_of(put(client, staff, template_path(view["id"]), stale)) == (
        VERSION_CONFLICT
    )
    response = put(
        client, staff, template_path(view["id"]), replace_body(view, kind="diagnostic")
    )
    assert field_codes(response) == ["kind_immutable"]
    taken = new_template(client, staff)
    assert code_of(
        post(client, staff, TEMPLATES, template_body(name=taken["name"].upper()))
    ) == (409, "duplicate_name")
    assert code_of(
        put(
            client,
            staff,
            template_path(view["id"]),
            replace_body(view, name=taken["name"].lower()),
        )
    ) == (409, "duplicate_name")
    for subject in (
        new_subject(url, is_active=False),
        new_subject(url, level_active=False),
        uuid.uuid4(),
    ):
        assert field_codes(
            post(client, staff, TEMPLATES, template_body(subject_id=subject))
        ) == ["subject_unknown"]
        assert field_codes(
            put(
                client,
                staff,
                template_path(view["id"]),
                replace_body(view, subject_id=str(subject)),
            )
        ) == ["subject_unknown"]
    # The Owner holds evaluation.manage too.
    ok(
        post(client, sign_in(client, owner.username), TEMPLATES, template_body()),
        CREATED,
    )


# --- D2: create -----------------------------------------------------------------------


def test_the_tutor_creates_a_draft(module_seeded_url, client, staff):
    url = module_seeded_url
    _, tutor, (student,) = teaching(url)
    subject = new_subject(url)
    template = new_template(
        client,
        staff,
        kind="diagnostic",
        subject_id=subject,
        criteria=(("Lectura", "10"), ("Escritura", "5"), ("Oral", "2.5")),
    )
    cookie = sign_in(client, tutor.username)
    [before] = run_sql(url, "SELECT count(*) AS n FROM notifications")

    view = ok(
        create(
            client,
            cookie,
            student,
            template,
            scores=[{"position": 2, "score": "4.5", "comment": "  Buena letra "}],
            feedback="  Avanza bien  ",
        ),
        CREATED,
    )

    assert set(view) == VIEW_KEYS
    assert (view["status"], view["version"], view["is_own"], view["kind"]) == (
        "draft",
        1,
        True,
        "diagnostic",
    )
    assert view["student"]["id"] == str(student)
    assert view["subject"]["id"] == str(subject)
    assert view["author"] == {"display_name": "Tutora Evalúa"}
    assert view["template"] == {
        "id": template["id"],
        "name": template["name"],
        "version": 1,
    }
    assert view["evaluated_on"] == TODAY.isoformat()
    assert (view["class_session"], view["course"]) == (None, None)
    assert view["scores"] == [
        {
            "position": 1,
            "title": "Lectura",
            "description": None,
            "max_score": "10.00",
            "score": None,
            "comment": None,
        },
        {
            "position": 2,
            "title": "Escritura",
            "description": None,
            "max_score": "5.00",
            "score": "4.50",
            "comment": "Buena letra",
        },
        {
            "position": 3,
            "title": "Oral",
            "description": None,
            "max_score": "2.50",
            "score": None,
            "comment": None,
        },
    ]
    assert view["feedback"] == "Avanza bien"
    assert (view["score_total"], view["score_percent"], view["finalized_at"]) == (
        None,
        None,
        None,
    )
    assert (
        view["can_edit"],
        view["can_finalize"],
        view["can_amend"],
        view["can_delete"],
        view["can_void"],
    ) == (True, True, False, True, False)
    created = [
        e
        for e in events(url, action="create", record_type="evaluations")
        if str(e.record_id) == view["id"]
    ]
    assert len(created) == 1
    assert len(events(url, action="create", record_type="evaluation_scores")) >= 3
    # A create never notifies anyone.
    assert run_sql(url, "SELECT count(*) AS n FROM notifications") == [before]


def test_the_create_order(module_seeded_url, client, staff, coordinator):
    url = module_seeded_url
    session_id, tutor, (student,) = teaching(url)
    cookie = sign_in(client, tutor.username)
    template = new_template(client, staff)
    unknown = uuid.uuid4()

    # 1. 401.
    client.cookies.clear()
    assert code_of(client.post(EVALUATIONS, json={}))[0] == 401
    # 2. 422 schema.
    assert code_of(post(client, cookie, EVALUATIONS, {"student_id": str(student)})) == (
        INVALID
    )
    # 3. 404: unknown student, a student the tutor doesn't teach, Finance.
    assert_same_not_found(
        create(client, cookie, new_student(url), template),
        create(client, cookie, unknown, template),
    )
    finance = sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username)
    assert_same_not_found(
        create(client, finance, student, template),
        create(client, finance, unknown, template),
    )
    # 4. 403 evaluation_tutor_only for a reviewer (404 for an unknown student).
    assert code_of(create(client, staff, student, template)) == TUTOR_ONLY
    assert code_of(create(client, staff, unknown, template)) == NOT_FOUND
    # 5. The template: unknown → 422, inactive → 409.
    response = create(client, cookie, student, {"id": unknown, "subject": None})
    assert field_codes(response) == ["template_unknown"]
    inactive = new_template(client, staff)
    ok(
        put(
            client,
            staff,
            template_path(inactive["id"]),
            replace_body(inactive, is_active=False),
        )
    )
    assert code_of(create(client, cookie, student, inactive)) == (
        409,
        "template_inactive",
    )
    # 6. The subject.
    subject = new_subject(url)
    bound = new_template(client, staff, subject_id=subject)
    assert field_codes(
        create(client, cookie, student, bound, subject_id=str(new_subject(url)))
    ) == ["subject_mismatch"]
    assert ok(create(client, cookie, student, bound), CREATED)["subject"]["id"] == str(
        subject
    )
    response = post(
        client,
        cookie,
        EVALUATIONS,
        {"student_id": str(student), "template_id": template["id"]},
    )
    assert field_codes(response) == ["subject_required"]
    assert field_codes(
        create(
            client,
            cookie,
            student,
            template,
            subject_id=str(new_subject(url, is_active=False)),
        )
    ) == ["subject_unknown"]
    # 9. The date.
    assert field_codes(
        create(client, cookie, student, template, evaluated_on="2031-03-06")
    ) == ["evaluated_on_in_future"]
    assert field_codes(
        create(client, cookie, student, template, evaluated_on="1999-12-31")
    ) == ["evaluated_on_invalid"]
    # 10. The scores.
    response = create(
        client,
        cookie,
        student,
        template,
        scores=[
            {"position": 9, "score": "1"},
            {"position": 1, "score": "11"},
            {"position": 2, "score": "1"},
            {"position": 2, "score": "1"},
        ],
    )
    assert response.json()["errors"] == [
        {
            "path": ["body", "scores", 0, "position"],
            "code": "criterion_unknown",
            "message": "The template has no criterion at this position.",
        },
        {
            "path": ["body", "scores", 1, "score"],
            "code": "score_out_of_range",
            "message": "The score is between 0 and the criterion's maximum.",
        },
        {
            "path": ["body", "scores", 3, "position"],
            "code": "duplicate_criterion",
            "message": "This criterion is scored twice.",
        },
    ]
    assert field_codes(
        create(
            client, cookie, student, template, scores=[{"position": 1, "score": "-1"}]
        )
    ) == ["score_out_of_range"]
    assert field_codes(
        create(
            client,
            cookie,
            student,
            template,
            scores=[{"position": 1, "score": "1.001"}],
        )
    ) == ["score_precision"]
    # 11-12. 201, an explicit past date.
    view = ok(
        create(client, cookie, student, template, evaluated_on="2030-11-02"), CREATED
    )
    assert view["evaluated_on"] == "2030-11-02"
    # A tutor who is also a coordinator records as the tutor.
    both = create_user(url, roles=(TUTOR, ACADEMIC_COORDINATOR))
    teaching(url, tutor=both, students=(student,))
    ok(create(client, sign_in(client, both.username), student, template), CREATED)
    assert session_id


def test_the_session_link(module_seeded_url, client, staff):
    url = module_seeded_url
    subject = new_subject(url)
    template = new_template(client, staff, subject_id=subject)
    session_id, tutor, (student,) = teaching(
        url, subject_id=subject, starts_at=NOW - timedelta(days=1, hours=6)
    )
    cookie = sign_in(client, tutor.username)

    view = ok(
        create(client, cookie, student, template, class_session_id=session_id), CREATED
    )
    assert view["class_session"]["id"] == session_id
    assert datetime.fromisoformat(
        view["class_session"]["starts_at"]
    ) == NOW - timedelta(days=1, hours=6)
    # The date defaults to the class's local date (12:00 UTC is 06:00 local).
    assert view["evaluated_on"] == "2031-03-04"
    completed, _, _ = teaching(
        url,
        tutor=tutor,
        students=(student,),
        state="completed",
        subject_id=subject,
        starts_at=NOW - timedelta(days=3),
    )
    ok(create(client, cookie, student, template, class_session_id=completed), CREATED)

    # 409 session_not_evaluable: a future confirmed class, offered, cancelled.
    for state, starts_at in (
        ("confirmed", TOMORROW),
        ("offered", NOW - timedelta(hours=3)),
        ("cancelled", NOW - timedelta(hours=5)),
    ):
        overrides: dict[str, Any] = {}
        if state == "cancelled":
            overrides = {
                "cancelled_at": NOW - timedelta(days=2),
                "cancellation_reason": "Lluvia",
            }
        not_yet, _, _ = teaching(
            url,
            tutor=tutor,
            students=(student,),
            state=state,
            starts_at=starts_at,
            subject_id=subject,
            **overrides,
        )
        response = create(client, cookie, student, template, class_session_id=not_yet)
        assert code_of(response) == (409, "session_not_evaluable")
        assert response.json()["details"] == {"state": state}
    # 422 class_session_invalid: unknown, another tutor's class, a group
    # class without the student.
    other_session, _, _ = teaching(url, students=(student,), subject_id=subject)
    group, _, _ = teaching(
        url,
        tutor=tutor,
        students=(new_student(url),),
        kind="group",
        subject_id=subject,
        starts_at=NOW - timedelta(hours=8),
    )
    for session in (uuid.uuid4(), other_session, group):
        response = create(
            client, cookie, student, template, class_session_id=str(session)
        )
        assert field_codes(response) == ["class_session_invalid"]
    # 422 subject_mismatch: the class's subject differs from the template's.
    elsewhere, _, _ = teaching(
        url, tutor=tutor, students=(student,), starts_at=NOW - timedelta(hours=10)
    )
    assert field_codes(
        create(client, cookie, student, template, class_session_id=elsewhere)
    ) == ["subject_mismatch"]


def test_the_course_link(module_seeded_url, client, staff):
    url = module_seeded_url
    subject = new_subject(url)
    template = new_template(client, staff, subject_id=subject)
    kept, other = str(uuid.uuid4()), str(uuid.uuid4())

    async def _cohort(session: AsyncSession) -> uuid.UUID:
        cohort = await persist_course_cohort(
            session,
            subject_id=subject,
            assessments=[
                {"id": kept, "title": "Examen final", "min_score_percent": 70},
                {"id": other, "title": "Parcial", "is_required": False},
            ],
        )
        return cohort.id

    cohort_id = in_uow(url, _cohort)
    session_id, tutor, (student,) = teaching(
        url, subject_id=subject, kind="course", cohort_id=cohort_id
    )
    cookie = sign_in(client, tutor.username)

    # The snapshot's id is accepted (no live template row exists for it).
    view = ok(
        create(
            client,
            cookie,
            student,
            template,
            class_session_id=session_id,
            course_assessment_id=kept,
        ),
        CREATED,
    )
    assert view["course"]["cohort_id"] == str(cohort_id)
    assert view["course"]["assessment_id"] == kept
    assert view["course"]["assessment_title"] == "Examen final"
    assert view["course"]["cohort_name"].startswith("Cohorte ")
    [row] = run_sql(
        url,
        "SELECT cohort_id, course_assessment_id, course_assessment_title"
        " FROM evaluations WHERE id = :id",
        {"id": view["id"]},
    )
    assert (str(row.cohort_id), str(row.course_assessment_id)) == (str(cohort_id), kept)
    # An id the snapshot doesn't hold.
    assert field_codes(
        create(
            client,
            cookie,
            student,
            template,
            class_session_id=session_id,
            course_assessment_id=str(uuid.uuid4()),
        )
    ) == ["course_assessment_unknown"]
    # Without a course class.
    individual, _, _ = teaching(
        url,
        tutor=tutor,
        students=(student,),
        subject_id=subject,
        starts_at=NOW - timedelta(hours=3),
    )
    for values in ({"class_session_id": individual}, {}):
        assert field_codes(
            create(
                client, cookie, student, template, course_assessment_id=kept, **values
            )
        ) == ["course_assessment_requires_course_session"]


# --- D3-D7: the lifecycle -------------------------------------------------------------


def test_the_lifecycle(module_seeded_url, client, staff, coordinator):
    url = module_seeded_url
    family = guardian_with_child(url)
    template = new_template(
        client, staff, criteria=(("Lectura", "10"), ("Escritura", "5"))
    )
    tutor, cookie, student, view = written(
        client, url, staff, family=family, template=template
    )
    evaluation_id = view["id"]
    view = ok(
        edit(
            client,
            cookie,
            evaluation_id,
            {"version": 1, "scores": [{"position": 2, "score": None}]},
        )
    )
    assert view["version"] == 2

    # D4 on a draft: the date, a score, the feedback; the same again writes
    # nothing (no version bump).
    body = {
        "version": 2,
        "evaluated_on": "2031-03-01",
        "scores": [{"position": 1, "score": "7", "comment": "Fluida"}],
        "feedback": "Primer corte",
        "change_reason": "ignored on a draft",
    }
    view = ok(edit(client, cookie, evaluation_id, body))
    assert (view["version"], view["evaluated_on"], view["feedback"]) == (
        3,
        "2031-03-01",
        "Primer corte",
    )
    assert view["last_change_reason"] is None
    assert (
        ok(edit(client, cookie, evaluation_id, body | {"version": 3}))["version"] == 3
    )
    # A scores-only change bumps the version once.
    view = ok(
        edit(
            client,
            cookie,
            evaluation_id,
            {"version": 3, "scores": [{"position": 1, "comment": None}]},
        )
    )
    assert (view["version"], view["scores"][0]["comment"]) == (4, None)

    # D5: incomplete first.
    response = finalize(client, cookie, evaluation_id, 4)
    assert code_of(response) == (409, "evaluation_incomplete")
    assert response.json()["details"] == {"missing_positions": ["2"]}
    ok(
        edit(
            client,
            cookie,
            evaluation_id,
            {"version": 4, "scores": [{"position": 2, "score": "3.5"}]},
        )
    )
    view = ok(finalize(client, cookie, evaluation_id, 5))
    assert (view["status"], view["version"]) == ("final", 6)
    assert (view["score_total"], view["max_total"], view["score_percent"]) == (
        "10.50",
        "15.00",
        "70.00",
    )
    assert datetime.fromisoformat(view["finalized_at"]) == NOW
    assert (view["can_edit"], view["can_amend"], view["can_delete"]) == (
        False,
        True,
        False,
    )
    [sent] = notifications(url, FINALIZED_TYPE, evaluation_id=evaluation_id)
    assert sent.recipient_user_id == family.user.id
    assert sent.params == {
        "student_id": str(student),
        "evaluation_id": evaluation_id,
        "subject_id": view["subject"]["id"],
        "evaluated_on": "2031-03-01",
    }
    assert code_of(finalize(client, cookie, evaluation_id, 6)) == (
        409,
        "evaluation_not_draft",
    )
    assert code_of(delete(client, cookie, evaluation_id, 6)) == (
        409,
        "evaluation_final",
    )

    # D4 amend: a reason is required, every criterion keeps a score.
    amend = {"version": 6, "scores": [{"position": 2, "score": "5"}]}
    assert field_codes(edit(client, cookie, evaluation_id, amend)) == [
        "change_reason_required"
    ]
    assert field_codes(
        edit(client, cookie, evaluation_id, amend | {"change_reason": "x" * 501})
    ) == ["change_reason_required"]
    assert field_codes(
        edit(
            client,
            cookie,
            evaluation_id,
            {
                "version": 6,
                "change_reason": "Corrección",
                "scores": [{"position": 1, "score": None}],
            },
        )
    ) == ["score_required"]
    view = ok(
        edit(client, cookie, evaluation_id, amend | {"change_reason": "Recalificado"})
    )
    assert (view["version"], view["score_total"], view["score_percent"]) == (
        7,
        "12.00",
        "80.00",
    )
    assert datetime.fromisoformat(view["amended_at"]) == NOW
    assert view["last_change_reason"] == "Recalificado"
    [row] = run_sql(
        url,
        "SELECT score_total, score_percent FROM evaluations WHERE id = :id",
        {"id": evaluation_id},
    )
    assert (row.score_total, row.score_percent) == (Decimal("12.00"), Decimal("80.00"))
    updates = [
        e
        for e in events(url, action="update", record_type="evaluations")
        if str(e.record_id) == evaluation_id
    ]
    assert any(
        e.changes.get("last_change_reason", {}).get("new") == "Recalificado"
        and e.changes.get("score_percent", {}).get("old") == "70.00"
        for e in updates
    )
    score_updates = [
        e for e in events(url, action="update", record_type="evaluation_scores")
    ]
    assert any(
        e.changes.get("score") == {"old": "3.50", "new": "5.00"} for e in score_updates
    )
    # Amendments notify nobody.
    assert len(notifications(url, FINALIZED_TYPE, evaluation_id=evaluation_id)) == 1

    # D6: void (staff), with a reason; the author is notified.
    assert code_of(void(client, cookie, evaluation_id, 7)) == DENIED
    assert field_codes(void(client, staff, evaluation_id, 7, reason="  ")) == [
        "reason_required"
    ]
    assert field_codes(void(client, staff, evaluation_id, 7, reason=None)) == [
        "reason_required"
    ]
    view = ok(void(client, staff, evaluation_id, 7, reason="  Alumno equivocado "))
    assert view["voided"]["reason"] == "Alumno equivocado"
    assert view["voided"]["by"] == {"display_name": "Coordinadora Académica"}
    assert (view["version"], view["can_void"], view["can_amend"]) == (8, False, False)
    [sent] = notifications(url, VOIDED_TYPE, evaluation_id=evaluation_id)
    assert sent.recipient_user_id == tutor.id
    assert sent.params == {"student_id": str(student), "evaluation_id": evaluation_id}
    assert code_of(void(client, staff, evaluation_id, 8)) == (409, "evaluation_voided")
    assert code_of(
        edit(client, cookie, evaluation_id, {"version": 8, "change_reason": "x"})
    ) == (409, "evaluation_voided")
    # The author still reads it (the notification's link); the history and
    # the family no longer see it.
    assert ok(get(client, cookie, evaluation_path(evaluation_id)))["voided"]
    guardian = sign_in(client, family.user.username)
    assert ok(get(client, guardian, history_path(student)))["entries"] == []
    assert ok(get(client, cookie, history_path(student)))["entries"] == []


def test_a_draft_is_deleted(module_seeded_url, client, staff):
    url = module_seeded_url
    _, cookie, student, view = written(client, url, staff)
    evaluation_id = view["id"]

    assert code_of(delete(client, cookie, evaluation_id, 9)) == VERSION_CONFLICT
    assert code_of(delete(client, cookie, evaluation_id, 1)) == NO_CONTENT
    [row] = run_sql(
        url,
        "SELECT deleted_at, deleted_by_user_id FROM evaluations WHERE id = :id",
        {"id": evaluation_id},
    )
    assert row.deleted_at == NOW and row.deleted_by_user_id is not None
    [event] = [
        e
        for e in events(url, action="soft_delete", record_type="evaluations")
        if str(e.record_id) == evaluation_id
    ]
    assert event
    unknown = uuid.uuid4()
    assert_same_not_found(
        get(client, cookie, evaluation_path(evaluation_id)),
        get(client, cookie, evaluation_path(unknown)),
    )
    assert_same_not_found(
        finalize(client, cookie, evaluation_id, 1), finalize(client, cookie, unknown, 1)
    )
    assert_same_not_found(
        delete(client, cookie, evaluation_id, 1), delete(client, cookie, unknown, 1)
    )
    assert ok(get(client, cookie, working_path(student)))["items"] == []
    # A delete notifies nobody.
    assert notifications(url, FINALIZED_TYPE, evaluation_id=evaluation_id) == []


def test_the_write_order(module_seeded_url, client, staff):
    url = module_seeded_url
    tutor, cookie, student, view = written(client, url, staff, final=True)
    evaluation_id = view["id"]
    _, colleague, _ = teaching(url, students=(student,))
    other = sign_in(client, colleague.username)

    # 1. 401.
    client.cookies.clear()
    assert code_of(client.patch(evaluation_path(evaluation_id), json={}))[0] == 401
    # 2. 422: no version, an unknown field.
    assert code_of(edit(client, cookie, evaluation_id, {})) == INVALID
    assert (
        code_of(edit(client, cookie, evaluation_id, {"version": 2, "status": "draft"}))
        == INVALID
    )
    # 4. 403: another tutor teaching the student reads it but doesn't write.
    assert ok(get(client, other, evaluation_path(evaluation_id)))["can_amend"] is False
    for response in (
        edit(client, other, evaluation_id, {"version": 2, "change_reason": "x"}),
        finalize(client, other, evaluation_id, 2),
        delete(client, other, evaluation_id, 2),
    ):
        assert code_of(response) == AUTHOR_ONLY
    assert code_of(void(client, other, evaluation_id, 2)) == DENIED
    # A reviewer doesn't amend.
    assert (
        code_of(
            edit(client, staff, evaluation_id, {"version": 2, "change_reason": "x"})
        )
        == AUTHOR_ONLY
    )
    # 6. 409 version_conflict before the state and content checks.
    assert code_of(edit(client, cookie, evaluation_id, {"version": 1})) == (
        VERSION_CONFLICT
    )
    assert (
        code_of(void(client, staff, evaluation_id, 1, reason=" ")) == VERSION_CONFLICT
    )
    # 7. 409 state before 8. 422 content.
    assert code_of(finalize(client, cookie, evaluation_id, 2)) == (
        409,
        "evaluation_not_draft",
    )
    assert tutor


def test_a_template_edit_never_changes_an_evaluation(module_seeded_url, client, staff):
    url = module_seeded_url
    template = new_template(client, staff, criteria=(("Lectura", "10"),))
    _, cookie, student, view = written(client, url, staff, template=template)
    replaced = ok(
        put(
            client,
            staff,
            template_path(template["id"]),
            replace_body(
                template,
                name=f"Renombrada {uuid.uuid4().hex[:6]}",
                criteria=[
                    {"title": "Comprensión", "max_score": "20"},
                    {"title": "Oral", "max_score": "5"},
                ],
            ),
        )
    )
    assert replaced["version"] == 2

    again = ok(get(client, cookie, evaluation_path(view["id"])))
    assert [(s["title"], s["max_score"]) for s in again["scores"]] == [
        ("Lectura", "10.00")
    ]
    assert again["template"] == {
        "id": template["id"],
        "name": template["name"],
        "version": 1,
    }
    final = ok(finalize(client, cookie, view["id"], 1))
    assert (final["max_total"], final["score_percent"]) == ("10.00", "40.00")
    # Deactivated: the evaluation stays; a new one can't use it.
    ok(
        put(
            client,
            staff,
            template_path(template["id"]),
            replace_body(replaced, is_active=False),
        )
    )
    assert ok(get(client, cookie, evaluation_path(view["id"])))["status"] == "final"
    # A new evaluation copies the current criteria.
    fresh = new_template(client, staff, criteria=(("Uno", "3"),))
    created = ok(create(client, cookie, student, fresh), CREATED)
    assert [s["title"] for s in created["scores"]] == ["Uno"]


# --- E5: the former student -----------------------------------------------------------


def test_a_former_students_evaluations_are_read_only(
    module_seeded_url, client, staff, at_now
):
    """W5.4: the last class ended 31+ days ago and nothing is upcoming: the
    author still reads their evaluations, read-only; others' are hidden."""
    url = module_seeded_url
    long_ago = NOW - timedelta(days=40)
    template = new_template(client, staff)
    _, tutor, (student,) = teaching(url, state="completed", starts_at=long_ago)
    at_now.set(long_ago + timedelta(hours=2))
    cookie = sign_in(client, tutor.username)
    draft = ok(create(client, cookie, student, template), CREATED)
    final = ok(
        create(client, cookie, student, template, scores=full_scores(template)),
        CREATED,
    )
    final = ok(finalize(client, cookie, final["id"], 1))
    at_now.set(NOW)
    # A later tutor's evaluation of the student.
    _, later, _ = teaching(url, students=(student,))
    later_cookie = sign_in(client, later.username)
    theirs = ok(
        create(client, later_cookie, student, template, scores=full_scores(template)),
        CREATED,
    )
    theirs = ok(finalize(client, later_cookie, theirs["id"], 1))
    cookie = sign_in(client, tutor.username)

    assert (
        code_of(edit(client, cookie, draft["id"], {"version": 1, "feedback": "x"}))
        == READ_ONLY
    )
    assert code_of(finalize(client, cookie, draft["id"], 1)) == READ_ONLY
    assert code_of(delete(client, cookie, draft["id"], 1)) == READ_ONLY
    assert (
        code_of(edit(client, cookie, final["id"], {"version": 2, "change_reason": "x"}))
        == READ_ONLY
    )
    assert code_of(create(client, cookie, student, template)) == NOT_FOUND
    own = ok(get(client, cookie, evaluation_path(final["id"])))
    assert not any(
        own[f] for f in ("can_edit", "can_amend", "can_finalize", "can_delete")
    )
    assert_same_not_found(
        get(client, cookie, evaluation_path(theirs["id"])),
        get(client, cookie, evaluation_path(uuid.uuid4())),
    )
    listed = ok(get(client, cookie, working_path(student)))["items"]
    assert {item["id"] for item in listed} == {draft["id"], final["id"]}
    history = ok(get(client, cookie, history_path(student)))
    assert [entry["id"] for entry in history["entries"]] == [final["id"]]
    # The current tutor reads both finals, not the former author's draft.
    current = ok(get(client, later_cookie, working_path(student)))["items"]
    assert {item["id"] for item in current} == {final["id"], theirs["id"]}


# --- E: the working list --------------------------------------------------------------


def test_the_working_list(module_seeded_url, client, staff):
    url = module_seeded_url
    subject = new_subject(url)
    template = new_template(client, staff)
    bound = new_template(client, staff, subject_id=subject)
    session_id, tutor, (student,) = teaching(url, subject_id=subject)
    cookie = sign_in(client, tutor.username)
    _, colleague, _ = teaching(url, students=(student,))
    other = sign_in(client, colleague.username)

    mine_draft = ok(create(client, cookie, student, template), CREATED)
    linked = ok(
        create(
            client,
            cookie,
            student,
            bound,
            class_session_id=session_id,
            scores=full_scores(bound),
        ),
        CREATED,
    )
    linked = ok(finalize(client, cookie, linked["id"], 1))
    voided = ok(
        create(client, cookie, student, template, scores=full_scores(template)),
        CREATED,
    )
    voided = ok(finalize(client, cookie, voided["id"], 1))
    ok(void(client, staff, voided["id"], 2))
    their_draft = ok(create(client, other, student, template), CREATED)

    def ids(caller: Any, **params: Any) -> list[str]:
        return [
            item["id"]
            for item in ok(get(client, caller, working_path(student), **params))[
                "items"
            ]
        ]

    # Newest first.
    assert ids(staff) == [voided["id"], linked["id"]]
    assert ids(cookie) == [voided["id"], linked["id"], mine_draft["id"]]
    assert ids(other) == [their_draft["id"], linked["id"]]
    assert ids(staff, status="voided") == [voided["id"]]
    assert ids(staff, status="final") == [linked["id"]]
    assert ids(cookie, status="draft") == [mine_draft["id"]]
    assert ids(staff, subject_id=str(subject)) == [linked["id"]]
    assert ids(cookie, class_session_id=session_id) == [linked["id"]]
    [item] = ok(
        get(client, cookie, working_path(student), class_session_id=session_id)
    )["items"]
    assert item["class_session"]["id"] == session_id
    # The cursor.
    first = ok(get(client, cookie, working_path(student), limit=2))
    assert [i["id"] for i in first["items"]] == [voided["id"], linked["id"]]
    rest = ok(
        get(client, cookie, working_path(student), limit=2, cursor=first["next_cursor"])
    )
    assert [i["id"] for i in rest["items"]] == [mine_draft["id"]]
    assert rest["next_cursor"] is None
    assert code_of(get(client, cookie, working_path(student), cursor="nope"))[0] == 400


# --- F: the history -------------------------------------------------------------------


def _period(url: str, name: str, period_type: str, starts: str, ends: str) -> str:
    [row] = run_sql(
        url,
        "INSERT INTO academic_periods (name, period_type, starts_on, ends_on)"
        " VALUES (:n, :t, :s, :e) RETURNING id",
        {
            "n": f"{name} {uuid.uuid4().hex[:6]}",
            "t": period_type,
            "s": date.fromisoformat(starts),
            "e": date.fromisoformat(ends),
        },
    )
    return str(row.id)


def test_the_history_rolls_up_every_tutor_by_period_and_subject(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    year = _period(url, "Ciclo", "school_year", "2010-08-20", "2011-07-10")
    fall = _period(url, "Semestre A", "semester", "2010-08-20", "2010-12-20")
    spring = _period(url, "Semestre B", "semester", "2011-01-10", "2011-07-10")
    term = _period(url, "Bimestre", "term", "2010-09-01", "2010-10-31")
    math, reading = new_subject(url), new_subject(url)
    on_math = new_template(client, staff, subject_id=math, criteria=(("A", "10"),))
    on_reading = new_template(client, staff, subject_id=reading, criteria=(("A", "4"),))
    _, first_tutor, (student,) = teaching(url)
    _, second_tutor, _ = teaching(url, students=(student,))

    def final(
        tutor: CreatedUser, template: dict[str, Any], day: str, score: str
    ) -> str:
        cookie = sign_in(client, tutor.username)
        view = ok(
            create(
                client,
                cookie,
                student,
                template,
                evaluated_on=day,
                scores=[{"position": 1, "score": score}],
                feedback=f"Retro {day}",
            ),
            CREATED,
        )
        return str(ok(finalize(client, cookie, view["id"], 1))["id"])

    a = final(first_tutor, on_math, "2010-09-15", "10")  # year, fall, term
    b = final(second_tutor, on_math, "2010-11-15", "5")  # year, fall
    c = final(first_tutor, on_reading, "2011-02-01", "1")  # year, spring
    d = final(second_tutor, on_math, "2012-01-01", "8")  # no period
    cookie = sign_in(client, first_tutor.username)

    history = ok(get(client, cookie, history_path(student)))
    assert history["group_by"] == "school_year" and history["truncated"] is False
    assert [e["id"] for e in history["entries"]] == [d, c, b, a]
    entry = history["entries"][3]
    assert entry == {
        "kind": "evaluation",
        "id": a,
        "evaluated_on": "2010-09-15",
        "subject": entry["subject"],
        "evaluation_kind": "ongoing",
        "template_name": on_math["name"],
        "author": {"display_name": "Tutora Evalúa"},
        "criteria": [
            {"title": "A", "max_score": "10.00", "score": "10.00", "comment": None}
        ],
        "score_total": "10.00",
        "max_total": "10.00",
        "score_percent": "100.00",
        "feedback": "Retro 2010-09-15",
        "course": None,
        "finalized_at": NOW.isoformat().replace("+00:00", "Z"),
        "updated": False,
    }
    groups = history["groups"]
    assert [g["period"] and g["period"]["id"] for g in groups] == [year, None]
    assert groups[0]["period"]["period_type"] == "school_year"
    assert entry["subject"]["id"] == str(math) and entry["subject"]["name"]
    # Subjects by name ignoring case, then id.
    subjects = groups[0]["subjects"]
    assert subjects == sorted(
        subjects, key=lambda s: (s["subject"]["name"].casefold(), s["subject"]["id"])
    )
    assert {
        s["subject"]["id"]: (s["count"], s["average_percent"], s["entry_ids"])
        for s in subjects
    } == {str(reading): (1, "25.00", [c]), str(math): (2, "75.00", [b, a])}
    assert groups[1]["subjects"][0]["entry_ids"] == [d]

    def periods(**params: Any) -> list[tuple[str | None, set[str]]]:
        """The groups in order, each with its entry ids (any subject)."""
        found = ok(get(client, cookie, history_path(student), **params))["groups"]
        return [
            (
                g["period"] and g["period"]["id"],
                {i for s in g["subjects"] for i in s["entry_ids"]},
            )
            for g in found
        ]

    assert periods(group_by="semester") == [
        (spring, {c}),
        (fall, {b, a}),
        (None, {d}),
    ]
    assert periods(group_by="term") == [(term, {a}), (None, {c, d, b})]
    assert periods(group_by="none") == [(None, {c, d, b, a})]
    # Filters.
    assert periods(group_by="none", subject_id=str(reading)) == [(None, {c})]
    assert periods(group_by="none", **{"from": "2010-11-01", "to": "2011-12-31"}) == [
        (None, {c, b})
    ]
    response = get(
        client,
        cookie,
        history_path(student),
        **{"from": "2011-01-02", "to": "2011-01-01"},
    )
    assert response.json()["errors"][0]["code"] == "range_invalid"
    # A deactivated period no longer groups anything.
    run_sql(
        url,
        "UPDATE academic_periods SET is_active = false WHERE id = :id",
        {"id": term},
    )
    assert periods(group_by="term") == [(None, {d, c, b, a})]


def test_the_history_cap(module_seeded_url, client, staff):
    url = module_seeded_url
    tutor, cookie, student, view = written(client, url, staff, final=True)
    run_sql(
        url,
        "INSERT INTO evaluations (student_id, subject_id, author_user_id,"
        " template_id, template_version, template_name, kind, evaluated_on,"
        " status, score_total, max_total, score_percent, finalized_at)"
        " SELECT student_id, subject_id, author_user_id, template_id, 1,"
        " template_name, kind, DATE '2020-01-01' + g, 'final', 1, 2, 50, now()"
        " FROM evaluations, generate_series(1, 500) AS g WHERE id = :id",
        {"id": view["id"]},
    )

    history = ok(get(client, cookie, history_path(student), group_by="none"))
    assert history["truncated"] is True
    assert len(history["entries"]) == 500
    # The newest are kept: the original evaluation (today) is first.
    assert history["entries"][0]["id"] == view["id"]
    assert history["groups"][0]["subjects"][0]["count"] == 500
    assert tutor


def test_the_history_access(module_seeded_url, client, staff, owner):
    url = module_seeded_url
    minor = guardian_with_child(url)
    tutor, cookie, student, view = written(
        client, url, staff, family=minor, final=True, feedback="Visible"
    )
    unknown = uuid.uuid4()

    def entries(caller: Any, who: Any = student) -> list[str]:
        return [e["id"] for e in ok(get(client, caller, history_path(who)))["entries"]]

    # Staff with evaluation.manage, the tutor, the minor's guardian.
    assert entries(staff) == [view["id"]]
    assert entries(sign_in(client, owner.username)) == [view["id"]]
    assert entries(cookie) == [view["id"]]
    assert entries(sign_in(client, minor.user.username)) == [view["id"]]
    # The student themself.
    me, mine = student_account(url)
    _, mine_tutor_cookie, _, own = written(client, url, staff, final=True)
    _, tutor_of_me, _ = teaching(url, students=(mine,))
    template = new_template(client, staff)
    tc = sign_in(client, tutor_of_me.username)
    my_view = ok(
        create(client, tc, mine, template, scores=full_scores(template)), CREATED
    )
    ok(finalize(client, tc, my_view["id"], 1))
    assert entries(sign_in(client, me.username), mine) == [my_view["id"]]
    # Anyone else: the same 404 as an unknown id.
    for caller in (
        sign_in(client, me.username),
        sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username),
        sign_in(client, create_user(url, roles=(GUARDIAN,)).username),
        sign_in(client, a_tutor(url).username),
        mine_tutor_cookie,
    ):
        assert_same_not_found(
            get(client, caller, history_path(student)),
            get(client, caller, history_path(unknown)),
        )
    assert own


def test_an_adult_needs_the_consent_and_a_revocation_applies_at_once(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    family = guardian_with_child(url, adult=True)
    _, _, student, view = written(client, url, staff, family=family, final=True)
    guardian = sign_in(client, family.user.username)
    # Not notified without the consent.
    assert notifications(url, FINALIZED_TYPE, evaluation_id=view["id"]) == []

    assert code_of(get(client, guardian, history_path(student))) == CONSENT
    give_academic_consent(url, family)
    history = ok(get(client, guardian, history_path(student)))
    assert [e["id"] for e in history["entries"]] == [view["id"]]
    revoke_academic_consent(url, family)
    assert code_of(get(client, guardian, history_path(student))) == CONSENT


def test_the_recipients_and_params(module_seeded_url, client, staff):
    """B3: the student's login and the guardians with academic access; the
    params carry ids and the date only."""
    url = module_seeded_url
    me, mine = student_account(url)
    _, tutor, _ = teaching(url, students=(mine,))
    template = new_template(client, staff)
    cookie = sign_in(client, tutor.username)
    view = ok(
        create(
            client,
            cookie,
            mine,
            template,
            scores=full_scores(template),
            feedback="Nunca en la notificación",
        ),
        CREATED,
    )
    ok(finalize(client, cookie, view["id"], 1))

    [sent] = notifications(url, FINALIZED_TYPE, evaluation_id=view["id"])
    assert sent.recipient_user_id == me.id
    assert set(sent.params) == {
        "student_id",
        "evaluation_id",
        "subject_id",
        "evaluated_on",
    }
    assert "Nunca" not in str(sent.params)


# --- I3: IDOR -------------------------------------------------------------------------


def test_the_hidden_evaluations_get_the_same_404(module_seeded_url, client, staff):
    url = module_seeded_url
    tutor, cookie, student, draft = written(client, url, staff)
    final = ok(
        create(
            client,
            cookie,
            student,
            new_template(client, staff, criteria=(("A", "1"),)),
            scores=[{"position": 1, "score": "1"}],
        ),
        CREATED,
    )
    final = ok(finalize(client, cookie, final["id"], 1))
    _, colleague, _ = teaching(url, students=(student,))
    lost = create_user(url, roles=(TUTOR, GUARDIAN))
    teaching(url, tutor=lost, students=(student,))
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": lost.id},
    )
    other_student, _ = student_account(url)
    unknown = uuid.uuid4()

    def same(caller: Any, evaluation_id: Any) -> None:
        assert_same_not_found(
            get(client, caller, evaluation_path(evaluation_id)),
            get(client, caller, evaluation_path(unknown)),
        )
        assert_same_not_found(
            edit(client, caller, evaluation_id, {"version": 1, "feedback": "x"}),
            edit(client, caller, unknown, {"version": 1, "feedback": "x"}),
        )
        assert_same_not_found(
            finalize(client, caller, evaluation_id, 1),
            finalize(client, caller, unknown, 1),
        )
        assert_same_not_found(
            void(client, caller, evaluation_id, 2), void(client, caller, unknown, 2)
        )
        assert_same_not_found(
            delete(client, caller, evaluation_id, 1), delete(client, caller, unknown, 1)
        )

    outsiders = [
        sign_in(client, a_tutor(url).username),
        sign_in(client, lost.username),
        sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username),
        sign_in(client, create_user(url, roles=(GUARDIAN,)).username),
        sign_in(client, other_student.username),
    ]
    for caller in outsiders:
        same(caller, draft["id"])
        same(caller, final["id"])
        for path in (working_path, history_path):
            assert_same_not_found(
                get(client, caller, path(student)), get(client, caller, path(unknown))
            )
    # Staff and another tutor teaching the student never see a draft.
    same(staff, draft["id"])
    same(sign_in(client, colleague.username), draft["id"])
    assert tutor


def test_the_original_tutor_after_a_real_substitution(
    module_seeded_url, client, owner, staff, at_now
):
    """#55: once the substitute accepted, the original tutor (the student
    not otherwise assigned) is a former author: their evaluation is
    read-only, the class link and the context are gone; the substitute
    records on the class."""
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
    template = new_template(client, staff, subject_id=subject)
    own = ok(create(client, original, setup.student, template), CREATED)
    detail = get(client, boss, f"/api/v1/class-sessions/{created['id']}").json()
    item = substituted(client, boss, detail, substitute.id)
    taker = sign_in(client, substitute.username)
    assert code_of(answer(client, taker, item["offer_id"], "accept")) == OK
    at_now.set(starts_at + timedelta(minutes=5))

    original = sign_in(client, setup.tutor.username)
    assert code_of(edit(client, original, own["id"], {"version": 1})) == READ_ONLY
    assert ok(get(client, original, evaluation_path(own["id"])))["is_own"] is True
    assert_same_not_found(
        get(client, original, context_path(created["id"])),
        get(client, original, context_path(uuid.uuid4())),
    )
    assert code_of(create(client, original, setup.student, template)) == NOT_FOUND
    taker = sign_in(client, substitute.username)
    context = ok(get(client, taker, context_path(created["id"])))
    assert context["evaluable"] is True
    linked = ok(
        create(client, taker, setup.student, template, class_session_id=created["id"]),
        CREATED,
    )
    assert linked["class_session"]["id"] == created["id"]
    # The substitute doesn't read the original's draft.
    assert code_of(get(client, taker, evaluation_path(own["id"]))) == NOT_FOUND


# --- AM1: the evaluation context ------------------------------------------------------


def test_the_evaluation_context(module_seeded_url, client, staff, owner):
    url = module_seeded_url
    subject = new_subject(url)
    a, b = new_student(url), new_student(url)
    session_id, tutor, _ = teaching(
        url, students=(a, b), kind="group", subject_id=subject
    )
    cookie = sign_in(client, tutor.username)

    context = ok(get(client, cookie, context_path(session_id)))
    assert set(context) == {
        "class_session_id",
        "kind",
        "state",
        "starts_at",
        "subject",
        "evaluable",
        "participants",
        "course",
    }
    assert (context["kind"], context["state"], context["evaluable"]) == (
        "group",
        "confirmed",
        True,
    )
    assert context["subject"]["id"] == str(subject)
    assert {p["student_id"] for p in context["participants"]} == {str(a), str(b)}
    assert context["course"] is None
    future, _, _ = teaching(url, tutor=tutor, starts_at=TOMORROW)
    assert ok(get(client, cookie, context_path(future)))["evaluable"] is False
    # Anyone else: the same 404 as an unknown id.
    family = guardian_with_child(url)
    for caller in (
        staff,
        sign_in(client, owner.username),
        sign_in(client, a_tutor(url).username),
        sign_in(client, family.user.username),
    ):
        assert_same_not_found(
            get(client, caller, context_path(session_id)),
            get(client, caller, context_path(uuid.uuid4())),
        )

    # A course class lists the snapshot's assessments.
    kept = str(uuid.uuid4())

    async def _cohort(session: AsyncSession) -> uuid.UUID:
        cohort = await persist_course_cohort(
            session,
            subject_id=subject,
            assessments=[{"id": kept, "title": "Final", "min_score_percent": 60}],
        )
        return cohort.id

    cohort_id = in_uow(url, _cohort)
    course_session, _, _ = teaching(
        url,
        tutor=tutor,
        students=(a,),
        kind="course",
        cohort_id=cohort_id,
        subject_id=subject,
        starts_at=NOW - timedelta(hours=3),
    )
    course = ok(get(client, cookie, context_path(course_session)))["course"]
    assert course["cohort_id"] == str(cohort_id)
    assert course["assessments"] == [
        {"id": kept, "title": "Final", "is_required": True, "min_score_percent": 60}
    ]
    statements, _ = count_statements(
        url,
        lambda s: workflow.evaluation_context(
            s, principal_of(tutor, TUTOR), uuid.UUID(course_session)
        ),
    )
    assert statements <= 4, statements


# --- Budgets (E2, F2) -----------------------------------------------------------------


def _many(
    url: str, client: TestClient, staff: Any, count: int
) -> tuple[CreatedUser, Family]:
    family = guardian_with_child(url, adult=True, consent=True)
    _, tutor, _ = teaching(url, students=(family.student_id,))
    cookie = sign_in(client, tutor.username)
    template = new_template(client, staff)
    for index in range(count):
        view = ok(
            create(
                client,
                cookie,
                family.student_id,
                template,
                scores=full_scores(template),
                evaluated_on=(TODAY - timedelta(days=index)).isoformat(),
            ),
            CREATED,
        )
        ok(finalize(client, cookie, view["id"], 1))
    return tutor, family


@pytest.mark.parametrize("count", [1, 20])
def test_the_statement_budgets(module_seeded_url, client, staff, coordinator, count):
    url = module_seeded_url
    day = (date(2031, 1, 1) + timedelta(days=count)).isoformat()
    _period(url, "Presupuesto", "school_year", day, day)
    tutor, family = _many(url, client, staff, count)
    student = family.student_id
    callers = [
        principal_of(tutor, TUTOR),
        principal_of(coordinator, ACADEMIC_COORDINATOR),
    ]
    for principal in callers:
        statements, page = count_statements(
            url,
            lambda s, p=principal: workflow.list_student_evaluations(
                s,
                p,
                student,
                status=None,
                subject_id=None,
                class_session_id=None,
                cursor=None,
                limit=50,
            ),
        )
        assert statements <= 8, statements
        assert len(page.items) == count
    for principal in [*callers, principal_of(family.user, GUARDIAN)]:
        statements, history = count_statements(
            url,
            lambda s, p=principal: workflow.academic_history(
                s,
                p,
                student,
                group_by="school_year",
                subject_id=None,
                from_=None,
                to=None,
            ),
        )
        assert statements <= 8, statements
        assert len(history.entries) == count
