"""Availability changes that affect scheduled sessions (M4-18 #63 B-G, I1,
I4) with real logins through the real app and real sessions (#48 create,
#52 offers, the #47 factories).

The module freezes the clock at :data:`T0` (Wednesday 2031-01-01, 06:00 in
America/Mexico_City). Sessions are on Monday 2031-01-06 unless a test says
otherwise. Each test makes its own tutor (weekly windows Monday 08:00-20:00
and Wednesday 16:00-18:00), subject and student.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from app.modules.staff import service as staff
from app.modules.staff.models import (
    TutorAvailabilityWindow,
    TutorQualification,
)
from app.workflows.availability_change import AVAILABILITY_CHANGE_HANDLER
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import (
    count_statements,
    get,
    patch,
    post,
    window,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.test_class_session_create import (
    MONDAY,
    MX,
    at,
    existing_session,
    new_student,
    new_subject,
)
from tests.integration.test_tutor_availability import (
    add_exception,
    availability_path,
    center_zone,
    delete_exception,
    detail,
    put_weekly,
)

SESSIONS = "/api/v1/class-sessions"
OFFERS = "/api/v1/assignment-offers"
APPROVALS = "/api/v1/approval-requests"
T0 = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)
TUESDAY = MONDAY + timedelta(days=1)
TYPE = "staff.availability_change"

OK = (200, None)
CREATED = (201, None)
ACCEPTED = (202, None)
NO_CONTENT = (204, None)
DENIED = (403, "permission_denied")
NOT_FOUND = (404, "not_found")
STALE = (409, "availability_change_stale")
NOT_PENDING = (409, "availability_change_not_pending")
VERSION_CONFLICT = (409, "version_conflict")
INVALID = (422, "validation_error")

WEEKLY = [window(1, "08:00", "20:00"), window(3, "16:00", "18:00")]
WITHOUT_MONDAY_16_18 = [
    window(1, "08:00", "16:00"),
    window(1, "18:00", "20:00"),
    window(3, "16:00", "18:00"),
]


@pytest.fixture(scope="module", autouse=True)
def stopped_clock() -> Any:
    with frozen_clock(T0) as frozen:
        yield frozen


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
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
def people(module_seeded_url: str) -> Staff:
    url = module_seeded_url
    return Staff(
        owner=create_user(url, roles=(OWNER,)),
        admin=create_user(url, roles=(ADMINISTRATOR,)),
        coordinator=create_user(url, roles=(ACADEMIC_COORDINATOR,)),
        finance=create_user(url, roles=(FINANCE_STAFF,)),
        suspended_admin=create_user(url, roles=(ADMINISTRATOR,), state="suspended"),
    )


@pytest.fixture(scope="module")
def owner(client: TestClient, people: Staff) -> Any:
    return sign_in(client, people.owner.username)


@dataclass(frozen=True)
class Tutor:
    user: CreatedUser
    name: str
    cookie: Any
    subject: uuid.UUID
    student: uuid.UUID

    @property
    def id(self) -> uuid.UUID:
        return self.user.id


def make_tutor(
    url: str,
    client: TestClient,
    *,
    windows: list[tuple[int, time, time]] | None = None,
    subject: uuid.UUID | None = None,
) -> Tutor:
    """A tutor who can sign in, with weekly windows (Monday 08:00-20:00 and
    Wednesday 16:00-18:00 by default; no settings row: version 0) and an
    approved qualification for a new subject."""
    name = f"Tutora {uuid.uuid4().hex[:8]}"
    person = create_user(url, roles=(TUTOR,), display_name=name)
    subject_id = subject or new_subject(url)
    weekly = windows or [(1, time(8), time(20)), (3, time(16), time(18))]

    async def _make(session: AsyncSession) -> None:
        session.add_all(
            TutorAvailabilityWindow(
                tutor_user_id=person.id, weekday=day, starts_at=start, ends_at=end
            )
            for day, start, end in weekly
        )
        session.add(
            TutorQualification(
                tutor_user_id=person.id,
                subject_id=subject_id,
                status="approved",
                decided_at=datetime(2026, 1, 5, tzinfo=MX),
                decided_by_user_id=person.id,
            )
        )
        await session.flush()

    in_uow(url, _make)
    return Tutor(
        user=person,
        name=name,
        cookie=sign_in(client, person.username),
        subject=subject_id,
        student=new_student(url),
    )


@pytest.fixture
def tutor(module_seeded_url: str, client: TestClient) -> Tutor:
    return make_tutor(module_seeded_url, client)


# --- Sessions ------------------------------------------------------------------------


def book(
    client: TestClient,
    owner: Any,
    tutor: Tutor,
    starts_at: datetime,
    *,
    minutes: int = 60,
    override: list[str] | None = None,
    student: uuid.UUID | None = None,
) -> dict[str, Any]:
    """A one-off session offered to the tutor (``POST /class-sessions``)."""
    body: dict[str, Any] = {
        "kind": "individual",
        "student_id": str(student or tutor.student),
        "subject_id": str(tutor.subject),
        "tutor_user_id": str(tutor.id),
        "classroom_id": None,
        "starts_at": starts_at.isoformat(),
        "ends_at": (starts_at + timedelta(minutes=minutes)).isoformat(),
    }
    if override:
        body["override"] = {"codes": override, "reason": "Acordado con el tutor"}
    response = post(client, owner, SESSIONS, body)
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    assert created["state"] == "offered"
    return created


def confirmed(
    client: TestClient, owner: Any, tutor: Tutor, starts_at: datetime, **kwargs: Any
) -> dict[str, Any]:
    """A session the tutor accepted."""
    created = book(client, owner, tutor, starts_at, **kwargs)
    offer_id = created["pending_offer"]["id"]
    response = post(client, tutor.cookie, f"{OFFERS}/{offer_id}/accept", None)
    assert code_of(response) == OK, response.text
    return read(client, owner, created["id"])


def read(client: TestClient, cookie: Any, session_id: Any) -> dict[str, Any]:
    response = get(client, cookie, f"{SESSIONS}/{session_id}")
    assert code_of(response) == OK, response.text
    found: dict[str, Any] = response.json()
    return found


def cancel_session(client: TestClient, owner: Any, session_id: Any) -> None:
    current = read(client, owner, session_id)
    response = post(
        client,
        owner,
        f"{SESSIONS}/{session_id}/cancel",
        {"version": current["version"], "reason": "La familia canceló"},
    )
    assert code_of(response) == OK, response.text


def calendar_markers(client: TestClient, cookie: Any, session_id: Any) -> list[str]:
    response = get(
        client,
        cookie,
        SESSIONS,
        **{"from": "2031-01-05T00:00:00Z", "to": "2031-01-12T00:00:00Z"},
    )
    assert code_of(response) == OK, response.text
    [item] = [i for i in response.json()["items"] if i["id"] == str(session_id)]
    markers: list[str] = item["markers"]
    return markers


# --- Requests and the inbox ----------------------------------------------------------


def shrink(tutor: Tutor, client: TestClient, cookie: Any = None) -> Response:
    """Take Monday 16:00-18:00 away (the weekly ``PUT``)."""
    version = detail(client, tutor.cookie, tutor.id)["version"]
    return put_weekly(
        client, cookie or tutor.cookie, tutor.id, version, WITHOUT_MONDAY_16_18
    )


def requested(response: Response) -> dict[str, Any]:
    assert code_of(response) == ACCEPTED, response.text
    item: dict[str, Any] = response.json()
    assert item["status"] == "pending"
    return item


def inbox_item(url: str, request_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, status, priority, requester_user_id, reason"
        " FROM approval_requests WHERE request_type = :t AND subject_id = :s",
        {"t": TYPE, "s": str(request_id)},
    )
    return row


def request_row(url: str, request_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT status, resolved_at, rejection_reason, version"
        " FROM tutor_availability_change_requests WHERE id = :id",
        {"id": str(request_id)},
    )
    return row


def decide(
    client: TestClient, cookie: Any, item: dict[str, Any], action: str, **body: Any
) -> Response:
    return post(
        client,
        cookie,
        f"{APPROVALS}/{item['approval_request_id']}/{action}",
        body or None,
    )


def cancel_request(
    client: TestClient, cookie: Any, tutor: Tutor, item: dict[str, Any], version: int
) -> Response:
    return post(
        client,
        cookie,
        f"{availability_path(tutor.id)}/change-requests/{item['id']}/cancel",
        {"version": version},
    )


def notifications(url: str, notification_type: str, key: str, value: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = :t AND params->>:k = :v",
        {"t": notification_type, "k": key, "v": str(value)},
    )


def flags(url: str, session_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT tutor_user_id, reason, source_id, created_by_user_id"
        " FROM class_session_reassignment_flags WHERE class_session_id = :id",
        {"id": str(session_id)},
    )


def request_count(url: str, tutor: Tutor) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM tutor_availability_change_requests"
        " WHERE tutor_user_id = :id",
        {"id": str(tutor.id)},
    )
    return int(row.n)


def session_state(url: str, session_id: Any) -> tuple[str, Any]:
    [row] = run_sql(
        url,
        "SELECT state, tutor_user_id FROM class_sessions WHERE id = :id",
        {"id": str(session_id)},
    )
    return row.state, row.tutor_user_id


def assert_offer_invariant(url: str, session_ids: list[Any]) -> None:
    """#52 A3 for these sessions: offered iff exactly one pending offer."""
    rows = run_sql(
        url,
        "SELECT s.id, s.state, count(o.id) FILTER (WHERE o.status = 'pending')"
        " AS pending FROM class_sessions s"
        " LEFT JOIN assignment_offer_sessions x ON x.class_session_id = s.id"
        " LEFT JOIN assignment_offers o ON o.id = x.offer_id"
        " WHERE s.id = ANY(CAST(:ids AS uuid[])) GROUP BY s.id",
        {"ids": [str(i) for i in session_ids]},
    )
    for row in rows:
        assert row.pending == (1 if row.state == "offered" else 0), row


