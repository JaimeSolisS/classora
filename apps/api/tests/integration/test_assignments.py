"""Homework assignments, submissions and grading (#82 C, D, E, F, G, J2,
J3, AM1) with real logins through the real app.

The lifecycle (create → student submit → tutor-recorded handed-in work →
grade → re-grade) with audit rows and notifications; every C2 and C3 order
step; the ``PATCH`` rules (AM1 ``null``, recipients, locked ``max_score``);
delete; the submit rules (deadline lock, late, missing → late, empty, the
URL rule, versions, guardians); grading (scored and feedback-only, ranges,
precision, re-grade reasons); late / missing under a frozen clock (deadline
extension, end of day); the per-student list (state filter, summary,
cursor) and the working list; the former-creator rule; staff and Finance;
the consent rule; the IDOR cases (the same ``404`` as an unknown id); a real
substitution; the notification params; the history entries; the statement
budgets. The family leak walk over every ``GET`` route is
``test_session_notes_leak.py``; the D3 races are
``test_assignments_concurrency.py``.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.workflows import assignments as workflow
from app.workflows import evaluations as evaluation_workflow
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
from tests.integration.lifecycle_support import code_of, events
from tests.integration.schedule_support import principal_of, stored
from tests.integration.session_request_support import (
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

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)  # 12:00 in America/Mexico_City
TODAY = date(2031, 3, 5)
STARTED = NOW - timedelta(minutes=30)
DUE = "2031-03-12"

OK = (200, None)
CREATED = (201, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
VERSION_CONFLICT = (409, "version_conflict")
TUTOR_ONLY = (403, "assignment_tutor_only")
CREATOR_ONLY = (403, "assignment_creator_only")
READ_ONLY = (403, "assignment_read_only")
STUDENT_ONLY = (403, "submission_student_only")
CONSENT = (403, "student_consent_required")
DENIED = (403, "permission_denied")
GRADED = (409, "submission_graded")
LOCKED = (409, "submission_locked")
REQUIRED = (409, "submission_required")
HAS_SUBMISSIONS = (409, "assignment_has_submissions")
HAS_GRADES = (409, "assignment_has_grades")
RECIPIENT_SUBMITTED = (409, "recipient_has_submission")
NOT_ASSIGNABLE = (409, "session_not_assignable")

SET_TYPE = "academics.assignment_set"
SUBMITTED_TYPE = "academics.assignment_submitted"
GRADED_TYPE = "academics.assignment_graded"

SUMMARY_KEYS = {
    "id",
    "version",
    "subject",
    "title",
    "instructions",
    "due_on",
    "due_time",
    "deadline_at",
    "max_score",
    "class_session",
    "creator",
    "is_own",
    "created_at",
    "updated_at",
    "counts",
    "can_edit",
    "can_delete",
}
VIEW_KEYS = SUMMARY_KEYS | {"roster"}
COUNT_KEYS = {"recipients", "pending", "missing", "submitted", "late", "graded"}
ROSTER_KEYS = {
    "student",
    "version",
    "state",
    "is_late",
    "submitted_at",
    "submitted_via",
    "text",
    "url",
    "score",
    "score_percent",
    "feedback",
    "graded_at",
    "grade_amended_at",
    "last_change_reason",
    "can_record_submission",
    "can_grade",
}
STUDENT_KEYS = {
    "id",
    "subject",
    "title",
    "instructions",
    "due_on",
    "due_time",
    "deadline_at",
    "max_score",
    "creator",
    "class_session",
    "submission",
    "can_submit",
    "can_grade",
}
SUBMISSION_KEYS = {
    "state",
    "is_late",
    "submitted_at",
    "submitted_via",
    "text",
    "url",
    "score",
    "score_percent",
    "feedback",
    "graded_at",
    "updated",
    "version",
}
GRADE_ENTRY_KEYS = {
    "kind",
    "id",
    "graded_on",
    "subject",
    "title",
    "author",
    "max_score",
    "score",
    "score_percent",
    "feedback",
    "due_on",
    "is_late",
    "graded_at",
    "updated",
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
    return create_user(module_seeded_url, roles=(OWNER,), display_name="Dueña Tareas")


@pytest.fixture(scope="module")
def coordinator(module_seeded_url: str) -> CreatedUser:
    return create_user(
        module_seeded_url,
        roles=(ACADEMIC_COORDINATOR,),
        display_name="Coordinadora Tareas",
    )


@pytest.fixture(scope="module")
def staff(client: TestClient, coordinator: CreatedUser) -> Any:
    """The coordinator's cookie (assignment.review)."""
    return sign_in(client, coordinator.username)


# --- Helpers --------------------------------------------------------------------------

ASSIGNMENTS = "/api/v1/assignments"


def a_path(assignment_id: Any, suffix: str = "") -> str:
    return f"{ASSIGNMENTS}/{assignment_id}{suffix}"


def row_path(assignment_id: Any, student_id: Any, what: str) -> str:
    return f"{ASSIGNMENTS}/{assignment_id}/students/{student_id}/{what}"


def student_path(student_id: Any, assignment_id: Any = None) -> str:
    base = f"/api/v1/students/{student_id}/assignments"
    return base if assignment_id is None else f"{base}/{assignment_id}"


def history_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/academic-history"


def ok(response: Response, expected: tuple[int, None] = OK) -> Any:
    assert code_of(response) == expected, response.text
    return response.json()


def field_codes(response: Response) -> list[tuple[list[Any], str]]:
    assert code_of(response) == INVALID, response.text
    return [(item["path"], item["code"]) for item in response.json()["errors"]]


def a_tutor(url: str, name: str = "Tutora Tareas") -> CreatedUser:
    return create_user(url, roles=(TUTOR,), display_name=name)


def teaching(
    url: str,
    *,
    tutor: CreatedUser | None = None,
    students: tuple[uuid.UUID, ...] | None = None,
    subject: uuid.UUID | None = None,
    state: str = "confirmed",
    starts_at: datetime = STARTED,
) -> tuple[CreatedUser, tuple[uuid.UUID, ...], uuid.UUID, str]:
    """A stored session of ``tutor`` (new by default) with ``students`` (one
    new student by default) in ``subject`` (new by default): ``(tutor,
    students, subject, session id)``."""
    tutor = tutor or a_tutor(url)
    students = students or (new_student(url),)
    subject = subject or new_subject(url)
    session_id = stored(
        url,
        starts_at,
        state=state,
        students=students,
        tutor_user_id=tutor.id,
        subject_id=subject,
        **({"kind": "group"} if len(students) > 1 else {}),
    )
    return tutor, students, subject, session_id


def body(subject: Any, students: Any = None, **values: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "subject_id": str(subject),
        "title": "Lectura",
        "instructions": "Leer el capítulo 3 y resumirlo.",
        "due_on": DUE,
        **values,
    }
    if students is not None:
        found["student_ids"] = [str(s) for s in students]
    return found


def create(client: TestClient, cookie: Any, data: dict[str, Any]) -> Response:
    return post(client, cookie, ASSIGNMENTS, data)


def submit(
    client: TestClient, cookie: Any, assignment_id: Any, student_id: Any, **data: Any
) -> Response:
    return put(client, cookie, row_path(assignment_id, student_id, "submission"), data)


def grade(
    client: TestClient, cookie: Any, assignment_id: Any, student_id: Any, **data: Any
) -> Response:
    return put(client, cookie, row_path(assignment_id, student_id, "grade"), data)


