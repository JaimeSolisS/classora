"""Reviewers grant a tutor a subject directly (HF-05 A; owner-approved E1)
with real logins through the real app: the effect, the check order, any
account state, concurrency, and the granted row behaving as any approved
qualification."""

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.modules.staff import service
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import (
    create_level,
    create_subject,
    get,
    patch,
    post,
    subject_path,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.test_class_session_create import MONDAY_10, MONDAY_11, new_tutor
from tests.integration.test_tutor_qualifications import (
    APPROVAL_TYPE,
    CREATED,
    DENIED,
    EXISTS,
    INACTIVE,
    INVALID,
    NOT_FOUND,
    OK,
    notifications_of,
    qualifications_path,
    request,
    revoke,
)


def grant_path(user_id: Any) -> str:
    return f"{qualifications_path(user_id)}/grant"


def grant(client: TestClient, cookie: Any, user_id: Any, subject_id: Any) -> Response:
    return post(client, cookie, grant_path(user_id), {"subject_id": str(subject_id)})


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
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


@pytest.fixture(scope="module")
def level(client: TestClient, owner_cookie: Any) -> dict[str, Any]:
    return create_level(client, owner_cookie)


@pytest.fixture
def subject(client: TestClient, owner_cookie: Any, level: dict[str, Any]) -> Any:
    return create_subject(client, owner_cookie, level["id"])


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,))


def rows_of(url: str, user_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, subject_id, status, created_at, decided_at,"
        " decided_by_user_id, version"
        " FROM tutor_qualifications WHERE tutor_user_id = :id ORDER BY created_at",
        {"id": user_id},
    )


def approvals_of(url: str, user_id: Any) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM approval_requests ar"
        " JOIN tutor_qualifications q ON q.id = ar.subject_id"
        " WHERE ar.request_type = :type AND q.tutor_user_id = :id",
        {"type": APPROVAL_TYPE, "id": user_id},
    )
    return int(row.n)


def approved_notifications(url: str, user_id: Any) -> list[Any]:
    return [
        n
        for n in notifications_of(url, user_id)
        if n.notification_type == "staff.qualification_approved"
    ]


# --- A1, A3: the effect --------------------------------------------------------------


@pytest.mark.parametrize("reviewer", [OWNER, ADMINISTRATOR])
def test_a_reviewer_grants_a_subject(
    module_seeded_url, client, level, subject, tutor, reviewer
):
    actor = create_user(module_seeded_url, roles=(reviewer,))
    now = datetime.now(UTC).replace(microsecond=0)

    with frozen_clock(now):
        cookie = sign_in(client, actor.username)
        response = grant(client, cookie, tutor.id, subject["id"])

    assert code_of(response) == CREATED, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["subject_id"] == subject["id"]
    assert body["subject_name"] == subject["name"]
    assert body["education_level_name"] == level["name"]
    assert body["requested_at"] == body["decided_at"]
    assert datetime.fromisoformat(body["decided_at"]) == now
    assert body["version"] == 1
    [row] = rows_of(module_seeded_url, tutor.id)
    assert (row.status, row.decided_by_user_id) == ("approved", actor.id)
    assert row.created_at == row.decided_at == now
    # No approval request; one notification, the existing type and params.
    assert approvals_of(module_seeded_url, tutor.id) == 0
    [notification] = approved_notifications(module_seeded_url, tutor.id)
    assert notification.params == {
        "subject_name": subject["name"],
        "education_level_name": level["name"],
    }
    # The automatic create event, with the reviewer as the actor.
    [created] = events(
        module_seeded_url, record_type="tutor_qualifications", record_id=row.id
    )
    assert (created.action, created.actor_user_id) == ("create", actor.id)


