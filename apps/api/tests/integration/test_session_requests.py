"""Creating, reading and withdrawing session requests, and their inbox type
(#59 C, D, E, A4; H2) with real logins through the real app.

Helpers: ``session_request_support`` (the clock is frozen at its ``NOW`` for
the module; slots are in the #48 fixed week of Monday 2031-01-06).
"""

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
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
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.session_request_support import (
    MONDAY_WINDOW,
    NOW,
    REQUESTS,
    Family,
    approval_of,
    create_request,
    created,
    get_slots,
    guardian_with_child,
    link_child,
    request_body,
    request_rows,
    slot_starts,
    slots_params,
    student_account,
    withdraw,
)
from tests.integration.test_class_session_create import (
    MONDAY,
    at,
    new_subject,
    new_tutor,
)

APPROVALS = "/api/v1/approval-requests"
INVALID = (422, "validation_error")
DETAIL_KEYS = {
    "id",
    "student",
    "subject",
    "starts_at",
    "ends_at",
    "duration_minutes",
    "status",
    "note",
    "requested_by_me",
    "can_withdraw",
    "created_at",
    "resolved_at",
    "version",
}


@pytest.fixture(scope="module", autouse=True)
def frozen() -> Iterator[None]:
    with frozen_clock(NOW):
        yield


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def family(module_seeded_url: str) -> Family:
    return guardian_with_child(module_seeded_url)


@pytest.fixture
def cookie(client: TestClient, family: Family) -> Any:
    return sign_in(client, family.user.username)


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    subject = new_subject(module_seeded_url)
    new_tutor(module_seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    return subject


@pytest.fixture(scope="module")
def owner_cookie(module_seeded_url: str, client: TestClient) -> Any:
    owner = create_user(module_seeded_url, roles=(OWNER,))
    return sign_in(client, owner.username)


def field_errors(response: Any) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [(e["path"][-1], e["code"]) for e in response.json()["errors"]]


def row_counts(url: str) -> tuple[int, int, int, int]:
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM session_requests) AS r,"
        " (SELECT count(*) FROM approval_requests) AS a,"
        " (SELECT count(*) FROM class_sessions) AS s,"
        " (SELECT count(*) FROM class_session_participants) AS p",
    )
    return (int(row.r), int(row.a), int(row.s), int(row.p))


# --- C: create -----------------------------------------------------------------------


def test_a_guardian_requests_a_slot(module_seeded_url, client, cookie, family, subject):
    before = row_counts(module_seeded_url)

    response = create_request(
        client,
        cookie,
        request_body(family.student_id, subject, note="  Prefiere por la mañana  "),
    )

    detail = created(response)
    assert set(detail) == DETAIL_KEYS
    assert detail["status"] == "pending"
    assert detail["note"] == "Prefiere por la mañana"
    assert detail["requested_by_me"] is True and detail["can_withdraw"] is True
    assert detail["resolved_at"] is None and detail["version"] == 1
    assert detail["duration_minutes"] == 60
    assert datetime.fromisoformat(detail["starts_at"]) == at(MONDAY, "10:00")
    assert datetime.fromisoformat(detail["ends_at"]) == at(MONDAY, "11:00")
    assert detail["student"]["id"] == str(family.student_id)
    assert set(detail["student"]) == {"id", "display_name"}
    assert set(detail["subject"]) == {"id", "name", "education_level_name"}
    # One request row and one inbox item; no session, no participant (C3).
    after = row_counts(module_seeded_url)
    assert after[0] == before[0] + 1 and after[1] == before[1] + 1
    assert after[2:] == before[2:]
    approval = approval_of(module_seeded_url, detail["id"])
    assert approval.status == "pending"
    assert approval.request_type == "scheduling.session_request"
    assert approval.requester_user_id == family.user.id


def test_an_empty_note_is_null_and_a_student_requests_for_themselves(
    module_seeded_url, client, subject
):
    account, student = student_account(module_seeded_url)
    cookie = sign_in(client, account.username)

    detail = created(
        create_request(client, cookie, request_body(student, subject, note="   "))
    )

    assert detail["note"] is None
    assert detail["student"]["id"] == str(student)


