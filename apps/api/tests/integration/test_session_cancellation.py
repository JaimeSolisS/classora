"""Cancelling one session or occurrence (#55 C, F3, G3; I3), with real logins
through the real app.

Each test takes its own decade (``series_edit_support.make_ctx``): "now" is
the Sunday 12:00 (Mexico City) before the first Monday, and every call
runs under a frozen clock. One-off sessions are on the second Tuesday at 10:00.
"""

import uuid
from datetime import UTC
from typing import Any

import pytest
from httpx import Response

from app.modules.identity.permissions import ACADEMIC_COORDINATOR, ADMINISTRATOR
from app.modules.scheduling import cancellation
from app.workflows.session_cancellation import on_session_cancelled
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_edit_support import (
    CREATED,
    NOT_FOUND,
    OK,
    SESSIONS,
    Ctx,
    add_closure,
    as_user,
    cancel_series,
    conflict_requests,
    edit_body,
    edit_series,
    hours,
    make_conflict,
    make_ctx,
    make_series,
    notes,
    on_day,
    owner_call,
    row_by_id,
    rows_of,
    run_job,
    series_row,
    set_row,
    with_family,
)
from tests.integration.test_assignment_offers import new_tutor_user, reassignment_items
from tests.integration.test_class_session_create import new_room, new_student

INVALID = (422, "validation_error")
NOT_CANCELLABLE = (409, "class_session_not_cancellable")
CANCELLED = "scheduling.session_cancelled"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def ctx(module_seeded_url: str, client: Any) -> Ctx:
    return make_ctx(module_seeded_url, client)


# --- Helpers -------------------------------------------------------------------------


