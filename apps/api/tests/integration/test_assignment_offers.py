"""Assignment offers (#52 C, D, E1, E2, F, G; I1) with real logins through the
real app.

The module freezes the clock at :data:`T0` (Wednesday 2031-01-01, 06:00 in
America/Mexico_City), so every deadline is exact; a test that moves time
nests its own frozen clock and signs in again under it. Sessions are on
Monday 2031-01-06 (more than 48 hours ahead, so the default deadline is
``T0 + 48 h``). Each test makes its own students, subjects, tutors and rooms
(``test_class_session_create`` helpers); closures are global, so the closure
test takes its own year.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.offer_flow import lock_sessions
from app.modules.staff.models import TutorAvailabilityWindow, TutorQualification
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post, unique_year
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.test_class_session_create import (
    ALL_DAY,
    MONDAY,
    MX,
    at,
    body,
    close_center,
    existing_session,
    new_room,
    new_student,
    new_subject,
)
from tests.integration.test_class_session_warnings import set_limits

SESSIONS = "/api/v1/class-sessions"
OFFERS = "/api/v1/assignment-offers"
APPROVALS = "/api/v1/approval-requests"
T0 = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)
DEFAULT_RESPOND_BY = T0 + timedelta(hours=48)
REASSIGNMENT = "scheduling.offer_reassignment"

CREATED = (201, None)
OK = (200, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
CONFLICT = (409, "scheduling_conflict")


@pytest.fixture(scope="module", autouse=True)
def stopped_clock() -> Any:
    with frozen_clock(T0) as frozen:
        yield frozen


# --- Data ----------------------------------------------------------------------------


def new_tutor_user(
    url: str,
    *,
    qualified_for: tuple[uuid.UUID, ...] | list[uuid.UUID] = (),
    **overrides: Any,
) -> CreatedUser:
    """A tutor who can sign in, available all day every day, with approved
    qualifications."""
    tutor = create_user(url, roles=(TUTOR,), **overrides)

    async def _make(session: AsyncSession) -> None:
        session.add_all(
            TutorAvailabilityWindow(
                tutor_user_id=tutor.id, weekday=day, starts_at=start, ends_at=end
            )
            for day, start, end in ALL_DAY
        )
        session.add_all(
            TutorQualification(
                tutor_user_id=tutor.id,
                subject_id=subject_id,
                status="approved",
                decided_at=datetime(2026, 1, 5, tzinfo=MX),
                decided_by_user_id=tutor.id,
            )
            for subject_id in qualified_for
        )
        await session.flush()

    in_uow(url, _make)
    return tutor


@dataclass(frozen=True)
class Setup:
    student: uuid.UUID
    subject: uuid.UUID
    tutor: CreatedUser
    room: uuid.UUID


@pytest.fixture
def setup(module_seeded_url: str) -> Setup:
    subject = new_subject(module_seeded_url)
    return Setup(
        student=new_student(module_seeded_url),
        subject=subject,
        tutor=new_tutor_user(module_seeded_url, qualified_for=[subject]),
        room=new_room(module_seeded_url),
    )


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@dataclass(frozen=True)
class Staff:
    owner: CreatedUser
    admin: CreatedUser
    coordinator: CreatedUser
    finance: CreatedUser
    suspended_admin: CreatedUser


@pytest.fixture(scope="module")
def staff(module_seeded_url: str) -> Staff:
    url = module_seeded_url
    return Staff(
        owner=create_user(url, roles=(OWNER,)),
        admin=create_user(url, roles=(ADMINISTRATOR,)),
        coordinator=create_user(url, roles=(ACADEMIC_COORDINATOR,)),
        finance=create_user(url, roles=(FINANCE_STAFF,)),
        suspended_admin=create_user(url, roles=(ADMINISTRATOR,), state="suspended"),
    )


@pytest.fixture(scope="module")
def owner(client: TestClient, staff: Staff) -> Any:
    return sign_in(client, staff.owner.username)


@pytest.fixture(scope="module")
def coordinator(client: TestClient, staff: Staff) -> Any:
    return sign_in(client, staff.coordinator.username)


def payload(
    setup: Setup,
    *,
    starts_at: datetime | None = None,
    minutes: int = 60,
    tutor: uuid.UUID | None | str = "default",
    student: uuid.UUID | None = None,
    **fields: Any,
) -> dict[str, Any]:
    start = starts_at or at(MONDAY, "10:00")
    made = body(
        setup,
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
        **fields,
    )
    made["student_id"] = str(student or setup.student)
    if tutor != "default":
        made["tutor_user_id"] = None if tutor is None else str(tutor)
    else:
        made["tutor_user_id"] = str(setup.tutor.id)
    return made


def create(client: TestClient, cookie: Any, data: dict[str, Any]) -> dict[str, Any]:
    response = post(client, cookie, SESSIONS, data)
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def offered(client: TestClient, owner: Any, setup: Setup, **kwargs: Any) -> Any:
    """A session created ``offered`` to the setup's tutor; ``(detail,
    offer_id)``."""
    created = create(client, owner, payload(setup, **kwargs))
    assert created["state"] == "offered"
    return created, created["pending_offer"]["id"]


def unassigned(client: TestClient, owner: Any, setup: Setup, **kwargs: Any) -> Any:
    created = create(client, owner, payload(setup, tutor=None, **kwargs))
    assert created["state"] == "unassigned"
    return created


def answer(
    client: TestClient, cookie: Any, offer_id: Any, action: str, data: Any = None
) -> Response:
    client.cookies.clear()
    return post(client, cookie, f"{OFFERS}/{offer_id}/{action}", data)


def offer_again(
    client: TestClient, cookie: Any, session_id: Any, **data: Any
) -> Response:
    return post(client, cookie, f"{SESSIONS}/{session_id}/offers", data)


def detail(client: TestClient, cookie: Any, session_id: Any) -> dict[str, Any]:
    response = get(client, cookie, f"{SESSIONS}/{session_id}")
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def recipients(url: str, notification_type: str, offer_id: Any) -> set[uuid.UUID]:
    rows = run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = :t AND params->>'offer_id' = :id",
        {"t": notification_type, "id": str(offer_id)},
    )
    return {row.recipient_user_id for row in rows}


def notification_params(
    url: str, notification_type: str, offer_id: Any
) -> list[dict[str, Any]]:
    rows = run_sql(
        url,
        "SELECT params FROM notifications"
        " WHERE notification_type = :t AND params->>'offer_id' = :id",
        {"t": notification_type, "id": str(offer_id)},
    )
    return [dict(row.params) for row in rows]


def reassignment_items(url: str, offer_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, status, priority, requester_user_id, resolved_by_user_id"
        " FROM approval_requests WHERE request_type = :t AND subject_id = :s",
        {"t": REASSIGNMENT, "s": str(offer_id)},
    )


def assert_invariant(url: str) -> None:
    """A3: a session is offered iff exactly one pending offer covers it, made
    to the session's tutor."""
    rows = run_sql(
        url,
        "SELECT s.id, s.state, s.tutor_user_id,"
        " count(o.id) FILTER (WHERE o.status = 'pending') AS pending,"
        " bool_and(o.tutor_user_id = s.tutor_user_id)"
        "   FILTER (WHERE o.status = 'pending') AS same_tutor"
        " FROM class_sessions s"
        " LEFT JOIN assignment_offer_sessions x ON x.class_session_id = s.id"
        " LEFT JOIN assignment_offers o ON o.id = x.offer_id"
        " GROUP BY s.id",
    )
    for row in rows:
        if row.state == "offered":
            # Sessions inserted directly by other helpers have no offer.
            assert row.pending in (0, 1), row
            if row.pending == 1:
                assert row.same_tutor, row
        else:
            assert row.pending == 0, row