# --- B2, C: direct changes -----------------------------------------------------------


def test_a_change_with_no_session_in_the_removed_time_applies_at_once(
    module_seeded_url, client, owner, tutor
):
    confirmed(client, owner, tutor, at(MONDAY, "10:00"))

    response = shrink(tutor, client)

    assert code_of(response) == OK, response.text
    assert response.json()["weekly"] == WITHOUT_MONDAY_16_18
    assert response.json()["pending_changes"] == []
    assert request_count(module_seeded_url, tutor) == 0


def test_changes_that_add_time_apply_with_sessions_present(
    module_seeded_url, client, owner, tutor
):
    confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    off = add_exception(
        client,
        tutor.cookie,
        tutor.id,
        starts_on="2031-02-03",
        ends_on="2031-02-03",
    )
    assert code_of(off) == CREATED

    grown = put_weekly(
        client, tutor.cookie, tutor.id, 0, [*WEEKLY, window(5, "09:00", "12:00")]
    )
    extra = add_exception(
        client,
        tutor.cookie,
        tutor.id,
        kind="available",
        starts_on="2031-01-06",
        ends_on="2031-01-06",
        starts_at="20:00",
        ends_at="21:00",
    )
    removed_off = delete_exception(client, tutor.cookie, tutor.id, off.json()["id"])

    assert [code_of(r) for r in (grown, extra, removed_off)] == [
        OK,
        CREATED,
        NO_CONTENT,
    ]
    assert request_count(module_seeded_url, tutor) == 0


