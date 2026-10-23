"""Staff review of session requests (#60 B, C, D, E, F, G; I1) with real
logins through the real app.

Helpers: ``session_request_support`` (the clock is frozen at its ``NOW``,
Monday 2030-12-30 12:00 Mexico City, for the module; requests are on Monday
2031-01-06 or later Mondays). Requests are created through the family route
where it matters (the happy path, the cap) and written directly otherwise
(:func:`insert_request`: no slot check, no rate limit, any ``created_at``).
"""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Subject
from app.modules.families.models import Student
from app.modules.identity.models import User
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.session_request_support import (
    MONDAY_WINDOW,
    NOW,
    REQUESTS,
    Family,
    approval_of,
    create_request,
    created,
    deactivate_guardian,
    guardian_with_child,
    insert_request,
    request_body,
    revoke_academic_consent,
    student_account,
    unlink,
    withdraw,
)
from tests.integration.test_class_session_create import (
    ALL_DAY,
    MONDAY,
    SATURDAY,
    at,
    change,
    close_center,
    existing_session,
    new_room,
    new_subject,
    new_tutor,
    revoke_all,
)

APPROVALS = "/api/v1/approval-requests"
INVALID = (422, "validation_error")
CONFLICT = (409, "scheduling_conflict")
CONFIRMED_TYPE = "scheduling.session_request_confirmed"
REJECTED_TYPE = "scheduling.session_request_rejected"
OFFER_RECEIVED = "scheduling.offer_received"
REVIEW_KEYS = {
    "id",
    "status",
    "version",
    "created_at",
    "resolved_at",
    "student",
    "subject",
    "starts_at",
    "ends_at",
    "duration_minutes",
    "note",
    "requested_by",
    "approval_request_id",
    "class_session_id",
    "rejection_reason",
    "can_decide",
}


@pytest.fixture(scope="module", autouse=True)
def frozen() -> Iterator[None]:
    with frozen_clock(NOW):
        yield


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(scope="module")
def owner_cookie(client: TestClient, owner: CreatedUser) -> Any:
    return sign_in(client, owner.username)


@pytest.fixture(scope="module")
def admin_cookie(module_seeded_url: str, client: TestClient) -> Any:
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    return sign_in(client, admin.username)


@pytest.fixture
def family(module_seeded_url: str) -> Family:
    return guardian_with_child(module_seeded_url)


@pytest.fixture
def cookie(client: TestClient, family: Family) -> Any:
    return sign_in(client, family.user.username)


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    return new_subject(module_seeded_url)


@pytest.fixture
def tutor(module_seeded_url: str, subject: uuid.UUID) -> uuid.UUID:
    """Qualified for the subject and free Mondays 10:00-12:00."""
    return new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)


def path(request_id: Any, action: str) -> str:
    return f"{REQUESTS}/{request_id}/{action}"


def confirm(client: TestClient, cookie: Any, request_id: Any, **body: Any) -> Response:
    payload = {"version": 1, **body}
    for key, value in payload.items():
        if isinstance(value, uuid.UUID):
            payload[key] = str(value)
    return post(client, cookie, path(request_id, "confirm"), payload)


def reject(
    client: TestClient,
    cookie: Any,
    request_id: Any,
    reason: Any = "Sin cupo",
    **body: Any,
) -> Response:
    return post(
        client,
        cookie,
        path(request_id, "reject"),
        {"version": 1, "reason": reason, **body},
    )


def review(client: TestClient, cookie: Any, request_id: Any) -> Response:
    return get(client, cookie, path(request_id, "review"))