# --- C1: an offer at creation --------------------------------------------------------


def test_create_with_a_tutor_makes_a_pending_offer_and_notifies_the_tutor(
    module_seeded_url, client, owner, setup, staff
):
    created, offer_id = offered(client, owner, setup)

    assert created["pending_offer"] == {
        "id": offer_id,
        "tutor": {
            "id": str(setup.tutor.id),
            "display_name": created["tutor"]["display_name"],
        },
        "respond_by": DEFAULT_RESPOND_BY.isoformat().replace("+00:00", "Z"),
    }
    assert (
        detail(client, owner, created["id"])["pending_offer"]
        == created["pending_offer"]
    )
    [row] = run_sql(
        module_seeded_url,
        "SELECT * FROM assignment_offers WHERE id = :id",
        {"id": offer_id},
    )
    assert (row.scope, row.status, row.tutor_user_id, row.offered_by_user_id) == (
        "session",
        "pending",
        setup.tutor.id,
        staff.owner.id,
    )
    assert row.offered_at == T0
    assert row.respond_by == DEFAULT_RESPOND_BY
    assert row.responded_at is None
    assert recipients(module_seeded_url, "scheduling.offer_received", offer_id) == {
        setup.tutor.id
    }
    [params] = notification_params(
        module_seeded_url, "scheduling.offer_received", offer_id
    )
    assert params == {
        "offer_id": offer_id,
        "subject_name": created["subject"]["name"],
        "starts_at": "2031-01-06T16:00:00Z",
        "respond_by": "2031-01-03T12:00:00Z",
    }
    # Audit: the offer and its session row were created by the Owner.
    [offer_event] = events(
        module_seeded_url,
        record_type="assignment_offers",
        record_id=uuid.UUID(offer_id),
    )
    assert (offer_event.action, offer_event.actor_user_id) == ("create", staff.owner.id)
    assert_invariant(module_seeded_url)


