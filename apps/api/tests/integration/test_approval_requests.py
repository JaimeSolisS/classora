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
        assert approval(module_seeded_url, approval_id).status == "pending"
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
