"""Substitutions (#55 A3, B, D, E, F1, F2, G3; I1) with real logins through
the real app.

As in ``test_assignment_offers`` (whose helpers this module reuses), the
clock is frozen at :data:`T0` (Wednesday 2031-01-01, 06:00 in Mexico City)
and the sessions are on Monday 2031-01-06, so the default deadline is
``T0 + 48 h``. Each test makes its own students, subjects, tutors and
rooms.
"""

import uuid
from datetime import date, timedelta
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
from app.modules.scheduling import service as scheduling
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_edit_support import with_family
from tests.integration.test_assignment_offers import (
    APPROVALS,
    DEFAULT_RESPOND_BY,
    OFFERS,
    SESSIONS,
    T0,
    Setup,
    answer,
    detail,
    new_tutor_user,
    notification_params,
    offered,
    payload,
    reassignment_items,
    recipients,
)
from tests.integration.test_class_session_create import (
    MONDAY,
    at,
    close_center,
    new_room,
    new_student,
    new_subject,
)
from tests.integration.test_class_session_warnings import set_limits

CREATED = (201, None)
OK = (200, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
CONFLICT = (409, "scheduling_conflict")
NOT_SUBSTITUTABLE = (409, "class_session_not_substitutable")
NOT_PENDING = (409, "offer_not_pending")


@pytest.fixture(scope="module", autouse=True)
def stopped_clock() -> Any:
    with frozen_clock(T0) as frozen:
        yield frozen


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(module_seeded_url: str) -> dict[str, CreatedUser]:
    url = module_seeded_url
    return {
        "owner": create_user(url, roles=(OWNER,)),
        "admin": create_user(url, roles=(ADMINISTRATOR,)),
        "coordinator": create_user(url, roles=(ACADEMIC_COORDINATOR,)),
        "finance": create_user(url, roles=(FINANCE_STAFF,)),
    }


@pytest.fixture(scope="module")
def owner(client: TestClient, staff: dict[str, CreatedUser]) -> Any:
    return sign_in(client, staff["owner"].username)


@pytest.fixture(scope="module")
def coordinator(client: TestClient, staff: dict[str, CreatedUser]) -> Any:
    return sign_in(client, staff["coordinator"].username)


@pytest.fixture
def setup(module_seeded_url: str) -> Setup:
    subject = new_subject(module_seeded_url)
    return Setup(
        student=new_student(module_seeded_url),
        subject=subject,
        tutor=new_tutor_user(module_seeded_url, qualified_for=[subject]),
        room=new_room(module_seeded_url),
    )


@pytest.fixture
def substitute(module_seeded_url: str, setup: Setup) -> CreatedUser:
    return new_tutor_user(module_seeded_url, qualified_for=[setup.subject])


# --- Helpers -------------------------------------------------------------------------


def confirmed(client: TestClient, owner: Any, setup: Setup, **kwargs: Any) -> Any:
    """A session the setup's tutor accepted: its detail."""
    created, offer_id = offered(client, owner, setup, **kwargs)
    tutor = sign_in(client, setup.tutor.username)
    accepted = answer(client, tutor, offer_id, "accept")
    assert code_of(accepted) == OK, accepted.text
    found = detail(client, owner, created["id"])
    assert found["state"] == "confirmed"
    return found


def substitute_with(
    client: TestClient, cookie: Any, session: Any, substitute_id: Any, **data: Any
) -> Response:
    body = {
        "substitute_tutor_user_id": str(substitute_id),
        "version": session["version"],
        "reason": "La tutora está enferma",
        **data,
    }
    return post(client, cookie, f"{SESSIONS}/{session['id']}/substitutions", body)


def substituted(
    client: TestClient, cookie: Any, session: Any, substitute_id: Any, **data: Any
) -> dict[str, Any]:
    response = substitute_with(client, cookie, session, substitute_id, **data)
    assert code_of(response) == CREATED, response.text
    item: dict[str, Any] = response.json()["substitution"]
    return item


def withdraw(
    client: TestClient, cookie: Any, session_id: Any, item_id: Any
) -> Response:
    return post(
        client,
        cookie,
        f"{SESSIONS}/{session_id}/substitutions/{item_id}/withdraw",
        None,
    )


def offer_status(url: str, offer_id: Any) -> str:
    [row] = run_sql(
        url,
        "SELECT status FROM assignment_offers WHERE id = :id",
        {"id": str(offer_id)},
    )
    return str(row.status)


def session_notes(url: str, notification_type: str, session_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = :t AND params->>'class_session_id' = :id",
        {"t": notification_type, "id": str(session_id)},
    )


def assert_invariants(url: str) -> None:
    """A3: ``offered`` iff exactly one pending assignment offer of the
    session's tutor; at most one pending substitution, only on a
    ``confirmed`` session with the original tutor and the snapshot's
    times; one substitution row per substitution offer."""
    rows = run_sql(
        url,
        "SELECT s.id, s.state, s.tutor_user_id, s.starts_at, s.ends_at,"
        " count(o.id) FILTER (WHERE o.status = 'pending'"
        "   AND o.kind = 'assignment') AS assignments,"
        " bool_and(o.tutor_user_id = s.tutor_user_id)"
        "   FILTER (WHERE o.status = 'pending' AND o.kind = 'assignment')"
        "   AS same_tutor,"
        " count(o.id) FILTER (WHERE o.status = 'pending'"
        "   AND o.kind = 'substitution') AS substitutions"
        " FROM class_sessions s"
        " LEFT JOIN assignment_offer_sessions x ON x.class_session_id = s.id"
        " LEFT JOIN assignment_offers o ON o.id = x.offer_id"
        " GROUP BY s.id",
    )
    for row in rows:
        if row.state == "offered":
            assert row.assignments in (0, 1), row
            if row.assignments == 1:
                assert row.same_tutor, row
        else:
            assert row.assignments == 0, row
        assert row.substitutions in (0, 1), row
        if row.substitutions:
            assert row.state == "confirmed", row
            [sub] = run_sql(
                url,
                "SELECT c.* FROM class_session_substitutions c"
                " JOIN assignment_offers o ON o.id = c.offer_id"
                " WHERE c.class_session_id = :id AND o.status = 'pending'",
                {"id": row.id},
            )
            assert sub.original_tutor_user_id == row.tutor_user_id, row
            assert (sub.slot_starts_at, sub.slot_ends_at) == (
                row.starts_at,
                row.ends_at,
            )
    [orphans] = run_sql(
        url,
        "SELECT count(*) AS n FROM assignment_offers o WHERE o.kind = 'substitution'"
        " AND (SELECT count(*) FROM class_session_substitutions c"
        "      WHERE c.offer_id = o.id) <> 1",
    )
    assert orphans.n == 0


# --- B2: substitute ------------------------------------------------------------------


def test_a_substitution_offers_the_session_and_leaves_it_confirmed(
    module_seeded_url, client, owner, setup, substitute, staff
):
    url = module_seeded_url
    session = confirmed(client, owner, setup)

    response = substitute_with(
        client, owner, session, substitute.id, reason="  La tutora está enferma  "
    )

    assert code_of(response) == CREATED, response.text
    created = response.json()
    item = created["substitution"]
    assert created["warnings"] == []
    assert item["status"] == "pending"
    assert item["class_session_id"] == session["id"]
    assert item["original_tutor"]["id"] == str(setup.tutor.id)
    assert item["substitute_tutor"]["id"] == str(substitute.id)
    assert item["requested_by"]["id"] == str(staff["owner"].id)
    assert item["reason"] == "La tutora está enferma"
    assert item["requested_at"] == "2031-01-01T12:00:00Z"
    assert item["respond_by"] == DEFAULT_RESPOND_BY.isoformat().replace("+00:00", "Z")
    assert (item["responded_at"], item["decline_note"]) == (None, None)
    # E1: the session is unchanged; it shows the pending substitution.
    after = detail(client, owner, session["id"])
    assert (after["state"], after["tutor"]["id"], after["version"]) == (
        "confirmed",
        str(setup.tutor.id),
        session["version"],
    )
    assert after["pending_offer"] is None
    assert after["pending_substitution"] == {
        "id": item["id"],
        "substitute": item["substitute_tutor"],
        "respond_by": item["respond_by"],
    }
    assert after["cancellation"] is None
    [offer] = run_sql(
        url, "SELECT * FROM assignment_offers WHERE id = :id", {"id": item["offer_id"]}
    )
    assert (offer.kind, offer.scope, offer.status, offer.tutor_user_id) == (
        "substitution",
        "session",
        "pending",
        substitute.id,
    )
    assert offer.offered_by_user_id == staff["owner"].id
    [row] = run_sql(
        url,
        "SELECT * FROM class_session_substitutions WHERE id = :id",
        {"id": item["id"]},
    )
    assert row.original_tutor_user_id == setup.tutor.id
    assert row.slot_starts_at == at(MONDAY, "10:00")
    assert row.slot_ends_at == at(MONDAY, "11:00")
    # F1: the substitute is told, with the kind and the original tutor, never
    # the reason.
    assert recipients(url, "scheduling.offer_received", item["offer_id"]) == {
        substitute.id
    }
    [params] = notification_params(url, "scheduling.offer_received", item["offer_id"])
    assert params == {
        "offer_id": item["offer_id"],
        "subject_name": session["subject"]["name"],
        "starts_at": "2031-01-06T16:00:00Z",
        "respond_by": "2031-01-03T12:00:00Z",
        "kind": "substitution",
        "original_tutor_name": item["original_tutor"]["display_name"],
    }
    # Audit: the offer, its session row and the substitution were created by
    # the Owner; the session has no new event.
    for record_type, record_id in (
        ("assignment_offers", item["offer_id"]),
        ("class_session_substitutions", item["id"]),
    ):
        [event] = events(url, record_type=record_type, record_id=uuid.UUID(record_id))
        assert (event.action, event.actor_user_id) == ("create", staff["owner"].id)
    assert_invariants(url)


def test_a_session_starting_soon_has_its_start_as_deadline(
    module_seeded_url, client, owner, setup, substitute
):
    start = at(date(2031, 1, 1), "16:00")  # T0 + 10 h
    session = confirmed(
        client,
        owner,
        setup,
        starts_at=start,
        override={"codes": ["notice_window"], "reason": "Urgente"},
    )

    item = substituted(client, owner, session, substitute.id)

    assert item["respond_by"] == "2031-01-01T22:00:00Z"
    # The booking notice is never re-checked for a substitution.


def test_each_refusal(module_seeded_url, client, owner, setup, substitute):
    url = module_seeded_url
    missing = {"id": str(uuid.uuid4()), "version": 1}
    assert code_of(substitute_with(client, owner, missing, substitute.id)) == NOT_FOUND

    session = confirmed(client, owner, setup)
    stale = substitute_with(
        client, owner, session | {"version": session["version"] + 5}, substitute.id
    )
    assert code_of(stale) == (409, "version_conflict")

    # Not confirmed: unassigned, offered (the create's states).
    other = Setup(
        student=new_student(url),
        subject=setup.subject,
        tutor=setup.tutor,
        room=new_room(url),
    )
    unassigned = post(
        client,
        owner,
        SESSIONS,
        payload(other, tutor=None, starts_at=at(MONDAY, "13:00")),
    ).json()
    was_offered, _ = offered(client, owner, other, starts_at=at(MONDAY, "15:00"))
    for target, state in ((unassigned, "unassigned"), (was_offered, "offered")):
        refused = substitute_with(client, owner, target, substitute.id)
        assert code_of(refused) == NOT_SUBSTITUTABLE, refused.text
        assert refused.json()["details"] == {"reason": "state", "state": state}

    # Forced states (factory): completed, skipped_closure, conflict,
    # cancelled.
    for state in ("completed", "skipped_closure", "conflict"):
        forced = confirmed(
            client,
            owner,
            Setup(new_student(url), setup.subject, setup.tutor, new_room(url)),
            starts_at=at(
                MONDAY,
                {"completed": "17:00", "skipped_closure": "18:00", "conflict": "19:00"}[
                    state
                ],
            ),
        )
        run_sql(
            url,
            "UPDATE class_sessions SET state = :s WHERE id = :id",
            {"s": state, "id": forced["id"]},
        )
        refused = substitute_with(client, owner, forced, substitute.id)
        assert refused.json()["details"] == {"reason": "state", "state": state}

    # Started.
    started = confirmed(
        client,
        owner,
        Setup(new_student(url), setup.subject, setup.tutor, new_room(url)),
        starts_at=at(MONDAY, "12:00"),
    )
    run_sql(
        url,
        "UPDATE class_sessions SET starts_at = :s, ends_at = :e WHERE id = :id",
        {"s": T0, "e": T0 + timedelta(hours=1), "id": started["id"]},
    )
    refused = substitute_with(client, owner, started, substitute.id)
    assert code_of(refused) == NOT_SUBSTITUTABLE
    assert refused.json()["details"] == {"reason": "started"}

    # A second substitution while one is pending.
    first = substituted(client, owner, session, substitute.id)
    other_substitute = new_tutor_user(url, qualified_for=[setup.subject])
    second = substitute_with(client, owner, session, other_substitute.id)
    assert code_of(second) == NOT_SUBSTITUTABLE
    assert second.json()["details"] == {
        "reason": "substitution_pending",
        "substitution_id": first["id"],
    }
    assert_invariants(url)


def test_the_422s(module_seeded_url, client, owner, setup, substitute):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    inactive = create_user(url, roles=(TUTOR,), state="suspended")

    def errors(substitute_id: Any, **data: Any) -> list[tuple[Any, str]]:
        response = substitute_with(client, owner, session, substitute_id, **data)
        assert code_of(response) == INVALID, response.text
        return [(e["path"], e["code"]) for e in response.json()["errors"]]

    path = ["body", "substitute_tutor_user_id"]
    assert errors(uuid.uuid4()) == [(path, "tutor_unknown")]
    assert errors(inactive.id) == [(path, "tutor_inactive")]
    assert errors(setup.tutor.id) == [(path, "substitute_is_current_tutor")]
    assert errors(substitute.id, respond_by="2031-01-03T12:01:00Z") == [
        (["body", "respond_by"], "respond_by_invalid")
    ]
    assert errors(substitute.id, reason="   ") == [
        (["body", "reason"], "reason_required")
    ]
    assert errors(substitute.id, reason="x" * 1001) == [
        (["body", "reason"], "reason_too_long")
    ]
    assert errors(substitute.id, tutor_user_id=str(substitute.id))[0][1] == (
        "extra_forbidden"
    )
    # Nothing was offered.
    assert detail(client, owner, session["id"])["pending_substitution"] is None


def test_the_substitute_is_checked(
    module_seeded_url, client, owner, coordinator, setup
):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    # Busy at that time: hard.
    busy = new_tutor_user(url, qualified_for=[setup.subject])
    elsewhere = Setup(new_student(url), setup.subject, busy, new_room(url))
    offered(client, owner, elsewhere)
    refused = substitute_with(client, owner, session, busy.id)
    assert code_of(refused) == CONFLICT
    assert [c["code"] for c in refused.json()["conflicts"]] == ["tutor_overlap"]

    # Not qualified: soft, needs an override (and schedule.override).
    unqualified = new_tutor_user(url)
    refused = substitute_with(client, owner, session, unqualified.id)
    assert [c["code"] for c in refused.json()["conflicts"]] == ["tutor_not_qualified"]
    override = {"codes": ["tutor_not_qualified"], "reason": "Cubre por hoy"}
    denied = substitute_with(
        client, coordinator, session, unqualified.id, override=override
    )
    assert code_of(denied) == (403, "permission_denied")
    allowed = substitute_with(client, owner, session, unqualified.id, override=override)
    assert code_of(allowed) == CREATED, allowed.text
    [stored] = run_sql(
        url,
        "SELECT codes, reason FROM class_session_overrides"
        " WHERE class_session_id = :id",
        {"id": session["id"]},
    )
    assert (stored.codes, stored.reason) == (["tutor_not_qualified"], "Cubre por hoy")
    assert [o["codes"] for o in detail(client, owner, session["id"])["overrides"]] == [
        ["tutor_not_qualified"]
    ]


def test_the_booking_notice_is_not_checked_again(
    module_seeded_url, client, owner, setup, substitute
):
    start = at(date(2031, 1, 1), "10:00")  # T0 + 4 h
    session = confirmed(
        client,
        owner,
        setup,
        starts_at=start,
        override={"codes": ["notice_window"], "reason": "Urgente"},
    )

    assert code_of(substitute_with(client, owner, session, substitute.id)) == CREATED


def test_a_closure_is_reported_for_a_one_off_session(
    module_seeded_url, client, owner, setup, substitute
):
    day = date(2031, 1, 13)
    session = confirmed(client, owner, setup, starts_at=at(day, "10:00"))
    close_center(module_seeded_url, day)

    refused = substitute_with(client, owner, session, substitute.id)

    assert [c["code"] for c in refused.json()["conflicts"]] == ["center_closed"]


def test_warnings_come_with_the_substitution(
    module_seeded_url, client, owner, setup, substitute
):
    session = confirmed(client, owner, setup)
    set_limits(client, owner, substitute.id, daily=30)

    response = substitute_with(client, owner, session, substitute.id)

    assert code_of(response) == CREATED, response.text
    assert [w["code"] for w in response.json()["warnings"]] == ["daily_limit_exceeded"]


# --- D1: accept ----------------------------------------------------------------------


def test_accept_hands_the_session_over(
    module_seeded_url, client, owner, setup, substitute
):
    url = module_seeded_url
    student_user, guardian_user = with_family(url, setup.student)
    _, stranger = with_family(url, new_student(url))
    session = confirmed(client, owner, setup)
    item = substituted(client, owner, session, substitute.id)
    cookie = sign_in(client, substitute.username)

    response = answer(client, cookie, item["offer_id"], "accept")

    assert code_of(response) == OK, response.text
    answered = response.json()
    assert (answered["kind"], answered["status"]) == ("substitution", "accepted")
    assert answered["substitution"] == {"original_tutor": item["original_tutor"]}
    after = detail(client, owner, session["id"])
    assert (after["state"], after["tutor"]["id"]) == ("confirmed", str(substitute.id))
    assert after["version"] == session["version"] + 1
    assert after["pending_substitution"] is None
    # The original tutor's slot is free again.
    elsewhere = Setup(new_student(url), setup.subject, setup.tutor, new_room(url))
    created = post(client, owner, SESSIONS, payload(elsewhere))
    assert code_of(created) == CREATED, created.text
    # F2: the original tutor and the family; never the substitute or a
    # stranger; no reason or name in the params.
    [told] = session_notes(url, "scheduling.tutor_substituted", session["id"])
    assert told.recipient_user_id == setup.tutor.id
    assert told.params == {
        "class_session_id": session["id"],
        "starts_at": "2031-01-06T16:00:00Z",
    }
    family = {
        n.recipient_user_id
        for n in session_notes(url, "scheduling.session_tutor_changed", session["id"])
    }
    assert family == {student_user, guardian_user}
    assert stranger not in family
    # Audit: the substitute changed the session and the offer.
    for record_type, record_id in (
        ("class_sessions", session["id"]),
        ("assignment_offers", item["offer_id"]),
    ):
        last = events(url, record_type=record_type, record_id=uuid.UUID(record_id))[-1]
        assert (last.action, last.actor_user_id) == ("update", substitute.id)
    assert_invariants(url)


def test_accept_when_the_substitute_was_booked_meanwhile(
    module_seeded_url, client, owner, setup, substitute
):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    item = substituted(client, owner, session, substitute.id)
    # B4: the slot isn't held; the substitute gets another session meanwhile.
    offered(
        client, owner, Setup(new_student(url), setup.subject, substitute, new_room(url))
    )
    cookie = sign_in(client, substitute.username)

    response = answer(client, cookie, item["offer_id"], "accept")

    assert code_of(response) == CONFLICT, response.text
    assert [c["code"] for c in response.json()["conflicts"]] == ["tutor_overlap"]
    assert offer_status(url, item["offer_id"]) == "pending"
    after = detail(client, owner, session["id"])
    assert (after["tutor"]["id"], after["version"]) == (
        str(setup.tutor.id),
        session["version"],
    )
    # The substitute can still decline.
    declined = answer(client, cookie, item["offer_id"], "decline")
    assert code_of(declined) == OK


def test_a_moved_session_withdraws_its_substitution(
    module_seeded_url, client, owner, setup, substitute
):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    item = substituted(client, owner, session, substitute.id)

    moved = patch(
        client,
        owner,
        f"{SESSIONS}/{session['id']}",
        {
            "version": session["version"],
            "starts_at": at(MONDAY, "12:00").isoformat(),
            "ends_at": at(MONDAY, "13:00").isoformat(),
        },
    )

    assert code_of(moved) == OK, moved.text
    assert moved.json()["pending_substitution"] is None
    assert offer_status(url, item["offer_id"]) == "withdrawn"
    cookie = sign_in(client, substitute.username)
    late = answer(client, cookie, item["offer_id"], "accept")
    assert code_of(late) == NOT_PENDING
    assert_invariants(url)


def test_a_session_changed_behind_the_service_is_no_longer_valid(
    module_seeded_url, client, owner, setup, substitute
):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    item = substituted(client, owner, session, substitute.id)
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'skipped_closure' WHERE id = :id",
        {"id": session["id"]},
    )
    cookie = sign_in(client, substitute.username)

    response = answer(client, cookie, item["offer_id"], "accept")

    assert code_of(response) == (409, "substitution_no_longer_valid")
    assert offer_status(url, item["offer_id"]) == "pending"
    [row] = run_sql(
        url,
        "SELECT state, tutor_user_id FROM class_sessions WHERE id = :id",
        {"id": session["id"]},
    )
    assert (row.state, row.tutor_user_id) == ("skipped_closure", setup.tutor.id)
    # Staff withdraw it (the safety net left it pending, A3 restored).
    assert code_of(withdraw(client, owner, session["id"], item["id"])) == OK
    assert_invariants(url)