def ok(response: Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def request_row(url: str, request_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT status, version, class_session_id, rejection_reason, resolved_at"
        " FROM session_requests WHERE id = :id",
        {"id": request_id},
    )
    return row


def notifications_of(url: str, user_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT notification_type, params FROM notifications"
        " WHERE recipient_user_id = :u ORDER BY created_at, id",
        {"u": user_id},
    )


def decision_notes(url: str, user_id: Any) -> list[Any]:
    return [
        row
        for row in notifications_of(url, user_id)
        if row.notification_type in (CONFIRMED_TYPE, REJECTED_TYPE)
    ]


def a_request(
    url: str,
    family: Family,
    subject: uuid.UUID,
    starts_at: datetime | None = None,
    **extra: Any,
) -> uuid.UUID:
    return insert_request(
        url,
        student_id=family.student_id,
        subject_id=subject,
        requested_by=family.user.id,
        starts_at=at(MONDAY, "10:00") if starts_at is None else starts_at,
        **extra,
    )


def assert_still_pending(url: str, request_id: Any) -> None:
    row = request_row(url, request_id)
    assert (row.status, row.version, row.class_session_id) == ("pending", 1, None)
    assert approval_of(url, request_id).status == "pending"


def conflict_codes(response: Response) -> list[tuple[str, str]]:
    assert code_of(response) == CONFLICT, response.text
    return [(c["code"], c["severity"]) for c in response.json()["conflicts"]]


# --- B: the staff read ---------------------------------------------------------------


def test_the_review_shows_a_pending_request(
    module_seeded_url, client, cookie, family, subject, tutor, owner_cookie
):
    detail = created(
        create_request(
            client, cookie, request_body(family.student_id, subject, note="Por favor")
        )
    )

    found = ok(review(client, owner_cookie, detail["id"]))

    assert set(found) == REVIEW_KEYS
    assert found["status"] == "pending" and found["can_decide"] is True
    assert found["student"] == {
        "id": str(family.student_id),
        "display_name": detail["student"]["display_name"],
        "is_active": True,
    }
    assert found["subject"]["id"] == str(subject) and found["subject"]["is_active"]
    assert found["requested_by"]["id"] == str(family.user.id)
    assert found["requested_by"]["display_name"]
    assert found["note"] == "Por favor" and found["duration_minutes"] == 60
    assert found["approval_request_id"] == str(
        approval_of(module_seeded_url, detail["id"]).id
    )
    assert found["class_session_id"] is None and found["rejection_reason"] is None
    assert code_of(review(client, owner_cookie, uuid.uuid4())) == (404, "not_found")


def test_the_review_takes_at_most_six_statements(module_seeded_url, family, subject):
    request_id = a_request(module_seeded_url, family, subject)

    async def call(session: AsyncSession) -> Any:
        return await scheduling.get_session_request_review(session, request_id)

    statements, found = count_statements(module_seeded_url, call)

    assert found.id == request_id
    assert statements <= 6


def test_a_started_request_can_no_longer_be_confirmed(
    module_seeded_url, client, family, subject, owner
):
    request_id = a_request(module_seeded_url, family, subject)
    with frozen_clock(at(MONDAY, "10:00")):
        staff = sign_in(client, owner.username)
        found = ok(review(client, staff, request_id))
        refused = confirm(client, staff, request_id, tutor_user_id=uuid.uuid4())

    assert found["can_decide"] is False
    assert code_of(refused) == (409, "session_request_started")
    assert_still_pending(module_seeded_url, request_id)


# --- C: confirm ----------------------------------------------------------------------


def test_confirming_offers_the_session_to_the_tutor(
    module_seeded_url, client, cookie, family, subject, tutor, owner, owner_cookie
):
    detail = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )
    approval = approval_of(module_seeded_url, detail["id"])

    body = ok(confirm(client, owner_cookie, detail["id"], tutor_user_id=tutor))

    request, session = body["request"], body["class_session"]
    assert request["status"] == "confirmed" and request["version"] == 2
    assert request["class_session_id"] == session["id"]
    assert request["resolved_at"] is not None and request["can_decide"] is False
    assert session["state"] == "offered" and session["kind"] == "individual"
    assert session["tutor"]["id"] == str(tutor)
    assert [p["student_id"] for p in session["participants"]] == [
        str(family.student_id)
    ]
    assert session["starts_at"] == detail["starts_at"]
    assert session["ends_at"] == detail["ends_at"]
    assert session["warnings"] == []
    offer = session["pending_offer"]
    # #52 B2 default: min(now + 48 h, start); the start is a week away.
    assert datetime.fromisoformat(offer["respond_by"]) == NOW + timedelta(hours=48)
    [offer_row] = run_sql(
        module_seeded_url,
        "SELECT o.id, o.status, o.offered_by_user_id, o.tutor_user_id"
        " FROM assignment_offers o JOIN assignment_offer_sessions s"
        " ON s.offer_id = o.id WHERE s.class_session_id = :s",
        {"s": session["id"]},
    )
    assert offer_row.status == "pending"
    assert (offer_row.offered_by_user_id, offer_row.tutor_user_id) == (owner.id, tutor)
    stored = request_row(module_seeded_url, detail["id"])
    assert stored.status == "confirmed"
    assert str(stored.class_session_id) == session["id"]
    inbox = approval_of(module_seeded_url, detail["id"])
    assert (inbox.status, inbox.resolved_by_user_id) == ("approved", owner.id)
    assert [
        n.notification_type for n in notifications_of(module_seeded_url, tutor)
    ] == [OFFER_RECEIVED]
    [note] = decision_notes(module_seeded_url, family.user.id)
    assert note.notification_type == CONFIRMED_TYPE
    assert note.params == {
        "session_request_id": detail["id"],
        "student_name": detail["student"]["display_name"],
        "subject_name": detail["subject"]["name"],
        "starts_at": detail["starts_at"].replace("+00:00", "Z"),
    }
    # The audit: the request's update, the session's and the offer's creates
    # and the inbox item's update, all by the Owner.
    [update] = events(
        module_seeded_url,
        record_type="session_requests",
        record_id=detail["id"],
        action="update",
    )
    assert update.actor_user_id == owner.id
    assert events(
        module_seeded_url,
        record_type="class_sessions",
        record_id=session["id"],
        action="create",
    )
    assert events(
        module_seeded_url,
        record_type="assignment_offers",
        record_id=offer_row.id,
        action="create",
    )
    assert events(
        module_seeded_url,
        record_type="approval_requests",
        record_id=approval.id,
        action="update",
    )
    # The decided request: the review shows the session, a second confirm is
    # not_pending with the status.
    assert (
        ok(review(client, owner_cookie, detail["id"]))["class_session_id"]
        == (session["id"])
    )
    again = confirm(client, owner_cookie, detail["id"], version=2, tutor_user_id=tutor)
    assert code_of(again) == (409, "session_request_not_pending")
    assert again.json()["details"] == {"status": "confirmed"}


