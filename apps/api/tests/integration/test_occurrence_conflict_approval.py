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
    OWNER,
)
from app.modules.scheduling import tasks
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.service import OCCURRENCE_CONFLICT_HANDLER
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql, settings_for
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_support import (
    Timeline,
    create_series,
    iso_z,
    owner_cookie,
    series_body,
    timeline,
)
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_class_session_create import (
    CREATED,
    change,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)
from tests.integration.test_series_offers import (
    Ctx,
    as_user,
    assert_invariant,
    conflict_row,
    make_series,
    offer_series,
    series_state,
)

APPROVALS = "/api/v1/approval-requests"
OFFERS = "/api/v1/assignment-offers"


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


# --- #266: the series' tutor changed after the conflict row was stored -------------


def offers_ctx(url: str) -> Ctx:
    """A :class:`~tests.integration.test_series_offers.Ctx` ("now" the Friday
    before the first Monday) whose ``tutor`` is tutor A."""
    tl = timeline()
    subject = new_subject(url)
    return Ctx(
        tl=tl,
        now=tl.now - timedelta(days=2),
        owner=create_user(url, roles=(OWNER,)),
        student=new_student(url),
        subject=subject,
        tutor=new_tutor_user(url, qualified_for=[subject]),
        room=new_room(url),
    )


def offer_and_accept(client, ctx: Ctx, series_id: Any, tutor, at) -> None:
    """Offer the series to ``tutor`` at ``at`` (from today) and accept it as
    that tutor one minute later."""
    cookie = as_user(client, ctx.owner, at)
    with frozen_clock(at):
        version = series_state_version(client, cookie, series_id)
        offered = offer_series(
            client, cookie, series_id, tutor_user_id=str(tutor.id), version=version
        )
    assert code_of(offered) == CREATED, offered.text
    later = at + timedelta(minutes=1)
    cookie = as_user(client, tutor, later)
    with frozen_clock(later):
        accepted = post(
            client, cookie, f"{OFFERS}/{offered.json()['offer']['id']}/accept", {}
        )
    assert code_of(accepted) == (200, None), accepted.text


def series_state_version(client, cookie, series_id: Any) -> int:
    response = get(client, cookie, f"/api/v1/class-series/{series_id}")
    assert code_of(response) == (200, None), response.text
    return int(response.json()["version"])


def conflict_row_id(url: str, series_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, tutor_user_id FROM class_sessions"
        " WHERE series_id = :series AND state = 'conflict'",
        {"series": str(series_id)},
    )
    return row


def path_a(url: str, client) -> dict[str, Any]:
    """Path A: a series without a tutor gets a ``conflict`` row (tutor
    ``NULL``; the series' student is busy) at night, then it is offered to
    tutor B, who accepts."""
    ctx = offers_ctx(url)
    created = make_series(client, ctx, tutor=False, count=26)
    week13 = ctx.tl.week(13)
    blocker, request_id = conflict_row(url, ctx, created["id"], week13)
    conflict = conflict_row_id(url, created["id"])
    assert conflict.tutor_user_id is None
    tutor_b = new_tutor_user(url, qualified_for=[ctx.subject])
    later = ctx.now + timedelta(weeks=1, hours=1)
    offer_and_accept(client, ctx, created["id"], tutor_b, later)
    state = series_state(url, created["id"])
    assert (state.assignment_status, state.tutor_user_id) == ("accepted", tutor_b.id)
    return {
        "ctx": ctx,
        "series_id": created["id"],
        "session_id": conflict.id,
        "request_id": request_id,
        "blocker": blocker,
        "tutor_b": tutor_b,
        "at": later + timedelta(hours=1),
        "week13": week13,
    }


def approve_as_owner(client, ctx: Ctx, request_id: Any, at) -> Any:
    cookie = as_user(client, ctx.owner, at)
    with frozen_clock(at):
        return post(client, cookie, f"{APPROVALS}/{request_id}/approve", {})