def test_a_session_starting_soon_has_its_start_as_deadline(
    module_seeded_url, client, owner, setup
):
    start = at(date(2031, 1, 1), "16:00")  # T0 + 10 h
    created, offer_id = offered(
        client,
        owner,
        setup,
        starts_at=start,
        override={"codes": ["notice_window"], "reason": "Urgente"},
    )

    assert datetime.fromisoformat(created["pending_offer"]["respond_by"]) == start


def test_an_explicit_earlier_deadline_is_kept(module_seeded_url, client, owner, setup):
    created, _ = offered(client, owner, setup, respond_by="2031-01-02T09:30:45-06:00")

    assert created["pending_offer"]["respond_by"] == "2031-01-02T15:30:00Z"


@pytest.mark.parametrize(
    ("respond_by", "tutor", "code"),
    [
        ("2031-01-03T12:01:00Z", "default", "respond_by_invalid"),
        ("2031-01-01T12:00:00Z", "default", "respond_by_invalid"),
        ("2030-12-31T12:00:00Z", "default", "respond_by_invalid"),
        ("2031-01-02T12:00:00Z", None, "respond_by_requires_tutor"),
    ],
)
def test_invalid_deadlines_are_422(
    module_seeded_url, client, owner, setup, respond_by, tutor, code
):
    response = post(
        client, owner, SESSIONS, payload(setup, tutor=tutor, respond_by=respond_by)
    )

    assert code_of(response) == INVALID, response.text
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", "respond_by"], code)
    ]


def test_a_started_session_cannot_be_created_with_a_tutor(
    module_seeded_url, client, owner, setup
):
    start = at(date(2031, 1, 1), "05:00")  # an hour before T0
    response = post(
        client,
        owner,
        SESSIONS,
        payload(
            setup,
            starts_at=start,
            override={
                "codes": ["notice_window", "outside_operating_hours"],
                "reason": "Registro tardío",
            },
        ),
    )

    assert code_of(response) == (409, "class_session_not_offerable"), response.text
    assert response.json()["details"] == {"reason": "started"}


def test_the_policy_in_force_at_offered_at_gives_the_response_time(
    module_seeded_url, client, owner, setup
):
    run_sql(
        module_seeded_url,
        "INSERT INTO scheduling_policies (effective_from, booking_notice_hours,"
        " change_notice_hours, response_hours, makeup_after_on_time,"
        " makeup_after_late, makeup_after_no_show, makeup_window_days)"
        " VALUES (:at, 24, 24, 12, true, false, false, 30)",
        {"at": T0 - timedelta(days=1)},
    )
    try:
        created, _ = offered(client, owner, setup)
    finally:
        run_sql(
            module_seeded_url,
            "DELETE FROM scheduling_policies WHERE effective_from = :at",
            {"at": T0 - timedelta(days=1)},
        )

    respond_by = datetime.fromisoformat(created["pending_offer"]["respond_by"])
    assert respond_by == T0 + timedelta(hours=12)


# --- D: accept and decline -----------------------------------------------------------


