"""Tutor qualifications (#44 B, C, D, E1; revocation #198 B-D) with real
logins through the real app: requests and cancels by the tutor, decisions
through the approval inbox, revocations, notifications, teaching subjects and
the eligibility helpers."""

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.modules.staff import service
from tests.authz.assertions import assert_same_not_found
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
from tests.integration.registration_support import APPROVALS, decide

TUTORS = "/api/v1/tutors"
APPROVAL_TYPE = "staff.qualification"

OK = (200, None)
CREATED = (201, None)
DENIED = (403, "permission_denied")
NOT_FOUND = (404, "not_found")
EXISTS = (409, "qualification_exists")
INACTIVE = (409, "subject_inactive")
TRANSITION_INVALID = (409, "approval_transition_invalid")
INVALID = (422, "validation_error")


def qualifications_path(user_id: Any) -> str:
    return f"{TUTORS}/{user_id}/qualifications"


def cancel_path(user_id: Any, qualification_id: Any) -> str:
    return f"{qualifications_path(user_id)}/{qualification_id}/cancel"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(scope="module")
def admin(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ADMINISTRATOR,))


@pytest.fixture(scope="module")
def owner_cookie(client: TestClient, owner: CreatedUser) -> Any:
    return sign_in(client, owner.username)


@pytest.fixture(scope="module")
def admin_cookie(client: TestClient, admin: CreatedUser) -> Any:
    return sign_in(client, admin.username)


@pytest.fixture(scope="module")
def level(client: TestClient, owner_cookie: Any) -> dict[str, Any]:
    return create_level(client, owner_cookie)


@pytest.fixture
def subject(client: TestClient, owner_cookie: Any, level: dict[str, Any]) -> Any:
    """A new active subject of the module's level."""
    return create_subject(client, owner_cookie, level["id"])


@pytest.fixture
def tutor(module_seeded_url: str, client: TestClient) -> tuple[CreatedUser, Any]:
    """A new tutor and their session cookie."""
    person = create_user(module_seeded_url, roles=(TUTOR,))
    return person, sign_in(client, person.username)


def request(client: TestClient, cookie: Any, user_id: Any, subject_id: Any) -> Response:
    return post(
        client, cookie, qualifications_path(user_id), {"subject_id": subject_id}
    )


def cancel(
    client: TestClient, cookie: Any, user_id: Any, qualification_id: Any
) -> Response:
    return post(client, cookie, cancel_path(user_id, qualification_id), None)


def requested(
    client: TestClient, cookie: Any, user_id: Any, subject_id: Any
) -> dict[str, Any]:
    response = request(client, cookie, user_id, subject_id)
    assert code_of(response) == CREATED, response.text
    body: dict[str, Any] = response.json()
    return body


def listed(client: TestClient, cookie: Any, user_id: Any, **params: Any) -> list[Any]:
    response = get(client, cookie, qualifications_path(user_id), **params)
    assert code_of(response) == OK, response.text
    items: list[Any] = response.json()
    return items


def approval_of(url: str, qualification_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, status, reason, requester_user_id, resolved_by_user_id"
        " FROM approval_requests WHERE request_type = :type AND subject_id = :id",
        {"type": APPROVAL_TYPE, "id": qualification_id},
    )
    return row


def qualification_row(url: str, qualification_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT status, decided_at, decided_by_user_id, rejection_reason, version"
        " FROM tutor_qualifications WHERE id = :id",
        {"id": qualification_id},
    )
    return row


def notifications_of(url: str, user_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT notification_type, params FROM notifications"
        " WHERE recipient_user_id = :id ORDER BY created_at, id",
        {"id": user_id},
    )


def approve(
    client: TestClient, cookie: Any, url: str, qualification_id: Any
) -> Response:
    return decide(client, cookie, approval_of(url, qualification_id).id, "approve")


def reject(
    client: TestClient, cookie: Any, url: str, qualification_id: Any, reason: Any
) -> Response:
    body = None if reason is None else {"reason": reason}
    return decide(client, cookie, approval_of(url, qualification_id).id, "reject", body)


# --- B2, B3: requesting ---------------------------------------------------------------