# --- D2: decline ---------------------------------------------------------------------


def test_decline_leaves_the_session_and_asks_staff(
    module_seeded_url, client, owner, coordinator, setup, substitute, staff
):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    item = substituted(client, owner, session, substitute.id)
    cookie = sign_in(client, substitute.username)

    response = answer(client, cookie, item["offer_id"], "decline", {"note": "No puedo"})

    assert code_of(response) == OK, response.text
    assert response.json()["decline_note"] == "No puedo"
    after = detail(client, owner, session["id"])
    assert (after["state"], after["tutor"]["id"], after["version"]) == (
        "confirmed",
        str(setup.tutor.id),
        session["version"],
    )
    [inbox] = reassignment_items(url, item["offer_id"])
    assert (inbox.status, inbox.priority) == ("pending", "normal")
    shown = get(client, coordinator, f"{APPROVALS}/{inbox.id}")
    assert code_of(shown) == OK, shown.text
    summary = shown.json()["subject"]
    assert summary["kind"] == "substitution"
    assert summary["original_tutor_name"] == item["original_tutor"]["display_name"]
    assert summary["tutor_name"] == item["substitute_tutor"]["display_name"]
    assert summary["session_state"] == "confirmed"
    told = recipients(url, "scheduling.offer_declined", item["offer_id"])
    assert {staff["owner"].id, staff["coordinator"].id} <= told
    assert not {staff["finance"].id, substitute.id, setup.tutor.id} & told
    [params] = notification_params(url, "scheduling.offer_declined", item["offer_id"])[
        :1
    ]
    assert params["kind"] == "substitution"
    assert "reason" not in params

    # A new substitution closes the item; "Mark resolved" works on another.
    second = new_tutor_user(url, qualified_for=[setup.subject])
    again = substituted(client, owner, after, second.id)
    assert reassignment_items(url, item["offer_id"])[0].status == "cancelled"
    cookie = sign_in(client, second.username)
    assert code_of(answer(client, cookie, again["offer_id"], "decline")) == OK
    [other] = reassignment_items(url, again["offer_id"])
    resolved = post(client, coordinator, f"{APPROVALS}/{other.id}/approve", None)
    assert code_of(resolved) == OK, resolved.text
    assert_invariants(url)