# --- A2: the order -------------------------------------------------------------------


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN]
)
def test_other_roles_are_403_before_the_body(
    module_seeded_url, client, subject, tutor, role
):
    caller = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, caller.username)

    assert code_of(grant(client, cookie, tutor.id, subject["id"])) == DENIED
    # 403 before 422 (a bad body).
    assert code_of(post(client, cookie, grant_path(tutor.id), {})) == DENIED
    assert rows_of(module_seeded_url, tutor.id) == []


def test_the_tutor_themselves_is_403(module_seeded_url, client, subject, tutor):
    cookie = sign_in(client, tutor.username)

    assert code_of(grant(client, cookie, tutor.id, subject["id"])) == DENIED


def test_bad_bodies_are_422_before_the_tutor(client, owner_cookie):
    for body in ({}, {"subject_id": "x"}, {"subject_id": str(uuid.uuid4()), "x": 1}):
        response = post(client, owner_cookie, grant_path(uuid.uuid4()), body)
        assert code_of(response) == INVALID, body
        assert [e["path"][0] for e in response.json()["errors"]] == ["body"], body


def test_an_unknown_user_or_a_non_tutor_is_404(
    module_seeded_url, client, owner_cookie, subject
):
    staff = create_user(module_seeded_url, roles=(FINANCE_STAFF,))

    assert code_of(grant(client, owner_cookie, uuid.uuid4(), subject["id"])) == (
        NOT_FOUND
    )
    assert code_of(grant(client, owner_cookie, staff.id, subject["id"])) == NOT_FOUND
    # 404 before the subject check.
    assert code_of(grant(client, owner_cookie, staff.id, uuid.uuid4())) == NOT_FOUND


def test_a_self_grant_is_403_even_for_a_reviewer_who_tutors(
    module_seeded_url, client, subject
):
    reviewer = create_user(module_seeded_url, roles=(ADMINISTRATOR, TUTOR))
    cookie = sign_in(client, reviewer.username)

    assert code_of(grant(client, cookie, reviewer.id, subject["id"])) == DENIED
    # The self check comes before the subject check.
    assert code_of(grant(client, cookie, reviewer.id, uuid.uuid4())) == DENIED
    assert rows_of(module_seeded_url, reviewer.id) == []


def test_an_unknown_subject_is_422_on_subject_id(client, owner_cookie, tutor):
    response = grant(client, owner_cookie, tutor.id, uuid.uuid4())

    assert code_of(response) == INVALID
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", "subject_id"], "subject_unknown")
    ]


def test_an_inactive_subject_or_level_is_409(
    module_seeded_url, client, owner_cookie, subject, tutor
):
    assert (
        code_of(
            patch(
                client,
                owner_cookie,
                subject_path(subject["id"]),
                {"version": 1, "is_active": False},
            )
        )
        == OK
    )
    assert code_of(grant(client, owner_cookie, tutor.id, subject["id"])) == INACTIVE

    other_level = create_level(client, owner_cookie)
    other = create_subject(client, owner_cookie, other_level["id"])
    run_sql(
        module_seeded_url,
        "UPDATE education_levels SET is_active = false WHERE id = :id",
        {"id": other_level["id"]},
    )
    assert code_of(grant(client, owner_cookie, tutor.id, other["id"])) == INACTIVE
    assert rows_of(module_seeded_url, tutor.id) == []


def test_a_pending_or_approved_qualification_is_409(
    module_seeded_url, client, owner_cookie, level, subject, tutor
):
    tutor_cookie = sign_in(client, tutor.username)
    assert code_of(request(client, tutor_cookie, tutor.id, subject["id"])) == CREATED

    # A pending request is decided in the inbox, not by a grant.
    assert code_of(grant(client, owner_cookie, tutor.id, subject["id"])) == EXISTS

    other = create_subject(client, owner_cookie, level["id"])
    assert code_of(grant(client, owner_cookie, tutor.id, other["id"])) == CREATED
    assert code_of(grant(client, owner_cookie, tutor.id, other["id"])) == EXISTS
    assert len(rows_of(module_seeded_url, tutor.id)) == 2
    assert len(approved_notifications(module_seeded_url, tutor.id)) == 1