def test_a_tutor_requests_a_qualification(
    module_seeded_url, client, level, subject, tutor
):
    person, cookie = tutor

    body = requested(client, cookie, person.id, subject["id"])

    assert set(body) == {
        "id",
        "subject_id",
        "subject_name",
        "education_level_id",
        "education_level_name",
        "subject_is_active",
        "status",
        "requested_at",
        "decided_at",
        "rejection_reason",
        "revoked_at",
        "revocation_reason",
        "version",
    }
    assert body["subject_id"] == subject["id"]
    assert body["subject_name"] == subject["name"]
    assert body["education_level_id"] == level["id"]
    assert body["education_level_name"] == level["name"]
    assert body["subject_is_active"] is True
    assert body["status"] == "pending"
    assert body["decided_at"] is None
    assert body["rejection_reason"] is None
    assert body["version"] == 1
    # One pending approval request, raised by the tutor, in the same commit.
    approval = approval_of(module_seeded_url, body["id"])
    assert approval.status == "pending"
    assert approval.requester_user_id == person.id
    # E1: audited with the tutor as the actor.
    [created] = events(
        module_seeded_url,
        record_type="tutor_qualifications",
        record_id=uuid.UUID(body["id"]),
    )
    assert created.action == "create"
    assert created.actor_user_id == person.id
    [approval_created] = events(
        module_seeded_url, record_type="approval_requests", record_id=approval.id
    )
    assert approval_created.actor_user_id == person.id


def test_a_duplicate_pending_or_approved_request_is_409(
    module_seeded_url, client, admin_cookie, subject, tutor
):
    person, cookie = tutor
    first = requested(client, cookie, person.id, subject["id"])

    assert code_of(request(client, cookie, person.id, subject["id"])) == EXISTS
    assert code_of(approve(client, admin_cookie, module_seeded_url, first["id"])) == OK
    assert code_of(request(client, cookie, person.id, subject["id"])) == EXISTS
    [count] = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM tutor_qualifications WHERE tutor_user_id = :id",
        {"id": person.id},
    )
    assert count.n == 1


def test_a_new_request_after_a_rejection_or_a_cancel_is_accepted(
    module_seeded_url, client, admin_cookie, subject, tutor
):
    person, cookie = tutor
    first = requested(client, cookie, person.id, subject["id"])
    assert (
        code_of(
            reject(client, admin_cookie, module_seeded_url, first["id"], "Not yet.")
        )
        == OK
    )

    second = requested(client, cookie, person.id, subject["id"])
    assert code_of(cancel(client, cookie, person.id, second["id"])) == OK

    third = requested(client, cookie, person.id, subject["id"])
    assert len({first["id"], second["id"], third["id"]}) == 3
    assert [item["status"] for item in listed(client, cookie, person.id)] == [
        "rejected",
        "cancelled",
        "pending",
    ]


def test_an_inactive_subject_or_level_is_409(
    module_seeded_url, client, owner_cookie, subject, tutor
):
    person, cookie = tutor
    deactivated = patch(
        client,
        owner_cookie,
        subject_path(subject["id"]),
        {"version": 1, "is_active": False},
    )
    assert code_of(deactivated) == OK
    assert code_of(request(client, cookie, person.id, subject["id"])) == INACTIVE

    # An active subject under an inactive level (set directly: the catalog
    # itself never leaves one so).
    other_level = create_level(client, owner_cookie)
    other = create_subject(client, owner_cookie, other_level["id"])
    run_sql(
        module_seeded_url,
        "UPDATE education_levels SET is_active = false WHERE id = :id",
        {"id": other_level["id"]},
    )
    assert code_of(request(client, cookie, person.id, other["id"])) == INACTIVE
    assert listed(client, cookie, person.id) == []


def test_an_unknown_subject_and_bad_bodies_are_422(client, tutor):
    person, cookie = tutor

    unknown = request(client, cookie, person.id, str(uuid.uuid4()))
    assert code_of(unknown) == INVALID
    assert [e["path"] for e in unknown.json()["errors"]] == [["body", "subject_id"]]
    for body in (
        {},
        {"subject_id": "x"},
        {"subject_id": str(uuid.uuid4()), "extra": 1},
    ):
        response = post(client, cookie, qualifications_path(person.id), body)
        assert code_of(response) == INVALID, body


def test_concurrent_duplicate_requests_give_one_201_and_one_409(
    module_seeded_url, subject, tutor
):
    person, cookie = tutor
    results: list[Any] = [None, None]
    start = threading.Barrier(2)

    def run(index: int) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results[index] = code_of(
                request(own_client, cookie, person.id, subject["id"])
            )

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [CREATED, EXISTS]
    [count] = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM approval_requests ar"
        " JOIN tutor_qualifications q ON q.id = ar.subject_id"
        " WHERE q.tutor_user_id = :id",
        {"id": person.id},
    )
    assert count.n == 1