def test_approve_confirms_with_the_tutor_who_accepted_after_the_conflict(
    module_seeded_url, client
):
    setup = path_a(module_seeded_url, client)
    ctx: Ctx = setup["ctx"]
    cancel_blocker(module_seeded_url, setup["blocker"])

    response = approve_as_owner(client, ctx, setup["request_id"], setup["at"])

    assert code_of(response) == (200, None), response.text
    row = state_of(module_seeded_url, setup["session_id"])
    assert (row.state, row.tutor_user_id) == ("confirmed", setup["tutor_b"].id)
    assert request_status(module_seeded_url, setup["request_id"]) == "approved"
    # A2: the tutor change and the state change are update events by the
    # reviewer (one or two events).
    updates = [
        e
        for e in events(module_seeded_url, record_id=setup["session_id"])
        if e.action == "update"
        and (
            (e.changes or {}).get("tutor_user_id", {}).get("new")
            == str(setup["tutor_b"].id)
            or (e.changes or {}).get("state", {}).get("new") == "confirmed"
        )
    ]
    assert updates
    assert {e.actor_user_id for e in updates} == {ctx.owner.id}
    changed = {key for e in updates for key in (e.changes or {})}
    assert {"tutor_user_id", "state"} <= changed
    assert_invariant(module_seeded_url, setup["series_id"])


def test_approve_with_the_new_tutor_busy_is_a_tutor_overlap_and_changes_nothing(
    module_seeded_url, client
):
    setup = path_a(module_seeded_url, client)
    ctx: Ctx = setup["ctx"]
    cancel_blocker(module_seeded_url, setup["blocker"])
    existing_session(
        module_seeded_url,
        state="confirmed",
        tutor_user_id=setup["tutor_b"].id,
        student_id=new_student(module_seeded_url),
        starts_at=ctx.tl.at(setup["week13"], "16:00"),
    )

    response = approve_as_owner(client, ctx, setup["request_id"], setup["at"])

    assert code_of(response) == (409, "scheduling_conflict"), response.text
    assert [c["code"] for c in response.json()["conflicts"]] == ["tutor_overlap"]
    row = state_of(module_seeded_url, setup["session_id"])
    assert (row.state, row.tutor_user_id) == ("conflict", None)
    assert request_status(module_seeded_url, setup["request_id"]) == "pending"


def test_approve_confirms_with_tutor_b_after_tutor_a_declined(
    module_seeded_url, client
):
    """Path B: the conflict row is stored with tutor A (the series offer is
    pending at night); A declines, the series is offered to B, B accepts."""
    url = module_seeded_url
    ctx = offers_ctx(url)
    created = make_series(client, ctx, tutor=False, count=26)
    week13 = ctx.tl.week(13)
    # Offered to A an hour before the night run (from today).
    before = ctx.now + timedelta(weeks=1) - timedelta(hours=1)
    cookie = as_user(client, ctx.owner, before)
    with frozen_clock(before):
        offered = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(ctx.tutor.id),
            version=created["version"],
        )
    assert code_of(offered) == CREATED, offered.text
    blocker, request_id = conflict_row(url, ctx, created["id"], week13)
    conflict = conflict_row_id(url, created["id"])
    assert conflict.tutor_user_id == ctx.tutor.id
    after = ctx.now + timedelta(weeks=1, hours=1)
    cookie = as_user(client, ctx.tutor, after)
    with frozen_clock(after):
        declined = post(
            client, cookie, f"{OFFERS}/{offered.json()['offer']['id']}/decline", {}
        )
    assert code_of(declined) == (200, None), declined.text
    assert series_state(url, created["id"]).tutor_user_id is None
    # The conflict row keeps the tutor it was stored with.
    assert state_of(url, conflict.id).tutor_user_id == ctx.tutor.id
    tutor_b = new_tutor_user(url, qualified_for=[ctx.subject])
    offer_and_accept(client, ctx, created["id"], tutor_b, after + timedelta(hours=1))
    cancel_blocker(url, blocker)

    response = approve_as_owner(client, ctx, request_id, after + timedelta(hours=2))

    assert code_of(response) == (200, None), response.text
    row = state_of(url, conflict.id)
    assert (row.state, row.tutor_user_id) == ("confirmed", tutor_b.id)
    assert request_status(url, request_id) == "approved"
    assert_invariant(url, created["id"])