def delete(
    client: TestClient, cookie: Any, assignment_id: Any, version: int
) -> Response:
    client.cookies.clear()
    return client.delete(
        a_path(assignment_id),
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


def roster_row(view: dict[str, Any], student_id: Any) -> dict[str, Any]:
    [found] = [r for r in view["roster"] if r["student"]["id"] == str(student_id)]
    return found


def homework(
    client: TestClient,
    url: str,
    *,
    students: tuple[uuid.UUID, ...] | None = None,
    tutor: CreatedUser | None = None,
    starts_at: datetime = STARTED,
    **values: Any,
) -> tuple[CreatedUser, Any, tuple[uuid.UUID, ...], uuid.UUID, dict[str, Any]]:
    """Homework by a tutor teaching ``students`` (one new student by default)
    in a new subject: ``(tutor, tutor cookie, students, subject, view)``."""
    tutor, students, subject, _ = teaching(
        url, tutor=tutor, students=students, starts_at=starts_at
    )
    cookie = sign_in(client, tutor.username)
    view = ok(create(client, cookie, body(subject, students, **values)), CREATED)
    return tutor, cookie, students, subject, view


# --- The lifecycle --------------------------------------------------------------------


def test_the_lifecycle(module_seeded_url, client, staff):
    url = module_seeded_url
    family = guardian_with_child(url)
    me, mine = student_account(url, display_name="Ana Alumna")
    tutor, students, subject, _ = teaching(url, students=(mine, family.student_id))
    cookie = sign_in(client, tutor.username)
    pupil = sign_in(client, me.username)
    guardian = sign_in(client, family.user.username)

    created = ok(
        create(
            client,
            cookie,
            body(
                subject,
                students,
                title="  Ensayo  ",
                max_score="10",
                due_on="2031-03-06",
                due_time="16:00",
            ),
        ),
        CREATED,
    )
    assert set(created) == VIEW_KEYS
    assert set(created["counts"]) == COUNT_KEYS
    assert set(created["roster"][0]) == ROSTER_KEYS
    assert (created["title"], created["version"], created["max_score"]) == (
        "Ensayo",
        1,
        "10.00",
    )
    assert (created["due_on"], created["due_time"]) == ("2031-03-06", "16:00:00")
    assert created["deadline_at"] == "2031-03-06T22:00:00Z"
    assert created["creator"] == {"display_name": "Tutora Tareas"}
    assert created["is_own"] is True
    assert created["class_session"] is None
    assert (created["can_edit"], created["can_delete"]) == (True, True)
    assert created["counts"] == {
        "recipients": 2,
        "pending": 2,
        "missing": 0,
        "submitted": 0,
        "late": 0,
        "graded": 0,
    }
    names = [r["student"]["display_name"] for r in created["roster"]]
    assert names == sorted(names, key=str.casefold)
    assignment_id = created["id"]
    # E8: one assignment_set per student, to the login and the guardian.
    assert {
        (row.recipient_user_id, row.params["student_id"])
        for row in notifications(url, SET_TYPE, assignment_id=assignment_id)
    } == {(me.id, str(mine)), (family.user.id, str(family.student_id))}
    for row in notifications(url, SET_TYPE, assignment_id=assignment_id):
        assert set(row.params) == {
            "student_id",
            "assignment_id",
            "subject_id",
            "due_on",
        }
        assert row.params["due_on"] == "2031-03-06"
        assert "Ensayo" not in str(row.params)

    # The student sees their homework, pending, and may submit.
    listed = ok(get(client, pupil, student_path(mine)))
    assert listed["summary"] == {
        "pending": 1,
        "missing": 0,
        "submitted": 0,
        "graded": 0,
    }
    [item] = listed["items"]
    assert set(item) == STUDENT_KEYS
    assert set(item["submission"]) == SUBMISSION_KEYS
    assert (item["can_submit"], item["can_grade"]) == (True, False)
    assert item["submission"]["state"] == "pending"

    # The student submits text and a link: the creator is notified.
    submitted = ok(
        submit(
            client,
            pupil,
            assignment_id,
            mine,
            text="  Mi ensayo  ",
            url="https://docs.example.com/ensayo",
        )
    )
    assert set(submitted) == STUDENT_KEYS
    sub = submitted["submission"]
    assert (sub["state"], sub["text"], sub["submitted_via"], sub["is_late"]) == (
        "submitted",
        "Mi ensayo",
        "student",
        False,
    )
    assert sub["version"] == 2
    [told] = notifications(url, SUBMITTED_TYPE, assignment_id=assignment_id)
    assert told.recipient_user_id == tutor.id
    assert told.params == {"student_id": str(mine), "assignment_id": assignment_id}

    # The tutor records the minor's work handed in on paper: nobody is told.
    recorded = ok(submit(client, cookie, assignment_id, family.student_id))
    assert recorded["submission"]["submitted_via"] == "tutor"
    assert recorded["submission"]["text"] is None
    assert len(notifications(url, SUBMITTED_TYPE, assignment_id=assignment_id)) == 1

    # Grading: visible and notified at once.
    graded = ok(
        grade(
            client, cookie, assignment_id, mine, version=2, score="7.5", feedback="Bien"
        )
    )
    assert set(graded) == ROSTER_KEYS
    assert (graded["score"], graded["score_percent"], graded["state"]) == (
        "7.50",
        "75.00",
        "graded",
    )
    assert graded["last_change_reason"] is None
    ok(grade(client, cookie, assignment_id, family.student_id, version=2, score="10"))
    assert {
        (row.recipient_user_id, row.params["student_id"])
        for row in notifications(url, GRADED_TYPE, assignment_id=assignment_id)
    } == {(me.id, str(mine)), (family.user.id, str(family.student_id))}
    for row in notifications(url, GRADED_TYPE, assignment_id=assignment_id):
        assert set(row.params) == {"student_id", "assignment_id", "subject_id"}

    # A re-grade needs a reason (internal), notifies nobody.
    assert field_codes(
        grade(client, cookie, assignment_id, mine, version=3, score="8")
    ) == [(["body", "change_reason"], "change_reason_required")]
    regraded = ok(
        grade(
            client,
            cookie,
            assignment_id,
            mine,
            version=3,
            score="8",
            feedback="Mejor",
            change_reason="Revisé el segundo párrafo",
        )
    )
    assert regraded["last_change_reason"] == "Revisé el segundo párrafo"
    assert regraded["grade_amended_at"] is not None
    assert len(notifications(url, GRADED_TYPE, assignment_id=assignment_id)) == 2

    # The student and the guardian read the grade (never the reason).
    own = ok(get(client, pupil, student_path(mine, assignment_id)))
    assert (own["submission"]["score"], own["submission"]["updated"]) == ("8.00", True)
    assert own["can_submit"] is False
    assert "Revisé" not in str(own)
    child = ok(get(client, guardian, student_path(family.student_id)))
    assert child["summary"] == {"pending": 0, "missing": 0, "submitted": 0, "graded": 1}
    assert child["items"][0]["submission"]["score_percent"] == "100.00"
    assert "Mi ensayo" not in str(child)
    # The tutor's roster and the coordinator's view show both.
    view = ok(get(client, staff, a_path(assignment_id)))
    assert view["counts"]["graded"] == 2
    assert (view["can_edit"], view["can_delete"], view["is_own"]) == (
        False,
        False,
        False,
    )
    assert roster_row(view, mine)["can_grade"] is False

    # The audit keeps the prior and new grade values.
    [row_id] = [
        r.id
        for r in run_sql(
            url,
            "SELECT id FROM assignment_submissions WHERE assignment_id = :a"
            " AND student_id = :s",
            {"a": assignment_id, "s": mine},
        )
    ]
    trail = events(url, record_type="assignment_submissions", record_id=row_id)
    assert [e.action for e in trail] == ["create", "update", "update", "update"]
    assert trail[-1].changes["score"] == {"old": "7.50", "new": "8.00"}
    # The history gains a scored entry for each.
    history = ok(get(client, pupil, history_path(mine)))
    [entry] = history["entries"]
    assert set(entry) == GRADE_ENTRY_KEYS
    assert (entry["kind"], entry["score"], entry["updated"]) == (
        "assignment_grade",
        "8.00",
        True,
    )
    assert entry["graded_on"] == TODAY.isoformat()


# --- C2: the create order -------------------------------------------------------------


def test_the_create_order(module_seeded_url, client, staff, at_now):
    url = module_seeded_url
    tutor, (student,), subject, session_id = teaching(url)
    cookie = sign_in(client, tutor.username)
    good = body(subject, [student])

    # 401
    client.cookies.clear()
    assert client.post(ASSIGNMENTS, json=good).status_code == 401
    # 2: the schema (lengths, max_score, duplicates, > 100).
    for changes, path, code in (
        ({"title": "  "}, "title", "title_required"),
        ({"title": "x" * 201}, "title", "too_long"),
        ({"instructions": ""}, "instructions", "instructions_required"),
        ({"instructions": "x" * 4001}, "instructions", "too_long"),
        ({"max_score": "0.4"}, "max_score", "max_score_out_of_range"),
        ({"max_score": "1000.01"}, "max_score", "max_score_out_of_range"),
        ({"max_score": "1.005"}, "max_score", "score_precision"),
        ({"student_ids": [str(student)] * 2}, "student_ids", "duplicate_recipient"),
        (
            {"student_ids": [str(uuid.uuid4()) for _ in range(101)]},
            "student_ids",
            "recipients_too_many",
        ),
        ({"due_time": "16:00+01:00"}, "due_time", "time_has_offset"),
    ):
        assert field_codes(create(client, cookie, good | changes)) == [
            (["body", path], code)
        ], changes
    assert code_of(create(client, cookie, good | {"extra": 1})) == INVALID
    # 3: only tutors (even with a valid body).
    for user in (
        create_user(url, roles=(ACADEMIC_COORDINATOR,)),
        create_user(url, roles=(FINANCE_STAFF,)),
        create_user(url, roles=(GUARDIAN,)),
    ):
        caller = sign_in(client, user.username)
        assert code_of(create(client, caller, good)) == TUTOR_ONLY
    assert code_of(create(client, staff, good)) == TUTOR_ONLY
    # 4: an active subject (unknown or inactive).
    assert field_codes(
        create(client, cookie, good | {"subject_id": str(uuid.uuid4())})
    )[0] == (["body", "subject_id"], "subject_unknown")
    inactive = new_subject(url)
    run_sql(
        url, "UPDATE subjects SET is_active = false WHERE id = :id", {"id": inactive}
    )
    assert field_codes(
        create(client, cookie, good | {"subject_id": str(inactive)})
    ) == [(["body", "subject_id"], "subject_unknown")]
    # 5: the session link.
    other_tutor, _, _, others = teaching(url, subject=subject)
    for link in (str(uuid.uuid4()), others):
        assert field_codes(
            create(client, cookie, good | {"class_session_id": link})
        ) == [(["body", "class_session_id"], "class_session_invalid")]
    for days, state in ((2, "offered"), (3, "cancelled")):
        _, _, _, wrong = teaching(
            url,
            tutor=tutor,
            students=(student,),
            subject=subject,
            state=state,
            starts_at=STARTED + timedelta(days=days),
        )
        assert (
            code_of(create(client, cookie, good | {"class_session_id": wrong}))
            == NOT_ASSIGNABLE
        )
    other_subject = new_subject(url)
    assert field_codes(
        create(
            client,
            cookie,
            good | {"class_session_id": session_id, "subject_id": str(other_subject)},
        )
    ) == [(["body", "subject_id"], "subject_mismatch")]
    # 6: the recipients.
    no_ids = {k: v for k, v in good.items() if k != "student_ids"}
    assert field_codes(create(client, cookie, no_ids)) == [
        (["body", "student_ids"], "recipients_required")
    ]
    assert field_codes(create(client, cookie, good | {"student_ids": []})) == [
        (["body", "student_ids"], "recipients_required")
    ]
    stranger = new_student(url)
    unknown = uuid.uuid4()
    response = create(
        client,
        cookie,
        good | {"student_ids": [str(student), str(stranger), str(unknown)]},
    )
    assert field_codes(response) == [(["body", "student_ids"], "recipient_invalid")]
    assert response.json()["details"] == {
        "student_ids": sorted([str(stranger), str(unknown)])
    }
    # With a session: its participants only; a current student who isn't one
    # is refused.
    _, (elsewhere,), _, _ = teaching(
        url, tutor=tutor, starts_at=STARTED + timedelta(days=1)
    )
    response = create(
        client,
        cookie,
        good | {"class_session_id": session_id, "student_ids": [str(elsewhere)]},
    )
    assert response.json()["details"] == {"student_ids": [str(elsewhere)]}
    # 7: the deadline.
    for due_on, due_time, code in (
        ("2031-03-04", None, "due_in_past"),
        ("2031-03-05", "11:59", "due_in_past"),
        ("2032-03-05", None, "due_too_far"),
    ):
        changes = {"due_on": due_on, **({"due_time": due_time} if due_time else {})}
        assert field_codes(create(client, cookie, good | changes)) == [
            (["body", "due_on"], code)
        ], changes
    for changes in (
        {"due_on": "2031-03-05"},
        {"due_on": "2031-03-05", "due_time": "12:30"},
        {"due_on": "2032-03-04"},
    ):
        ok(create(client, cookie, good | changes), CREATED)
    # The session's participants who are current students by default.
    linked = ok(
        create(client, cookie, no_ids | {"class_session_id": session_id}), CREATED
    )
    assert [r["student"]["id"] for r in linked["roster"]] == [str(student)]
    assert linked["class_session"]["id"] == session_id
    # A tutor who lost the student (30+ days) can't set homework for them.
    assert other_tutor


def test_default_recipients_skip_participants_who_are_not_current(
    module_seeded_url, client
):
    url = module_seeded_url
    a, b = new_student(url), new_student(url)
    tutor, _, subject, session_id = teaching(url, students=(a, b))
    cookie = sign_in(client, tutor.username)
    view = ok(
        create(
            client,
            cookie,
            {
                "subject_id": str(subject),
                "title": "Grupo",
                "instructions": "Todos",
                "due_on": DUE,
                "class_session_id": session_id,
            },
        ),
        CREATED,
    )
    assert {r["student"]["id"] for r in view["roster"]} == {str(a), str(b)}
    # A session whose participants are no longer current students: none.
    long_ago = STARTED - timedelta(days=40)
    old_tutor, _, old_subject, old_session = teaching(
        url, state="completed", starts_at=long_ago
    )
    old = sign_in(client, old_tutor.username)
    assert field_codes(
        create(
            client,
            old,
            {
                "subject_id": str(old_subject),
                "title": "Tarde",
                "instructions": "x",
                "due_on": DUE,
                "class_session_id": old_session,
            },
        )
    ) == [(["body", "student_ids"], "recipients_required")]


# --- C4: PATCH -----------------------------------------------------------------------


def test_the_patch_rules(module_seeded_url, client):
    url = module_seeded_url
    a, b = new_student(url), new_student(url)
    c_user, c = student_account(url)
    family = guardian_with_child(url)
    tutor, cookie, _, subject, view = homework(
        client, url, students=(a, b, c, family.student_id), max_score="10"
    )
    assignment_id = view["id"]
    # Students a, b, c and the family's child; recipients a, b, c, child.
    edited = ok(
        patch(client, cookie, a_path(assignment_id), {"version": 1, "title": "Nuevo"})
    )
    assert (edited["title"], edited["version"]) == ("Nuevo", 2)
    # Unchanged: 200, no write, no version bump.
    same = ok(
        patch(client, cookie, a_path(assignment_id), {"version": 2, "title": "Nuevo"})
    )
    assert same["version"] == 2
    assert (
        ok(patch(client, cookie, a_path(assignment_id), {"version": 2}))["version"] == 2
    )
    assert code_of(patch(client, cookie, a_path(assignment_id), {"version": 1})) == (
        VERSION_CONFLICT
    )
    # Immutable and null keys.
    for changes in (
        {"subject_id": str(subject)},
        {"class_session_id": str(uuid.uuid4())},
        {"title": None},
        {"instructions": None},
        {"due_on": None},
        {"add_student_ids": None},
        {"remove_student_ids": None},
        {"version": None},
    ):
        assert (
            code_of(
                patch(client, cookie, a_path(assignment_id), {"version": 2} | changes)
            )
            == INVALID
        ), changes
    # Remove one (no submission), add one back: added students are told.
    before = len(notifications(url, SET_TYPE, assignment_id=assignment_id))
    changed = ok(
        patch(
            client,
            cookie,
            a_path(assignment_id),
            {
                "version": 2,
                "remove_student_ids": [str(c)],
                "add_student_ids": [str(a)],  # already a recipient: ignored
            },
        )
    )
    assert changed["version"] == 3
    assert {r["student"]["id"] for r in changed["roster"]} == {
        str(a),
        str(b),
        str(family.student_id),
    }
    assert len(notifications(url, SET_TYPE, assignment_id=assignment_id)) == before
    readded = ok(
        patch(
            client,
            cookie,
            a_path(assignment_id),
            {"version": 3, "add_student_ids": [str(c)]},
        )
    )
    assert readded["counts"]["recipients"] == 4
    # Only the re-added student's login heard again (a and b have none).
    sent = notifications(url, SET_TYPE, assignment_id=assignment_id)
    assert sorted(str(row.recipient_user_id) for row in sent) == sorted(
        [str(c_user.id), str(c_user.id), str(family.user.id)]
    )
    # The family hears of a removal only through the 404 of the homework.
    ok(
        patch(
            client,
            cookie,
            a_path(assignment_id),
            {"version": 4, "remove_student_ids": [str(family.student_id)]},
        )
    )
    guardian = sign_in(client, family.user.username)
    assert_same_not_found(
        get(client, guardian, student_path(family.student_id, assignment_id)),
        get(client, guardian, student_path(family.student_id, uuid.uuid4())),
    )
    # Add an invalid student; a submitted recipient can't be removed.
    stranger = new_student(url)
    response = patch(
        client,
        cookie,
        a_path(assignment_id),
        {"version": 5, "add_student_ids": [str(stranger)]},
    )
    assert field_codes(response) == [(["body", "add_student_ids"], "recipient_invalid")]
    assert response.json()["details"] == {"student_ids": [str(stranger)]}
    ok(submit(client, cookie, assignment_id, a))
    response = patch(
        client,
        cookie,
        a_path(assignment_id),
        {"version": 5, "remove_student_ids": [str(a)]},
    )
    assert code_of(response) == RECIPIENT_SUBMITTED
    assert response.json()["details"] == {"student_ids": [str(a)]}
    # The deadline: not before today.
    assert field_codes(
        patch(
            client,
            cookie,
            a_path(assignment_id),
            {"version": 5, "due_on": "2031-03-04"},
        )
    ) == [(["body", "due_on"], "due_in_past")]
    assert field_codes(
        patch(
            client,
            cookie,
            a_path(assignment_id),
            {"version": 5, "due_on": "2032-03-05"},
        )
    ) == [(["body", "due_on"], "due_too_far")]
    # max_score is locked after a grade.
    ok(grade(client, cookie, assignment_id, a, version=2, score="9"))
    for changes in ({"max_score": "20"}, {"max_score": None}):
        assert (
            code_of(
                patch(client, cookie, a_path(assignment_id), {"version": 5} | changes)
            )
            == HAS_GRADES
        )
    # The same max_score is no change.
    assert (
        ok(
            patch(
                client,
                cookie,
                a_path(assignment_id),
                {"version": 5, "max_score": "10.00"},
            )
        )["version"]
        == 5
    )
    assert tutor


def test_removing_the_last_recipient(module_seeded_url, client):
    url = module_seeded_url
    _, cookie, (student,), _, view = homework(client, url)
    assert field_codes(
        patch(
            client,
            cookie,
            a_path(view["id"]),
            {"version": 1, "remove_student_ids": [str(student)]},
        )
    ) == [(["body", "remove_student_ids"], "recipients_required")]


def test_am1_null_clears_due_time_and_max_score(module_seeded_url, client, at_now):
    url = module_seeded_url
    me, mine = student_account(url)
    _, cookie, _, _, view = homework(
        client,
        url,
        students=(mine,),
        due_on="2031-03-05",
        due_time="13:00",
        max_score="10",
    )
    assignment_id = view["id"]
    assert view["deadline_at"] == "2031-03-05T19:00:00Z"
    pupil = sign_in(client, me.username)
    at_now.set(datetime(2031, 3, 5, 20, 0, tzinfo=UTC))  # 14:00 local
    late = ok(submit(client, pupil, assignment_id, mine, text="Tarde"))
    assert late["submission"]["is_late"] is True
    # due_time: null → the end of the day; the submission is no longer late.
    cleared = ok(
        patch(client, cookie, a_path(assignment_id), {"version": 1, "due_time": None})
    )
    assert (cleared["due_time"], cleared["deadline_at"]) == (
        None,
        "2031-03-06T05:59:59.999999Z",
    )
    assert roster_row(cleared, mine)["is_late"] is False
    # Omitting both keys leaves both values.
    kept = ok(
        patch(client, cookie, a_path(assignment_id), {"version": 2, "title": "X"})
    )
    assert (kept["due_time"], kept["max_score"]) == (None, "10.00")
    # max_score: null with no grades → feedback-only; a score is refused.
    feedback_only = ok(
        patch(client, cookie, a_path(assignment_id), {"version": 3, "max_score": None})
    )
    assert feedback_only["max_score"] is None
    # A null for an already-null field changes nothing.
    assert (
        ok(
            patch(
                client, cookie, a_path(assignment_id), {"version": 4, "max_score": None}
            )
        )["version"]
        == 4
    )
    assert field_codes(
        grade(client, cookie, assignment_id, mine, version=2, score="5", feedback="x")
    ) == [(["body", "score"], "score_not_allowed")]


# --- C5: DELETE ----------------------------------------------------------------------


def test_delete_rules(module_seeded_url, client, staff):
    url = module_seeded_url
    me, mine = student_account(url)
    _, cookie, _, _, view = homework(client, url, students=(mine,))
    keeper, _, _, _, kept = homework(client, url, students=(mine,))
    assert code_of(delete(client, cookie, view["id"], 2)) == VERSION_CONFLICT
    pupil = sign_in(client, me.username)
    # Kept homework with a submission can't be deleted.
    ok(submit(client, pupil, kept["id"], mine, text="Hecho"))
    tutor_of_kept = sign_in(client, keeper.username)
    assert code_of(delete(client, tutor_of_kept, kept["id"], 1)) == HAS_SUBMISSIONS
    response = delete(client, cookie, view["id"], 1)
    assert response.status_code == 204, response.text
    # Every route answers 404 afterwards; lists leave it out.
    unknown = uuid.uuid4()
    for caller in (cookie, staff):
        assert_same_not_found(
            get(client, caller, a_path(view["id"])),
            get(client, caller, a_path(unknown)),
        )
    assert_same_not_found(
        get(client, pupil, student_path(mine, view["id"])),
        get(client, pupil, student_path(mine, unknown)),
    )
    assert_same_not_found(
        submit(client, pupil, view["id"], mine, text="x"),
        submit(client, pupil, unknown, mine, text="x"),
    )
    assert_same_not_found(
        patch(client, cookie, a_path(view["id"]), {"version": 1, "title": "x"}),
        patch(client, cookie, a_path(unknown), {"version": 1, "title": "x"}),
    )
    assert [i["id"] for i in ok(get(client, pupil, student_path(mine)))["items"]] == [
        kept["id"]
    ]
    assert view["id"] not in {
        i["id"] for i in ok(get(client, cookie, ASSIGNMENTS))["items"]
    }
    assert [
        e.action
        for e in events(url, record_type="assignments", record_id=uuid.UUID(view["id"]))
    ] == ["create", "soft_delete"]


# --- D1: submissions -----------------------------------------------------------------


def test_the_student_submission_rules(module_seeded_url, client, at_now):
    url = module_seeded_url
    me, mine = student_account(url)
    sibling_family = guardian_with_child(url)
    tutor, cookie, _, _, view = homework(
        client,
        url,
        students=(mine, sibling_family.student_id),
        due_on="2031-03-05",
        due_time="13:00",
    )
    assignment_id = view["id"]
    pupil = sign_in(client, me.username)
    guardian = sign_in(client, sibling_family.user.username)
    # Empty, too long, the URL rule (each a 422 at the content step).
    assert field_codes(submit(client, pupil, assignment_id, mine, text="  ")) == [
        (["body"], "submission_empty")
    ]
    assert field_codes(
        submit(client, pupil, assignment_id, mine, text="x" * 10001)
    ) == [(["body", "text"], "too_long")]
    for link in (
        "javascript:alert(1)",
        "ftp://example.com/a",
        "/relative",
        "https://user:pw@example.com/",
        "https://example.com/" + "a" * 1990,
    ):
        assert field_codes(submit(client, pupil, assignment_id, mine, url=link)) == [
            (["body", "url"], "url_invalid")
        ], link
    # A guardian never submits; another student's row is the unknown-id 404.
    assert (
        code_of(
            submit(client, guardian, assignment_id, sibling_family.student_id, text="x")
        )
        == STUDENT_ONLY
    )
    assert_same_not_found(
        submit(client, pupil, assignment_id, sibling_family.student_id, text="x"),
        submit(client, pupil, uuid.uuid4(), mine, text="x"),
    )
    # First submission (no version needed), then a replacement needs it.
    first = ok(submit(client, pupil, assignment_id, mine, url="https://example.com/a"))
    assert first["submission"]["version"] == 2
    assert field_codes(submit(client, pupil, assignment_id, mine, text="otra")) == [
        (["body", "version"], "version_required")
    ]
    assert (
        code_of(submit(client, pupil, assignment_id, mine, version=1, text="otra"))
        == VERSION_CONFLICT
    )
    replaced = ok(submit(client, pupil, assignment_id, mine, version=2, text="otra"))
    assert (replaced["submission"]["text"], replaced["submission"]["url"]) == (
        "otra",
        None,
    )
    # After the deadline: the existing submission is locked.
    at_now.set(datetime(2031, 3, 5, 19, 0, 1, tzinfo=UTC))
    assert (
        code_of(submit(client, pupil, assignment_id, mine, version=3, text="tarde"))
        == LOCKED
    )
    item = ok(get(client, pupil, student_path(mine, assignment_id)))
    assert item["can_submit"] is False
    # The creator still records (no lock), and a missing row may be
    # submitted late by its student (here the tutor records the sibling's).
    missing = ok(get(client, guardian, student_path(sibling_family.student_id)))
    assert missing["items"][0]["submission"]["state"] == "missing"
    recorded = ok(submit(client, cookie, assignment_id, sibling_family.student_id))
    assert recorded["submission"]["is_late"] is True
    # Graded: nobody changes it.
    ok(grade(client, cookie, assignment_id, mine, version=3, feedback="Bien"))
    assert (
        code_of(submit(client, pupil, assignment_id, mine, version=4, text="x"))
        == GRADED
    )
    assert (
        code_of(submit(client, cookie, assignment_id, mine, version=4, text="x"))
        == GRADED
    )
    assert tutor


def test_a_missing_submission_can_be_submitted_late_by_the_student(
    module_seeded_url, client, at_now
):
    url = module_seeded_url
    me, mine = student_account(url)
    _, cookie, _, _, view = homework(
        client, url, students=(mine,), due_on="2031-03-05", due_time="13:00"
    )
    pupil = sign_in(client, me.username)
    at_now.set(datetime(2031, 3, 6, 18, 0, tzinfo=UTC))
    item = ok(get(client, pupil, student_path(mine, view["id"])))
    assert (item["submission"]["state"], item["can_submit"]) == ("missing", True)
    late = ok(submit(client, pupil, view["id"], mine, text="Perdón"))
    assert (late["submission"]["state"], late["submission"]["is_late"]) == (
        "submitted",
        True,
    )
    # A student replaces a tutor-recorded submission before the deadline.
    _, cookie2, _, _, other = homework(client, url, students=(mine,))
    recorded = ok(submit(client, cookie2, other["id"], mine, text="Papel"))
    replaced = ok(
        submit(
            client,
            pupil,
            other["id"],
            mine,
            version=recorded["submission"]["version"],
            text="Digital",
        )
    )
    assert replaced["submission"]["submitted_via"] == "student"
    assert cookie


# --- D2: grading ---------------------------------------------------------------------


def test_the_grading_rules(module_seeded_url, client):
    url = module_seeded_url
    me, mine = student_account(url)
    other = new_student(url)
    tutor, cookie, _, _, scored = homework(
        client, url, students=(mine, other), max_score="3"
    )
    _, _, _, _, feedback_only = homework(
        client,
        url,
        tutor=tutor,
        students=(mine,),
        starts_at=STARTED + timedelta(days=1),
    )
    pupil = sign_in(client, me.username)
    # Nothing submitted yet.
    assert code_of(grade(client, cookie, scored["id"], mine, version=1, score="1")) == (
        REQUIRED
    )
    ok(submit(client, pupil, scored["id"], mine, text="Respuesta"))
    ok(submit(client, pupil, feedback_only["id"], mine, text="Respuesta"))
    for data, path, code in (
        ({}, "score", "score_required"),
        ({"score": "-1"}, "score", "score_out_of_range"),
        ({"score": "3.01"}, "score", "score_out_of_range"),
        ({"score": "1", "feedback": "x" * 4001}, "feedback", "too_long"),
    ):
        assert field_codes(
            grade(client, cookie, scored["id"], mine, version=2, **data)
        ) == [(["body", path], code)], data
    assert field_codes(
        grade(client, cookie, scored["id"], mine, version=2, score="1.005")
    ) == [(["body", "score"], "score_precision")]
    assert code_of(grade(client, cookie, scored["id"], mine, version=1, score="1")) == (
        VERSION_CONFLICT
    )
    first = ok(grade(client, cookie, scored["id"], mine, version=2, score="1"))
    assert (first["score"], first["score_percent"]) == ("1.00", "33.33")
    # An unchanged re-grade writes nothing (no reason needed).
    same = ok(grade(client, cookie, scored["id"], mine, version=3, score="1.00"))
    assert same["version"] == 3
    assert field_codes(
        grade(
            client,
            cookie,
            scored["id"],
            mine,
            version=3,
            score="2",
            change_reason="x" * 501,
        )
    ) == [(["body", "change_reason"], "change_reason_required")]
    again = ok(
        grade(
            client,
            cookie,
            scored["id"],
            mine,
            version=3,
            score="2",
            change_reason="Error",
        )
    )
    assert (again["score_percent"], again["version"]) == ("66.67", 4)
    # Feedback only.
    assert field_codes(
        grade(client, cookie, feedback_only["id"], mine, version=2, score="1")
    ) == [
        (["body", "score"], "score_not_allowed"),
        (["body", "feedback"], "feedback_required"),
    ]
    done = ok(
        grade(client, cookie, feedback_only["id"], mine, version=2, feedback="Muy bien")
    )
    assert (done["score"], done["feedback"], done["state"]) == (
        None,
        "Muy bien",
        "graded",
    )
    # Feedback-only grades stay out of the history; scored ones are in it.
    history = ok(get(client, pupil, history_path(mine)))
    assert [uuid.UUID(e["id"]) for e in history["entries"]] == [
        run_sql(
            url,
            "SELECT id FROM assignment_submissions WHERE assignment_id = :a"
            " AND student_id = :s",
            {"a": scored["id"], "s": mine},
        )[0].id
    ]


# --- E3: late and missing at read time, the per-student list -------------------------


def test_late_and_missing_follow_the_deadline(module_seeded_url, client, at_now):
    url = module_seeded_url
    me, mine = student_account(url)
    tutor, cookie, _, _, view = homework(
        client, url, students=(mine,), due_on="2031-03-05"
    )
    pupil = sign_in(client, me.username)
    assignment_id = view["id"]
    # due_time NULL: the end of the local day; 23:59 local is on time.
    at_now.set(datetime(2031, 3, 6, 5, 59, tzinfo=UTC))
    assert ok(get(client, pupil, student_path(mine)))["summary"]["pending"] == 1
    at_now.set(datetime(2031, 3, 6, 6, 0, tzinfo=UTC))
    item = ok(get(client, pupil, student_path(mine, assignment_id)))
    assert item["submission"]["state"] == "missing"
    late = ok(submit(client, pupil, assignment_id, mine, text="Tarde"))
    assert late["submission"]["is_late"] is True
    # Extending the deadline un-flags it (computed, never stored).
    at_now.set(datetime(2031, 3, 6, 6, 30, tzinfo=UTC))  # 2031-03-06 local
    extended = ok(
        patch(
            client,
            cookie,
            a_path(assignment_id),
            {"version": 1, "due_on": "2031-03-07"},
        )
    )
    assert roster_row(extended, mine)["is_late"] is False
    assert extended["counts"]["late"] == 0
    assert (
        ok(get(client, pupil, student_path(mine, assignment_id)))["submission"][
            "is_late"
        ]
        is False
    )
    assert tutor


def test_the_student_list_filters_and_pages(module_seeded_url, client, at_now):
    url = module_seeded_url
    me, mine = student_account(url)
    tutor, (student,), subject, _ = teaching(url, students=(mine,))
    cookie = sign_in(client, tutor.username)
    pupil = sign_in(client, me.username)
    made = []
    for day, due_time in (
        ("2031-03-06", None),
        ("2031-03-06", "10:00"),
        ("2031-03-06", "18:00"),
        ("2031-03-07", None),
        ("2031-03-05", "12:30"),
    ):
        made.append(
            ok(
                create(
                    client,
                    cookie,
                    body(
                        subject,
                        [student],
                        due_on=day,
                        **({"due_time": due_time} if due_time else {}),
                    ),
                ),
                CREATED,
            )
        )
    ok(submit(client, pupil, made[0]["id"], mine, text="Hecho"))
    at_now.set(datetime(2031, 3, 5, 19, 0, tzinfo=UTC))  # 13:00: one is missing
    listed = ok(get(client, pupil, student_path(mine)))
    # Order: due_on desc, due_time desc nulls first, id.
    assert [i["id"] for i in listed["items"]] == [
        made[3]["id"],
        made[0]["id"],
        made[2]["id"],
        made[1]["id"],
        made[4]["id"],
    ]
    assert listed["summary"] == {
        "pending": 3,
        "missing": 1,
        "submitted": 1,
        "graded": 0,
    }
    # The state filter; the summary ignores it.
    missing = ok(get(client, pupil, student_path(mine), state="missing"))
    assert [i["id"] for i in missing["items"]] == [made[4]["id"]]
    assert missing["summary"] == listed["summary"]
    # from / to on due_on; the summary counts the filtered set.
    ranged = ok(
        get(
            client,
            pupil,
            student_path(mine),
            **{"from": "2031-03-06", "to": "2031-03-06"},
        )
    )
    assert len(ranged["items"]) == 3
    assert ranged["summary"] == {
        "pending": 2,
        "missing": 0,
        "submitted": 1,
        "graded": 0,
    }
    assert field_codes(
        get(
            client,
            pupil,
            student_path(mine),
            **{"from": "2031-03-07", "to": "2031-03-06"},
        )
    ) == [(["query", "from"], "range_invalid")]
    # The cursor: pages of 2.
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = ok(get(client, pupil, student_path(mine), **params))
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [i["id"] for i in listed["items"]]
    assert code_of(get(client, pupil, student_path(mine), cursor="nope")) == (
        400,
        "invalid_cursor",
    )
    assert code_of(get(client, pupil, student_path(mine), limit=51)) == INVALID
    # The subject filter.
    assert (
        ok(get(client, pupil, student_path(mine), subject_id=str(uuid.uuid4())))[
            "items"
        ]
        == []
    )


def test_the_working_list(module_seeded_url, client, staff):
    url = module_seeded_url
    a, b = new_student(url), new_student(url)
    tutor, _, subject, session_id = teaching(url, students=(a, b))
    cookie = sign_in(client, tutor.username)
    first = ok(
        create(client, cookie, body(subject, [a, b], due_on="2031-03-06")), CREATED
    )
    second = ok(
        create(
            client,
            cookie,
            {
                "subject_id": str(subject),
                "title": "Clase",
                "instructions": "x",
                "due_on": "2031-03-08",
                "class_session_id": session_id,
            },
        ),
        CREATED,
    )
    listed = ok(get(client, cookie, ASSIGNMENTS))
    assert [i["id"] for i in listed["items"]] == [second["id"], first["id"]]
    assert set(listed["items"][0]) == SUMMARY_KEYS
    assert listed["items"][0]["counts"]["recipients"] == 2
    assert [
        i["id"]
        for i in ok(get(client, cookie, ASSIGNMENTS, class_session_id=session_id))[
            "items"
        ]
    ] == [second["id"]]
    assert ok(get(client, cookie, ASSIGNMENTS, due="past"))["items"] == []
    assert (
        len(ok(get(client, cookie, ASSIGNMENTS, due="upcoming", limit=1))["items"]) == 1
    )
    page = ok(get(client, cookie, ASSIGNMENTS, limit=1))
    rest = ok(get(client, cookie, ASSIGNMENTS, limit=1, cursor=page["next_cursor"]))
    assert [rest["items"][0]["id"]] == [first["id"]]
    # Another tutor sees none of them; reviewers see them; a student filter.
    other = sign_in(client, a_tutor(url).username)
    assert ok(get(client, other, ASSIGNMENTS))["items"] == []
    reviewed = ok(get(client, staff, ASSIGNMENTS, student_id=str(a), limit=50))
    assert {first["id"], second["id"]} <= {i["id"] for i in reviewed["items"]}
    for user in (
        create_user(url, roles=(FINANCE_STAFF,)),
        create_user(url, roles=(GUARDIAN,)),
        create_user(url, roles=(STUDENT,)),
    ):
        caller = sign_in(client, user.username)
        assert code_of(get(client, caller, ASSIGNMENTS)) == DENIED


# --- E6: the former creator, staff, Finance ------------------------------------------


def test_a_former_creator_reads_their_homework_read_only(
    module_seeded_url, client, at_now
):
    url = module_seeded_url
    long_ago = NOW - timedelta(days=40)
    me, mine = student_account(url)
    tutor, _, subject, _ = teaching(
        url, students=(mine,), state="completed", starts_at=long_ago
    )
    at_now.set(long_ago + timedelta(hours=2))
    cookie = sign_in(client, tutor.username)
    own = ok(
        create(
            client,
            cookie,
            body(
                subject,
                [mine],
                max_score="10",
                due_on=(long_ago + timedelta(days=2)).date().isoformat(),
            ),
        ),
        CREATED,
    )
    pupil = sign_in(client, me.username)
    ok(submit(client, pupil, own["id"], mine, text="Hecho"))
    ok(grade(client, cookie, own["id"], mine, version=2, score="9"))
    at_now.set(NOW)
    # Another tutor's homework for the same student.
    other_tutor, _, other_subject, _ = teaching(url, students=(mine,))
    other_cookie = sign_in(client, other_tutor.username)
    theirs = ok(create(client, other_cookie, body(other_subject, [mine])), CREATED)
    cookie = sign_in(client, tutor.username)

    read = ok(get(client, cookie, a_path(own["id"])))
    assert (read["can_edit"], read["can_delete"]) == (False, False)
    assert roster_row(read, mine)["can_grade"] is False
    for response in (
        patch(client, cookie, a_path(own["id"]), {"version": 1, "title": "x"}),
        delete(client, cookie, own["id"], 1),
        submit(client, cookie, own["id"], mine, version=3, text="x"),
        grade(client, cookie, own["id"], mine, version=3, score="8", change_reason="r"),
    ):
        assert code_of(response) == READ_ONLY
    response = create(client, cookie, body(subject, [mine]))
    assert field_codes(response) == [(["body", "student_ids"], "recipient_invalid")]
    assert_same_not_found(
        get(client, cookie, a_path(theirs["id"])),
        get(client, cookie, a_path(uuid.uuid4())),
    )
    # The per-student list: their own only; the history: their own entries.
    assert [i["id"] for i in ok(get(client, cookie, student_path(mine)))["items"]] == [
        own["id"]
    ]
    assert_same_not_found(
        get(client, cookie, student_path(mine, theirs["id"])),
        get(client, cookie, student_path(mine, uuid.uuid4())),
    )
    history = ok(get(client, cookie, history_path(mine)))
    assert [e["kind"] for e in history["entries"]] == ["assignment_grade"]
    assert own["id"] in {i["id"] for i in ok(get(client, cookie, ASSIGNMENTS))["items"]}
    # Without homework of theirs, another former student is the unknown 404.
    other = new_student(url)
    teaching(
        url,
        tutor=tutor,
        students=(other,),
        state="completed",
        starts_at=long_ago + timedelta(days=1),
    )
    assert_same_not_found(
        get(client, cookie, student_path(other)),
        get(client, cookie, student_path(uuid.uuid4())),
    )


def test_staff_read_and_never_write(module_seeded_url, client, owner, staff):
    url = module_seeded_url
    me, mine = student_account(url)
    _, cookie, _, _, view = homework(client, url, students=(mine,), max_score="5")
    pupil = sign_in(client, me.username)
    ok(submit(client, pupil, view["id"], mine, text="Hecho"))
    administrator = create_user(url, roles=(ADMINISTRATOR,))
    for caller in (
        staff,
        sign_in(client, owner.username),
        sign_in(client, administrator.username),
    ):
        read = ok(get(client, caller, a_path(view["id"])))
        assert roster_row(read, mine)["text"] == "Hecho"
        assert (
            ok(get(client, caller, student_path(mine)))["items"][0]["id"] == view["id"]
        )
        for response in (
            patch(client, caller, a_path(view["id"]), {"version": 1, "title": "x"}),
            delete(client, caller, view["id"], 1),
            submit(client, caller, view["id"], mine, version=2, text="x"),
            grade(client, caller, view["id"], mine, version=2, score="1"),
        ):
            assert code_of(response) == CREATOR_ONLY
    finance = sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username)
    unknown = uuid.uuid4()
    assert code_of(get(client, finance, ASSIGNMENTS)) == DENIED
    for hidden, missing in (
        (
            get(client, finance, a_path(view["id"])),
            get(client, finance, a_path(unknown)),
        ),
        (
            get(client, finance, student_path(mine)),
            get(client, finance, student_path(unknown)),
        ),
        (
            get(client, finance, student_path(mine, view["id"])),
            get(client, finance, student_path(mine, unknown)),
        ),
        (
            get(client, finance, history_path(mine)),
            get(client, finance, history_path(unknown)),
        ),
        (
            patch(client, finance, a_path(view["id"]), {"version": 1}),
            patch(client, finance, a_path(unknown), {"version": 1}),
        ),
        (delete(client, finance, view["id"], 1), delete(client, finance, unknown, 1)),
        (
            submit(client, finance, view["id"], mine, version=2, text="x"),
            submit(client, finance, unknown, mine, version=2, text="x"),
        ),
        (
            grade(client, finance, view["id"], mine, version=2, score="1"),
            grade(client, finance, unknown, mine, version=2, score="1"),
        ),
    ):
        assert_same_not_found(hidden, missing)
    assert cookie


