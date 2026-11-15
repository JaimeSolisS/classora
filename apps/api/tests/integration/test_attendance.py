"""Attendance (#76 B, C, D, H1, H2) with real logins through the real app:
``GET`` / ``PUT /api/v1/class-sessions/{id}/attendance`` and ``POST
…/attendance/corrections``.

The tutor's marks (individual, group, course; partial; re-marks; no-op), the
C2 and D2 orders step by step, the states (A3, E2: marking never changes the
session's state), the staff corrections with their append-only rows and
named audit event, the IDOR cases (another tutor, the original tutor after a
real #55 substitution, a pending substitute, a lost tutor role, families:
the same ``404`` as an unknown id), the raw-SQL guards and the B3 statement
budget.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.authz import Principal
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
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
from tests.integration.schedule_support import stored
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
from tests.integration.test_class_session_create import (
    MONDAY as OFFER_MONDAY,
)
from tests.integration.test_class_session_create import (
    at,
    new_room,
    new_student,
    new_subject,
)
from tests.integration.test_session_substitutions import substituted

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
STARTED = NOW - timedelta(minutes=30)
"""Started 30 minutes ago, ends in 30 (started but not ended)."""

OK = (200, None)
CREATED = (201, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
VERSION_CONFLICT = (409, "version_conflict")
TUTOR_ONLY = (403, "attendance_tutor_only")
DENIED = (403, "permission_denied")


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
    return f"/api/v1/class-sessions/{session_id}/attendance"


def read(client: TestClient, cookie: Any, session_id: Any) -> Response:
    return get(client, cookie, path(session_id))


def put(client: TestClient, cookie: Any, session_id: Any, body: Any) -> Response:
    client.cookies.clear()
    return client.put(path(session_id), json=body, headers=signed_headers(cookie))


def mark(
    client: TestClient,
    cookie: Any,
    session_id: Any,
    version: int,
    *marks: tuple[Any, str],
) -> Response:
    return put(
        client,
        cookie,
        session_id,
        {
            "version": version,
            "marks": [{"student_id": str(s), "status": status} for s, status in marks],
        },
    )


def correct(
    client: TestClient,
    cookie: Any,
    session_id: Any,
    version: int,
    student_id: Any,
    status: str,
    reason: Any = "Llegó tarde según recepción",
) -> Response:
    body: dict[str, Any] = {
        "version": version,
        "student_id": str(student_id),
        "status": status,
    }
    if reason is not None:
        body["reason"] = reason
    return post(client, cookie, path(session_id) + "/corrections", body)


def statuses(response: Response) -> dict[str, str | None]:
    assert response.status_code in (200, 201), response.text
    return {item["student"]["id"]: item["status"] for item in response.json()["items"]}


def version_of(url: str, session_id: Any) -> int:
    [row] = run_sql(
        url, "SELECT version FROM class_sessions WHERE id = :id", {"id": session_id}
    )
    return int(row.version)


def without_request_id(response: Response) -> dict[str, Any]:
    return {k: v for k, v in response.json().items() if k != "request_id"}


@dataclass
class Lesson:
    id: str
    tutor: CreatedUser
    students: list[uuid.UUID]


def lesson(
    url: str,
    *,
    kind: str = "individual",
    students: int = 1,
    state: str = "confirmed",
    starts_at: datetime = STARTED,
    tutor: CreatedUser | None = None,
    with_tutor: bool = True,
) -> Lesson:
    """A stored session of a new tutor (a login) with ``students`` new
    participants."""
    tutor = tutor or create_user(url, roles=(TUTOR,), display_name="Tutora Lista")
    ids = [new_student(url) for _ in range(students)]
    extra: dict[str, Any] = {"kind": kind}
    if with_tutor:
        extra["tutor_user_id"] = tutor.id
    session_id = stored(url, starts_at, state=state, students=tuple(ids), **extra)
    return Lesson(id=session_id, tutor=tutor, students=ids)


def principal_of(user_id: uuid.UUID, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


# --- C3: the tutor's marks ------------------------------------------------------------


def test_the_tutor_marks_an_individual_session(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url)
    [student] = made.students
    [before] = run_sql(
        url,
        "SELECT state, actual_minutes, first_confirmed_at FROM class_sessions"
        " WHERE id = :id",
        {"id": made.id},
    )
    cookie = sign_in(client, made.tutor.username)
    [notified] = run_sql(url, "SELECT count(*) AS n FROM notifications")

    shown = read(client, cookie, made.id)
    assert code_of(shown) == OK
    body = shown.json()
    assert (body["can_mark"], body["can_correct"], body["block"]) == (
        True,
        False,
        None,
    )
    assert (body["version"], body["state"], body["kind"]) == (
        1,
        "confirmed",
        "individual",
    )
    [item] = body["items"]
    assert item["status"] is None
    assert (item["marked_by"], item["marked_at"], item["corrected"]) == (
        None,
        None,
        False,
    )

    response = mark(client, cookie, made.id, 1, (student, "present"))

    assert code_of(response) == OK
    body = response.json()
    assert body["version"] == 2
    [item] = body["items"]
    assert item["status"] == "present"
    assert item["marked_by"] == {"display_name": "Tutora Lista"}
    assert datetime.fromisoformat(item["marked_at"]) == NOW
    assert version_of(url, made.id) == 2
    # E2: marking never changes the session.
    [after] = run_sql(
        url,
        "SELECT state, actual_minutes, first_confirmed_at FROM class_sessions"
        " WHERE id = :id",
        {"id": made.id},
    )
    assert tuple(after) == tuple(before)
    [row] = run_sql(
        url,
        "SELECT id, marked_by_user_id FROM class_session_attendance"
        " WHERE class_session_id = :id",
        {"id": made.id},
    )
    assert row.marked_by_user_id == made.tutor.id
    [created] = events(url, record_type="class_session_attendance", record_id=row.id)
    assert (created.action, created.actor_user_id) == ("create", made.tutor.id)
    assert created.changes is not None
    assert created.changes["status"] == {"old": None, "new": "present"}
    # No notification.
    [count] = run_sql(url, "SELECT count(*) AS n FROM notifications")
    assert count.n == notified.n


def test_the_body_is_a_closed_allow_list(module_seeded_url, client, owner):
    url = module_seeded_url
    made = lesson(url)
    [student] = made.students
    assert (
        code_of(
            correct(
                client, sign_in(client, owner.username), made.id, 1, student, "late"
            )
        )
        == CREATED
    )

    body = read(client, sign_in(client, made.tutor.username), made.id).json()

    assert set(body) == {
        "class_session_id",
        "version",
        "kind",
        "state",
        "starts_at",
        "ends_at",
        "subject",
        "can_mark",
        "can_correct",
        "block",
        "items",
    }
    assert set(body["subject"]) == {"id", "name", "education_level_name"}
    [item] = body["items"]
    assert set(item) == {
        "student",
        "status",
        "marked_by",
        "marked_at",
        "corrected",
        "corrections",
    }
    assert set(item["student"]) == {"id", "display_name"}
    assert set(item["marked_by"]) == {"display_name"}
    [correction] = item["corrections"]
    assert set(correction) == {
        "id",
        "previous_status",
        "new_status",
        "reason",
        "corrected_by",
        "created_at",
    }
    assert correction["corrected_by"] == {"display_name": "Dueña Centro"}
    # B2 (choice): the tutor sees the correction and its reason.
    assert correction["reason"] == "Llegó tarde según recepción"
    assert str(owner.id) not in str(body)


def test_a_group_tutor_marks_two_of_three_then_the_third(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url, kind="group", students=3)
    first, second, third = made.students
    cookie = sign_in(client, made.tutor.username)

    partial = mark(client, cookie, made.id, 1, (first, "present"), (second, "late"))

    assert code_of(partial) == OK
    assert statuses(partial) == {
        str(first): "present",
        str(second): "late",
        str(third): None,
    }
    names = [item["student"]["display_name"] for item in partial.json()["items"]]
    assert names == sorted(names, key=str.casefold)
    assert partial.json()["version"] == 2

    rest = mark(client, cookie, made.id, 2, (third, "absent"))

    assert statuses(rest) == {
        str(first): "present",
        str(second): "late",
        str(third): "absent",
    }
    assert rest.json()["version"] == 3


def test_a_re_mark_updates_and_an_identical_put_is_a_no_op(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url, kind="group", students=2)
    first, second = made.students
    cookie = sign_in(client, made.tutor.username)
    assert code_of(mark(client, cookie, made.id, 1, (first, "present"))) == OK
    [row] = run_sql(
        url,
        "SELECT id FROM class_session_attendance WHERE student_id = :s",
        {"s": first},
    )

    changed = mark(client, cookie, made.id, 2, (first, "late"))

    assert statuses(changed)[str(first)] == "late"
    assert changed.json()["version"] == 3
    update = events(url, record_id=row.id, action="update")
    assert len(update) == 1
    assert update[0].changes is not None
    assert update[0].changes["status"] == {"old": "present", "new": "late"}
    audit_count = len(events(url))

    same = mark(client, cookie, made.id, 3, (first, "late"))

    assert code_of(same) == OK
    assert same.json()["version"] == 3
    assert version_of(url, made.id) == 3
    assert len(events(url)) == audit_count
    assert statuses(same) == {str(first): "late", str(second): None}


def test_a_course_session_is_marked_too(module_seeded_url, client):
    made = lesson(module_seeded_url, kind="course", students=2)
    cookie = sign_in(client, made.tutor.username)

    response = mark(
        client,
        cookie,
        made.id,
        1,
        (made.students[0], "absent"),
        (made.students[1], "present"),
    )

    assert code_of(response) == OK
    assert response.json()["kind"] == "course"
    assert sorted(statuses(response).values()) == ["absent", "present"]


# --- C2: the order --------------------------------------------------------------------


def test_anonymous_gets_401(module_seeded_url, client):
    made = lesson(module_seeded_url)
    client.cookies.clear()

    assert client.get(path(made.id)).status_code == 401
    response = client.put(
        path(made.id),
        json={
            "version": 1,
            "marks": [{"student_id": str(uuid.uuid4()), "status": "late"}],
        },
    )
    assert response.status_code in (401, 403)


@pytest.mark.parametrize(
    ("body", "codes"),
    [
        ({"version": 1, "marks": []}, {"too_short"}),
        ({"marks": [{"student_id": "S", "status": "late"}]}, {"missing"}),
        (
            {"version": 1, "marks": [{"student_id": "S", "status": "excused"}]},
            {"literal_error"},
        ),
        (
            {"version": 1, "marks": [{"student_id": "S", "status": "late"}], "x": 1},
            {"extra_forbidden"},
        ),
        (
            {
                "version": 1,
                "marks": [
                    {"student_id": "S", "status": "late"},
                    {"student_id": "S", "status": "absent"},
                ],
            },
            {"duplicate_student"},
        ),
    ],
)
def test_the_body_422s(module_seeded_url, client, body, codes):
    made = lesson(module_seeded_url)
    student = str(made.students[0])
    for item in body.get("marks", []):
        item["student_id"] = student

    response = put(client, sign_in(client, made.tutor.username), made.id, body)

    assert code_of(response) == INVALID
    assert {error["code"] for error in response.json()["errors"]} == codes
    # The schema comes before the record policy: an unknown session too.
    other = put(client, sign_in(client, made.tutor.username), uuid.uuid4(), body)
    assert code_of(other) == INVALID


def test_more_than_500_marks_is_422(module_seeded_url, client):
    made = lesson(module_seeded_url)
    marks = [{"student_id": str(uuid.uuid4()), "status": "late"} for _ in range(501)]

    response = put(
        client,
        sign_in(client, made.tutor.username),
        made.id,
        {"version": 1, "marks": marks},
    )

    assert code_of(response) == INVALID
    assert {e["code"] for e in response.json()["errors"]} == {"too_long"}


def test_a_visible_staff_member_gets_403_and_others_the_404(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url)
    [student] = made.students
    staff = sign_in(client, create_user(url, roles=(ADMINISTRATOR,)).username)

    # 403 before the version check: a stale version still gets 403.
    assert code_of(mark(client, staff, made.id, 99, (student, "late"))) == TUTOR_ONLY
    assert code_of(mark(client, staff, made.id, 1, (student, "late"))) == TUTOR_ONLY
    # An unknown session: 404 before 403.
    assert code_of(mark(client, staff, uuid.uuid4(), 1, (student, "late"))) == NOT_FOUND
    assert version_of(url, made.id) == 1


def test_the_version_conflict_after_a_write(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url, kind="group", students=2)
    first, second = made.students
    cookie = sign_in(client, made.tutor.username)
    assert code_of(mark(client, cookie, made.id, 1, (first, "present"))) == OK

    stale = mark(client, cookie, made.id, 1, (second, "late"))

    assert code_of(stale) == VERSION_CONFLICT
    assert statuses(read(client, cookie, made.id))[str(second)] is None


def test_version_before_state_and_state_before_participants(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url, starts_at=NOW + timedelta(hours=2))
    cookie = sign_in(client, made.tutor.username)

    assert code_of(mark(client, cookie, made.id, 7, (uuid.uuid4(), "late"))) == (
        VERSION_CONFLICT
    )
    assert code_of(mark(client, cookie, made.id, 1, (uuid.uuid4(), "late"))) == (
        409,
        "attendance_not_started",
    )


def test_a_non_participant_is_the_same_422_for_unknown_and_foreign(
    module_seeded_url, client
):
    url = module_seeded_url
    made = lesson(url)
    elsewhere = lesson(url)
    cookie = sign_in(client, made.tutor.username)

    unknown = mark(
        client,
        cookie,
        made.id,
        1,
        (made.students[0], "present"),
        (uuid.uuid4(), "late"),
    )
    foreign = mark(
        client,
        cookie,
        made.id,
        1,
        (made.students[0], "present"),
        (elsewhere.students[0], "late"),
    )

    for response in (unknown, foreign):
        assert code_of(response) == INVALID
        assert response.json()["errors"] == [
            {
                "path": ["body", "marks", 1, "student_id"],
                "code": "student_not_participant",
                "message": "Not a student of this session.",
            }
        ]
    assert without_request_id(unknown) == without_request_id(foreign)
    # Nothing was written (the valid mark neither).
    assert version_of(url, made.id) == 1
    assert statuses(read(client, cookie, made.id)) == {str(made.students[0]): None}


def test_a_corrected_mark_wins_over_the_tutor(module_seeded_url, client, owner):
    url = module_seeded_url
    made = lesson(url, kind="group", students=2)
    first, second = made.students
    tutor = sign_in(client, made.tutor.username)
    assert code_of(mark(client, tutor, made.id, 1, (first, "present"))) == OK
    staff = sign_in(client, owner.username)
    assert code_of(correct(client, staff, made.id, 2, first, "absent")) == CREATED
    tutor = sign_in(client, made.tutor.username)

    refused = mark(client, tutor, made.id, 3, (second, "late"), (first, "present"))

    assert code_of(refused) == (409, "attendance_corrected")
    assert refused.json()["details"] == {"student_id": str(first)}
    assert version_of(url, made.id) == 3
    # The identical mark is a no-op; the other student can still be marked.
    same = mark(client, tutor, made.id, 3, (first, "absent"))
    assert code_of(same) == OK
    assert same.json()["version"] == 3
    other = mark(client, tutor, made.id, 3, (first, "absent"), (second, "late"))
    assert statuses(other) == {str(first): "absent", str(second): "late"}
    [item] = [i for i in other.json()["items"] if i["student"]["id"] == str(first)]
    assert item["corrected"] is True


# --- A3, E2: the states ---------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["offered", "conflict", "cancelled", "skipped_closure"]
)
def test_the_tutor_cannot_mark_outside_confirmed_or_completed(
    module_seeded_url, client, state
):
    made = lesson(module_seeded_url, state=state)
    cookie = sign_in(client, made.tutor.username)

    response = mark(client, cookie, made.id, 1, (made.students[0], "present"))

    assert code_of(response) == (409, "attendance_not_recordable")
    assert response.json()["details"] == {"state": state}
    body = read(client, cookie, made.id).json()
    assert (body["can_mark"], body["block"]) == (False, "attendance_not_recordable")


@pytest.mark.parametrize(
    "state", ["unassigned", "offered", "conflict", "cancelled", "skipped_closure"]
)
def test_staff_cannot_correct_outside_confirmed_or_completed(
    module_seeded_url, client, owner, state
):
    made = lesson(module_seeded_url, state=state, with_tutor=state != "unassigned")
    cookie = sign_in(client, owner.username)

    response = correct(client, cookie, made.id, 1, made.students[0], "present")

    assert code_of(response) == (409, "attendance_not_recordable")
    body = read(client, cookie, made.id).json()
    assert (body["can_correct"], body["block"]) == (False, "attendance_not_recordable")


def test_a_future_session_is_not_started(module_seeded_url, client, owner):
    made = lesson(module_seeded_url, starts_at=NOW + timedelta(minutes=1))
    tutor = sign_in(client, made.tutor.username)
    student = made.students[0]

    assert code_of(mark(client, tutor, made.id, 1, (student, "present"))) == (
        409,
        "attendance_not_started",
    )
    body = read(client, tutor, made.id).json()
    assert (body["can_mark"], body["block"]) == (False, "attendance_not_started")
    staff = sign_in(client, owner.username)
    assert code_of(correct(client, staff, made.id, 1, student, "present")) == (
        409,
        "attendance_not_started",
    )


def test_a_session_starting_now_and_one_a_week_old_are_markable(
    module_seeded_url, client
):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    starting = lesson(url, starts_at=NOW, tutor=tutor)
    old = lesson(url, starts_at=NOW - timedelta(days=7), tutor=tutor)
    cookie = sign_in(client, tutor.username)

    for made in (starting, old):
        response = mark(client, cookie, made.id, 1, (made.students[0], "late"))
        assert code_of(response) == OK


def test_a_completed_session_is_locked_for_the_tutor_and_correctable(
    module_seeded_url, client
):
    url = module_seeded_url
    made = lesson(url, state="completed", students=2)
    first, second = made.students
    tutor = sign_in(client, made.tutor.username)

    assert code_of(mark(client, tutor, made.id, 1, (first, "present"))) == (
        409,
        "attendance_locked",
    )
    body = read(client, tutor, made.id).json()
    assert (body["can_mark"], body["block"]) == (False, "attendance_locked")

    coordinator = create_user(url, roles=(ACADEMIC_COORDINATOR,))
    staff = sign_in(client, coordinator.username)
    shown = read(client, staff, made.id).json()
    assert (shown["can_correct"], shown["block"]) == (True, None)
    response = correct(client, staff, made.id, 1, second, "absent")
    assert code_of(response) == CREATED
    assert response.json()["state"] == "completed"
    assert statuses(response)[str(second)] == "absent"


# --- D: staff corrections -------------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR])
def test_a_correction_is_stored_and_audited(module_seeded_url, client, role):
    url = module_seeded_url
    made = lesson(url)
    [student] = made.students
    assert (
        code_of(
            mark(
                client,
                sign_in(client, made.tutor.username),
                made.id,
                1,
                (student, "present"),
            )
        )
        == OK
    )
    corrector = create_user(url, roles=(role,), display_name="Coordinación")
    cookie = sign_in(client, corrector.username)

    response = correct(
        client, cookie, made.id, 2, student, "late", reason="  Llegó 20 min tarde  "
    )

    assert code_of(response) == CREATED
    body = response.json()
    assert body["version"] == 3
    assert (body["can_correct"], body["can_mark"]) == (True, False)
    [item] = body["items"]
    assert item["status"] == "late"
    assert item["marked_by"] == {"display_name": "Coordinación"}
    assert item["corrected"] is True
    [history] = item["corrections"]
    assert (history["previous_status"], history["new_status"], history["reason"]) == (
        "present",
        "late",
        "Llegó 20 min tarde",
    )
    [row] = run_sql(
        url,
        "SELECT c.id, c.attendance_id, c.previous_status, c.new_status, c.reason,"
        " c.corrected_by_user_id FROM class_session_attendance_corrections c"
        " JOIN class_session_attendance a ON a.id = c.attendance_id"
        " WHERE a.class_session_id = :s",
        {"s": made.id},
    )
    assert (row.previous_status, row.new_status, row.reason) == (
        "present",
        "late",
        "Llegó 20 min tarde",
    )
    assert row.corrected_by_user_id == corrector.id
    [event] = events(
        url, action="scheduling.attendance_corrected", record_id=row.attendance_id
    )
    assert event.record_type == "class_session_attendance"
    assert event.actor_user_id == corrector.id
    assert event.details == {
        "correction_id": str(row.id),
        "class_session_id": made.id,
        "student_id": str(student),
        "previous_status": "present",
        "new_status": "late",
        "reason": "Llegó 20 min tarde",
    }
    [state] = run_sql(
        url, "SELECT state FROM class_sessions WHERE id = :id", {"id": made.id}
    )
    assert state.state == "confirmed"


def test_staff_can_make_the_first_mark(module_seeded_url, client, owner):
    url = module_seeded_url
    made = lesson(url)
    [student] = made.students

    response = correct(
        client, sign_in(client, owner.username), made.id, 1, student, "absent"
    )

    assert code_of(response) == CREATED
    [item] = response.json()["items"]
    [history] = item["corrections"]
    assert (history["previous_status"], history["new_status"]) == (None, "absent")
    [event] = events(url, action="scheduling.attendance_corrected")[-1:]
    assert event.details is not None
    assert event.details["previous_status"] is None


def test_the_correction_422s(module_seeded_url, client, owner):
    url = module_seeded_url
    made = lesson(url)
    elsewhere = lesson(url)
    [student] = made.students
    cookie = sign_in(client, owner.username)

    for reason, code in (
        (None, "reason_required"),
        ("   ", "reason_required"),
        ("x" * 1001, "reason_too_long"),
    ):
        response = correct(client, cookie, made.id, 1, student, "late", reason=reason)
        assert code_of(response) == INVALID
        assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
            (["body", "reason"], code)
        ]
    assert (
        code_of(correct(client, cookie, made.id, 1, student, "late", reason="x" * 1000))
        == CREATED
    )

    same = correct(client, cookie, made.id, 2, student, "late")
    assert code_of(same) == INVALID
    assert [(e["path"], e["code"]) for e in same.json()["errors"]] == [
        (["body", "status"], "attendance_unchanged")
    ]
    unknown = correct(client, cookie, made.id, 2, uuid.uuid4(), "late")
    foreign = correct(client, cookie, made.id, 2, elsewhere.students[0], "late")
    for response in (unknown, foreign):
        assert code_of(response) == INVALID
        assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
            (["body", "student_id"], "student_not_participant")
        ]
    assert without_request_id(unknown) == without_request_id(foreign)
    assert code_of(correct(client, cookie, made.id, 1, student, "absent")) == (
        VERSION_CONFLICT
    )
    assert code_of(correct(client, cookie, uuid.uuid4(), 2, student, "absent")) == (
        NOT_FOUND
    )
    assert version_of(url, made.id) == 2


def test_others_cannot_correct(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url)
    [student] = made.students
    family = guardian_with_child(url)
    learner, _ = student_account(url)
    callers = [
        create_user(url, roles=(FINANCE_STAFF,)),
        made.tutor,
        learner,
        family.user,
    ]

    for user in callers:
        response = correct(
            client, sign_in(client, user.username), made.id, 1, student, "late"
        )
        assert code_of(response) == DENIED, user
    assert version_of(url, made.id) == 1


def test_corrections_never_change_and_attendance_needs_a_participant(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    made = lesson(url)
    [student] = made.students
    assert (
        code_of(
            correct(
                client, sign_in(client, owner.username), made.id, 1, student, "late"
            )
        )
        == CREATED
    )
    [correction] = run_sql(
        url,
        "SELECT c.id FROM class_session_attendance_corrections c"
        " JOIN class_session_attendance a ON a.id = c.attendance_id"
        " WHERE a.class_session_id = :s",
        {"s": made.id},
    )

    for statement in (
        "UPDATE class_session_attendance_corrections SET reason = 'Otra'"
        " WHERE id = :id",
        "DELETE FROM class_session_attendance_corrections WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(url, statement, {"id": correction.id})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"
    with pytest.raises(IntegrityError, match="fk_class_session_attendance_participant"):
        run_sql(
            url,
            "INSERT INTO class_session_attendance (class_session_id, student_id,"
            " status, marked_by_user_id, marked_at)"
            " VALUES (:s, :st, 'late', :u, now())",
            {"s": made.id, "st": new_student(url), "u": owner.id},
        )


# --- B1, H2: who may read and mark (IDOR) ---------------------------------------------


def test_another_tutor_gets_the_404_of_an_unknown_id(module_seeded_url, client):
    url = module_seeded_url
    made = lesson(url)
    other = sign_in(client, create_user(url, roles=(TUTOR,)).username)
    unknown = uuid.uuid4()

    assert_same_not_found(read(client, other, made.id), read(client, other, unknown))
    assert_same_not_found(
        mark(client, other, made.id, 1, (made.students[0], "late")),
        mark(client, other, unknown, 1, (made.students[0], "late")),
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
            mark(client, cookie, made.id, 1, (own_student, "present")),
            mark(client, cookie, unknown, 1, (own_student, "present")),
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

    assert_same_not_found(
        read(client, cookie, made.id), read(client, cookie, uuid.uuid4())
    )
    assert code_of(mark(client, cookie, made.id, 1, (made.students[0], "late"))) == (
        NOT_FOUND
    )


@pytest.mark.parametrize(
    ("role", "can_correct"),
    [
        (OWNER, True),
        (ADMINISTRATOR, True),
        (ACADEMIC_COORDINATOR, True),
        (FINANCE_STAFF, False),
    ],
)
def test_staff_read_attendance(module_seeded_url, client, role, can_correct):
    url = module_seeded_url
    made = lesson(url)
    assert (
        code_of(
            mark(
                client,
                sign_in(client, made.tutor.username),
                made.id,
                1,
                (made.students[0], "late"),
            )
        )
        == OK
    )
    cookie = sign_in(client, create_user(url, roles=(role,)).username)

    response = read(client, cookie, made.id)

    assert code_of(response) == OK
    body = response.json()
    assert (body["can_mark"], body["can_correct"]) == (False, can_correct)
    assert statuses(response) == {str(made.students[0]): "late"}
    assert code_of(mark(client, cookie, made.id, 2, (made.students[0], "absent"))) == (
        TUTOR_ONLY
    )


def test_the_current_tutor_after_a_real_substitution(
    module_seeded_url, client, owner, at_now
):
    """#55: the original tutor gets 404 once the substitute accepted; a
    pending substitute gets 404 until then."""
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
        mark(client, original, created["id"], version, (setup.student, "present")),
        mark(client, original, unknown, version, (setup.student, "present")),
    )
    response = mark(client, taker, created["id"], version, (setup.student, "present"))
    assert code_of(response) == OK
    assert response.json()["items"][0]["marked_by"] is not None


# --- B3: the statement budget ---------------------------------------------------------


@pytest.mark.parametrize("count", [1, 20])
def test_the_read_takes_at_most_7_statements(module_seeded_url, client, owner, count):
    url = module_seeded_url
    made = lesson(url, kind="group", students=count)
    tutor = sign_in(client, made.tutor.username)
    marks = [(student, "present") for student in made.students]
    assert code_of(mark(client, tutor, made.id, 1, *marks)) == OK
    assert (
        code_of(
            correct(
                client,
                sign_in(client, owner.username),
                made.id,
                2,
                made.students[0],
                "late",
            )
        )
        == CREATED
    )

    def read_as(principal: Principal) -> Any:
        async def _read(session: Any) -> Any:
            return await scheduling.get_session_attendance(
                session, principal, uuid.UUID(made.id)
            )

        return _read

    for principal in (
        principal_of(made.tutor.id, TUTOR),
        principal_of(owner.id, OWNER),
    ):
        statements, result = count_statements(url, read_as(principal))
        assert len(result.items) == count
        assert statements <= 7, statements