# --- C1, C2, G1: a weekly change that needs approval --------------------------------


def test_a_weekly_change_over_a_confirmed_session_waits_for_approval(
    module_seeded_url, client, owner, tutor
):
    session = confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    before = detail(client, tutor.cookie, tutor.id)

    item = requested(shrink(tutor, client))

    assert item["kind"] == "weekly"
    assert item["change"] == {"windows": WITHOUT_MONDAY_16_18, "exception": None}
    assert item["affected_count"] == 1
    assert item["affected_sessions"] == [
        {
            "id": session["id"],
            "starts_at": session["starts_at"],
            "ends_at": session["ends_at"],
            "subject_name": session["subject"]["name"],
            "state": "confirmed",
        }
    ]
    assert item["requested_by"] == {
        "id": str(tutor.id),
        "display_name": tutor.name,
    }
    assert item["resolved_at"] is None
    assert item["rejection_reason"] is None
    assert item["version"] == 1
    # Nothing changed; the request is listed with the availability.
    after = detail(client, tutor.cookie, tutor.id)
    assert after["weekly"] == before["weekly"] == WEEKLY
    assert after["version"] == before["version"] == 0
    assert after["pending_changes"] == [item]
    assert after["recent_changes"] == []
    inbox = inbox_item(module_seeded_url, item["id"])
    assert str(inbox.id) == item["approval_request_id"]
    assert (inbox.status, inbox.priority, inbox.requester_user_id) == (
        "pending",
        "normal",
        tutor.id,
    )


def test_a_session_within_24_hours_makes_the_request_high_priority(
    module_seeded_url, client, owner, tutor
):
    # T0 is Wednesday 06:00: a Wednesday 16:00 session needs a notice override.
    book(
        client, owner, tutor, at(date(2031, 1, 1), "16:00"), override=["notice_window"]
    )

    item = requested(
        put_weekly(client, tutor.cookie, tutor.id, 0, [window(1, "08:00", "20:00")])
    )

    assert inbox_item(module_seeded_url, item["id"]).priority == "high"


def test_several_requests_may_wait_and_a_free_change_still_applies(
    module_seeded_url, client, owner, tutor
):
    confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    book(client, owner, tutor, at(date(2031, 1, 8), "16:00"))

    first = requested(shrink(tutor, client))
    second = requested(
        add_exception(
            client, tutor.cookie, tutor.id, starts_on="2031-01-08", ends_on="2031-01-08"
        )
    )
    free = add_exception(
        client, tutor.cookie, tutor.id, starts_on="2031-02-10", ends_on="2031-02-10"
    )

    assert code_of(free) == CREATED
    pending = detail(client, tutor.cookie, tutor.id)["pending_changes"]
    assert [p["id"] for p in pending] == [first["id"], second["id"]]