def test_an_administrator_confirms_with_a_room_and_an_earlier_deadline(
    module_seeded_url, client, family, subject, tutor, admin_cookie
):
    request_id = a_request(module_seeded_url, family, subject)
    room = new_room(module_seeded_url)
    respond_by = NOW + timedelta(hours=5)

    body = ok(
        confirm(
            client,
            admin_cookie,
            request_id,
            tutor_user_id=tutor,
            classroom_id=room,
            respond_by=respond_by.isoformat(),
        )
    )

    session = body["class_session"]
    assert session["classroom"]["id"] == str(room)
    assert datetime.fromisoformat(session["pending_offer"]["respond_by"]) == respond_by


def test_the_refusals_come_in_order(
    module_seeded_url, client, family, subject, tutor, owner_cookie, cookie
):
    unknown = confirm(client, owner_cookie, uuid.uuid4(), tutor_user_id=tutor)
    request_id = a_request(module_seeded_url, family, subject)
    stale = confirm(client, owner_cookie, request_id, version=7, tutor_user_id=tutor)
    no_tutor = confirm(client, owner_cookie, request_id)
    extra = confirm(
        client, owner_cookie, request_id, tutor_user_id=tutor, kind="individual"
    )
    _withdrawn = withdraw(client, cookie, request_id, 1)
    withdrawn = confirm(
        client, owner_cookie, request_id, version=2, tutor_user_id=tutor
    )

    assert code_of(unknown) == (404, "not_found")
    assert code_of(stale) == (409, "version_conflict")
    assert code_of(no_tutor) == INVALID
    assert [e["path"][-1] for e in no_tutor.json()["errors"]] == ["tutor_user_id"]
    assert code_of(extra) == INVALID
    assert _withdrawn.status_code == 200, _withdrawn.text
    assert code_of(withdrawn) == (409, "session_request_not_pending")
    assert withdrawn.json()["details"] == {"status": "withdrawn"}


def test_a_rejected_request_cannot_be_confirmed(
    module_seeded_url, client, family, subject, tutor, owner_cookie
):
    request_id = a_request(module_seeded_url, family, subject)
    ok(reject(client, owner_cookie, request_id))

    refused = confirm(client, owner_cookie, request_id, version=2, tutor_user_id=tutor)

    assert code_of(refused) == (409, "session_request_not_pending")
    assert refused.json()["details"] == {"status": "rejected"}


def test_an_override_needs_schedule_override(
    module_seeded_url, client, family, subject, tutor, admin_cookie
):
    request_id = a_request(module_seeded_url, family, subject)

    refused = confirm(
        client,
        admin_cookie,
        request_id,
        tutor_user_id=tutor,
        override={"codes": ["notice_window"], "reason": "Urgente"},
    )

    assert code_of(refused) == (403, "permission_denied")
    assert_still_pending(module_seeded_url, request_id)