def test_only_the_tutor_requests_for_themselves(
    module_seeded_url, client, admin_cookie, subject, tutor
):
    person, cookie = tutor
    other = create_user(module_seeded_url, roles=(TUTOR,))
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))

    # Another tutor can't see this one: indistinguishable from a missing tutor.
    assert_same_not_found(
        request(client, cookie, other.id, subject["id"]),
        request(client, cookie, uuid.uuid4(), subject["id"]),
    )
    # An Administrator (or Coordinator) sees the tutor but doesn't request.
    assert code_of(request(client, admin_cookie, person.id, subject["id"])) == DENIED
    coordinator_cookie = sign_in(client, coordinator.username)
    assert (
        code_of(request(client, coordinator_cookie, person.id, subject["id"])) == DENIED
    )
    [count] = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM tutor_qualifications WHERE subject_id = :id",
        {"id": subject["id"]},
    )
    assert count.n == 0


# --- B4: cancelling -------------------------------------------------------------------


def test_cancel_then_cancel_again(
    module_seeded_url, client, admin_cookie, subject, tutor
):
    person, cookie = tutor
    item = requested(client, cookie, person.id, subject["id"])

    assert code_of(cancel(client, admin_cookie, person.id, item["id"])) == DENIED
    response = cancel(client, cookie, person.id, item["id"])

    assert code_of(response) == OK
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["decided_at"] is not None
    assert body["version"] == 2
    assert approval_of(module_seeded_url, item["id"]).status == "cancelled"
    assert code_of(cancel(client, cookie, person.id, item["id"])) == TRANSITION_INVALID
    # E1: the status change is audited with the tutor as the actor.
    updates = [
        e
        for e in events(
            module_seeded_url,
            record_type="tutor_qualifications",
            record_id=uuid.UUID(item["id"]),
        )
        if e.action == "update"
    ]
    assert [e.actor_user_id for e in updates] == [person.id]


def test_a_decided_or_unknown_qualification_cannot_be_cancelled(
    module_seeded_url, client, admin_cookie, subject, tutor
):
    person, cookie = tutor
    item = requested(client, cookie, person.id, subject["id"])
    assert code_of(approve(client, admin_cookie, module_seeded_url, item["id"])) == OK

    assert code_of(cancel(client, cookie, person.id, item["id"])) == TRANSITION_INVALID
    assert code_of(cancel(client, cookie, person.id, uuid.uuid4())) == NOT_FOUND
    # Another tutor's qualification id under this tutor: 404.
    other = create_user(module_seeded_url, roles=(TUTOR,))
    other_cookie = sign_in(client, other.username)
    assert code_of(cancel(client, other_cookie, other.id, item["id"])) == NOT_FOUND
    assert qualification_row(module_seeded_url, item["id"]).status == "approved"


# --- B5: listing ----------------------------------------------------------------------


def test_list_visibility(module_seeded_url, client, admin_cookie, subject, tutor):
    person, cookie = tutor
    item = requested(client, cookie, person.id, subject["id"])
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
    finance = create_user(module_seeded_url, roles=(FINANCE_STAFF,))
    other = create_user(module_seeded_url, roles=(TUTOR,))

    for viewer in (cookie, admin_cookie, sign_in(client, coordinator.username)):
        assert [i["id"] for i in listed(client, viewer, person.id)] == [item["id"]]
    finance_response = get(
        client, sign_in(client, finance.username), qualifications_path(person.id)
    )
    assert code_of(finance_response) == DENIED
    other_cookie = sign_in(client, other.username)
    assert_same_not_found(
        get(client, other_cookie, qualifications_path(person.id)),
        get(client, other_cookie, qualifications_path(uuid.uuid4())),
    )


