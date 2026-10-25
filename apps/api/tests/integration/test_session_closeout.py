"""Session closeout (#77 C, D, E, H1, H2) with real logins through the real
app: ``GET`` / ``POST /api/v1/class-sessions/{id}/closeout`` and the
``academics.session_closeout`` inbox item.

Success for individual, group and course sessions (the session
``completed`` with ``actual_minutes``, version + 1, the closeout row, the
pending approval request, the audit rows), the all-absent no-show, the D2
order step by step (incl. the rollback after a ``summary_required``), the
states, the D4 consequences of ``completed``, the handler that refuses
decisions outside the closeout routes (#78), the inbox visibility and
summary budget, the summary visibility (E5), the IDOR cases (H2) and the C3
statement budget.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import IntegrityError

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    TUTOR,
)
from app.workflows import session_closeout as workflow
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.session_request_support import (
    guardian_with_child,
    student_account,
)
from tests.integration.test_assignment_offers import (
    T0,
    Setup,
    answer,
    new_tutor_user,
    offered,
)
from tests.integration.test_attendance import Lesson, lesson, mark, principal_of
from tests.integration.test_class_session_create import MONDAY as OFFER_MONDAY
from tests.integration.test_class_session_create import (
    at,
    new_room,
    new_student,
    new_subject,
)
from tests.integration.test_session_substitutions import substituted

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
STARTED = NOW - timedelta(minutes=30)

OK = (200, None)
CREATED = (201, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
VERSION_CONFLICT = (409, "version_conflict")
TUTOR_ONLY = (403, "closeout_tutor_only")
ALREADY = (409, "closeout_already_submitted")
NOT_CLOSABLE = (409, "session_not_closable")
NOT_STARTED = (409, "session_not_started")
INCOMPLETE = (409, "closeout_attendance_incomplete")
ROUTE_REQUIRED = (409, "closeout_review_route_required")

SUMMARY = {
    "topics": "Fracciones equivalentes",
    "performance": "Participó y resolvió los ejercicios",
    "homework": "Ejercicios 1 a 5",
    "next_steps": "Suma de fracciones",
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


# --- Helpers --------------------------------------------------------------------------


def path(session_id: Any) -> str:
    return f"/api/v1/class-sessions/{session_id}/closeout"


def read(client: TestClient, cookie: Any, session_id: Any) -> Response:
    return get(client, cookie, path(session_id))


def submit(
    client: TestClient,
    cookie: Any,
    session_id: Any,
    version: int,
    *,
    minutes: Any = 55,
    summary: Any = None,
    **extra: Any,
) -> Response:
    body: dict[str, Any] = {
        "version": version,
        "actual_minutes": minutes,
        "summary": SUMMARY if summary is None else summary,
        **extra,
    }
    return post(client, cookie, path(session_id), body)


def session_row(url: str, session_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT state, actual_minutes, version, first_confirmed_at"
        " FROM class_sessions WHERE id = :id",
        {"id": session_id},
    )
    return row


def closeout_rows(url: str, session_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM session_closeouts WHERE class_session_id = :id",
        {"id": session_id},
    )


def approval_rows(url: str, closeout_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM approval_requests WHERE subject_id = :id",
        {"id": closeout_id},
    )


def marked(
    client: TestClient,
    made: Lesson,
    statuses: list[str] | None = None,
) -> tuple[Any, int]:
    """The tutor's cookie and the session version after marking every
    participant (``present`` by default)."""
    cookie = sign_in(client, made.tutor.username)
    marks = [
        (student, (statuses or ["present"] * len(made.students))[i])
        for i, student in enumerate(made.students)
    ]
    response = mark(client, cookie, made.id, 1, *marks)
    assert code_of(response) == OK, response.text
    return cookie, int(response.json()["version"])


def submitted(client: TestClient, made: Lesson, **kwargs: Any) -> dict[str, Any]:
    cookie, version = marked(client, made)
    response = submit(client, cookie, made.id, version, **kwargs)
    assert code_of(response) == CREATED, response.text
    body: dict[str, Any] = response.json()
    return body


# --- D3: success ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "students"), [("individual", 1), ("group", 3), ("course", 2)]
)
def test_the_tutor_closes_out_a_session(module_seeded_url, client, kind, students):
    url = module_seeded_url
    made = lesson(url, kind=kind, students=students)
    before = session_row(url, made.id)
    cookie, version = marked(client, made)
    [notified] = run_sql(url, "SELECT count(*) AS n FROM notifications")

    shown = read(client, cookie, made.id)
    assert code_of(shown) == OK
    body = shown.json()
    assert (body["can_submit"], body["block"], body["closeout"]) == (True, None, None)
    assert body["attendance"] == {
        "total": students,
        "marked": students,
        "all_absent": False,
        "missing_student_ids": [],
    }
    assert (body["kind"], body["scheduled_minutes"]) == (kind, 60)

    response = submit(client, cookie, made.id, version, minutes=55)

    assert code_of(response) == CREATED
    body = response.json()
    assert (body["state"], body["version"]) == ("completed", version + 1)
    assert (body["can_submit"], body["block"]) == (False, "closeout_already_submitted")
    closeout = body["closeout"]
    assert closeout["status"] == "submitted"
    assert closeout["actual_minutes"] == 55
    assert closeout["summary"] == SUMMARY
    assert closeout["summary_hidden"] is False
    assert closeout["submitted_by"] == {"display_name": "Tutora Lista"}
    assert datetime.fromisoformat(closeout["submitted_at"]) == NOW
    after = session_row(url, made.id)
    assert (after.state, after.actual_minutes, after.version) == (
        "completed",
        55,
        version + 1,
    )
    assert after.first_confirmed_at == before.first_confirmed_at
    [row] = closeout_rows(url, made.id)
    assert (row.status, row.tutor_user_id, row.version) == (
        "submitted",
        made.tutor.id,
        1,
    )
    assert (row.topics, row.homework) == (SUMMARY["topics"], SUMMARY["homework"])
    [request] = approval_rows(url, row.id)
    assert (request.request_type, request.status, request.requester_user_id) == (
        "academics.session_closeout",
        "pending",
        made.tutor.id,
    )
    assert request.priority == "normal"
    assert closeout["approval_request_id"] == str(request.id)
    # The audit rows: the session's update and the closeout's create.
    session_events = events(url, record_type="class_sessions", record_id=made.id)
    assert session_events[-1].action == "update"
    assert session_events[-1].actor_user_id == made.tutor.id
    assert session_events[-1].changes == {
        "state": {"old": "confirmed", "new": "completed"},
        "actual_minutes": {"old": None, "new": 55},
    }
    [created] = events(url, record_type="session_closeouts", record_id=row.id)
    assert (created.action, created.actor_user_id) == ("create", made.tutor.id)
    assert created.changes is not None
    assert created.changes["status"] == {"old": None, "new": "submitted"}
    # D3 step 4: no notification.
    [count] = run_sql(url, "SELECT count(*) AS n FROM notifications")
    assert count.n == notified.n


def test_the_body_is_a_closed_allow_list(module_seeded_url, client):
    body = submitted(client, lesson(module_seeded_url))

    assert set(body) == {
        "class_session_id",
        "version",
        "kind",
        "state",
        "starts_at",
        "ends_at",
        "scheduled_minutes",
        "subject",
        "tutor",
        "can_submit",
        "can_review",
        "can_correct",
        "can_resubmit",
        "can_request_correction",
        "block",
        "attendance",
        "closeout",
    }
    assert set(body["subject"]) == {"id", "name", "education_level_name"}
    assert set(body["closeout"]) == {
        "id",
        "status",
        "actual_minutes",
        "summary",
        "summary_hidden",
        "submitted_by",
        "submitted_at",
        "approval_request_id",
        # #78 E1, AM2.
        "version",
        "review",
        "approval",
        "revisions",
        "corrections",
        "attendance_changed_since_approval",
    }
    assert set(body["closeout"]["summary"]) == {
        "topics",
        "performance",
        "homework",
        "next_steps",
    }


def test_the_summary_is_stored_trimmed_with_empty_as_null(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url)
    summary = {
        "topics": "  Fracciones  ",
        "performance": "Bien\n",
        "homework": "   ",
        "next_steps": " Decimales",
    }

    body = submitted(client, made, summary=summary)

    assert body["closeout"]["summary"] == {
        "topics": "Fracciones",
        "performance": "Bien",
        "homework": None,
        "next_steps": "Decimales",
    }
    [row] = closeout_rows(url, made.id)
    assert (row.topics, row.performance, row.homework, row.next_steps) == (
        "Fracciones",
        "Bien",
        None,
        "Decimales",
    )


def test_an_all_absent_session_closes_out_without_a_summary(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url, kind="group", students=2)
    cookie, version = marked(client, made, ["absent", "absent"])
    assert read(client, cookie, made.id).json()["attendance"]["all_absent"] is True

    response = submit(client, cookie, made.id, version, minutes=0, summary={})

    assert code_of(response) == CREATED
    closeout = response.json()["closeout"]
    assert closeout["actual_minutes"] == 0
    assert closeout["summary"] == {
        "topics": None,
        "performance": None,
        "homework": None,
        "next_steps": None,
    }
    assert session_row(url, made.id).actual_minutes == 0


# --- D2: the order --------------------------------------------------------------------


def test_anonymous_gets_401(module_seeded_url, client):
    made = lesson(module_seeded_url)
    client.cookies.clear()

    assert client.get(path(made.id)).status_code == 401
    assert (
        client.post(
            path(made.id), json={"version": 1, "actual_minutes": 5, "summary": {}}
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    ("changes", "where"),
    [
        ({"actual_minutes": -1}, ["body", "actual_minutes"]),
        ({"actual_minutes": 721}, ["body", "actual_minutes"]),
        ({"actual_minutes": None}, ["body", "actual_minutes"]),
        ({"summary": SUMMARY | {"topics": "x" * 2001}}, ["body", "summary", "topics"]),
        (
            {"summary": SUMMARY | {"homework": " " + "x" * 2001}},
            ["body", "summary", "homework"],
        ),
        ({"summary": SUMMARY | {"grade": "A"}}, ["body", "summary", "grade"]),
        ({"note": "x"}, ["body", "note"]),
        ({"version": None}, ["body", "version"]),
    ],
)
def test_the_body_422s(module_seeded_url, client, changes, where):
    made = lesson(module_seeded_url)
    cookie = sign_in(client, made.tutor.username)
    body: dict[str, Any] = {"version": 1, "actual_minutes": 55, "summary": SUMMARY}
    body |= changes

    response = post(client, cookie, path(made.id), body)

    assert code_of(response) == INVALID
    assert where in [error["path"] for error in response.json()["errors"]]
    if "x" * 2001 in str(changes):
        [error] = response.json()["errors"]
        assert error["code"] == "too_long"


def test_the_bounds_are_inclusive(module_seeded_url, client):
    url = module_seeded_url
    for minutes in (0, 720):
        made = lesson(url)
        long_text = {k: "x" * 2000 for k in SUMMARY}
        assert submitted(client, made, minutes=minutes, summary=long_text)["closeout"][
            "actual_minutes"
        ] == (minutes)


def test_a_visible_staff_member_gets_403_after_404s(module_seeded_url, client, owner):
    made = lesson(module_seeded_url)
    staff = sign_in(client, owner.username)

    assert code_of(submit(client, staff, made.id, 1)) == TUTOR_ONLY
    assert code_of(submit(client, staff, uuid.uuid4(), 1)) == NOT_FOUND
    # 403 before the version and the state.
    assert code_of(submit(client, staff, made.id, 99)) == TUTOR_ONLY


def test_version_before_state_and_state_before_attendance(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url, kind="group", students=2)
    cookie = sign_in(client, made.tutor.username)

    # Nothing marked: the version conflict comes first.
    assert code_of(submit(client, cookie, made.id, 2)) == VERSION_CONFLICT
    # A future session: not started before the attendance check.
    future = lesson(url, starts_at=NOW + timedelta(hours=2), tutor=made.tutor)
    assert code_of(submit(client, cookie, future.id, 1)) == NOT_STARTED


def test_attendance_must_be_complete(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url, kind="group", students=3)
    cookie = sign_in(client, made.tutor.username)
    first, *rest = made.students
    response = mark(client, cookie, made.id, 1, (first, "present"))
    version = response.json()["version"]
    shown = read(client, cookie, made.id).json()
    assert shown["can_submit"] is True
    assert shown["attendance"]["missing_student_ids"] == sorted(str(s) for s in rest)

    response = submit(client, cookie, made.id, version)

    assert code_of(response) == INCOMPLETE
    assert response.json()["details"] == {
        "missing_student_ids": sorted(str(s) for s in rest)
    }
    assert session_row(url, made.id).state == "confirmed"
    assert closeout_rows(url, made.id) == []


def test_attendance_is_checked_before_the_summary(module_seeded_url, client):
    made = lesson(module_seeded_url)
    cookie = sign_in(client, made.tutor.username)

    assert code_of(submit(client, cookie, made.id, 1, summary={})) == INCOMPLETE


@pytest.mark.parametrize("field", ["topics", "performance", "next_steps"])
def test_a_required_summary_field_422s_and_rolls_back(module_seeded_url, client, field):
    url = module_seeded_url
    made = lesson(url)
    cookie, version = marked(client, made)

    response = submit(
        client, cookie, made.id, version, summary=SUMMARY | {field: "   "}
    )

    assert code_of(response) == INVALID
    assert response.json()["errors"] == [
        {
            "path": ["body", "summary", field],
            "code": "summary_required",
            "message": "Fill in this part of the summary.",
        }
    ]
    # Rolled back: nothing changed.
    row = session_row(url, made.id)
    assert (row.state, row.actual_minutes, row.version) == ("confirmed", None, version)
    assert closeout_rows(url, made.id) == []


def test_every_missing_required_field_is_listed(module_seeded_url, client):
    made = lesson(module_seeded_url, kind="group", students=2)
    # One present and one absent: the summary stays required.
    cookie, version = marked(client, made, ["present", "absent"])

    response = submit(client, cookie, made.id, version, summary={"homework": "x"})

    assert code_of(response) == INVALID
    assert [e["path"][-1] for e in response.json()["errors"]] == [
        "topics",
        "performance",
        "next_steps",
    ]


# --- A5: the states -------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["offered", "conflict", "cancelled", "skipped_closure"]
)
def test_other_states_are_not_closable(module_seeded_url, client, owner, state):
    made = lesson(module_seeded_url, state=state)
    cookie = sign_in(client, made.tutor.username)

    response = submit(client, cookie, made.id, 1)

    assert code_of(response) == NOT_CLOSABLE
    assert response.json()["details"] == {"state": state}
    body = read(client, cookie, made.id).json()
    assert (body["can_submit"], body["block"]) == (False, "session_not_closable")


def test_an_unassigned_session_has_no_tutor_to_close_it(module_seeded_url, client):
    """An unassigned session has no tutor (ADR-0005 D3), so nobody is its
    current tutor: staff read it; ``closeout_block`` answers
    ``session_not_closable`` for it (tests/unit/test_closeout_rules.py)."""
    url = module_seeded_url
    made = lesson(url, state="unassigned", with_tutor=False)
    staff = sign_in(client, create_user(url, roles=(ADMINISTRATOR,)).username)
    tutor = sign_in(client, made.tutor.username)

    body = read(client, staff, made.id).json()

    assert (body["state"], body["tutor"], body["block"]) == (
        "unassigned",
        None,
        "closeout_tutor_only",
    )
    assert code_of(submit(client, staff, made.id, 1)) == TUTOR_ONLY
    assert code_of(submit(client, tutor, made.id, 1)) == NOT_FOUND


def test_a_future_session_is_not_started(module_seeded_url, client):
    made = lesson(module_seeded_url, starts_at=NOW + timedelta(minutes=1))
    cookie = sign_in(client, made.tutor.username)

    assert code_of(submit(client, cookie, made.id, 1)) == NOT_STARTED
    body = read(client, cookie, made.id).json()
    assert (body["can_submit"], body["block"]) == (False, "session_not_started")


def test_a_started_and_a_week_old_session_are_closable(module_seeded_url, client):
    url = module_seeded_url
    for starts_at in (NOW, STARTED, NOW - timedelta(days=7)):
        submitted(client, lesson(url, starts_at=starts_at))


def test_a_second_submit_is_already_submitted(module_seeded_url, client):
    made = lesson(module_seeded_url)
    body = submitted(client, made)
    cookie = sign_in(client, made.tutor.username)

    assert code_of(submit(client, cookie, made.id, body["version"])) == ALREADY


def test_a_completed_session_without_a_closeout(module_seeded_url, client, owner):
    """#299 demo rows and factories: closeout null, POST already submitted."""
    url = module_seeded_url
    made = lesson(url, state="completed")
    run_sql(
        url,
        "UPDATE class_sessions SET actual_minutes = 50 WHERE id = :id",
        {"id": made.id},
    )
    cookie = sign_in(client, made.tutor.username)

    body = read(client, cookie, made.id).json()

    assert (body["closeout"], body["can_submit"], body["block"]) == (
        None,
        False,
        "closeout_already_submitted",
    )
    assert code_of(submit(client, cookie, made.id, 1)) == ALREADY
    staff = read(client, sign_in(client, owner.username), made.id).json()
    assert (staff["closeout"], staff["block"]) == (None, "closeout_tutor_only")


