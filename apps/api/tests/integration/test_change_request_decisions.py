"""Staff decisions on change requests (#62 C, E, F; H1), with real logins
through the real app.

Helpers: ``change_request_support`` (each test takes its own decade; the
scene's confirmed session is on the second Tuesday at 10:00, about 214 hours
after "now").
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
)
from app.modules.scheduling import cancellation
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import SessionChangeRequest
from app.workflows import change_request_decision
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, post
from tests.integration.change_request_support import (
    APPROVED_TYPE,
    CANCELLED_TYPE,
    CHANGED,
    CONFLICT,
    INVALID,
    NOT_FOUND,
    NOT_PENDING,
    OK,
    REJECTED_TYPE,
    RESCHEDULED_TYPE,
    ROUTE_REQUIRED,
    STALE,
    STARTED,
    Scene,
    approval_of,
    approve,
    call,
    create,
    created,
    hours,
    make_scene,
    notes_of_type,
    notes_to,
    ok,
    read,
    reject,
    request_row,
    review,
)
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_edit_support import (
    add_closure,
    make_series,
    row_by_id,
    rows_of,
    run_job,
)
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_class_session_create import (
    new_student,
    revoke_all,
    time_off,
)
from tests.integration.test_session_cancellation import cancel, one_off, substitute

APPROVALS = "/api/v1/approval-requests"
REVIEW_KEYS = {
    "id",
    "kind",
    "status",
    "initiated_by",
    "requested_by",
    "student",
    "subject",
    "session",
    "session_starts_at",
    "session_ends_at",
    "proposed_starts_at",
    "proposed_ends_at",
    "note",
    "policy",
    "request_outcome",
    "final_outcome",
    "outcome",
    "decision",
    "decided_by",
    "approval_request_id",
    "created_at",
    "resolved_at",
    "version",
    "can_decide",
}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def scene(module_seeded_url: str, client: Any) -> Scene:
    return make_scene(module_seeded_url, client)


def z(value: datetime | None) -> str | None:
    return (
        None
        if value is None
        else value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    )


def late_cancellation(scene: Scene) -> dict[str, Any]:
    """A family cancellation 23 hours ahead: ``late``, no makeup."""
    return created(create(scene, scene.guardian, at=scene.start - hours(23)))


def reschedule_request(
    scene: Scene, proposed: datetime | None = None, **kwargs: Any
) -> dict[str, Any]:
    return created(
        create(
            scene,
            kwargs.pop("user", scene.guardian),
            kind="reschedule",
            proposed_starts_at=proposed or scene.start + hours(3),
            **kwargs,
        )
    )


def insert_request(
    scene: Scene,
    *,
    kind: str = "reschedule",
    proposed: datetime | None = None,
    created_at: datetime,
    classification: str = "on_time",
) -> uuid.UUID:
    """A pending family request written directly (no checks), with any
    ``created_at``, and its inbox item."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        from app.modules.administration import service as administration

        policy = await administration.get_scheduling_policy(session, created_at)
        row = SessionChangeRequest(
            id=uuid.uuid7(),
            class_session_id=uuid.UUID(scene.session["id"]),
            kind=kind,
            initiated_by="family",
            requested_by_user_id=scene.guardian.id,
            student_id=scene.ctx.student,
            session_starts_at=scene.start,
            session_ends_at=scene.start + hours(1),
            proposed_starts_at=proposed,
            proposed_ends_at=None if proposed is None else proposed + hours(1),
            policy_id=policy.id,
            classification=classification,
            makeup_allowed=False,
            makeup_until=None,
            status="pending",
            exception=False,
        )
        row.created_at = created_at
        session.add(row)
        await session.flush()
        await administration.create_approval_request(
            session,
            request_type="scheduling.change_request",
            subject_id=row.id,
            requester_user_id=scene.guardian.id,
        )
        return row.id

    return in_uow(scene.url, _make)


# --- C2: approving a cancellation ----------------------------------------------------