def test_accept_confirms_the_session_for_the_tutor(
    module_seeded_url, client, owner, setup
):
    created, offer_id = offered(client, owner, setup)
    tutor = sign_in(client, setup.tutor.username)

    response = answer(client, tutor, offer_id, "accept")

    assert code_of(response) == OK, response.text
    item = response.json()
    assert item["status"] == "accepted"
    assert item["responded_at"] == "2031-01-01T12:00:00Z"
    assert item["decline_note"] is None
    assert [s["state"] for s in item["sessions"]] == ["confirmed"]
    session = detail(client, owner, created["id"])
    assert (session["state"], session["tutor"]["id"], session["pending_offer"]) == (
        "confirmed",
        str(setup.tutor.id),
        None,
    )
    # The session holds the tutor: another overlapping session can't be
    # offered to them.
    other = unassigned(
        client, owner, setup, student=new_student(module_seeded_url), classroom_id=None
    )
    refused = offer_again(
        client, owner, other["id"], tutor_user_id=str(setup.tutor.id), version=1
    )
    assert [c["code"] for c in refused.json()["conflicts"]] == ["tutor_overlap"]
    [event] = [
        e
        for e in events(
            module_seeded_url,
            record_type="assignment_offers",
            record_id=uuid.UUID(offer_id),
        )
        if e.action == "update"
    ]
    assert event.actor_user_id == setup.tutor.id
    assert_invariant(module_seeded_url)


def test_decline_returns_the_session_and_asks_staff_to_reassign_it(
    module_seeded_url, client, owner, setup, staff
):
    created, offer_id = offered(client, owner, setup)
    tutor = sign_in(client, setup.tutor.username)

    response = answer(client, tutor, offer_id, "decline", {"note": "  Viajo ese día  "})

    assert code_of(response) == OK, response.text
    item = response.json()
    assert (item["status"], item["decline_note"]) == ("declined", "Viajo ese día")
    session = detail(client, owner, created["id"])
    assert (session["state"], session["tutor"], session["pending_offer"]) == (
        "unassigned",
        None,
        None,
    )
    # The room and the student are still held.
    clash = post(
        client,
        owner,
        SESSIONS,
        payload(setup, tutor=None, student=new_student(module_seeded_url)),
    )
    assert [c["code"] for c in clash.json()["conflicts"]] == ["room_overlap"]
    # One reassignment item, no requester; normal priority (5 days ahead).
    [inbox] = reassignment_items(module_seeded_url, offer_id)
    assert (inbox.status, inbox.priority, inbox.requester_user_id) == (
        "pending",
        "normal",
        None,
    )
    # The holders of schedule.assign are told, never Finance, the tutor or a
    # suspended administrator.
    told = recipients(module_seeded_url, "scheduling.offer_declined", offer_id)
    assert {staff.owner.id, staff.admin.id, staff.coordinator.id} <= told
    assert not {staff.finance.id, staff.suspended_admin.id, setup.tutor.id} & told
    expected = {
        row.id
        for row in run_sql(
            module_seeded_url,
            "SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id"
            " JOIN roles r ON r.id = ur.role_id WHERE u.state = 'active'"
            " AND r.code IN ('owner', 'administrator', 'academic_coordinator')",
        )
    }
    assert told == expected
    # The note is in the history and the inbox, never in the notification.
    sent = notification_params(module_seeded_url, "scheduling.offer_declined", offer_id)
    assert len(sent) == len(told)
    for params in sent:
        assert params == {
            "offer_id": offer_id,
            "class_session_id": created["id"],
            "subject_name": created["subject"]["name"],
            "starts_at": "2031-01-06T16:00:00Z",
            "tutor_name": created["tutor"]["display_name"],
        }
    history = get(client, owner, f"{SESSIONS}/{created['id']}/offers").json()
    assert [(o["status"], o["decline_note"]) for o in history["items"]] == [
        ("declined", "Viajo ese día")
    ]
    coordinator = sign_in(client, staff.coordinator.username)
    shown = get(client, coordinator, f"{APPROVALS}/{inbox.id}").json()
    assert shown["subject"] == {
        "class_session_id": created["id"],
        "subject_name": created["subject"]["name"],
        "starts_at": "2031-01-06T16:00:00Z",
        "tutor_name": created["tutor"]["display_name"],
        "outcome": "declined",
        "decline_note": "Viajo ese día",
        "session_state": "unassigned",
    }
    assert_invariant(module_seeded_url)


def test_a_decline_without_a_body_or_with_a_blank_note_has_no_note(
    module_seeded_url, client, owner, setup
):
    _, first = offered(client, owner, setup)
    _, second = offered(
        client,
        owner,
        setup,
        starts_at=at(MONDAY, "12:00"),
        student=new_student(module_seeded_url),
    )
    tutor = sign_in(client, setup.tutor.username)

    assert answer(client, tutor, first, "decline").json()["decline_note"] is None
    assert (
        answer(client, tutor, second, "decline", {"note": "   "}).json()["decline_note"]
        is None
    )