# --- E1: the consent rule ------------------------------------------------------------


def test_an_adult_needs_the_consent_and_a_revocation_applies_at_once(
    module_seeded_url, client
):
    url = module_seeded_url
    family = guardian_with_child(url, adult=True)
    _, cookie, (student,), _, view = homework(
        client, url, students=(family.student_id,), max_score="10"
    )
    ok(submit(client, cookie, view["id"], student))
    ok(grade(client, cookie, view["id"], student, version=2, score="5"))
    guardian = sign_in(client, family.user.username)
    # Not notified without the consent.
    assert notifications(url, SET_TYPE, assignment_id=view["id"]) == []
    assert notifications(url, GRADED_TYPE, assignment_id=view["id"]) == []
    unknown = uuid.uuid4()

    def writes() -> None:
        # The submit route answers the matrix's 403 for this guardian.
        assert code_of(
            submit(client, guardian, view["id"], student, version=3, text="x")
        ) in (CONSENT, STUDENT_ONLY)
        # The routes a family never uses: the unknown-id 404.
        for hidden, missing in (
            (
                patch(client, guardian, a_path(view["id"]), {"version": 1}),
                patch(client, guardian, a_path(unknown), {"version": 1}),
            ),
            (
                delete(client, guardian, view["id"], 1),
                delete(client, guardian, unknown, 1),
            ),
            (
                grade(client, guardian, view["id"], student, version=3, score="1"),
                grade(client, guardian, unknown, student, version=3, score="1"),
            ),
            (
                get(client, guardian, a_path(view["id"])),
                get(client, guardian, a_path(unknown)),
            ),
        ):
            assert_same_not_found(hidden, missing)

    assert code_of(get(client, guardian, student_path(student))) == CONSENT
    assert code_of(get(client, guardian, student_path(student, view["id"]))) == CONSENT
    assert code_of(get(client, guardian, history_path(student))) == CONSENT
    assert (
        code_of(submit(client, guardian, view["id"], student, version=3, text="x"))
        == CONSENT
    )
    writes()
    give_academic_consent(url, family)
    assert (
        ok(get(client, guardian, student_path(student)))["items"][0]["id"] == view["id"]
    )
    assert ok(get(client, guardian, history_path(student)))["entries"]
    assert (
        code_of(submit(client, guardian, view["id"], student, version=3, text="x"))
        == STUDENT_ONLY
    )
    writes()
    revoke_academic_consent(url, family)
    assert code_of(get(client, guardian, student_path(student))) == CONSENT
    assert code_of(get(client, guardian, student_path(student, view["id"]))) == CONSENT
    assert (
        code_of(submit(client, guardian, view["id"], student, version=3, text="x"))
        == CONSENT
    )
    writes()


