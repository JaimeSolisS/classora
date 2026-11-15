"""Overdue closeouts and closeout on behalf of a tutor (#301 B, C, D, E, F,
H1, H2) with real logins through the real app: ``GET
/api/v1/closeouts/overdue``, ``POST`` / ``PUT …/closeout/on-behalf``, the
GET additions and the #78 review rules for on-behalf submissions.

Success for individual, group and course sessions (the closeout of the
tutor with the marker, the approval request of the tutor, the audit event,
the notification, the session ``completed``), the all-absent no-show,
attendance marked through #76 corrections, a tutor who lost the role or
was deactivated, every C2 and D2 step, D3 (a takeover leaves the approval
in force), the self-review and request-changes rules, the tutor ``PUT``
that clears the marker, the GET flags per role, the list (filters, order,
pagination, exclusions, canaries, statement budget) and the IDOR cases.
"""

import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text

from app.core.pagination import OffsetParams
from app.modules.academics import service as academics
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    TUTOR,
)
from app.workflows import closeout_on_behalf as on_behalf
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
from tests.integration.session_request_support import student_account
from tests.integration.test_attendance import (
    Lesson,
    correct,
    lesson,
    mark,
    principal_of,
)
from tests.integration.test_class_session_create import new_student
from tests.integration.test_closeout_review import (
    approve,
    correct_closeout,
    inbox_subject,
    notes,
    resubmit,
    revisions,
    send_back,
    version_of,
)
from tests.integration.test_session_closeout import (
    NOW,
    SUMMARY,
    approval_rows,
    closeout_rows,
    marked,
    path,
    read,
    session_row,
    submitted,
)

OK = (200, None)
CREATED = (201, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
DENIED = (403, "permission_denied")
VERSION_CONFLICT = (409, "version_conflict")
OWN_SESSION = (403, "closeout_on_behalf_own_session")
SELF_REVIEW = (403, "closeout_self_review")
ALREADY = (409, "closeout_already_submitted")
NOT_CLOSABLE = (409, "session_not_closable")
NOT_ENDED = (409, "session_not_ended")
NOT_WITH_TUTOR = (409, "closeout_not_with_tutor")
ON_BEHALF = (409, "closeout_submitted_on_behalf")
INCOMPLETE = (409, "closeout_attendance_incomplete")

REASON = "El tutor dejó el centro"
ENDED = NOW - timedelta(minutes=90)
"""A start whose 60-minute class ended 30 minutes ago."""
OVERDUE = "/api/v1/closeouts/overdue"
ON_BEHALF_TYPE = "academics.closeout_submitted_on_behalf"


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
def admin(module_seeded_url: str) -> CreatedUser:
    return create_user(
        module_seeded_url, roles=(ADMINISTRATOR,), display_name="Administración"
    )


@pytest.fixture(scope="module")
def finance(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(FINANCE_STAFF,))


# --- Helpers --------------------------------------------------------------------------


def on_behalf_path(session_id: Any) -> str:
    return f"{path(session_id)}/on-behalf"


def body_of(
    version: int, *, minutes: Any = 55, summary: Any = None, **extra: Any
) -> dict[str, Any]:
    return {
        "version": version,
        "actual_minutes": minutes,
        "summary": SUMMARY if summary is None else summary,
        "reason": REASON,
        **extra,
    }


def submit_for(
    client: TestClient, cookie: Any, session_id: Any, version: int, **kwargs: Any
) -> Response:
    return post(client, cookie, on_behalf_path(session_id), body_of(version, **kwargs))


def take_over(
    client: TestClient, cookie: Any, session_id: Any, version: int, **kwargs: Any
) -> Response:
    client.cookies.clear()
    return client.put(
        on_behalf_path(session_id),
        json=body_of(version, **kwargs),
        headers=signed_headers(cookie),
    )


def ended_lesson(url: str, **kwargs: Any) -> Lesson:
    """A confirmed session that ended 30 minutes ago, without marks."""
    return lesson(url, starts_at=kwargs.pop("starts_at", ENDED), **kwargs)


def cancelled_lesson(url: str, **kwargs: Any) -> Lesson:
    """A session cancelled after it was confirmed."""
    made = lesson(url, **kwargs)
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'cancelled', cancelled_at = :at,"
        " cancellation_reason = 'Cerrado' WHERE id = :id",
        {"at": NOW, "id": made.id},
    )
    return made


def ready(client: TestClient, made: Lesson, **kwargs: Any) -> int:
    """Every participant marked by the tutor; the session's version."""
    _, version = marked(client, made, **kwargs)
    return version


