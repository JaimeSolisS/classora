"""Recently deleted records (#129 B1-B3) with real logins through the real
app: ``GET /api/v1/deleted-records`` and ``POST
/api/v1/deleted-records/{kind}/{record_id}/restore``.

Each of the five kinds is created and deleted through its own routes, then
listed and restored: the list's field pin, only the caller's own records
inside the window, the statement cap, an empty list for family and Finance
users, the restore order (``404`` for someone else's, a live, an expired
and an unknown record; ``version_conflict``; the kind's ``403`` once the
tutor no longer teaches the student; ``deleted_record_restore_conflict``),
the ``restore`` audit event, no notification, and a restored
``changes_requested`` report that can be submitted again.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    TUTOR,
)
from app.workflows import deleted_records as workflow
from tests.authz.assertions import assert_same_not_found
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import test_assignments as assignments
from tests.integration import test_evaluations as evaluations
from tests.integration import test_learning_plans as plans
from tests.integration import test_progress_reports as reports
from tests.integration import test_session_notes as notes
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.schedule_support import principal_of
from tests.integration.session_request_support import (
    guardian_with_child,
    student_account,
)
from tests.integration.test_class_session_create import new_subject

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
LIST = "/api/v1/deleted-records"

OK = (200, None)
CREATED = (201, None)
NO_CONTENT = (204, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
VERSION_CONFLICT = (409, "version_conflict")
RESTORE_CONFLICT = (409, "deleted_record_restore_conflict")

ITEM_KEYS = {
    "kind",
    "id",
    "version",
    "student",
    "title",
    "reference_date",
    "deleted_at",
    "restorable_until",
}

TABLES = {
    "session_note": "session_notes",
    "evaluation": "evaluations",
    "learning_plan": "learning_plans",
    "assignment": "assignments",
    "progress_report": "progress_reports",
}
KINDS = tuple(TABLES)


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
def staff(client: TestClient, module_seeded_url: str) -> Any:
    coordinator = create_user(
        module_seeded_url, roles=(ACADEMIC_COORDINATOR,), display_name="Coordinadora"
    )
    return sign_in(client, coordinator.username)


# --- Helpers --------------------------------------------------------------------------


def restore_path(kind: str, record_id: Any) -> str:
    return f"{LIST}/{kind}/{record_id}/restore"


def restore(
    client: TestClient, cookie: Any, kind: str, record_id: Any, version: int
) -> Response:
    return post(client, cookie, restore_path(kind, record_id), {"version": version})


def listed(client: TestClient, cookie: Any) -> dict[str, Any]:
    response = get(client, cookie, LIST)
    assert code_of(response) == OK, response.text
    body: dict[str, Any] = response.json()
    return body


def ok(response: Response, expected: tuple[int, None] = OK) -> Any:
    assert code_of(response) == expected, response.text
    return response.json() if response.content else None


def row(url: str, kind: str, record_id: Any) -> Any:
    [found] = run_sql(
        url,
        f"SELECT * FROM {TABLES[kind]} WHERE id = :id",  # noqa: S608
        {"id": record_id},
    )
    return found


class Deleted:
    """A record its tutor created and deleted through the kind's routes."""

    def __init__(
        self,
        kind: str,
        tutor: CreatedUser,
        cookie: Any,
        record_id: str,
        student: uuid.UUID,
        url: str,
    ) -> None:
        self.kind = kind
        self.tutor = tutor
        self.cookie = cookie
        self.id = record_id
        self.student = student
        self.url = url

    @property
    def version(self) -> int:
        return int(row(self.url, self.kind, self.id).version)