# --- J3: IDOR -----------------------------------------------------------------------


def test_the_hidden_assignments_get_the_same_404(module_seeded_url, client):
    url = module_seeded_url
    me, mine = student_account(url)
    co, co_student = student_account(url)
    family = guardian_with_child(url)
    tutor, cookie, students, subject, view = homework(
        client, url, students=(mine, co_student, family.student_id), max_score="10"
    )
    assignment_id = view["id"]
    ok(submit(client, sign_in(client, me.username), assignment_id, mine, text="Mío"))
    # Another tutor currently teaching the student (not the creator).
    colleague, _, _, _ = teaching(url, students=(mine,), subject=subject)
    colleague_cookie = sign_in(client, colleague.username)
    # A tutor who lost the role, after teaching the student.
    lost = create_user(url, roles=(TUTOR, GUARDIAN))
    teaching(url, tutor=lost, students=(mine,), subject=subject)
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": lost.id},
    )
    unknown = uuid.uuid4()

    def working_hidden(caller: Any) -> None:
        for hidden, missing in (
            (
                get(client, caller, a_path(assignment_id)),
                get(client, caller, a_path(unknown)),
            ),
            (
                patch(client, caller, a_path(assignment_id), {"version": 1}),
                patch(client, caller, a_path(unknown), {"version": 1}),
            ),
            (
                delete(client, caller, assignment_id, 1),
                delete(client, caller, unknown, 1),
            ),
        ):
            assert_same_not_found(hidden, missing)

    def rows_hidden(caller: Any, student: Any) -> None:
        for hidden, missing in (
            (
                submit(client, caller, assignment_id, student, version=2, text="x"),
                submit(client, caller, unknown, student, version=2, text="x"),
            ),
            (
                grade(client, caller, assignment_id, student, version=2, score="1"),
                grade(client, caller, unknown, student, version=2, score="1"),
            ),
            (
                get(client, caller, student_path(student, assignment_id)),
                get(client, caller, student_path(student, unknown)),
            ),
            (
                get(client, caller, student_path(student)),
                get(client, caller, student_path(unknown)),
            ),
        ):
            assert_same_not_found(hidden, missing)

    outsiders = [
        sign_in(client, a_tutor(url).username),
        sign_in(client, lost.username),
        sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username),
        sign_in(client, create_user(url, roles=(GUARDIAN,)).username),
        sign_in(client, student_account(url)[0].username),
    ]
    for caller in outsiders:
        working_hidden(caller)
        rows_hidden(caller, mine)
    # A co-recipient of the same group homework: the other's row is hidden.
    co_cookie = sign_in(client, co.username)
    working_hidden(co_cookie)
    rows_hidden(co_cookie, mine)
    assert ok(get(client, co_cookie, student_path(co_student, assignment_id)))
    # The non-creator tutor of the student: R3-R5 404, R6/R7 403, R8/R9 200.
    working_hidden(colleague_cookie)
    assert (
        code_of(
            submit(client, colleague_cookie, assignment_id, mine, version=2, text="x")
        )
        == CREATOR_ONLY
    )
    assert (
        code_of(
            grade(client, colleague_cookie, assignment_id, mine, version=2, score="1")
        )
        == CREATOR_ONLY
    )
    seen = ok(get(client, colleague_cookie, student_path(mine, assignment_id)))
    assert (seen["can_submit"], seen["can_grade"]) == (False, False)
    assert seen["submission"]["text"] == "Mío"
    # ... but not the rows of students they don't teach.
    rows_hidden(colleague_cookie, family.student_id)
    # A guardian on a student not linked to them.
    guardian = sign_in(client, family.user.username)
    rows_hidden(guardian, mine)
    working_hidden(guardian)
    # A student submitting for a sibling-like other recipient.
    assert_same_not_found(
        submit(
            client,
            sign_in(client, me.username),
            assignment_id,
            family.student_id,
            text="x",
        ),
        submit(
            client, sign_in(client, me.username), unknown, family.student_id, text="x"
        ),
    )
    assert tutor and cookie and students