def submitted_for(
    client: TestClient, made: Lesson, reviewer: CreatedUser
) -> dict[str, Any]:
    version = ready(client, made)
    response = submit_for(client, sign_in(client, reviewer.username), made.id, version)
    assert code_of(response) == CREATED, response.text
    result: dict[str, Any] = response.json()
    return result


def remove_tutor_role(url: str, user_id: Any) -> None:
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": user_id},
    )


def on_behalf_events(url: str, closeout_id: Any) -> list[Any]:
    return [
        e
        for e in events(url, record_type="session_closeouts", record_id=closeout_id)
        if e.action == ON_BEHALF_TYPE
    ]


def overdue(client: TestClient, cookie: Any, **params: Any) -> Response:
    return get(client, cookie, OVERDUE, **params)


def zone_of(url: str) -> ZoneInfo:
    [row] = run_sql(url, "SELECT time_zone FROM business_settings")
    return ZoneInfo(row.time_zone)


# --- C: submit on behalf --------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "students"), [("individual", 1), ("group", 3), ("course", 2)]
)
def test_a_reviewer_closes_out_for_the_tutor(
    module_seeded_url, client, coordinator, kind, students
):
    url = module_seeded_url
    made = ended_lesson(url, kind=kind, students=students)
    version = ready(client, made)
    staff = sign_in(client, coordinator.username)
    shown = read(client, staff, made.id).json()
    assert (shown["can_submit_on_behalf"], shown["can_take_over"]) == (True, False)

    response = submit_for(client, staff, made.id, version, summary=SUMMARY)

    assert code_of(response) == CREATED
    body = response.json()
    assert (body["state"], body["version"]) == ("completed", version + 1)
    closeout = body["closeout"]
    assert closeout["status"] == "submitted"
    assert closeout["submitted_by"] == {"display_name": "Tutora Lista"}
    assert closeout["on_behalf"] == {
        "by": {"display_name": "Coordinación Académica"},
        "reason": REASON,
        "submitted_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert closeout["summary"] == SUMMARY
    # E5: the submitter doesn't review it; nobody sends it back.
    assert (body["can_review"], body["can_correct"]) == (False, False)
    assert (body["can_request_changes"], body["can_submit_on_behalf"]) == (
        False,
        False,
    )
    assert session_row(url, made.id).state == "completed"
    [row] = closeout_rows(url, made.id)
    assert (row.tutor_user_id, row.on_behalf_by_user_id, row.on_behalf_reason) == (
        made.tutor.id,
        coordinator.id,
        REASON,
    )
    assert (row.submitted_at, row.last_submitted_at) == (NOW, NOW)
    [request] = approval_rows(url, row.id)
    assert (request.status, request.requester_user_id) == ("pending", made.tutor.id)
    [event] = on_behalf_events(url, row.id)
    assert event.actor_user_id == coordinator.id
    assert event.details == {
        "class_session_id": made.id,
        "mode": "submit",
        "reason": REASON,
    }
    [note] = notes(url, made.tutor.id, ON_BEHALF_TYPE)
    assert note.params == {
        "class_session_id": made.id,
        "starts_at": ENDED.isoformat().replace("+00:00", "Z"),
        "subject_name": body["subject"]["name"],
    }
    # E4: the inbox names who submitted it on the tutor's behalf.
    subject = inbox_subject(client, staff, request.id)
    assert subject["on_behalf_by_display_name"] == "Coordinación Académica"
    assert subject["kind_of_review"] == "first"
    # The tutor sees the marker and the reason; the tutor's PUT is locked.
    tutor_view = read(client, sign_in(client, made.tutor.username), made.id).json()
    assert tutor_view["closeout"]["on_behalf"]["reason"] == REASON
    assert tutor_view["can_resubmit"] is False


def test_an_all_absent_class_is_closed_out_with_zero_minutes(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    made = ended_lesson(url, kind="group", students=2)
    version = ready(client, made, statuses=["absent", "absent"])

    response = submit_for(
        client, sign_in(client, owner.username), made.id, version, minutes=0, summary={}
    )

    assert code_of(response) == CREATED
    assert response.json()["closeout"]["actual_minutes"] == 0


def test_missing_marks_are_made_with_staff_corrections_first(
    module_seeded_url, client, coordinator
):
    # E4: the body carries no attendance; #76 corrections mark the students.
    url = module_seeded_url
    made = ended_lesson(url, kind="group", students=2)
    staff = sign_in(client, coordinator.username)

    response = submit_for(client, staff, made.id, 1)

    assert code_of(response) == INCOMPLETE
    assert response.json()["details"]["missing_student_ids"] == sorted(
        str(s) for s in made.students
    )
    assert not closeout_rows(url, made.id)
    version = 1
    for student in made.students:
        corrected = correct(client, staff, made.id, version, student, "present")
        assert code_of(corrected) == CREATED, corrected.text
        version = int(corrected.json()["version"])
    assert code_of(submit_for(client, staff, made.id, version)) == CREATED


@pytest.mark.parametrize("change", ["lost_role", "suspended"])
def test_a_tutor_who_cannot_act_is_closed_out_and_notified(
    module_seeded_url, client, owner, change
):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR, GUARDIAN), display_name="Tutor Ido")
    made = ended_lesson(url, tutor=tutor)
    version = ready(client, made)
    if change == "lost_role":
        remove_tutor_role(url, tutor.id)
    else:
        run_sql(
            url, "UPDATE users SET state = 'suspended' WHERE id = :id", {"id": tutor.id}
        )
    staff = sign_in(client, owner.username)
    [item] = overdue(client, staff, tutor_user_id=str(tutor.id)).json()["items"]
    assert item["tutor"] == {
        "user_id": str(tutor.id),
        "display_name": "Tutor Ido",
        "can_submit": False,
    }

    assert code_of(submit_for(client, staff, made.id, version)) == CREATED

    [row] = closeout_rows(url, made.id)
    assert row.tutor_user_id == tutor.id
    assert notes(url, tutor.id, ON_BEHALF_TYPE)
    assert overdue(client, staff, tutor_user_id=str(tutor.id)).json()["total"] == 0