def deleted(
    client: TestClient,
    url: str,
    staff: Any,
    kind: str,
    report: dict[str, Any] | None = None,
) -> Deleted:
    """A new tutor's record of ``kind``, deleted by them."""
    if kind == "session_note":
        session_id, tutor, (student,) = notes.lesson(url)
        cookie = sign_in(client, tutor.username)
        view = ok(notes.create(client, cookie, session_id, student), CREATED)
        ok(notes.delete(client, cookie, view["id"], view["version"]), NO_CONTENT)
    elif kind == "evaluation":
        subject = new_subject(url)
        _, tutor, (student,) = evaluations.teaching(url, subject_id=subject)
        template = evaluations.new_template(client, staff, subject_id=subject)
        cookie = sign_in(client, tutor.username)
        view = ok(
            evaluations.create(
                client,
                cookie,
                student,
                template,
                scores=evaluations.full_scores(template),
            ),
            CREATED,
        )
        ok(evaluations.delete(client, cookie, view["id"], view["version"]), NO_CONTENT)
    elif kind == "learning_plan":
        tutor, cookie, student, _, view = plans.drafted(client, url)
        ok(plans.delete(client, cookie, view["id"], view["version"]), NO_CONTENT)
    elif kind == "assignment":
        tutor, cookie, (student, *_), _, view = assignments.homework(client, url)
        ok(assignments.delete(client, cookie, view["id"], view["version"]), NO_CONTENT)
    else:
        tutor, cookie, student, _, view = reports.drafted(client, url, **(report or {}))
        ok(reports.delete(client, cookie, view["id"], view["version"]), NO_CONTENT)
    return Deleted(kind, tutor, cookie, view["id"], student, url)


def end_teaching(url: str, record: Deleted) -> None:
    """The tutor no longer teaches the student: their sessions go to
    another tutor (as a substitution does)."""
    other = create_user(url, roles=(TUTOR,))
    run_sql(
        url,
        "UPDATE class_sessions SET tutor_user_id = :o WHERE tutor_user_id = :t",
        {"o": other.id, "t": record.tutor.id},
    )


# --- B1, B2: every kind ---------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_is_listed_and_restored(module_seeded_url, client, staff, kind):
    url = module_seeded_url
    record = deleted(client, url, staff, kind)
    stored = row(url, kind, record.id)
    assert stored.deleted_by_user_id == record.tutor.id

    body = listed(client, record.cookie)

    assert body["has_more"] is False
    [item] = body["items"]
    assert set(item) == ITEM_KEYS
    assert set(item["student"]) == {"id", "display_name"}
    assert (item["kind"], item["id"], item["version"]) == (
        kind,
        record.id,
        stored.version,
    )
    assert item["student"]["id"] == str(record.student)
    assert item["student"]["display_name"]
    deleted_at = datetime.fromisoformat(item["deleted_at"])
    assert deleted_at == stored.deleted_at
    assert datetime.fromisoformat(item["restorable_until"]) == deleted_at + timedelta(
        days=30
    )
    if kind in ("session_note", "progress_report"):
        assert item["title"] is None
    else:
        assert item["title"]
    assert date.fromisoformat(item["reference_date"])
    # No content of any kind in the list.
    for secret in ("body", "summary", "narrative", "scores", "feedback"):
        assert secret not in item
    [notified] = run_sql(url, "SELECT count(*) AS n FROM notifications")

    restored = ok(restore(client, record.cookie, kind, record.id, stored.version))

    assert restored == {"kind": kind, "id": record.id, "version": stored.version + 1}
    live = row(url, kind, record.id)
    assert (live.deleted_at, live.deleted_by_user_id) == (None, None)
    assert live.version == stored.version + 1
    [event] = events(url, action="restore", record_id=uuid.UUID(record.id))
    assert event.actor_user_id == record.tutor.id
    assert event.record_type == TABLES[kind]
    # Nothing is notified (E3).
    assert run_sql(url, "SELECT count(*) AS n FROM notifications") == [notified]
    # The record is back as it was, and gone from the list.
    if kind == "session_note":
        assert live.visibility == "internal"
    elif kind != "assignment":
        assert live.status == "draft"
    assert listed(client, record.cookie)["items"] == []
    # A second restore is 404.
    assert code_of(restore(client, record.cookie, kind, record.id, live.version)) == (
        NOT_FOUND
    )


def test_a_restored_assignment_is_back_for_its_creator_without_a_notification(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    record = deleted(client, url, staff, "assignment")
    sets = run_sql(
        url,
        "SELECT count(*) AS n FROM notifications"
        " WHERE notification_type = 'academics.assignment_set'",
    )
    assert code_of(get(client, record.cookie, assignments.a_path(record.id))) == (
        NOT_FOUND
    )

    ok(restore(client, record.cookie, "assignment", record.id, record.version))

    view = ok(get(client, record.cookie, assignments.a_path(record.id)))
    assert [r["student"]["id"] for r in view["roster"]] == [str(record.student)]
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM notifications"
            " WHERE notification_type = 'academics.assignment_set'",
        )
        == sets
    )