def test_a_long_note_is_422_and_nothing_changes(
    module_seeded_url, client, owner, setup
):
    created, offer_id = offered(client, owner, setup)
    tutor = sign_in(client, setup.tutor.username)

    response = answer(client, tutor, offer_id, "decline", {"note": "x" * 501})

    assert code_of(response) == INVALID
    assert response.json()["errors"][0]["code"] == "decline_note_too_long"
    assert detail(client, owner, created["id"])["state"] == "offered"


def test_a_decline_within_24_hours_is_high_priority(
    module_seeded_url, client, owner, setup
):
    _, offer_id = offered(
        client,
        owner,
        setup,
        starts_at=at(date(2031, 1, 1), "16:00"),
        override={"codes": ["notice_window"], "reason": "Urgente"},
    )
    tutor = sign_in(client, setup.tutor.username)

    answer(client, tutor, offer_id, "decline")

    [inbox] = reassignment_items(module_seeded_url, offer_id)
    assert inbox.priority == "high"


def test_only_the_offered_tutor_can_answer(
    module_seeded_url, client, owner, setup, staff
):
    created, offer_id = offered(client, owner, setup)
    others = [
        new_tutor_user(module_seeded_url),
        staff.coordinator,
        staff.owner,
        create_user(module_seeded_url, roles=(STUDENT,)),
        create_user(module_seeded_url, roles=(GUARDIAN,)),
    ]

    for user in others:
        cookie = sign_in(client, user.username)
        for action in ("accept", "decline"):
            assert code_of(answer(client, cookie, offer_id, action)) == NOT_FOUND
        listed = get(client, cookie, OFFERS, status="all").json()
        assert offer_id not in [item["id"] for item in listed["items"]]

    assert detail(client, owner, created["id"])["state"] == "offered"
    [row] = run_sql(
        module_seeded_url,
        "SELECT status FROM assignment_offers WHERE id = :id",
        {"id": offer_id},
    )
    assert row.status == "pending"
    unknown = sign_in(client, setup.tutor.username)
    assert code_of(answer(client, unknown, uuid.uuid4(), "accept")) == NOT_FOUND


def test_a_second_answer_is_409(module_seeded_url, client, owner, setup):
    _, offer_id = offered(client, owner, setup)
    tutor = sign_in(client, setup.tutor.username)
    assert code_of(answer(client, tutor, offer_id, "accept")) == OK

    for action in ("accept", "decline"):
        response = answer(client, tutor, offer_id, action)
        assert code_of(response) == (409, "offer_not_pending")
        assert response.json()["details"] == {"status": "accepted"}


def test_an_answer_at_the_deadline_is_too_late(module_seeded_url, client, owner, setup):
    created, offer_id = offered(client, owner, setup)

    with frozen_clock(DEFAULT_RESPOND_BY):
        tutor = sign_in(client, setup.tutor.username)
        accepted = answer(client, tutor, offer_id, "accept")
        declined = answer(client, tutor, offer_id, "decline")

    assert code_of(accepted) == (409, "offer_expired")
    assert code_of(declined) == (409, "offer_expired")
    assert detail(client, owner, created["id"])["state"] == "offered"


def test_an_answer_just_before_the_deadline_is_accepted(
    module_seeded_url, client, owner, setup
):
    _, offer_id = offered(client, owner, setup)

    with frozen_clock(DEFAULT_RESPOND_BY - timedelta(seconds=1)):
        tutor = sign_in(client, setup.tutor.username)
        response = answer(client, tutor, offer_id, "accept")

    assert code_of(response) == OK


# --- G2: the tutor's list ------------------------------------------------------------