def test_a_substituted_session_is_attributed_to_the_substitute(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    made = ended_lesson(url)
    substitute = create_user(url, roles=(TUTOR,))
    run_sql(
        url,
        "UPDATE class_sessions SET tutor_user_id = :t WHERE id = :s",
        {"t": substitute.id, "s": made.id},
    )
    staff = sign_in(client, owner.username)
    [student] = made.students
    corrected = correct(client, staff, made.id, 1, student, "present")
    assert code_of(corrected) == CREATED, corrected.text

    response = submit_for(client, staff, made.id, int(corrected.json()["version"]))

    assert code_of(response) == CREATED
    [row] = closeout_rows(url, made.id)
    assert row.tutor_user_id == substitute.id
    [request] = approval_rows(url, row.id)
    assert request.requester_user_id == substitute.id


# --- C2: the order --------------------------------------------------------------------


def test_anonymous_gets_401(module_seeded_url, client):
    made = ended_lesson(module_seeded_url)
    client.cookies.clear()
    assert client.post(on_behalf_path(made.id), json={}).status_code == 401
    assert client.put(on_behalf_path(made.id), json={}).status_code == 401
    assert client.get(OVERDUE).status_code == 401


@pytest.mark.parametrize("role", [FINANCE_STAFF, TUTOR, GUARDIAN, "student"])
def test_non_reviewers_get_403_before_any_read(module_seeded_url, client, role):
    url = module_seeded_url
    made = ended_lesson(url)
    if role == "student":
        cookie = sign_in(client, student_account(url)[0].username)
    else:
        cookie = sign_in(client, create_user(url, roles=(role,)).username)
    for target in (made.id, uuid.uuid4()):
        assert code_of(submit_for(client, cookie, target, 1)) == DENIED
        assert code_of(take_over(client, cookie, target, 1)) == DENIED
        # Even an invalid body: the permission comes first.
        assert code_of(post(client, cookie, on_behalf_path(target), {})) == DENIED
    assert code_of(overdue(client, cookie)) == DENIED


def test_the_tutor_of_record_gets_403(module_seeded_url, client):
    url = module_seeded_url
    made = ended_lesson(url)
    tutor = sign_in(client, made.tutor.username)

    assert code_of(submit_for(client, tutor, made.id, 1)) == DENIED
    assert read(client, tutor, made.id).json()["can_submit_on_behalf"] is False


@pytest.mark.parametrize(
    ("body", "where", "code"),
    [
        ({"reason": None}, ["body", "reason"], "reason_required"),
        ({"reason": "   "}, ["body", "reason"], "reason_required"),
        ({"reason": "x" * 1001}, ["body", "reason"], "reason_too_long"),
        ({"actual_minutes": 721}, ["body", "actual_minutes"], None),
        ({"extra": 1}, ["body", "extra"], None),
        ({"summary": {"topics": "x" * 2001}}, ["body", "summary", "topics"], None),
    ],
)
def test_the_body_422s(module_seeded_url, client, owner, body, where, code):
    url = module_seeded_url
    made = ended_lesson(url)
    staff = sign_in(client, owner.username)
    payload = body_of(1) | body
    if body.get("reason", "") is None:
        del payload["reason"]
    client.cookies.clear()
    put = client.put(
        on_behalf_path(made.id), json=payload, headers=signed_headers(staff)
    )

    for response in (post(client, staff, on_behalf_path(made.id), payload), put):
        assert code_of(response) == INVALID
        [error] = [e for e in response.json()["errors"] if e["path"] == where]
        if code is not None:
            assert error["code"] == code
    assert not closeout_rows(url, made.id)


def test_the_submit_order(module_seeded_url, client, owner):
    url = module_seeded_url
    staff = sign_in(client, owner.username)

    # 404 for an unknown session.
    assert code_of(submit_for(client, staff, uuid.uuid4(), 1)) == NOT_FOUND
    # The version before the block: a stale version on an unfinished class.
    running = lesson(url)
    assert code_of(submit_for(client, staff, running.id, 9)) == VERSION_CONFLICT
    # session_not_ended (in progress, and in the future).
    assert code_of(submit_for(client, staff, running.id, 1)) == NOT_ENDED
    future = lesson(url, starts_at=NOW + timedelta(days=1))
    assert code_of(submit_for(client, staff, future.id, 1)) == NOT_ENDED
    # session_not_closable with the state.
    for state in ("offered", "cancelled"):
        made = (
            cancelled_lesson(url, starts_at=ENDED)
            if state == "cancelled"
            else lesson(url, state=state, starts_at=ENDED)
        )
        response = submit_for(client, staff, made.id, 1)
        assert code_of(response) == NOT_CLOSABLE
        assert response.json()["details"] == {"state": state}
    # closeout_already_submitted: completed without a closeout (#299), or
    # already closed out by the tutor.
    done = lesson(url, state="completed", starts_at=ENDED)
    assert code_of(submit_for(client, staff, done.id, 1)) == ALREADY
    by_tutor = ended_lesson(url)
    body = submitted(client, by_tutor)
    assert code_of(submit_for(client, staff, by_tutor.id, body["version"])) == ALREADY


def test_a_reviewer_who_tutors_the_session_gets_403(module_seeded_url, client):
    url = module_seeded_url
    staff_tutor = create_user(url, roles=(ACADEMIC_COORDINATOR, TUTOR))
    made = ended_lesson(url, tutor=staff_tutor)
    cookie = sign_in(client, staff_tutor.username)
    assert read(client, cookie, made.id).json()["can_submit_on_behalf"] is False

    response = submit_for(client, cookie, made.id, 1)

    assert code_of(response) == OWN_SESSION
    assert not closeout_rows(url, made.id)
    # Own session comes before the other blocks (here: already completed).
    done = lesson(
        url, tutor=staff_tutor, state="completed", starts_at=ENDED - timedelta(days=1)
    )
    assert code_of(submit_for(client, cookie, done.id, 1)) == OWN_SESSION


def test_a_missing_summary_rolls_back(module_seeded_url, client, owner):
    url = module_seeded_url
    made = ended_lesson(url)
    version = ready(client, made)
    staff = sign_in(client, owner.username)

    response = submit_for(client, staff, made.id, version, summary={"topics": "x"})

    assert code_of(response) == INVALID
    paths = {tuple(e["path"]) for e in response.json()["errors"]}
    assert paths == {
        ("body", "summary", "performance"),
        ("body", "summary", "next_steps"),
    }
    assert (session_row(url, made.id).state, session_row(url, made.id).version) == (
        "confirmed",
        version,
    )
    assert not closeout_rows(url, made.id)
    assert not notes(url, made.tutor.id, ON_BEHALF_TYPE)


# --- D: take over ---------------------------------------------------------------------


def sent_back_by_tutor(
    client: TestClient, made: Lesson, reviewer: CreatedUser
) -> dict[str, Any]:
    body = submitted(client, made)
    response = send_back(
        client, sign_in(client, reviewer.username), made.id, version_of(body)
    )
    assert code_of(response) == OK, response.text
    result: dict[str, Any] = response.json()
    return result


def test_a_reviewer_takes_over_from_a_tutor_who_left(
    module_seeded_url, client, owner, coordinator
):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR, GUARDIAN), display_name="Tutora Ida")
    made = ended_lesson(url, tutor=tutor)
    body = sent_back_by_tutor(client, made, owner)
    remove_tutor_role(url, tutor.id)
    staff = sign_in(client, coordinator.username)
    shown = read(client, staff, made.id).json()
    assert (shown["can_take_over"], shown["can_correct"]) == (True, False)

    response = take_over(
        client, staff, made.id, version_of(body), minutes=50, summary=SUMMARY
    )

    assert code_of(response) == OK
    result = response.json()
    closeout = result["closeout"]
    assert (closeout["status"], closeout["version"]) == ("submitted", 3)
    assert closeout["actual_minutes"] == 50
    assert closeout["review"]["tutor_note"] is None
    assert closeout["on_behalf"]["by"] == {"display_name": "Coordinación Académica"}
    assert closeout["submitted_by"] == {"display_name": "Tutora Ida"}
    assert result["can_take_over"] is False
    [row] = closeout_rows(url, made.id)
    assert (row.tutor_user_id, row.on_behalf_by_user_id, row.on_behalf_reason) == (
        tutor.id,
        coordinator.id,
        REASON,
    )
    assert (row.current_approval_id, row.submitted_at) == (None, NOW)
    [request] = approval_rows(url, row.id)
    assert (request.status, request.reason, request.requester_user_id) == (
        "pending",
        None,
        tutor.id,
    )
    [event] = on_behalf_events(url, row.id)
    assert (event.actor_user_id, event.details["mode"]) == (coordinator.id, "takeover")
    assert notes(url, tutor.id, ON_BEHALF_TYPE)
    subject = inbox_subject(client, sign_in(client, owner.username), request.id)
    assert (subject["kind_of_review"], subject["on_behalf_by_display_name"]) == (
        "resubmission",
        "Coordinación Académica",
    )
    # Another reviewer corrects and approves it; nobody sends it back.
    owner_cookie = sign_in(client, owner.username)
    assert code_of(send_back(client, owner_cookie, made.id, 3)) == ON_BEHALF
    assert code_of(approve(client, owner_cookie, made.id, 3)) == OK