# --- B5: withdraw --------------------------------------------------------------------


def test_withdraw(module_seeded_url, client, owner, coordinator, setup, substitute):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    item = substituted(client, owner, session, substitute.id)
    other = confirmed(
        client,
        owner,
        Setup(new_student(url), setup.subject, setup.tutor, new_room(url)),
        starts_at=at(MONDAY, "14:00"),
    )

    assert code_of(withdraw(client, coordinator, other["id"], item["id"])) == NOT_FOUND
    assert code_of(withdraw(client, coordinator, uuid.uuid4(), item["id"])) == NOT_FOUND
    response = withdraw(client, coordinator, session["id"], item["id"])

    assert code_of(response) == OK, response.text
    assert (response.json()["status"], response.json()["responded_at"]) == (
        "withdrawn",
        "2031-01-01T12:00:00Z",
    )
    after = detail(client, owner, session["id"])
    assert (after["version"], after["pending_substitution"]) == (
        session["version"],
        None,
    )
    assert reassignment_items(url, item["offer_id"]) == []
    assert recipients(url, "scheduling.offer_declined", item["offer_id"]) == set()
    again = withdraw(client, coordinator, session["id"], item["id"])
    assert code_of(again) == NOT_PENDING
    assert again.json()["details"] == {"status": "withdrawn"}
    # The substitute sees it among the answered offers.
    cookie = sign_in(client, substitute.username)
    answered = get(client, cookie, OFFERS, status="answered").json()["items"]
    assert [(o["id"], o["status"]) for o in answered] == [
        (item["offer_id"], "withdrawn")
    ]
    assert_invariants(url)