def test_the_original_tutor_after_a_real_substitution(
    module_seeded_url, client, owner, at_now
):
    """#55: once the substitute accepted, the original tutor (the student not
    otherwise assigned) is a former creator: read-only; the substitute sets
    new homework."""
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
    created, offer_id = offered(
        client, boss, setup, starts_at=at(OFFER_MONDAY, "16:00")
    )
    original = sign_in(client, setup.tutor.username)
    assert code_of(answer(client, original, offer_id, "accept")) == OK
    due = (T0 + timedelta(days=3)).date().isoformat()
    own = ok(
        create(client, original, body(subject, [setup.student], due_on=due)), CREATED
    )
    detail = get(client, boss, f"/api/v1/class-sessions/{created['id']}").json()
    item = substituted(client, boss, detail, substitute.id)
    taker = sign_in(client, substitute.username)
    assert code_of(answer(client, taker, item["offer_id"], "accept")) == OK

    original = sign_in(client, setup.tutor.username)
    assert (
        code_of(
            patch(client, original, a_path(own["id"]), {"version": 1, "title": "x"})
        )
        == READ_ONLY
    )
    assert code_of(submit(client, original, own["id"], setup.student)) == READ_ONLY
    assert ok(get(client, original, a_path(own["id"])))["id"] == own["id"]
    # The substituted-away class is no longer theirs to link.
    assert field_codes(
        create(
            client,
            original,
            body(subject, None, due_on=due) | {"class_session_id": created["id"]},
        )
    ) == [(["body", "class_session_id"], "class_session_invalid")]
    taker = sign_in(client, substitute.username)
    ok(create(client, taker, body(subject, [setup.student], due_on=due)), CREATED)
    # The substitute reads the student's homework, never grades the other's.
    assert (
        ok(get(client, taker, student_path(setup.student, own["id"])))["id"]
        == own["id"]
    )
    assert code_of(submit(client, taker, own["id"], setup.student)) == CREATOR_ONLY