# --- A4: any account state -----------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "pending", "password_hash": None},
        {"state": "suspended"},
        {"state": "archived"},
    ],
    ids=["pending", "suspended", "archived"],
)
def test_any_account_state_is_accepted(
    module_seeded_url, client, owner_cookie, subject, overrides
):
    person = create_user(module_seeded_url, roles=(TUTOR,), **overrides)

    response = grant(client, owner_cookie, person.id, subject["id"])

    assert code_of(response) == CREATED, response.text
    assert len(approved_notifications(module_seeded_url, person.id)) == 1


# --- A5: concurrency -----------------------------------------------------------------


def race(url: str, calls: list[Any]) -> list[Any]:
    results: list[Any] = [None] * len(calls)
    start = threading.Barrier(len(calls))

    def run(index: int) -> None:
        with auth_client(url) as own_client:
            start.wait()
            results[index] = code_of(calls[index](own_client))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def test_concurrent_grants_give_one_201_and_one_409(
    module_seeded_url, owner_cookie, admin_cookie, subject, tutor
):
    results = race(
        module_seeded_url,
        [
            lambda c: grant(c, owner_cookie, tutor.id, subject["id"]),
            lambda c: grant(c, admin_cookie, tutor.id, subject["id"]),
        ],
    )

    assert sorted(results) == [CREATED, EXISTS]
    assert len(rows_of(module_seeded_url, tutor.id)) == 1
    assert len(approved_notifications(module_seeded_url, tutor.id)) == 1


def test_a_grant_racing_a_request_gives_one_201_and_one_409(
    module_seeded_url, client, owner_cookie, subject, tutor
):
    tutor_cookie = sign_in(client, tutor.username)

    results = race(
        module_seeded_url,
        [
            lambda c: grant(c, owner_cookie, tutor.id, subject["id"]),
            lambda c: request(c, tutor_cookie, tutor.id, subject["id"]),
        ],
    )

    assert sorted(results) == [CREATED, EXISTS]
    assert len(rows_of(module_seeded_url, tutor.id)) == 1


# --- A6: after a grant ---------------------------------------------------------------


def test_a_granted_subject_counts_everywhere_and_can_be_revoked(
    module_seeded_url, client, owner_cookie, subject
):
    person = new_tutor(module_seeded_url)
    response = grant(client, owner_cookie, person, subject["id"])
    assert code_of(response) == CREATED
    item = response.json()

    listed = get(client, owner_cookie, qualifications_path(person)).json()
    assert [(i["id"], i["status"]) for i in listed] == [(item["id"], "approved")]
    assert listed[0]["requested_at"] == listed[0]["decided_at"]
    detail = get(client, owner_cookie, f"/api/v1/tutors/{person}").json()
    assert [s["subject_id"] for s in detail["teaching_subjects"]] == [subject["id"]]
    subject_id = uuid.UUID(subject["id"])
    qualified = in_uow(
        module_seeded_url,
        lambda session: service.is_qualified(session, person, subject_id),
    )
    tutor_ids = in_uow(
        module_seeded_url,
        lambda session: service.qualified_tutor_ids(session, subject_id),
    )
    assert qualified and person in tutor_ids
    # #51 suggestions list the tutor (available all week, nothing booked).
    suggestions = get(
        client,
        owner_cookie,
        "/api/v1/tutor-suggestions",
        subject_id=subject["id"],
        starts_at=MONDAY_10.isoformat(),
        ends_at=MONDAY_11.isoformat(),
    ).json()
    assert [i["tutor"]["id"] for i in suggestions["items"]] == [str(person)]

    revoked = revoke(client, owner_cookie, person, item["id"], version=1)

    assert code_of(revoked) == OK, revoked.text
    assert revoked.json()["status"] == "revoked"
    assert (
        get(client, owner_cookie, f"/api/v1/tutors/{person}").json()[
            "teaching_subjects"
        ]
        == []
    )