def test_the_create_is_audited_and_notifies_nobody(
    module_seeded_url, client, cookie, family, subject
):
    [before] = run_sql(module_seeded_url, "SELECT count(*) AS n FROM notifications")

    detail = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )

    [after] = run_sql(module_seeded_url, "SELECT count(*) AS n FROM notifications")
    assert after.n == before.n
    [event] = events(
        module_seeded_url, record_type="session_requests", record_id=detail["id"]
    )
    assert event.action == "create" and event.actor_user_id == family.user.id
    assert event.request_id
    approval = approval_of(module_seeded_url, detail["id"])
    [inbox] = events(
        module_seeded_url, record_type="approval_requests", record_id=approval.id
    )
    assert inbox.action == "create" and inbox.actor_user_id == family.user.id


@pytest.mark.parametrize(
    "extra",
    [
        {"tutor_user_id": str(uuid.uuid4())},
        {"classroom_id": str(uuid.uuid4())},
        {"ends_at": "2031-01-06T11:00:00-06:00"},
        {"duration_minutes": 50},
        {"note": "x" * 501},
    ],
    ids=["tutor", "classroom", "ends_at", "duration", "note"],
)
def test_the_body_schema_is_422(
    module_seeded_url, client, cookie, family, subject, extra
):
    before = row_counts(module_seeded_url)

    response = create_request(
        client, cookie, request_body(family.student_id, subject) | extra
    )

    assert code_of(response) == INVALID, response.text
    assert row_counts(module_seeded_url) == before


@pytest.mark.parametrize(
    ("starts_at", "code"),
    [
        ("2031-01-06T10:00:00", "naive_datetime"),
        ("2031-01-06T10:00:30-06:00", "not_whole_minutes"),
        ("2031-01-06T10:15:00-06:00", "not_on_grid"),
        ("2031-01-06T23:30:00-06:00", "crosses_local_date"),
    ],
)
def test_the_start_is_422(client, cookie, family, subject, starts_at, code):
    response = create_request(
        client, cookie, request_body(family.student_id, subject, starts_at)
    )

    assert field_errors(response) == [("starts_at", code)]


def test_the_grid_is_local(client, cookie, family, subject):
    # 16:30 UTC is 10:30 in Mexico City: on the grid.
    response = create_request(
        client, cookie, request_body(family.student_id, subject, "2031-01-06T16:30:00Z")
    )

    assert created(response)["status"] == "pending"


def test_reference_errors_come_together(module_seeded_url, client):
    family = guardian_with_child(module_seeded_url, student_active=False)
    cookie = sign_in(client, family.user.username)

    response = create_request(
        client,
        cookie,
        request_body(family.student_id, uuid.uuid4(), "2031-01-06T10:15:00-06:00"),
    )

    assert field_errors(response) == [
        ("starts_at", "not_on_grid"),
        ("student_id", "student_inactive"),
        ("subject_id", "subject_unknown"),
    ]


def test_the_record_policy_comes_before_the_body_checks(client, cookie, subject):
    response = create_request(
        client, cookie, request_body(uuid.uuid4(), subject, "2031-01-06T10:15:00")
    )

    assert code_of(response) == (404, "not_found")


def test_the_limit_comes_before_the_overlap(
    module_seeded_url, client, cookie, family, subject
):
    for n in range(5):
        created(
            create_request(
                client,
                cookie,
                request_body(
                    family.student_id,
                    subject,
                    at(MONDAY, "10:00") + timedelta(days=7 * n),
                ),
            )
        )

    overlapping = create_request(
        client, cookie, request_body(family.student_id, subject, at(MONDAY, "10:30"))
    )
    other_child = link_child(module_seeded_url, family)
    allowed = create_request(client, cookie, request_body(other_child, subject))

    assert code_of(overlapping) == (409, "session_request_limit_reached")
    assert overlapping.json().keys() == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "request_id",
    }
    assert created(allowed)["status"] == "pending"


def test_an_overlapping_pending_request_is_409_and_back_to_back_is_fine(
    module_seeded_url, client, cookie, family, subject
):
    created(create_request(client, cookie, request_body(family.student_id, subject)))

    overlapping = create_request(
        client, cookie, request_body(family.student_id, subject, at(MONDAY, "10:30"))
    )
    back_to_back = create_request(
        client, cookie, request_body(family.student_id, subject, at(MONDAY, "11:00"))
    )

    assert code_of(overlapping) == (409, "session_request_overlap")
    assert created(back_to_back)["status"] == "pending"