# --- C1: exceptions ------------------------------------------------------------------


def test_time_off_over_an_offered_session_waits_for_approval(
    module_seeded_url, client, owner, tutor
):
    session = book(client, owner, tutor, at(MONDAY, "16:00"))

    item = requested(
        add_exception(
            client,
            tutor.cookie,
            tutor.id,
            starts_on="2031-01-06",
            ends_on="2031-01-07",
            note="Cita médica",
        )
    )

    assert item["kind"] == "exception_create"
    assert item["change"] == {
        "windows": None,
        "exception": {
            "id": None,
            "kind": "unavailable",
            "starts_on": "2031-01-06",
            "ends_on": "2031-01-07",
            "starts_at": None,
            "ends_at": None,
            "note": "Cita médica",
        },
    }
    assert [s["id"] for s in item["affected_sessions"]] == [session["id"]]
    assert [s["state"] for s in item["affected_sessions"]] == ["offered"]
    assert detail(client, tutor.cookie, tutor.id)["exceptions"] == []


def test_time_off_that_only_touches_a_session_applies(
    module_seeded_url, client, owner, tutor
):
    book(client, owner, tutor, at(MONDAY, "16:00"))

    response = add_exception(
        client,
        tutor.cookie,
        tutor.id,
        starts_on="2031-01-06",
        ends_on="2031-01-06",
        starts_at="15:00",
        ends_at="16:00",
    )

    assert code_of(response) == CREATED
    assert request_count(module_seeded_url, tutor) == 0


def test_deleting_extra_hours_a_session_uses_waits_for_approval(
    module_seeded_url, client, owner, tutor
):
    extra = add_exception(
        client,
        tutor.cookie,
        tutor.id,
        kind="available",
        starts_on="2031-01-07",
        ends_on="2031-01-07",
        starts_at="10:00",
        ends_at="12:00",
        note="Horas extra",
    ).json()
    session = book(client, owner, tutor, at(TUESDAY, "10:00"))

    item = requested(delete_exception(client, tutor.cookie, tutor.id, extra["id"]))

    assert item["kind"] == "exception_delete"
    assert item["change"]["exception"] == {
        "id": extra["id"],
        "kind": "available",
        "starts_on": "2031-01-07",
        "ends_on": "2031-01-07",
        "starts_at": "10:00",
        "ends_at": "12:00",
        "note": "Horas extra",
    }
    assert [s["id"] for s in item["affected_sessions"]] == [session["id"]]
    assert [e["id"] for e in detail(client, tutor.cookie, tutor.id)["exceptions"]] == [
        extra["id"]
    ]


# --- B1: sessions that are never affected --------------------------------------------


def test_sessions_that_a_change_never_affects(module_seeded_url, client, owner):
    url = module_seeded_url
    every_day = [(day, time(0), time(23, 59)) for day in range(1, 8)]
    tutor = make_tutor(url, client, windows=every_day)
    other = make_tutor(url, client, windows=every_day, subject=tutor.subject)
    wednesday = date(2031, 1, 1)

    def stored(start: datetime, state: str, **extra: Any) -> uuid.UUID:
        tutor_id = None if state == "unassigned" else tutor.id
        return existing_session(
            url,
            starts_at=start,
            state=state,
            subject_id=tutor.subject,
            tutor_user_id=extra.pop("tutor_user_id", tutor_id),
            **extra,
        )

    stored(at(date(2030, 12, 30), "10:00"), "confirmed")  # past
    stored(at(wednesday, "05:30"), "confirmed")  # started (T0 is 06:00)
    stored(at(MONDAY, "08:00"), "completed")
    stored(
        at(MONDAY, "09:00"),
        "cancelled",
        cancelled_at=T0,
        cancellation_reason="Cancelada",
    )
    stored(at(MONDAY, "10:00"), "skipped_closure")
    stored(at(MONDAY, "11:00"), "conflict")
    stored(at(MONDAY, "12:00"), "unassigned")
    stored(at(MONDAY, "13:00"), "confirmed", tutor_user_id=other.id)
    # Booked outside the tutor's availability with an override (#49).
    in_uow(
        url,
        lambda session: _clear_tuesday(session, tutor.id),
    )
    book(client, owner, tutor, at(TUESDAY, "10:00"), override=["tutor_unavailable"])

    response = put_weekly(client, tutor.cookie, tutor.id, 0, [])

    assert code_of(response) == OK, response.text
    assert request_count(url, tutor) == 0


async def _clear_tuesday(session: AsyncSession, tutor_id: uuid.UUID) -> None:
    for row in await session.scalars(
        select(TutorAvailabilityWindow)
        .where(TutorAvailabilityWindow.tutor_user_id == tutor_id)
        .where(TutorAvailabilityWindow.weekday == 2)
    ):
        await session.delete(row)
    await session.flush()