def test_my_offers_lists_only_my_offers_by_tab(module_seeded_url, client, owner, setup):
    _, late = offered(client, owner, setup, starts_at=at(MONDAY, "15:00"))
    _, soon = offered(
        client,
        owner,
        setup,
        starts_at=at(MONDAY, "12:00"),
        student=new_student(module_seeded_url),
        respond_by="2031-01-02T12:00:00Z",
    )
    _, answered = offered(
        client,
        owner,
        setup,
        starts_at=at(MONDAY, "18:00"),
        student=new_student(module_seeded_url),
    )
    tutor = sign_in(client, setup.tutor.username)
    assert code_of(answer(client, tutor, answered, "decline")) == OK

    pending = get(client, tutor, OFFERS).json()
    assert [item["id"] for item in pending["items"]] == [soon, late]
    assert pending["total"] == 2
    assert (pending["limit"], pending["offset"]) == (20, 0)
    item = pending["items"][0]
    assert item["sessions"][0]["subject"]["education_level_name"]
    assert item["sessions"][0]["classroom"]["name"]
    assert len(item["sessions"][0]["participants"]) == 1
    assert item["tutor"]["id"] == str(setup.tutor.id)
    assert get(client, tutor, OFFERS, status="answered").json()["total"] == 1
    every = get(client, tutor, OFFERS, status="all", limit=1, offset=1).json()
    assert (every["total"], len(every["items"])) == (3, 1)
    past = get(client, tutor, OFFERS, status="all", offset=10).json()
    assert (past["total"], past["items"]) == (3, [])
    assert code_of(get(client, tutor, OFFERS, status="open")) == INVALID


def test_a_page_of_offers_takes_a_fixed_number_of_statements(
    module_seeded_url, client, owner, setup
):
    for hour in ("10:00", "12:00", "14:00"):
        offered(
            client,
            owner,
            setup,
            starts_at=at(MONDAY, hour),
            student=new_student(module_seeded_url),
        )
    count, page = count_statements(
        module_seeded_url,
        lambda session: scheduling.list_my_offers(
            session, caller_id=setup.tutor.id, status="all", limit=20, offset=0
        ),
    )

    assert len(page.items) == 3
    assert count <= 6


# --- C2: offering an unassigned session again ----------------------------------------


def declined_session(
    module_seeded_url: str, client: TestClient, owner: Any, setup: Setup, **kwargs: Any
) -> tuple[dict[str, Any], str]:
    created, offer_id = offered(client, owner, setup, **kwargs)
    tutor = sign_in(client, setup.tutor.username)
    assert code_of(answer(client, tutor, offer_id, "decline")) == OK
    return detail(client, owner, created["id"]), offer_id


def test_offering_again_closes_the_reassignment_item(
    module_seeded_url, client, owner, setup, staff, coordinator
):
    session, first = declined_session(module_seeded_url, client, owner, setup)
    other = new_tutor_user(module_seeded_url, qualified_for=[setup.subject])

    response = offer_again(
        client,
        coordinator,
        session["id"],
        tutor_user_id=str(other.id),
        version=session["version"],
    )

    assert code_of(response) == CREATED, response.text
    created = response.json()
    assert created["warnings"] == []
    offer = created["offer"]
    assert (offer["status"], offer["tutor"]["id"], offer["offered_by"]["id"]) == (
        "pending",
        str(other.id),
        str(staff.coordinator.id),
    )
    assert offer["respond_by"] == "2031-01-03T12:00:00Z"
    after = detail(client, owner, session["id"])
    assert (after["state"], after["tutor"]["id"]) == ("offered", str(other.id))
    assert after["pending_offer"]["id"] == offer["id"]
    [inbox] = reassignment_items(module_seeded_url, first)
    assert (inbox.status, inbox.resolved_by_user_id) == (
        "cancelled",
        staff.coordinator.id,
    )
    assert recipients(module_seeded_url, "scheduling.offer_received", offer["id"]) == {
        other.id
    }
    history = get(client, owner, f"{SESSIONS}/{session['id']}/offers").json()
    assert [o["status"] for o in history["items"]] == ["pending", "declined"]
    assert_invariant(module_seeded_url)