# --- C3: the slot was taken since the request ----------------------------------------


def test_the_student_is_busy_now(
    module_seeded_url, client, family, subject, tutor, owner_cookie
):
    request_id = a_request(module_seeded_url, family, subject)
    existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "10:30"),
        state="confirmed",
        student_id=family.student_id,
    )

    refused = confirm(client, owner_cookie, request_id, tutor_user_id=tutor)

    assert conflict_codes(refused) == [("student_overlap", "hard")]
    assert_still_pending(module_seeded_url, request_id)


def test_the_tutor_is_busy_now(
    module_seeded_url, client, family, subject, tutor, owner_cookie
):
    request_id = a_request(module_seeded_url, family, subject)
    existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "10:00"),
        state="confirmed",
        tutor_user_id=tutor,
    )

    refused = confirm(client, owner_cookie, request_id, tutor_user_id=tutor)

    assert conflict_codes(refused) == [("tutor_overlap", "hard")]
    assert_still_pending(module_seeded_url, request_id)


def test_two_requests_with_one_tutor_at_one_time(
    module_seeded_url, client, family, subject, tutor, owner_cookie, admin_cookie
):
    other = guardian_with_child(module_seeded_url)
    first = a_request(module_seeded_url, family, subject)
    second = a_request(module_seeded_url, other, subject)

    done = confirm(client, owner_cookie, first, tutor_user_id=tutor)
    refused = confirm(client, admin_cookie, second, tutor_user_id=tutor)

    assert done.status_code == 200, done.text
    assert conflict_codes(refused) == [("tutor_overlap", "hard")]
    assert_still_pending(module_seeded_url, second)


@pytest.mark.parametrize("cause", ["unavailable", "revoked"])
def test_soft_tutor_conflicts_need_the_owners_override(
    module_seeded_url, client, family, subject, owner_cookie, admin_cookie, cause
):
    if cause == "unavailable":
        tutor = new_tutor(module_seeded_url, qualified_for=[subject], windows=[])
        code = "tutor_unavailable"
    else:
        tutor = new_tutor(
            module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW
        )
        revoke_all(module_seeded_url, tutor)
        code = "tutor_not_qualified"
    request_id = a_request(module_seeded_url, family, subject)

    refused = confirm(client, admin_cookie, request_id, tutor_user_id=tutor)
    assert conflict_codes(refused) == [(code, "soft")]
    assert_still_pending(module_seeded_url, request_id)

    done = confirm(
        client,
        owner_cookie,
        request_id,
        tutor_user_id=tutor,
        override={"codes": [code], "reason": "Acordado con la familia"},
    )

    session = ok(done)["class_session"]
    assert [o["codes"] for o in session["overrides"]] == [[code]]
    [override] = run_sql(
        module_seeded_url,
        "SELECT codes, reason FROM class_session_overrides WHERE class_session_id = :s",
        {"s": session["id"]},
    )
    assert list(override.codes) == [code]
    assert events(
        module_seeded_url,
        record_type="class_sessions",
        record_id=session["id"],
        action="scheduling.session_override",
    )


def test_a_closure_added_since_the_request(
    module_seeded_url, client, family, subject, owner_cookie
):
    # A date no other test of the module uses (the closure is center-wide).
    day = date(2031, 2, 24)
    tutor = new_tutor(module_seeded_url, qualified_for=[subject], windows=ALL_DAY)
    request_id = a_request(module_seeded_url, family, subject, at(day, "10:00"))
    close_center(module_seeded_url, day)

    refused = confirm(client, owner_cookie, request_id, tutor_user_id=tutor)

    assert conflict_codes(refused) == [("center_closed", "soft")]
    assert_still_pending(module_seeded_url, request_id)


def test_outside_the_operating_hours_now(
    module_seeded_url, client, family, subject, owner_cookie
):
    # Saturdays close at 14:00 (the default hours).
    tutor = new_tutor(module_seeded_url, qualified_for=[subject], windows=ALL_DAY)
    request_id = a_request(module_seeded_url, family, subject, at(SATURDAY, "14:00"))

    refused = confirm(client, owner_cookie, request_id, tutor_user_id=tutor)

    assert conflict_codes(refused) == [("outside_operating_hours", "soft")]
    assert_still_pending(module_seeded_url, request_id)