def test_the_list_order_and_status_filter(
    module_seeded_url, client, owner_cookie, admin_cookie, tutor
):
    person, cookie = tutor
    first_level = create_level(client, owner_cookie, sort_order=1)
    late_level = create_level(client, owner_cookie, sort_order=900)
    b = create_subject(
        client, owner_cookie, first_level["id"], f"B {uuid.uuid4().hex[:6]}"
    )
    a = create_subject(
        client, owner_cookie, first_level["id"], f"a {uuid.uuid4().hex[:6]}"
    )
    late = create_subject(client, owner_cookie, late_level["id"])
    late_item = requested(client, cookie, person.id, late["id"])
    b_item = requested(client, cookie, person.id, b["id"])
    a_item = requested(client, cookie, person.id, a["id"])
    assert code_of(approve(client, admin_cookie, module_seeded_url, b_item["id"])) == OK

    assert [i["id"] for i in listed(client, cookie, person.id)] == [
        a_item["id"],
        b_item["id"],
        late_item["id"],
    ]
    assert [i["id"] for i in listed(client, cookie, person.id, status="approved")] == [
        b_item["id"]
    ]
    assert listed(client, cookie, person.id, status="rejected") == []
    response = get(client, cookie, qualifications_path(person.id), status="expired")
    assert code_of(response) == INVALID


# --- C: decisions through the approval inbox ------------------------------------------


def test_approving_mirrors_the_status_and_notifies_the_tutor(
    module_seeded_url, client, admin, admin_cookie, level, subject, tutor
):
    person, cookie = tutor
    item = requested(client, cookie, person.id, subject["id"])
    approval_id = approval_of(module_seeded_url, item["id"]).id

    # The inbox item and its summary (C1).
    shown = get(client, admin_cookie, f"{APPROVALS}/{approval_id}")
    assert code_of(shown) == OK
    assert shown.json()["request_type"] == APPROVAL_TYPE
    assert shown.json()["allowed_actions"] == ["approve", "reject"]
    [display_name] = run_sql(
        module_seeded_url,
        "SELECT display_name FROM users WHERE id = :id",
        {"id": person.id},
    )
    assert shown.json()["subject"] == {
        "tutor_display_name": display_name.display_name,
        "subject_name": subject["name"],
        "education_level_name": level["name"],
    }

    assert code_of(approve(client, admin_cookie, module_seeded_url, item["id"])) == OK

    row = qualification_row(module_seeded_url, item["id"])
    assert row.status == "approved"
    assert row.decided_at is not None
    assert row.decided_by_user_id == admin.id
    assert row.rejection_reason is None
    assert approval_of(module_seeded_url, item["id"]).status == "approved"
    [notification] = notifications_of(module_seeded_url, person.id)
    assert notification.notification_type == "staff.qualification_approved"
    assert notification.params == {
        "subject_name": subject["name"],
        "education_level_name": level["name"],
    }
    # E1: the update is audited with the reviewer as the actor.
    updates = [
        e
        for e in events(
            module_seeded_url,
            record_type="tutor_qualifications",
            record_id=uuid.UUID(item["id"]),
        )
        if e.action == "update"
    ]
    assert [e.actor_user_id for e in updates] == [admin.id]
    assert updates[0].changes["status"] == {"old": "pending", "new": "approved"}
    # Request changes is not supported for this type.
    again = requested(
        client,
        cookie,
        person.id,
        create_subject(client, admin_cookie, level["id"])["id"],
    )
    changes = decide(
        client,
        admin_cookie,
        approval_of(module_seeded_url, again["id"]).id,
        "request-changes",
        {"reason": "More detail."},
    )
    assert code_of(changes) == (409, "approval_action_unsupported")


def test_rejecting_needs_a_reason_and_notifies_without_it(
    module_seeded_url, client, owner, owner_cookie, level, subject, tutor
):
    person, cookie = tutor
    item = requested(client, cookie, person.id, subject["id"])

    assert (
        code_of(reject(client, owner_cookie, module_seeded_url, item["id"], None))
        == INVALID
    )
    assert (
        code_of(reject(client, owner_cookie, module_seeded_url, item["id"], "  "))
        == INVALID
    )
    assert qualification_row(module_seeded_url, item["id"]).status == "pending"
    assert notifications_of(module_seeded_url, person.id) == []

    response = reject(
        client, owner_cookie, module_seeded_url, item["id"], " Missing degree. "
    )

    assert code_of(response) == OK
    row = qualification_row(module_seeded_url, item["id"])
    assert row.status == "rejected"
    assert row.rejection_reason == "Missing degree."
    assert row.decided_by_user_id == owner.id
    assert approval_of(module_seeded_url, item["id"]).reason == "Missing degree."
    [notification] = notifications_of(module_seeded_url, person.id)
    assert notification.notification_type == "staff.qualification_rejected"
    assert notification.params == {
        "subject_name": subject["name"],
        "education_level_name": level["name"],
    }
    [shown] = listed(client, cookie, person.id)
    assert shown["status"] == "rejected"
    assert shown["rejection_reason"] == "Missing degree."


