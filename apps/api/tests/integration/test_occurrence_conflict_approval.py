"""The ``scheduling.occurrence_conflict`` inbox item (#53 J4, G).

Each test makes a series, blocks its tutor in week 13 with a confirmed
one-off session, and runs the nightly job one week later: the week-13
Monday becomes a ``conflict`` row with a pending request (requester
``NULL``).
"""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
)
from app.modules.scheduling import tasks
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.service import OCCURRENCE_CONFLICT_HANDLER
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql, settings_for
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import (
    Timeline,
    create_series,
    iso_z,
    owner_cookie,
    series_body,
    timeline,
)
from tests.integration.test_class_session_create import (
    CREATED,
    change,
    existing_session,
    new_student,
    new_subject,
    new_tutor,
)

APPROVALS = "/api/v1/approval-requests"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def conflict_setup(url: str, client) -> dict[str, Any]:
    """A series with a ``conflict`` row in week 13 and its pending request."""
    tl = timeline()
    subject = new_subject(url)
    tutor = new_tutor(url, qualified_for=[subject])
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = create_series(
            client,
            cookie,
            series_body(
                student=new_student(url),
                subject=subject,
                tutor=tutor,
                starts_on=tl.monday,
            ),
        )
    assert code_of(response) == CREATED, response.text
    blocker = existing_session(
        url,
        state="confirmed",
        tutor_user_id=tutor,
        student_id=new_student(url),
        starts_at=tl.at(tl.week(13), "16:00"),
    )
    later = tl.now + timedelta(weeks=1)
    with frozen_clock(later):
        asyncio.run(tasks.run_materialization(settings_for(url), now=later))
    [row] = run_sql(
        url,
        "SELECT s.id, s.state, s.version, r.id AS request_id, r.status"
        " FROM class_sessions s JOIN approval_requests r ON r.subject_id = s.id"
        " WHERE s.series_id = :series AND s.state = 'conflict'",
        {"series": response.json()["id"]},
    )
    return {
        "tl": tl,
        "later": later,
        "tutor": tutor,
        "blocker": blocker,
        "series_id": response.json()["id"],
        "session_id": row.id,
        "request_id": row.request_id,
    }


def state_of(url: str, session_id: uuid.UUID) -> Any:
    [row] = run_sql(
        url,
        "SELECT state, tutor_user_id, cancellation_reason FROM class_sessions"
        " WHERE id = :id",
        {"id": str(session_id)},
    )
    return row


def request_status(url: str, request_id: uuid.UUID) -> str:
    [row] = run_sql(
        url,
        "SELECT status FROM approval_requests WHERE id = :id",
        {"id": str(request_id)},
    )
    return str(row.status)


def cancel_blocker(url: str, blocker: uuid.UUID) -> None:
    async def _cancel(session) -> None:
        row = await session.get(ClassSession, blocker)
        await change(
            session,
            ClassSession,
            blocker,
            state="cancelled",
            cancelled_at=row.starts_at - timedelta(days=30),
            cancellation_reason="Cancelada",
        )

    in_uow(url, _cancel)


def signed(url: str, client, role: str, tl_now) -> Any:
    user = create_user(url, roles=(role,))
    with frozen_clock(tl_now):
        return sign_in(client, user.username)


def test_approve_after_the_blocker_is_cancelled_schedules_it(module_seeded_url, client):
    ctx = conflict_setup(module_seeded_url, client)
    cancel_blocker(module_seeded_url, ctx["blocker"])

    with frozen_clock(ctx["later"]):
        cookie = owner_cookie(module_seeded_url, client)
        response = post(client, cookie, f"{APPROVALS}/{ctx['request_id']}/approve", {})

    assert code_of(response) == (200, None), response.text
    row = state_of(module_seeded_url, ctx["session_id"])
    assert (row.state, row.tutor_user_id) == ("offered", ctx["tutor"])
    assert request_status(module_seeded_url, ctx["request_id"]) == "approved"
    [codes] = run_sql(
        module_seeded_url,
        "SELECT conflict_codes FROM class_sessions WHERE id = :id",
        {"id": str(ctx["session_id"])},
    )
    # Informational: kept after the row leaves conflict.
    assert list(codes.conflict_codes) == ["tutor_overlap"]