# --- B1: who sees what ----------------------------------------------------------------


def test_only_the_deleter_sees_their_records_inside_the_window(
    module_seeded_url, client, staff, at_now
):
    url = module_seeded_url
    record = deleted(client, url, staff, "session_note")
    other = sign_in(client, notes.a_tutor(url).username)
    deleted_at = row(url, "session_note", record.id).deleted_at

    assert listed(client, other) == {"items": [], "has_more": False}
    at_now.set(deleted_at + timedelta(days=30) - timedelta(seconds=1))
    cookie = sign_in(client, record.tutor.username)
    assert [i["id"] for i in listed(client, cookie)["items"]] == [record.id]
    at_now.set(deleted_at + timedelta(days=30))
    assert listed(client, cookie)["items"] == []
    # Past the window the restore is the 404 of an unknown id.
    assert_same_not_found(
        restore(client, cookie, "session_note", record.id, record.version),
        restore(client, cookie, "session_note", uuid.uuid4(), 1),
    )


def test_family_and_finance_users_get_an_empty_list(module_seeded_url, client):
    url = module_seeded_url
    family = guardian_with_child(url)
    student, _ = student_account(url)
    finance = create_user(url, roles=(FINANCE_STAFF,))

    for user in (family.user, student, finance):
        cookie = sign_in(client, user.username)
        assert listed(client, cookie) == {"items": [], "has_more": False}