def test_the_chosen_room_is_busy_or_closed(
    module_seeded_url, client, family, subject, tutor, owner_cookie
):
    busy = new_room(module_seeded_url)
    existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "10:00"),
        state="unassigned",
        classroom_id=busy,
    )
    closed = new_room(module_seeded_url, windows=[(1, time(13), time(14))])
    request_id = a_request(module_seeded_url, family, subject)

    overlap = confirm(
        client, owner_cookie, request_id, tutor_user_id=tutor, classroom_id=busy
    )
    unavailable = confirm(
        client, owner_cookie, request_id, tutor_user_id=tutor, classroom_id=closed
    )

    assert conflict_codes(overlap) == [("room_overlap", "hard")]
    assert conflict_codes(unavailable) == [("room_unavailable", "soft")]
    assert_still_pending(module_seeded_url, request_id)


@pytest.mark.parametrize("what", ["student", "subject"])
def test_an_inactive_student_or_subject_is_422(
    module_seeded_url, client, family, subject, tutor, owner_cookie, what
):
    request_id = a_request(module_seeded_url, family, subject)

    async def deactivate(session: AsyncSession) -> None:
        if what == "student":
            await change(session, Student, family.student_id, is_active=False)
        else:
            await change(session, Subject, subject, is_active=False)

    in_uow(module_seeded_url, deactivate)

    refused = confirm(client, owner_cookie, request_id, tutor_user_id=tutor)

    assert code_of(refused) == INVALID
    assert [(e["path"][-1], e["code"]) for e in refused.json()["errors"]] == [
        (f"{what}_id", f"{what}_inactive")
    ]
    assert_still_pending(module_seeded_url, request_id)


# --- C4: the booking notice counts from the request [E3] -----------------------------


def test_the_notice_counts_from_when_the_family_asked(
    module_seeded_url, client, family, subject, owner
):
    day = MONDAY + timedelta(days=7)
    start = at(day, "10:00")
    tutor = new_tutor(module_seeded_url, qualified_for=[subject], windows=ALL_DAY)
    in_time = a_request(
        module_seeded_url, family, subject, start, created_at=start - timedelta(days=3)
    )
    other = guardian_with_child(module_seeded_url)
    late = a_request(
        module_seeded_url, other, subject, start, created_at=start - timedelta(hours=3)
    )
    second_tutor = new_tutor(
        module_seeded_url, qualified_for=[subject], windows=ALL_DAY
    )

    with frozen_clock(start - timedelta(hours=2)):
        staff = sign_in(client, owner.username)
        confirmed = confirm(client, staff, in_time, tutor_user_id=tutor)
        refused = confirm(client, staff, late, tutor_user_id=second_tutor)
        overridden = confirm(
            client,
            staff,
            late,
            tutor_user_id=second_tutor,
            override={"codes": ["notice_window"], "reason": "La familia avisó tarde"},
        )

    session = ok(confirmed)["class_session"]
    assert session["overrides"] == []
    # The offer's deadline is the start (#52: never after it).
    assert datetime.fromisoformat(session["pending_offer"]["respond_by"]) == start
    assert code_of(refused) == CONFLICT
    assert refused.json()["conflicts"] == [
        {
            "code": "notice_window",
            "severity": "soft",
            "details": {"booking_notice_hours": 24},
        }
    ]
    assert ok(overridden)["class_session"]["overrides"][0]["codes"] == ["notice_window"]


# --- D: reject -----------------------------------------------------------------------


def test_rejecting_stores_the_reason_on_the_request_and_the_inbox_item(
    module_seeded_url, client, family, subject, owner, owner_cookie
):
    request_id = a_request(module_seeded_url, family, subject)

    found = ok(reject(client, owner_cookie, request_id, "  Sin tutor ese día  "))

    assert found["status"] == "rejected" and found["version"] == 2
    assert found["rejection_reason"] == "Sin tutor ese día"
    assert found["class_session_id"] is None and found["can_decide"] is False
    row = request_row(module_seeded_url, request_id)
    assert (row.status, row.rejection_reason) == ("rejected", "Sin tutor ese día")
    [inbox] = run_sql(
        module_seeded_url,
        "SELECT status, reason, resolved_by_user_id FROM approval_requests"
        " WHERE subject_id = :s",
        {"s": request_id},
    )
    assert tuple(inbox) == ("rejected", "Sin tutor ese día", owner.id)
    [note] = decision_notes(module_seeded_url, family.user.id)
    assert note.notification_type == REJECTED_TYPE
    assert "Sin tutor" not in str(note.params)
    assert code_of(reject(client, owner_cookie, request_id, version=2)) == (
        409,
        "session_request_not_pending",
    )