# --- B1: local time in the center zone ------------------------------------------------


def test_sessions_are_judged_by_their_local_pieces_across_dst(
    module_seeded_url, client
):
    url = module_seeded_url
    tutor = make_tutor(url, client, windows=[(7, time(0), time(4))])
    # 2031-11-02 01:00-02:00 PST: the repeated hour's second pass.
    fall_back = datetime(2031, 11, 2, 9, 0, tzinfo=UTC)
    # 2031-03-09 01:30 PST to 03:30 PDT: one real hour across the gap.
    spring = datetime(2031, 3, 9, 9, 30, tzinfo=UTC)
    for start in (fall_back, spring):
        existing_session(
            url,
            starts_at=start,
            state="confirmed",
            subject_id=tutor.subject,
            tutor_user_id=tutor.id,
        )
    with center_zone(url, "America/Tijuana"):
        # Keeping 01:00-02:00 and 03:30-04:00 local leaves the fall-back
        # session covered and the spring session (01:30-03:30) uncovered.
        item = requested(
            put_weekly(
                client,
                tutor.cookie,
                tutor.id,
                0,
                [window(7, "01:00", "02:00"), window(7, "03:30", "04:00")],
            )
        )
        assert [s["starts_at"] for s in item["affected_sessions"]] == [
            "2031-03-09T09:30:00Z"
        ]
        cancel = cancel_request(client, tutor.cookie, tutor, item, 1)
        assert code_of(cancel) == OK
        # Without 01:00-02:00 local, the fall-back session is affected too.
        item = requested(
            put_weekly(client, tutor.cookie, tutor.id, 0, [window(7, "02:00", "04:00")])
        )
        assert [s["starts_at"] for s in item["affected_sessions"]] == [
            "2031-03-09T09:30:00Z",
            "2031-11-02T09:00:00Z",
        ]


# --- B5: series occurrences ----------------------------------------------------------


