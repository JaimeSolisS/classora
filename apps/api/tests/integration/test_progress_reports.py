"""Progress reports (#83 C, D, E, F, AM1-AM3, I2, I3) with real logins
through the real app.

The lifecycle (create → edit → submit → request changes → edit → resubmit
→ approve) with the approval request's status, audit rows and
notifications at each step; delete in draft and after a request for
changes; retract from submitted and from approved; the immutability
trigger; frozen facts and their content (each source, the subject filter,
the center zone's day boundaries, a Sunday session in a weekly report, an
empty period, the caps, decimal averages); every C2, C3 and D1 order step;
period rules and uniqueness; the inbox (route-required, summaries); the
self-review rule; the former-author rule; staff and Finance; the family
reads (approved only, the consent rule); the create hints (AM2); the
recipients and params (AM3); the IDOR cases (the same ``404`` as an
unknown id); the statement budgets. The family leak walk over every
``GET`` route is ``test_session_notes_leak.py``; the D6 races are
``test_progress_reports_concurrency.py``.
"""

import itertools
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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
from app.workflows import progress_reports as workflow
from app.workflows.progress_report_approval import PROGRESS_REPORT_HANDLER
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
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
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
TODAY = date(2031, 3, 5)  # NOW in the center's zone (America/Mexico_City, UTC-6)
STARTED = NOW - timedelta(minutes=30)
MONDAY = date(2031, 3, 3)
LAST_MONDAY = date(2031, 2, 24)

OK = (200, None)
CREATED = (201, None)
NO_CONTENT = (204, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
VERSION_CONFLICT = (409, "version_conflict")
PERMISSION_DENIED = (403, "permission_denied")
TUTOR_ONLY = (403, "progress_report_tutor_only")
SUBJECT_NOT_ASSIGNED = (403, "report_subject_not_assigned")
AUTHOR_ONLY = (403, "progress_report_author_only")
READ_ONLY = (403, "progress_report_read_only")
SELF_REVIEW = (403, "progress_report_self_review")
CONSENT = (403, "student_consent_required")
UNDER_REVIEW = (409, "progress_report_under_review")
NOT_SUBMITTED = (409, "progress_report_not_submitted")
CLOSED = (409, "progress_report_closed")
EXISTS = (409, "progress_report_exists")
AUTHOR_NOT_CURRENT = (409, "progress_report_author_not_current")
ROUTE_REQUIRED = (409, "progress_report_review_route_required")
SUMMARY_REQUIRED = (422, "summary_required")

REQUEST_TYPE = "academics.progress_report"
APPROVED_TYPE = "academics.progress_report_approved"
CHANGES_TYPE = "academics.progress_report_changes_requested"
RETRACTED_TYPE = "academics.progress_report_retracted"

VIEW_KEYS = {
    "id",
    "version",
    "status",
    "student",
    "author",
    "is_own",
    "kind",
    "period_start",
    "period_end",
    "subject",
    "summary",
    "strengths",
    "areas_for_improvement",
    "next_steps",
    "facts",
    "facts_computed_at",
    "facts_preview",
    "submitted_at",
    "changes_requested",
    "approved",
    "retracted",
    "created_at",
    "updated_at",
    "can_edit",
    "can_submit",
    "can_delete",
    "can_approve",
    "can_request_changes",
    "can_retract",
}
CAN_KEYS = sorted(k for k in VIEW_KEYS if k.startswith("can_"))
LIST_ITEM_KEYS = {
    "id",
    "version",
    "status",
    "student",
    "author",
    "kind",
    "period_start",
    "period_end",
    "subject",
    "submitted_at",
    "approved_at",
}
FAMILY_KEYS = {
    "id",
    "student_id",
    "kind",
    "period_start",
    "period_end",
    "subject",
    "author",
    "summary",
    "strengths",
    "areas_for_improvement",
    "next_steps",
    "facts",
    "approved_at",
}
FAMILY_ITEM_KEYS = {
    "id",
    "kind",
    "period_start",
    "period_end",
    "subject",
    "author",
    "approved_at",
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
        display_name="Coordinadora Informes",
    )


@pytest.fixture(scope="module")
def staff(client: TestClient, coordinator: CreatedUser) -> Any:
    """The coordinator's cookie (progress_report.review)."""
    return sign_in(client, coordinator.username)


# --- Helpers --------------------------------------------------------------------------

REPORTS = "/api/v1/progress-reports"


def report_path(report_id: Any, action: str = "") -> str:
    return f"{REPORTS}/{report_id}{action}"


def approved_path(student_id: Any, report_id: Any = None) -> str:
    base = f"/api/v1/students/{student_id}/progress-reports"
    return base if report_id is None else f"{base}/{report_id}"


def ok(response: Response, expected: tuple[int, None] = OK) -> dict[str, Any]:
    assert code_of(response) == expected, response.text
    body: dict[str, Any] = response.json()
    return body


def field_codes(response: Response) -> list[tuple[list[Any], str]]:
    assert code_of(response) == INVALID, response.text
    return [(item["path"], item["code"]) for item in response.json()["errors"]]


def a_tutor(url: str, name: str = "Tutora Informes", *roles: str) -> CreatedUser:
    return create_user(url, roles=(TUTOR, *roles), display_name=name)


def teaching(
    url: str,
    *,
    tutor: CreatedUser | None = None,
    students: tuple[uuid.UUID, ...] | None = None,
    subject: uuid.UUID | None = None,
    state: str = "confirmed",
    starts_at: datetime | None = None,
) -> tuple[CreatedUser, uuid.UUID, uuid.UUID]:
    """A stored session of ``tutor`` (new by default) with ``students`` (one
    new student by default) in ``subject`` (new by default): ``(tutor,
    first student, subject)``. A given tutor's extra session is upcoming (a
    current assignment that no fact counts), two hours after the last one."""
    if starts_at is None:
        starts_at = (
            STARTED if tutor is None else NOW + timedelta(hours=2 * next(_UPCOMING))
        )
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


_UPCOMING = itertools.count(1)


def body_for(student: Any, **values: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "student_id": str(student),
        "kind": "on_demand",
        "period_start": "2031-02-01",
        "period_end": TODAY.isoformat(),
        "summary": "Avanza bien",
    }
    body.update(values)
    return {k: v for k, v in body.items() if v is not ...}


def create(client: TestClient, cookie: Any, student: Any, **values: Any) -> Response:
    return post(client, cookie, REPORTS, body_for(student, **values))


def act(
    client: TestClient,
    cookie: Any,
    report_id: Any,
    action: str,
    version: int,
    **body: Any,
) -> Response:
    return post(
        client,
        cookie,
        report_path(report_id, f"/{action}"),
        {"version": version, **body},
    )


def edit(
    client: TestClient, cookie: Any, report_id: Any, version: int, **values: Any
) -> Response:
    return patch(client, cookie, report_path(report_id), {"version": version, **values})


def delete(client: TestClient, cookie: Any, report_id: Any, version: int) -> Response:
    client.cookies.clear()
    return client.delete(
        report_path(report_id),
        params={"version": version},
        headers=signed_headers(cookie),
    )


def request_row(url: str, report_id: Any) -> Any:
    rows = run_sql(
        url,
        "SELECT id, status, reason FROM approval_requests"
        " WHERE request_type = :t AND subject_id = :s",
        {"t": REQUEST_TYPE, "s": report_id},
    )
    return rows[0] if rows else None


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


def drafted(
    client: TestClient,
    url: str,
    *,
    family: Family | None = None,
    tutor: CreatedUser | None = None,
    **values: Any,
) -> tuple[CreatedUser, Any, uuid.UUID, uuid.UUID, dict[str, Any]]:
    """A draft by a tutor teaching one student (``family``'s child when
    given) in a new subject: ``(tutor, tutor cookie, student, subject,
    view)``."""
    students = None if family is None else (family.student_id,)
    tutor, student, subject = teaching(url, tutor=tutor, students=students)
    cookie = sign_in(client, tutor.username)
    view = ok(create(client, cookie, student, **values), CREATED)
    return tutor, cookie, student, subject, view


def submitted(
    client: TestClient, url: str, **kwargs: Any
) -> tuple[CreatedUser, Any, uuid.UUID, uuid.UUID, dict[str, Any]]:
    tutor, cookie, student, subject, view = drafted(client, url, **kwargs)
    view = ok(act(client, cookie, view["id"], "submit", view["version"]))
    return tutor, cookie, student, subject, view


def approved(
    client: TestClient, url: str, reviewer: Any, **kwargs: Any
) -> tuple[CreatedUser, Any, uuid.UUID, uuid.UUID, dict[str, Any]]:
    tutor, cookie, student, subject, view = submitted(client, url, **kwargs)
    view = ok(act(client, reviewer, view["id"], "approve", view["version"]))
    return tutor, cookie, student, subject, view


# --- The lifecycle (C, D) -------------------------------------------------------------


def test_the_lifecycle(module_seeded_url, client, staff, coordinator):
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
            kind="weekly",
            period_start=MONDAY.isoformat(),
            period_end=...,
            subject_id=str(subject),
            summary="  Primer borrador  ",
            strengths="Lectura",
        ),
        CREATED,
    )
    assert set(created) == VIEW_KEYS
    report_id = created["id"]
    assert (created["status"], created["version"], created["kind"]) == (
        "draft",
        1,
        "weekly",
    )
    assert (created["period_start"], created["period_end"]) == (
        "2031-03-03",
        "2031-03-09",
    )
    assert created["summary"] == "Primer borrador"
    assert created["author"] == {"display_name": "Tutora Informes"}
    assert created["is_own"] is True
    assert created["subject"]["id"] == str(subject)
    assert set(created["student"]) == {"id", "display_name"}
    # A draft never submitted has no frozen facts; the author sees a preview.
    assert created["facts"] is None and created["facts_computed_at"] is None
    assert created["facts_preview"]["schema_version"] == 1
    assert {k: created[k] for k in CAN_KEYS} == {
        "can_approve": False,
        "can_delete": True,
        "can_edit": True,
        "can_request_changes": False,
        "can_retract": False,
        "can_submit": True,
    }
    assert request_row(url, report_id) is None
    # Nobody sees a draft but its author.
    assert_same_not_found(
        get(client, staff, report_path(report_id)),
        get(client, staff, report_path(uuid.uuid4())),
    )
    assert ok(get(client, guardian, approved_path(student)))["items"] == []

    edited = ok(edit(client, cookie, report_id, 1, next_steps="Practicar"))
    assert (edited["version"], edited["next_steps"]) == (2, "Practicar")
    submitted_view = ok(act(client, cookie, report_id, "submit", 2))
    assert (submitted_view["status"], submitted_view["version"]) == ("submitted", 3)
    assert submitted_view["facts"]["schema_version"] == 1
    assert submitted_view["facts_computed_at"] is not None
    assert submitted_view["submitted_at"] is not None
    assert submitted_view["facts_preview"] is None
    assert not any(submitted_view[k] for k in CAN_KEYS)
    request = request_row(url, report_id)
    assert request.status == "pending"
    # Submitting notified nobody; the family still sees nothing.
    for kind in (APPROVED_TYPE, CHANGES_TYPE, RETRACTED_TYPE):
        assert notifications(url, kind, report_id=report_id) == []
    assert ok(get(client, guardian, approved_path(student)))["items"] == []
    review = ok(get(client, staff, report_path(report_id)))
    assert review["is_own"] is False
    assert (review["can_approve"], review["can_request_changes"]) == (True, True)
    assert review["can_retract"] is True
    assert not (review["can_edit"] or review["can_submit"] or review["can_delete"])

    sent_back = ok(act(client, staff, report_id, "request-changes", 3, reason=" Más "))
    assert (sent_back["status"], sent_back["version"]) == ("changes_requested", 4)
    assert sent_back["changes_requested"]["reason"] == "Más"
    assert sent_back["changes_requested"]["by"] == {
        "display_name": "Coordinadora Informes"
    }
    assert request_row(url, report_id).status == "changes_requested"
    assert request_row(url, report_id).reason == "Más"
    [told] = notifications(url, CHANGES_TYPE, report_id=report_id)
    assert told.recipient_user_id == tutor.id
    assert told.params == {"student_id": str(student), "report_id": report_id}
    author_view = ok(get(client, cookie, report_path(report_id)))
    assert author_view["changes_requested"]["reason"] == "Más"
    assert author_view["facts"] is not None  # the last frozen facts stay
    assert author_view["facts_preview"] is not None
    assert author_view["can_edit"] and author_view["can_submit"]

    ok(edit(client, cookie, report_id, 4, summary="Segundo borrador"))
    resubmitted = ok(act(client, cookie, report_id, "submit", 5))
    assert (resubmitted["status"], resubmitted["version"]) == ("submitted", 6)
    # The last reason stays until approval (the author reads it).
    assert resubmitted["changes_requested"]["reason"] == "Más"
    assert request_row(url, report_id).status == "pending"
    assert request_row(url, report_id).reason is None

    final = ok(act(client, staff, report_id, "approve", 6))
    assert (final["status"], final["version"]) == ("approved_for_family", 7)
    assert final["approved"]["by"] == {"display_name": "Coordinadora Informes"}
    assert final["changes_requested"] is None
    assert final["can_retract"] is True
    assert not (final["can_approve"] or final["can_request_changes"])
    assert request_row(url, report_id).status == "approved"
    listed = ok(get(client, guardian, approved_path(student)))
    assert [item["id"] for item in listed["items"]] == [report_id]
    assert set(listed["items"][0]) == FAMILY_ITEM_KEYS
    detail = ok(get(client, guardian, approved_path(student, report_id)))
    assert set(detail) == FAMILY_KEYS
    assert detail["summary"] == "Segundo borrador"
    assert detail["facts"] == final["facts"]
    assert "Más" not in str(detail)

    assert [
        e.action
        for e in events(
            url, record_type="progress_reports", record_id=uuid.UUID(report_id)
        )
    ] == [
        "create",
        "update",
        "update",
        "academics.progress_report_submitted",
        "update",
        "academics.progress_report_changes_requested",
        "update",
        "update",
        "academics.progress_report_submitted",
        "update",
        "academics.progress_report_approved",
    ]
    approved_event = events(
        url,
        record_type="progress_reports",
        record_id=uuid.UUID(report_id),
        action="academics.progress_report_approved",
    )[0]
    assert approved_event.details == {
        "report_id": report_id,
        "approver_user_id": str(coordinator.id),
    }
    # The edit after approval and every author write are refused.
    assert code_of(edit(client, cookie, report_id, 7, summary="x")) == CLOSED
    assert code_of(act(client, cookie, report_id, "submit", 7)) == CLOSED
    assert code_of(delete(client, cookie, report_id, 7)) == CLOSED