def test_an_unchanged_takeover_is_allowed(module_seeded_url, client, owner, admin):
    url = module_seeded_url
    made = ended_lesson(url)
    body = sent_back_by_tutor(client, made, owner)

    response = take_over(client, sign_in(client, admin.username), made.id, 2)

    assert code_of(response) == OK
    assert response.json()["closeout"]["status"] == "submitted"
    assert version_of(body) == 2


def test_the_takeover_order(module_seeded_url, client, owner, coordinator):
    url = module_seeded_url
    staff = sign_in(client, coordinator.username)
    unknown = take_over(client, staff, uuid.uuid4(), 1)
    # 404: no closeout (waiting, or completed without one).
    for made in (ended_lesson(url), lesson(url, state="completed", starts_at=ENDED)):
        assert_same_not_found(take_over(client, staff, made.id, 1), unknown)

    made = ended_lesson(url)
    body = submitted(client, made)
    # closeout_not_with_tutor while submitted (after the version check).
    assert code_of(take_over(client, staff, made.id, 9)) == VERSION_CONFLICT
    assert code_of(take_over(client, staff, made.id, version_of(body))) == (
        NOT_WITH_TUTOR
    )
    assert read(client, staff, made.id).json()["can_take_over"] is False
    # ... and while approved.
    owner_cookie = sign_in(client, owner.username)
    assert code_of(approve(client, owner_cookie, made.id, 1)) == OK
    assert code_of(take_over(client, staff, made.id, 2)) == NOT_WITH_TUTOR

    # A missing summary and a participant without a mark.
    other = ended_lesson(url)
    back = sent_back_by_tutor(client, other, owner)
    response = take_over(
        client, staff, other.id, version_of(back), summary={"topics": "x"}
    )
    assert code_of(response) == INVALID
    student = new_student(url)
    run_sql(
        url,
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " VALUES (:s, :st)",
        {"s": other.id, "st": student},
    )
    response = take_over(client, staff, other.id, version_of(back))
    assert code_of(response) == INCOMPLETE
    assert response.json()["details"]["missing_student_ids"] == [str(student)]
    [row] = closeout_rows(url, other.id)
    assert (row.status, row.version, row.on_behalf_by_user_id) == (
        "changes_requested",
        2,
        None,
    )