@pytest.mark.parametrize(
    "hhmm",
    ["12:00", "09:00"],
    ids=["no-tutor-window", "before-the-window"],
)
def test_a_time_without_a_free_tutor_is_slot_unavailable(
    module_seeded_url, client, cookie, family, subject, hhmm
):
    before = row_counts(module_seeded_url)

    response = create_request(
        client, cookie, request_body(family.student_id, subject, at(MONDAY, hhmm))
    )

    assert code_of(response) == (409, "slot_unavailable")
    assert set(response.json()) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "request_id",
    }
    assert row_counts(module_seeded_url) == before


def test_the_notice_and_a_closed_day_are_slot_unavailable(
    client, cookie, family, subject
):
    soon = at(NOW.date() + timedelta(days=1), "10:00")
    sunday = at(MONDAY + timedelta(days=6), "10:00")

    for starts_at in (soon, sunday):
        response = create_request(
            client, cookie, request_body(family.student_id, subject, starts_at)
        )
        assert code_of(response) == (409, "slot_unavailable")


# --- C3: a request holds no time -----------------------------------------------------


def test_a_request_holds_no_time(
    module_seeded_url, client, cookie, family, subject, owner_cookie
):
    created(create_request(client, cookie, request_body(family.student_id, subject)))
    other = guardian_with_child(module_seeded_url)
    other_cookie = sign_in(client, other.user.username)

    # The same student's slots still list it, flagged.
    own = get_slots(client, cookie, slots_params(family.student_id, subject))
    assert slot_starts(own) == ["10:00", "10:30", "11:00"]
    flags = [s["already_requested"] for s in own.json()["days"][0]["slots"]]
    assert flags == [True, True, False]
    # Another family's student sees the same times, unflagged.
    theirs = get_slots(client, other_cookie, slots_params(other.student_id, subject))
    assert slot_starts(theirs) == ["10:00", "10:30", "11:00"]
    assert not any(s["already_requested"] for s in theirs.json()["days"][0]["slots"])
    # Staff can still book the same student at that time.
    booked = post(
        client,
        owner_cookie,
        "/api/v1/class-sessions",
        {
            "kind": "individual",
            "student_id": str(family.student_id),
            "subject_id": str(subject),
            "starts_at": at(MONDAY, "10:00").isoformat(),
            "ends_at": at(MONDAY, "11:00").isoformat(),
        },
    )
    assert booked.status_code == 201, booked.text


def test_another_familys_request_does_not_block_this_one(
    module_seeded_url, client, cookie, family, subject
):
    other = guardian_with_child(module_seeded_url)
    other_cookie = sign_in(client, other.user.username)
    created(
        create_request(client, other_cookie, request_body(other.student_id, subject))
    )

    assert created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )


# --- D: read, list and withdraw -------------------------------------------------------


def test_the_list_holds_every_childs_requests_newest_first(
    module_seeded_url, client, cookie, family, subject
):
    second = link_child(module_seeded_url, family)
    first_id = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )["id"]
    second_id = created(create_request(client, cookie, request_body(second, subject)))[
        "id"
    ]
    third = created(
        create_request(
            client,
            cookie,
            request_body(family.student_id, subject, at(MONDAY, "11:00")),
        )
    )
    _ok(withdraw(client, cookie, third["id"], third["version"]))

    everything = get(client, cookie, REQUESTS)
    pending = get(client, cookie, REQUESTS, status="pending")
    by_child = get(client, cookie, REQUESTS, student_id=str(second))
    paged = get(client, cookie, REQUESTS, limit=1, offset=1)

    assert [i["id"] for i in everything.json()["items"]] == [
        third["id"],
        second_id,
        first_id,
    ]
    assert everything.json()["total"] == 3
    assert {i["status"] for i in everything.json()["items"]} == {"pending", "withdrawn"}
    assert [i["id"] for i in pending.json()["items"]] == [second_id, first_id]
    assert [i["id"] for i in by_child.json()["items"]] == [second_id]
    assert paged.json() == {
        "items": [everything.json()["items"][1]],
        "total": 3,
        "limit": 1,
        "offset": 1,
    }
    assert (everything.json()["limit"], everything.json()["offset"]) == (25, 0)
    assert code_of(get(client, cookie, REQUESTS, limit=101)) == INVALID
    assert set(everything.json()["items"][0]) == DETAIL_KEYS


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_foreign_student_filter_and_a_staff_caller_get_an_empty_page(
    module_seeded_url, client, cookie, family, subject, owner_cookie
):
    other = guardian_with_child(module_seeded_url)
    other_cookie = sign_in(client, other.user.username)
    created(
        create_request(client, other_cookie, request_body(other.student_id, subject))
    )

    assert _ok(get(client, cookie, REQUESTS, student_id=str(other.student_id))) == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 0,
    }
    assert _ok(get(client, owner_cookie, REQUESTS)) == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 0,
    }


