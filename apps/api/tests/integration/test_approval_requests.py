"""The approval inbox core through the real app (#40 B3-B5, C1-C5, E1).

A fake type, ``test.sample`` (review permission ``payroll.finalize``: only
Owners and Administrators it was delegated to), sits next to the real
``identity.registration``. Its handler records every call and can be told to
fail; the ``sample_type`` fixture registers it and removes it afterwards
(``_docs/testing-guidelines.md``).
"""

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit_event
from app.core.authz import Principal
from app.core.errors import AppError
from app.core.pagination import OffsetParams
from app.modules.administration import service as administration
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import reset_rate_limits, run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow, signed_in
from tests.integration.registration_support import (
    APPROVALS,
    approve,
    decide,
    list_requests,
    register,
    reject,
    request_row,
    roles_of,
    unique_username,
)

SAMPLE = "test.sample"
SAMPLE_PERMISSION = "payroll.finalize"
OK = (200, None)
NOT_FOUND = (404, "not_found")
TRANSITION_INVALID = (409, "approval_transition_invalid")
UNSUPPORTED = (409, "approval_action_unsupported")
VALIDATION = (422, "validation_error")


class SampleRefusedError(AppError):
    status = 409
    code = "sample_refused"

    def __init__(self) -> None:
        super().__init__()


@dataclass
class SampleHandler:
    """Records calls; ``fail`` makes every action write an audit event and
    then raise, to prove the action shares the decision's transaction."""

    request_type: str = SAMPLE
    review_permission: str = SAMPLE_PERMISSION
    supports_request_changes: bool = True
    reason_required_on_reject: bool = True
    fail: bool = False
    calls: list[tuple[str, uuid.UUID, str | None]] = field(default_factory=list)

    async def _act(
        self, session: AsyncSession, name: str, subject_id: uuid.UUID, reason: Any
    ) -> None:
        self.calls.append((name, subject_id, reason))
        if self.fail:
            record_audit_event(session, action="test.sample_side_effect")
            await session.flush()
            raise SampleRefusedError()

    async def approve(self, session, request, principal) -> None:  # type: ignore[no-untyped-def]
        await self._act(session, "approve", request.subject_id, None)

    async def reject(self, session, request, principal, reason) -> None:  # type: ignore[no-untyped-def]
        await self._act(session, "reject", request.subject_id, reason)

    async def request_changes(self, session, request, principal, reason) -> None:  # type: ignore[no-untyped-def]
        await self._act(session, "request_changes", request.subject_id, reason)

    async def summaries(self, session, subject_ids) -> dict[uuid.UUID, dict[str, Any]]:  # type: ignore[no-untyped-def]
        # One statement, like a real type's join.
        await session.execute(text("SELECT 1"))
        return {
            subject_id: {"label": f"Sample {subject_id}"} for subject_id in subject_ids
        }


@pytest.fixture
def sample_type() -> Iterator[SampleHandler]:
    handler = SampleHandler()
    administration.register_approval_handler(handler)
    yield handler
    administration.unregister_approval_handler(SAMPLE)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


def create_sample(
    url: str,
    requester: CreatedUser | None = None,
    *,
    priority: str = "normal",
    assignee: CreatedUser | None = None,
) -> uuid.UUID:
    async def _create(session: AsyncSession) -> uuid.UUID:
        request = await administration.create_approval_request(
            session,
            request_type=SAMPLE,
            subject_id=uuid.uuid7(),
            requester_user_id=requester.id if requester else None,
            priority=priority,
            assignee_user_id=assignee.id if assignee else None,
        )
        return request.id

    return in_uow(url, _create)


def approval(url: str, approval_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT status, reason, resolved_at, resolved_by_user_id, version"
        " FROM approval_requests WHERE id = :id",
        {"id": approval_id},
    )
    return row


def registration(url: str, client: TestClient) -> Any:
    username = unique_username()
    reset_rate_limits(url)
    assert code_of(register(client, username=username)) == (202, None)
    return request_row(url, username)


def assign(
    client: TestClient,
    cookie: Any,
    approval_id: Any,
    assignee_id: Any,
    version: Any,
) -> Any:
    """``POST …/{approval_id}/assign`` (#118 A1)."""
    client.cookies.clear()
    return client.post(
        f"{APPROVALS}/{approval_id}/assign",
        headers=signed_headers(cookie),
        json={
            "assignee_user_id": str(assignee_id) if assignee_id else None,
            "version": version,
        },
    )


def assignee_options(client: TestClient, cookie: Any, approval_id: Any) -> Any:
    client.cookies.clear()
    return client.get(
        f"{APPROVALS}/{approval_id}/assignee-options", headers=cookie_header(cookie)
    )