def test_approving_a_cancellation_cancels_through_the_session_service(
    scene, monkeypatch
):
    calls: list[dict[str, Any]] = []

    async def spy(session: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cancellation, "_steps", [spy])
    substitute_user = new_tutor_user(scene.url, qualified_for=[scene.ctx.subject])
    substitution = substitute(scene.ctx, scene.session, substitute_user.id)
    request = created(create(scene, scene.guardian, note="Viaje"))

    found = ok(approve(scene, request["id"], reason="  Entendido  "))

    session_id = uuid.UUID(scene.session["id"])
    assert found["class_session"]["state"] == "cancelled"
    assert found["class_session"]["cancellation"]["reason"] == "family_request"
    assert found["warnings"] == []
    decided = found["change_request"]
    assert set(decided) == REVIEW_KEYS
    assert decided["status"] == "approved"
    assert decided["decision"]["exception"] is False
    assert decided["decision"]["reason"] == "Entendido"
    assert decided["final_outcome"] == decided["request_outcome"]
    assert decided["can_decide"] is False
    row = request_row(scene.url, request["id"])
    assert (row.status, row.decided_by_user_id, row.version) == (
        "approved",
        scene.ctx.owner.id,
        2,
    )
    assert row_by_id(scene.url, session_id).cancellation_reason == "family_request"
    # #55: the pending substitution is withdrawn.
    [offer] = run_sql(
        scene.url,
        "SELECT o.status FROM assignment_offers o JOIN class_session_substitutions s"
        " ON s.offer_id = o.id WHERE s.id = :id",
        {"id": substitution["id"]},
    )
    assert offer.status == "withdrawn"
    # F2: the others hear about the cancellation, the requester doesn't.
    told = {
        n.recipient_user_id
        for n in notes_of_type(
            scene.url, CANCELLED_TYPE, "class_session_id", session_id
        )
    }
    assert scene.ctx.tutor.id in told
    assert substitute_user.id in told
    assert scene.other_guardian.id in told
    assert scene.guardian.id not in told
    assert scene.ctx.owner.id not in told
    # F1: the requester gets the decision.
    [note] = notes_to(scene.url, scene.guardian.id, APPROVED_TYPE)
    assert note.params == {
        "change_request_id": request["id"],
        "kind": "cancellation",
        "starts_at": z(scene.start),
        "new_starts_at": None,
        "makeup_until": z(scene.start + timedelta(days=30)),
        "exception": False,
    }
    assert notes_to(scene.url, scene.other_guardian.id, APPROVED_TYPE) == []
    # C2 step 12: the seam, once, with the request.
    assert calls == [
        {
            "class_session_id": session_id,
            "initiated_by": "family",
            "cancelled_at": scene.ctx.tl.now,
            "by_user_id": scene.ctx.owner.id,
            "change_request_id": uuid.UUID(request["id"]),
        }
    ]
    # C2 step 13 and #55's event.
    [named] = [
        e
        for e in events(
            scene.url,
            record_type="session_change_requests",
            record_id=uuid.UUID(request["id"]),
        )
        if e.action == "scheduling.change_request_decided"
    ]
    assert named.actor_user_id == scene.ctx.owner.id
    assert named.details == {
        "change_request_id": request["id"],
        "class_session_id": str(session_id),
        "kind": "cancellation",
        "initiated_by": "family",
        "decision": "approved",
        "exception": False,
        "classification": "on_time",
        "final_classification": "on_time",
        "final_makeup_until": (scene.start + timedelta(days=30))
        .astimezone(UTC)
        .isoformat(),
        "policy_id": str(row.policy_id),
        "reason": "Entendido",
    }
    [cancelled] = [
        e
        for e in events(scene.url, record_type="class_sessions", record_id=session_id)
        if e.action == "scheduling.session_cancelled"
    ]
    assert cancelled.details["change_request_id"] == request["id"]
    assert approval_of(scene.url, request["id"]).status == "approved"
    # The family reads the final outcome and the decision.
    seen = ok(read(scene, scene.guardian, request["id"]))
    assert seen["status"] == "approved"
    assert seen["decision"]["reason"] == "Entendido"
    assert seen["class_session"]["id"] == str(session_id)


