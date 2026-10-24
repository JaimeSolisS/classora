"""The tutor schedule (#67 B1-B5, G1, G2) with real logins through the real
app: ``GET /api/v1/tutors/{user_id}/schedule``.

IDOR (another tutor, a user who isn't a tutor: the same ``404`` as an
unknown id), the current-tutor rule through a real #55 substitution, the
#52 accept setting ``first_confirmed_at`` once (A2), the closed allow-list,
the #57 range rules and the statement budget.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
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
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.schedule_schemas import ScheduleRangeQuery
from tests.authz.assertions import assert_same_not_found
from tests.conftest import frozen_clock
from tests.factories.scheduling import persist_subject
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get, unique_year
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.schedule_support import (
    ids_of,
    principal_of,
    read,
    stored,
    tutor_path,
    week,
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

NOT_FOUND = (404, "not_found")
FORBIDDEN = (403, "permission_denied")
INVALID = (422, "validation_error")
OK = (200, None)


def _monday(year: int) -> date:
    day = date(year, 1, 1)
    return day + timedelta(days=(7 - day.weekday()) % 7)


MONDAY = _monday(unique_year())


def slot(day: int, hour: int) -> Any:
    return week(MONDAY)[0] + timedelta(days=day, hours=hour)


@pytest.fixture(scope="module", autouse=True)
def stopped_clock() -> Any:
    with frozen_clock(T0) as frozen:
        yield frozen


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(client: TestClient, module_seeded_url: str) -> Any:
    return sign_in(client, create_user(module_seeded_url, roles=(OWNER,)).username)


def tutor_week(
    client: TestClient, cookie: Any, user_id: Any, day: date = MONDAY
) -> Any:
    return read(client, cookie, tutor_path(user_id), *week(day))


def new_setup(url: str) -> Setup:
    subject = new_subject(url)
    return Setup(
        student=new_student(url),
        subject=subject,
        tutor=new_tutor_user(url, qualified_for=[subject]),
        room=new_room(url),
    )


# --- B1: who may read ---------------------------------------------------------------


def test_a_tutor_sees_only_their_own_sessions(module_seeded_url, client):
    url = module_seeded_url
    mine, theirs = (create_user(url, roles=(TUTOR,)) for _ in range(2))
    own = [
        stored(url, slot(0, 14), tutor_user_id=mine.id, students=(new_student(url),)),
        stored(url, slot(0, 16), state="offered", tutor_user_id=mine.id),
    ]
    stored(url, slot(0, 14), tutor_user_id=theirs.id)
    stored(url, slot(0, 18), state="unassigned", tutor=False)
    cookie = sign_in(client, mine.username)

    assert ids_of(tutor_week(client, cookie, mine.id)) == own


def test_another_tutor_and_a_non_tutor_are_the_same_404_as_an_unknown_id(
    module_seeded_url, client
):
    url = module_seeded_url
    mine, theirs = (create_user(url, roles=(TUTOR,)) for _ in range(2))
    stored(url, slot(1, 14), tutor_user_id=theirs.id)
    guardian = create_user(url, roles=(GUARDIAN,))
    cookie = sign_in(client, mine.username)
    unknown = tutor_week(client, cookie, uuid.uuid4())

    assert_same_not_found(tutor_week(client, cookie, theirs.id), unknown)
    assert_same_not_found(tutor_week(client, cookie, guardian.id), unknown)


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR])
def test_staff_read_a_tutors_schedule_but_not_a_non_tutors(
    module_seeded_url, client, role
):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    shown = stored(url, slot(1, 16), tutor_user_id=tutor.id)
    not_a_tutor = create_user(url, roles=(STUDENT,))
    staff = create_user(url, roles=(role,))
    cookie = sign_in(client, staff.username)

    assert ids_of(tutor_week(client, cookie, tutor.id)) == [shown]
    assert_same_not_found(
        tutor_week(client, cookie, not_a_tutor.id),
        tutor_week(client, cookie, uuid.uuid4()),
    )
    # Their own id: they are not tutors.
    assert code_of(tutor_week(client, cookie, staff.id)) == NOT_FOUND


@pytest.mark.parametrize("role", [FINANCE_STAFF, STUDENT, GUARDIAN])
def test_callers_without_tutor_view_get_403(module_seeded_url, client, role):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    cookie = sign_in(client, create_user(url, roles=(role,)).username)

    assert code_of(tutor_week(client, cookie, tutor.id)) == FORBIDDEN


# --- B3, A2: the current tutor, through the real #52 and #55 flows ------------------


def first_confirmed_at(url: str, session_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT first_confirmed_at FROM class_sessions WHERE id = :id",
        {"id": str(session_id)},
    )
    return row.first_confirmed_at


def test_accept_then_substitution(module_seeded_url, client, owner):
    url = module_seeded_url
    setup = new_setup(url)
    substitute = new_tutor_user(url, qualified_for=[setup.subject])
    created, offer_id = offered(
        client, owner, setup, starts_at=at(OFFER_MONDAY, "15:00")
    )
    tutor = sign_in(client, setup.tutor.username)
    # Offered: the tutor sees it ("awaiting your answer"); not confirmed yet.
    [item] = tutor_week(client, tutor, setup.tutor.id, OFFER_MONDAY).json()["items"]
    assert (item["id"], item["state"]) == (created["id"], "offered")
    assert first_confirmed_at(url, created["id"]) is None

    # G2: the real accept sets first_confirmed_at once, to the clock.
    assert code_of(answer(client, tutor, offer_id, "accept")) == OK
    confirmed_at = first_confirmed_at(url, created["id"])
    assert confirmed_at == T0

    # #55: once the substitute accepts, only they see the session.
    session = get(client, owner, f"/api/v1/class-sessions/{created['id']}").json()
    item = substituted(client, owner, session, substitute.id)
    substitute_cookie = sign_in(client, substitute.username)
    assert code_of(answer(client, substitute_cookie, item["offer_id"], "accept")) == OK

    assert ids_of(tutor_week(client, tutor, setup.tutor.id, OFFER_MONDAY)) == []
    assert ids_of(
        tutor_week(client, substitute_cookie, substitute.id, OFFER_MONDAY)
    ) == [created["id"]]
    # The handover changed the tutor, never first_confirmed_at.
    assert first_confirmed_at(url, created["id"]) == confirmed_at


def test_the_audit_row_of_the_confirmation_lists_first_confirmed_at(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    setup = new_setup(url)
    created, offer_id = offered(
        client, owner, setup, starts_at=at(OFFER_MONDAY, "17:00")
    )
    tutor = sign_in(client, setup.tutor.username)
    assert code_of(answer(client, tutor, offer_id, "accept")) == OK

    rows = run_sql(
        url,
        "SELECT changes FROM audit_events WHERE record_type = 'class_sessions'"
        " AND record_id = :id AND action = 'update' ORDER BY occurred_at, id",
        {"id": created["id"]},
    )
    confirming = [row.changes for row in rows if "first_confirmed_at" in row.changes]
    assert len(confirming) == 1
    assert confirming[0]["state"]["new"] == "confirmed"
    assert confirming[0]["first_confirmed_at"]["old"] is None


# --- B4: the item -------------------------------------------------------------------


def test_the_tutor_item_is_a_closed_allow_list(module_seeded_url, client):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    room = new_room(url)
    students = (new_student(url), new_student(url))
    stored(
        url,
        slot(2, 14),
        kind="group",
        tutor_user_id=tutor.id,
        classroom_id=room,
        students=students,
    )
    cookie = sign_in(client, tutor.username)

    [item] = tutor_week(client, cookie, tutor.id).json()["items"]

    assert set(item) == {
        "id",
        "kind",
        "state",
        "starts_at",
        "ends_at",
        "subject",
        "classroom",
        "participants",
    }
    assert item["classroom"]["id"] == str(room)
    assert {p["student_id"] for p in item["participants"]} == {str(s) for s in students}
    assert all(set(p) == {"student_id", "display_name"} for p in item["participants"])


# --- B2, B3: the range rules ---------------------------------------------------------


SEEN: dict[str, Any] = {"state": "cancelled", "seen": True}
DAY_2 = "2031-01-07T00:00:00Z"
DAY_47 = "2031-02-16T00:00:00Z"


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"from": "2031-01-06T00:00:00", "to": DAY_2}, "naive_datetime"),
        ({"from": DAY_2, "to": DAY_2}, "range_invalid"),
        ({"from": "2031-01-01T00:00:00Z", "to": DAY_47}, "range_too_long"),
        ({"to": DAY_2}, None),
        (
            {
                "from": "2031-01-06T00:00:00Z",
                "to": DAY_2,
                "tutor_user_id": str(uuid.UUID(int=1)),
            },
            None,
        ),
    ],
)
def test_the_range_422s(module_seeded_url, client, params, error):
    tutor = create_user(module_seeded_url, roles=(TUTOR,))
    cookie = sign_in(client, tutor.username)

    response = get(client, cookie, tutor_path(tutor.id), **params)

    assert code_of(response) == INVALID, response.text
    if error is not None:
        assert error in {e["code"] for e in response.json()["errors"]}


def test_the_overlap_edges(module_seeded_url, client):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    start, end = slot(3, 12), slot(3, 15)
    # Cancelled after a confirmation (shown, and free to overlap).
    stored(url, start - timedelta(hours=1), tutor_user_id=tutor.id, **SEEN)
    across = stored(url, start - timedelta(minutes=30), tutor_user_id=tutor.id, **SEEN)
    inside = stored(url, start + timedelta(hours=1), tutor_user_id=tutor.id, **SEEN)
    stored(url, end, tutor_user_id=tutor.id, **SEEN)
    cookie = sign_in(client, tutor.username)

    assert ids_of(read(client, cookie, tutor_path(tutor.id), start, end)) == [
        across,
        inside,
    ]


def test_more_than_1000_matches_are_truncated(module_seeded_url, client):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    start = slot(4, 8)

    async def _bulk(session: AsyncSession) -> None:
        subject = await persist_subject(session)
        session.add_all(
            ClassSession(
                kind="individual",
                state="cancelled",
                subject_id=subject.id,
                tutor_user_id=tutor.id,
                starts_at=start,
                ends_at=start + timedelta(minutes=60),
                scheduled_minutes=60,
                cancelled_at=start - timedelta(days=1),
                cancellation_reason="Prueba",
                first_confirmed_at=start - timedelta(days=2),
            )
            for _ in range(1001)
        )
        await session.flush()

    in_uow(url, _bulk)
    cookie = sign_in(client, tutor.username)

    body = tutor_week(client, cookie, tutor.id).json()

    assert len(body["items"]) == 1000
    assert body["truncated"] is True


# --- B5: the statement budget ---------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 30])
def test_the_schedule_takes_at_most_7_statements(module_seeded_url, count):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    room = new_room(url)
    budget = MONDAY + timedelta(days=14)
    base = week(budget)[0]
    for index in range(count):
        stored(
            url,
            base + timedelta(days=index // 8, hours=10 + index % 8),
            tutor_user_id=tutor.id,
            classroom_id=room,
            students=(new_student(url), new_student(url)),
        )
    start, end = week(budget)
    query = ScheduleRangeQuery.model_validate({"from": start, "to": end})
    callers = {
        "tutor": principal_of(tutor, TUTOR),
        "staff": principal_of(create_user(url, roles=(OWNER,)), OWNER),
    }

    def read_as(principal: Any) -> Any:
        async def _read(session: AsyncSession) -> Any:
            return await scheduling.tutor_schedule(session, principal, tutor.id, query)

        return _read

    counts = {}
    for name, principal in callers.items():
        counts[name], result = count_statements(url, read_as(principal))
        assert len(result.items) == count
    assert all(value <= 7 for value in counts.values()), counts