def test_a_record_the_tutor_may_no_longer_delete_is_not_listed_and_gets_its_403(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    expected = {
        "session_note": (403, "note_read_only"),
        "evaluation": (403, "evaluation_read_only"),
        "learning_plan": (403, "plan_read_only"),
        "assignment": (403, "assignment_read_only"),
        "progress_report": (403, "progress_report_read_only"),
    }
    for kind, code in expected.items():
        record = deleted(client, url, staff, kind)
        end_teaching(url, record)

        assert listed(client, record.cookie)["items"] == [], kind
        response = restore(client, record.cookie, kind, record.id, record.version)
        assert code_of(response) == code, kind
        assert row(url, kind, record.id).deleted_at is not None


def test_the_list_takes_at_most_eight_statements(module_seeded_url, client):
    url = module_seeded_url
    tutor = notes.a_tutor(url)
    cookie = sign_in(client, tutor.username)
    principal = principal_of(tutor, TUTOR)

    slots = iter(range(1, 20))

    def delete_notes(count: int) -> None:
        for _ in range(count):
            session_id, _, (student,) = notes.lesson(
                url, tutor=tutor, starts_at=NOW - timedelta(hours=2 * next(slots))
            )
            view = ok(notes.create(client, cookie, session_id, student), CREATED)
            ok(notes.delete(client, cookie, view["id"], view["version"]), NO_CONTENT)

    delete_notes(1)
    one, found = count_statements(
        url, lambda session: workflow.list_deleted(session, principal)
    )
    delete_notes(4)
    many, more = count_statements(
        url, lambda session: workflow.list_deleted(session, principal)
    )

    assert (len(found.items), len(more.items)) == (1, 5)
    assert one <= 8
    assert many == one


def test_only_the_100_most_recent_are_listed(module_seeded_url, client):
    url = module_seeded_url
    session_id, tutor, (student,) = notes.lesson(url)
    cookie = sign_in(client, tutor.username)
    for minutes in range(101):
        run_sql(
            url,
            "INSERT INTO session_notes (class_session_id, student_id,"
            " author_user_id, body, visibility, deleted_at, deleted_by_user_id)"
            " VALUES (:s, :st, :u, 'Nota', 'internal', :at, :u)",
            {
                "s": session_id,
                "st": student,
                "u": tutor.id,
                "at": NOW - timedelta(minutes=minutes),
            },
        )

    body = listed(client, cookie)

    assert body["has_more"] is True
    assert len(body["items"]) == 100
    times = [datetime.fromisoformat(i["deleted_at"]) for i in body["items"]]
    assert times == sorted(times, reverse=True)
    assert times[0] == NOW


# --- B2: the restore order ------------------------------------------------------------


def test_the_restore_order(module_seeded_url, client, staff):
    url = module_seeded_url
    record = deleted(client, url, staff, "session_note")
    version = record.version
    other = sign_in(client, notes.a_tutor(url).username)
    session_id, _, (student,) = notes.lesson(
        url, tutor=record.tutor, starts_at=NOW - timedelta(hours=3)
    )
    live = ok(notes.create(client, record.cookie, session_id, student), CREATED)

    # 401.
    client.cookies.clear()
    assert (
        code_of(client.post(restore_path("session_note", record.id), json={}))[0] == 401
    )
    # 422: an unknown kind, no version.
    assert code_of(restore(client, record.cookie, "payment", record.id, version)) == (
        INVALID
    )
    assert (
        code_of(
            post(client, record.cookie, restore_path("session_note", record.id), {})
        )
        == INVALID
    )
    # 404: someone else's, a live record, a wrong kind, an unknown id.
    unknown = restore(client, record.cookie, "session_note", uuid.uuid4(), 1)
    assert_same_not_found(
        restore(client, other, "session_note", record.id, version), unknown
    )
    assert_same_not_found(
        restore(client, record.cookie, "session_note", live["id"], 1), unknown
    )
    assert_same_not_found(
        restore(client, record.cookie, "evaluation", record.id, version), unknown
    )
    # 409 version_conflict.
    assert (
        code_of(restore(client, record.cookie, "session_note", record.id, version - 1))
        == VERSION_CONFLICT
    )
    assert row(url, "session_note", record.id).deleted_at is not None
    ok(restore(client, record.cookie, "session_note", record.id, version))


def test_a_report_whose_period_is_taken_again_is_a_restore_conflict(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    weekly = {
        "kind": "weekly",
        "period_start": "2031-02-24",
        "period_end": ...,
    }
    record = deleted(client, url, staff, "progress_report", report=weekly)
    ok(reports.create(client, record.cookie, record.student, **weekly), CREATED)

    response = restore(
        client, record.cookie, "progress_report", record.id, record.version
    )

    assert code_of(response) == RESTORE_CONFLICT
    assert row(url, "progress_report", record.id).deleted_at is not None


def test_a_restored_report_sent_back_can_be_submitted_again(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    tutor, cookie, _, _, view = reports.submitted(client, url)
    view = ok(
        reports.act(
            client,
            staff,
            view["id"],
            "request-changes",
            view["version"],
            reason="Más detalle",
        )
    )
    ok(reports.delete(client, cookie, view["id"], view["version"]), NO_CONTENT)
    assert reports.request_row(url, view["id"]).status == "cancelled"
    version = row(url, "progress_report", view["id"]).version

    restored = ok(restore(client, cookie, "progress_report", view["id"], version))

    assert row(url, "progress_report", view["id"]).status == "changes_requested"
    resent = ok(reports.act(client, cookie, view["id"], "submit", restored["version"]))
    assert resent["status"] == "submitted"
    assert reports.request_row(url, view["id"]).status == "pending"


def test_a_rolled_back_restore_leaves_the_record_deleted(
    module_seeded_url, client, staff
):
    """B3: the row stays deleted for every other route until the restore
    commits."""
    url = module_seeded_url
    record = deleted(client, url, staff, "session_note")
    principal = principal_of(record.tutor, TUTOR)
    version = record.version
    seen: list[tuple[int, str | None]] = []

    async def _restore_then_fail() -> None:
        engine = short_lived_engine(url)
        try:
            with pytest.raises(RuntimeError, match="roll back"):
                async with UnitOfWork(create_sessionmaker(engine)) as uow:
                    await workflow.restore(
                        uow.session,
                        principal=principal,
                        kind=workflow.Kind.SESSION_NOTE,
                        record_id=uuid.UUID(record.id),
                        version=version,
                    )
                    # Another route, before the commit: still 404.
                    response = notes.edit(
                        client, record.cookie, record.id, version, "x"
                    )
                    seen.append(code_of(response))
                    raise RuntimeError("roll back")
        finally:
            await engine.dispose()

    asyncio.run(_restore_then_fail())

    assert seen == [NOT_FOUND]
    assert row(url, "session_note", record.id).deleted_at is not None