def one_off(ctx: Ctx, *, tutor: bool = True, accept: bool = False, **body: Any) -> Any:
    """A one-off session on the second Tuesday at 10:00: ``offered`` to the
    ctx tutor (``accept``: ``confirmed``) or ``unassigned``."""
    start = body.pop("starts_at", ctx.tl.at(ctx.tl.week(2, 2), "10:00"))
    payload = {
        "kind": "individual",
        "student_id": str(body.pop("student", ctx.student)),
        "subject_id": str(ctx.subject),
        "tutor_user_id": str(ctx.tutor.id) if tutor else None,
        "classroom_id": str(body.pop("room", ctx.room)),
        "starts_at": start.isoformat(),
        "ends_at": (start + hours(1)).isoformat(),
        **body,
    }
    response = owner_call(
        ctx, ctx.tl.now, lambda cookie: post(ctx.client, cookie, SESSIONS, payload)
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    if accept:
        cookie = as_user(ctx, ctx.tutor, ctx.tl.now)
        offer_id = created["pending_offer"]["id"]
        with frozen_clock(ctx.tl.now):
            answered = post(
                ctx.client, cookie, f"/api/v1/assignment-offers/{offer_id}/accept", None
            )
        assert code_of(answered) == OK, answered.text
    return read(ctx, created["id"])


def read(ctx: Ctx, session_id: Any) -> dict[str, Any]:
    response = owner_call(
        ctx,
        ctx.tl.now,
        lambda cookie: get(ctx.client, cookie, f"{SESSIONS}/{session_id}"),
    )
    assert code_of(response) == OK, response.text
    found: dict[str, Any] = response.json()
    return found


def cancel(
    ctx: Ctx,
    session_id: Any,
    version: int,
    reason: Any = "Cierre por mantenimiento",
    *,
    user: Any = None,
    at: Any = None,
) -> Response:
    body: dict[str, Any] = {"version": version}
    if reason is not None:
        body["reason"] = reason
    cookie = as_user(ctx, user or ctx.owner, at or ctx.tl.now)
    with frozen_clock(at or ctx.tl.now):
        return post(ctx.client, cookie, f"{SESSIONS}/{session_id}/cancel", body)


def substitute(ctx: Ctx, session: Any, substitute_id: Any) -> dict[str, Any]:
    response = owner_call(
        ctx,
        ctx.tl.now,
        lambda cookie: post(
            ctx.client,
            cookie,
            f"{SESSIONS}/{session['id']}/substitutions",
            {
                "substitute_tutor_user_id": str(substitute_id),
                "version": session["version"],
                "reason": "Enfermedad",
            },
        ),
    )
    assert code_of(response) == CREATED, response.text
    item: dict[str, Any] = response.json()["substitution"]
    return item


def offer_status(url: str, offer_id: Any) -> str:
    [row] = run_sql(
        url,
        "SELECT status FROM assignment_offers WHERE id = :id",
        {"id": str(offer_id)},
    )
    return str(row.status)


def cancelled_notes(ctx: Ctx, session_id: Any) -> list[Any]:
    return notes(ctx.url, CANCELLED, "class_session_id", session_id)


# --- C1: each cancellable state ------------------------------------------------------


@pytest.mark.parametrize("state", ["unassigned", "offered", "confirmed"])
def test_a_session_is_cancelled_with_its_reason(ctx, state):
    session = one_off(ctx, tutor=state != "unassigned", accept=state == "confirmed")
    assert session["state"] == state

    response = cancel(ctx, session["id"], session["version"], "  Cierre por obra  ")

    assert code_of(response) == OK, response.text
    found = response.json()
    assert found["state"] == "cancelled"
    assert found["cancellation"] == {
        "cancelled_at": ctx.tl.now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "reason": "Cierre por obra",
    }
    assert found["pending_offer"] is None
    row = row_by_id(ctx.url, session["id"])
    assert (row.state, row.cancellation_reason, row.cancelled_at) == (
        "cancelled",
        "Cierre por obra",
        ctx.tl.now,
    )


def test_an_offered_session_withdraws_its_offer_and_tells_the_tutor(ctx):
    student_user, guardian_user = with_family(ctx.url, ctx.student)
    session = one_off(ctx)
    offer_id = session["pending_offer"]["id"]

    response = cancel(ctx, session["id"], session["version"])

    assert code_of(response) == OK, response.text
    assert offer_status(ctx.url, offer_id) == "withdrawn"
    told = cancelled_notes(ctx, session["id"])
    assert [n.recipient_user_id for n in told] == [ctx.tutor.id]
    assert told[0].params == {
        "class_session_id": session["id"],
        "starts_at": session["starts_at"],
    }
    # The family never saw an offered session.
    assert not {student_user, guardian_user} & {n.recipient_user_id for n in told}


def test_a_confirmed_session_with_a_pending_substitution(ctx):
    student_user, guardian_user = with_family(ctx.url, ctx.student)
    _, stranger = with_family(ctx.url, new_student(ctx.url))
    session = one_off(ctx, accept=True)
    replacement = new_tutor_user(ctx.url, qualified_for=[ctx.subject])
    item = substitute(ctx, session, replacement.id)

    response = cancel(ctx, session["id"], session["version"])

    assert code_of(response) == OK, response.text
    assert response.json()["pending_substitution"] is None
    assert offer_status(ctx.url, item["offer_id"]) == "withdrawn"
    told = {n.recipient_user_id for n in cancelled_notes(ctx, session["id"])}
    assert told == {ctx.tutor.id, replacement.id, student_user, guardian_user}
    assert ctx.owner.id not in told and stranger not in told
    for note in cancelled_notes(ctx, session["id"]):
        assert set(note.params) == {"class_session_id", "starts_at"}


def test_the_actor_is_never_told(ctx, module_seeded_url):
    session = one_off(ctx, accept=True)
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))

    response = cancel(ctx, session["id"], session["version"], user=admin)

    assert code_of(response) == OK, response.text
    told = {n.recipient_user_id for n in cancelled_notes(ctx, session["id"])}
    assert admin.id not in told and ctx.tutor.id in told