@pytest.mark.parametrize(
    ("reason", "code"),
    [("", "missing"), ("   ", "missing"), ("x" * 1001, "string_too_long")],
    ids=["empty", "blank", "too-long"],
)
def test_the_reason_is_validated(
    module_seeded_url, client, family, subject, owner_cookie, reason, code
):
    request_id = a_request(module_seeded_url, family, subject)

    refused = reject(client, owner_cookie, request_id, reason)

    assert code_of(refused) == INVALID
    assert [(e["path"][-1], e["code"]) for e in refused.json()["errors"]] == [
        ("reason", code)
    ]
    assert_still_pending(module_seeded_url, request_id)


def test_reject_refusals(module_seeded_url, client, family, subject, owner_cookie):
    request_id = a_request(module_seeded_url, family, subject)

    assert code_of(reject(client, owner_cookie, uuid.uuid4())) == (404, "not_found")
    assert code_of(reject(client, owner_cookie, request_id, version=3)) == (
        409,
        "version_conflict",
    )
    assert code_of(post(client, owner_cookie, path(request_id, "reject"), {})) == (
        INVALID
    )


def test_a_started_request_can_be_rejected(
    module_seeded_url, client, family, subject, owner
):
    request_id = a_request(module_seeded_url, family, subject)
    with frozen_clock(at(MONDAY, "11:30")):
        staff = sign_in(client, owner.username)
        found = ok(reject(client, staff, request_id, "Ya pasó"))

    assert found["status"] == "rejected"


def test_rejecting_frees_the_pending_cap(
    module_seeded_url, client, cookie, family, subject, tutor, owner_cookie
):
    pending = [
        a_request(
            module_seeded_url,
            family,
            subject,
            at(MONDAY + timedelta(days=7 * week), "10:00"),
        )
        for week in range(1, 6)
    ]
    sixth = request_body(family.student_id, subject)
    assert code_of(create_request(client, cookie, sixth)) == (
        409,
        "session_request_limit_reached",
    )

    ok(reject(client, owner_cookie, pending[0]))

    assert created(create_request(client, cookie, sixth))["status"] == "pending"


# --- E: the inbox can't decide; access -----------------------------------------------


def test_the_inbox_resolves_only_through_the_review_routes(
    module_seeded_url, client, family, subject, tutor, owner_cookie
):
    request_id = a_request(module_seeded_url, family, subject)
    approval = approval_of(module_seeded_url, request_id)

    for action, body in (("approve", {}), ("reject", {"reason": "No"})):
        refused = post(
            client, owner_cookie, f"{APPROVALS}/{approval.id}/{action}", body
        )
        assert code_of(refused) == (409, "session_request_review_route_required")
        assert refused.json()["details"] == {
            "review_path": f"/schedule/requests/{request_id}"
        }
    assert_still_pending(module_seeded_url, request_id)

    ok(confirm(client, owner_cookie, request_id, tutor_user_id=tutor))
    item = ok(get(client, owner_cookie, f"{APPROVALS}/{approval.id}"))
    assert item["status"] == "approved" and item["allowed_actions"] == []
    assert item["subject"]["status"] == "confirmed"
    assert item["subject"]["class_session_id"] == (
        str(request_row(module_seeded_url, request_id).class_session_id)
    )


@pytest.mark.parametrize(
    "roles",
    [(ACADEMIC_COORDINATOR,), (FINANCE_STAFF,), (TUTOR,), ("student",), ("guardian",)],
)
def test_only_owners_and_administrators_review(
    module_seeded_url, client, family, subject, tutor, roles
):
    request_id = a_request(module_seeded_url, family, subject)
    if roles == ("student",):
        account, _ = student_account(module_seeded_url)
    else:
        account = create_user(module_seeded_url, roles=roles)
    viewer = sign_in(client, account.username)

    responses = [
        review(client, viewer, request_id),
        confirm(client, viewer, request_id, tutor_user_id=tutor),
        reject(client, viewer, request_id),
    ]

    # require("schedule.manage"), as every staff route (choice, engineer D2).
    for response in responses:
        assert code_of(response) == (403, "permission_denied"), response.text
    assert_still_pending(module_seeded_url, request_id)