def test_each_refusal_of_an_offer_again(module_seeded_url, client, owner, setup):
    session, _ = declined_session(module_seeded_url, client, owner, setup)
    tutor = str(setup.tutor.id)

    def again(session_id: Any = session["id"], **data: Any) -> Response:
        return offer_again(
            client,
            owner,
            session_id,
            **({"tutor_user_id": tutor, "version": session["version"]} | data),
        )

    assert code_of(again(uuid.uuid4())) == NOT_FOUND
    assert code_of(again(version=session["version"] - 1)) == (409, "version_conflict")
    unknown = again(tutor_user_id=str(uuid.uuid4()))
    assert code_of(unknown) == INVALID
    assert [e["code"] for e in unknown.json()["errors"]] == ["tutor_unknown"]
    pending = new_tutor_user(module_seeded_url, state="pending")
    both = again(tutor_user_id=str(pending.id), respond_by="2031-01-05T00:00:00Z")
    assert [(e["path"][-1], e["code"]) for e in both.json()["errors"]] == [
        ("tutor_user_id", "tutor_inactive"),
        ("respond_by", "respond_by_invalid"),
    ]
    assert code_of(again(state="offered")) == INVALID  # extra=forbid
    confirmed = existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "17:00"),
        tutor_user_id=new_tutor_user(module_seeded_url).id,
    )
    not_offerable = again(confirmed, version=1)
    assert code_of(not_offerable) == (409, "class_session_not_offerable")
    assert not_offerable.json()["details"] == {"state": "confirmed"}
    started = existing_session(
        module_seeded_url, starts_at=T0 - timedelta(minutes=30), state="unassigned"
    )
    begun = again(started, version=1)
    assert code_of(begun) == (409, "class_session_not_offerable")
    assert begun.json()["details"] == {"reason": "started"}
    assert detail(client, owner, session["id"])["state"] == "unassigned"


def test_an_unqualified_tutor_needs_an_override(
    module_seeded_url, client, owner, coordinator, setup
):
    session, _ = declined_session(module_seeded_url, client, owner, setup)
    unqualified = new_tutor_user(module_seeded_url)
    data = {"tutor_user_id": str(unqualified.id), "version": session["version"]}
    override = {"codes": ["tutor_not_qualified"], "reason": "Suplente aprobado"}

    refused = offer_again(client, owner, session["id"], **data)
    assert [(c["code"], c["severity"]) for c in refused.json()["conflicts"]] == [
        ("tutor_not_qualified", "soft")
    ]
    assert code_of(
        offer_again(client, coordinator, session["id"], **data, override=override)
    ) == (403, "permission_denied")
    allowed = offer_again(client, owner, session["id"], **data, override=override)

    assert code_of(allowed) == CREATED, allowed.text
    overrides = detail(client, owner, session["id"])["overrides"]
    assert [o["codes"] for o in overrides] == [["tutor_not_qualified"]]


def test_a_closure_added_after_creation_is_reported(
    module_seeded_url, client, owner, setup
):
    year = unique_year()
    monday = date(year, 1, 1) + timedelta(days=(7 - date(year, 1, 1).weekday()) % 7)
    session, _ = declined_session(
        module_seeded_url, client, owner, setup, starts_at=at(monday, "10:00")
    )
    close_center(module_seeded_url, monday)

    response = offer_again(
        client,
        owner,
        session["id"],
        tutor_user_id=str(setup.tutor.id),
        version=session["version"],
    )

    assert [c["code"] for c in response.json()["conflicts"]] == ["center_closed"]


def test_the_booking_notice_is_not_checked_again(
    module_seeded_url, client, owner, setup, staff
):
    start = at(date(2031, 1, 1), "10:00")  # T0 + 4 h
    session = unassigned(
        client,
        owner,
        setup,
        starts_at=start,
        override={"codes": ["notice_window"], "reason": "Urgente"},
    )

    with frozen_clock(start - timedelta(hours=2)):
        cookie = sign_in(client, staff.owner.username)
        response = offer_again(
            client,
            cookie,
            session["id"],
            tutor_user_id=str(setup.tutor.id),
            version=session["version"],
        )

    assert code_of(response) == CREATED, response.text
    assert datetime.fromisoformat(response.json()["offer"]["respond_by"]) == start


def test_warnings_come_with_the_offer(module_seeded_url, client, owner, setup):
    session = unassigned(client, owner, setup)
    existing_session(
        module_seeded_url,
        starts_at=at(MONDAY, "08:00"),
        tutor_user_id=setup.tutor.id,
    )
    set_limits(client, owner, setup.tutor.id, daily=60)

    response = offer_again(
        client,
        owner,
        session["id"],
        tutor_user_id=str(setup.tutor.id),
        version=session["version"],
    )

    assert code_of(response) == CREATED, response.text
    assert [w["code"] for w in response.json()["warnings"]] == ["daily_limit_exceeded"]


def test_a_tutor_may_be_offered_a_session_they_declined(
    module_seeded_url, client, owner, setup
):
    session, _ = declined_session(module_seeded_url, client, owner, setup)

    response = offer_again(
        client,
        owner,
        session["id"],
        tutor_user_id=str(setup.tutor.id),
        version=session["version"],
    )

    assert code_of(response) == CREATED, response.text