def test_delete_in_draft_and_after_a_request_for_changes(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    _, cookie, student, _, draft = drafted(client, url)
    assert code_of(delete(client, cookie, draft["id"], 1)) == NO_CONTENT
    for response in (
        get(client, cookie, report_path(draft["id"])),
        edit(client, cookie, draft["id"], 2, summary="x"),
        act(client, cookie, draft["id"], "submit", 2),
        delete(client, cookie, draft["id"], 2),
    ):
        assert_same_not_found(response, get(client, cookie, report_path(uuid.uuid4())))
    assert [
        e.action
        for e in events(
            url, record_type="progress_reports", record_id=uuid.UUID(draft["id"])
        )
    ] == ["create", "soft_delete"]

    _, cookie, _, _, sent = submitted(client, url)
    sent = ok(act(client, staff, sent["id"], "request-changes", 2, reason="Rehacer"))
    assert code_of(delete(client, cookie, sent["id"], 3)) == NO_CONTENT
    assert request_row(url, sent["id"]).status == "cancelled"
    assert_same_not_found(
        get(client, staff, report_path(sent["id"])),
        get(client, staff, report_path(uuid.uuid4())),
    )
    # Submitted: under review; approved: closed.
    _, cookie, _, _, waiting = submitted(client, url)
    assert code_of(delete(client, cookie, waiting["id"], 2)) == UNDER_REVIEW
    _, cookie, _, _, done = approved(client, url, staff)
    assert code_of(delete(client, cookie, done["id"], 3)) == CLOSED


def test_retract_from_submitted_and_from_approved(module_seeded_url, client, staff):
    url = module_seeded_url
    family = guardian_with_child(url)
    guardian = sign_in(client, family.user.username)
    tutor, cookie, student, _, waiting = submitted(client, url, family=family)
    gone = ok(
        act(client, staff, waiting["id"], "retract", 2, reason="Alumno equivocado")
    )
    assert (gone["status"], gone["version"]) == ("retracted", 3)
    assert gone["retracted"]["reason"] == "Alumno equivocado"
    assert not any(gone[k] for k in CAN_KEYS)
    row = request_row(url, waiting["id"])
    assert (row.status, row.reason) == ("rejected", "Alumno equivocado")
    [told] = notifications(url, RETRACTED_TYPE, report_id=waiting["id"])
    assert told.recipient_user_id == tutor.id
    assert told.params == {"student_id": str(student), "report_id": waiting["id"]}
    assert (
        code_of(act(client, staff, waiting["id"], "retract", 3, reason="x")) == CLOSED
    )
    assert code_of(act(client, staff, waiting["id"], "approve", 3)) == NOT_SUBMITTED
    # The author still reads it, read-only.
    assert ok(get(client, cookie, report_path(waiting["id"])))["status"] == "retracted"

    shown = ok(
        act(
            client,
            cookie,
            ok(create(client, cookie, student), CREATED)["id"],
            "submit",
            1,
        )
    )
    shown = ok(act(client, staff, shown["id"], "approve", 2))
    assert (
        ok(get(client, guardian, approved_path(student, shown["id"])))["id"]
        == (shown["id"])
    )
    gone = ok(act(client, staff, shown["id"], "retract", 3, reason="Contenido erróneo"))
    assert gone["status"] == "retracted"
    assert gone["approved"] is not None  # the approval stays on record
    assert request_row(url, shown["id"]).status == "approved"  # nothing to decide
    assert_same_not_found(
        get(client, guardian, approved_path(student, shown["id"])),
        get(client, guardian, approved_path(student, uuid.uuid4())),
    )
    assert ok(get(client, guardian, approved_path(student)))["items"] == []
    # Families are never told about a retraction.
    assert {
        row.recipient_user_id
        for row in notifications(url, RETRACTED_TYPE, report_id=shown["id"])
    } == {tutor.id}
    [event] = events(
        url,
        record_type="progress_reports",
        record_id=uuid.UUID(shown["id"]),
        action="academics.progress_report_retracted",
    )
    assert event.details == {
        "report_id": shown["id"],
        "reason": "Contenido erróneo",
        "previous_status": "approved_for_family",
    }
    # Retracting a report sent back is refused; a draft is invisible.
    _, cookie, _, _, sent = submitted(client, url)
    ok(act(client, staff, sent["id"], "request-changes", 2, reason="Rehacer"))
    assert code_of(act(client, staff, sent["id"], "retract", 3, reason="x")) == (
        NOT_SUBMITTED
    )


def test_the_trigger_freezes_an_approved_report(module_seeded_url, client, staff):
    url = module_seeded_url
    *_, done = approved(client, url, staff)
    for change in ("summary = 'Cambiado'", "facts = '{}'::jsonb"):
        with pytest.raises(DBAPIError, match="never changes"):
            run_sql(
                url,
                f"UPDATE progress_reports SET {change} WHERE id = :id",  # noqa: S608
                {"id": done["id"]},
            )
    with pytest.raises(DBAPIError, match="never deleted"):
        run_sql(url, "DELETE FROM progress_reports WHERE id = :id", {"id": done["id"]})
    ok(act(client, staff, done["id"], "retract", 3, reason="Error"))


# --- C2: the create order -------------------------------------------------------------


def test_the_create_order(module_seeded_url, client, staff, coordinator):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    cookie = sign_in(client, tutor.username)
    anonymous = client.post(REPORTS, json=body_for(student))
    assert anonymous.status_code == 401

    # 422 schema, before any record is read (an unknown student too).
    unknown = uuid.uuid4()
    assert field_codes(create(client, cookie, unknown, summary="x" * 4001)) == [
        (["body", "summary"], "too_long")
    ]
    assert field_codes(create(client, cookie, unknown, strengths="x" * 2001)) == [
        (["body", "strengths"], "too_long")
    ]
    assert code_of(create(client, cookie, unknown, kind="yearly")) == INVALID
    assert code_of(create(client, cookie, unknown, status="approved")) == INVALID
    # 404: an unknown student, a tutor who doesn't teach them, Finance.
    assert_same_not_found(
        create(client, cookie, unknown), create(client, cookie, new_student(url))
    )
    other = sign_in(client, a_tutor(url, "Otra Tutora").username)
    assert_same_not_found(
        create(client, other, student), create(client, other, unknown)
    )
    finance = sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username)
    assert_same_not_found(
        create(client, finance, student), create(client, finance, unknown)
    )
    # 403: staff don't author.
    assert code_of(create(client, staff, student)) == TUTOR_ONLY
    # Subject: unknown or inactive, then not taught.
    for bad in (
        uuid.uuid4(),
        new_subject(url, is_active=False),
        new_subject(url, level_active=False),
    ):
        assert field_codes(create(client, cookie, student, subject_id=str(bad))) == [
            (["body", "subject_id"], "subject_unknown")
        ]
    assert (
        code_of(create(client, cookie, student, subject_id=str(new_subject(url))))
        == SUBJECT_NOT_ASSIGNED
    )
    # Periods.
    for values, path in (
        (
            {"kind": "weekly", "period_start": "2031-03-04", "period_end": ...},
            "period_start",
        ),
        (
            {
                "kind": "weekly",
                "period_start": "2031-03-03",
                "period_end": "2031-03-08",
            },
            "period_end",
        ),
        (
            {"kind": "monthly", "period_start": "2031-02-02", "period_end": ...},
            "period_start",
        ),
        (
            {
                "kind": "monthly",
                "period_start": "2031-02-01",
                "period_end": "2031-03-01",
            },
            "period_end",
        ),
        ({"period_start": "2030-03-01", "period_end": "2031-03-02"}, "period_end"),
        ({"period_start": "2031-03-05", "period_end": "2031-03-04"}, "period_end"),
        ({"period_end": ...}, "period_end"),
    ):
        assert field_codes(create(client, cookie, student, **values)) == [
            (["body", path], "period_invalid")
        ], values
    assert field_codes(
        create(
            client, cookie, student, period_start="2031-03-06", period_end="2031-03-10"
        )
    ) == [(["body", "period_start"], "period_in_future")]
    # 366 days, ending in the future, and today's start are allowed.
    ok(create(client, cookie, student, period_start="2030-03-06"), CREATED)
    ok(
        create(
            client,
            cookie,
            student,
            period_start=TODAY.isoformat(),
            period_end="2031-03-20",
        ),
        CREATED,
    )
    monthly = ok(
        create(
            client,
            cookie,
            student,
            kind="monthly",
            period_start="2031-02-01",
            period_end=...,
        ),
        CREATED,
    )
    assert monthly["period_end"] == "2031-02-28"
    # Uniqueness: one live weekly or monthly report per period and subject.
    weekly = {"kind": "weekly", "period_start": MONDAY.isoformat(), "period_end": ...}
    first = ok(create(client, cookie, student, **weekly), CREATED)
    duplicate = create(client, cookie, student, **weekly)
    assert code_of(duplicate) == EXISTS
    assert duplicate.json()["details"] == {"report_id": first["id"]}
    ok(create(client, cookie, student, subject_id=str(subject), **weekly), CREATED)
    assert (
        code_of(create(client, cookie, student, subject_id=str(subject), **weekly))
        == EXISTS
    )
    # On demand: no uniqueness. After a delete or a retract: allowed again.
    ok(create(client, cookie, student), CREATED)
    ok(create(client, cookie, student), CREATED)
    assert code_of(delete(client, cookie, first["id"], 1)) == NO_CONTENT
    again = ok(create(client, cookie, student, **weekly), CREATED)
    again = ok(act(client, cookie, again["id"], "submit", 1))
    ok(act(client, staff, again["id"], "retract", 2, reason="Repetido"))
    ok(create(client, cookie, student, **weekly), CREATED)
    # A holder who is also the current tutor authors.
    both = a_tutor(url, "Coordinadora Tutora", ACADEMIC_COORDINATOR)
    teaching(url, tutor=both, students=(student,), subject=subject)
    ok(create(client, sign_in(client, both.username), student), CREATED)
    # Nobody was notified by any create.
    for kind in (APPROVED_TYPE, CHANGES_TYPE):
        assert notifications(url, kind, student_id=student) == []
    del coordinator