def test_the_list_takes_at_most_five_statements(
    module_seeded_url, client, cookie, family, subject
):
    second = link_child(module_seeded_url, family)
    for student in (family.student_id, second):
        for hhmm in ("10:00", "11:00"):
            created(
                create_request(
                    client, cookie, request_body(student, subject, at(MONDAY, hhmm))
                )
            )
    principal = Principal(
        user_id=family.user.id, roles=frozenset({"guardian"}), permissions=frozenset()
    )

    async def call(session: AsyncSession) -> Any:
        return await scheduling.list_session_requests(
            session, principal, student_id=None, status="all", limit=20, offset=0
        )

    statements, page = count_statements(module_seeded_url, call)

    assert page.total == 4 and len(page.items) == 4
    assert statements <= 5


def test_the_detail_and_a_co_guardian(
    module_seeded_url, client, cookie, family, subject
):
    from app.modules.families.models import Student
    from tests.factories.base import persist
    from tests.factories.families import GuardianFactory, link_guardian
    from tests.integration.lifecycle_support import in_uow

    detail = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )
    co = create_user(module_seeded_url, roles=("guardian",))

    async def _link(session: AsyncSession) -> None:
        guardian = await persist(session, GuardianFactory, user_id=co.id)
        student = await session.get(Student, family.student_id)
        assert student is not None
        await link_guardian(session, student, guardian, "father")

    in_uow(module_seeded_url, _link)
    co_cookie = sign_in(client, co.username)

    mine = _ok(get(client, cookie, f"{REQUESTS}/{detail['id']}"))
    theirs = _ok(get(client, co_cookie, f"{REQUESTS}/{detail['id']}"))
    refused = withdraw(client, co_cookie, detail["id"], detail["version"])

    assert mine == detail
    assert theirs["requested_by_me"] is False and theirs["can_withdraw"] is False
    assert code_of(refused) == (403, "permission_denied")
    assert request_rows(module_seeded_url, family.student_id)[0].status == "pending"


def test_withdrawing_frees_the_cap_and_cancels_the_inbox_item(
    module_seeded_url, client, cookie, family, subject
):
    detail = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )

    stale = withdraw(client, cookie, detail["id"], 99)
    done = _ok(withdraw(client, cookie, detail["id"], detail["version"]))
    again = withdraw(client, cookie, detail["id"], done["version"])

    assert code_of(stale) == (409, "version_conflict")
    assert done["status"] == "withdrawn" and done["version"] == 2
    assert done["can_withdraw"] is False and done["resolved_at"] is not None
    assert code_of(again) == (409, "session_request_not_pending")
    approval = approval_of(module_seeded_url, detail["id"])
    assert approval.status == "cancelled"
    assert approval.resolved_by_user_id == family.user.id
    # The same time can be requested again.
    assert created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )


def test_unknown_requests_are_404(client, cookie):
    unknown = uuid.uuid4()

    assert code_of(get(client, cookie, f"{REQUESTS}/{unknown}")) == (404, "not_found")
    assert code_of(withdraw(client, cookie, unknown, 1)) == (404, "not_found")
    assert code_of(withdraw(client, cookie, unknown, "x")) == INVALID


# --- E: the inbox type ---------------------------------------------------------------