def assignee_of(url: str, approval_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT assignee_user_id FROM approval_requests WHERE id = :id",
        {"id": approval_id},
    )
    return row.assignee_user_id


def assigned_notifications(url: str, user_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT params FROM notifications WHERE recipient_user_id = :u"
        " AND notification_type = 'administration.approval_assigned'",
        {"u": user_id},
    )


def get(client: TestClient, cookie: Any, approval_id: Any) -> Any:
    client.cookies.clear()
    return client.get(f"{APPROVALS}/{approval_id}", headers=cookie_header(cookie))


def listed_ids(client: TestClient, cookie: Any, **params: Any) -> list[str]:
    params.setdefault("request_type", None)
    params.setdefault("limit", 100)
    response = list_requests(client, cookie, **params)
    assert code_of(response) == OK, response.text
    return [item["id"] for item in response.json()["items"]]


# --- B5: the handler runs in the decision's transaction ------------------------------


def test_a_failing_handler_leaves_the_request_pending_and_writes_nothing(
    module_seeded_url, client, clock, owner, sample_type
):
    approval_id = create_sample(module_seeded_url, owner)
    sample_type.fail = True
    cookie = signed_in(client, owner.username)

    for action, body in (
        ("approve", None),
        ("reject", {"reason": "No."}),
        ("request-changes", {"reason": "Fix it."}),
    ):
        assert code_of(decide(client, cookie, approval_id, action, body)) == (
            409,
            "sample_refused",
        )

    assert [call[0] for call in sample_type.calls] == [
        "approve",
        "reject",
        "request_changes",
    ]
    row = approval(module_seeded_url, approval_id)
    assert (row.status, row.reason, row.resolved_at, row.version) == (
        "pending",
        None,
        None,
        1,
    )
    assert [e.action for e in events(module_seeded_url, record_id=approval_id)] == [
        "create"
    ]
    assert events(module_seeded_url, action="test.sample_side_effect") == []


def test_request_changes_then_resubmit_then_approve(
    module_seeded_url, client, clock, owner, sample_type
):
    approval_id = create_sample(module_seeded_url, owner)
    cookie = signed_in(client, owner.username)

    response = decide(
        client, cookie, approval_id, "request-changes", {"reason": "  Add the date. "}
    )

    assert code_of(response) == OK
    body = response.json()
    assert (body["status"], body["reason"], body["resolved_at"]) == (
        "changes_requested",
        "Add the date.",
        None,
    )
    assert body["allowed_actions"] == ["reject"]
    assert code_of(approve(client, cookie, approval_id)) == TRANSITION_INVALID

    in_uow(
        module_seeded_url,
        lambda session: administration.resubmit_approval_request(
            session, request_id=approval_id, by_user_id=owner.id
        ),
        actor_id=owner.id,
    )
    row = approval(module_seeded_url, approval_id)
    assert (row.status, row.reason) == ("pending", None)

    approved = approve(client, cookie, approval_id)
    assert code_of(approved) == OK
    assert approved.json()["status"] == "approved"
    assert approved.json()["version"] == 4
    assert [c[0] for c in sample_type.calls] == ["request_changes", "approve"]
    assert sample_type.calls[0][2] == "Add the date."
    updates = events(module_seeded_url, record_id=approval_id, action="update")
    assert [e.changes["status"]["new"] for e in updates] == [
        "changes_requested",
        "pending",
        "approved",
    ]


def test_cancel_is_final(module_seeded_url, client, clock, owner, sample_type):
    approval_id = create_sample(module_seeded_url, owner)
    in_uow(
        module_seeded_url,
        lambda session: administration.cancel_approval_request(
            session, request_id=approval_id, by_user_id=owner.id
        ),
        actor_id=owner.id,
    )
    row = approval(module_seeded_url, approval_id)
    assert (row.status, row.resolved_by_user_id) == ("cancelled", owner.id)
    assert row.resolved_at is not None
    cookie = signed_in(client, owner.username)

    assert code_of(approve(client, cookie, approval_id)) == TRANSITION_INVALID
    assert (
        code_of(decide(client, cookie, approval_id, "reject", {"reason": "x"}))
        == TRANSITION_INVALID
    )
    assert sample_type.calls == []
    # A system cancel has no resolver.
    other = create_sample(module_seeded_url)
    in_uow(
        module_seeded_url,
        lambda session: administration.cancel_approval_request(
            session, request_id=other, by_user_id=None
        ),
    )
    assert approval(module_seeded_url, other).resolved_by_user_id is None
    with pytest.raises(AppError) as cancelled_twice:
        in_uow(
            module_seeded_url,
            lambda session: administration.cancel_approval_request(
                session, request_id=other, by_user_id=None
            ),
        )
    assert cancelled_twice.value.code == "approval_transition_invalid"


