"""Session notes (#79 C, D, E, F, I2, I3) with real logins through the real
app: ``POST`` / ``GET /api/v1/class-sessions/{id}/notes``, ``GET
/api/v1/students/{id}/notes``, ``PATCH`` / ``DELETE /api/v1/notes/{id}``,
``POST …/publish`` and ``…/unpublish``, and the family route ``GET
/api/v1/students/{id}/learning-notes``.

The success paths with their audit rows and notifications, the C2 and D1
orders step by step, the E5 states, the group session, the former-student
read-only rule (frozen clock), the B3 recipients and notification params,
the staff unpublish with its reason, the IDOR cases (another tutor, the
original tutor after a real #55 substitution, a lost tutor role, Finance,
an unlinked guardian, another student, another family's session filter, a
deleted note, an unknown id: the same ``404``), the adult consent rule and
the E1, E2 and F statement budgets. The family leak walk over every ``GET``
route is ``test_session_notes_leak.py``; the D6 races are
``test_session_notes_concurrency.py``.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.families import service as families
from app.modules.families.models import Guardian, Student
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.workflows import session_notes as workflow
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.base import persist
from tests.factories.families import (
    ADULT_BIRTHDATE,
    GuardianFactory,
    link_guardian,
    login_consent,
    persist_student,
    student_login,
)
from tests.factories.identity import persist_user
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.schedule_support import principal_of, stored
from tests.integration.session_request_support import (
    MINOR_BIRTHDATE,
    Family,
    deactivate_guardian,
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
STARTED = NOW - timedelta(minutes=30)
TOMORROW = NOW + timedelta(days=1)

OK = (200, None)
CREATED = (201, None)
NO_CONTENT = (204, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
UNAUTHENTICATED = (401, "not_authenticated")
VERSION_CONFLICT = (409, "version_conflict")
TUTOR_ONLY = (403, "note_tutor_only")
AUTHOR_ONLY = (403, "note_author_only")
READ_ONLY = (403, "note_read_only")
PUBLISHED = (409, "note_published")
ALREADY = (409, "note_already_published")
NOT_PUBLISHED = (409, "note_not_published")
NOT_NOTABLE = (409, "session_not_notable")
CONSENT = (403, "student_consent_required")

PUBLISHED_TYPE = "academics.learning_note_published"
UNPUBLISHED_TYPE = "academics.learning_note_unpublished"


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


# --- Helpers --------------------------------------------------------------------------


def notes_path(session_id: Any) -> str:
    return f"/api/v1/class-sessions/{session_id}/notes"


def note_path(note_id: Any, action: str = "") -> str:
    return f"/api/v1/notes/{note_id}{action}"


def history_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/notes"


def family_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/learning-notes"


def create(
    client: TestClient, cookie: Any, session_id: Any, student_id: Any, body: Any = None
) -> Response:
    return post(
        client,
        cookie,
        notes_path(session_id),
        {"student_id": str(student_id), "body": body or f"Nota {uuid.uuid4().hex}"},
    )


def edit(
    client: TestClient, cookie: Any, note_id: Any, version: int, body: str
) -> Response:
    return patch(client, cookie, note_path(note_id), {"version": version, "body": body})


def publish(client: TestClient, cookie: Any, note_id: Any, version: int) -> Response:
    return post(client, cookie, note_path(note_id, "/publish"), {"version": version})


def unpublish(
    client: TestClient, cookie: Any, note_id: Any, version: int, reason: Any = None
) -> Response:
    body: dict[str, Any] = {"version": version}
    if reason is not None:
        body["reason"] = reason
    return post(client, cookie, note_path(note_id, "/unpublish"), body)


def delete(client: TestClient, cookie: Any, note_id: Any, version: int) -> Response:
    client.cookies.clear()
    return client.delete(
        note_path(note_id), params={"version": version}, headers=signed_headers(cookie)
    )


def ok(response: Response, expected: tuple[int, None] = OK) -> dict[str, Any]:
    assert code_of(response) == expected, response.text
    body: dict[str, Any] = response.json()
    return body


def note_row(url: str, note_id: Any) -> Any:
    [row] = run_sql(url, "SELECT * FROM session_notes WHERE id = :id", {"id": note_id})
    return row


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


def bodies(response: Response) -> list[str]:
    assert code_of(response) == OK, response.text
    return [item["body"] for item in response.json()["items"]]


def a_tutor(url: str, name: str = "Tutora Notas") -> CreatedUser:
    return create_user(url, roles=(TUTOR,), display_name=name)


def lesson(
    url: str,
    *,
    tutor: CreatedUser | None = None,
    students: tuple[uuid.UUID, ...] | None = None,
    state: str = "confirmed",
    starts_at: datetime = STARTED,
    kind: str = "individual",
    **overrides: Any,
) -> tuple[str, CreatedUser, tuple[uuid.UUID, ...]]:
    """A stored session of ``tutor`` (a new one by default) with
    ``students`` (one new student by default)."""
    tutor = tutor or a_tutor(url)
    students = students or (new_student(url),)
    session_id = stored(
        url,
        starts_at,
        state=state,
        students=students,
        tutor_user_id=tutor.id,
        kind=kind,
        **overrides,
    )
    return session_id, tutor, students


def written(
    client: TestClient,
    url: str,
    *,
    family: Family | None = None,
    body: str | None = None,
) -> tuple[str, CreatedUser, Any, dict[str, Any]]:
    """A session of a new tutor with one student (``family``'s child when
    given) and one internal note: ``(session id, tutor, tutor cookie,
    note)``."""
    students = None if family is None else (family.student_id,)
    session_id, tutor, (student,) = lesson(url, students=students)
    cookie = sign_in(client, tutor.username)
    note = ok(create(client, cookie, session_id, student, body), CREATED)
    return session_id, tutor, cookie, note


# --- C: create ------------------------------------------------------------------------


def test_the_tutor_writes_an_internal_note(module_seeded_url, client):
    url = module_seeded_url
    session_id, tutor, (student,) = lesson(url)
    cookie = sign_in(client, tutor.username)
    [notified] = run_sql(url, "SELECT count(*) AS n FROM notifications")

    response = create(client, cookie, session_id, student, "  Trajo la calculadora  ")

    note = ok(response, CREATED)
    assert set(note) == {
        "id",
        "version",
        "class_session_id",
        "session_starts_at",
        "subject",
        "student",
        "author",
        "is_own",
        "body",
        "visibility",
        "published_at",
        "published_by",
        "unpublished_at",
        "unpublish_reason",
        "edited",
        "created_at",
        "can_edit",
        "can_publish",
        "can_unpublish",
        "can_delete",
    }
    assert note["body"] == "Trajo la calculadora"
    assert (note["visibility"], note["version"], note["is_own"]) == (
        "internal",
        1,
        True,
    )
    assert note["author"] == {"display_name": "Tutora Notas"}
    assert note["student"]["id"] == str(student)
    assert note["class_session_id"] == session_id
    assert datetime.fromisoformat(note["session_starts_at"]) == STARTED
    assert (note["published_at"], note["published_by"], note["edited"]) == (
        None,
        None,
        False,
    )
    assert (
        note["can_edit"],
        note["can_publish"],
        note["can_unpublish"],
        note["can_delete"],
    ) == (True, True, False, True)
    row = note_row(url, note["id"])
    assert (row.visibility, row.author_user_id, row.student_id) == (
        "internal",
        tutor.id,
        student,
    )
    [event] = events(url, action="create", record_type="session_notes")[-1:]
    assert str(event.record_id) == note["id"]
    # A create never notifies anyone.
    assert run_sql(url, "SELECT count(*) AS n FROM notifications") == [notified]


@pytest.mark.parametrize(
    ("state", "starts_at"),
    [("confirmed", TOMORROW), ("confirmed", STARTED), ("completed", STARTED)],
)
def test_notes_go_on_confirmed_and_completed_sessions(
    module_seeded_url, client, state, starts_at
):
    url = module_seeded_url
    session_id, tutor, (student,) = lesson(url, state=state, starts_at=starts_at)
    cookie = sign_in(client, tutor.username)

    ok(create(client, cookie, session_id, student), CREATED)


@pytest.mark.parametrize(
    "state", ["offered", "conflict", "cancelled", "skipped_closure"]
)
def test_other_states_are_not_notable(module_seeded_url, client, state):
    url = module_seeded_url
    session_id, tutor, (student,) = lesson(url, state=state, starts_at=TOMORROW)
    cookie = sign_in(client, tutor.username)

    response = create(client, cookie, session_id, student)

    assert code_of(response) == NOT_NOTABLE
    assert response.json()["details"] == {"state": state}
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM session_notes WHERE class_session_id = :id",
            {"id": session_id},
        )[0].n
        == 0
    )


def test_the_create_order(module_seeded_url, client, owner):
    url = module_seeded_url
    session_id, tutor, (student,) = lesson(url)
    cookie = sign_in(client, tutor.username)
    unknown = uuid.uuid4()

    # 1. 401.
    client.cookies.clear()
    assert (
        code_of(
            client.post(
                notes_path(session_id), json={"student_id": str(student), "body": "x"}
            )
        )[0]
        == 401
    )
    # 2. 422 schema: an empty body, too long, an unknown field.
    for body, code in (("   ", "body_required"), ("x" * 4001, "too_long")):
        response = post(
            client,
            cookie,
            notes_path(session_id),
            {"student_id": str(student), "body": body},
        )
        assert code_of(response) == INVALID
        assert response.json()["errors"][0]["code"] == code
    response = post(
        client,
        cookie,
        notes_path(session_id),
        {"student_id": str(student), "body": "x", "visibility": "family"},
    )
    assert code_of(response) == INVALID
    # 3. 404: an unknown session, another tutor's session.
    other = sign_in(client, a_tutor(url).username)
    assert_same_not_found(
        create(client, other, session_id, student),
        create(client, other, unknown, student),
    )
    # 4. 403 note_tutor_only for a reviewer.
    staff = sign_in(client, owner.username)
    assert code_of(create(client, staff, session_id, student)) == TUTOR_ONLY
    assert code_of(create(client, staff, unknown, student)) == NOT_FOUND
    # 7. 422 student_not_participant: a non-participant and an unknown id
    #    are the same.
    outsider = create(client, cookie, session_id, new_student(url))
    nobody = create(client, cookie, session_id, uuid.uuid4())
    for response in (outsider, nobody):
        assert code_of(response) == INVALID
        assert response.json()["errors"] == [
            {
                "path": ["body", "student_id"],
                "code": "student_not_participant",
                "message": "The student doesn't take part in this session.",
            }
        ]
    # 9-10. 201.
    ok(create(client, cookie, session_id, student), CREATED)


def test_an_unassigned_session_has_no_tutor(module_seeded_url, client, owner):
    url = module_seeded_url
    student = new_student(url)
    session_id = stored(url, TOMORROW, state="unassigned", students=(student,))
    staff = sign_in(client, owner.username)

    assert code_of(create(client, staff, session_id, student)) == TUTOR_ONLY
    assert code_of(get(client, staff, notes_path(session_id))) == OK


# --- D: edit, publish, unpublish, delete ----------------------------------------------


def test_the_note_lifecycle(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    session_id, tutor, cookie, note = written(client, url, family=family)
    note_id = note["id"]

    # D2: edit, then the same text again (no write, no version bump).
    edited = ok(edit(client, cookie, note_id, 1, "  Repasar fracciones  "))
    assert (edited["body"], edited["version"], edited["edited"]) == (
        "Repasar fracciones",
        2,
        True,
    )
    same = ok(edit(client, cookie, note_id, 2, "Repasar fracciones"))
    assert same["version"] == 2
    [update] = [
        e
        for e in events(url, action="update", record_type="session_notes")
        if str(e.record_id) == note_id
    ]
    assert update.changes["body"] == {
        "old": note["body"],
        "new": "Repasar fracciones",
    }

    # D3: publish → family, the guardian of the minor is notified.
    shared = ok(publish(client, cookie, note_id, 2))
    assert (shared["visibility"], shared["version"]) == ("family", 3)
    assert shared["published_by"] == {"display_name": "Tutora Notas"}
    assert datetime.fromisoformat(shared["published_at"]) == NOW
    assert (shared["can_edit"], shared["can_unpublish"], shared["can_delete"]) == (
        False,
        True,
        False,
    )
    [sent] = notifications(url, PUBLISHED_TYPE, class_session_id=session_id)
    assert sent.recipient_user_id == family.user.id
    assert sent.params == {
        "student_id": str(family.student_id),
        "class_session_id": session_id,
        "starts_at": STARTED.isoformat().replace("+00:00", "Z"),
    }
    assert bodies(
        get(
            client,
            sign_in(client, family.user.username),
            family_path(family.student_id),
        )
    ) == ["Repasar fracciones"]

    # D4: the author stops sharing it: no reason needed, nobody notified.
    back = ok(unpublish(client, cookie, note_id, 3))
    assert (back["visibility"], back["unpublish_reason"]) == ("internal", None)
    assert datetime.fromisoformat(back["unpublished_at"]) == NOW
    assert notifications(url, UNPUBLISHED_TYPE, note_id=note_id) == []
    assert (
        bodies(
            get(
                client,
                sign_in(client, family.user.username),
                family_path(family.student_id),
            )
        )
        == []
    )

    # Republished: notified again (choice), the unpublish fields cleared.
    again = ok(publish(client, cookie, note_id, 4))
    assert (again["unpublished_at"], again["unpublish_reason"]) == (None, None)
    assert len(notifications(url, PUBLISHED_TYPE, class_session_id=session_id)) == 2
    ok(unpublish(client, cookie, note_id, 5, "Me equivoqué"))
    assert note_row(url, note_id).unpublish_reason == "Me equivoqué"

    # D5: delete → 204, then 404 everywhere and gone from every list.
    assert code_of(delete(client, cookie, note_id, 6)) == NO_CONTENT
    row = note_row(url, note_id)
    assert (row.deleted_by_user_id, row.visibility) == (tutor.id, "internal")
    assert row.deleted_at is not None
    assert any(
        str(e.record_id) == note_id
        for e in events(url, action="soft_delete", record_type="session_notes")
    )
    unknown = uuid.uuid4()
    for write in (
        lambda nid: edit(client, cookie, nid, 7, "x"),
        lambda nid: publish(client, cookie, nid, 7),
        lambda nid: unpublish(client, cookie, nid, 7),
        lambda nid: delete(client, cookie, nid, 7),
    ):
        assert_same_not_found(write(note_id), write(unknown))
    assert bodies(get(client, cookie, notes_path(session_id))) == []
    assert bodies(get(client, cookie, history_path(family.student_id))) == []


def test_the_write_order(module_seeded_url, client, owner):
    url = module_seeded_url
    session_id, tutor, cookie, note = written(client, url)
    note_id = note["id"]
    unknown = uuid.uuid4()
    [student] = [uuid.UUID(note["student"]["id"])]
    # Another tutor currently assigned to the student (their own session).
    assigned = a_tutor(url, "Tutor Asignado")
    lesson(url, tutor=assigned, students=(student,), starts_at=TOMORROW)
    other = sign_in(client, assigned.username)
    staff = sign_in(client, owner.username)

    # 1. 401.
    client.cookies.clear()
    assert (
        code_of(client.patch(note_path(note_id), json={"version": 1, "body": "x"}))[0]
        == 401
    )
    # 2. 422: no version.
    assert code_of(patch(client, cookie, note_path(note_id), {"body": "x"})) == INVALID
    assert code_of(post(client, cookie, note_path(note_id, "/publish"), {})) == INVALID
    client.cookies.clear()
    assert (
        code_of(client.delete(note_path(note_id), headers=signed_headers(cookie)))
        == INVALID
    )
    # 3. 404 for an unrelated tutor, the same as an unknown id.
    stranger = sign_in(client, a_tutor(url, "Tutor Ajeno").username)
    assert_same_not_found(
        publish(client, stranger, note_id, 1), publish(client, stranger, unknown, 1)
    )
    # 4. 403 note_author_only: the assigned tutor and a reviewer.
    for caller in (other, staff):
        assert code_of(edit(client, caller, note_id, 1, "x")) == AUTHOR_ONLY
        assert code_of(publish(client, caller, note_id, 1)) == AUTHOR_ONLY
        assert code_of(delete(client, caller, note_id, 1)) == AUTHOR_ONLY
    assert code_of(unpublish(client, other, note_id, 1, "x")) == AUTHOR_ONLY
    # 6. 409 version_conflict (before the state check).
    assert code_of(publish(client, cookie, note_id, 7)) == VERSION_CONFLICT
    # 7. The state codes.
    assert code_of(unpublish(client, cookie, note_id, 1)) == NOT_PUBLISHED
    ok(publish(client, cookie, note_id, 1))
    assert code_of(publish(client, cookie, note_id, 2)) == ALREADY
    assert code_of(edit(client, cookie, note_id, 2, "x")) == PUBLISHED
    assert code_of(delete(client, cookie, note_id, 2)) == PUBLISHED
    assert note_row(url, note_id).version == 2


def test_staff_unpublish_needs_a_reason_and_notifies_the_author(
    module_seeded_url, client, coordinator
):
    url = module_seeded_url
    session_id, tutor, cookie, note = written(client, url)
    note_id = note["id"]
    ok(publish(client, cookie, note_id, 1))
    staff = sign_in(client, coordinator.username)

    for reason in (None, "   "):
        response = unpublish(client, staff, note_id, 2, reason)
        assert code_of(response) == INVALID
        assert response.json()["errors"] == [
            {
                "path": ["body", "reason"],
                "code": "reason_required",
                "message": "Give a reason.",
            }
        ]
    response = unpublish(client, staff, note_id, 2, "x" * 501)
    assert code_of(response) == INVALID
    assert response.json()["errors"][0]["code"] == "reason_too_long"

    back = ok(unpublish(client, staff, note_id, 2, "  Contiene un dato médico  "))

    assert (back["visibility"], back["unpublish_reason"]) == (
        "internal",
        "Contiene un dato médico",
    )
    assert (back["can_edit"], back["can_publish"], back["can_unpublish"]) == (
        False,
        False,
        False,
    )
    row = note_row(url, note_id)
    assert row.unpublished_by_user_id == coordinator.id
    [sent] = notifications(url, UNPUBLISHED_TYPE, note_id=note_id)
    assert sent.recipient_user_id == tutor.id
    assert sent.params == {
        "note_id": note_id,
        "class_session_id": session_id,
        "starts_at": STARTED.isoformat().replace("+00:00", "Z"),
    }
    # The author sees the reason on the note.
    [mine] = get(client, cookie, notes_path(session_id)).json()["items"]
    assert mine["unpublish_reason"] == "Contiene un dato médico"
    assert mine["can_publish"] is True


# --- E: staff and tutor reads ---------------------------------------------------------


def test_the_session_list(module_seeded_url, client, owner):
    url = module_seeded_url
    first, second = new_student(url), new_student(url)
    session_id, tutor, _ = lesson(url, students=(first, second), kind="group")
    cookie = sign_in(client, tutor.username)
    a = ok(create(client, cookie, session_id, first, "Primera"), CREATED)
    ok(create(client, cookie, session_id, second, "Segunda"), CREATED)

    assert bodies(get(client, cookie, notes_path(session_id))) == ["Primera", "Segunda"]
    assert bodies(
        get(client, cookie, notes_path(session_id), student_id=str(first))
    ) == ["Primera"]
    assert (
        bodies(
            get(
                client, cookie, notes_path(session_id), student_id=str(new_student(url))
            )
        )
        == []
    )
    for user in (owner, create_user(url, roles=(ADMINISTRATOR,))):
        staff = sign_in(client, user.username)
        [seen, _] = get(client, staff, notes_path(session_id)).json()["items"]
        assert seen["id"] == a["id"]
        assert (seen["is_own"], seen["can_edit"], seen["can_unpublish"]) == (
            False,
            False,
            False,
        )
    # Finance, another tutor, a guardian and a student: the same 404.
    unknown = uuid.uuid4()
    for user in (
        create_user(url, roles=(FINANCE_STAFF,)),
        a_tutor(url),
        create_user(url, roles=(GUARDIAN,)),
        student_account(url)[0],
    ):
        caller = sign_in(client, user.username)
        assert_same_not_found(
            get(client, caller, notes_path(session_id)),
            get(client, caller, notes_path(unknown)),
        )


def test_the_student_history(module_seeded_url, client, owner):
    url = module_seeded_url
    student = new_student(url)
    session_a, tutor_a, _ = lesson(url, students=(student,))
    session_b, tutor_b, _ = lesson(url, students=(student,), starts_at=TOMORROW)
    cookie_a = sign_in(client, tutor_a.username)
    cookie_b = sign_in(client, tutor_b.username)
    ok(create(client, cookie_a, session_a, student, "De A"), CREATED)
    ok(create(client, cookie_b, session_b, student, "De B 1"), CREATED)
    ok(create(client, cookie_b, session_b, student, "De B 2"), CREATED)

    # Both tutors are assigned: every note, newest first; others read-only.
    seen = get(client, cookie_a, history_path(student)).json()["items"]
    assert [item["body"] for item in seen] == ["De B 2", "De B 1", "De A"]
    assert [item["can_edit"] for item in seen] == [False, False, True]
    assert [item["author"]["display_name"] for item in seen][-1] == "Tutora Notas"
    # Pagination.
    first = get(client, cookie_a, history_path(student), limit=2).json()
    assert [item["body"] for item in first["items"]] == ["De B 2", "De B 1"]
    rest = get(
        client, cookie_a, history_path(student), limit=2, cursor=first["next_cursor"]
    ).json()
    assert ([item["body"] for item in rest["items"]], rest["next_cursor"]) == (
        ["De A"],
        None,
    )
    assert code_of(get(client, cookie_a, history_path(student), cursor="x")) == (
        400,
        "invalid_cursor",
    )
    assert code_of(get(client, cookie_a, history_path(student), limit=51)) == INVALID
    # A reviewer reads all; Finance, an unrelated tutor, families: 404.
    staff = sign_in(client, owner.username)
    assert len(bodies(get(client, staff, history_path(student)))) == 3
    unknown = uuid.uuid4()
    assert_same_not_found(
        get(client, staff, history_path(unknown)),
        get(client, staff, history_path(unknown)),
    )
    for user in (create_user(url, roles=(FINANCE_STAFF,)), a_tutor(url)):
        caller = sign_in(client, user.username)
        assert_same_not_found(
            get(client, caller, history_path(student)),
            get(client, caller, history_path(unknown)),
        )


def test_a_former_students_notes_are_read_only(module_seeded_url, client, at_now):
    """W5.4: the last session ended 31+ days ago and nothing is upcoming:
    the author still reads their notes, read-only (403 note_read_only)."""
    url = module_seeded_url
    long_ago = NOW - timedelta(days=40)
    session_id, tutor, (student,) = lesson(url, state="completed", starts_at=long_ago)
    at_now.set(long_ago + timedelta(hours=2))
    cookie = sign_in(client, tutor.username)
    note = ok(create(client, cookie, session_id, student, "Antes"), CREATED)
    other = ok(create(client, cookie, session_id, student, "Otra"), CREATED)
    ok(publish(client, cookie, other["id"], 1))
    # A note of another tutor about the student stays hidden from them.
    at_now.set(NOW)
    cookie = sign_in(client, tutor.username)

    assert code_of(edit(client, cookie, note["id"], 1, "x")) == READ_ONLY
    assert code_of(publish(client, cookie, note["id"], 1)) == READ_ONLY
    assert code_of(delete(client, cookie, note["id"], 1)) == READ_ONLY
    assert code_of(unpublish(client, cookie, other["id"], 2)) == READ_ONLY
    assert code_of(create(client, cookie, session_id, student)) == READ_ONLY
    history = get(client, cookie, history_path(student)).json()["items"]
    assert [item["body"] for item in history] == ["Otra", "Antes"]
    assert not any(
        item[flag]
        for item in history
        for flag in ("can_edit", "can_publish", "can_unpublish", "can_delete")
    )
    assert bodies(get(client, cookie, notes_path(session_id))) == ["Antes", "Otra"]
    # Another tutor's later note about the student isn't theirs to read.
    later_session, later_tutor, _ = lesson(url, students=(student,))
    ok(
        create(
            client,
            sign_in(client, later_tutor.username),
            later_session,
            student,
            "Nueva",
        ),
        CREATED,
    )
    assert [
        item["body"]
        for item in get(client, cookie, history_path(student)).json()["items"]
    ] == [
        "Otra",
        "Antes",
    ]


# --- F: the family route --------------------------------------------------------------


def test_the_family_route_shows_only_shared_notes(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    session_id, tutor, cookie, internal = written(
        client, url, family=family, body="Interna"
    )
    shared = ok(
        create(client, cookie, session_id, family.student_id, "Compartida"), CREATED
    )
    ok(publish(client, cookie, shared["id"], 1))
    guardian = sign_in(client, family.user.username)

    response = get(client, guardian, family_path(family.student_id))

    assert code_of(response) == OK
    [item] = response.json()["items"]
    assert set(item) == {
        "id",
        "class_session_id",
        "session_starts_at",
        "subject_name",
        "author",
        "body",
        "published_at",
    }
    assert (item["body"], item["author"]) == (
        "Compartida",
        {"display_name": "Tutora Notas"},
    )
    assert item["class_session_id"] == session_id
    assert "Interna" not in response.text
    assert str(tutor.id) not in response.text
    # The class filter: this class, another family's class, an unknown one.
    assert bodies(
        get(
            client,
            guardian,
            family_path(family.student_id),
            class_session_id=session_id,
        )
    ) == ["Compartida"]
    other_family = guardian_with_child(url)
    other_session, _, other_cookie, other_note = written(
        client, url, family=other_family
    )
    ok(publish(client, other_cookie, other_note["id"], 1))
    for filtered in (other_session, str(uuid.uuid4())):
        assert (
            bodies(
                get(
                    client,
                    guardian,
                    family_path(family.student_id),
                    class_session_id=filtered,
                )
            )
            == []
        )
    # A session cancelled after the note was shared: still shown (#67 shows
    # once-confirmed cancelled sessions); no new notes on it.
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'cancelled', cancelled_at = now(),"
        " cancellation_reason = 'Centro cerrado' WHERE id = :id",
        {"id": session_id},
    )
    assert bodies(get(client, guardian, family_path(family.student_id))) == [
        "Compartida"
    ]
    assert code_of(create(client, cookie, session_id, family.student_id)) == NOT_NOTABLE


def test_the_family_route_access(module_seeded_url, client, owner):
    url = module_seeded_url
    unknown = uuid.uuid4()
    # The student themself (an adult with a login).
    me, mine = student_account(url)
    session_id, tutor, (student,) = lesson(url, students=(mine,))
    cookie = sign_in(client, tutor.username)
    note = ok(create(client, cookie, session_id, mine, "Para el alumno"), CREATED)
    ok(publish(client, cookie, note["id"], 1))
    own = sign_in(client, me.username)
    assert bodies(get(client, own, family_path(mine))) == ["Para el alumno"]
    # Another student, staff, the tutor, Finance and an unlinked guardian:
    # the same 404 as an unknown id.
    for user in (
        student_account(url)[0],
        owner,
        tutor,
        create_user(url, roles=(FINANCE_STAFF,)),
        create_user(url, roles=(GUARDIAN,)),
    ):
        caller = sign_in(client, user.username)
        assert_same_not_found(
            get(client, caller, family_path(mine)),
            get(client, caller, family_path(unknown)),
        )


def test_an_adult_needs_the_consent_and_a_revocation_applies_at_once(
    module_seeded_url, client
):
    url = module_seeded_url
    family = guardian_with_child(url, adult=True)
    session_id, _, cookie, note = written(client, url, family=family, body="Adulto")
    ok(publish(client, cookie, note["id"], 1))
    guardian = sign_in(client, family.user.username)

    assert code_of(get(client, guardian, family_path(family.student_id))) == CONSENT
    give_academic_consent(url, family)
    assert bodies(get(client, guardian, family_path(family.student_id))) == ["Adulto"]
    revoke_academic_consent(url, family)
    assert code_of(get(client, guardian, family_path(family.student_id))) == CONSENT
    # An inactive guardian profile: 404.
    deactivate_guardian(url, family.guardian_id)
    assert_same_not_found(
        get(client, guardian, family_path(family.student_id)),
        get(client, guardian, family_path(uuid.uuid4())),
    )


def test_a_group_note_reaches_only_its_students_family(module_seeded_url, client):
    url = module_seeded_url
    a, b = guardian_with_child(url), guardian_with_child(url)
    session_id, tutor, _ = lesson(
        url, students=(a.student_id, b.student_id), kind="group"
    )
    cookie = sign_in(client, tutor.username)
    note = ok(create(client, cookie, session_id, a.student_id, "Solo para A"), CREATED)
    ok(publish(client, cookie, note["id"], 1))

    [sent] = notifications(url, PUBLISHED_TYPE, class_session_id=session_id)
    assert sent.recipient_user_id == a.user.id
    assert bodies(
        get(
            client,
            sign_in(client, a.user.username),
            family_path(a.student_id),
            class_session_id=session_id,
        )
    ) == ["Solo para A"]
    b_cookie = sign_in(client, b.user.username)
    response = get(
        client, b_cookie, family_path(b.student_id), class_session_id=session_id
    )
    assert bodies(response) == []
    assert "Solo para A" not in response.text
    assert_same_not_found(
        get(client, b_cookie, family_path(a.student_id)),
        get(client, b_cookie, family_path(uuid.uuid4())),
    )


# --- I3: IDOR -------------------------------------------------------------------------


def test_a_tutor_who_lost_the_role_gets_404_everywhere(module_seeded_url, client):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR, GUARDIAN), display_name="Ex Tutora")
    session_id, _, (student,) = lesson(url, tutor=tutor)
    cookie = sign_in(client, tutor.username)
    note = ok(create(client, cookie, session_id, student), CREATED)
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": tutor.id},
    )
    cookie = sign_in(client, tutor.username)
    unknown = uuid.uuid4()

    assert_same_not_found(
        get(client, cookie, notes_path(session_id)),
        get(client, cookie, notes_path(unknown)),
    )
    assert_same_not_found(
        create(client, cookie, session_id, student),
        create(client, cookie, unknown, student),
    )
    assert_same_not_found(
        get(client, cookie, history_path(student)),
        get(client, cookie, history_path(unknown)),
    )
    assert_same_not_found(
        publish(client, cookie, note["id"], 1), publish(client, cookie, unknown, 1)
    )
    assert_same_not_found(
        edit(client, cookie, note["id"], 1, "x"), edit(client, cookie, unknown, 1, "x")
    )


def test_finance_gets_404_everywhere(module_seeded_url, client):
    url = module_seeded_url
    session_id, _, cookie, note = written(client, url)
    student = note["student"]["id"]
    ok(publish(client, cookie, note["id"], 1))
    finance = sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username)
    unknown = uuid.uuid4()

    for path_of in (notes_path, history_path, family_path):
        assert_same_not_found(
            get(
                client,
                finance,
                path_of(session_id if path_of is notes_path else student),
            ),
            get(client, finance, path_of(unknown)),
        )
    assert_same_not_found(
        unpublish(client, finance, note["id"], 2, "x"),
        unpublish(client, finance, unknown, 2, "x"),
    )
    assert_same_not_found(
        create(client, finance, session_id, student),
        create(client, finance, unknown, student),
    )


def test_the_original_tutor_after_a_real_substitution(
    module_seeded_url, client, owner, at_now
):
    """#55: once the substitute accepted, the original tutor (the student not
    otherwise assigned) loses the session route; their own notes stay
    readable (read-only) and the substitute reads them as the current
    tutor."""
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
    original = sign_in(client, setup.tutor.username)
    assert code_of(answer(client, original, offer_id, "accept")) == OK
    note = ok(
        create(client, original, created["id"], setup.student, "Del original"), CREATED
    )
    detail = get(client, staff, f"/api/v1/class-sessions/{created['id']}").json()
    item = substituted(client, staff, detail, substitute.id)
    taker = sign_in(client, substitute.username)
    assert code_of(answer(client, taker, item["offer_id"], "accept")) == OK

    original = sign_in(client, setup.tutor.username)
    unknown = uuid.uuid4()
    assert_same_not_found(
        get(client, original, notes_path(created["id"])),
        get(client, original, notes_path(unknown)),
    )
    assert_same_not_found(
        create(client, original, created["id"], setup.student),
        create(client, original, unknown, setup.student),
    )
    assert code_of(edit(client, original, note["id"], 1, "x")) == READ_ONLY
    [own] = get(client, original, history_path(setup.student)).json()["items"]
    assert (own["body"], own["can_edit"]) == ("Del original", False)
    # The substitute reads it as the current tutor, but it isn't theirs.
    taker = sign_in(client, substitute.username)
    [seen] = get(client, taker, notes_path(created["id"])).json()["items"]
    assert (seen["body"], seen["is_own"], seen["can_edit"]) == (
        "Del original",
        False,
        False,
    )
    assert code_of(publish(client, taker, note["id"], 1)) == AUTHOR_ONLY
    ok(create(client, taker, created["id"], setup.student, "Del sustituto"), CREATED)


# --- B3: recipients -------------------------------------------------------------------


def _recipients(
    url: str, student_id: uuid.UUID, today: date = date(2031, 3, 5)
) -> list[uuid.UUID]:
    async def _run(session: AsyncSession) -> list[uuid.UUID]:
        return await families.academic_notification_recipients(
            session, student_id, today=today
        )

    return in_uow(url, _run)


def _guardian(
    url: str,
    student_id: uuid.UUID,
    *,
    user: bool = True,
    consent: bool = False,
    active: bool = True,
) -> uuid.UUID | None:
    account = create_user(url, roles=(GUARDIAN,)) if user else None

    async def _make(session: AsyncSession) -> None:
        guardian: Guardian = await persist(
            session,
            GuardianFactory,
            user_id=None if account is None else account.id,
            is_active=active,
        )
        student = await session.get(Student, student_id)
        assert student is not None
        await link_guardian(session, student, guardian)
        if consent:
            await login_consent(
                session,
                student,
                guardian,
                recorded_by=guardian.user_id or uuid.uuid4(),
                scope="guardian_academic_access",
            )

    in_uow(url, _make)
    return None if account is None else account.id


def test_the_recipients(module_seeded_url):
    url = module_seeded_url

    async def _student(
        session: AsyncSession, birthdate: date, login: bool
    ) -> tuple[uuid.UUID, uuid.UUID | None]:
        student = await persist_student(session, contacts=0, birthdate=birthdate)
        user_id = None
        if login:
            user_id = (await persist_user(session, roles=(STUDENT,))).id
            await student_login(session, student, user_id)
        return student.id, user_id

    # A minor: every active linked guardian with an account, no consent needed.
    minor, _ = in_uow(url, lambda s: _student(s, MINOR_BIRTHDATE, False))
    g1 = _guardian(url, minor)
    _guardian(url, minor, active=False)
    _guardian(url, minor, user=False)
    assert _recipients(url, minor) == [g1]
    # An adult with a login: their own account, plus the consenting guardian.
    adult, own = in_uow(url, lambda s: _student(s, ADULT_BIRTHDATE, True))
    with_consent = _guardian(url, adult, consent=True)
    _guardian(url, adult)
    assert _recipients(url, adult) == sorted([own, with_consent])
    # A revoked consent: gone at once.
    run_sql(
        url,
        "UPDATE consent_records SET revoked_at = now(), revoked_by_user_id = :u,"
        " revocation_cause = 'revoked' WHERE student_id = :s",
        {"s": adult, "u": with_consent},
    )
    assert _recipients(url, adult) == [own]
    # A minor turning 18: the guardian without consent drops out.
    assert _recipients(url, minor, today=date(2034, 5, 1)) == []
    assert _recipients(url, uuid.uuid4()) == []


# --- Budgets (E1, E2, F) --------------------------------------------------------------


def _many(url: str, client: TestClient, count: int) -> tuple[str, CreatedUser, Family]:
    family = guardian_with_child(url)
    session_id, tutor, _ = lesson(url, students=(family.student_id,))
    cookie = sign_in(client, tutor.username)
    for _ in range(count):
        note = ok(create(client, cookie, session_id, family.student_id), CREATED)
        ok(publish(client, cookie, note["id"], 1))
    return session_id, tutor, family


@pytest.mark.parametrize("count", [1, 20])
def test_the_statement_budgets(module_seeded_url, client, owner, count):
    url = module_seeded_url
    session_id, tutor, family = _many(url, client, count)
    tutor_principal = principal_of(tutor, TUTOR)
    reviewer = principal_of(owner, OWNER)
    guardian = principal_of(family.user, GUARDIAN)
    sid = uuid.UUID(session_id)

    for principal in (tutor_principal, reviewer):
        statements, listed = count_statements(
            url,
            lambda s, p=principal: workflow.list_session_notes(
                s, p, sid, student_id=None
            ),
        )
        assert statements <= 6, statements
        assert len(listed.items) == count
        statements, page = count_statements(
            url,
            lambda s, p=principal: workflow.list_student_notes(
                s, p, family.student_id, cursor=None, limit=50
            ),
        )
        assert statements <= 6, statements
        assert len(page.items) == count
    statements, page = count_statements(
        url,
        lambda s: workflow.list_family_learning_notes(
            s, guardian, family.student_id, class_session_id=None, cursor=None, limit=50
        ),
    )
    assert statements <= 6, statements
    assert len(page.items) == count


def test_the_family_budget_for_an_adult(module_seeded_url, client):
    """The adult rule reads the zone: still at most 6 statements."""
    url = module_seeded_url
    family = guardian_with_child(url, adult=True, consent=True)
    session_id, _, cookie, note = written(client, url, family=family)
    ok(publish(client, cookie, note["id"], 1))
    guardian = principal_of(family.user, GUARDIAN)

    statements, page = count_statements(
        url,
        lambda s: workflow.list_family_learning_notes(
            s, guardian, family.student_id, class_session_id=None, cursor=None, limit=20
        ),
    )
    assert statements <= 6, statements
    assert len(page.items) == 1