# --- IDOR ----------------------------------------------------------------------------


def test_only_the_substitute_can_answer(
    module_seeded_url, client, owner, coordinator, setup, substitute
):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    item = substituted(client, owner, session, substitute.id)
    users = [
        new_tutor_user(url),
        setup.tutor,  # the original tutor
        create_user(url, roles=(STUDENT,)),
        create_user(url, roles=(GUARDIAN,)),
    ]
    cookies = [sign_in(client, user.username) for user in users] + [coordinator]

    for cookie in cookies:
        for action in ("accept", "decline"):
            assert (
                code_of(answer(client, cookie, item["offer_id"], action)) == NOT_FOUND
            )
    assert offer_status(url, item["offer_id"]) == "pending"
    assert detail(client, owner, session["id"])["tutor"]["id"] == str(setup.tutor.id)


# --- E: reads ------------------------------------------------------------------------


def test_the_substitutions_route(module_seeded_url, client, owner, setup, staff):
    url = module_seeded_url
    finance = sign_in(client, staff["finance"].username)
    assert (
        code_of(get(client, finance, f"{SESSIONS}/{uuid.uuid4()}/substitutions"))
        == NOT_FOUND
    )
    session = confirmed(client, owner, setup)
    path = f"{SESSIONS}/{session['id']}/substitutions"
    assert get(client, finance, path).json() == {"items": []}

    made = []
    for minutes in range(5):
        substitute = new_tutor_user(url, qualified_for=[setup.subject])
        with frozen_clock(T0 + timedelta(minutes=minutes)):
            item = substituted(client, owner, session, substitute.id)
            assert code_of(withdraw(client, owner, session["id"], item["id"])) == OK
        made.append(item["id"])

    listed = get(client, finance, path).json()["items"]
    assert [i["id"] for i in listed] == list(reversed(made))
    assert {i["status"] for i in listed} == {"withdrawn"}
    assert listed[0]["reason"] == "La tutora está enferma"

    one = confirmed(
        client,
        owner,
        Setup(new_student(url), setup.subject, setup.tutor, new_room(url)),
        starts_at=at(MONDAY, "14:00"),
    )
    substituted(
        client, owner, one, new_tutor_user(url, qualified_for=[setup.subject]).id
    )
    budgets = [
        count_statements(
            url,
            lambda db, sid=sid: scheduling.list_session_substitutions(
                db, uuid.UUID(sid)
            ),
        )[0]
        for sid in (one["id"], session["id"])
    ]
    assert budgets[0] == budgets[1] <= 4