def test_an_unassigned_session_closes_its_reassignment_item(ctx):
    session = one_off(ctx)
    offer_id = session["pending_offer"]["id"]
    cookie = as_user(ctx, ctx.tutor, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        declined = post(
            ctx.client, cookie, f"/api/v1/assignment-offers/{offer_id}/decline", None
        )
    assert code_of(declined) == OK
    session = read(ctx, session["id"])
    assert session["state"] == "unassigned"

    response = cancel(ctx, session["id"], session["version"])

    assert code_of(response) == OK, response.text
    [item] = reassignment_items(ctx.url, offer_id)
    assert (item.status, item.resolved_by_user_id) == ("cancelled", ctx.owner.id)
    assert cancelled_notes(ctx, session["id"]) == []


def test_a_conflict_row_closes_its_inbox_request(ctx):
    created = make_series(ctx)
    rows = rows_of(ctx.url, created["id"])
    row = on_day(rows, ctx, ctx.tl.week(2))
    make_conflict(ctx.url, row.id, ["tutor_unavailable"])
    row = row_by_id(ctx.url, row.id)

    response = cancel(ctx, row.id, row.version)

    assert code_of(response) == OK, response.text
    [request] = conflict_requests(ctx.url, row.id)
    assert request.status == "cancelled"
    assert cancelled_notes(ctx, row.id) == []


def test_a_row_covered_by_a_series_offer_stays_covered(ctx):
    created = make_series(ctx, tutor=True)
    offer_id = created["assignment"]["offer_id"]
    rows = rows_of(ctx.url, created["id"])
    row = on_day(rows, ctx, ctx.tl.week(1))
    assert row.state == "offered"

    response = cancel(ctx, row.id, row.version)

    assert code_of(response) == OK, response.text
    assert offer_status(ctx.url, offer_id) == "pending"
    # The tutor of the offered row is told.
    assert [n.recipient_user_id for n in cancelled_notes(ctx, row.id)] == [ctx.tutor.id]
    cookie = as_user(ctx, ctx.tutor, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        accepted = post(
            ctx.client, cookie, f"/api/v1/assignment-offers/{offer_id}/accept", None
        )
    assert code_of(accepted) == OK, accepted.text
    states = {r.id: r.state for r in rows_of(ctx.url, created["id"])}
    assert states.pop(row.id) == "cancelled"
    assert set(states.values()) == {"confirmed"}


def test_the_named_audit_event_and_the_seam(ctx, monkeypatch):
    assert on_session_cancelled in cancellation.registered_cancellation_steps()
    calls: list[dict[str, Any]] = []

    async def spy(session: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cancellation, "_steps", [spy])
    session = one_off(ctx)
    offer_id = session["pending_offer"]["id"]

    response = cancel(ctx, session["id"], session["version"])

    assert code_of(response) == OK, response.text
    assert calls == [
        {
            "class_session_id": uuid.UUID(session["id"]),
            "initiated_by": "center",
            "cancelled_at": ctx.tl.now,
            "by_user_id": ctx.owner.id,
        }
    ]
    found = events(
        ctx.url, record_type="class_sessions", record_id=uuid.UUID(session["id"])
    )
    [named] = [e for e in found if e.action == "scheduling.session_cancelled"]
    assert named.actor_user_id == ctx.owner.id
    assert named.details == {
        "class_session_id": session["id"],
        "series_id": None,
        "previous_state": "offered",
        "reason": "Cierre por mantenimiento",
        "withdrawn_offer_ids": [offer_id],
        "cancelled_request_ids": [],
    }
    assert "update" in [e.action for e in found]


# --- C1: refusals --------------------------------------------------------------------


def test_each_refusal(ctx, module_seeded_url):
    url = ctx.url
    assert code_of(cancel(ctx, uuid.uuid4(), 1)) == NOT_FOUND
    session = one_off(ctx, accept=True)
    assert code_of(cancel(ctx, session["id"], session["version"] + 3)) == (
        409,
        "version_conflict",
    )
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
    denied = cancel(ctx, session["id"], session["version"], user=coordinator)
    assert code_of(denied) == (403, "permission_denied")
    for reason, code in (
        (None, "reason_required"),
        ("  ", "reason_required"),
        ("x" * 1001, "reason_too_long"),
    ):
        response = cancel(ctx, session["id"], session["version"], reason)
        assert code_of(response) == INVALID
        assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
            (["body", "reason"], code)
        ]

    # Started: at its start, refused.
    started = cancel(
        ctx,
        session["id"],
        session["version"],
        at=ctx.tl.at(ctx.tl.week(2, 2), "10:00"),
    )
    assert code_of(started) == NOT_CANCELLABLE
    assert started.json()["details"] == {"reason": "started"}

    # Final or skipped states.
    for state in ("completed", "skipped_closure"):
        other = one_off(
            ctx,
            accept=True,
            student=new_student(url),
            room=new_room(url),
            starts_at=ctx.tl.at(
                ctx.tl.week(2, 2), "12:00" if state == "completed" else "14:00"
            ),
        )
        set_row(url, other["id"], state=state)
        refused = cancel(ctx, other["id"], row_by_id(url, other["id"]).version)
        assert code_of(refused) == NOT_CANCELLABLE
        assert refused.json()["details"] == {"reason": "state", "state": state}
    done = cancel(ctx, session["id"], session["version"])
    assert code_of(done) == OK
    again = cancel(ctx, session["id"], done.json()["version"])
    assert again.json()["details"] == {"reason": "state", "state": "cancelled"}
    assert (
        row_by_id(url, session["id"]).cancellation_reason == "Cierre por mantenimiento"
    )


# --- C2: series occurrences ----------------------------------------------------------


def test_a_cancelled_occurrence_is_never_re_created(ctx):
    created = make_series(ctx, tutor=True, accept=True)
    before = series_row(ctx.url, created["id"])
    rows = rows_of(ctx.url, created["id"])
    row = on_day(rows, ctx, ctx.tl.week(2, 3))
    others = {r.id: dict(r._mapping) for r in rows if r.id != row.id}

    response = cancel(ctx, row.id, row.version)
    assert code_of(response) == OK, response.text
    cancelled = row_by_id(ctx.url, row.id)
    assert (cancelled.series_id, cancelled.original_start) == (
        row.series_id,
        row.original_start,
    )
    run_job(ctx.url, ctx.tl.now + hours(1))
    run_job(ctx.url, ctx.tl.now + hours(2))

    after = rows_of(ctx.url, created["id"])
    on_date = [r for r in after if r.original_start == row.original_start]
    assert [r.id for r in on_date] == [row.id]
    assert {r.id: dict(r._mapping) for r in after if r.id != row.id} == others
    series = series_row(ctx.url, created["id"])
    assert (series.version, series.rrule, series.assignment_status) == (
        before.version,
        before.rrule,
        before.assignment_status,
    )
    [named] = [
        e
        for e in events(ctx.url, record_type="class_sessions", record_id=row.id)
        if e.action == "scheduling.session_cancelled"
    ]
    assert named.details["series_id"] == str(created["id"])
    assert named.details["previous_state"] == "confirmed"


# --- G3: other writers withdraw a pending substitution ----------------------------


def _series_with_substitution(ctx: Ctx) -> tuple[Any, Any, dict[str, Any]]:
    created = make_series(ctx, tutor=True, accept=True)
    row = on_day(rows_of(ctx.url, created["id"]), ctx, ctx.tl.week(3))
    replacement = new_tutor_user(ctx.url, qualified_for=[ctx.subject])
    item = substitute(ctx, read(ctx, row.id), replacement.id)
    return created, row, item


def test_a_series_row_reports_no_center_closure(ctx):
    created = make_series(ctx, tutor=True, accept=True)
    row = on_day(rows_of(ctx.url, created["id"]), ctx, ctx.tl.week(4))
    add_closure(ctx.url, ctx.tl.week(4))  # the nightly job hasn't run yet
    replacement = new_tutor_user(ctx.url, qualified_for=[ctx.subject])

    item = substitute(ctx, read(ctx, row.id), replacement.id)

    assert item["status"] == "pending"


def test_a_series_edit_withdraws_the_substitution(ctx):
    created, row, item = _series_with_substitution(ctx)

    response = edit_series(
        ctx,
        created["id"],
        ctx.tl.now,
        edit_body(
            mode="all",
            version=series_row(ctx.url, created["id"]).version,
            start_time="17:00",
            count=24,
        ),
    )

    assert code_of(response) == OK, response.text
    assert offer_status(ctx.url, item["offer_id"]) == "withdrawn"
    assert row_by_id(ctx.url, row.id).state == "confirmed"


def test_a_series_cancellation_withdraws_the_substitution(ctx):
    created, row, item = _series_with_substitution(ctx)

    response = cancel_series(
        ctx,
        created["id"],
        ctx.tl.now,
        {
            "mode": "all",
            "from_date": None,
            "reason": "Fin de curso",
            "version": series_row(ctx.url, created["id"]).version,
        },
    )

    assert code_of(response) == OK, response.text
    assert offer_status(ctx.url, item["offer_id"]) == "withdrawn"
    assert row_by_id(ctx.url, row.id).state == "cancelled"


def test_a_closure_skip_withdraws_the_substitution(ctx):
    _, row, item = _series_with_substitution(ctx)
    add_closure(ctx.url, ctx.tl.week(3))

    run_job(ctx.url, ctx.tl.now + hours(1))

    assert row_by_id(ctx.url, row.id).state == "skipped_closure"
    assert offer_status(ctx.url, item["offer_id"]) == "withdrawn"


def test_an_unchanged_row_keeps_its_substitution(ctx):
    created, row, item = _series_with_substitution(ctx)

    # Monday/Wednesday → Monday only: the Monday rows keep their slot.
    response = edit_series(
        ctx,
        created["id"],
        ctx.tl.now,
        edit_body(
            mode="all",
            version=series_row(ctx.url, created["id"]).version,
            weekdays=[1],
            count=12,
        ),
    )

    assert code_of(response) == OK, response.text
    assert offer_status(ctx.url, item["offer_id"]) == "pending"