def test_the_tutor_of_record_cannot_take_over(module_seeded_url, client, owner):
    url = module_seeded_url
    staff_tutor = create_user(url, roles=(ADMINISTRATOR, TUTOR))
    made = ended_lesson(url, tutor=staff_tutor)
    body = sent_back_by_tutor(client, made, owner)
    cookie = sign_in(client, staff_tutor.username)
    assert read(client, cookie, made.id).json()["can_take_over"] is False

    # Before the version check.
    assert code_of(take_over(client, cookie, made.id, 9)) == OWN_SESSION
    assert code_of(take_over(client, cookie, made.id, version_of(body))) == OWN_SESSION


def test_a_takeover_of_a_correction_request_keeps_the_approval_in_force(
    module_seeded_url, client, owner, coordinator
):
    # D3: approved → the tutor asks for a correction → changes requested →
    # a reviewer takes over: the earlier revision stays current.
    url = module_seeded_url
    made = ended_lesson(url)
    body = submitted(client, made)
    owner_cookie = sign_in(client, owner.username)
    assert code_of(approve(client, owner_cookie, made.id, version_of(body))) == OK
    tutor = sign_in(client, made.tutor.username)
    assert (
        code_of(resubmit(client, tutor, made.id, 2, minutes=50, reason="Error")) == OK
    )
    assert code_of(send_back(client, owner_cookie, made.id, 3)) == OK
    [before] = closeout_rows(url, made.id)
    [revision] = revisions(url, before.id)

    response = take_over(
        client, sign_in(client, coordinator.username), made.id, 4, minutes=45
    )

    assert code_of(response) == OK
    [after] = closeout_rows(url, made.id)
    assert after.current_approval_id == before.current_approval_id == revision.id
    assert response.json()["closeout"]["approval"]["approved_minutes"] == 55
    assert len(revisions(url, after.id)) == 1


