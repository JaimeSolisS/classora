"""Every registered approval type appears in the inbox (#118 E9, F1, F2).

Parametrized over the types ``build_app()`` registers. Each case creates one
request through ``create_approval_request`` with a random ``subject_id`` (there
is no FK; each type's own issue tests its summaries with a real subject) and
checks the generic inbox behaviour: who lists and opens it, which decisions it
offers, and that assignment works for it.

Adding an approval type: extend :data:`EXPECTED` below, and M8-01b's web pins
too (``APPROVAL_TYPE_LABELS``, the panel's summary renderer,
``REJECT_REASON_REQUIRED``).
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import build_app
from app.modules.administration import approvals
from app.modules.administration import service as administration
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    TUTOR,
)
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
    signed_headers,
)
from tests.integration.conftest import reset_rate_limits, run_sql
from tests.integration.lifecycle_support import code_of, in_uow, signed_in
from tests.integration.registration_support import APPROVALS

EXPECTED: dict[str, tuple[str, bool, bool, bool]] = {
    # type: (review_permission, inbox_decisions, supports_request_changes,
    #        reason_required_on_reject)
    "academics.progress_report": ("progress_report.review", False, True, True),
    "academics.session_closeout": ("teaching_hours.approve", False, True, True),
    "identity.registration": ("registration.review", True, False, False),
    "payroll.period": ("payroll.finalize", False, False, True),
    "scheduling.change_request": ("schedule.manage", False, False, True),
    "scheduling.occurrence_conflict": ("schedule.manage", True, False, True),
    "scheduling.offer_reassignment": ("schedule.assign", True, False, True),
    "scheduling.session_request": ("schedule.manage", False, False, True),
    "staff.availability_change": ("tutor.manage", True, False, True),
    "staff.qualification": ("qualification.review", True, False, True),
}

NEW_TYPE_HINT = (
    "A new approval type must be added to EXPECTED here and to M8-01b's web"
    " pins (APPROVAL_TYPE_LABELS, the panel's summary renderer,"
    " REJECT_REASON_REQUIRED)."
)

build_app()
TYPES = sorted(approvals.registered_approval_types())
DELEGABLE = {permission.code for permission in PERMISSIONS if permission.delegable}
STAFF_ROLES = (ACADEMIC_COORDINATOR, ADMINISTRATOR, FINANCE_STAFF)


def _flags(request_type: str) -> tuple[str, bool, bool, bool]:
    handler = approvals._handlers[request_type]
    return (
        handler.review_permission,
        approvals._inbox_decisions(handler),
        handler.supports_request_changes,
        handler.reason_required_on_reject,
    )


def test_the_pinned_table_matches_the_registry():
    build_app()

    assert set(EXPECTED) == approvals.registered_approval_types(), NEW_TYPE_HINT
    assert {t: _flags(t) for t in TYPES} == EXPECTED, NEW_TYPE_HINT


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def _reviewer(url: str, permission: str) -> CreatedUser:
    """A user who holds ``permission`` through a delegation when it is
    delegable, otherwise through the narrowest staff role that holds it."""
    if permission in DELEGABLE:
        return create_user(url, roles=(ADMINISTRATOR,), delegations=(permission,))
    for role in STAFF_ROLES:
        if permission in ROLE_PERMISSIONS[role]:
            return create_user(url, roles=(role,))
    return create_user(url, roles=(OWNER,))


def _outsider(url: str, permission: str) -> CreatedUser | None:
    """A staff user who doesn't hold ``permission`` (None when every staff
    role holds it)."""
    for role in STAFF_ROLES:
        if permission not in ROLE_PERMISSIONS[role]:
            return create_user(url, roles=(role,))
    return None


def _create(url: str, request_type: str) -> uuid.UUID:
    async def _run(session: AsyncSession) -> uuid.UUID:
        request = await administration.create_approval_request(
            session,
            request_type=request_type,
            subject_id=uuid.uuid7(),
            requester_user_id=None,
        )
        return request.id

    return in_uow(url, _run)


def _list(client: TestClient, cookie: Any, request_type: str) -> Any:
    client.cookies.clear()
    response = client.get(
        APPROVALS,
        params={"request_type": request_type, "sort": "newest", "limit": 100},
        headers=cookie_header(cookie),
    )
    assert code_of(response) == (200, None), response.text
    return response.json()


def _get(client: TestClient, cookie: Any, approval_id: Any) -> Any:
    client.cookies.clear()
    return client.get(f"{APPROVALS}/{approval_id}", headers=cookie_header(cookie))


def _assign(
    client: TestClient, cookie: Any, approval_id: Any, assignee: Any, version: int
) -> Any:
    client.cookies.clear()
    return client.post(
        f"{APPROVALS}/{approval_id}/assign",
        headers=signed_headers(cookie),
        json={
            "assignee_user_id": str(assignee) if assignee else None,
            "version": version,
        },
    )


def test_a_tutor_reviews_no_type():
    assert not any(EXPECTED[t][0] in ROLE_PERMISSIONS[TUTOR] for t in EXPECTED)


@pytest.mark.parametrize("request_type", TYPES)
def test_every_type_is_in_the_inbox(module_seeded_url, client, request_type):
    url = module_seeded_url
    reset_rate_limits(url)
    permission, inbox_decisions, supports_changes, _ = EXPECTED[request_type]
    owner = create_user(url, roles=(OWNER,))
    reviewer = _reviewer(url, permission)
    outsider = _outsider(url, permission)
    tutor = create_user(url, roles=(TUTOR,))
    approval_id = _create(url, request_type)

    # The Owner and a holder of only this type's permission list and open it.
    owner_cookie = signed_in(client, owner.username)
    reviewer_cookie = signed_in(client, reviewer.username)
    for cookie in (owner_cookie, reviewer_cookie):
        items = _list(client, cookie, request_type)["items"]
        [item] = [i for i in items if i["id"] == str(approval_id)]
        assert item["request_type"] == request_type
        assert item["status"] == "pending"
        assert code_of(_get(client, cookie, approval_id)) == (200, None)
    expected_actions = (
        ["approve", "reject", *(["request_changes"] if supports_changes else [])]
        if inbox_decisions
        else []
    )
    assert item["allowed_actions"] == expected_actions

    # Others list nothing of the type and get 404.
    for user in (tutor, outsider):
        if user is None:
            continue
        cookie = signed_in(client, user.username)
        page = _list(client, cookie, request_type)
        assert (page["items"], page["total"]) == ([], 0)
        assert code_of(_get(client, cookie, approval_id)) == (404, "not_found")
        assert code_of(_assign(client, cookie, approval_id, None, 1)) == (
            404,
            "not_found",
        )

    # Assignment is type-agnostic: the Owner assigns the reviewer, the
    # reviewer unassigns.
    assigned = _assign(client, owner_cookie, approval_id, reviewer.id, 1)
    assert code_of(assigned) == (200, None), assigned.text
    assert assigned.json()["assignee"]["id"] == str(reviewer.id)
    assert run_sql(
        url,
        "SELECT 1 FROM notifications WHERE recipient_user_id = :u"
        " AND notification_type = 'administration.approval_assigned'",
        {"u": reviewer.id},
    )
    unassigned = _assign(client, reviewer_cookie, approval_id, None, 2)
    assert code_of(unassigned) == (200, None)
    assert unassigned.json()["assignee"] is None
    assert unassigned.json()["status"] == "pending"