def test_a_concurrent_approval_and_rejection_decide_once(
    module_seeded_url, admin_cookie, subject, tutor
):
    person, cookie = tutor
    with auth_client(module_seeded_url) as setup:
        item = requested(setup, cookie, person.id, subject["id"])
    approval_id = approval_of(module_seeded_url, item["id"]).id
    calls = (
        lambda c: decide(c, admin_cookie, approval_id, "approve"),
        lambda c: decide(c, admin_cookie, approval_id, "reject", {"reason": "No."}),
    )
    results: list[Any] = [None, None]
    start = threading.Barrier(2)

    def run(index: int) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results[index] = code_of(calls[index](own_client))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [OK, TRANSITION_INVALID]
    status = "approved" if results[0] == OK else "rejected"
    assert qualification_row(module_seeded_url, item["id"]).status == status
    assert approval_of(module_seeded_url, item["id"]).status == status
    assert len(notifications_of(module_seeded_url, person.id)) == 1


def test_who_sees_and_decides_qualification_requests(
    module_seeded_url, client, subject, tutor
):
    person, cookie = tutor
    item = requested(client, cookie, person.id, subject["id"])
    approval_id = approval_of(module_seeded_url, item["id"]).id
    path = f"{APPROVALS}/{approval_id}"

    for role in (ACADEMIC_COORDINATOR, FINANCE_STAFF):
        viewer = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)
        assert code_of(get(client, viewer, path)) == NOT_FOUND, role
        assert code_of(decide(client, viewer, approval_id, "approve")) == NOT_FOUND, (
            role
        )
    # The tutor doesn't hold qualification.review: their inbox is empty.
    inbox = get(client, cookie, APPROVALS, status="all")
    assert code_of(inbox) == OK
    assert inbox.json()["items"] == []
    assert code_of(get(client, cookie, path)) == NOT_FOUND
    # An Administrator sees it in the list.
    admin = sign_in(
        client, create_user(module_seeded_url, roles=(ADMINISTRATOR,)).username
    )
    listing = get(client, admin, APPROVALS, request_type=APPROVAL_TYPE, limit=100)
    assert str(approval_id) in [i["id"] for i in listing.json()["items"]]
    assert qualification_row(module_seeded_url, item["id"]).status == "pending"


# --- D: only approved counts ----------------------------------------------------------


def eligibility(url: str, tutor_id: uuid.UUID, subject_id: Any) -> tuple[Any, ...]:
    subject_uuid = uuid.UUID(str(subject_id))

    async def _read(session: Any) -> tuple[Any, ...]:
        return (
            await service.is_qualified(session, tutor_id, subject_uuid),
            subject_uuid in await service.approved_subject_ids(session, tutor_id),
            tutor_id in await service.qualified_tutor_ids(session, subject_uuid),
        )

    return in_uow(url, _read)


def teaching(client: TestClient, cookie: Any, user_id: Any) -> list[Any]:
    response = get(client, cookie, f"{TUTORS}/{user_id}")
    assert code_of(response) == OK, response.text
    subjects: list[Any] = response.json()["teaching_subjects"]
    return subjects


def test_only_approved_qualifications_count(
    module_seeded_url, client, owner_cookie, admin_cookie, level, tutor
):
    person, cookie = tutor
    pending, rejected, cancelled, approved = (
        create_subject(client, owner_cookie, level["id"]) for _ in range(4)
    )
    requested(client, cookie, person.id, pending["id"])
    r = requested(client, cookie, person.id, rejected["id"])
    assert (
        code_of(reject(client, admin_cookie, module_seeded_url, r["id"], "No.")) == OK
    )
    c = requested(client, cookie, person.id, cancelled["id"])
    assert code_of(cancel(client, cookie, person.id, c["id"])) == OK
    a = requested(client, cookie, person.id, approved["id"])

    for not_counted in (pending, rejected, cancelled, approved):
        assert eligibility(module_seeded_url, person.id, not_counted["id"]) == (
            False,
            False,
            False,
        )
    assert teaching(client, cookie, person.id) == []

    assert code_of(approve(client, admin_cookie, module_seeded_url, a["id"])) == OK

    assert eligibility(module_seeded_url, person.id, approved["id"]) == (
        True,
        True,
        True,
    )
    for not_counted in (pending, rejected, cancelled):
        assert eligibility(module_seeded_url, person.id, not_counted["id"]) == (
            False,
            False,
            False,
        )
    expected = {
        "subject_id": approved["id"],
        "subject_name": approved["name"],
        "education_level_id": level["id"],
        "education_level_name": level["name"],
        "subject_is_active": True,
    }
    assert teaching(client, cookie, person.id) == [expected]
    assert teaching(client, admin_cookie, person.id) == [expected]

    # A deactivated subject keeps its approved qualification (D2).
    deactivated = patch(
        client,
        owner_cookie,
        subject_path(approved["id"]),
        {"version": 1, "is_active": False},
    )
    assert code_of(deactivated) == OK
    assert teaching(client, cookie, person.id) == [
        {**expected, "subject_is_active": False}
    ]
    assert eligibility(module_seeded_url, person.id, approved["id"]) == (
        True,
        True,
        True,
    )