# --- C3: the edit order ---------------------------------------------------------------


def test_the_edit_order(module_seeded_url, client, staff, owner):
    url = module_seeded_url
    tutor, cookie, student, subject, draft = drafted(client, url)
    report = draft["id"]
    anonymous = client.patch(report_path(report), json={"version": 1})
    assert anonymous.status_code == 401
    assert code_of(patch(client, cookie, report_path(report), {})) == INVALID
    for immutable in (
        "student_id",
        "kind",
        "period_start",
        "period_end",
        "subject_id",
        "status",
        "facts",
    ):
        assert code_of(edit(client, cookie, report, 1, **{immutable: None})) == INVALID
    assert field_codes(edit(client, cookie, report, 1, summary="x" * 4001)) == [
        (["body", "summary"], "too_long")
    ]
    # 404: another tutor of the student, a holder (drafts are the author's).
    colleague = a_tutor(url, "Colega")
    teaching(url, tutor=colleague, students=(student,), subject=subject)
    other = sign_in(client, colleague.username)
    for caller in (other, staff, sign_in(client, owner.username)):
        assert_same_not_found(
            edit(client, caller, report, 1, summary="x"),
            edit(client, caller, uuid.uuid4(), 1, summary="x"),
        )
    # Version, then content.
    assert code_of(edit(client, cookie, report, 9, summary="x")) == VERSION_CONFLICT
    unchanged = ok(edit(client, cookie, report, 1, summary="Avanza bien"))
    assert unchanged["version"] == 1  # an unchanged body writes nothing
    cleared = ok(edit(client, cookie, report, 1, summary="   ", strengths="Bien"))
    assert (cleared["summary"], cleared["strengths"], cleared["version"]) == (
        None,
        "Bien",
        2,
    )
    # Submit needs a summary (a top-level code: the body is only the version).
    assert code_of(act(client, cookie, report, "submit", 2)) == SUMMARY_REQUIRED
    ok(edit(client, cookie, report, 2, summary="Listo"))
    ok(act(client, cookie, report, "submit", 3))
    # Submitted: a holder reads it but isn't the author; the author waits.
    assert code_of(edit(client, staff, report, 4, summary="x")) == AUTHOR_ONLY
    assert code_of(act(client, staff, report, "submit", 4)) == AUTHOR_ONLY
    assert code_of(delete(client, staff, report, 4)) == AUTHOR_ONLY
    assert code_of(edit(client, cookie, report, 4, summary="x")) == UNDER_REVIEW
    assert code_of(act(client, cookie, report, "submit", 4)) == UNDER_REVIEW
    # The 403 comes before the version check.
    assert code_of(edit(client, staff, report, 99, summary="x")) == AUTHOR_ONLY
    del tutor


# --- D1: the decision order -----------------------------------------------------------