# --- B3: notifications --------------------------------------------------------------


def test_the_recipients_and_params(module_seeded_url, client):
    """E8: one notification per student; inactive guardian profiles and
    guardians of adults without the consent get nothing; ids only."""
    url = module_seeded_url
    me, mine = student_account(url)
    adult_family = guardian_with_child(url, adult=True)
    _, cookie, _, subject, view = homework(
        client,
        url,
        students=(mine, adult_family.student_id),
        title="Nunca en la notificación",
        instructions="Tampoco esto",
        max_score="10",
    )
    sent = notifications(url, SET_TYPE, assignment_id=view["id"])
    assert {row.recipient_user_id for row in sent} == {me.id}
    [row] = sent
    assert row.params == {
        "student_id": str(mine),
        "assignment_id": view["id"],
        "subject_id": str(subject),
        "due_on": DUE,
    }
    pupil = sign_in(client, me.username)
    ok(
        submit(
            client, pupil, view["id"], mine, text="Texto secreto", url="https://e.com/x"
        )
    )
    [told] = notifications(url, SUBMITTED_TYPE, assignment_id=view["id"])
    assert "secreto" not in str(told.params) and "e.com" not in str(told.params)
    ok(
        grade(
            client, cookie, view["id"], mine, version=2, score="7", feedback="Privado"
        )
    )
    [graded] = notifications(url, GRADED_TYPE, assignment_id=view["id"])
    assert graded.params == {
        "student_id": str(mine),
        "assignment_id": view["id"],
        "subject_id": str(subject),
    }
    for found in (row, told, graded):
        for secret in ("Nunca", "Tampoco", "secreto", "e.com", "Privado"):
            assert secret not in str(found.params)