def test_a_series_occurrence_inside_the_horizon_is_affected(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    response = post(
        client,
        owner,
        "/api/v1/class-series",
        {
            "kind": "individual",
            "student_id": str(tutor.student),
            "subject_id": str(tutor.subject),
            "tutor_user_id": str(tutor.id),
            "classroom_id": None,
            "starts_on": "2031-01-06",
            "start_time": "16:00",
            "duration_minutes": 60,
            "recurrence": {"weekdays": [1], "interval": 1, "until": None, "count": 3},
        },
    )
    assert code_of(response) == CREATED, response.text
    series_id = response.json()["id"]
    rows = run_sql(
        url,
        "SELECT id, version FROM class_sessions WHERE series_id = :id"
        " ORDER BY starts_at",
        {"id": series_id},
    )
    assert len(rows) == 3
    # The third occurrence moves to 10:00 (an exception, #54).
    moved = client_patch(
        client,
        owner,
        rows[2].id,
        {
            "version": rows[2].version,
            "starts_at": at(MONDAY + timedelta(weeks=2), "10:00").isoformat(),
            "ends_at": at(MONDAY + timedelta(weeks=2), "11:00").isoformat(),
        },
    )
    assert code_of(moved) == OK, moved.text

    item = requested(shrink(tutor, client))

    assert [s["id"] for s in item["affected_sessions"]] == [
        str(rows[0].id),
        str(rows[1].id),
    ]
    approved = decide(client, owner, item, "approve")
    assert code_of(approved) == OK, approved.text
    [count] = run_sql(
        url,
        "SELECT count(*) AS n FROM class_sessions WHERE series_id = :id",
        {"id": series_id},
    )
    assert count.n == 3  # nothing materialized


def client_patch(
    client: TestClient, cookie: Any, session_id: Any, body: dict[str, Any]
) -> Response:
    return patch(client, cookie, f"{SESSIONS}/{session_id}", body)


# --- D3, E, F: approve ---------------------------------------------------------------


def test_approving_applies_the_change_and_flags_the_sessions(
    module_seeded_url, client, owner, people, tutor
):
    url = module_seeded_url
    kept = confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    offered = book(client, owner, tutor, at(MONDAY, "17:00"))
    item = requested(shrink(tutor, client))

    response = decide(client, owner, item, "approve")

    assert code_of(response) == OK, response.text
    assert response.json()["status"] == "approved"
    after = detail(client, tutor.cookie, tutor.id)
    assert after["weekly"] == WITHOUT_MONDAY_16_18
    assert after["version"] == 1
    assert after["pending_changes"] == []
    [recent] = after["recent_changes"]
    assert (recent["id"], recent["status"]) == (item["id"], "approved")
    assert recent["resolved_at"] is not None
    # E1: the confirmed session keeps its tutor; the offered one is unassigned
    # and its offer withdrawn.
    assert session_state(url, kept["id"]) == ("confirmed", tutor.id)
    assert session_state(url, offered["id"]) == ("unassigned", None)
    [offer] = run_sql(
        url,
        "SELECT status FROM assignment_offers WHERE id = :id",
        {"id": offered["pending_offer"]["id"]},
    )
    assert offer.status == "withdrawn"
    assert_offer_invariant(url, [kept["id"], offered["id"]])
    # E2, E3: the flag on the detail and the calendar.
    for session in (kept, offered):
        found = read(client, owner, session["id"])
        assert found["reassignment"]["reason"] == "availability_change"
        assert found["reassignment"]["tutor"] == {
            "id": str(tutor.id),
            "display_name": tutor.name,
        }
        assert found["reassignment"]["change_request_id"] == item["id"]
        assert "reassignment_needed" in calendar_markers(client, owner, session["id"])
        [flag] = flags(url, session["id"])
        assert (flag.tutor_user_id, flag.source_id, flag.created_by_user_id) == (
            tutor.id,
            uuid.UUID(item["id"]),
            people.owner.id,
        )
    # F1 to the tutor; F2 once to every schedule.assign holder.
    [approved] = notifications(
        url, "staff.availability_change_approved", "change_request_id", item["id"]
    )
    assert approved.recipient_user_id == tutor.id
    assert approved.params == {
        "change_request_id": item["id"],
        "kind": "weekly",
        "affected_count": 2,
    }
    needed = notifications(
        url, "scheduling.reassignment_needed", "tutor_user_id", tutor.id
    )
    recipients = [row.recipient_user_id for row in needed]
    assert len(recipients) == len(set(recipients))
    assert {people.owner.id, people.admin.id, people.coordinator.id} <= set(recipients)
    assert not {people.finance.id, people.suspended_admin.id, tutor.id} & set(
        recipients
    )
    assert needed[0].params == {
        "tutor_user_id": str(tutor.id),
        "tutor_name": tutor.name,
        "session_count": 2,
        "first_starts_at": "2031-01-06T22:00:00Z",
    }
    # The windows' events have the approver as actor.
    window_events = events(url, record_type="tutor_availability_windows")
    actors = {
        e.actor_user_id
        for e in window_events
        if str(tutor.id)
        in {
            (e.changes.get("tutor_user_id") or {}).get("old"),
            (e.changes.get("tutor_user_id") or {}).get("new"),
        }
    }
    assert actors - {None} == {people.owner.id}  # the setup's rows have none


def test_the_approval_recomputes_the_affected_sessions(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    snapshot = confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    item = requested(shrink(tutor, client))
    # Booked into the removed time after the request (still available then),
    # and the snapshot's session cancelled meanwhile.
    later = book(client, owner, tutor, at(MONDAY, "17:00"))
    cancel_session(client, owner, snapshot["id"])

    assert code_of(decide(client, owner, item, "approve")) == OK

    assert [f.source_id for f in flags(url, later["id"])] == [uuid.UUID(item["id"])]
    assert flags(url, snapshot["id"]) == []


def test_a_recompute_with_no_session_still_applies_without_flags(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    session = confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    item = requested(shrink(tutor, client))
    cancel_session(client, owner, session["id"])

    assert code_of(decide(client, owner, item, "approve")) == OK

    assert detail(client, tutor.cookie, tutor.id)["weekly"] == WITHOUT_MONDAY_16_18
    assert flags(url, session["id"]) == []
    assert (
        notifications(url, "scheduling.reassignment_needed", "tutor_user_id", tutor.id)
        == []
    )
    [approved] = notifications(
        url, "staff.availability_change_approved", "change_request_id", item["id"]
    )
    assert approved.params["affected_count"] == 0


def test_approving_time_off_inserts_the_exception(
    module_seeded_url, client, owner, people, tutor
):
    url = module_seeded_url
    session = book(client, owner, tutor, at(MONDAY, "16:00"))
    item = requested(
        add_exception(
            client, tutor.cookie, tutor.id, starts_on="2031-01-06", ends_on="2031-01-06"
        )
    )

    assert code_of(decide(client, owner, item, "approve")) == OK

    [exception] = detail(client, tutor.cookie, tutor.id)["exceptions"]
    assert (exception["kind"], exception["starts_on"]) == ("unavailable", "2031-01-06")
    assert session_state(url, session["id"]) == ("unassigned", None)
    [created] = [
        e
        for e in events(url, record_type="tutor_availability_exceptions")
        if e.record_id == uuid.UUID(exception["id"])
    ]
    assert (created.action, created.actor_user_id) == ("create", people.owner.id)


def test_approving_a_delete_removes_the_exception(
    module_seeded_url, client, owner, tutor
):
    extra = add_exception(
        client,
        tutor.cookie,
        tutor.id,
        kind="available",
        starts_on="2031-01-07",
        ends_on="2031-01-07",
        starts_at="10:00",
        ends_at="12:00",
    ).json()
    session = confirmed(client, owner, tutor, at(TUESDAY, "10:00"))
    item = requested(delete_exception(client, tutor.cookie, tutor.id, extra["id"]))

    assert code_of(decide(client, owner, item, "approve")) == OK

    assert detail(client, tutor.cookie, tutor.id)["exceptions"] == []
    assert read(client, owner, session["id"])["reassignment"] is not None


# --- D3 step 3: stale requests -------------------------------------------------------


def test_a_weekly_request_is_stale_after_another_weekly_change(
    module_seeded_url, client, owner, tutor
):
    confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    item = requested(shrink(tutor, client))
    grown = put_weekly(
        client, tutor.cookie, tutor.id, 0, [*WEEKLY, window(5, "09:00", "12:00")]
    )
    assert code_of(grown) == OK

    response = decide(client, owner, item, "approve")

    assert code_of(response) == STALE
    assert inbox_item(module_seeded_url, item["id"]).status == "pending"
    assert request_row(module_seeded_url, item["id"]).status == "pending"
    assert detail(client, tutor.cookie, tutor.id)["weekly"][0] == WEEKLY[0]


def test_a_delete_request_is_stale_once_the_exception_is_gone(
    module_seeded_url, client, owner, tutor
):
    extra = add_exception(
        client,
        tutor.cookie,
        tutor.id,
        kind="available",
        starts_on="2031-01-07",
        ends_on="2031-01-07",
        starts_at="10:00",
        ends_at="12:00",
    ).json()
    session = book(client, owner, tutor, at(TUESDAY, "10:00"))
    item = requested(delete_exception(client, tutor.cookie, tutor.id, extra["id"]))
    cancel_session(client, owner, session["id"])
    gone = delete_exception(client, tutor.cookie, tutor.id, extra["id"])
    assert code_of(gone) == NO_CONTENT

    assert code_of(decide(client, owner, item, "approve")) == STALE
    assert inbox_item(module_seeded_url, item["id"]).status == "pending"


# --- D4: reject ----------------------------------------------------------------------


def test_rejecting_changes_nothing_and_keeps_the_reason_out_of_the_notification(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    session = confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    item = requested(shrink(tutor, client))

    assert code_of(decide(client, owner, item, "reject")) == INVALID
    response = decide(client, owner, item, "reject", reason="Sin sustituto")

    assert code_of(response) == OK, response.text
    after = detail(client, tutor.cookie, tutor.id)
    assert after["weekly"] == WEEKLY
    [recent] = after["recent_changes"]
    assert (recent["status"], recent["rejection_reason"]) == (
        "rejected",
        "Sin sustituto",
    )
    assert flags(url, session["id"]) == []
    [rejected] = notifications(
        url, "staff.availability_change_rejected", "change_request_id", item["id"]
    )
    assert rejected.recipient_user_id == tutor.id
    assert "Sin sustituto" not in str(rejected.params)
    assert rejected.params["affected_count"] == 1


# --- D6: withdraw --------------------------------------------------------------------


def test_the_tutor_withdraws_a_pending_request(
    module_seeded_url, client, owner, people, tutor
):
    url = module_seeded_url
    confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    item = requested(shrink(tutor, client))
    other = make_tutor(url, client)
    coordinator = sign_in(client, people.coordinator.username)

    assert code_of(cancel_request(client, other.cookie, tutor, item, 1)) == NOT_FOUND
    assert code_of(cancel_request(client, coordinator, tutor, item, 1)) == DENIED
    assert code_of(cancel_request(client, tutor.cookie, tutor, item, 7)) == (
        VERSION_CONFLICT
    )
    response = cancel_request(client, tutor.cookie, tutor, item, 1)

    assert code_of(response) == OK, response.text
    assert response.json()["status"] == "cancelled"
    assert inbox_item(url, item["id"]).status == "cancelled"
    again = cancel_request(client, tutor.cookie, tutor, item, 2)
    assert code_of(again) == NOT_PENDING
    assert again.json()["details"] == {"status": "cancelled"}
    assert code_of(decide(client, owner, item, "approve")) == (
        409,
        "approval_transition_invalid",
    )
    assert detail(client, tutor.cookie, tutor.id)["weekly"] == WEEKLY


# --- C4, E4: roles -------------------------------------------------------------------


def test_who_may_submit_see_and_decide(module_seeded_url, client, owner, people, tutor):
    url = module_seeded_url
    confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    coordinator = sign_in(client, people.coordinator.username)
    finance = sign_in(client, people.finance.username)
    admin = sign_in(client, people.admin.username)

    assert code_of(shrink(tutor, client, coordinator)) == DENIED
    assert code_of(shrink(tutor, client, finance)) == DENIED
    item = requested(shrink(tutor, client, admin))

    assert item["requested_by"]["id"] == str(people.admin.id)
    path = f"{APPROVALS}/{item['approval_request_id']}"
    assert code_of(get(client, finance, path)) == NOT_FOUND
    assert code_of(get(client, tutor.cookie, path)) == NOT_FOUND
    assert code_of(get(client, coordinator, path)) == NOT_FOUND
    found = get(client, admin, path).json()
    assert found["subject"]["tutor"]["id"] == str(tutor.id)
    assert found["subject"]["affected_count"] == 1
    assert code_of(decide(client, admin, item, "approve")) == OK
    assert request_row(url, item["id"]).status == "approved"


# --- E1: flags close by themselves ---------------------------------------------------


def test_a_flag_closes_when_the_session_gets_another_tutor_or_is_cancelled(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    kept = confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    offered = book(client, owner, tutor, at(MONDAY, "17:00"))
    item = requested(shrink(tutor, client))
    assert code_of(decide(client, owner, item, "approve")) == OK
    replacement = make_tutor(url, client, subject=tutor.subject)

    current = read(client, owner, offered["id"])
    response = post(
        client,
        owner,
        f"{SESSIONS}/{offered['id']}/offers",
        {"tutor_user_id": str(replacement.id), "version": current["version"]},
    )
    assert code_of(response) == CREATED, response.text
    cancel_session(client, owner, kept["id"])

    for session in (kept, offered):
        assert read(client, owner, session["id"])["reassignment"] is None
        assert len(flags(url, session["id"])) == 1  # history stays


# --- I1 audit ------------------------------------------------------------------------


def test_requests_and_flags_are_audited(
    module_seeded_url, client, owner, people, tutor
):
    url = module_seeded_url
    session = confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    item = requested(shrink(tutor, client))
    assert code_of(decide(client, owner, item, "approve")) == OK

    request_events = [
        (e.action, e.actor_user_id)
        for e in events(
            url,
            record_type="tutor_availability_change_requests",
            record_id=uuid.UUID(item["id"]),
        )
    ]
    assert request_events == [("create", tutor.id), ("update", people.owner.id)]
    [flag_event] = [
        e
        for e in events(url, record_type="class_session_reassignment_flags")
        if e.changes["class_session_id"]["new"] == session["id"]
    ]
    assert (flag_event.action, flag_event.actor_user_id) == ("create", people.owner.id)


# --- I4: statement budgets -----------------------------------------------------------


def _rules(windows: list[tuple[int, time, time]]) -> Any:
    return staff.TutorAvailabilityRules(
        windows=tuple(staff.WeeklyWindow(*w) for w in windows), exceptions=()
    )


@pytest.mark.parametrize("count", [1, 30])
def test_the_impact_read_takes_a_fixed_number_of_statements(
    module_seeded_url, client, count
):
    url = module_seeded_url
    tutor = make_tutor(url, client)
    for week in range(count):
        existing_session(
            url,
            starts_at=at(MONDAY + timedelta(weeks=week), "16:00"),
            state="confirmed",
            subject_id=tutor.subject,
            tutor_user_id=tutor.id,
        )

    statements, affected = count_statements(
        url,
        lambda db: scheduling.AVAILABILITY_IMPACT_SOURCE.affected_sessions(
            db,
            tutor.id,
            current=_rules([(1, time(8), time(20))]),
            proposed=_rules([(1, time(8), time(16))]),
            dates=None,
        ),
    )

    assert len(affected) == count
    assert statements <= 6


def test_the_summaries_and_the_availability_read_budgets(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    base, _ = count_statements(
        url,
        lambda db: staff.get_availability(
            db, principal=_owner_principal(), user_id=tutor.id
        ),
    )
    confirmed(client, owner, tutor, at(MONDAY, "16:00"))
    first = requested(shrink(tutor, client))
    second = requested(
        add_exception(
            client, tutor.cookie, tutor.id, starts_on="2031-01-06", ends_on="2031-01-06"
        )
    )
    assert code_of(decide(client, owner, second, "reject", reason="No")) == OK

    statements, found = count_statements(
        url,
        lambda db: staff.get_availability(
            db, principal=_owner_principal(), user_id=tutor.id
        ),
    )
    assert statements <= base + 2  # G2
    assert [p.id for p in found.pending_changes] == [uuid.UUID(first["id"])]
    statements, summaries = count_statements(
        url,
        lambda db: AVAILABILITY_CHANGE_HANDLER.summaries(
            db, [uuid.UUID(first["id"]), uuid.UUID(second["id"])]
        ),
    )
    assert statements <= 4  # D2
    assert summaries[uuid.UUID(first["id"])]["change_request_status"] == "pending"


def _owner_principal() -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({"owner"}),
        permissions=frozenset({"tutor.view", "tutor.manage"}),
    )