def test_the_decision_order(module_seeded_url, client, staff, owner):
    url = module_seeded_url
    tutor, cookie, student, subject, view = submitted(client, url)
    report = view["id"]
    reasons = {
        "approve": {},
        "request-changes": {"reason": "x"},
        "retract": {"reason": "x"},
    }
    for action, extra in reasons.items():
        response = client.post(
            report_path(report, f"/{action}"), json={"version": 2, **extra}
        )
        assert response.status_code == 401
        assert (
            code_of(post(client, staff, report_path(report, f"/{action}"), extra))
            == INVALID
        )
    # 404 before the permission: a tutor who can't read the report.
    colleague = a_tutor(url, "Colega Decide")
    teaching(url, tutor=colleague, students=(student,), subject=subject)
    other = sign_in(client, colleague.username)
    finance = sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username)
    for caller in (other, finance):
        for action, extra in reasons.items():
            assert_same_not_found(
                act(client, caller, report, action, 2, **extra),
                act(client, caller, uuid.uuid4(), action, 2, **extra),
            )
    # 403 permission_denied for the author (they read it).
    for action, extra in reasons.items():
        assert code_of(act(client, cookie, report, action, 2, **extra)) == (
            PERMISSION_DENIED
        )
    # 409 version, then state, then the 422 content.
    for action, extra in reasons.items():
        assert code_of(act(client, staff, report, action, 1, **extra)) == (
            VERSION_CONFLICT
        )
    assert field_codes(
        act(client, staff, report, "request-changes", 2, reason="  ")
    ) == [(["body", "reason"], "reason_required")]
    assert field_codes(act(client, staff, report, "retract", 2, reason="x" * 1001)) == [
        (["body", "reason"], "too_long")
    ]
    ok(act(client, staff, report, "request-changes", 2, reason="Rehacer"))
    assert code_of(act(client, staff, report, "approve", 3)) == NOT_SUBMITTED
    assert (
        code_of(act(client, staff, report, "request-changes", 3, reason="x"))
        == NOT_SUBMITTED
    )
    # A 409 state comes before a bad reason.
    assert (
        code_of(act(client, staff, report, "request-changes", 3, reason=""))
        == NOT_SUBMITTED
    )
    # A draft is the author's only: 404 for every reviewer.
    _, _, _, _, draft = drafted(client, url)
    for caller in (staff, sign_in(client, owner.username)):
        assert_same_not_found(
            act(client, caller, draft["id"], "approve", 1),
            act(client, caller, uuid.uuid4(), "approve", 1),
        )
    del tutor


def test_the_self_review_rule(module_seeded_url, client, staff):
    """E4: an Administrator who is also the author can't decide their own
    report; the Owner can."""
    url = module_seeded_url
    admin_tutor = a_tutor(url, "Admin Tutora", ADMINISTRATOR)
    _, cookie, _, _, view = submitted(client, url, tutor=admin_tutor)
    for action, extra in (
        ("approve", {}),
        ("request-changes", {"reason": "x"}),
        ("retract", {"reason": "x"}),
    ):
        assert code_of(act(client, cookie, view["id"], action, 2, **extra)) == (
            SELF_REVIEW
        )
    own = ok(get(client, cookie, report_path(view["id"])))
    assert not (own["can_approve"] or own["can_request_changes"] or own["can_retract"])
    ok(act(client, staff, view["id"], "approve", 2))

    owner_tutor = a_tutor(url, "Dueña Tutora", OWNER)
    _, cookie, _, _, view = submitted(client, url, tutor=owner_tutor)
    assert ok(get(client, cookie, report_path(view["id"])))["can_approve"] is True
    done = ok(act(client, cookie, view["id"], "approve", 2))
    assert done["status"] == "approved_for_family"
    # The Owner approving their own report isn't notified about it.
    assert (
        notifications(url, APPROVED_TYPE, report_id=view["id"], audience="author") == []
    )


# --- The approval inbox (B4, AM1) -----------------------------------------------------


def test_the_inbox_items_point_to_the_review_page(module_seeded_url, client, staff):
    url = module_seeded_url
    _, _, student, subject, view = submitted(
        client, url, subject_id=..., summary="Nunca en la bandeja"
    )
    del subject
    request = request_row(url, view["id"])
    for action, body in (
        ("approve", {}),
        ("reject", {"reason": "x"}),
        ("request-changes", {"reason": "x"}),
    ):
        response = post(
            client, staff, f"/api/v1/approval-requests/{request.id}/{action}", body
        )
        assert code_of(response) == ROUTE_REQUIRED, response.text
        assert response.json()["details"] == {
            "review_path": f"/progress-reports/{view['id']}"
        }
    assert request_row(url, view["id"]).status == "pending"
    page = ok(
        get(
            client,
            staff,
            "/api/v1/approval-requests",
            request_type=REQUEST_TYPE,
            limit=100,
        )
    )
    [item] = [i for i in page["items"] if i["subject"].get("report_id") == view["id"]]
    assert item["allowed_actions"] == []
    assert set(item["subject"]) == {
        "report_id",
        "student",
        "author",
        "kind",
        "period_start",
        "period_end",
        "subject",
        "submitted_at",
    }
    assert item["subject"]["student"]["id"] == str(student)
    assert item["subject"]["author"] == {"display_name": "Tutora Informes"}
    assert item["subject"]["subject"] is None
    assert "Nunca" not in str(item)
    # After the dedicated route decided it, the inbox still points there.
    ok(act(client, staff, view["id"], "approve", 2))
    response = post(
        client, staff, f"/api/v1/approval-requests/{request.id}/approve", {}
    )
    assert code_of(response) == ROUTE_REQUIRED


def test_the_summaries_take_three_statements(module_seeded_url, client):
    url = module_seeded_url
    ids = []
    for _ in range(3):
        *_, view = submitted(client, url)
        ids.append(uuid.UUID(view["id"]))
    statements, found = count_statements(
        url, lambda s: PROGRESS_REPORT_HANDLER.summaries(s, ids)
    )
    assert statements <= 3
    assert {uuid.UUID(v["report_id"]) for v in found.values()} == set(ids)


# --- B2: facts ------------------------------------------------------------------------


def _template(url: str, subject: uuid.UUID) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO evaluation_templates (name, kind, subject_id)"
        " VALUES (:n, 'ongoing', :s) RETURNING id",
        {"n": f"Plantilla {uuid.uuid4().hex[:8]}", "s": subject},
    )
    return uuid.UUID(str(row.id))


def _evaluation(
    url: str,
    student: uuid.UUID,
    subject: uuid.UUID,
    author: uuid.UUID,
    template: uuid.UUID,
    evaluated_on: date,
    percent: str,
    *,
    status: str = "final",
    voided: bool = False,
    feedback: str | None = None,
) -> uuid.UUID:
    final = status == "final"
    [row] = run_sql(
        url,
        "INSERT INTO evaluations (student_id, subject_id, author_user_id,"
        " template_id, template_version, template_name, kind, evaluated_on,"
        " status, feedback, score_total, max_total, score_percent, finalized_at,"
        " voided_at, voided_by_user_id, void_reason)"
        " VALUES (:st, :sub, :a, :t, 1, 'Lectura', 'ongoing', :on, :status, :f,"
        " :score, :max, :pct, :fin, :void, :void_by, :void_reason) RETURNING id",
        {
            "st": student,
            "sub": subject,
            "a": author,
            "t": template,
            "on": evaluated_on,
            "status": status,
            "f": feedback,
            "score": Decimal(percent) / 10 if final else None,
            "max": Decimal(10) if final else None,
            "pct": Decimal(percent) if final else None,
            "fin": NOW if final else None,
            "void": NOW if voided else None,
            "void_by": author if voided else None,
            "void_reason": "Error" if voided else None,
        },
    )
    return uuid.UUID(str(row.id))


def _homework(
    url: str,
    student: uuid.UUID,
    subject: uuid.UUID,
    creator: uuid.UUID,
    due_on: date,
    *,
    submitted_at: datetime | None = None,
    percent: str | None = None,
    deleted: bool = False,
) -> None:
    [made] = run_sql(
        url,
        "INSERT INTO assignments (creator_user_id, subject_id, title, instructions,"
        " due_on, max_score, deleted_at, deleted_by_user_id)"
        " VALUES (:u, :sub, 'Tarea', 'Leer', :due, 10, :del, :del_by) RETURNING id",
        {
            "u": creator,
            "sub": subject,
            "due": due_on,
            "del": NOW if deleted else None,
            "del_by": creator if deleted else None,
        },
    )
    graded = percent is not None
    run_sql(
        url,
        "INSERT INTO assignment_submissions (assignment_id, student_id, text,"
        " submitted_at, submitted_by_user_id, submitted_via, score, score_percent,"
        " feedback, graded_at, graded_on, graded_by_user_id)"
        " VALUES (:a, :st, :text, :sub_at, :sub_by, :via, :score, :pct, :fb, :g_at,"
        " :g_on, :g_by)",
        {
            "a": made.id,
            "st": student,
            "text": "Mi tarea" if submitted_at else None,
            "sub_at": submitted_at,
            "sub_by": creator if submitted_at else None,
            "via": "tutor" if submitted_at else None,
            "score": Decimal(percent) / 10 if graded else None,
            "pct": Decimal(percent) if graded else None,
            "fb": "Retroalimentación secreta" if graded else None,
            "g_at": NOW if graded else None,
            "g_on": TODAY if graded else None,
            "g_by": creator if graded else None,
        },
    )


def _plan(
    url: str,
    student: uuid.UUID,
    subject: uuid.UUID,
    creator: uuid.UUID,
    *,
    title: str,
    status: str,
    starts_on: date,
    activated_at: datetime | None,
    closed_at: datetime | None = None,
    milestones: tuple[str, ...] = ("pending",),
    deleted: bool = False,
) -> None:
    [plan] = run_sql(
        url,
        "INSERT INTO learning_plans (student_id, subject_id, created_by_user_id,"
        " title, description, starts_on, status, activated_at, closed_at,"
        " deleted_at, deleted_by_user_id)"
        " VALUES (:st, :sub, :u, :title, 'Descripción secreta', :starts, :status,"
        " :act, :closed, :del, :del_by) RETURNING id",
        {
            "st": student,
            "sub": subject,
            "u": creator,
            "title": title,
            "starts": starts_on,
            "status": status,
            "act": activated_at,
            "closed": closed_at,
            "del": NOW if deleted else None,
            "del_by": creator if deleted else None,
        },
    )
    [objective] = run_sql(
        url,
        "INSERT INTO learning_plan_objectives (plan_id, position, title)"
        " VALUES (:p, 1, 'Objetivo secreto') RETURNING id",
        {"p": plan.id},
    )
    for position, state in enumerate(milestones, start=1):
        run_sql(
            url,
            "INSERT INTO learning_plan_milestones (plan_id, objective_id, position,"
            " title, status, completed_at, completed_by_user_id)"
            " VALUES (:p, :o, :pos, 'Hito secreto', :s, :at, :by)",
            {
                "p": plan.id,
                "o": objective.id,
                "pos": position,
                "s": state,
                "at": NOW if state == "done" else None,
                "by": creator if state == "done" else None,
            },
        )