# --- D4: the consequences of completed ------------------------------------------------


def test_completed_locks_the_tutor_and_keeps_staff_corrections(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    made = lesson(url)
    body = submitted(client, made)
    version = body["version"]
    tutor = sign_in(client, made.tutor.username)
    staff = sign_in(client, owner.username)
    [first] = made.students

    assert code_of(mark(client, tutor, made.id, version, (first, "late"))) == (
        409,
        "attendance_locked",
    )
    corrected = post(
        client,
        staff,
        f"/api/v1/class-sessions/{made.id}/attendance/corrections",
        {
            "version": version,
            "student_id": str(first),
            "status": "absent",
            "reason": "Recepción",
        },
    )
    assert code_of(corrected) == CREATED
    assert corrected.json()["state"] == "completed"
    version = corrected.json()["version"]
    # #55 cancel and #54 PATCH keep refusing a completed (started) session.
    cancelled = post(
        client,
        staff,
        f"/api/v1/class-sessions/{made.id}/cancel",
        {"version": version, "reason": "Prueba"},
    )
    assert code_of(cancelled) == (409, "class_session_not_cancellable")
    moved = patch(
        client,
        staff,
        f"/api/v1/class-sessions/{made.id}",
        {
            "version": version,
            "starts_at": (NOW + timedelta(days=1)).isoformat(),
            "ends_at": (NOW + timedelta(days=1, hours=1)).isoformat(),
        },
    )
    assert code_of(moved) == (409, "session_not_editable")
    # #62: the tutor can no longer ask for a change.
    changed = post(
        client,
        tutor,
        "/api/v1/change-requests",
        {
            "class_session_id": made.id,
            "kind": "reschedule",
            "proposed_starts_at": (NOW + timedelta(days=2)).isoformat(),
        },
    )
    assert code_of(changed) == (409, "class_session_not_changeable")
    # The session keeps occupying its tutor (states unchanged: a second
    # session for the tutor at the same time is refused by the exclusion).
    with pytest.raises(IntegrityError):
        lesson(url, tutor=made.tutor)


def test_the_family_schedule_shows_the_completed_session(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    made = lesson(url, kind="group", students=1)
    run_sql(
        url,
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " VALUES (:s, :st)",
        {"s": made.id, "st": family.student_id},
    )
    made.students.append(family.student_id)
    submitted(client, made)
    cookie = sign_in(client, family.user.username)

    response = get(
        client,
        cookie,
        f"/api/v1/students/{family.student_id}/schedule",
        **{
            "from": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "to": (NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
    )

    assert code_of(response) == OK
    [item] = [i for i in response.json()["items"] if i["id"] == made.id]
    assert item["state"] == "completed"


# --- E: the approval type ------------------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "reject", "request-changes"])
def test_the_inbox_cannot_decide(module_seeded_url, client, owner, action):
    """#78 E2, E3: decisions go through the closeout routes."""
    url = module_seeded_url
    body = submitted(client, lesson(url))
    request_id = body["closeout"]["approval_request_id"]
    staff = sign_in(client, owner.username)
    item = get(client, staff, f"/api/v1/approval-requests/{request_id}").json()
    assert item["allowed_actions"] == []

    response = post(
        client,
        staff,
        f"/api/v1/approval-requests/{request_id}/{action}",
        {} if action == "approve" else {"reason": "Revisión"},
    )

    assert code_of(response) == ROUTE_REQUIRED
    assert response.json()["details"] == {
        "review_path": f"/schedule/sessions/{body['class_session_id']}"
    }
    [request] = approval_rows(url, body["closeout"]["id"])
    assert request.status == "pending"


@pytest.mark.parametrize(
    ("role", "visible"),
    [
        (OWNER, True),
        (ADMINISTRATOR, True),
        (ACADEMIC_COORDINATOR, True),
        (FINANCE_STAFF, False),
        (TUTOR, False),
        (GUARDIAN, False),
    ],
)
def test_who_sees_the_inbox_item(module_seeded_url, client, role, visible):
    url = module_seeded_url
    made = lesson(url, kind="group", students=2)
    body = submitted(client, made)
    request_id = body["closeout"]["approval_request_id"]
    cookie = sign_in(client, create_user(url, roles=(role,)).username)

    response = get(client, cookie, f"/api/v1/approval-requests/{request_id}")

    if not visible:
        assert code_of(response) == NOT_FOUND
        listed = get(client, cookie, "/api/v1/approval-requests", status="all")
        assert request_id not in {i["id"] for i in listed.json().get("items", [])}
        return
    assert code_of(response) == OK
    item = response.json()
    assert item["request_type"] == "academics.session_closeout"
    assert item["requester"]["display_name"] == "Tutora Lista"
    assert item["subject"] == {
        "class_session_id": made.id,
        "starts_at": STARTED.isoformat().replace("+00:00", "Z"),
        "subject_name": body["subject"]["name"],
        "tutor_display_name": "Tutora Lista",
        "kind": "group",
        "scheduled_minutes": 60,
        "actual_minutes": 55,
        "participant_count": 2,
        # #78 C4.
        "kind_of_review": "first",
        "approved_minutes": None,
    }
    listed = get(
        client,
        cookie,
        "/api/v1/approval-requests",
        request_type="academics.session_closeout",
        status="all",
        limit=100,
    )
    assert code_of(listed) == OK
    assert request_id in {i["id"] for i in listed.json()["items"]}


@pytest.mark.parametrize("count", [1, 5])
def test_the_inbox_summaries_take_three_statements(module_seeded_url, client, count):
    url = module_seeded_url
    closeout_ids = [
        uuid.UUID(
            submitted(client, lesson(url, kind="group", students=2))["closeout"]["id"]
        )
        for _ in range(count)
    ]

    async def _summaries(session: Any) -> Any:
        return await workflow.SESSION_CLOSEOUT_HANDLER.summaries(session, closeout_ids)

    statements, result = count_statements(url, _summaries)

    assert set(result) == set(closeout_ids)
    assert statements <= 3, statements
    for summary in result.values():
        assert "topics" not in summary
        assert summary["participant_count"] == 2


# --- C2, E5: who sees the summary ----------------------------------------------------


@pytest.mark.parametrize(
    ("role", "sees"),
    [
        (OWNER, True),
        (ADMINISTRATOR, True),
        (ACADEMIC_COORDINATOR, True),
        (FINANCE_STAFF, False),
    ],
)
def test_staff_read_the_closeout(module_seeded_url, client, role, sees):
    url = module_seeded_url
    made = lesson(url)
    submitted(client, made)
    cookie = sign_in(client, create_user(url, roles=(role,)).username)

    response = read(client, cookie, made.id)

    assert code_of(response) == OK
    body = response.json()
    assert (body["can_submit"], body["block"]) == (False, "closeout_tutor_only")
    closeout = body["closeout"]
    assert closeout["actual_minutes"] == 55
    assert closeout["status"] == "submitted"
    if sees:
        assert (closeout["summary"], closeout["summary_hidden"]) == (SUMMARY, False)
    else:
        assert (closeout["summary"], closeout["summary_hidden"]) == (None, True)
    assert code_of(submit(client, cookie, made.id, body["version"])) == TUTOR_ONLY


def test_staff_see_a_waiting_session(module_seeded_url, client, owner):
    made = lesson(module_seeded_url)
    body = read(client, sign_in(client, owner.username), made.id).json()

    assert (body["state"], body["closeout"], body["block"]) == (
        "confirmed",
        None,
        "closeout_tutor_only",
    )
    assert body["tutor"] == {"display_name": "Tutora Lista"}


# --- H2: IDOR -------------------------------------------------------------------------


def test_another_tutor_gets_the_404_of_an_unknown_id(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url)
    other = sign_in(client, create_user(url, roles=(TUTOR,)).username)
    unknown = uuid.uuid4()

    assert_same_not_found(read(client, other, made.id), read(client, other, unknown))
    assert_same_not_found(
        submit(client, other, made.id, 1), submit(client, other, unknown, 1)
    )


def test_families_get_the_404_of_an_unknown_id(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    learner, own_student = student_account(url)
    made = lesson(url, kind="group", students=0)
    for student_id in (family.student_id, own_student):
        run_sql(
            url,
            "INSERT INTO class_session_participants (class_session_id, student_id)"
            " VALUES (:s, :st)",
            {"s": made.id, "st": student_id},
        )
    unknown = uuid.uuid4()

    for user in (family.user, learner):
        cookie = sign_in(client, user.username)
        assert_same_not_found(
            read(client, cookie, made.id), read(client, cookie, unknown)
        )
        assert_same_not_found(
            submit(client, cookie, made.id, 1), submit(client, cookie, unknown, 1)
        )


def test_a_user_who_lost_the_tutor_role_gets_404(module_seeded_url, client):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR, GUARDIAN))
    made = lesson(url, tutor=tutor)
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": tutor.id},
    )
    cookie = sign_in(client, tutor.username)
    unknown = uuid.uuid4()

    assert_same_not_found(read(client, cookie, made.id), read(client, cookie, unknown))
    assert_same_not_found(
        submit(client, cookie, made.id, 1), submit(client, cookie, unknown, 1)
    )


def test_the_current_tutor_after_a_real_substitution(
    module_seeded_url, client, owner, at_now
):
    """#55: the original tutor gets 404 once the substitute accepted (and
    the substitute submits, as the closeout's tutor); a pending substitute
    gets 404 until then."""
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
    staff = sign_in(client, owner.username)
    starts_at = at(OFFER_MONDAY, "16:00")
    created, offer_id = offered(client, staff, setup, starts_at=starts_at)
    assert (
        code_of(
            answer(client, sign_in(client, setup.tutor.username), offer_id, "accept")
        )
        == OK
    )
    detail = get(client, staff, f"/api/v1/class-sessions/{created['id']}").json()
    item = substituted(client, staff, detail, substitute.id)

    # Pending: the original tutor is still the current one.
    at_now.set(starts_at + timedelta(minutes=5))
    original = sign_in(client, setup.tutor.username)
    pending = sign_in(client, substitute.username)
    assert code_of(read(client, original, created["id"])) == OK
    assert_same_not_found(
        read(client, pending, created["id"]), read(client, pending, uuid.uuid4())
    )
    assert_same_not_found(
        submit(client, pending, created["id"], 1),
        submit(client, pending, uuid.uuid4(), 1),
    )

    at_now.set(T0 + timedelta(minutes=1))
    taker = sign_in(client, substitute.username)
    assert code_of(answer(client, taker, item["offer_id"], "accept")) == OK

    at_now.set(starts_at + timedelta(minutes=5))
    original = sign_in(client, setup.tutor.username)
    taker = sign_in(client, substitute.username)
    unknown = uuid.uuid4()
    assert_same_not_found(
        read(client, original, created["id"]), read(client, original, unknown)
    )
    version = read(client, taker, created["id"]).json()["version"]
    assert_same_not_found(
        submit(client, original, created["id"], version),
        submit(client, original, unknown, version),
    )
    marked_now = mark(client, taker, created["id"], version, (setup.student, "present"))
    assert code_of(marked_now) == OK
    response = submit(client, taker, created["id"], marked_now.json()["version"])
    assert code_of(response) == CREATED
    [row] = closeout_rows(url, created["id"])
    assert row.tutor_user_id == substitute.id


# --- D5: the backstop -----------------------------------------------------------------


def test_a_duplicate_closeout_row_is_refused(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url)
    submitted(client, made)

    with pytest.raises(IntegrityError, match="uq_session_closeouts_class_session_id"):
        run_sql(
            url,
            "INSERT INTO session_closeouts (class_session_id, tutor_user_id,"
            " status, submitted_at, last_submitted_at)"
            " VALUES (:s, :u, 'submitted', now(), now())",
            {"s": made.id, "u": made.tutor.id},
        )


# --- C3: the statement budget ---------------------------------------------------------


@pytest.mark.parametrize("count", [1, 20])
def test_the_read_takes_at_most_8_statements(module_seeded_url, client, owner, count):
    url = module_seeded_url
    made = lesson(url, kind="group", students=count)
    submitted(client, made)

    def read_as(principal: Any) -> Any:
        async def _read(session: Any) -> Any:
            return await workflow.get_session_closeout(
                session, principal, uuid.UUID(made.id)
            )

        return _read

    for principal in (
        principal_of(made.tutor.id, TUTOR),
        principal_of(owner.id, OWNER),
        principal_of(owner.id, FINANCE_STAFF),
    ):
        statements, result = count_statements(url, read_as(principal))
        assert result.attendance.total == count
        assert result.closeout is not None
        assert statements <= 8, statements