# --- #198: revoking an approved qualification ---------------------------------------

NOT_APPROVED = (409, "qualification_not_approved")
VERSION_CONFLICT = (409, "version_conflict")


def revoke_path(user_id: Any, qualification_id: Any) -> str:
    return f"{qualifications_path(user_id)}/{qualification_id}/revoke"


def revoke(
    client: TestClient,
    cookie: Any,
    user_id: Any,
    qualification_id: Any,
    *,
    reason: Any = "Outdated certificate.",
    version: Any = 2,
) -> Response:
    return post(
        client,
        cookie,
        revoke_path(user_id, qualification_id),
        {"reason": reason, "version": version},
    )


def approved_item(
    client: TestClient,
    tutor_cookie: Any,
    reviewer_cookie: Any,
    url: str,
    user_id: Any,
    subject_id: Any,
) -> dict[str, Any]:
    """A new approved qualification (version 2)."""
    item = requested(client, tutor_cookie, user_id, subject_id)
    assert code_of(approve(client, reviewer_cookie, url, item["id"])) == OK
    [shown] = [
        i for i in listed(client, tutor_cookie, user_id) if i["id"] == item["id"]
    ]
    assert shown["status"] == "approved" and shown["version"] == 2
    return shown


def revocation_row(url: str, qualification_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT status, decided_at, decided_by_user_id, rejection_reason,"
        " revoked_at, revoked_by_user_id, revocation_reason, version"
        " FROM tutor_qualifications WHERE id = :id",
        {"id": qualification_id},
    )
    return row


def revoked_notifications(url: str, user_id: Any) -> list[Any]:
    return [
        n
        for n in notifications_of(url, user_id)
        if n.notification_type == "staff.qualification_revoked"
    ]


def audit_updates(url: str, qualification_id: Any) -> list[Any]:
    return [
        e
        for e in events(
            url,
            record_type="tutor_qualifications",
            record_id=uuid.UUID(str(qualification_id)),
        )
        if e.action == "update"
    ]


@pytest.mark.parametrize("reviewer", [OWNER, ADMINISTRATOR])
def test_a_reviewer_revokes_an_approved_qualification(
    module_seeded_url, client, owner_cookie, level, subject, tutor, reviewer
):
    person, cookie = tutor
    actor = create_user(module_seeded_url, roles=(reviewer,))
    actor_cookie = sign_in(client, actor.username)
    item = approved_item(
        client, cookie, owner_cookie, module_seeded_url, person.id, subject["id"]
    )
    approved = revocation_row(module_seeded_url, item["id"])

    response = revoke(
        client, actor_cookie, person.id, item["id"], reason="  Outdated certificate.  "
    )

    assert code_of(response) == OK, response.text
    body = response.json()
    assert body["id"] == item["id"]
    assert body["status"] == "revoked"
    assert body["revocation_reason"] == "Outdated certificate."
    assert body["revoked_at"] is not None
    assert body["rejection_reason"] is None
    assert body["decided_at"] == item["decided_at"]
    assert body["version"] == 3
    row = revocation_row(module_seeded_url, item["id"])
    assert row.status == "revoked"
    assert row.revoked_by_user_id == actor.id
    assert row.revocation_reason == "Outdated certificate."
    # The approval decision is kept: approved by X on D1, revoked by Y on D2.
    assert row.decided_at == approved.decided_at
    assert row.decided_by_user_id == approved.decided_by_user_id
    assert row.rejection_reason is None
    # B4: one notification, without the reason.
    [notification] = revoked_notifications(module_seeded_url, person.id)
    assert notification.params == {
        "subject_name": subject["name"],
        "education_level_name": level["name"],
    }
    # B6: the approval request stays approved.
    assert approval_of(module_seeded_url, item["id"]).status == "approved"
    # D2: audited with the reviewer as the actor.
    revocation = audit_updates(module_seeded_url, item["id"])[-1]
    assert revocation.actor_user_id == actor.id
    assert revocation.changes["status"] == {"old": "approved", "new": "revoked"}
    assert revocation.changes["revocation_reason"] == {
        "old": None,
        "new": "Outdated certificate.",
    }
    assert revocation.changes["revoked_by_user_id"]["new"] == str(actor.id)
    assert revocation.changes["revoked_at"]["old"] is None
    assert revocation.changes["revoked_at"]["new"] is not None
    # B3: the tutor and a viewer see it; the status filter finds it.
    for viewer in (cookie, actor_cookie):
        [shown] = listed(client, viewer, person.id, status="revoked")
        assert shown == body
    assert listed(client, cookie, person.id, status="approved") == []