# --- E: the review rules --------------------------------------------------------------


def test_the_on_behalf_submitter_cannot_review_it(
    module_seeded_url, client, admin, coordinator
):
    url = module_seeded_url
    made = ended_lesson(url)
    body = submitted_for(client, made, admin)
    cookie = sign_in(client, admin.username)
    version = version_of(body)

    assert code_of(approve(client, cookie, made.id, version)) == SELF_REVIEW
    assert (
        code_of(correct_closeout(client, cookie, made.id, version, actual_minutes=50))
        == SELF_REVIEW
    )
    # Request changes: refused as an on-behalf submission.
    assert code_of(send_back(client, cookie, made.id, version)) == ON_BEHALF
    other = sign_in(client, coordinator.username)
    shown = read(client, other, made.id).json()
    assert (shown["can_review"], shown["can_correct"]) == (True, True)
    assert shown["can_request_changes"] is False
    assert code_of(send_back(client, other, made.id, version)) == ON_BEHALF
    [row] = closeout_rows(url, made.id)
    assert (row.status, row.version) == ("submitted", 1)
    assert not notes(url, made.tutor.id, "academics.closeout_changes_requested")
    # Another reviewer corrects and approves it.
    corrected = correct_closeout(client, other, made.id, version, actual_minutes=50)
    assert code_of(corrected) == OK
    assert code_of(approve(client, other, made.id, 2)) == OK
    [revision] = revisions(url, row.id)
    assert (revision.tutor_user_id, revision.approved_minutes) == (made.tutor.id, 50)


def test_the_owner_approves_their_own_on_behalf_submission(
    module_seeded_url, client, owner
):
    made = ended_lesson(module_seeded_url)
    body = submitted_for(client, made, owner)
    cookie = sign_in(client, owner.username)
    assert read(client, cookie, made.id).json()["can_review"] is True

    assert code_of(approve(client, cookie, made.id, version_of(body))) == OK


def test_the_tutor_put_clears_the_marker(module_seeded_url, client, owner, admin):
    # E3: on behalf → approved → the tutor asks for a correction (the marker
    # is cleared) → request changes is allowed again → the tutor resubmits.
    url = module_seeded_url
    made = ended_lesson(url)
    body = submitted_for(client, made, admin)
    owner_cookie = sign_in(client, owner.username)
    assert code_of(approve(client, owner_cookie, made.id, version_of(body))) == OK
    tutor = sign_in(client, made.tutor.username)
    assert read(client, tutor, made.id).json()["closeout"]["on_behalf"] is not None

    response = resubmit(client, tutor, made.id, 2, minutes=50, reason="Fueron 50")

    assert code_of(response) == OK
    assert response.json()["closeout"]["on_behalf"] is None
    [row] = closeout_rows(url, made.id)
    assert (row.on_behalf_by_user_id, row.on_behalf_reason) == (None, None)
    staff = read(client, sign_in(client, admin.username), made.id).json()
    assert (staff["can_review"], staff["can_request_changes"]) == (True, True)
    assert code_of(send_back(client, owner_cookie, made.id, 3)) == OK
    assert code_of(resubmit(client, tutor, made.id, 4, minutes=50)) == OK
    # The audit log keeps the earlier marker.
    [event] = on_behalf_events(url, row.id)
    assert event.details["reason"] == REASON