def test_open_creates_keeps_a_pending_one_and_reopens_a_decided_one(
    module_seeded_url, client, clock, owner, sample_type
):
    """#232 QA D1: the seam for a subject that needs a decision again."""
    url = module_seeded_url
    subject = uuid.uuid7()

    def open_(
        priority: str = "normal", assignee_user_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        async def _open(session: AsyncSession) -> uuid.UUID:
            request = await administration.open_approval_request(
                session,
                request_type=SAMPLE,
                subject_id=subject,
                requester_user_id=None,
                priority=priority,
                assignee_user_id=assignee_user_id,
            )
            return request.id

        return in_uow(url, _open)

    approval_id = open_()  # none yet: created
    assert approval(url, approval_id).status == "pending"
    assert open_() == approval_id  # pending: kept, nothing changed
    assert approval(url, approval_id).version == 1
    assert open_("high") == approval_id  # pending: fields refreshed
    assert approval(url, approval_id).version == 2

    cookie = signed_in(client, owner.username)
    response = decide(client, cookie, approval_id, "reject", {"reason": "No."})
    assert code_of(response) == OK
    assert open_() == approval_id  # decided: reopened
    row = approval(url, approval_id)
    assert (row.status, row.reason, row.resolved_at, row.resolved_by_user_id) == (
        "pending",
        None,
        None,
        None,
    )
    assert row.version == 4
    # #118 E4: an existing row keeps its assignee on a refresh and a reopen;
    # the argument applies only on create.
    assert code_of(assign(client, cookie, approval_id, owner.id, 4)) == OK
    other = create_user(url, roles=(OWNER,))
    assert open_(assignee_user_id=other.id) == approval_id  # pending refresh
    assert assignee_of(url, approval_id) == owner.id
    reopened = get(client, cookie, approval_id).json()
    assert (reopened["priority"], reopened["allowed_actions"]) == (
        "normal",
        ["approve", "reject", "request_changes"],
    )
    updates = events(url, record_id=approval_id, action="update")
    assert [e.changes["status"]["new"] for e in updates if "status" in e.changes] == [
        "rejected",
        "pending",
    ]
    # changes_requested is not pending either: back to pending.
    decide(client, cookie, approval_id, "request-changes", {"reason": "Date?"})
    assert open_() == approval_id
    assert approval(url, approval_id).status == "pending"
    assert assignee_of(url, approval_id) == owner.id  # reopened: kept
    assert approve(client, cookie, approval_id).json()["status"] == "approved"
    assert (
        len(
            run_sql(
                url,
                "SELECT 1 FROM approval_requests WHERE subject_id = :s",
                {"s": subject},
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "body",
    [None, {}, {"reason": None}, {"reason": "   "}, {"reason": "x" * 1001}],
)
def test_a_required_reason_is_checked_after_404_and_409(
    module_seeded_url, client, clock, owner, sample_type, body
):
    approval_id = create_sample(module_seeded_url, owner)
    cookie = signed_in(client, owner.username)

    response = decide(client, cookie, approval_id, "reject", body)

    assert code_of(response) == VALIDATION
    [error] = response.json()["errors"]
    assert error["path"] == ["body", "reason"]
    assert code_of(decide(client, cookie, uuid.uuid4(), "reject", body)) == NOT_FOUND
    assert sample_type.calls == []
    assert approval(module_seeded_url, approval_id).status == "pending"
    assert [e.action for e in events(module_seeded_url, record_id=approval_id)] == [
        "create"
    ]


def test_a_reject_with_a_reason_stores_it_trimmed(
    module_seeded_url, client, clock, owner, sample_type
):
    approval_id = create_sample(module_seeded_url, owner)
    cookie = signed_in(client, owner.username)

    response = decide(
        client, cookie, approval_id, "reject", {"reason": " Not eligible. " + "x" * 984}
    )

    assert code_of(response) == OK
    body = response.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "Not eligible. " + "x" * 984
    assert body["resolved_by"]["id"] == str(owner.id)
    assert sample_type.calls == [("reject", sample_type.calls[0][1], body["reason"])]


def test_unknown_body_fields_are_422(
    module_seeded_url, client, clock, owner, sample_type
):
    approval_id = create_sample(module_seeded_url, owner)
    cookie = signed_in(client, owner.username)

    assert (
        code_of(
            decide(client, cookie, approval_id, "reject", {"reason": "x", "extra": 1})
        )
        == VALIDATION
    )


# --- D2: registrations can't be sent back --------------------------------------------


def test_request_changes_on_a_registration_is_unsupported(
    module_seeded_url, client, clock, owner
):
    row = registration(module_seeded_url, client)
    cookie = signed_in(client, owner.username)

    response = decide(
        client, cookie, row.approval_id, "request-changes", {"reason": "Why?"}
    )

    assert code_of(response) == UNSUPPORTED
    assert approval(module_seeded_url, row.approval_id).status == "pending"


def test_a_registration_rejection_may_carry_a_reason(
    module_seeded_url, client, clock, owner
):
    row = registration(module_seeded_url, client)
    cookie = signed_in(client, owner.username)

    response = decide(
        client, cookie, row.approval_id, "reject", {"reason": "Unknown family."}
    )

    assert code_of(response) == OK
    assert response.json()["reason"] == "Unknown family."
    assert approval(module_seeded_url, row.approval_id).reason == "Unknown family."


# --- B4: concurrent decisions --------------------------------------------------------


def _race(url: str, cookie: Any, approval_id: Any, calls: tuple[Any, Any]) -> list[Any]:
    results: list[Any] = [None, None]
    start = threading.Barrier(2)

    def run(index: int) -> None:
        with auth_client(url) as own_client:
            start.wait()
            results[index] = code_of(calls[index](own_client, cookie, approval_id))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_two_concurrent_approvals_decide_once(module_seeded_url, client, clock, owner):
    row = registration(module_seeded_url, client)
    cookie = signed_in(client, owner.username)

    results = _race(module_seeded_url, cookie, row.approval_id, (approve, approve))

    assert sorted(results) == [OK, TRANSITION_INVALID]
    assert roles_of(module_seeded_url, row.user_id) == ["guardian"]
    assert (
        len(
            events(
                module_seeded_url,
                record_id=row.id,
                action="identity.registration_approved",
            )
        )
        == 1
    )


def test_a_concurrent_approval_and_rejection_decide_once(
    module_seeded_url, client, clock, owner
):
    row = registration(module_seeded_url, client)
    cookie = signed_in(client, owner.username)

    results = _race(module_seeded_url, cookie, row.approval_id, (approve, reject))

    assert sorted(results) == [OK, TRANSITION_INVALID]
    status = approval(module_seeded_url, row.approval_id).status
    assert status == ("approved" if results[0] == OK else "rejected")
    decided = [
        e
        for action in (
            "identity.registration_approved",
            "identity.registration_rejected",
        )
        for e in events(module_seeded_url, record_id=row.id, action=action)
    ]
    assert len(decided) == 1


# --- C5: who sees what ---------------------------------------------------------------


@pytest.fixture
def both_types(
    module_seeded_url, client, owner, sample_type
) -> tuple[uuid.UUID, uuid.UUID]:
    """A pending registration and a pending sample, by approval id."""
    row = registration(module_seeded_url, client)
    return row.approval_id, create_sample(module_seeded_url, owner)


def test_an_owner_sees_both_types(module_seeded_url, client, owner, both_types):
    cookie = signed_in(client, owner.username)

    ids = listed_ids(client, cookie)

    assert {str(i) for i in both_types} <= set(ids)
    for approval_id in both_types:
        assert code_of(get(client, cookie, approval_id)) == OK


def test_an_administrator_sees_only_registrations(
    module_seeded_url, client, both_types
):
    registration_id, sample_id = both_types
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    cookie = signed_in(client, admin.username)

    ids = listed_ids(client, cookie)

    assert str(registration_id) in ids
    assert str(sample_id) not in ids
    assert listed_ids(client, cookie, request_type=SAMPLE) == []
    assert code_of(get(client, cookie, sample_id)) == NOT_FOUND
    assert code_of(approve(client, cookie, sample_id)) == NOT_FOUND


def test_a_delegated_administrator_sees_both(module_seeded_url, client, both_types):
    admin = create_user(
        module_seeded_url, roles=(ADMINISTRATOR,), delegations=(SAMPLE_PERMISSION,)
    )
    cookie = signed_in(client, admin.username)

    ids = listed_ids(client, cookie)

    assert {str(i) for i in both_types} <= set(ids)
    assert code_of(get(client, cookie, both_types[1])) == OK


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN]
)
def test_other_roles_see_nothing_and_get_404(
    module_seeded_url, client, both_types, sample_type, role
):
    member = create_user(module_seeded_url, roles=(role,))
    cookie = signed_in(client, member.username)

    for status in ("open", "all"):
        response = list_requests(client, cookie, request_type=None, status=status)
        assert code_of(response) == OK
        assert response.json()["items"] == []
        assert response.json()["total"] == 0
    for approval_id in both_types:
        assert code_of(get(client, cookie, approval_id)) == NOT_FOUND
        assert code_of(approve(client, cookie, approval_id)) == NOT_FOUND
        assert (
            code_of(decide(client, cookie, approval_id, "reject", {"reason": "x"}))
            == NOT_FOUND
        )
        assert (
            code_of(
                decide(client, cookie, approval_id, "request-changes", {"reason": "x"})
            )
            == NOT_FOUND
        )
        # #149: assigning and the assignee options are hidden the same way.
        assert code_of(assign(client, cookie, approval_id, member.id, 1)) == NOT_FOUND
        assert code_of(assignee_options(client, cookie, approval_id)) == NOT_FOUND
        assert approval(module_seeded_url, approval_id).status == "pending"
        assert assignee_of(module_seeded_url, approval_id) is None
    assert sample_type.calls == []


# --- C1: filters and order -----------------------------------------------------------


def test_the_assignee_filter(module_seeded_url, client, owner, sample_type):
    other_owner = create_user(module_seeded_url, roles=(OWNER,))
    mine = create_sample(module_seeded_url, owner, assignee=owner)
    theirs = create_sample(module_seeded_url, owner, assignee=other_owner)
    nobody = create_sample(module_seeded_url, owner)
    cookie = signed_in(client, owner.username)

    me = listed_ids(client, cookie, request_type=SAMPLE, assignee="me")
    unassigned = listed_ids(client, cookie, request_type=SAMPLE, assignee="unassigned")
    anyone = listed_ids(client, cookie, request_type=SAMPLE)

    assert me == [str(mine)]
    assert str(nobody) in unassigned
    assert not {str(mine), str(theirs)} & set(unassigned)
    assert {str(mine), str(theirs), str(nobody)} <= set(anyone)
    [item] = list_requests(client, cookie, request_type=SAMPLE, assignee="me").json()[
        "items"
    ]
    assert item["assignee"]["id"] == str(owner.id)
    assert code_of(list_requests(client, cookie, assignee="them")) == VALIDATION


def test_the_status_and_type_filters_and_the_order(
    module_seeded_url, client, clock, owner, sample_type
):
    run_sql(
        module_seeded_url,
        "DELETE FROM approval_requests WHERE request_type = :t",
        {"t": SAMPLE},
    )
    low = create_sample(module_seeded_url, owner, priority="low")
    normal = create_sample(module_seeded_url, owner)
    high = create_sample(module_seeded_url, owner, priority="high")
    changes = create_sample(module_seeded_url, owner)
    approved = create_sample(module_seeded_url, owner)
    cookie = signed_in(client, owner.username)
    assert (
        code_of(decide(client, cookie, changes, "request-changes", {"reason": "More."}))
        == OK
    )
    assert code_of(approve(client, cookie, approved)) == OK

    open_ids = listed_ids(client, cookie, request_type=SAMPLE)

    # High first, then normal (oldest first), then low.
    assert open_ids == [str(high), str(normal), str(changes), str(low)]
    assert listed_ids(client, cookie, request_type=SAMPLE, status="pending") == [
        str(high),
        str(normal),
        str(low),
    ]
    assert listed_ids(
        client, cookie, request_type=SAMPLE, status="changes_requested"
    ) == [str(changes)]
    assert listed_ids(client, cookie, request_type=SAMPLE, status="approved") == [
        str(approved)
    ]
    assert len(listed_ids(client, cookie, request_type=SAMPLE, status="all")) == 5
    assert listed_ids(client, cookie, request_type=SAMPLE, status="cancelled") == []
    registrations = listed_ids(client, cookie, request_type="identity.registration")
    assert not set(open_ids) & set(registrations)
    page = list_requests(client, cookie, request_type=SAMPLE, limit=2, offset=1).json()
    assert (page["total"], [i["id"] for i in page["items"]]) == (
        4,
        [str(normal), str(changes)],
    )
    [item] = [
        i
        for i in list_requests(client, cookie, request_type=SAMPLE).json()["items"]
        if i["id"] == str(high)
    ]
    assert item["priority"] == "high"
    assert item["subject"]["label"].startswith("Sample ")
    assert item["requester"]["id"] == str(owner.id)
    assert item["allowed_actions"] == ["approve", "reject", "request_changes"]


def test_a_page_takes_at_most_five_statements(
    module_seeded_url, client, owner, sample_type
):
    for _ in range(15):
        create_sample(module_seeded_url, owner)
    for _ in range(15):
        registration(module_seeded_url, client)
    principal = Principal(
        user_id=owner.id,
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )

    count, page = count_statements(
        module_seeded_url,
        lambda session: administration.list_approval_requests(
            session,
            principal=principal,
            filters=administration.ApprovalRequestFilters(status="open"),
            page=OffsetParams(limit=100, offset=0),
        ),
    )

    assert {item.request_type for item in page.items} == {
        SAMPLE,
        "identity.registration",
    }
    assert len(page.items) >= 30
    assert count <= 5


# --- #118 A: assignment --------------------------------------------------------------

CLOSED = (409, "approval_request_closed")
CONFLICT = (409, "version_conflict")
INELIGIBLE = (422, "approval_assignee_ineligible")


def test_assign_reassign_and_unassign(module_seeded_url, client, owner, sample_type):
    url = module_seeded_url
    other = create_user(url, roles=(OWNER,))
    approval_id = create_sample(url, owner, priority="high")
    cookie = signed_in(client, owner.username)

    assigned = assign(client, cookie, approval_id, other.id, 1)

    assert code_of(assigned) == OK, assigned.text
    body = assigned.json()
    assert body["assignee"]["id"] == str(other.id)
    assert (body["status"], body["priority"], body["version"]) == ("pending", "high", 2)
    [update] = events(url, record_id=approval_id, action="update")
    assert set(update.changes) == {"assignee_user_id"}
    assert update.changes["assignee_user_id"] == {"old": None, "new": str(other.id)}
    [notification] = assigned_notifications(url, other.id)
    assert notification.params == {
        "request_id": str(approval_id),
        "request_type": SAMPLE,
    }

    # Reassign to self: no notification for anyone.
    mine = assign(client, cookie, approval_id, owner.id, 2)
    assert code_of(mine) == OK
    assert mine.json()["assignee"]["id"] == str(owner.id)
    assert assigned_notifications(url, owner.id) == []
    # Unassign: none either.
    unassigned = assign(client, cookie, approval_id, None, 3)
    assert code_of(unassigned) == OK
    assert (unassigned.json()["assignee"], unassigned.json()["version"]) == (None, 4)
    assert len(assigned_notifications(url, other.id)) == 1
    row = approval(url, approval_id)
    assert (row.status, row.reason, row.resolved_at, row.resolved_by_user_id) == (
        "pending",
        None,
        None,
        None,
    )
    assert sample_type.calls == []


def test_assigning_the_current_assignee_changes_nothing(
    module_seeded_url, client, owner, sample_type
):
    url = module_seeded_url
    other = create_user(url, roles=(OWNER,))
    approval_id = create_sample(url, owner)
    cookie = signed_in(client, owner.username)
    assert code_of(assign(client, cookie, approval_id, other.id, 1)) == OK

    again = assign(client, cookie, approval_id, other.id, 2)

    assert code_of(again) == OK
    assert again.json()["version"] == 2
    assert len(events(url, record_id=approval_id, action="update")) == 1
    assert len(assigned_notifications(url, other.id)) == 1
    # Unassigning an unassigned request: the same.
    nobody = create_sample(url, owner)
    assert assign(client, cookie, nobody, None, 1).json()["version"] == 1
    assert events(url, record_id=nobody, action="update") == []


def test_the_assignment_order(module_seeded_url, client, owner, sample_type):
    url = module_seeded_url
    approval_id = create_sample(url, owner)
    admin = create_user(url, roles=(ADMINISTRATOR,))
    tutor = create_user(url, roles=(TUTOR,))
    cookie = signed_in(client, owner.username)

    # 422 on the body first.
    client.cookies.clear()
    for body in (
        {"version": 1},
        {"assignee_user_id": None},
        {"assignee_user_id": None, "version": 1, "extra": 1},
        {"assignee_user_id": "nope", "version": 1},
    ):
        response = client.post(
            f"{APPROVALS}/{approval_id}/assign",
            headers=signed_headers(cookie),
            json=body,
        )
        assert code_of(response) == VALIDATION
    # 404: missing, or hidden (an Administrator can't review the sample).
    assert code_of(assign(client, cookie, uuid.uuid7(), owner.id, 1)) == NOT_FOUND
    admin_cookie = signed_in(client, admin.username)
    assert code_of(assign(client, admin_cookie, approval_id, admin.id, 99)) == (
        NOT_FOUND
    )
    # 409 version_conflict before the closed and the eligibility checks.
    assert code_of(assign(client, cookie, approval_id, tutor.id, 2)) == CONFLICT
    # 422: not a holder of the review permission, unknown, or not active.
    archived = create_user(url, roles=(OWNER,), state="archived")
    for target in (tutor.id, admin.id, uuid.uuid7(), archived.id):
        assert code_of(assign(client, cookie, approval_id, target, 1)) == INELIGIBLE
    assert assignee_of(url, approval_id) is None
    assert approval(url, approval_id).version == 1
    assert events(url, record_id=approval_id, action="update") == []
    for user in (tutor, admin, archived):
        assert assigned_notifications(url, user.id) == []
    # A delegated Administrator holds it: eligible.
    delegated = create_user(
        url, roles=(ADMINISTRATOR,), delegations=(SAMPLE_PERMISSION,)
    )
    assert code_of(assign(client, cookie, approval_id, delegated.id, 1)) == OK

    # 409 approval_request_closed after the version check, before eligibility.
    assert code_of(approve(client, cookie, approval_id)) == OK
    version = approval(url, approval_id).version
    assert code_of(assign(client, cookie, approval_id, tutor.id, version)) == CLOSED
    assert code_of(assign(client, cookie, approval_id, None, version)) == CLOSED
    assert code_of(assign(client, cookie, approval_id, None, version - 1)) == CONFLICT
    # A decision keeps the assignee as history.
    assert assignee_of(url, approval_id) == delegated.id


@pytest.mark.parametrize("final", ["reject", "cancel"])
def test_rejected_and_cancelled_requests_are_closed(
    module_seeded_url, client, owner, sample_type, final
):
    url = module_seeded_url
    approval_id = create_sample(url, owner, assignee=owner)
    cookie = signed_in(client, owner.username)
    if final == "reject":
        response = decide(client, cookie, approval_id, "reject", {"reason": "No."})
        assert code_of(response) == OK
    else:

        async def _cancel(session: AsyncSession) -> None:
            await administration.cancel_approval_request(
                session, request_id=approval_id, by_user_id=None
            )

        in_uow(url, _cancel)
    version = approval(url, approval_id).version

    assert code_of(assign(client, cookie, approval_id, None, version)) == CLOSED
    assert assignee_of(url, approval_id) == owner.id


def test_changes_requested_items_can_be_assigned_and_resubmitting_keeps_it(
    module_seeded_url, client, owner, sample_type
):
    url = module_seeded_url
    other = create_user(url, roles=(OWNER,))
    approval_id = create_sample(url, owner)
    cookie = signed_in(client, owner.username)
    assert (
        code_of(
            decide(client, cookie, approval_id, "request-changes", {"reason": "More."})
        )
        == OK
    )

    version = approval(url, approval_id).version
    assert code_of(assign(client, cookie, approval_id, other.id, version)) == OK

    async def _resubmit(session: AsyncSession) -> None:
        await administration.resubmit_approval_request(
            session, request_id=approval_id, by_user_id=owner.id
        )

    in_uow(url, _resubmit)
    assert approval(url, approval_id).status == "pending"
    assert assignee_of(url, approval_id) == other.id


def test_a_holder_of_the_review_permission_may_assign_others(
    module_seeded_url, client, owner, sample_type
):
    # E2: no separate permission; a delegated Administrator assigns an Owner.
    url = module_seeded_url
    approval_id = create_sample(url, owner)
    delegated = create_user(
        url, roles=(ADMINISTRATOR,), delegations=(SAMPLE_PERMISSION,)
    )
    cookie = signed_in(client, delegated.username)

    response = assign(client, cookie, approval_id, owner.id, 1)

    assert code_of(response) == OK
    [notification] = assigned_notifications(url, owner.id)
    assert notification.params["request_id"] == str(approval_id)
    [update] = events(url, record_id=approval_id, action="update")
    assert update.actor_user_id == delegated.id


def _assign_call(assignee_id: uuid.UUID) -> Any:
    def call(own_client: TestClient, cookie: Any, approval_id: Any) -> Any:
        return assign(own_client, cookie, approval_id, assignee_id, 1)

    return call


def test_two_concurrent_assignments_with_one_version_apply_once(
    module_seeded_url, client, owner, sample_type
):
    url = module_seeded_url
    first = create_user(url, roles=(OWNER,))
    second = create_user(url, roles=(OWNER,))
    approval_id = create_sample(url, owner)
    cookie = signed_in(client, owner.username)

    results = _race(
        url, cookie, approval_id, (_assign_call(first.id), _assign_call(second.id))
    )

    assert sorted(results) == [OK, CONFLICT]
    winner = first if results[0] == OK else second
    loser = second if winner is first else first
    assert assignee_of(url, approval_id) == winner.id
    assert approval(url, approval_id).version == 2
    assert len(events(url, record_id=approval_id, action="update")) == 1
    assert len(assigned_notifications(url, winner.id)) == 1
    assert assigned_notifications(url, loser.id) == []


# --- #118 B: assignee options --------------------------------------------------------


def test_the_assignee_options(module_seeded_url, client, owner, sample_type):
    url = module_seeded_url
    approval_id = create_sample(url, owner)
    delegated = create_user(
        url,
        roles=(ADMINISTRATOR,),
        delegations=(SAMPLE_PERMISSION,),
        display_name="Aaa Delegated",
    )
    plain_admin = create_user(url, roles=(ADMINISTRATOR,))
    archived = create_user(url, roles=(OWNER,), state="archived")
    tutor = create_user(url, roles=(TUTOR,))
    cookie = signed_in(client, owner.username)

    response = assignee_options(client, cookie, approval_id)

    assert code_of(response) == OK
    items = response.json()["items"]
    ids = [item["id"] for item in items]
    assert {str(owner.id), str(delegated.id)} <= set(ids)
    assert not {str(plain_admin.id), str(archived.id), str(tutor.id)} & set(ids)
    assert set(items[0]) == {"id", "display_name"}
    assert items == sorted(items, key=lambda i: (i["display_name"], i["id"]))
    # Everyone listed can be assigned (B3).
    version = 1
    for item in items[:3]:
        assert code_of(assign(client, cookie, approval_id, item["id"], version)) == OK
        version = approval(url, approval_id).version
    # Closed requests still answer (read only).
    assert code_of(approve(client, cookie, approval_id)) == OK
    assert code_of(assignee_options(client, cookie, approval_id)) == OK
    # 404: missing, or hidden.
    assert code_of(assignee_options(client, cookie, uuid.uuid7())) == NOT_FOUND
    for user in (plain_admin, tutor):
        other_cookie = signed_in(client, user.username)
        assert code_of(assignee_options(client, other_cookie, approval_id)) == (
            NOT_FOUND
        )


def test_the_assignee_options_take_at_most_four_statements(
    module_seeded_url, owner, sample_type
):
    approval_id = create_sample(module_seeded_url, owner)
    for _ in range(5):
        create_user(module_seeded_url, roles=(OWNER,))
    principal = Principal(
        user_id=owner.id,
        roles=frozenset({OWNER}),
        permissions=ROLE_PERMISSIONS[OWNER],
    )

    count, options = count_statements(
        module_seeded_url,
        lambda session: administration.list_assignee_options(
            session, principal=principal, request_id=approval_id
        ),
    )

    assert len(options) >= 6
    assert count <= 4


# --- #118 C: sort and priority filter ------------------------------------------------


def test_the_sorts_and_the_priority_filter(
    module_seeded_url, client, clock, owner, sample_type
):
    url = module_seeded_url
    run_sql(url, "DELETE FROM approval_requests WHERE request_type = :t", {"t": SAMPLE})
    low = create_sample(url, owner, priority="low")
    normal = create_sample(url, owner, assignee=owner)
    high = create_sample(url, owner, priority="high")
    high_2 = create_sample(url, owner, priority="high", assignee=owner)
    changes = create_sample(url, owner)
    cookie = signed_in(client, owner.username)
    assert (
        code_of(decide(client, cookie, changes, "request-changes", {"reason": "More."}))
        == OK
    )
    # created_at is the transaction's time; space them out explicitly.
    for index, approval_id in enumerate((low, normal, high, high_2, changes)):
        run_sql(
            url,
            "UPDATE approval_requests SET created_at ="
            " TIMESTAMPTZ '2031-01-01 00:00:00+00' + make_interval(mins => :m)"
            " WHERE id = :id",
            {"m": index, "id": approval_id},
        )
    ordered = [str(i) for i in (low, normal, high, high_2, changes)]

    def ids(**params: Any) -> list[str]:
        return listed_ids(client, cookie, request_type=SAMPLE, **params)

    assert ids() == [str(high), str(high_2), str(normal), str(changes), str(low)]
    assert ids(sort="priority") == ids()
    assert ids(sort="oldest") == ordered
    assert ids(sort="newest") == ordered[::-1]
    assert ids(priority="high") == [str(high), str(high_2)]
    assert ids(priority="high", sort="newest") == [str(high_2), str(high)]
    assert ids(priority="normal") == [str(normal), str(changes)]
    assert ids(priority="low") == [str(low)]
    assert ids(priority="high", assignee="me") == [str(high_2)]
    assert ids(priority="normal", status="changes_requested") == [str(changes)]
    assert ids(priority="normal", status="pending", assignee="unassigned") == []
    page = list_requests(
        client, cookie, request_type=SAMPLE, priority="normal", sort="newest"
    ).json()
    assert (page["total"], [i["id"] for i in page["items"]]) == (
        2,
        [str(changes), str(normal)],
    )
    second = list_requests(
        client, cookie, request_type=SAMPLE, sort="oldest", limit=2, offset=2
    ).json()
    assert (second["total"], [i["id"] for i in second["items"]]) == (5, ordered[2:4])
    for bad in ({"sort": "status"}, {"priority": "urgent"}, {"sort": ""}):
        assert code_of(list_requests(client, cookie, **bad)) == VALIDATION