def _session(
    url: str,
    tutor: CreatedUser,
    student: uuid.UUID,
    subject: uuid.UUID,
    starts_at: datetime,
    *,
    state: str = "confirmed",
    mark: str | None = None,
) -> None:
    session_id = stored(
        url,
        starts_at,
        state=state,
        students=(student,),
        tutor_user_id=tutor.id,
        subject_id=subject,
    )
    if mark is not None:
        run_sql(
            url,
            "INSERT INTO class_session_attendance (class_session_id, student_id,"
            " status, marked_by_user_id, marked_at) VALUES (:c, :s, :m, :u, :at)",
            {"c": session_id, "s": student, "m": mark, "u": tutor.id, "at": NOW},
        )


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def test_the_facts_content(module_seeded_url, client):
    """E2, B2 for the week 2031-02-24 (Monday) to 2031-03-02 (Sunday), center
    zone UTC-6: every source, the subject filter, local day boundaries, a
    Sunday-night session, the decimals."""
    url = module_seeded_url
    tutor, student, maths = teaching(url)
    english = new_subject(url)
    teaching(url, tutor=tutor, students=(student,), subject=english)
    cookie = sign_in(client, tutor.username)
    sunday = LAST_MONDAY + timedelta(days=6)
    # Attendance: in the week (local dates), counted by mark.
    _session(url, tutor, student, maths, _utc(LAST_MONDAY, 7), mark="present")
    _session(
        url, tutor, student, maths, _utc(MONDAY, 5, 30), state="completed", mark="late"
    )  # Sunday 23:30 local
    _session(
        url,
        tutor,
        student,
        english,
        _utc(LAST_MONDAY, 15, 0) + timedelta(days=1),
        mark="absent",
    )
    _session(url, tutor, student, english, _utc(LAST_MONDAY, 15) + timedelta(days=2))
    # Outside the week or never counted.
    _session(url, tutor, student, maths, _utc(MONDAY, 6, 30), mark="present")
    _session(url, tutor, student, maths, _utc(LAST_MONDAY, 5, 30), mark="present")
    _session(
        url,
        tutor,
        student,
        maths,
        _utc(LAST_MONDAY, 16) + timedelta(days=3),
        state="cancelled",
    )
    # Evaluations.
    template = _template(url, maths)
    for subject, on, percent, extra in (
        (maths, LAST_MONDAY, "80.00", {}),
        (maths, sunday, "70.50", {}),
        (english, LAST_MONDAY + timedelta(days=1), "90.00", {}),
        (maths, LAST_MONDAY - timedelta(days=1), "60.00", {}),
        (maths, MONDAY, "50.00", {}),
        (maths, LAST_MONDAY + timedelta(days=2), "0", {"status": "draft"}),
        (maths, LAST_MONDAY + timedelta(days=3), "10.00", {"voided": True}),
    ):
        _evaluation(
            url,
            student,
            subject,
            tutor.id,
            template,
            on,
            percent,
            feedback="Comentario secreto",
            **extra,
        )
    # Homework (states now: NOW is after every deadline of the week).
    _homework(
        url,
        student,
        maths,
        tutor.id,
        LAST_MONDAY + timedelta(days=2),
        submitted_at=_utc(LAST_MONDAY + timedelta(days=3), 12),
        percent="80.00",
    )
    _homework(url, student, maths, tutor.id, LAST_MONDAY + timedelta(days=3))
    _homework(
        url,
        student,
        english,
        tutor.id,
        sunday - timedelta(days=1),
        submitted_at=_utc(LAST_MONDAY, 12),
    )
    _homework(url, student, maths, tutor.id, MONDAY + timedelta(days=1))
    _homework(
        url, student, maths, tutor.id, LAST_MONDAY + timedelta(days=4), deleted=True
    )
    # Learning plans.
    _plan(
        url,
        student,
        maths,
        tutor.id,
        title="Plan activo",
        status="active",
        starts_on=date(2031, 2, 1),
        activated_at=_utc(date(2031, 2, 1), 18),
        milestones=("done", "pending", "done"),
    )
    _plan(
        url,
        student,
        maths,
        tutor.id,
        title="Plan borrador",
        status="draft",
        starts_on=date(2031, 2, 1),
        activated_at=None,
    )
    _plan(
        url,
        student,
        maths,
        tutor.id,
        title="Plan cerrado antes",
        status="completed",
        starts_on=date(2031, 1, 1),
        activated_at=_utc(date(2031, 1, 2), 18),
        closed_at=_utc(date(2031, 2, 20), 18),
        milestones=("done",),
    )
    _plan(
        url,
        student,
        english,
        tutor.id,
        title="Plan interrumpido",
        status="discontinued",
        starts_on=date(2031, 2, 1),
        activated_at=_utc(date(2031, 2, 2), 18),
        closed_at=_utc(LAST_MONDAY + timedelta(days=1), 18),
    )
    _plan(
        url,
        student,
        english,
        tutor.id,
        title="Plan futuro",
        status="active",
        starts_on=MONDAY + timedelta(days=1),
        activated_at=NOW,
    )

    week = {
        "kind": "weekly",
        "period_start": LAST_MONDAY.isoformat(),
        "period_end": ...,
    }
    whole = ok(create(client, cookie, student, **week), CREATED)["facts_preview"]
    assert whole["period"] == {"start": "2031-02-24", "end": "2031-03-02"}
    assert whole["subject"] is None
    assert whole["attendance"] == {
        "sessions": 4,
        "present": 1,
        "late": 1,
        "absent": 1,
        "not_recorded": 1,
    }
    evaluations = whole["evaluations"]
    assert (evaluations["count"], evaluations["average_percent"]) == (3, "80.17")
    assert evaluations["truncated"] is False
    by_subject = {g["subject"]["id"]: g for g in evaluations["by_subject"]}
    assert (
        by_subject[str(maths)]["count"],
        by_subject[str(maths)]["average_percent"],
    ) == (2, "75.25")
    assert by_subject[str(english)]["average_percent"] == "90.00"
    assert [e["evaluated_on"] for e in evaluations["entries"]] == [
        "2031-03-02",
        "2031-02-25",
        "2031-02-24",
    ]
    assert evaluations["entries"][0] == {
        "evaluated_on": "2031-03-02",
        "subject": by_subject[str(maths)]["subject"],
        "evaluation_kind": "ongoing",
        "template_name": "Lectura",
        "score_percent": "70.50",
    }
    assert whole["assignments"] == {
        "count": 3,
        "pending": 0,
        "missing": 1,
        "submitted": 1,
        "graded": 1,
        "late": 1,
        "average_percent": "80.00",
    }
    plans = {plan["title"]: plan for plan in whole["learning_plans"]}
    assert set(plans) == {"Plan activo", "Plan interrumpido"}
    assert plans["Plan activo"] == {
        "title": "Plan activo",
        "subject": {
            "id": str(maths),
            "name": by_subject[str(maths)]["subject"]["name"],
        },
        "status": "active",
        "progress_percent": "67",
        "objectives_completed": 0,
        "objectives_total": 1,
        "milestones_done": 2,
        "milestones_total": 3,
    }
    assert whole["learning_plans"][0]["title"] == "Plan activo"  # active first
    assert whole["learning_plans_truncated"] is False
    text = str(whole)
    for secret in ("secreto", "secreta", "Mi tarea", "Tutora"):
        assert secret not in text

    # The subject filter.
    only = ok(create(client, cookie, student, subject_id=str(maths), **week), CREATED)[
        "facts_preview"
    ]
    assert only["subject"]["id"] == str(maths)
    assert only["attendance"] == {
        "sessions": 2,
        "present": 1,
        "late": 1,
        "absent": 0,
        "not_recorded": 0,
    }
    assert (only["evaluations"]["count"], only["evaluations"]["average_percent"]) == (
        2,
        "75.25",
    )
    assert only["assignments"]["count"] == 2
    assert [p["title"] for p in only["learning_plans"]] == ["Plan activo"]

    # A period with no data: zeros and null averages; it can be submitted.
    empty = ok(
        create(
            client, cookie, student, period_start="2030-06-01", period_end="2030-06-30"
        ),
        CREATED,
    )
    facts = empty["facts_preview"]
    assert facts["attendance"]["sessions"] == 0
    assert (facts["evaluations"]["count"], facts["evaluations"]["average_percent"]) == (
        0,
        None,
    )
    assert facts["evaluations"]["by_subject"] == facts["evaluations"]["entries"] == []
    assert facts["assignments"]["average_percent"] is None
    assert ok(act(client, cookie, empty["id"], "submit", 1))["facts"] == facts