def test_the_flags_and_the_reason_per_role(
    module_seeded_url, client, owner, coordinator, finance
):
    url = module_seeded_url
    made = ended_lesson(url)
    submitted_for(client, made, coordinator)

    seen = read(client, sign_in(client, finance.username), made.id).json()
    assert seen["closeout"]["on_behalf"] == {
        "by": {"display_name": "Coordinación Académica"},
        "reason": None,
        "submitted_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert not any(
        seen[flag]
        for flag in (
            "can_review",
            "can_correct",
            "can_request_changes",
            "can_submit_on_behalf",
            "can_take_over",
        )
    )
    shown = read(client, sign_in(client, owner.username), made.id).json()
    assert shown["closeout"]["on_behalf"]["reason"] == REASON
    assert (shown["can_review"], shown["can_request_changes"]) == (True, False)
    # Before any closeout: Finance never gets the on-behalf flag.
    waiting = ended_lesson(url)
    finance_view = read(client, sign_in(client, finance.username), waiting.id).json()
    assert finance_view["can_submit_on_behalf"] is False
    # A tutor's own closeout: request changes as before.
    own = ended_lesson(url)
    submitted(client, own)
    plain = read(client, sign_in(client, owner.username), own.id).json()
    assert plain["closeout"]["on_behalf"] is None
    assert (plain["can_review"], plain["can_request_changes"]) == (True, True)


def test_a_rollback_sends_no_notification(module_seeded_url, client, owner):
    # F1: the notification is created in the decision's Unit of Work.
    url = module_seeded_url
    made = ended_lesson(url)
    version = ready(client, made)

    async def _fail(session: Any) -> None:
        await on_behalf.submit_closeout_on_behalf(
            session,
            principal=principal_of(owner.id, OWNER),
            class_session_id=uuid.UUID(made.id),
            body=academics.CloseoutOnBehalfSubmit.model_validate(body_of(version)),
        )
        written = await session.scalar(
            text(
                "SELECT count(*) FROM notifications"
                " WHERE recipient_user_id = :u AND notification_type = :t"
            ),
            {"u": made.tutor.id, "t": ON_BEHALF_TYPE},
        )
        assert written == 1  # written in the Unit of Work, not yet committed
        raise RuntimeError("after the write")

    with pytest.raises(RuntimeError, match="after the write"):
        count_statements(url, _fail)
    assert not notes(url, made.tutor.id, ON_BEHALF_TYPE)
    assert not closeout_rows(url, made.id)
    assert session_row(url, made.id).state == "confirmed"


# --- B: the overdue list --------------------------------------------------------------


def test_the_list_filters_orders_and_pages(module_seeded_url, client, coordinator):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,), display_name="Tutora Lista")
    days = [ENDED - timedelta(days=d) for d in (3, 1, 2)]
    made = [ended_lesson(url, tutor=tutor, starts_at=day) for day in days]
    group = ended_lesson(
        url, tutor=tutor, kind="group", students=3, starts_at=ENDED - timedelta(days=4)
    )
    cookie = sign_in(client, group.tutor.username)
    assert code_of(mark(client, cookie, group.id, 1, (group.students[0], "late"))) == OK
    # Exclusions: in progress, future, completed without a closeout,
    # cancelled, offered; another tutor's.
    lesson(url, tutor=tutor)
    lesson(url, tutor=tutor, starts_at=NOW + timedelta(days=2))
    lesson(url, tutor=tutor, state="completed", starts_at=ENDED - timedelta(days=5))
    cancelled_lesson(url, tutor=tutor, starts_at=ENDED - timedelta(days=6))
    lesson(url, state="offered", starts_at=ENDED - timedelta(days=7), tutor=tutor)
    closed = ended_lesson(url, tutor=tutor, starts_at=ENDED - timedelta(days=8))
    submitted(client, closed)
    ended_lesson(url)
    staff = sign_in(client, coordinator.username)

    response = overdue(client, staff, tutor_user_id=str(tutor.id))

    assert code_of(response) == OK
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (4, 25, 0)
    ids = [item["class_session_id"] for item in page["items"]]
    assert ids == [group.id, made[0].id, made[2].id, made[1].id]
    first = page["items"][0]
    assert first == {
        "class_session_id": group.id,
        "starts_at": (ENDED - timedelta(days=4)).isoformat().replace("+00:00", "Z"),
        "ends_at": (ENDED - timedelta(days=4, minutes=-60))
        .isoformat()
        .replace("+00:00", "Z"),
        "kind": "group",
        "scheduled_minutes": 60,
        "subject": first["subject"],
        "tutor": {
            "user_id": str(tutor.id),
            "display_name": "Tutora Lista",
            "can_submit": True,
        },
        "participant_count": 3,
        "attendance": {"marked": 1, "total": 3},
    }
    assert set(first["subject"]) == {"name", "education_level_name"}
    # Pagination keeps the total.
    second = overdue(client, staff, tutor_user_id=str(tutor.id), limit=2, offset=2)
    assert second.json()["total"] == 4
    assert [i["class_session_id"] for i in second.json()["items"]] == ids[2:]
    beyond = overdue(client, staff, tutor_user_id=str(tutor.id), offset=10).json()
    assert (beyond["total"], beyond["items"]) == (4, [])
    # Center-local dates on starts_at (inclusive).
    zone = zone_of(url)
    day = days[2].astimezone(zone).date()
    dated = overdue(
        client, staff, tutor_user_id=str(tutor.id), **{"from": str(day), "to": str(day)}
    ).json()
    assert [i["class_session_id"] for i in dated["items"]] == [made[2].id]
    later = overdue(client, staff, tutor_user_id=str(tutor.id), **{"from": str(day)})
    assert later.json()["total"] == 2
    earlier = overdue(client, staff, tutor_user_id=str(tutor.id), **{"to": str(day)})
    assert earlier.json()["total"] == 3
    # An unknown tutor: an empty page.
    empty = overdue(client, staff, tutor_user_id=str(uuid.uuid4())).json()
    assert (empty["total"], empty["items"]) == (0, [])
    # Unfiltered, the oldest first.
    everything = overdue(client, staff, limit=100).json()
    ends = [item["ends_at"] for item in everything["items"]]
    assert ends == sorted(ends)