# --- F: who is notified [E4] ---------------------------------------------------------


def _suspend(url: str, user_id: uuid.UUID) -> None:
    async def _change(session: AsyncSession) -> None:
        await change(session, User, user_id, state="suspended")

    in_uow(url, _change)


@pytest.mark.parametrize(
    "case", ["unlinked", "profile_inactive", "consent_revoked", "suspended"]
)
def test_a_requester_who_may_no_longer_act_is_not_notified(
    module_seeded_url, client, subject, tutor, owner_cookie, case
):
    if case == "consent_revoked":
        family = guardian_with_child(module_seeded_url, adult=True, consent=True)
    else:
        family = guardian_with_child(module_seeded_url)
    confirmed = a_request(module_seeded_url, family, subject)
    rejected = a_request(
        module_seeded_url, family, subject, at(MONDAY + timedelta(days=7), "10:00")
    )
    if case == "unlinked":
        unlink(module_seeded_url, family)
    elif case == "profile_inactive":
        deactivate_guardian(module_seeded_url, family.guardian_id)
    elif case == "consent_revoked":
        revoke_academic_consent(module_seeded_url, family)
    else:
        _suspend(module_seeded_url, family.user.id)

    ok(confirm(client, owner_cookie, confirmed, tutor_user_id=tutor))
    ok(reject(client, owner_cookie, rejected))

    assert decision_notes(module_seeded_url, family.user.id) == []
    assert request_row(module_seeded_url, confirmed).status == "confirmed"
    assert request_row(module_seeded_url, rejected).status == "rejected"


def test_a_student_requester_is_notified_and_nobody_else(
    module_seeded_url, client, subject, tutor, owner_cookie
):
    account, student_id = student_account(module_seeded_url)
    guardian = guardian_with_child(module_seeded_url)
    request_id = insert_request(
        module_seeded_url,
        student_id=student_id,
        subject_id=subject,
        requested_by=account.id,
        starts_at=at(MONDAY, "10:00"),
    )

    ok(reject(client, owner_cookie, request_id))

    [note] = decision_notes(module_seeded_url, account.id)
    assert note.notification_type == REJECTED_TYPE
    assert decision_notes(module_seeded_url, guardian.user.id) == []


# --- G: what the family sees [E5] ----------------------------------------------------


def test_the_family_sees_the_outcome_and_nothing_about_the_session(
    module_seeded_url, client, cookie, family, subject, owner_cookie
):
    secret_name = "Tutora Muy Secreta"
    tutor = new_tutor(
        module_seeded_url,
        qualified_for=[subject],
        windows=MONDAY_WINDOW,
        display_name=secret_name,
    )
    room = new_room(module_seeded_url)
    confirmed = a_request(module_seeded_url, family, subject)
    rejected = a_request(
        module_seeded_url, family, subject, at(MONDAY + timedelta(days=7), "11:00")
    )
    body = ok(
        confirm(client, owner_cookie, confirmed, tutor_user_id=tutor, classroom_id=room)
    )
    ok(reject(client, owner_cookie, rejected, "No hay salón"))
    session = body["class_session"]
    secrets = [
        str(tutor),
        secret_name,
        session["id"],
        str(room),
        session["pending_offer"]["id"],
    ]

    responses = [
        get(client, cookie, REQUESTS),
        get(client, cookie, f"{REQUESTS}/{confirmed}"),
        get(client, cookie, f"{REQUESTS}/{rejected}"),
        withdraw(client, cookie, confirmed, 2),
        get(client, cookie, "/api/v1/notifications"),
    ]

    confirmed_detail = ok(responses[1])
    rejected_detail = ok(responses[2])
    assert confirmed_detail["status"] == "confirmed"
    assert confirmed_detail["rejection_reason"] is None
    assert confirmed_detail["can_withdraw"] is False
    assert rejected_detail["status"] == "rejected"
    assert rejected_detail["rejection_reason"] == "No hay salón"
    assert rejected_detail["can_withdraw"] is False
    assert code_of(responses[3]) == (409, "session_request_not_pending")
    assert "details" not in responses[3].json()
    for response in responses:
        for secret in secrets:
            assert secret not in response.text, (secret, response.text)
    for note in notifications_of(module_seeded_url, family.user.id):
        for secret in [*secrets, "No hay salón"]:
            assert secret not in str(note.params)