def test_only_reviewers_revoke(module_seeded_url, client, owner_cookie, subject, tutor):
    person, cookie = tutor
    item = approved_item(
        client, cookie, owner_cookie, module_seeded_url, person.id, subject["id"]
    )
    for role in (ACADEMIC_COORDINATOR, FINANCE_STAFF):
        viewer = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)
        assert code_of(revoke(client, viewer, person.id, item["id"])) == DENIED, role
    # The tutor themselves can't withdraw an approved qualification.
    assert code_of(revoke(client, cookie, person.id, item["id"])) == DENIED
    # 403 comes before 422.
    assert code_of(revoke(client, cookie, person.id, item["id"], reason="")) == DENIED

    assert revocation_row(module_seeded_url, item["id"]).status == "approved"
    assert revoked_notifications(module_seeded_url, person.id) == []


def test_an_unknown_or_another_tutors_qualification_is_404(
    module_seeded_url, client, owner_cookie, subject, tutor
):
    person, cookie = tutor
    item = approved_item(
        client, cookie, owner_cookie, module_seeded_url, person.id, subject["id"]
    )
    other = create_user(module_seeded_url, roles=(TUTOR,))

    assert code_of(revoke(client, owner_cookie, person.id, uuid.uuid4())) == NOT_FOUND
    assert_same_not_found(
        revoke(client, owner_cookie, other.id, item["id"]),
        revoke(client, owner_cookie, other.id, uuid.uuid4()),
    )
    assert revocation_row(module_seeded_url, item["id"]).status == "approved"


def test_only_an_approved_qualification_can_be_revoked(
    module_seeded_url, client, owner_cookie, level, tutor
):
    person, cookie = tutor
    pending, rejected, cancelled, revoked = (
        create_subject(client, owner_cookie, level["id"]) for _ in range(4)
    )
    p = requested(client, cookie, person.id, pending["id"])
    r = requested(client, cookie, person.id, rejected["id"])
    assert (
        code_of(reject(client, owner_cookie, module_seeded_url, r["id"], "No.")) == OK
    )
    c = requested(client, cookie, person.id, cancelled["id"])
    assert code_of(cancel(client, cookie, person.id, c["id"])) == OK
    v = approved_item(
        client, cookie, owner_cookie, module_seeded_url, person.id, revoked["id"]
    )
    assert code_of(revoke(client, owner_cookie, person.id, v["id"])) == OK
    before = notifications_of(module_seeded_url, person.id)

    for item in listed(client, cookie, person.id):
        updates = len(audit_updates(module_seeded_url, item["id"]))
        response = revoke(
            client, owner_cookie, person.id, item["id"], version=item["version"]
        )
        assert code_of(response) == NOT_APPROVED, item["status"]
        after = listed(client, cookie, person.id)
        assert next(i for i in after if i["id"] == item["id"]) == item
        assert len(audit_updates(module_seeded_url, item["id"])) == updates
    assert {i["status"] for i in listed(client, cookie, person.id)} == {
        "pending",
        "rejected",
        "cancelled",
        "revoked",
    }
    assert notifications_of(module_seeded_url, person.id) == before
    assert revocation_row(module_seeded_url, p["id"]).status == "pending"