# --- G1: the academic history -------------------------------------------------------


def _final_evaluation(
    client: TestClient, staff: Any, cookie: Any, student: Any, subject: Any, on: str
) -> str:
    template = ok(
        post(
            client,
            staff,
            "/api/v1/evaluation-templates",
            {
                "name": f"Plantilla {uuid.uuid4().hex[:8]}",
                "kind": "ongoing",
                "subject_id": str(subject),
                "criteria": [{"title": "Lectura", "max_score": "10"}],
            },
        ),
        CREATED,
    )
    made = ok(
        post(
            client,
            cookie,
            "/api/v1/evaluations",
            {
                "student_id": str(student),
                "template_id": template["id"],
                "evaluated_on": on,
                "scores": [{"position": 1, "score": "5"}],
            },
        ),
        CREATED,
    )
    ok(
        post(
            client, cookie, f"/api/v1/evaluations/{made['id']}/finalize", {"version": 1}
        )
    )
    return str(made["id"])


def test_the_history_entries_and_groups(module_seeded_url, client, staff, at_now):
    url = module_seeded_url
    me, mine = student_account(url)
    tutor, _, subject, _ = teaching(url, students=(mine,))
    cookie = sign_in(client, tutor.username)
    pupil = sign_in(client, me.username)
    evaluation_id = _final_evaluation(
        client, staff, cookie, mine, subject, "2031-03-04"
    )
    scored = ok(create(client, cookie, body(subject, [mine], max_score="4")), CREATED)
    other = ok(create(client, cookie, body(subject, [mine], max_score="10")), CREATED)
    feedback_only = ok(create(client, cookie, body(subject, [mine])), CREATED)
    for made in (scored, other, feedback_only):
        ok(submit(client, pupil, made["id"], mine, text="x"))
    ok(grade(client, cookie, scored["id"], mine, version=2, score="3"))
    at_now.set(NOW + timedelta(minutes=5))
    ok(grade(client, cookie, other["id"], mine, version=2, score="5"))
    ok(grade(client, cookie, feedback_only["id"], mine, version=2, feedback="Bien"))

    history = ok(get(client, pupil, history_path(mine), group_by="none"))
    kinds = [
        (e["kind"], e.get("graded_on") or e.get("evaluated_on"))
        for e in history["entries"]
    ]
    assert kinds == [
        ("assignment_grade", "2031-03-05"),
        ("assignment_grade", "2031-03-05"),
        ("evaluation", "2031-03-04"),
    ]
    first, second, evaluation = history["entries"]
    assert first["graded_at"] > second["graded_at"]
    assert (first["score_percent"], second["score_percent"]) == ("50.00", "75.00")
    assert evaluation["id"] == evaluation_id
    for entry in (first, second):
        assert set(entry) == GRADE_ENTRY_KEYS
        assert "last_change_reason" not in entry and "text" not in entry
    [group] = history["groups"]
    [subject_group] = group["subjects"]
    assert (subject_group["count"], subject_group["average_percent"]) == (1, "50.00")
    assert (
        subject_group["assignment_count"],
        subject_group["assignment_average_percent"],
    ) == (2, "62.50")
    assert subject_group["entry_ids"] == [e["id"] for e in history["entries"]]
    # Filters apply to both kinds.
    only = ok(get(client, pupil, history_path(mine), **{"from": "2031-03-05"}))
    assert {e["kind"] for e in only["entries"]} == {"assignment_grade"}
    # A subject with homework grades only: no evaluation average.
    other_subject = new_subject(url)
    teaching(
        url,
        tutor=tutor,
        students=(mine,),
        subject=other_subject,
        starts_at=STARTED + timedelta(days=1),
    )
    alone = ok(
        create(client, cookie, body(other_subject, [mine], max_score="2")), CREATED
    )
    ok(submit(client, pupil, alone["id"], mine, text="x"))
    ok(grade(client, cookie, alone["id"], mine, version=2, score="2"))
    grouped = ok(get(client, pupil, history_path(mine), subject_id=str(other_subject)))
    [[only_homework]] = [g["subjects"] for g in grouped["groups"]]
    assert (only_homework["count"], only_homework["average_percent"]) == (0, None)
    assert only_homework["assignment_average_percent"] == "100.00"
    # A deleted assignment leaves the history.
    run_sql(
        url,
        "UPDATE assignments SET deleted_at = now(), deleted_by_user_id = :u"
        " WHERE id = :id",
        {"u": tutor.id, "id": alone["id"]},
    )
    assert (
        ok(get(client, pupil, history_path(mine), subject_id=str(other_subject)))[
            "entries"
        ]
        == []
    )