# --- E2: the reassignment item -------------------------------------------------------


def test_the_reassignment_item_actions(
    module_seeded_url, client, owner, setup, staff, coordinator
):
    session, offer_id = declined_session(module_seeded_url, client, owner, setup)
    [inbox] = reassignment_items(module_seeded_url, offer_id)
    item = f"{APPROVALS}/{inbox.id}"

    still = post(client, coordinator, f"{item}/approve", None)
    assert code_of(still) == (409, "reassignment_still_needed")
    assert reassignment_items(module_seeded_url, offer_id)[0].status == "pending"
    finance = sign_in(client, staff.finance.username)
    assert code_of(get(client, finance, item)) == NOT_FOUND
    assert code_of(post(client, coordinator, f"{item}/reject", {})) == (
        422,
        "validation_error",
    )

    # Once the session no longer waits for a tutor (cancelled here, as #55
    # will), "Mark resolved" works.
    async def _cancel(db: AsyncSession) -> None:
        await scheduling.transition_class_session(
            db,
            uuid.UUID(session["id"]),
            to_state="cancelled",
            expected_version=session["version"],
            reason="Sin tutor",
        )

    in_uow(module_seeded_url, _cancel)
    resolved = post(client, coordinator, f"{item}/approve", None)
    assert code_of(resolved) == OK, resolved.text
    assert reassignment_items(module_seeded_url, offer_id)[0].status == "approved"


def test_dismissing_the_item_leaves_the_session(
    module_seeded_url, client, owner, setup, coordinator
):
    session, offer_id = declined_session(module_seeded_url, client, owner, setup)
    [inbox] = reassignment_items(module_seeded_url, offer_id)

    response = post(
        client,
        coordinator,
        f"{APPROVALS}/{inbox.id}/reject",
        {"reason": "Se reprograma"},
    )

    assert code_of(response) == OK, response.text
    assert reassignment_items(module_seeded_url, offer_id)[0].status == "rejected"
    assert detail(client, owner, session["id"])["state"] == "unassigned"
    # A later offer leaves the dismissed item alone.
    again = offer_again(
        client,
        owner,
        session["id"],
        tutor_user_id=str(setup.tutor.id),
        version=session["version"],
    )
    assert code_of(again) == CREATED
    assert reassignment_items(module_seeded_url, offer_id)[0].status == "rejected"


# --- E4: withdraw seam ---------------------------------------------------------------


def test_withdraw_pending_offers(module_seeded_url, client, owner, setup):
    created, offer_id = offered(client, owner, setup)
    session_id = uuid.UUID(created["id"])

    async def _withdraw(db: AsyncSession) -> list[uuid.UUID]:
        [row] = await lock_sessions(db, [session_id])
        withdrawn = await scheduling.withdraw_pending_offers(
            db, [session_id], by_user_id=None
        )
        await scheduling.transition_class_session(
            db,
            session_id,
            to_state="cancelled",
            expected_version=row.version,
            reason="Cancelada",
        )
        return withdrawn

    assert in_uow(module_seeded_url, _withdraw) == [uuid.UUID(offer_id)]
    [row] = run_sql(
        module_seeded_url,
        "SELECT status, responded_at FROM assignment_offers WHERE id = :id",
        {"id": offer_id},
    )
    assert row.status == "withdrawn"
    assert row.responded_at == T0
    assert reassignment_items(module_seeded_url, offer_id) == []
    assert recipients(module_seeded_url, "scheduling.offer_declined", offer_id) == set()
    assert (
        in_uow(
            module_seeded_url,
            lambda db: scheduling.withdraw_pending_offers(
                db, [session_id], by_user_id=None
            ),
        )
        == []
    )
    assert_invariant(module_seeded_url)


# --- G3: the session's offers --------------------------------------------------------


def test_the_session_offers_route(module_seeded_url, client, owner, staff):
    finance = sign_in(client, staff.finance.username)

    assert (
        code_of(get(client, finance, f"{SESSIONS}/{uuid.uuid4()}/offers")) == NOT_FOUND
    )
    unoffered = existing_session(
        module_seeded_url, starts_at=at(MONDAY, "19:00"), state="unassigned"
    )
    assert get(client, finance, f"{SESSIONS}/{unoffered}/offers").json() == {
        "items": []
    }