def test_the_facts_caps(module_seeded_url, client):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    cookie = sign_in(client, tutor.username)
    template = _template(url, subject)
    for day in range(51):
        _evaluation(
            url,
            student,
            subject,
            tutor.id,
            template,
            date(2031, 1, 1) + timedelta(days=day),
            "50.00",
        )
    for number in range(21):
        _plan(
            url,
            student,
            subject,
            tutor.id,
            title=f"Plan {number:02}",
            status="completed",
            starts_on=date(2031, 1, 1),
            activated_at=_utc(date(2031, 1, 2), 18),
            closed_at=_utc(date(2031, 2, 1), 18),
        )
    facts = ok(create(client, cookie, student, period_start="2031-01-01"), CREATED)[
        "facts_preview"
    ]
    evaluations = facts["evaluations"]
    assert evaluations["count"] == 51
    assert len(evaluations["entries"]) == 50
    assert evaluations["truncated"] is True
    assert evaluations["entries"][0]["evaluated_on"] == "2031-02-20"
    assert len(facts["learning_plans"]) == 20
    assert facts["learning_plans_truncated"] is True


def test_the_facts_are_frozen_at_each_submit(module_seeded_url, client, staff):
    """A finalized or voided evaluation after the submit doesn't change the
    submitted or approved facts; a resubmit recomputes them."""
    url = module_seeded_url
    family = guardian_with_child(url)
    guardian = sign_in(client, family.user.username)
    tutor, cookie, student, subject, view = submitted(client, url, family=family)
    assert view["facts"]["evaluations"]["count"] == 0
    template = _template(url, subject)
    first = _evaluation(url, student, subject, tutor.id, template, TODAY, "40.00")
    still = ok(get(client, staff, report_path(view["id"])))
    assert still["facts"] == view["facts"]
    sent = ok(act(client, staff, view["id"], "request-changes", 2, reason="Rehacer"))
    assert sent["facts"] == view["facts"]
    again = ok(act(client, cookie, view["id"], "submit", 3))
    assert again["facts"]["evaluations"]["count"] == 1
    assert again["facts_computed_at"] >= view["facts_computed_at"]
    done = ok(act(client, staff, view["id"], "approve", 4))
    run_sql(
        url,
        "UPDATE evaluations SET voided_at = now(), voided_by_user_id = :u,"
        " void_reason = 'Error' WHERE id = :id",
        {"u": tutor.id, "id": first},
    )
    _evaluation(url, student, subject, tutor.id, template, TODAY, "90.00")
    shown = ok(get(client, guardian, approved_path(student, view["id"])))
    assert shown["facts"] == done["facts"]
    assert shown["facts"]["evaluations"]["average_percent"] == "40.00"


# --- E5: the former author, staff and Finance -----------------------------------------


def test_a_former_author_reads_their_reports_read_only(
    module_seeded_url, client, staff, at_now
):
    """W5.4: the last class ended 31+ days ago and nothing is upcoming: the
    author reads their reports read-only; the reviewer still approves or
    retracts, never sends one back; the approved list shows only theirs."""
    url = module_seeded_url
    long_ago = NOW - timedelta(days=40)
    tutor, student, subject = teaching(url, state="completed", starts_at=long_ago)
    at_now.set(long_ago + timedelta(hours=2))
    cookie = sign_in(client, tutor.username)
    period = {"period_start": "2031-01-01", "period_end": "2031-01-20"}
    draft = ok(create(client, cookie, student, **period), CREATED)
    waiting = ok(create(client, cookie, student, **period), CREATED)
    waiting = ok(act(client, cookie, waiting["id"], "submit", 1))
    done = ok(create(client, cookie, student, **period), CREATED)
    done = ok(act(client, cookie, done["id"], "submit", 1))
    at_now.set(NOW)
    done = ok(act(client, staff, done["id"], "approve", 2))
    colleague, _, _ = teaching(url, students=(student,), subject=subject)
    theirs = ok(create(client, sign_in(client, colleague.username), student), CREATED)
    theirs = ok(
        act(client, sign_in(client, colleague.username), theirs["id"], "submit", 1)
    )
    theirs = ok(act(client, staff, theirs["id"], "approve", 2))
    cookie = sign_in(client, tutor.username)

    read = ok(get(client, cookie, report_path(draft["id"])))
    assert not any(read[k] for k in CAN_KEYS)
    assert read["facts_preview"] is None
    for response in (
        edit(client, cookie, draft["id"], 1, summary="x"),
        act(client, cookie, draft["id"], "submit", 1),
        delete(client, cookie, draft["id"], 1),
    ):
        assert code_of(response) == READ_ONLY
    assert code_of(create(client, cookie, student)) == NOT_FOUND
    # D4: the reviewer can't send it back to a former author.
    review = ok(get(client, staff, report_path(waiting["id"])))
    assert (review["can_approve"], review["can_request_changes"]) == (True, False)
    assert (
        code_of(act(client, staff, waiting["id"], "request-changes", 2, reason="x"))
        == AUTHOR_NOT_CURRENT
    )
    # Approved reports: only their own.
    listed = ok(get(client, cookie, approved_path(student)))
    assert [item["id"] for item in listed["items"]] == [done["id"]]
    assert (listed["can_create"], listed["creatable_subjects"]) == (False, [])
    assert_same_not_found(
        get(client, cookie, approved_path(student, theirs["id"])),
        get(client, cookie, approved_path(student, uuid.uuid4())),
    )
    # Approving a former author's report is allowed.
    ok(act(client, staff, waiting["id"], "approve", 2))
    # A former tutor with no approved report of the student: 404.
    other = new_student(url)
    teaching(
        url,
        tutor=tutor,
        students=(other,),
        state="completed",
        starts_at=long_ago + timedelta(days=1),
    )
    assert_same_not_found(
        get(client, cookie, approved_path(other)),
        get(client, cookie, approved_path(uuid.uuid4())),
    )


def test_staff_and_finance(module_seeded_url, client, staff, owner):
    url = module_seeded_url
    _, cookie, student, _, draft = drafted(client, url)
    *_, done = approved(client, url, staff, family=None)
    finance = sign_in(client, create_user(url, roles=(FINANCE_STAFF,)).username)
    unknown = uuid.uuid4()
    for path, missing in (
        (report_path(draft["id"]), report_path(unknown)),
        (report_path(done["id"]), report_path(unknown)),
        (approved_path(student), approved_path(unknown)),
        (
            approved_path(done["student"]["id"], done["id"]),
            approved_path(done["student"]["id"], unknown),
        ),
        (REPORTS, REPORTS),
    ):
        assert_same_not_found(get(client, finance, path), get(client, finance, missing))
    # Holders read every non-draft report and the approved shape.
    administrator = sign_in(client, create_user(url, roles=(ADMINISTRATOR,)).username)
    for reviewer in (staff, administrator, sign_in(client, owner.username)):
        assert ok(get(client, reviewer, report_path(done["id"])))["id"] == done["id"]
        assert (
            ok(get(client, reviewer, approved_path(done["student"]["id"])))["items"][0][
                "id"
            ]
            == done["id"]
        )
        assert_same_not_found(
            get(client, reviewer, report_path(draft["id"])),
            get(client, reviewer, report_path(unknown)),
        )
    del cookie


# --- F: the family reads --------------------------------------------------------------


def test_the_family_reads(module_seeded_url, client, staff):
    url = module_seeded_url
    family = guardian_with_child(url)
    guardian = sign_in(client, family.user.username)
    me, mine = student_account(url)
    own = sign_in(client, me.username)
    tutor, cookie, student, subject, done = approved(client, url, staff, family=family)
    # Only approved reports: a draft, a submitted and a sent-back one stay out.
    ok(create(client, cookie, student), CREATED)
    waiting = ok(
        act(
            client,
            cookie,
            ok(create(client, cookie, student), CREATED)["id"],
            "submit",
            1,
        )
    )
    sent = ok(
        act(
            client,
            cookie,
            ok(create(client, cookie, student), CREATED)["id"],
            "submit",
            1,
        )
    )
    ok(act(client, staff, sent["id"], "request-changes", 2, reason="Rehacer"))
    listed = ok(get(client, guardian, approved_path(student)))
    assert [item["id"] for item in listed["items"]] == [done["id"]]
    assert (listed["can_create"], listed["creatable_subjects"]) == (False, [])
    for hidden in (waiting["id"], sent["id"]):
        assert_same_not_found(
            get(client, guardian, approved_path(student, hidden)),
            get(client, guardian, approved_path(student, uuid.uuid4())),
        )
        assert_same_not_found(
            get(client, guardian, report_path(hidden)),
            get(client, guardian, report_path(uuid.uuid4())),
        )
    detail = ok(get(client, guardian, approved_path(student, done["id"])))
    assert set(detail) == FAMILY_KEYS
    assert detail["author"] == {"display_name": "Tutora Informes"}
    # The working routes are hidden from families.
    assert_same_not_found(
        get(client, guardian, report_path(done["id"])),
        get(client, guardian, report_path(uuid.uuid4())),
    )
    assert_same_not_found(get(client, guardian, REPORTS), get(client, own, REPORTS))
    # A student reads their own; another student and an unlinked guardian 404.
    teaching(url, tutor=tutor, students=(mine,), subject=subject)
    theirs = ok(
        act(
            client, cookie, ok(create(client, cookie, mine), CREATED)["id"], "submit", 1
        )
    )
    theirs = ok(act(client, staff, theirs["id"], "approve", 2))
    assert [i["id"] for i in ok(get(client, own, approved_path(mine)))["items"]] == [
        theirs["id"]
    ]
    assert ok(get(client, own, approved_path(mine, theirs["id"])))["id"] == theirs["id"]
    stranger = sign_in(client, guardian_with_child(url).user.username)
    for caller, student_id, report in (
        (own, student, done["id"]),
        (stranger, student, done["id"]),
        (guardian, mine, theirs["id"]),
    ):
        assert_same_not_found(
            get(client, caller, approved_path(student_id)),
            get(client, caller, approved_path(uuid.uuid4())),
        )
        assert_same_not_found(
            get(client, caller, approved_path(student_id, report)),
            get(client, caller, approved_path(uuid.uuid4(), report)),
        )
    # A report of another student through this student's path: 404.
    assert_same_not_found(
        get(client, guardian, approved_path(student, theirs["id"])),
        get(client, guardian, approved_path(student, uuid.uuid4())),
    )