# --- Statement budgets --------------------------------------------------------------


def _many(url: str, client: TestClient, count: int) -> tuple[Any, ...]:
    family = guardian_with_child(url, adult=True, consent=True)
    me, mine = student_account(url)
    tutor, students, subject, _ = teaching(url, students=(family.student_id, mine))
    cookie = sign_in(client, tutor.username)
    for _ in range(count):
        made = ok(
            create(client, cookie, body(subject, students, max_score="10")), CREATED
        )
        ok(submit(client, cookie, made["id"], family.student_id))
        ok(grade(client, cookie, made["id"], family.student_id, version=2, score="5"))
    return tutor, family, me, mine


@pytest.mark.parametrize("count", [1, 20])
def test_the_list_statement_budgets(module_seeded_url, client, coordinator, count):
    url = module_seeded_url
    tutor, family, me, mine = _many(url, client, count)
    for principal, student in (
        (principal_of(tutor, TUTOR), family.student_id),
        (principal_of(coordinator, ACADEMIC_COORDINATOR), family.student_id),
        (principal_of(family.user, GUARDIAN), family.student_id),
        (principal_of(me, STUDENT), mine),
    ):
        statements, listed = count_statements(
            url,
            lambda s, p=principal, st=student: workflow.list_student_assignments(
                s,
                p,
                st,
                state=None,
                subject_id=None,
                from_=None,
                to=None,
                cursor=None,
                limit=20,
            ),
        )
        assert statements <= 7, (principal.roles, statements)
        assert len(listed.items) == count
        statements, history = count_statements(
            url,
            lambda s, p=principal, st=student: evaluation_workflow.academic_history(
                s, p, st, group_by="school_year", subject_id=None, from_=None, to=None
            ),
        )
        assert statements <= 10, (principal.roles, statements)
    for principal in (
        principal_of(tutor, TUTOR),
        principal_of(coordinator, ACADEMIC_COORDINATOR),
    ):
        statements, page = count_statements(
            url,
            lambda s, p=principal: workflow.list_assignments(
                s,
                p,
                student_id=family.student_id,
                subject_id=None,
                class_session_id=None,
                due=None,
                cursor=None,
                limit=20,
            ),
        )
        assert statements <= 8, (principal.roles, statements)
        assert len(page.items) == count


@pytest.mark.parametrize("recipients", [1, 100])
def test_the_detail_statement_budget(
    module_seeded_url, client, coordinator, recipients
):
    url = module_seeded_url
    students = tuple(new_student(url) for _ in range(recipients))
    tutor, cookie, _, _, view = homework(client, url, students=students)
    for principal in (
        principal_of(tutor, TUTOR),
        principal_of(coordinator, ACADEMIC_COORDINATOR),
    ):
        statements, detail = count_statements(
            url,
            lambda s, p=principal: workflow.get_assignment(s, p, uuid.UUID(view["id"])),
        )
        assert statements <= 8, (principal.roles, statements)
        assert len(detail.roster) == recipients
    assert cookie