def test_the_detail_budget_with_a_pending_substitution(
    module_seeded_url, client, owner, setup, substitute
):
    session = confirmed(client, owner, setup)
    substituted(client, owner, session, substitute.id)

    statements, found = count_statements(
        module_seeded_url,
        lambda db: scheduling.get_class_session(db, uuid.UUID(session["id"])),
    )

    assert statements <= 7
    assert found.pending_substitution is not None
    assert found.pending_substitution.substitute.id == substitute.id


def test_my_offers_shows_the_substitution(
    module_seeded_url, client, owner, setup, substitute
):
    session = confirmed(client, owner, setup)
    item = substituted(client, owner, session, substitute.id)
    cookie = sign_in(client, substitute.username)

    [card] = get(client, cookie, OFFERS).json()["items"]

    assert card["id"] == item["offer_id"]
    assert card["kind"] == "substitution"
    assert card["substitution"] == {"original_tutor": item["original_tutor"]}
    assert "reason" not in str(card)
    statements, page = count_statements(
        module_seeded_url,
        lambda db: scheduling.list_my_offers(
            db, caller_id=substitute.id, status="all", limit=20, offset=0
        ),
    )
    assert statements <= 7
    assert page.items[0].substitution.original_tutor.id == setup.tutor.id
    # #52 G3: the session's offer history shows both kinds.
    history = get(client, owner, f"{SESSIONS}/{session['id']}/offers").json()["items"]
    assert [o["kind"] for o in history] == ["substitution", "assignment"]