def test_an_adult_needs_the_consent_and_a_revocation_applies_at_once(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    family = guardian_with_child(url, adult=True)
    _, cookie, student, _, done = approved(client, url, staff, family=family)
    draft = ok(create(client, cookie, student), CREATED)
    guardian = sign_in(client, family.user.username)
    # Not notified without the consent.
    assert {
        row.recipient_user_id
        for row in notifications(url, APPROVED_TYPE, report_id=done["id"])
    } == {done_author(url, done["id"])}

    def reads_need_consent() -> None:
        assert code_of(get(client, guardian, approved_path(student))) == CONSENT
        assert code_of(get(client, guardian, approved_path(student, done["id"]))) == (
            CONSENT
        )

    def writes_are_hidden() -> None:
        unknown = uuid.uuid4()
        for report in (done, draft):
            for response, missing in (
                (
                    get(client, guardian, report_path(report["id"])),
                    get(client, guardian, report_path(unknown)),
                ),
                (
                    edit(client, guardian, report["id"], 1, summary="x"),
                    edit(client, guardian, unknown, 1, summary="x"),
                ),
                (
                    act(client, guardian, report["id"], "submit", 1),
                    act(client, guardian, unknown, "submit", 1),
                ),
                (
                    act(client, guardian, report["id"], "approve", 1),
                    act(client, guardian, unknown, "approve", 1),
                ),
                (
                    act(client, guardian, report["id"], "retract", 1, reason="x"),
                    act(client, guardian, unknown, "retract", 1, reason="x"),
                ),
                (
                    delete(client, guardian, report["id"], 1),
                    delete(client, guardian, unknown, 1),
                ),
            ):
                assert_same_not_found(response, missing)
        assert_same_not_found(
            create(client, guardian, student), create(client, guardian, unknown)
        )

    reads_need_consent()
    writes_are_hidden()
    give_academic_consent(url, family)
    assert [
        i["id"] for i in ok(get(client, guardian, approved_path(student)))["items"]
    ] == [done["id"]]
    assert (
        ok(get(client, guardian, approved_path(student, done["id"])))["id"]
        == (done["id"])
    )
    writes_are_hidden()
    revoke_academic_consent(url, family)
    reads_need_consent()
    writes_are_hidden()


def done_author(url: str, report_id: Any) -> uuid.UUID:
    [row] = run_sql(
        url,
        "SELECT author_user_id FROM progress_reports WHERE id = :id",
        {"id": report_id},
    )
    return uuid.UUID(str(row.author_user_id))


# --- AM2: the create hints ------------------------------------------------------------


def test_the_create_hints(module_seeded_url, client, staff, coordinator):
    url = module_seeded_url
    family = guardian_with_child(url, adult=True, consent=True)
    tutor, student, maths = teaching(url, students=(family.student_id,))
    retired = new_subject(url)
    teaching(url, tutor=tutor, students=(student,), subject=retired)
    run_sql(
        url, "UPDATE subjects SET is_active = false WHERE id = :id", {"id": retired}
    )
    cookie = sign_in(client, tutor.username)
    hints = ok(get(client, cookie, approved_path(student), kind="weekly"))
    assert hints["can_create"] is True
    assert [s["id"] for s in hints["creatable_subjects"]] == [str(maths)]
    assert set(hints["creatable_subjects"][0]) == {"id", "name"}
    english = new_subject(url)
    assert code_of(create(client, cookie, student, subject_id=str(english))) == (
        SUBJECT_NOT_ASSIGNED
    )
    # A coordinator: no; a coordinator who is also the current tutor: yes.
    assert (
        ok(get(client, staff, approved_path(student)))["can_create"],
        ok(get(client, staff, approved_path(student)))["creatable_subjects"],
    ) == (False, [])
    both = a_tutor(url, "Coordinadora Docente", ACADEMIC_COORDINATOR)
    teaching(url, tutor=both, students=(student,), subject=maths)
    assert (
        ok(get(client, sign_in(client, both.username), approved_path(student)))[
            "can_create"
        ]
        is True
    )
    # The student's family: no.
    guardian = sign_in(client, family.user.username)
    assert (
        ok(get(client, guardian, approved_path(student)))["can_create"],
        ok(get(client, guardian, approved_path(student)))["creatable_subjects"],
    ) == (False, [])
    del coordinator


# --- B5, AM3: recipients and params ---------------------------------------------------


def test_the_recipients_and_params(module_seeded_url, client, staff):
    url = module_seeded_url
    me, mine = student_account(url)
    consenting = guardian_with_child(url, adult=True, consent=True)
    # A guardian of the same adult student: one with consent, one without.
    run_sql(
        url,
        "UPDATE student_guardians SET student_id = :s WHERE guardian_id = :g",
        {"s": mine, "g": consenting.guardian_id},
    )
    run_sql(
        url,
        "UPDATE consent_records SET student_id = :s WHERE guardian_id = :g",
        {"s": mine, "g": consenting.guardian_id},
    )
    silent = guardian_with_child(url, adult=True)
    run_sql(
        url,
        "UPDATE student_guardians SET student_id = :s WHERE guardian_id = :g",
        {"s": mine, "g": silent.guardian_id},
    )
    tutor, cookie, student, _, view = submitted(
        client, url, family=Family(me, uuid.uuid4(), mine), summary="Nunca aquí"
    )
    assert student == mine
    done = ok(act(client, staff, view["id"], "approve", 2))
    sent = notifications(url, APPROVED_TYPE, report_id=view["id"])
    assert {row.recipient_user_id: row.params["audience"] for row in sent} == {
        tutor.id: "author",
        me.id: "family",
        consenting.user.id: "family",
    }
    for row in sent:
        assert set(row.params) == {
            "student_id",
            "report_id",
            "kind",
            "period_start",
            "period_end",
            "audience",
        }
        assert row.params["student_id"] == str(mine)
        assert (
            row.params["kind"],
            row.params["period_start"],
            row.params["period_end"],
        ) == ("on_demand", "2031-02-01", "2031-03-05")
        assert "Nunca" not in str(row.params)
    assert done["approved"] is not None
    # Sent back and retracted: the author only, ids only.
    _, _, _, _, other = submitted(client, url, tutor=tutor)
    ok(act(client, staff, other["id"], "request-changes", 2, reason="Motivo secreto"))
    [row] = notifications(url, CHANGES_TYPE, report_id=other["id"])
    assert set(row.params) == {"student_id", "report_id"}
    assert "secreto" not in str(row.params)


def test_an_author_who_is_also_family_gets_the_author_audience(
    module_seeded_url, client, staff
):
    """AM3: after deduplication a user who is both the author and a family
    recipient gets ``author``."""
    url = module_seeded_url
    parent_tutor = create_user(
        url, roles=(TUTOR, GUARDIAN), display_name="Madre Tutora"
    )
    family = guardian_with_child(url)
    run_sql(
        url,
        "UPDATE guardians SET user_id = :u WHERE id = :g",
        {"u": parent_tutor.id, "g": family.guardian_id},
    )
    _, _, _, _, view = submitted(client, url, tutor=parent_tutor, family=family)
    ok(act(client, staff, view["id"], "approve", 2))
    sent = notifications(url, APPROVED_TYPE, report_id=view["id"])
    assert [(row.recipient_user_id, row.params["audience"]) for row in sent] == [
        (parent_tutor.id, "author")
    ]


# --- I3: IDOR -------------------------------------------------------------------------


def test_the_hidden_reports_get_the_same_404(module_seeded_url, client, staff):
    url = module_seeded_url
    tutor, cookie, student, subject, draft = drafted(
        client, url, tutor=a_tutor(url, "Tutora Informes", GUARDIAN)
    )
    waiting = ok(
        act(
            client,
            cookie,
            ok(create(client, cookie, student), CREATED)["id"],
            "submit",
            1,
        )
    )
    other_tutor = sign_in(client, a_tutor(url, "Ajena").username)
    colleague = a_tutor(url, "Colega Idor")
    teaching(url, tutor=colleague, students=(student,), subject=subject)
    current = sign_in(client, colleague.username)
    unknown = uuid.uuid4()
    for caller, report in (
        (other_tutor, draft["id"]),
        (other_tutor, waiting["id"]),
        (current, draft["id"]),
        (current, waiting["id"]),
        (staff, draft["id"]),
    ):
        for response, missing in (
            (
                get(client, caller, report_path(report)),
                get(client, caller, report_path(unknown)),
            ),
            (
                edit(client, caller, report, 1, summary="x"),
                edit(client, caller, unknown, 1, summary="x"),
            ),
            (delete(client, caller, report, 1), delete(client, caller, unknown, 1)),
        ):
            assert_same_not_found(response, missing)
    # The working list never shows them either.
    assert ok(get(client, current, REPORTS))["items"] == []
    # A tutor who lost the role: 404, even on their own report.
    run_sql(
        url,
        "DELETE FROM user_roles WHERE user_id = :id"
        " AND role_id = (SELECT id FROM roles WHERE code = 'tutor')",
        {"id": tutor.id},
    )
    gone = sign_in(client, tutor.username)
    assert_same_not_found(
        get(client, gone, report_path(draft["id"])),
        get(client, gone, report_path(unknown)),
    )
    assert_same_not_found(get(client, gone, REPORTS), get(client, gone, REPORTS))


def test_the_original_tutor_after_a_real_substitution(
    module_seeded_url, client, owner, staff, at_now
):
    """#55: once the substitute accepted, the original tutor (the student
    not otherwise assigned) is a former author: read-only; the substitute
    writes their own reports and gets 404 on the original's draft."""
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
    period = {
        "period_start": (T0 - timedelta(days=10)).date().isoformat(),
        "period_end": T0.date().isoformat(),
    }
    own = ok(create(client, original, setup.student, **period), CREATED)
    detail = get(client, boss, f"/api/v1/class-sessions/{created['id']}").json()
    item = substituted(client, boss, detail, substitute.id)
    taker = sign_in(client, substitute.username)
    assert code_of(answer(client, taker, item["offer_id"], "accept")) == OK

    original = sign_in(client, setup.tutor.username)
    assert code_of(edit(client, original, own["id"], 1, summary="x")) == READ_ONLY
    assert ok(get(client, original, report_path(own["id"])))["id"] == own["id"]
    assert code_of(create(client, original, setup.student, **period)) == NOT_FOUND
    taker = sign_in(client, substitute.username)
    assert_same_not_found(
        get(client, taker, report_path(own["id"])),
        get(client, taker, report_path(uuid.uuid4())),
    )
    ok(create(client, taker, setup.student, **period), CREATED)
    del staff


# --- E2, F3: lists --------------------------------------------------------------------


def test_the_working_list(module_seeded_url, client, staff, at_now):
    url = module_seeded_url
    tutor, cookie, student, _, draft = drafted(client, url)
    at_now.set(NOW + timedelta(minutes=1))
    first = ok(
        act(
            client,
            cookie,
            ok(create(client, cookie, student), CREATED)["id"],
            "submit",
            1,
        )
    )
    at_now.set(NOW + timedelta(minutes=2))
    second = ok(
        act(
            client,
            cookie,
            ok(create(client, cookie, student), CREATED)["id"],
            "submit",
            1,
        )
    )
    at_now.set(NOW)
    mine = ok(get(client, cookie, REPORTS))
    assert {item["id"] for item in mine["items"]} >= {
        draft["id"],
        first["id"],
        second["id"],
    }
    assert set(mine["items"][0]) == LIST_ITEM_KEYS
    assert all(
        item["author"] == {"display_name": "Tutora Informes"} for item in mine["items"]
    )
    # The reviewer queue: oldest submission first, no drafts.
    queue = ok(get(client, staff, REPORTS, status="submitted", student_id=str(student)))
    assert [item["id"] for item in queue["items"]] == [first["id"], second["id"]]
    every = ok(get(client, staff, REPORTS, student_id=str(student)))
    assert draft["id"] not in {item["id"] for item in every["items"]}
    assert ok(get(client, staff, REPORTS, mine="true"))["items"] == []
    # The cursor.
    page = ok(
        get(
            client, staff, REPORTS, status="submitted", student_id=str(student), limit=1
        )
    )
    assert [item["id"] for item in page["items"]] == [first["id"]]
    rest = ok(
        get(
            client,
            staff,
            REPORTS,
            status="submitted",
            student_id=str(student),
            limit=1,
            cursor=page["next_cursor"],
        )
    )
    assert [item["id"] for item in rest["items"]] == [second["id"]]
    assert code_of(get(client, staff, REPORTS, cursor="nope")) == (
        400,
        "invalid_cursor",
    )
    assert code_of(get(client, staff, REPORTS, limit=51)) == INVALID
    del tutor


def test_the_approved_list_order_filters_and_cursor(module_seeded_url, client, staff):
    url = module_seeded_url
    family = guardian_with_child(url)
    guardian = sign_in(client, family.user.username)
    tutor, student, subject = teaching(url, students=(family.student_id,))
    cookie = sign_in(client, tutor.username)
    made = {}
    for name, values in (
        (
            "january",
            {"kind": "monthly", "period_start": "2031-01-01", "period_end": ...},
        ),
        (
            "february",
            {
                "kind": "monthly",
                "period_start": "2031-02-01",
                "period_end": ...,
                "subject_id": str(subject),
            },
        ),
        ("week", {"kind": "weekly", "period_start": "2031-02-24", "period_end": ...}),
    ):
        view = ok(create(client, cookie, student, **values), CREATED)
        view = ok(act(client, cookie, view["id"], "submit", 1))
        made[name] = ok(act(client, staff, view["id"], "approve", 2))["id"]
    listed = ok(get(client, guardian, approved_path(student)))
    assert [item["id"] for item in listed["items"]] == [
        made["week"],
        made["february"],
        made["january"],
    ]
    weekly = ok(get(client, guardian, approved_path(student), kind="weekly"))
    assert [item["id"] for item in weekly["items"]] == [made["week"]]
    by_subject = ok(
        get(client, guardian, approved_path(student), subject_id=str(subject))
    )
    assert [item["id"] for item in by_subject["items"]] == [made["february"]]
    page = ok(get(client, guardian, approved_path(student), limit=2))
    assert len(page["items"]) == 2
    rest = ok(
        get(
            client,
            guardian,
            approved_path(student),
            limit=2,
            cursor=page["next_cursor"],
        )
    )
    assert [item["id"] for item in rest["items"]] == [made["january"]]
    assert rest["next_cursor"] is None


# --- Statement budgets ----------------------------------------------------------------


def _many(url: str, client: TestClient, staff: Any, count: int) -> Any:
    family = guardian_with_child(url, adult=True, consent=True)
    tutor, student, subject = teaching(url, students=(family.student_id,))
    cookie = sign_in(client, tutor.username)
    ids = []
    for _ in range(count):
        view = ok(create(client, cookie, student), CREATED)
        view = ok(act(client, cookie, view["id"], "submit", 1))
        ids.append(ok(act(client, staff, view["id"], "approve", 2))["id"])
    return tutor, family, student, subject, ids


@pytest.mark.parametrize("count", [1, 20])
def test_the_list_statement_budgets(
    module_seeded_url, client, staff, coordinator, count
):
    url = module_seeded_url
    tutor, family, student, _, ids = _many(url, client, staff, count)
    for principal in (
        principal_of(tutor, TUTOR),
        principal_of(coordinator, ACADEMIC_COORDINATOR),
        principal_of(family.user, GUARDIAN),
    ):
        statements, listed = count_statements(
            url,
            lambda s, p=principal: workflow.list_approved(
                s, p, student, kind=None, subject_id=None, cursor=None, limit=50
            ),
        )
        assert statements <= 6, (principal.roles, statements)
        assert len(listed.items) == count
    statements, page = count_statements(
        url,
        lambda s: workflow.list_reports(
            s,
            principal_of(tutor, TUTOR),
            status=None,
            student_id=None,
            kind=None,
            mine=False,
            cursor=None,
            limit=20,
        ),
    )
    assert statements <= 5
    assert len(page.items) == count


@pytest.mark.parametrize("rows", [1, 30])
def test_the_view_and_facts_statement_budgets(
    module_seeded_url, client, staff, coordinator, rows
):
    url = module_seeded_url
    tutor, student, subject = teaching(url)
    template = _template(url, subject)
    for day in range(rows):
        on = date(2031, 2, 1) + timedelta(days=day)
        _evaluation(url, student, subject, tutor.id, template, on, "50.00")
        _homework(url, student, subject, tutor.id, on)
        _session(url, tutor, student, subject, _utc(on, 15), mark="present")
        _plan(
            url,
            student,
            subject,
            tutor.id,
            title=f"P{day}",
            status="completed",
            starts_on=date(2031, 1, 1),
            activated_at=_utc(date(2031, 1, 2), 18),
            closed_at=_utc(on, 18),
            milestones=("done", "pending"),
        )
    cookie = sign_in(client, tutor.username)
    view = ok(create(client, cookie, student), CREATED)
    assert view["facts_preview"]["evaluations"]["count"] == rows
    report_id = uuid.UUID(view["id"])
    author = principal_of(tutor, TUTOR)
    statements, found = count_statements(
        url, lambda s: workflow.get_report(s, author, report_id)
    )
    assert statements <= 14
    assert found.facts_preview is not None

    async def _compile(session: Any) -> Any:
        from app.modules.academics import service as academics
        from app.modules.catalog import service as catalog

        row = await academics.report_with_subject(
            session, report_id, subjects=catalog.subject_name_columns()
        )
        return await workflow.compile_facts(session, row)

    statements, facts = count_statements(url, _compile)
    assert statements <= 1 + 8  # the report itself, then the compile
    assert facts["assignments"]["count"] == rows
    submitted_view = ok(act(client, cookie, view["id"], "submit", 1))
    for principal in (author, principal_of(coordinator, ACADEMIC_COORDINATOR)):
        statements, found = count_statements(
            url, lambda s, p=principal: workflow.get_report(s, p, report_id)
        )
        assert statements <= 6, (principal.roles, statements)
        assert found.facts_preview is None
    assert submitted_view["facts"]["evaluations"]["count"] == rows