def test_a_stale_version_and_bad_bodies(
    module_seeded_url, client, owner_cookie, subject, tutor
):
    person, cookie = tutor
    item = approved_item(
        client, cookie, owner_cookie, module_seeded_url, person.id, subject["id"]
    )
    path = revoke_path(person.id, item["id"])

    assert (
        code_of(revoke(client, owner_cookie, person.id, item["id"], version=1))
        == VERSION_CONFLICT
    )
    for body in (
        {"reason": "", "version": 2},
        {"reason": "   ", "version": 2},
        {"reason": "x" * 1001, "version": 2},
        {"reason": "Ok."},
        {"version": 2},
        {"reason": "Ok.", "version": 0},
        {"reason": "Ok.", "version": 2, "status": "revoked"},
    ):
        assert code_of(post(client, owner_cookie, path, body)) == INVALID, body
    # 422 on the body comes before the 404.
    assert (
        code_of(revoke(client, owner_cookie, person.id, uuid.uuid4(), reason=""))
        == INVALID
    )
    assert revocation_row(module_seeded_url, item["id"]).status == "approved"
    assert revoked_notifications(module_seeded_url, person.id) == []
    # 1000 characters is accepted.
    response = revoke(client, owner_cookie, person.id, item["id"], reason="x" * 1000)
    assert code_of(response) == OK
    assert len(response.json()["revocation_reason"]) == 1000


def test_concurrent_revokes_give_one_200_one_409_and_one_notification(
    module_seeded_url, owner_cookie, admin_cookie, subject, tutor
):
    person, cookie = tutor
    with auth_client(module_seeded_url) as setup:
        item = approved_item(
            setup, cookie, owner_cookie, module_seeded_url, person.id, subject["id"]
        )
    cookies = (owner_cookie, admin_cookie)
    results: list[Any] = [None, None]
    start = threading.Barrier(2)

    def run(index: int) -> None:
        with auth_client(module_seeded_url) as own_client:
            start.wait()
            results[index] = code_of(
                revoke(own_client, cookies[index], person.id, item["id"])
            )

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [OK, VERSION_CONFLICT]
    assert len(revoked_notifications(module_seeded_url, person.id)) == 1
    assert revocation_row(module_seeded_url, item["id"]).version == 3


def test_a_revoked_qualification_stops_counting_and_can_be_requested_again(
    module_seeded_url, client, owner_cookie, admin_cookie, level, subject, tutor
):
    person, cookie = tutor
    kept = create_subject(client, owner_cookie, level["id"])
    item = approved_item(
        client, cookie, owner_cookie, module_seeded_url, person.id, subject["id"]
    )
    approved_item(
        client, cookie, owner_cookie, module_seeded_url, person.id, kept["id"]
    )
    assert eligibility(module_seeded_url, person.id, subject["id"]) == (
        True,
        True,
        True,
    )

    assert code_of(revoke(client, admin_cookie, person.id, item["id"])) == OK

    # C1: it stops counting at once; the other approved one still counts.
    assert eligibility(module_seeded_url, person.id, subject["id"]) == (
        False,
        False,
        False,
    )
    assert eligibility(module_seeded_url, person.id, kept["id"]) == (True, True, True)
    for viewer in (cookie, admin_cookie):
        assert [s["subject_id"] for s in teaching(client, viewer, person.id)] == [
            kept["id"]
        ]
    # C2: the tutor requests the same subject again; the revoked row stays.
    again = requested(client, cookie, person.id, subject["id"])
    assert again["status"] == "pending"
    statuses = {
        i["id"]: i["status"]
        for i in listed(client, cookie, person.id)
        if i["subject_id"] == subject["id"]
    }
    assert statuses == {item["id"]: "revoked", again["id"]: "pending"}
    # Approving the new request makes the subject count again.
    assert code_of(approve(client, admin_cookie, module_seeded_url, again["id"])) == OK
    assert eligibility(module_seeded_url, person.id, subject["id"]) == (
        True,
        True,
        True,
    )


def test_an_ex_tutors_qualification_and_an_inactive_subject_can_be_revoked(
    module_seeded_url, client, owner_cookie, subject, tutor
):
    person, cookie = tutor
    item = approved_item(
        client, cookie, owner_cookie, module_seeded_url, person.id, subject["id"]
    )
    deactivated = patch(
        client,
        owner_cookie,
        subject_path(subject["id"]),
        {"version": 1, "is_active": False},
    )
    assert code_of(deactivated) == OK
    run_sql(
        module_seeded_url,
        "DELETE FROM user_roles WHERE user_id = :id",
        {"id": person.id},
    )

    response = revoke(client, owner_cookie, person.id, item["id"])

    assert code_of(response) == OK, response.text
    assert revocation_row(module_seeded_url, item["id"]).status == "revoked"