def test_a_tutors_approved_cancellation_tells_the_family_without_naming_the_tutor(
    scene,
):
    request = created(create(scene, scene.ctx.tutor))

    ok(approve(scene, request["id"]))

    session_id = scene.session["id"]
    told = {
        n.recipient_user_id
        for n in notes_of_type(
            scene.url, CANCELLED_TYPE, "class_session_id", session_id
        )
    }
    assert told == {scene.guardian.id, scene.other_guardian.id}
    [note] = notes_to(scene.url, scene.ctx.tutor.id, APPROVED_TYPE)
    assert note.params["kind"] == "cancellation"
    assert row_by_id(scene.url, session_id).cancellation_reason == "tutor_request"
    # The family never sees the tutor's request.
    assert code_of(read(scene, scene.guardian, request["id"])) == NOT_FOUND


# --- C2: approving a reschedule ------------------------------------------------------


def test_approving_a_reschedule_moves_the_session(scene, monkeypatch):
    calls: list[dict[str, Any]] = []

    async def spy(session: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(change_request_decision, "on_session_rescheduled", spy)
    proposed = scene.start + hours(3)
    request = reschedule_request(scene, proposed)

    found = ok(approve(scene, request["id"]))

    session = found["class_session"]
    assert (session["state"], session["tutor"]["id"]) == (
        "confirmed",
        str(scene.ctx.tutor.id),
    )
    assert session["starts_at"] == z(proposed)
    assert session["ends_at"] == z(proposed + hours(1))
    assert session["classroom"]["id"] == str(scene.ctx.room)
    assert isinstance(found["warnings"], list)
    session_id = uuid.UUID(scene.session["id"])
    told = {
        n.recipient_user_id
        for n in notes_of_type(
            scene.url, RESCHEDULED_TYPE, "class_session_id", session_id
        )
    }
    assert told == {scene.ctx.tutor.id, scene.other_guardian.id}
    [note] = notes_to(scene.url, scene.guardian.id, APPROVED_TYPE)
    assert note.params["new_starts_at"] == z(proposed)
    assert note.params["makeup_until"] is None
    assert calls == [
        {
            "class_session_id": session_id,
            "previous_starts_at": scene.start,
            "previous_ends_at": scene.start + hours(1),
            "initiated_by": "family",
            "change_request_id": uuid.UUID(request["id"]),
            "by_user_id": scene.ctx.owner.id,
        }
    ]
    # The family's view follows the session.
    seen = ok(read(scene, scene.guardian, request["id"]))
    assert seen["class_session"]["starts_at"] == z(proposed)


def test_a_series_occurrence_becomes_an_exception(scene):
    ctx = scene.ctx
    series = make_series(ctx, tutor=True, accept=True, start_time="16:00")
    rows = rows_of(scene.url, series["id"])
    target = next(
        r
        for r in rows
        if r.starts_at.astimezone(ctx.tl.now.tzinfo).date() == ctx.tl.week(2, 3)
    )
    proposed = ctx.tl.at(ctx.tl.week(2, 3), "18:00")
    request = created(
        create(
            scene,
            scene.guardian,
            kind="reschedule",
            session_id=target.id,
            proposed_starts_at=proposed,
        )
    )

    ok(approve(scene, request["id"]))

    moved = row_by_id(scene.url, target.id)
    assert (moved.starts_at, moved.is_exception, moved.original_start) == (
        proposed,
        True,
        target.original_start,
    )
    assert moved.state == "confirmed"
    run_job(scene.url, ctx.tl.now + hours(1))
    again = [
        r
        for r in rows_of(scene.url, series["id"])
        if r.original_start == target.original_start
    ]
    assert len(again) == 1


def test_the_approver_may_move_the_reschedule_to_another_room(scene):
    proposed = scene.start + hours(3)
    request = reschedule_request(scene, proposed)
    one_off(
        scene.ctx,
        tutor=False,
        student=new_student(scene.url),
        room=scene.ctx.room,
        starts_at=proposed,
    )

    refused = approve(scene, request["id"])
    assert code_of(refused) == CONFLICT
    assert [c["code"] for c in refused.json()["conflicts"]] == ["room_overlap"]
    assert request_row(scene.url, request["id"]).status == "pending"

    found = ok(approve(scene, request["id"], classroom_id=scene.ctx.other_room))
    assert found["class_session"]["classroom"]["id"] == str(scene.ctx.other_room)


# --- C3: the slot changed since the request ------------------------------------------


@pytest.mark.parametrize(
    ("change", "codes"),
    [
        ("tutor_busy", ["tutor_overlap"]),
        ("tutor_time_off", ["tutor_unavailable"]),
        ("revoked", ["tutor_not_qualified"]),
        ("student_busy", ["student_overlap"]),
        ("closure", ["center_closed"]),
    ],
)
def test_approval_runs_every_check_live(scene, change, codes):
    proposed = scene.at(scene.ctx.tl.week(2, 3), "10:00")
    request = reschedule_request(scene, proposed)
    if change == "tutor_busy":
        one_off(
            scene.ctx,
            student=new_student(scene.url),
            room=scene.ctx.other_room,
            starts_at=proposed,
        )
    elif change == "tutor_time_off":
        time_off(scene.url, scene.ctx.tutor.id, scene.ctx.tl.week(2, 3))
    elif change == "revoked":
        revoke_all(scene.url, scene.ctx.tutor.id)
    elif change == "student_busy":
        one_off(scene.ctx, tutor=False, room=scene.ctx.other_room, starts_at=proposed)
    else:
        add_closure(scene.url, scene.ctx.tl.week(2, 3))

    response = approve(scene, request["id"])

    assert code_of(response) == CONFLICT, response.text
    assert [c["code"] for c in response.json()["conflicts"]] == codes
    assert request_row(scene.url, request["id"]).status == "pending"
    assert approval_of(scene.url, request["id"]).status == "pending"
    assert row_by_id(scene.url, scene.session["id"]).starts_at == scene.start


def test_a_soft_conflict_can_be_overridden_with_the_permission(scene):
    proposed = scene.at(scene.ctx.tl.week(2, 3), "10:00")
    request = reschedule_request(scene, proposed)
    time_off(scene.url, scene.ctx.tutor.id, scene.ctx.tl.week(2, 3))
    override = {"codes": ["tutor_unavailable"], "reason": "Lo confirmó por teléfono"}
    admin = create_user(scene.url, roles=(ADMINISTRATOR,))

    assert code_of(approve(scene, request["id"], override=override, user=admin)) == (
        403,
        "permission_denied",
    )
    found = ok(approve(scene, request["id"], override=override))
    assert [o["codes"] for o in found["class_session"]["overrides"]] == [
        ["tutor_unavailable"]
    ]


def test_the_booking_notice_counts_from_the_request(scene):
    # The proposal is one hour after the session (it overlaps only the
    # session itself); approved 2 hours before the proposal, while the
    # session hasn't started. The request was made 3 days ahead.
    proposed = scene.start + hours(1)
    request = insert_request(
        scene, proposed=proposed, created_at=proposed - timedelta(days=3)
    )

    response = approve(scene, request, at=proposed - hours(2))

    assert code_of(response) == OK, response.text
    assert row_by_id(scene.url, scene.session["id"]).starts_at == proposed


def test_a_request_made_inside_the_notice_breaks_it(scene):
    proposed = scene.start + hours(1)
    request = insert_request(scene, proposed=proposed, created_at=proposed - hours(2))

    response = approve(scene, request, at=proposed - timedelta(minutes=90))

    assert code_of(response) == CONFLICT, response.text
    assert [c["code"] for c in response.json()["conflicts"]] == ["notice_window"]


def test_a_session_staff_cancelled_meanwhile_is_changed(scene):
    request = created(create(scene, scene.guardian))
    current = row_by_id(scene.url, scene.session["id"])
    assert code_of(cancel(scene.ctx, scene.session["id"], current.version)) == OK

    response = approve(scene, request["id"])

    assert code_of(response) == CHANGED
    assert response.json()["details"] == {"state": "cancelled"}
    review_body = ok(review(scene, request["id"]))
    assert review_body["can_decide"] is False
    # Staff reject it instead.
    assert code_of(reject(scene, request["id"])) == OK


def test_a_started_session_can_no_longer_be_approved_but_can_be_rejected(scene):
    request = created(create(scene, scene.guardian))

    assert code_of(approve(scene, request["id"], at=scene.start)) == STARTED
    assert ok(review(scene, request["id"], at=scene.start))["can_decide"] is False
    rejected = ok(reject(scene, request["id"], at=scene.start + hours(2)))
    assert rejected["status"] == "rejected"


# --- C2 step 10: exceptions ----------------------------------------------------------


def test_an_exception_on_a_late_cancellation_is_excused_with_a_makeup(scene):
    request = late_cancellation(scene)
    assert request["outcome"]["classification"] == "late"

    found = ok(approve(scene, request["id"], exception=True, reason="Enfermedad"))

    decided = found["change_request"]
    assert decided["request_outcome"] == {
        "classification": "late",
        "makeup_allowed": False,
        "makeup_until": None,
    }
    assert decided["final_outcome"] == {
        "classification": "excused",
        "makeup_allowed": True,
        "makeup_until": z(scene.start + timedelta(days=30)),
    }
    assert decided["outcome"] == decided["final_outcome"]
    seen = ok(read(scene, scene.guardian, request["id"]))
    assert seen["outcome"]["classification"] == "excused"
    assert seen["decision"] == {
        "exception": True,
        "reason": "Enfermedad",
        "decided_at": seen["resolved_at"],
    }
    [note] = notes_to(scene.url, scene.guardian.id, APPROVED_TYPE)
    assert note.params["exception"] is True
    assert "reason" not in note.params


def test_an_exception_on_a_late_reschedule_has_no_makeup(scene):
    request = reschedule_request(scene, at=scene.start - hours(23))
    assert request["outcome"]["classification"] == "late"

    found = ok(approve(scene, request["id"], exception=True, reason="Enfermedad"))

    assert found["change_request"]["final_outcome"] == {
        "classification": "excused",
        "makeup_allowed": False,
        "makeup_until": None,
    }


def test_exception_errors(scene):
    tutor_request = created(create(scene, scene.ctx.tutor))

    missing = approve(scene, tutor_request["id"], exception=True)
    assert code_of(missing) == INVALID
    assert [(e["path"], e["code"]) for e in missing.json()["errors"]] == [
        (["body", "reason"], "reason_required")
    ]
    excused = approve(scene, tutor_request["id"], exception=True, reason="Ok")
    assert code_of(excused) == INVALID
    assert excused.json()["errors"][0]["code"] == "exception_not_applicable"
    room = approve(scene, tutor_request["id"], classroom_id=scene.ctx.other_room)
    assert code_of(room) == INVALID
    assert room.json()["errors"][0]["code"] == "classroom_not_applicable"
    assert request_row(scene.url, tutor_request["id"]).status == "pending"


def test_the_outcome_uses_the_request_time_policy_version(scene):
    request = created(create(scene, scene.guardian, at=scene.start - hours(30)))
    assert request["outcome"]["classification"] == "on_time"
    # A stricter version takes effect after the request.
    run_sql(
        scene.url,
        "INSERT INTO scheduling_policies (effective_from, booking_notice_hours,"
        " change_notice_hours, response_hours, makeup_after_on_time,"
        " makeup_after_late, makeup_after_no_show, makeup_window_days)"
        " VALUES (:at, 24, 300, 48, false, false, false, 90)",
        {"at": scene.start - hours(29)},
    )
    try:
        found = ok(
            approve(
                scene,
                request["id"],
                exception=True,
                reason="Ok",
                at=scene.start - hours(28),
            )
        )
        assert found["change_request"]["request_outcome"]["classification"] == "on_time"
        # The exception uses the stored version's 30-day window, not 90.
        assert found["change_request"]["final_outcome"]["makeup_until"] == z(
            scene.start + timedelta(days=30)
        )
        assert found["change_request"]["policy"]["id"] == str(
            request_row(scene.url, request["id"]).policy_id
        )
    finally:
        run_sql(
            scene.url,
            "DELETE FROM scheduling_policies WHERE effective_from = :at",
            {"at": scene.start - hours(29)},
        )


# --- C4: reject ----------------------------------------------------------------------


def test_rejecting_keeps_the_session_and_tells_the_requester(scene):
    request = created(create(scene, scene.guardian))

    assert code_of(reject(scene, request["id"], reason="   ")) == INVALID
    assert code_of(reject(scene, request["id"], reason="x" * 1001)) == INVALID
    assert code_of(reject(scene, request["id"], version=7)) == STALE
    found = ok(reject(scene, request["id"], reason="  No hay sustituto  "))

    assert found["status"] == "rejected"
    assert found["decision"] == {
        "exception": False,
        "reason": "No hay sustituto",
        "decided_at": found["resolved_at"],
    }
    assert found["final_outcome"] is None
    assert row_by_id(scene.url, scene.session["id"]).state == "confirmed"
    [note] = notes_to(scene.url, scene.guardian.id, REJECTED_TYPE)
    assert note.params == {
        "change_request_id": request["id"],
        "kind": "cancellation",
        "starts_at": z(scene.start),
    }
    item = approval_of(scene.url, request["id"])
    assert (item.status, item.reason) == ("rejected", "No hay sustituto")
    seen = ok(read(scene, scene.guardian, request["id"]))
    assert seen["decision"]["reason"] == "No hay sustituto"
    again = approve(scene, request["id"], version=2)
    assert code_of(again) == NOT_PENDING
    assert again.json()["details"] == {"status": "rejected"}


# --- C1: the review ------------------------------------------------------------------


def test_the_review_shows_the_session_live(scene):
    request = reschedule_request(scene, note="Por favor")

    found = ok(review(scene, request["id"]))

    assert set(found) == REVIEW_KEYS
    assert found["initiated_by"] == "family"
    assert found["requested_by"]["id"] == str(scene.guardian.id)
    assert found["session"]["tutor"]["id"] == str(scene.ctx.tutor.id)
    assert found["session"]["classroom"]["id"] == str(scene.ctx.room)
    assert found["session"]["state"] == "confirmed"
    assert found["can_decide"] is True
    assert found["final_outcome"] is None
    assert found["decision"] is None
    assert found["approval_request_id"] == str(approval_of(scene.url, request["id"]).id)
    assert code_of(review(scene, uuid.uuid4())) == NOT_FOUND

    # C1: at most six statements, with a room and a tutor (the most reads).
    with frozen_clock(scene.ctx.tl.now):
        statements, found_review = count_statements(
            scene.url,
            lambda session: scheduling.get_change_request_review(
                session, uuid.UUID(request["id"])
            ),
        )
    assert found_review.session.classroom is not None
    assert found_review.session.tutor is not None
    assert found_review.approval_request_id is not None
    assert statements <= 6


def test_only_owners_and_administrators_decide(scene):
    request = created(create(scene, scene.guardian))
    for role in (ACADEMIC_COORDINATOR, FINANCE_STAFF):
        user = create_user(scene.url, roles=(role,))
        assert code_of(review(scene, request["id"], user=user)) == (
            403,
            "permission_denied",
        )
        assert code_of(approve(scene, request["id"], user=user)) == (
            403,
            "permission_denied",
        )
        assert code_of(reject(scene, request["id"], user=user)) == (
            403,
            "permission_denied",
        )
    for user in (scene.guardian, scene.ctx.tutor):
        assert code_of(approve(scene, request["id"], user=user)) == (
            403,
            "permission_denied",
        )
    admin = create_user(scene.url, roles=(ADMINISTRATOR,))
    assert code_of(approve(scene, request["id"], user=admin)) == OK


# --- C6: the inbox handler -----------------------------------------------------------


def test_the_generic_inbox_decisions_point_to_the_review_page(scene):
    request = created(create(scene, scene.guardian))
    item = approval_of(scene.url, request["id"])

    def generic(action: str, body: Any) -> Any:
        return call(
            scene,
            scene.ctx.owner,
            None,
            lambda cookie: post(
                scene.ctx.client, cookie, f"{APPROVALS}/{item.id}/{action}", body
            ),
        )

    for action, body in (("approve", None), ("reject", {"reason": "No"})):
        response = generic(action, body)
        assert code_of(response) == ROUTE_REQUIRED, response.text
        assert response.json()["details"] == {
            "review_path": f"/schedule/change-requests/{request['id']}"
        }
    assert approval_of(scene.url, request["id"]).status == "pending"
    assert request_row(scene.url, request["id"]).status == "pending"


def test_the_inbox_summary(scene):
    request = late_cancellation(scene)

    async def _summaries(session: AsyncSession) -> Any:
        return await scheduling.CHANGE_REQUEST_APPROVAL_HANDLER.summaries(
            session, [uuid.UUID(request["id"])]
        )

    statements, found = count_statements(scene.url, _summaries)

    assert statements <= 3
    summary = found[uuid.UUID(request["id"])]
    assert summary["kind"] == "cancellation"
    assert summary["initiated_by"] == "family"
    assert summary["classification"] == "late"
    assert summary["status"] == "pending"
    assert summary["change_request_id"] == request["id"]
    assert summary["starts_at"] == z(scene.start)
    assert summary["proposed_starts_at"] is None
    assert summary["student_name"]


# --- F1: the requester must still be allowed -----------------------------------------


def test_an_unlinked_guardian_loses_the_request_and_its_notification(scene):
    request = created(create(scene, scene.guardian))
    run_sql(
        scene.url,
        "DELETE FROM student_guardians WHERE student_id = :s AND guardian_id ="
        " (SELECT id FROM guardians WHERE user_id = :u)",
        {"s": scene.ctx.student, "u": scene.guardian.id},
    )

    assert code_of(read(scene, scene.guardian, request["id"])) == NOT_FOUND
    ok(approve(scene, request["id"]))
    assert notes_to(scene.url, scene.guardian.id, APPROVED_TYPE) == []
    # The other guardian still hears about the cancellation.
    assert notes_to(scene.url, scene.other_guardian.id, CANCELLED_TYPE)


def test_stale_and_decided_requests(scene):
    request = created(create(scene, scene.guardian))

    assert code_of(approve(scene, request["id"], version=5)) == STALE
    ok(approve(scene, request["id"]))
    again = reject(scene, request["id"], version=2)
    assert code_of(again) == NOT_PENDING
    assert again.json()["details"] == {"status": "approved"}
    assert code_of(approve(scene, uuid.uuid4())) == NOT_FOUND
    # A new request on the cancelled session: the family no longer sees it,
    # its tutor is told it can't be changed.
    assert code_of(create(scene, scene.other_guardian)) == NOT_FOUND
    refused = create(scene, scene.ctx.tutor)
    assert code_of(refused) == (409, "class_session_not_changeable")
    assert refused.json()["details"] == {"state": "cancelled"}
