"""Closeout review and corrections (#78 C, D, E, F, I1, I2; AM1, AM2) with
real logins through the real app: ``POST …/closeout/approve``,
``…/request-changes``, ``…/corrections``, ``PUT …/closeout``, the widened
``GET …/closeout``, the attendance unlock while ``changes_requested`` and
the generic inbox routes.

Every row of the status machine (status, versions, revision rows and their
snapshots, the core request, audit events, notifications), the C1/C2/C3/D2
orders, E4/D3 (a pending correction leaves the approval in force), E6
(attendance unlock, snapshot and re-approval), E7 (self-review), the
Finance redaction, rollbacks, the IDOR cases and the E2 statement budget.
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import DBAPIError

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
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.session_request_support import (
    guardian_with_child,
    student_account,
)
from tests.integration.test_attendance import (
    Lesson,
    correct,
    lesson,
    mark,
    principal_of,
)
from tests.integration.test_session_closeout import (
    NOW,
    SUMMARY,
    approval_rows,
    closeout_rows,
    marked,
    path,
    read,
    submit,
    submitted,
)

OK = (200, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
DENIED = (403, "permission_denied")
VERSION_CONFLICT = (409, "version_conflict")
TUTOR_ONLY = (403, "closeout_tutor_only")
SELF_REVIEW = (403, "closeout_self_review")
NOT_SUBMITTED = (409, "closeout_not_submitted")
WITH_TUTOR = (409, "closeout_with_tutor")
UNDER_REVIEW = (409, "closeout_under_review")
UNCHANGED = (422, "closeout_unchanged")
INCOMPLETE = (409, "closeout_attendance_incomplete")
LOCKED = (409, "attendance_locked")
ROUTE_REQUIRED = (409, "closeout_review_route_required")

REASON = "Falta la tarea asignada"


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
        display_name="Coordinación Académica",
    )


@pytest.fixture(scope="module")
def finance(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


# --- Helpers --------------------------------------------------------------------------


def decide(
    client: TestClient, cookie: Any, session_id: Any, action: str, body: Any
) -> Response:
    return post(client, cookie, f"{path(session_id)}/{action}", body)


def approve(client: TestClient, cookie: Any, session_id: Any, version: int) -> Response:
    return decide(client, cookie, session_id, "approve", {"version": version})


def send_back(
    client: TestClient,
    cookie: Any,
    session_id: Any,
    version: int,
    reason: Any = REASON,
) -> Response:
    return decide(
        client,
        cookie,
        session_id,
        "request-changes",
        {"version": version, "reason": reason},
    )


def correct_closeout(
    client: TestClient, cookie: Any, session_id: Any, version: int, **body: Any
) -> Response:
    payload = {"version": version, "reason": "Terminó antes", **body}
    return decide(client, cookie, session_id, "corrections", payload)


def resubmit(
    client: TestClient,
    cookie: Any,
    session_id: Any,
    version: int,
    *,
    minutes: int = 55,
    summary: Any = None,
    **extra: Any,
) -> Response:
    client.cookies.clear()
    return client.put(
        path(session_id),
        json={
            "version": version,
            "actual_minutes": minutes,
            "summary": SUMMARY if summary is None else summary,
            **extra,
        },
        headers=signed_headers(cookie),
    )


def version_of(body: dict[str, Any]) -> int:
    return int(body["closeout"]["version"])


def revisions(url: str, closeout_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM session_closeout_approvals WHERE closeout_id = :id"
        " ORDER BY revision",
        {"id": closeout_id},
    )


def corrections(url: str, closeout_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM session_closeout_corrections WHERE closeout_id = :id"
        " ORDER BY id",
        {"id": closeout_id},
    )


def notes(url: str, user_id: Any, notification_type: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM notifications WHERE recipient_user_id = :u"
        " AND notification_type = :t ORDER BY id",
        {"u": user_id, "t": notification_type},
    )


def minutes_of(url: str, session_id: Any) -> int:
    [row] = run_sql(
        url,
        "SELECT actual_minutes FROM class_sessions WHERE id = :id",
        {"id": session_id},
    )
    return int(row.actual_minutes)


def inbox_subject(client: TestClient, cookie: Any, request_id: Any) -> dict[str, Any]:
    item = get(client, cookie, f"/api/v1/approval-requests/{request_id}").json()
    subject: dict[str, Any] = item["subject"]
    return subject


def approved(client: TestClient, made: Lesson, reviewer: CreatedUser) -> dict[str, Any]:
    body = submitted(client, made)
    response = approve(
        client, sign_in(client, reviewer.username), made.id, version_of(body)
    )
    assert code_of(response) == OK, response.text
    result: dict[str, Any] = response.json()
    return result


def sent_back(
    client: TestClient, made: Lesson, reviewer: CreatedUser
) -> dict[str, Any]:
    body = submitted(client, made)
    response = send_back(
        client, sign_in(client, reviewer.username), made.id, version_of(body)
    )
    assert code_of(response) == OK, response.text
    result: dict[str, Any] = response.json()
    return result


# --- C1: approve ----------------------------------------------------------------------


def test_a_reviewer_approves(module_seeded_url, client, coordinator):
    url = module_seeded_url
    made = lesson(url, kind="group", students=2)
    body = submitted(client, made)
    staff = sign_in(client, coordinator.username)
    assert body["closeout"]["version"] == 1
    shown = read(client, staff, made.id).json()
    assert (shown["can_review"], shown["can_correct"]) == (True, True)

    response = approve(client, staff, made.id, 1)

    assert code_of(response) == OK
    result = response.json()
    closeout = result["closeout"]
    assert (closeout["status"], closeout["version"]) == ("approved", 2)
    [revision] = revisions(url, closeout["id"])
    assert (revision.revision, revision.supersedes_approval_id, revision.source) == (
        1,
        None,
        "review",
    )
    assert str(revision.class_session_id) == made.id
    assert revision.tutor_user_id == made.tutor.id
    assert revision.session_starts_at == NOW - timedelta(minutes=30)
    assert (revision.session_kind, revision.scheduled_minutes) == ("group", 60)
    assert revision.approved_minutes == 55
    assert revision.attendance == {str(s): "present" for s in made.students}
    assert (revision.approved_by_user_id, revision.approved_at) == (coordinator.id, NOW)
    assert closeout["approval"] == {
        "id": str(revision.id),
        "revision": 1,
        "approved_minutes": 55,
        "approved_by": {"display_name": "Coordinación Académica"},
        "approved_at": NOW.isoformat().replace("+00:00", "Z"),
        "source": "review",
    }
    assert closeout["revisions"] == [closeout["approval"]]
    assert closeout["review"]["status"] == "approved"
    assert closeout["attendance_changed_since_approval"] is False
    assert (result["can_review"], result["can_correct"]) == (False, True)
    [row] = closeout_rows(url, made.id)
    assert (row.status, row.current_approval_id, row.version) == (
        "approved",
        revision.id,
        2,
    )
    [request] = approval_rows(url, row.id)
    assert (request.status, request.resolved_by_user_id) == ("approved", coordinator.id)
    [event] = [
        e
        for e in events(url, record_type="session_closeouts", record_id=row.id)
        if e.action == "academics.closeout_approved"
    ]
    assert event.actor_user_id == coordinator.id
    assert event.details == {
        "approval_id": str(revision.id),
        "revision": 1,
        "approved_minutes": 55,
        "supersedes_approval_id": None,
        "source": "review",
    }
    [note] = notes(url, made.tutor.id, "academics.closeout_approved")
    assert note.params == {
        "class_session_id": made.id,
        "starts_at": (NOW - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        "subject_name": result["subject"]["name"],
        "approved_minutes": 55,
    }
    tutor_view = read(client, sign_in(client, made.tutor.username), made.id).json()
    assert (tutor_view["can_resubmit"], tutor_view["can_request_correction"]) == (
        False,
        True,
    )
    # The inbox no longer lists it as pending.
    pending = get(
        client,
        staff,
        "/api/v1/approval-requests",
        request_type="academics.session_closeout",
        limit=100,
    ).json()["items"]
    assert str(request.id) not in {item["id"] for item in pending}


# --- C2: request changes, then D: resubmit --------------------------------------------


def test_changes_requested_then_resubmitted(module_seeded_url, client, owner, at_now):
    url = module_seeded_url
    made = lesson(url)
    body = submitted(client, made)
    staff = sign_in(client, owner.username)

    response = send_back(client, staff, made.id, 1, reason=f"  {REASON}  ")

    assert code_of(response) == OK
    closeout = response.json()["closeout"]
    assert (closeout["status"], closeout["version"]) == ("changes_requested", 2)
    assert closeout["review"]["reason"] == REASON
    assert response.json()["state"] == "completed"
    [row] = closeout_rows(url, made.id)
    assert (row.review_reason, row.current_approval_id) == (REASON, None)
    [request] = approval_rows(url, row.id)
    assert (request.status, request.reason) == ("changes_requested", REASON)
    [event] = [
        e
        for e in events(url, record_type="session_closeouts", record_id=row.id)
        if e.action == "academics.closeout_changes_requested"
    ]
    assert event.details == {"reason": REASON}
    [note] = notes(url, made.tutor.id, "academics.closeout_changes_requested")
    assert set(note.params) == {"class_session_id", "starts_at", "subject_name"}
    assert not revisions(url, row.id)

    # E6/AM1: the tutor may mark attendance again.
    tutor = sign_in(client, made.tutor.username)
    view = read(client, tutor, made.id).json()
    assert (view["can_resubmit"], view["closeout"]["review"]["reason"]) == (
        True,
        REASON,
    )
    attendance = get(client, tutor, f"/api/v1/class-sessions/{made.id}/attendance")
    assert (attendance.json()["can_mark"], attendance.json()["block"]) == (True, None)
    [student] = made.students
    marked_now = mark(
        client, tutor, made.id, attendance.json()["version"], (student, "late")
    )
    assert code_of(marked_now) == OK

    at_now.set(NOW + timedelta(hours=1))
    response = resubmit(
        client, tutor, made.id, 2, minutes=58, reason="  Añadí la tarea  "
    )

    assert code_of(response) == OK
    closeout = response.json()["closeout"]
    assert (closeout["status"], closeout["version"]) == ("submitted", 3)
    assert closeout["review"]["tutor_note"] == "Añadí la tarea"
    assert closeout["review"]["last_submitted_at"] == (
        (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    )
    assert closeout["actual_minutes"] == 58
    assert minutes_of(url, made.id) == 58
    [row] = closeout_rows(url, made.id)
    assert (row.submitted_at, row.status) == (NOW, "submitted")
    [request] = approval_rows(url, row.id)
    assert (request.status, request.reason) == ("pending", None)
    [event] = [
        e
        for e in events(url, record_type="session_closeouts", record_id=row.id)
        if e.action == "academics.closeout_resubmitted"
    ]
    assert event.actor_user_id == made.tutor.id
    assert event.details == {
        "from_status": "changes_requested",
        "reason": "Añadí la tarea",
    }
    # No notification for a resubmission; the inbox lists it again.
    staff = sign_in(client, owner.username)  # after the clock moved
    sent = run_sql(
        url,
        "SELECT 1 FROM notifications WHERE params->>'class_session_id' = :s",
        {"s": made.id},
    )
    assert len(sent) == 1
    subject = inbox_subject(client, staff, request.id)
    assert (subject["kind_of_review"], subject["approved_minutes"]) == (
        "resubmission",
        None,
    )
    # Approving snapshots the marks as they are now.
    approved_now = approve(client, staff, made.id, 3)
    assert code_of(approved_now) == OK
    [revision] = revisions(url, row.id)
    assert (revision.approved_minutes, revision.attendance) == (
        58,
        {str(student): "late"},
    )
    del body


# --- C3: staff corrections ------------------------------------------------------------


def test_a_correction_while_submitted_then_approve(module_seeded_url, client, owner):
    url = module_seeded_url
    made = lesson(url)
    submitted(client, made)
    staff = sign_in(client, owner.username)

    response = correct_closeout(client, staff, made.id, 1, actual_minutes=50)

    assert code_of(response) == OK
    closeout = response.json()["closeout"]
    assert (closeout["status"], closeout["version"]) == ("submitted", 2)
    assert closeout["actual_minutes"] == 50
    assert minutes_of(url, made.id) == 50
    [row] = closeout_rows(url, made.id)
    [fixed] = corrections(url, row.id)
    assert (fixed.fields, fixed.previous_minutes, fixed.new_minutes) == (
        ["actual_minutes"],
        55,
        50,
    )
    assert (fixed.closeout_status, fixed.approval_id, fixed.reason) == (
        "submitted",
        None,
        "Terminó antes",
    )
    assert fixed.corrected_by_user_id == owner.id
    assert not revisions(url, row.id)
    [request] = approval_rows(url, row.id)
    assert request.status == "pending"
    [event] = [
        e
        for e in events(url, record_type="session_closeouts", record_id=row.id)
        if e.action == "academics.closeout_corrected"
    ]
    assert event.details == {
        "correction_id": str(fixed.id),
        "fields": ["actual_minutes"],
        "previous_minutes": 55,
        "new_minutes": 50,
        "reason": "Terminó antes",
        "approval_id": None,
    }
    [note] = notes(url, made.tutor.id, "academics.closeout_corrected")
    assert note.params["approved_minutes"] is None
    assert closeout["corrections"] == [
        {
            "id": str(fixed.id),
            "fields": ["actual_minutes"],
            "previous_minutes": 55,
            "new_minutes": 50,
            "reason": "Terminó antes",
            "corrected_by": {"display_name": "Dueña Centro"},
            "created_at": closeout["corrections"][0]["created_at"],
            "approval_id": None,
        }
    ]

    assert code_of(approve(client, staff, made.id, 2)) == OK
    [revision] = revisions(url, row.id)
    assert revision.approved_minutes == 50


def test_a_correction_of_an_approved_closeout_supersedes(
    module_seeded_url, client, owner, coordinator
):
    url = module_seeded_url
    made = lesson(url)
    body = approved(client, made, coordinator)
    staff = sign_in(client, owner.username)

    response = correct_closeout(
        client,
        staff,
        made.id,
        version_of(body),
        summary={"topics": "Fracciones y decimales", "homework": None},
    )

    assert code_of(response) == OK
    closeout = response.json()["closeout"]
    assert (closeout["status"], closeout["version"]) == ("approved", 3)
    assert closeout["summary"]["topics"] == "Fracciones y decimales"
    assert closeout["summary"]["homework"] is None
    assert closeout["summary"]["performance"] == SUMMARY["performance"]
    first, second = revisions(url, closeout["id"])
    assert (first.revision, first.approved_minutes, first.source) == (1, 55, "review")
    assert first.approved_by_user_id == coordinator.id
    assert (second.revision, second.supersedes_approval_id, second.source) == (
        2,
        first.id,
        "staff_correction",
    )
    assert (second.approved_minutes, second.approved_by_user_id) == (55, owner.id)
    [row] = closeout_rows(url, made.id)
    assert row.current_approval_id == second.id
    [fixed] = corrections(url, row.id)
    assert (fixed.fields, fixed.closeout_status, fixed.approval_id) == (
        ["topics", "homework"],
        "approved",
        second.id,
    )
    assert (fixed.previous_minutes, fixed.new_minutes) == (None, None)
    [request] = approval_rows(url, row.id)
    assert request.status == "approved"
    [note] = notes(url, made.tutor.id, "academics.closeout_corrected")
    assert note.params["approved_minutes"] == 55
    assert [r["revision"] for r in closeout["revisions"]] == [2, 1]
    assert closeout["approval"]["revision"] == 2
    # Revision 1 never changes.
    for statement in (
        "UPDATE session_closeout_approvals SET approved_minutes = 1 WHERE id = :id",
        "DELETE FROM session_closeout_approvals WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(url, statement, {"id": first.id})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"

    # A second correction chains revision 3 on revision 2.
    again = correct_closeout(client, staff, made.id, 3, actual_minutes=45)
    assert code_of(again) == OK
    *_, third = revisions(url, row.id)
    assert (third.revision, third.supersedes_approval_id, third.approved_minutes) == (
        3,
        second.id,
        45,
    )


def test_a_correction_while_changes_requested_is_with_the_tutor(
    module_seeded_url, client, owner
):
    made = lesson(module_seeded_url)
    body = sent_back(client, made, owner)

    response = correct_closeout(
        client,
        sign_in(client, owner.username),
        made.id,
        version_of(body),
        actual_minutes=50,
    )

    assert code_of(response) == WITH_TUTOR


def test_a_correction_must_change_something(module_seeded_url, client, owner):
    made = lesson(module_seeded_url)
    submitted(client, made)
    staff = sign_in(client, owner.username)

    for body in (
        {"actual_minutes": 55},
        {"summary": {"topics": SUMMARY["topics"]}},
        {"summary": {}},
        {},
    ):
        assert code_of(correct_closeout(client, staff, made.id, 1, **body)) == UNCHANGED


def test_a_correction_checks_the_summary_and_rolls_back(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    made = lesson(url)
    submitted(client, made)
    staff = sign_in(client, owner.username)
    [before] = run_sql(url, "SELECT count(*) AS n FROM notifications")

    response = correct_closeout(
        client, staff, made.id, 1, actual_minutes=40, summary={"topics": "  "}
    )

    assert code_of(response) == INVALID
    assert response.json()["errors"][0]["path"] == ["body", "summary", "topics"]
    assert response.json()["errors"][0]["code"] == "summary_required"
    [row] = closeout_rows(url, made.id)
    assert (row.version, row.topics) == (1, SUMMARY["topics"])
    assert minutes_of(url, made.id) == 55
    assert not corrections(url, row.id)
    [after] = run_sql(url, "SELECT count(*) AS n FROM notifications")
    assert after.n == before.n


@pytest.mark.parametrize(
    ("body", "where"),
    [
        ({"actual_minutes": 721}, ["body", "actual_minutes"]),
        ({"actual_minutes": -1}, ["body", "actual_minutes"]),
        ({"summary": {"topics": "x" * 2001}}, ["body", "summary", "topics"]),
        ({"reason": " "}, ["body", "reason"]),
        ({"reason": "x" * 1001}, ["body", "reason"]),
        ({"room": "A"}, ["body", "room"]),
    ],
)
def test_the_correction_body_422s(module_seeded_url, client, owner, body, where):
    made = lesson(module_seeded_url)
    submitted(client, made)

    response = correct_closeout(
        client, sign_in(client, owner.username), made.id, 1, **body
    )

    assert code_of(response) == INVALID
    assert [e["path"] for e in response.json()["errors"]] == [where]


def test_an_all_absent_closeout_is_corrected_to_zero(module_seeded_url, client, owner):
    url = module_seeded_url
    made = lesson(url)
    cookie, version = marked(client, made, ["absent"])
    empty = {"topics": None, "performance": None, "homework": None, "next_steps": None}
    response = submit(client, cookie, made.id, version, minutes=60, summary=empty)
    assert code_of(response) == (201, None)
    staff = sign_in(client, owner.username)

    response = correct_closeout(
        client, staff, made.id, 1, actual_minutes=0, reason="Nadie asistió"
    )
    assert code_of(response) == OK
    assert code_of(approve(client, staff, made.id, 2)) == OK

    [row] = closeout_rows(url, made.id)
    [revision] = revisions(url, row.id)
    assert (revision.approved_minutes, revision.attendance) == (
        0,
        {str(made.students[0]): "absent"},
    )


# --- E4 / D3: the tutor's correction request ------------------------------------------


def test_a_correction_request_keeps_the_approval_in_force(
    module_seeded_url, client, owner, coordinator
):
    url = module_seeded_url
    made = lesson(url)
    body = approved(client, made, coordinator)
    tutor = sign_in(client, made.tutor.username)
    version = version_of(body)

    missing = resubmit(client, tutor, made.id, version, minutes=50)
    assert code_of(missing) == INVALID
    assert missing.json()["errors"] == [
        {
            "path": ["body", "reason"],
            "code": "reason_required",
            "message": "Give a reason.",
        }
    ]
    assert code_of(resubmit(client, tutor, made.id, version, reason="X")) == UNCHANGED

    response = resubmit(
        client, tutor, made.id, version, minutes=50, reason="Salimos 5 minutos antes"
    )

    assert code_of(response) == OK
    closeout = response.json()["closeout"]
    assert (closeout["status"], closeout["actual_minutes"]) == ("submitted", 50)
    assert closeout["review"]["tutor_note"] == "Salimos 5 minutos antes"
    # D3: the approved revision stays current, untouched.
    [first] = revisions(url, closeout["id"])
    [row] = closeout_rows(url, made.id)
    assert (row.current_approval_id, first.approved_minutes) == (first.id, 55)
    assert closeout["approval"]["approved_minutes"] == 55
    [request] = approval_rows(url, row.id)
    assert request.status == "pending"
    staff = sign_in(client, owner.username)
    subject = inbox_subject(client, staff, request.id)
    assert (subject["kind_of_review"], subject["approved_minutes"]) == (
        "correction",
        55,
    )
    staff_view = read(client, staff, made.id).json()
    assert staff_view["closeout"]["review"]["tutor_note"] == "Salimos 5 minutos antes"
    assert staff_view["can_review"] is True
    [event] = [
        e
        for e in events(url, record_type="session_closeouts", record_id=row.id)
        if e.action == "academics.closeout_resubmitted"
    ]
    assert event.details == {
        "from_status": "approved",
        "reason": "Salimos 5 minutos antes",
    }

    # Changes requested on the correction, resubmitted, approved: revision 2.
    back = send_back(client, staff, made.id, closeout["version"])
    assert code_of(back) == OK
    assert back.json()["closeout"]["approval"]["approved_minutes"] == 55
    again = resubmit(
        client, tutor, made.id, back.json()["closeout"]["version"], minutes=50
    )
    assert code_of(again) == OK
    assert code_of(approve(client, staff, made.id, version_of(again.json()))) == OK
    first, second = revisions(url, row.id)
    assert (second.supersedes_approval_id, second.approved_minutes) == (first.id, 50)
    assert first.approved_minutes == 55


def test_a_tutor_put_while_submitted_is_under_review(module_seeded_url, client):
    made = lesson(module_seeded_url)
    submitted(client, made)

    response = resubmit(client, sign_in(client, made.tutor.username), made.id, 1)

    assert code_of(response) == UNDER_REVIEW


def test_the_put_body_422s(module_seeded_url, client):
    made = lesson(module_seeded_url)
    submitted(client, made)
    tutor = sign_in(client, made.tutor.username)

    for extra, where in (
        ({"minutes": 721}, ["body", "actual_minutes"]),
        ({"reason": "x" * 1001}, ["body", "reason"]),
        ({"note": "x"}, ["body", "note"]),
    ):
        response = resubmit(client, tutor, made.id, 1, **extra)
        assert code_of(response) == INVALID
        assert [e["path"] for e in response.json()["errors"]] == [where]


def test_the_put_order(module_seeded_url, client, owner):
    url = module_seeded_url
    made = lesson(url)
    body = sent_back(client, made, owner)
    tutor = sign_in(client, made.tutor.username)
    version = version_of(body)

    # Version before status; status before attendance and summary.
    assert code_of(resubmit(client, tutor, made.id, version + 1)) == VERSION_CONFLICT
    # A participant without a mark (one added after the closeout).
    student = run_sql(
        url,
        "SELECT student_id FROM class_session_participants WHERE class_session_id = :s",
        {"s": made.id},
    )[0].student_id
    extra = lesson(url, kind="group", students=1).students[0]
    run_sql(
        url,
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " VALUES (:s, :st)",
        {"s": made.id, "st": extra},
    )
    incomplete = resubmit(client, tutor, made.id, version, summary={"topics": None})
    assert code_of(incomplete) == INCOMPLETE
    assert incomplete.json()["details"] == {"missing_student_ids": [str(extra)]}
    attendance = get(client, tutor, f"/api/v1/class-sessions/{made.id}/attendance")
    assert (
        code_of(
            mark(
                client, tutor, made.id, attendance.json()["version"], (extra, "present")
            )
        )
        == OK
    )
    # Then the summary.
    response = resubmit(client, tutor, made.id, version, summary={"topics": None})
    assert code_of(response) == INVALID
    assert {e["path"][-1] for e in response.json()["errors"]} == {
        "topics",
        "performance",
        "next_steps",
    }
    [row] = closeout_rows(url, made.id)
    assert (row.status, row.version) == ("changes_requested", version)
    del student


# --- E6: attendance and review --------------------------------------------------------


def test_the_tutor_marks_attendance_only_while_changes_are_requested(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    made = lesson(url)
    body = submitted(client, made)
    tutor = sign_in(client, made.tutor.username)
    staff = sign_in(client, owner.username)
    [student] = made.students

    def attendance() -> dict[str, Any]:
        response = get(client, tutor, f"/api/v1/class-sessions/{made.id}/attendance")
        assert code_of(response) == OK
        result: dict[str, Any] = response.json()
        return result

    # submitted: locked (GET and PUT).
    shown = attendance()
    assert (shown["can_mark"], shown["block"]) == (False, "attendance_locked")
    assert (
        code_of(mark(client, tutor, made.id, shown["version"], (student, "late")))
        == LOCKED
    )
    # changes_requested: open.
    back = send_back(client, staff, made.id, version_of(body))
    shown = attendance()
    assert (shown["can_mark"], shown["block"]) == (True, None)
    assert (
        code_of(mark(client, tutor, made.id, shown["version"], (student, "late"))) == OK
    )
    # resubmitted (submitted) then approved: locked again.
    assert code_of(resubmit(client, tutor, made.id, version_of(back.json()))) == OK
    shown = attendance()
    assert (shown["can_mark"], shown["block"]) == (False, "attendance_locked")
    assert code_of(approve(client, staff, made.id, 3)) == OK
    shown = attendance()
    assert (shown["can_mark"], shown["block"]) == (False, "attendance_locked")
    assert (
        code_of(mark(client, tutor, made.id, shown["version"], (student, "absent")))
        == LOCKED
    )


def test_a_staff_attendance_correction_after_approval_is_reapproved(
    module_seeded_url, client, owner, coordinator
):
    url = module_seeded_url
    made = lesson(url)
    body = approved(client, made, coordinator)
    staff = sign_in(client, owner.username)
    [student] = made.students
    # Nothing changed yet: a no-field correction is closeout_unchanged.
    assert (
        code_of(correct_closeout(client, staff, made.id, version_of(body))) == UNCHANGED
    )

    fixed = correct(
        client, staff, made.id, body["version"], student, "late", "Recepción"
    )
    assert code_of(fixed) == (201, None)
    shown = read(client, staff, made.id).json()
    assert shown["closeout"]["attendance_changed_since_approval"] is True

    response = correct_closeout(
        client, staff, made.id, version_of(body), reason="Asistencia corregida"
    )

    assert code_of(response) == OK
    closeout = response.json()["closeout"]
    assert closeout["attendance_changed_since_approval"] is False
    first, second = revisions(url, closeout["id"])
    assert first.attendance == {str(student): "present"}
    assert (second.attendance, second.supersedes_approval_id) == (
        {str(student): "late"},
        first.id,
    )
    [row] = closeout_rows(url, made.id)
    [reapproval] = corrections(url, row.id)
    assert (reapproval.fields, reapproval.approval_id) == ([], second.id)


def test_a_staff_attendance_correction_while_submitted_is_snapshotted(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    made = lesson(url)
    body = submitted(client, made)
    staff = sign_in(client, owner.username)
    [student] = made.students
    fixed = correct(client, staff, made.id, body["version"], student, "late", "Tarde")
    assert code_of(fixed) == (201, None)

    assert code_of(approve(client, staff, made.id, version_of(body))) == OK

    [row] = closeout_rows(url, made.id)
    [revision] = revisions(url, row.id)
    assert revision.attendance == {str(student): "late"}


# --- C1 / C2 orders, generic routes ---------------------------------------------------


def test_anonymous_gets_401(module_seeded_url, client):
    made = lesson(module_seeded_url)
    client.cookies.clear()
    for action in ("approve", "request-changes", "corrections"):
        assert client.post(f"{path(made.id)}/{action}", json={}).status_code == 401
    assert client.put(path(made.id), json={}).status_code == 401


@pytest.mark.parametrize("role", [FINANCE_STAFF, TUTOR, GUARDIAN, "student"])
def test_non_reviewers_get_403_before_any_read(module_seeded_url, client, role):
    made = lesson(module_seeded_url)
    if role == "student":
        cookie = sign_in(client, student_account(module_seeded_url)[0].username)
    else:
        cookie = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)
    for target in (made.id, uuid.uuid4()):
        for action, body in (
            ("approve", {"version": 1}),
            ("request-changes", {"version": 1, "reason": REASON}),
            ("corrections", {"version": 1, "actual_minutes": 1, "reason": REASON}),
        ):
            assert code_of(decide(client, cookie, target, action, body)) == DENIED


def test_the_tutor_cannot_review_their_own(module_seeded_url, client):
    made = lesson(module_seeded_url)
    submitted(client, made)
    tutor = sign_in(client, made.tutor.username)

    assert code_of(approve(client, tutor, made.id, 1)) == DENIED


def test_a_reviewer_gets_the_same_404_without_a_closeout(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    staff = sign_in(client, owner.username)
    waiting = lesson(url)
    completed = lesson(url, state="completed")
    for action, body in (
        ("approve", {"version": 1}),
        ("request-changes", {"version": 1, "reason": REASON}),
        ("corrections", {"version": 1, "actual_minutes": 1, "reason": REASON}),
    ):
        unknown = decide(client, staff, uuid.uuid4(), action, body)
        for target in (waiting.id, completed.id):
            assert_same_not_found(decide(client, staff, target, action, body), unknown)


def test_the_review_order(module_seeded_url, client, owner, coordinator):
    url = module_seeded_url
    made = lesson(url)
    submitted(client, made)
    staff = sign_in(client, coordinator.username)

    assert code_of(decide(client, staff, made.id, "approve", {})) == INVALID
    assert code_of(approve(client, staff, made.id, 2)) == VERSION_CONFLICT
    missing = decide(client, staff, made.id, "request-changes", {"version": 1})
    assert code_of(missing) == INVALID
    assert missing.json()["errors"][0]["code"] == "reason_required"
    too_long = send_back(client, staff, made.id, 1, reason="x" * 1001)
    assert too_long.json()["errors"][0]["code"] == "reason_too_long"
    assert code_of(approve(client, staff, made.id, 1)) == OK
    # Version before status.
    assert code_of(approve(client, staff, made.id, 1)) == VERSION_CONFLICT
    assert code_of(approve(client, staff, made.id, 2)) == NOT_SUBMITTED
    assert code_of(send_back(client, staff, made.id, 2)) == NOT_SUBMITTED
    [row] = closeout_rows(url, made.id)
    assert row.version == 2


@pytest.mark.parametrize("action", ["approve", "reject", "request-changes"])
def test_the_generic_routes_point_to_the_review_page(
    module_seeded_url, client, owner, action
):
    url = module_seeded_url
    made = lesson(url)
    body = submitted(client, made)
    staff = sign_in(client, owner.username)
    request_id = body["closeout"]["approval_request_id"]

    response = post(
        client,
        staff,
        f"/api/v1/approval-requests/{request_id}/{action}",
        {} if action == "approve" else {"reason": REASON},
    )

    assert code_of(response) == ROUTE_REQUIRED
    assert response.json()["details"] == {
        "review_path": f"/schedule/sessions/{made.id}"
    }
    [row] = closeout_rows(url, made.id)
    assert (row.status, row.version) == ("submitted", 1)
    [request] = approval_rows(url, row.id)
    assert request.status == "pending"


# --- E7: self-review ------------------------------------------------------------------


def test_an_administrator_cannot_review_their_own_closeout(module_seeded_url, client):
    url = module_seeded_url
    admin_tutor = create_user(
        url, roles=(ADMINISTRATOR, TUTOR), display_name="Admin Tutora"
    )
    made = lesson(url, tutor=admin_tutor)
    submitted(client, made)
    cookie = sign_in(client, admin_tutor.username)

    shown = read(client, cookie, made.id).json()
    assert (shown["can_review"], shown["can_correct"]) == (False, False)
    # Self-review comes before the version check.
    assert code_of(approve(client, cookie, made.id, 9)) == SELF_REVIEW
    assert code_of(send_back(client, cookie, made.id, 1)) == SELF_REVIEW
    assert (
        code_of(correct_closeout(client, cookie, made.id, 1, actual_minutes=50))
        == SELF_REVIEW
    )
    [row] = closeout_rows(url, made.id)
    assert (row.status, row.version) == ("submitted", 1)


def test_the_owner_may_review_their_own_closeout(module_seeded_url, client):
    url = module_seeded_url
    owner_tutor = create_user(url, roles=(OWNER, TUTOR))
    made = lesson(url, tutor=owner_tutor)
    submitted(client, made)
    cookie = sign_in(client, owner_tutor.username)

    assert read(client, cookie, made.id).json()["can_review"] is True
    assert code_of(approve(client, cookie, made.id, 1)) == OK


# --- E1: Finance reads minutes and metadata only --------------------------------------


def test_finance_reads_no_reasons_or_summary(
    module_seeded_url, client, owner, coordinator, finance
):
    url = module_seeded_url
    made = lesson(url)
    body = approved(client, made, coordinator)
    staff = sign_in(client, owner.username)
    assert (
        code_of(
            correct_closeout(
                client, staff, made.id, version_of(body), actual_minutes=50
            )
        )
        == OK
    )
    tutor = sign_in(client, made.tutor.username)
    assert code_of(resubmit(client, tutor, made.id, 3, minutes=45, reason="Nota")) == OK
    assert code_of(send_back(client, staff, made.id, 4)) == OK

    shown = read(client, sign_in(client, finance.username), made.id).json()

    closeout = shown["closeout"]
    assert (closeout["summary"], closeout["summary_hidden"]) == (None, True)
    assert closeout["review"]["reason"] is None
    assert closeout["review"]["tutor_note"] is None
    assert closeout["review"]["status"] == "changes_requested"
    assert [c["reason"] for c in closeout["corrections"]] == [None]
    assert closeout["corrections"][0]["new_minutes"] == 50
    assert [r["approved_minutes"] for r in closeout["revisions"]] == [50, 55]
    assert closeout["actual_minutes"] == 45
    assert not any(
        shown[flag]
        for flag in (
            "can_review",
            "can_correct",
            "can_resubmit",
            "can_request_correction",
        )
    )
    full = read(client, staff, made.id).json()["closeout"]
    assert full["review"]["reason"] == REASON
    assert full["corrections"][0]["reason"] == "Terminó antes"


# --- I2: the tutor PUT record policy --------------------------------------------------


def test_the_put_hides_the_closeout_from_everyone_else(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    made = lesson(url)
    body = sent_back(client, made, owner)
    version = version_of(body)
    unknown = uuid.uuid4()
    other_tutor = create_user(url, roles=(TUTOR,))
    family = guardian_with_child(url)
    learner, _ = student_account(url)

    for user in (other_tutor, family.user, learner):
        cookie = sign_in(client, user.username)
        assert_same_not_found(
            resubmit(client, cookie, made.id, version),
            resubmit(client, cookie, unknown, version),
        )
    staff = sign_in(client, owner.username)
    assert code_of(resubmit(client, staff, made.id, version)) == TUTOR_ONLY

    # The original tutor after a substitution: 404; the current tutor who
    # didn't submit it: 403.
    substitute = create_user(url, roles=(TUTOR,))
    run_sql(
        url,
        "UPDATE class_sessions SET tutor_user_id = :t WHERE id = :s",
        {"t": substitute.id, "s": made.id},
    )
    original = sign_in(client, made.tutor.username)
    assert_same_not_found(
        resubmit(client, original, made.id, version),
        resubmit(client, original, unknown, version),
    )
    taker = sign_in(client, substitute.username)
    assert code_of(resubmit(client, taker, made.id, version)) == TUTOR_ONLY
    assert read(client, taker, made.id).json()["can_resubmit"] is False


def test_a_tutor_who_lost_the_role_gets_404_and_reviewers_still_decide(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR, GUARDIAN))
    made = lesson(url, tutor=tutor)
    body = submitted(client, made)
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": tutor.id},
    )
    staff = sign_in(client, owner.username)
    assert code_of(send_back(client, staff, made.id, version_of(body))) == OK
    cookie = sign_in(client, tutor.username)

    assert_same_not_found(
        resubmit(client, cookie, made.id, 2), resubmit(client, cookie, uuid.uuid4(), 2)
    )
    assert notes(url, tutor.id, "academics.closeout_changes_requested")


# --- E2: the statement budget ---------------------------------------------------------


@pytest.mark.parametrize(("students", "extra_revisions"), [(1, 0), (20, 4)])
def test_the_read_takes_at_most_10_statements(
    module_seeded_url, client, owner, coordinator, students, extra_revisions
):
    url = module_seeded_url
    made = lesson(url, kind="group", students=students)
    body = approved(client, made, coordinator)
    staff = sign_in(client, owner.username)
    version = version_of(body)
    for minutes in range(extra_revisions):
        response = correct_closeout(
            client, staff, made.id, version, actual_minutes=40 + minutes
        )
        assert code_of(response) == OK
        version = version_of(response.json())

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
        assert result.closeout is not None
        assert len(result.closeout.revisions) == 1 + extra_revisions
        assert statements <= 10, statements