def test_approve_and_reject_wait_for_60(
    module_seeded_url, client, cookie, family, subject, owner_cookie
):
    detail = created(
        create_request(
            client, cookie, request_body(family.student_id, subject, note="Hola")
        )
    )
    approval = approval_of(module_seeded_url, detail["id"])
    item = _ok(get(client, owner_cookie, f"{APPROVALS}/{approval.id}"))

    approve = post(client, owner_cookie, f"{APPROVALS}/{approval.id}/approve", {})
    reject = post(
        client,
        owner_cookie,
        f"{APPROVALS}/{approval.id}/reject",
        {"reason": "No hay cupo"},
    )

    assert code_of(approve) == (409, "session_request_review_unavailable")
    assert code_of(reject) == (409, "session_request_review_unavailable")
    assert approval_of(module_seeded_url, detail["id"]).status == "pending"
    assert request_rows(module_seeded_url, family.student_id)[0].status == "pending"
    assert item["request_type"] == "scheduling.session_request"
    assert item["allowed_actions"] == ["approve", "reject"]
    assert item["subject"] == {
        "student_name": detail["student"]["display_name"],
        "subject_name": detail["subject"]["name"],
        "education_level_name": detail["subject"]["education_level_name"],
        "starts_at": detail["starts_at"].replace("+00:00", "Z"),
        "ends_at": detail["ends_at"].replace("+00:00", "Z"),
        "duration_minutes": 60,
        "requested_by": item["requester"]["display_name"],
        "note": "Hola",
    }


@pytest.mark.parametrize(
    ("roles", "sees"),
    [
        ((OWNER,), True),
        ((ADMINISTRATOR,), True),
        ((ACADEMIC_COORDINATOR,), False),
        ((FINANCE_STAFF,), False),
        ((TUTOR,), False),
        (("student",), False),
        (("guardian",), False),
    ],
)
def test_inbox_visibility(
    module_seeded_url, client, cookie, family, subject, roles, sees
):
    detail = created(
        create_request(client, cookie, request_body(family.student_id, subject))
    )
    approval = approval_of(module_seeded_url, detail["id"])
    if roles == ("student",):
        account, _ = student_account(module_seeded_url)
    else:
        account = create_user(module_seeded_url, roles=roles)
    viewer = sign_in(client, account.username)

    response = get(client, viewer, f"{APPROVALS}/{approval.id}")
    approve = post(client, viewer, f"{APPROVALS}/{approval.id}/approve", {})

    if sees:
        assert response.status_code == 200, response.text
        assert code_of(approve) == (409, "session_request_review_unavailable")
    else:
        assert code_of(response) == (404, "not_found")
        assert code_of(approve) == (404, "not_found")


def test_the_summaries_take_four_statements(
    module_seeded_url, client, cookie, family, subject
):
    ids = [
        uuid.UUID(
            created(
                create_request(
                    client,
                    cookie,
                    request_body(family.student_id, subject, at(MONDAY, hhmm)),
                )
            )["id"]
        )
        for hhmm in ("10:00", "11:00")
    ]

    async def call(session: AsyncSession) -> Any:
        return await scheduling.SESSION_REQUEST_APPROVAL_HANDLER.summaries(session, ids)

    statements, summaries = count_statements(module_seeded_url, call)

    assert set(summaries) == set(ids)
    assert statements <= 4


# --- A4: the time-zone dependency (own database) -------------------------------------


def test_a_pending_request_blocks_a_zone_change_until_withdrawn(seeded_url):
    owner = create_user(seeded_url, roles=(OWNER,))
    family = guardian_with_child(seeded_url)
    subject = new_subject(seeded_url)
    new_tutor(seeded_url, qualified_for=[subject], windows=MONDAY_WINDOW)
    with auth_client(seeded_url) as client:
        cookie = sign_in(client, family.user.username)
        detail = created(
            create_request(client, cookie, request_body(family.student_id, subject))
        )
        staff = sign_in(client, owner.username)
        reauthenticate(client, staff)
        blocked = patch(
            client,
            staff,
            "/api/v1/business-settings",
            {"version": 1, "time_zone": "America/Tijuana"},
        )
        _ok(withdraw(client, cookie, detail["id"], detail["version"]))
        allowed = patch(
            client,
            staff,
            "/api/v1/business-settings",
            {"version": 1, "time_zone": "America/Tijuana"},
        )

    assert code_of(blocked) == (409, "tz_change_requires_migration")
    assert allowed.status_code == 200, allowed.text