# --- A2: the history is append-only (QA O1) -------------------------------------------


def test_substitution_rows_are_never_updated_or_deleted(
    module_seeded_url, client, owner, setup
):
    url = module_seeded_url
    session = confirmed(client, owner, setup)
    made: list[dict[str, Any]] = []
    for answer_with in ("withdraw", "decline", "accept"):
        substitute = new_tutor_user(url, qualified_for=[setup.subject])
        current = detail(client, owner, session["id"])
        item = substituted(client, owner, current, substitute.id)
        made.append(item)
        if answer_with == "withdraw":
            assert code_of(withdraw(client, owner, session["id"], item["id"])) == OK
        else:
            cookie = sign_in(client, substitute.username)
            assert code_of(answer(client, cookie, item["offer_id"], answer_with)) == OK
    # A last pending one, then the session is cancelled (it is withdrawn).
    last = new_tutor_user(url, qualified_for=[setup.subject])
    made.append(
        substituted(client, owner, detail(client, owner, session["id"]), last.id)
    )
    final = detail(client, owner, session["id"])
    cancelled = post(
        client,
        owner,
        f"{SESSIONS}/{session['id']}/cancel",
        {"version": final["version"], "reason": "Cierre"},
    )
    assert code_of(cancelled) == OK, cancelled.text

    ids = [item["id"] for item in made]
    rows = run_sql(
        url,
        "SELECT id, created_at, updated_at FROM class_session_substitutions"
        " WHERE class_session_id = :s",
        {"s": session["id"]},
    )
    assert sorted(str(row.id) for row in rows) == sorted(ids)
    assert all(row.created_at == row.updated_at for row in rows)
    for substitution_id in ids:
        actions = [
            event.action
            for event in events(
                url,
                record_type="class_session_substitutions",
                record_id=uuid.UUID(substitution_id),
            )
        ]
        assert actions == ["create"]
    statuses = [offer_status(url, item["offer_id"]) for item in made]
    assert statuses == ["withdrawn", "declined", "accepted", "withdrawn"]