def test_the_list_422s(module_seeded_url, client, owner):
    staff = sign_in(client, owner.username)

    response = overdue(client, staff, **{"from": "2031-03-05", "to": "2031-03-04"})

    assert code_of(response) == INVALID
    assert response.json()["errors"] == [
        {
            "path": ["query", "to"],
            "code": "range_invalid",
            "message": "The last date must not be before the first.",
        }
    ]
    for params in (
        {"tutor_user_id": "nope"},
        {"from": "05/03/2031"},
        {"limit": 101},
        {"offset": -1},
    ):
        assert code_of(overdue(client, staff, **params)) == INVALID, params
    # Same day is fine, no maximum span.
    assert (
        code_of(overdue(client, staff, **{"from": "2020-01-01", "to": "2031-12-31"}))
        == OK
    )


def test_the_list_names_no_students(module_seeded_url, client, owner):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    made = ended_lesson(url, tutor=tutor, kind="group", students=2)
    for i, student in enumerate(made.students):
        run_sql(
            url,
            "UPDATE students SET given_names = :g, family_names = :f WHERE id = :id",
            {"g": f"CanarioNombre{i}", "f": f"CanarioApellido{i}", "id": student},
        )

    response = overdue(
        client, sign_in(client, owner.username), tutor_user_id=str(tutor.id)
    )

    assert response.json()["total"] == 1
    assert "Canario" not in response.text
    for student in made.students:
        assert str(student) not in response.text


@pytest.mark.parametrize("count", [1, 12])
def test_the_list_takes_at_most_5_statements(module_seeded_url, count):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    for d in range(count):
        ended_lesson(
            url,
            tutor=tutor,
            kind="group",
            students=2,
            starts_at=ENDED - timedelta(days=d),
        )

    async def _list(session: Any) -> Any:
        return await on_behalf.list_overdue_closeouts(
            session,
            page=OffsetParams(limit=50, offset=0),
            tutor_user_id=tutor.id,
            from_date=(NOW - timedelta(days=30)).date(),
            to_date=NOW.date(),
        )

    statements, result = count_statements(url, _list)

    assert result.total == count
    assert len(result.items) == count
    assert statements <= 5, statements


def test_the_inbox_summaries_stay_within_3_statements(module_seeded_url, client, owner):
    url = module_seeded_url
    ids = []
    for i in range(5):
        made = ended_lesson(url)
        if i % 2:
            submitted_for(client, made, owner)
        else:
            submitted(client, made)
        [row] = closeout_rows(url, made.id)
        ids.append(row.id)

    def summaries_of(subset: list[Any]) -> Any:
        async def _summaries(session: Any) -> Any:
            return await workflow.SESSION_CLOSEOUT_HANDLER.summaries(session, subset)

        return _summaries

    for subset in (ids[:1], ids):
        statements, result = count_statements(url, summaries_of(subset))
        assert set(result) == set(subset)
        assert statements <= 3, statements
    assert [result[i]["on_behalf_by_display_name"] for i in ids] == [
        None,
        "Dueña Centro",
        None,
        "Dueña Centro",
        None,
    ]