def test_approve_while_it_still_conflicts_changes_nothing(module_seeded_url, client):
    ctx = conflict_setup(module_seeded_url, client)

    with frozen_clock(ctx["later"]):
        cookie = owner_cookie(module_seeded_url, client)
        response = post(client, cookie, f"{APPROVALS}/{ctx['request_id']}/approve", {})

    assert code_of(response) == (409, "scheduling_conflict")
    assert [c["code"] for c in response.json()["conflicts"]] == ["tutor_overlap"]
    assert state_of(module_seeded_url, ctx["session_id"]).state == "conflict"
    assert request_status(module_seeded_url, ctx["request_id"]) == "pending"


def test_reject_cancels_the_occurrence_with_the_reason(module_seeded_url, client):
    ctx = conflict_setup(module_seeded_url, client)

    with frozen_clock(ctx["later"]):
        cookie = owner_cookie(module_seeded_url, client)
        without = post(client, cookie, f"{APPROVALS}/{ctx['request_id']}/reject", {})
        response = post(
            client,
            cookie,
            f"{APPROVALS}/{ctx['request_id']}/reject",
            {"reason": "  Sin tutor ese día  "},
        )

    assert code_of(without)[0] == 422
    assert code_of(response) == (200, None), response.text
    row = state_of(module_seeded_url, ctx["session_id"])
    assert (row.state, row.cancellation_reason) == ("cancelled", "Sin tutor ese día")
    assert request_status(module_seeded_url, ctx["request_id"]) == "rejected"


def test_a_row_no_longer_in_conflict_is_409(module_seeded_url, client):
    ctx = conflict_setup(module_seeded_url, client)

    async def _cancel(session) -> None:
        await change(
            session,
            ClassSession,
            ctx["session_id"],
            state="cancelled",
            cancelled_at=ctx["later"],
            cancellation_reason="Otro camino",
        )

    in_uow(module_seeded_url, _cancel)
    with frozen_clock(ctx["later"]):
        cookie = owner_cookie(module_seeded_url, client)
        response = post(client, cookie, f"{APPROVALS}/{ctx['request_id']}/approve", {})

    assert code_of(response) == (409, "approval_transition_invalid")
    assert request_status(module_seeded_url, ctx["request_id"]) == "pending"


def test_an_administrator_sees_it_and_coordinators_and_finance_do_not(
    module_seeded_url, client
):
    ctx = conflict_setup(module_seeded_url, client)
    path = f"{APPROVALS}/{ctx['request_id']}"

    seen = {}
    for role in (ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF):
        cookie = signed(module_seeded_url, client, role, ctx["later"])
        with frozen_clock(ctx["later"]):
            seen[role] = (
                code_of(get(client, cookie, path)),
                code_of(post(client, cookie, f"{path}/approve", {})),
            )

    assert seen[ADMINISTRATOR][0] == (200, None)
    assert seen[ACADEMIC_COORDINATOR] == ((404, "not_found"), (404, "not_found"))
    assert seen[FINANCE_STAFF] == ((404, "not_found"), (404, "not_found"))


def test_the_item_carries_its_summary(module_seeded_url, client):
    ctx = conflict_setup(module_seeded_url, client)
    tl: Timeline = ctx["tl"]

    with frozen_clock(ctx["later"]):
        cookie = owner_cookie(module_seeded_url, client)
        item = get(client, cookie, f"{APPROVALS}/{ctx['request_id']}").json()

    assert item["request_type"] == "scheduling.occurrence_conflict"
    assert item["requester"] is None
    subject = item["subject"]
    assert subject["class_session_id"] == str(ctx["session_id"])
    assert subject["series_id"] == ctx["series_id"]
    assert subject["starts_at"] == iso_z(tl.at(tl.week(13), "16:00"))
    assert subject["conflict_codes"] == ["tutor_overlap"]
    assert len(subject["student_display_names"]) == 1
    assert subject["tutor_display_name"]
    assert subject["subject_name"]


def test_summaries_take_a_fixed_number_of_statements(module_seeded_url, client):
    one = conflict_setup(module_seeded_url, client)
    two = conflict_setup(module_seeded_url, client)

    single, _ = count_statements(
        module_seeded_url,
        lambda s: OCCURRENCE_CONFLICT_HANDLER.summaries(s, [one["session_id"]]),
    )
    double, found = count_statements(
        module_seeded_url,
        lambda s: OCCURRENCE_CONFLICT_HANDLER.summaries(
            s, [one["session_id"], two["session_id"]]
        ),
    )

    assert single == double == 5
    assert set(found) == {one["session_id"], two["session_id"]}
