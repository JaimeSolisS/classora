"""Series offers (#233 C, D, E, F; I1) with real logins through the real app.

Each test takes its own decade (:func:`~tests.integration.series_support.
timeline`). "Now" is the **Friday** before the series' first Monday, 12:00
in Mexico City, so the first occurrence (Monday 16:00) is more than 48 hours
ahead (the default deadline is ``offered_at + 48 h``) and the default
12-week horizon still holds exactly 12 Mondays and 12 Wednesdays.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling import tasks
from app.modules.scheduling.models import ClassSession
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql, settings_for
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_support import (
    Timeline,
    create_series,
    iso_z,
    read_series,
    series_body,
    series_rows,
    timeline,
)
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_class_session_create import (
    change,
    existing_session,
    new_room,
    new_student,
    new_subject,
)

SERIES = "/api/v1/class-series"
SESSIONS = "/api/v1/class-sessions"
OFFERS = "/api/v1/assignment-offers"
APPROVALS = "/api/v1/approval-requests"
REASSIGNMENT = "scheduling.offer_reassignment"

CREATED = (201, None)
OK = (200, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
CONFLICT = (409, "scheduling_conflict")
NOT_OFFERABLE = (409, "series_not_offerable")
AUDIT_COUNT = "SELECT count(*) AS n FROM audit_events"
OFFER_COUNT = "SELECT count(*) AS n FROM assignment_offers"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@dataclass(frozen=True)
class Ctx:
    """A test's timeline, its references and a signed-in Owner."""

    tl: Timeline
    now: datetime
    owner: CreatedUser
    student: uuid.UUID
    subject: uuid.UUID
    tutor: CreatedUser
    room: uuid.UUID

    def body(self, *, tutor: bool = True, **changes: Any) -> dict[str, Any]:
        # 24 occurrences (the horizon) unless a test needs an open-ended
        # series: the nightly runs of other tests extend open-ended ones.
        changes.setdefault("count", 24)
        return series_body(
            student=self.student,
            subject=self.subject,
            tutor=self.tutor.id if tutor else None,
            room=self.room,
            starts_on=changes.pop("starts_on", self.tl.monday),
            **changes,
        )


@pytest.fixture
def ctx(module_seeded_url: str) -> Ctx:
    tl = timeline()
    subject = new_subject(module_seeded_url)
    return Ctx(
        tl=tl,
        now=tl.now - timedelta(days=2),
        owner=create_user(module_seeded_url, roles=(OWNER,)),
        student=new_student(module_seeded_url),
        subject=subject,
        tutor=new_tutor_user(module_seeded_url, qualified_for=[subject]),
        room=new_room(module_seeded_url),
    )


def as_user(client: TestClient, user: CreatedUser, at: datetime) -> Any:
    client.cookies.clear()
    with frozen_clock(at):
        return sign_in(client, user.username)


def make_series(client: TestClient, ctx: Ctx, **changes: Any) -> dict[str, Any]:
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        response = create_series(client, cookie, ctx.body(**changes))
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def offer_series(client: TestClient, cookie: Any, series_id: Any, **data: Any) -> Any:
    return post(client, cookie, f"{SERIES}/{series_id}/offers", data)


def answer(
    client: TestClient, ctx: Ctx, offer_id: Any, action: str, at: datetime, data=None
) -> Any:
    cookie = as_user(client, ctx.tutor, at)
    with frozen_clock(at):
        return post(client, cookie, f"{OFFERS}/{offer_id}/{action}", data)


def series_offers(url: str, series_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT o.*, (SELECT count(*) FROM assignment_offer_sessions x"
        " WHERE x.offer_id = o.id) AS covered FROM assignment_offers o"
        " WHERE o.class_series_id = :id ORDER BY o.offered_at, o.id",
        {"id": str(series_id)},
    )


def series_state(url: str, series_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT tutor_user_id, assignment_status, assignment_from, version"
        " FROM class_series WHERE id = :id",
        {"id": str(series_id)},
    )
    return row


def run_job(url: str, at: datetime) -> tasks.MaterializationResult:
    with frozen_clock(at):
        return asyncio.run(tasks.run_materialization(settings_for(url), now=at))


def notifications(url: str, notification_type: str, offer_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = :t AND params->>'offer_id' = :id",
        {"t": notification_type, "id": str(offer_id)},
    )


def reassignment_items(url: str, offer_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, status, priority FROM approval_requests"
        " WHERE request_type = :t AND subject_id = :s",
        {"t": REASSIGNMENT, "s": str(offer_id)},
    )


def assert_invariant(url: str, series_id: Any) -> None:
    """A3 for one series: every row is ``offered`` iff exactly one pending
    offer covers it, made to its tutor; the series assignment agrees with its
    series offers."""
    rows = run_sql(
        url,
        "SELECT s.id, s.state, s.tutor_user_id,"
        " count(o.id) FILTER (WHERE o.status = 'pending') AS pending,"
        " bool_and(o.tutor_user_id = s.tutor_user_id)"
        "   FILTER (WHERE o.status = 'pending') AS same_tutor"
        " FROM class_sessions s"
        " LEFT JOIN assignment_offer_sessions x ON x.class_session_id = s.id"
        " LEFT JOIN assignment_offers o ON o.id = x.offer_id"
        " WHERE s.series_id = :id GROUP BY s.id",
        {"id": str(series_id)},
    )
    for row in rows:
        if row.state == "offered":
            assert (row.pending, row.same_tutor) == (1, True), row
        else:
            assert row.pending == 0, row
    series = series_state(url, series_id)
    offers = series_offers(url, series_id)
    pending = [o for o in offers if o.status == "pending"]
    if series.assignment_status == "pending":
        [offer] = pending
        assert offer.tutor_user_id == series.tutor_user_id
        assert offer.from_date == series.assignment_from
    else:
        assert pending == []
    if series.assignment_status == "accepted":
        assert offers[-1].status == "accepted"
        assert offers[-1].tutor_user_id == series.tutor_user_id
    if series.assignment_status is None:
        assert series.tutor_user_id is None


# --- C1: a series created with a tutor ---------------------------------------------


def test_a_series_created_with_a_tutor_is_offered_in_one_offer(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx)

    rows = series_rows(module_seeded_url, created["id"])
    assert len(rows) == 24
    assert {r.state for r in rows} == {"offered"}
    [offer] = series_offers(module_seeded_url, created["id"])
    assert (offer.scope, offer.status, offer.tutor_user_id) == (
        "series",
        "pending",
        ctx.tutor.id,
    )
    assert (offer.from_date, offer.covered) == (ctx.tl.monday, 24)
    assert offer.offered_by_user_id == ctx.owner.id
    assert offer.offered_at == ctx.now
    assert offer.respond_by == ctx.now + timedelta(hours=48)
    series = series_state(module_seeded_url, created["id"])
    assert (series.assignment_status, series.assignment_from) == (
        "pending",
        ctx.tl.monday,
    )
    assert created["assignment"] == {
        "status": "pending",
        "tutor": created["tutor"],
        "from_date": ctx.tl.monday.isoformat(),
        "offer_id": str(offer.id),
        "respond_by": iso_z(offer.respond_by),
    }
    [notice] = notifications(module_seeded_url, "scheduling.offer_received", offer.id)
    assert notice.recipient_user_id == ctx.tutor.id
    assert notice.params == {
        "offer_id": str(offer.id),
        "subject_name": notice.params["subject_name"],
        "starts_at": iso_z(ctx.tl.at(ctx.tl.monday, "16:00")),
        "respond_by": iso_z(offer.respond_by),
        "class_series_id": created["id"],
        "from_date": ctx.tl.monday.isoformat(),
        "occurrence_count": 24,
    }
    # The session detail shows the covering series offer.
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        detail = get(client, cookie, f"{SESSIONS}/{rows[0].id}").json()
        history = get(client, cookie, f"{SESSIONS}/{rows[0].id}/offers").json()
    assert detail["pending_offer"]["id"] == str(offer.id)
    assert detail["pending_offer"]["scope"] == "series"
    assert [item["id"] for item in history["items"]] == [str(offer.id)]
    assert history["items"][0]["session_count"] == 24
    assert_invariant(module_seeded_url, created["id"])


def test_a_series_starting_within_48_hours_has_its_first_start_as_deadline(
    module_seeded_url, client, ctx
):
    # Sunday 12:00: the first occurrence (Monday 16:00) is 28 hours ahead.
    sunday = ctx.tl.now
    cookie = as_user(client, ctx.owner, sunday)
    with frozen_clock(sunday):
        response = create_series(client, cookie, ctx.body(count=2))

    assert code_of(response) == CREATED, response.text
    [offer] = series_offers(module_seeded_url, response.json()["id"])
    assert offer.respond_by == ctx.tl.at(ctx.tl.monday, "16:00")


def test_an_explicit_earlier_deadline_and_the_create_422s(
    module_seeded_url, client, ctx
):
    earlier = ctx.now + timedelta(hours=5, seconds=30)
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        kept = create_series(
            client, cookie, ctx.body(count=2, respond_by=earlier.isoformat())
        )
        without_tutor = create_series(
            client,
            cookie,
            ctx.body(
                tutor=False,
                count=2,
                starts_on=ctx.tl.week(2),
                respond_by=earlier.isoformat(),
            ),
        )
        too_late = create_series(
            client,
            cookie,
            ctx.body(
                count=2,
                starts_on=ctx.tl.week(3),
                respond_by=(ctx.now + timedelta(hours=49)).isoformat(),
            ),
        )
        past = create_series(
            client,
            cookie,
            ctx.body(
                count=2,
                starts_on=ctx.tl.week(4),
                respond_by=(ctx.now - timedelta(minutes=1)).isoformat(),
            ),
        )

    assert code_of(kept) == CREATED, kept.text
    [offer] = series_offers(module_seeded_url, kept.json()["id"])
    assert offer.respond_by == earlier.replace(second=0)
    for response, code in (
        (without_tutor, "respond_by_requires_tutor"),
        (too_late, "respond_by_invalid"),
        (past, "respond_by_invalid"),
    ):
        assert code_of(response) == INVALID, response.text
        assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
            (["body", "respond_by"], code)
        ]
    [count] = run_sql(
        module_seeded_url,
        "SELECT count(*) AS n FROM class_series WHERE subject_id = :s",
        {"s": str(ctx.subject)},
    )
    assert count.n == 1


def test_a_series_beyond_the_horizon_gets_an_offer_without_sessions(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx, starts_on=ctx.tl.week(20), count=None)

    assert created["occurrences"] == []
    [offer] = series_offers(module_seeded_url, created["id"])
    assert (offer.status, offer.covered) == ("pending", 0)
    assert offer.respond_by == ctx.now + timedelta(hours=48)
    [notice] = notifications(module_seeded_url, "scheduling.offer_received", offer.id)
    assert notice.params["occurrence_count"] == 0
    assert notice.params["starts_at"] == iso_z(ctx.tl.at(ctx.tl.week(20), "16:00"))
    # The nightly job, once the rows enter the horizon, attaches them.
    run_job(module_seeded_url, ctx.now + timedelta(weeks=8))
    [offer] = series_offers(module_seeded_url, created["id"])
    rows = series_rows(module_seeded_url, created["id"])
    assert rows and {r.state for r in rows} == {"offered"}
    assert offer.covered == len(rows)
    assert offer.respond_by == ctx.now + timedelta(hours=48)
    assert_invariant(module_seeded_url, created["id"])


def test_a_series_with_a_tutor_and_nothing_still_to_come_is_409(
    module_seeded_url, client, ctx
):
    # Friday 08:00, once, but "now" is Friday 12:00.
    friday = ctx.now.date()
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        response = create_series(
            client,
            cookie,
            ctx.body(starts_on=friday, start_time="08:00", weekdays=[5], count=1),
        )

    assert code_of(response) == NOT_OFFERABLE, response.text
    assert response.json()["details"] == {"reason": "ended"}


# --- D: answering a series offer --------------------------------------------------


def test_accept_confirms_every_row_and_later_rows_are_confirmed(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx, count=None)
    rows = series_rows(module_seeded_url, created["id"])
    [offer] = series_offers(module_seeded_url, created["id"])
    # A covered row cancelled before the accept stays cancelled.
    cancelled = rows[3].id
    cancelled_version = row_version(module_seeded_url, cancelled)

    async def _cancel(session: AsyncSession) -> None:
        await scheduling.transition_class_session(
            session,
            cancelled,
            to_state="cancelled",
            expected_version=cancelled_version,
            reason="Cancelada",
        )

    with frozen_clock(ctx.now):
        in_uow(module_seeded_url, _cancel)
    version = series_state(module_seeded_url, created["id"]).version

    response = answer(client, ctx, offer.id, "accept", ctx.now + timedelta(hours=1))

    assert code_of(response) == OK, response.text
    item = response.json()
    assert (item["scope"], item["status"]) == ("series", "accepted")
    states = {r.id: r.state for r in series_rows(module_seeded_url, created["id"])}
    assert states.pop(cancelled) == "cancelled"
    assert set(states.values()) == {"confirmed"}
    series = series_state(module_seeded_url, created["id"])
    assert (series.assignment_status, series.tutor_user_id) == (
        "accepted",
        ctx.tutor.id,
    )
    assert series.version == version + 1
    updates = [
        e
        for e in events(module_seeded_url, record_id=uuid.UUID(created["id"]))
        if e.action == "update"
    ]
    assert updates[-1].changes["assignment_status"] == {
        "old": "pending",
        "new": "accepted",
    }
    assert_invariant(module_seeded_url, created["id"])

    # One week later the job creates the new rows confirmed, with no offer
    # and no notification.
    received = len(
        run_sql(
            module_seeded_url,
            "SELECT id FROM notifications WHERE recipient_user_id = :u",
            {"u": ctx.tutor.id},
        )
    )
    run_job(module_seeded_url, ctx.now + timedelta(weeks=1))

    after = series_rows(module_seeded_url, created["id"])
    new = [r for r in after if r.id not in {r.id for r in rows}]
    assert len(new) == 2
    assert {(r.state, r.tutor_user_id) for r in new} == {("confirmed", ctx.tutor.id)}
    assert len(series_offers(module_seeded_url, created["id"])) == 1
    assert (
        len(
            run_sql(
                module_seeded_url,
                "SELECT id FROM notifications WHERE recipient_user_id = :u",
                {"u": ctx.tutor.id},
            )
        )
        == received
    )
    assert_invariant(module_seeded_url, created["id"])
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        detail = read_series(client, cookie, created["id"]).json()
    assert detail["assignment"] == {
        "status": "accepted",
        "tutor": detail["tutor"],
        "from_date": ctx.tl.monday.isoformat(),
        "offer_id": str(offer.id),
        "respond_by": None,
    }


def test_decline_returns_every_row_and_clears_the_series_tutor(
    module_seeded_url, client, ctx
):
    finance = create_user(module_seeded_url, roles=(FINANCE_STAFF,))
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
    created = make_series(client, ctx, count=None)
    [offer] = series_offers(module_seeded_url, created["id"])

    response = answer(
        client,
        ctx,
        offer.id,
        "decline",
        ctx.now + timedelta(hours=1),
        {"note": "No puedo los miércoles"},
    )

    assert code_of(response) == OK, response.text
    rows = series_rows(module_seeded_url, created["id"])
    assert {(r.state, r.tutor_user_id) for r in rows} == {("unassigned", None)}
    # The room is still held.
    assert {r.classroom_id for r in rows} == {ctx.room}
    series = series_state(module_seeded_url, created["id"])
    assert (series.tutor_user_id, series.assignment_status, series.assignment_from) == (
        None,
        None,
        None,
    )
    [item] = reassignment_items(module_seeded_url, offer.id)
    assert (item.status, item.priority) == ("pending", "normal")
    declined = notifications(module_seeded_url, "scheduling.offer_declined", offer.id)
    recipients = {n.recipient_user_id for n in declined}
    assert {ctx.owner.id, coordinator.id} <= recipients
    assert finance.id not in recipients
    assert ctx.tutor.id not in recipients
    params = declined[0].params
    assert "note" not in params and "decline_note" not in params
    assert params["class_session_id"] == str(rows[0].id)
    assert (params["class_series_id"], params["occurrence_count"]) == (
        created["id"],
        24,
    )
    assert params["from_date"] == ctx.tl.monday.isoformat()
    assert_invariant(module_seeded_url, created["id"])

    # The inbox summary carries the series.
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        shown = get(client, cookie, f"{APPROVALS}/{item.id}").json()
    assert shown["subject"]["class_series_id"] == created["id"]
    assert shown["subject"]["occurrence_count"] == 24
    assert shown["subject"]["from_date"] == ctx.tl.monday.isoformat()

    # Later rows are created unassigned.
    run_job(module_seeded_url, ctx.now + timedelta(weeks=1))
    new = [
        r
        for r in series_rows(module_seeded_url, created["id"])
        if r.id not in {row.id for row in rows}
    ]
    assert {(r.state, r.tutor_user_id) for r in new} == {("unassigned", None)}


def test_the_job_while_the_offer_is_pending_attaches_the_new_rows(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx, count=None)
    [before] = series_offers(module_seeded_url, created["id"])

    # Tuesday: the week-13 Monday enters the horizon.
    run_job(module_seeded_url, ctx.now + timedelta(days=4))

    rows = series_rows(module_seeded_url, created["id"])
    assert len(rows) == 25
    assert {r.state for r in rows} == {"offered"}
    [after] = series_offers(module_seeded_url, created["id"])
    assert (after.id, after.covered) == (before.id, 25)
    assert after.respond_by == before.respond_by
    assert_invariant(module_seeded_url, created["id"])


def test_only_the_offered_tutor_can_answer(module_seeded_url, client, ctx):
    created = make_series(client, ctx)
    [offer] = series_offers(module_seeded_url, created["id"])
    others = [
        new_tutor_user(module_seeded_url, qualified_for=[ctx.subject]),
        create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,)),
        create_user(module_seeded_url, roles=(STUDENT,)),
        create_user(module_seeded_url, roles=(GUARDIAN,)),
        ctx.owner,
    ]

    for user in others:
        cookie = as_user(client, user, ctx.now)
        with frozen_clock(ctx.now):
            for action in ("accept", "decline"):
                response = post(client, cookie, f"{OFFERS}/{offer.id}/{action}", None)
                assert code_of(response) == NOT_FOUND, (user.username, action)
            page = get(client, cookie, OFFERS, status="all").json()
        assert str(offer.id) not in {item["id"] for item in page["items"]}

    [offer] = series_offers(module_seeded_url, created["id"])
    assert offer.status == "pending"


# --- C2: offering a series without a tutor -----------------------------------------


def test_an_offer_from_week_3_covers_only_the_rows_from_week_3(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx, tutor=False)
    week3 = ctx.tl.week(3)
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))

    cookie = as_user(client, coordinator, ctx.now)
    with frozen_clock(ctx.now):
        response = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(ctx.tutor.id),
            version=created["version"],
            from_date=week3.isoformat(),
        )

    assert code_of(response) == CREATED, response.text
    body = response.json()
    assert body["warnings"] == []
    item = body["offer"]
    assert (item["scope"], item["status"], item["session_count"]) == (
        "series",
        "pending",
        20,
    )
    assert len(item["sessions"]) == 10 and item["sessions_truncated"] is True
    assert item["sessions"][0]["starts_at"] == iso_z(ctx.tl.at(week3, "16:00"))
    assert item["series"]["from_date"] == week3.isoformat()
    assert item["series"]["weekdays"] == [1, 3]
    assert item["series"]["start_time"] == "16:00"
    assert item["offered_by"]["id"] == str(coordinator.id)
    rows = series_rows(module_seeded_url, created["id"])
    assert [r.state for r in rows[:4]] == ["unassigned"] * 4
    assert {(r.state, r.tutor_user_id) for r in rows[4:]} == {("offered", ctx.tutor.id)}
    series = series_state(module_seeded_url, created["id"])
    assert (series.tutor_user_id, series.assignment_status, series.assignment_from) == (
        ctx.tutor.id,
        "pending",
        week3,
    )
    assert series.version == created["version"] + 1
    [notice] = notifications(module_seeded_url, "scheduling.offer_received", item["id"])
    assert notice.params["occurrence_count"] == 20
    assert_invariant(module_seeded_url, created["id"])

    # The tutor's list shows the series card data.
    cookie = as_user(client, ctx.tutor, ctx.now)
    with frozen_clock(ctx.now):
        page = get(client, cookie, OFFERS).json()
    [mine] = [i for i in page["items"] if i["id"] == item["id"]]
    assert mine == item


def test_the_default_from_date_is_today_and_a_started_row_is_not_covered(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx, tutor=False)
    # Monday of week 2, 16:30: that day's row has started.
    later = ctx.tl.at(ctx.tl.week(2), "16:30")

    cookie = as_user(client, ctx.owner, later)
    with frozen_clock(later):
        response = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(ctx.tutor.id),
            version=created["version"],
        )

    assert code_of(response) == CREATED, response.text
    assert response.json()["offer"]["series"]["from_date"] == (
        ctx.tl.week(2).isoformat()
    )
    rows = {r.starts_at: r for r in series_rows(module_seeded_url, created["id"])}
    assert rows[ctx.tl.at(ctx.tl.week(2), "16:00")].state == "unassigned"
    assert rows[ctx.tl.at(ctx.tl.week(2, 3), "16:00")].state == "offered"
    assert_invariant(module_seeded_url, created["id"])


def test_every_refusal_of_a_series_offer(module_seeded_url, client, ctx):
    created = make_series(client, ctx, tutor=False, count=4)
    inactive = new_tutor_user(
        module_seeded_url, qualified_for=[ctx.subject], state="suspended"
    )
    good = {"tutor_user_id": str(ctx.tutor.id), "version": created["version"]}
    finance = create_user(module_seeded_url, roles=(FINANCE_STAFF,))

    cookie = as_user(client, ctx.owner, ctx.now)
    with frozen_clock(ctx.now):
        unknown = offer_series(client, cookie, uuid.uuid4(), **good)
        stale = offer_series(client, cookie, created["id"], **good | {"version": 9})
        override = offer_series(
            client,
            cookie,
            created["id"],
            **good,
            override={"codes": ["tutor_unavailable"], "reason": "Sí"},
        )
        past = offer_series(
            client,
            cookie,
            created["id"],
            **good,
            from_date=(ctx.now.date() - timedelta(days=1)).isoformat(),
        )
        ended = offer_series(
            client,
            cookie,
            created["id"],
            **good,
            from_date=ctx.tl.week(3).isoformat(),
        )
        refs = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(inactive.id),
            version=created["version"],
            respond_by=(ctx.now + timedelta(hours=60)).isoformat(),
        )
        not_a_tutor = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(ctx.owner.id),
            version=created["version"],
        )
        offered = offer_series(client, cookie, created["id"], **good)
        again = offer_series(
            client, cookie, created["id"], **good | {"version": created["version"] + 1}
        )
    finance_cookie = as_user(client, finance, ctx.now)
    with frozen_clock(ctx.now):
        denied = offer_series(client, finance_cookie, created["id"], **good)

    assert code_of(unknown) == NOT_FOUND
    assert code_of(stale) == (409, "version_conflict")
    assert code_of(override) == INVALID
    assert [e["path"] for e in override.json()["errors"]] == [["body", "override"]]
    assert code_of(past) == INVALID
    assert [(e["path"], e["code"]) for e in past.json()["errors"]] == [
        (["body", "from_date"], "from_date_in_past")
    ]
    # count=4 ends on the week-2 Wednesday.
    assert code_of(ended) == NOT_OFFERABLE
    assert ended.json()["details"] == {"reason": "ended"}
    assert code_of(refs) == INVALID
    assert {e["code"] for e in refs.json()["errors"]} == {
        "tutor_inactive",
        "respond_by_invalid",
    }
    assert code_of(not_a_tutor) == INVALID
    assert [e["code"] for e in not_a_tutor.json()["errors"]] == ["tutor_unknown"]
    assert code_of(offered) == CREATED, offered.text
    assert code_of(again) == NOT_OFFERABLE
    assert again.json()["details"] == {"reason": "assigned"}
    assert code_of(denied) == (403, "permission_denied")
    assert len(series_offers(module_seeded_url, created["id"])) == 1


def test_a_tutor_overlap_in_week_4_refuses_the_whole_offer(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx, tutor=False)
    blocker = existing_session(
        module_seeded_url,
        state="confirmed",
        tutor_user_id=ctx.tutor.id,
        student_id=new_student(module_seeded_url),
        starts_at=ctx.tl.at(ctx.tl.week(4), "16:30"),
    )
    cookie = as_user(client, ctx.owner, ctx.now)
    [audits_before] = run_sql(module_seeded_url, AUDIT_COUNT)
    offers_before = run_sql(module_seeded_url, OFFER_COUNT)

    with frozen_clock(ctx.now):
        response = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(ctx.tutor.id),
            version=created["version"],
        )

    assert code_of(response) == CONFLICT, response.text
    [conflict] = response.json()["conflicts"]
    assert conflict["code"] == "tutor_overlap"
    assert conflict["details"]["class_session_ids"] == [str(blocker)]
    assert conflict["details"]["occurrence_start"] == iso_z(
        ctx.tl.at(ctx.tl.week(4), "16:00")
    )
    assert conflict["details"]["date"] == ctx.tl.week(4).isoformat()
    assert {r.state for r in series_rows(module_seeded_url, created["id"])} == {
        "unassigned"
    }
    assert series_state(module_seeded_url, created["id"]).tutor_user_id is None
    assert run_sql(module_seeded_url, OFFER_COUNT) == offers_before
    [audits_after] = run_sql(
        module_seeded_url, "SELECT count(*) AS n FROM audit_events"
    )
    assert audits_after.n == audits_before.n


def test_a_soft_conflict_refuses_without_an_override(module_seeded_url, client, ctx):
    created = make_series(client, ctx, tutor=False, count=2)
    unqualified = new_tutor_user(module_seeded_url)

    cookie = as_user(client, ctx.owner, ctx.now)
    with frozen_clock(ctx.now):
        response = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(unqualified.id),
            version=created["version"],
        )

    assert code_of(response) == CONFLICT, response.text
    found = response.json()["conflicts"]
    assert [c["code"] for c in found] == ["tutor_not_qualified"] * 2
    assert [c["details"]["date"] for c in found] == [
        ctx.tl.monday.isoformat(),
        ctx.tl.week(1, 3).isoformat(),
    ]


def test_the_booking_notice_is_not_checked_again(module_seeded_url, client, ctx):
    created = make_series(client, ctx, tutor=False, count=2)
    # Monday 14:00: the first row starts in 2 hours.
    soon = ctx.tl.at(ctx.tl.monday, "14:00")

    cookie = as_user(client, ctx.owner, soon)
    with frozen_clock(soon):
        response = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(ctx.tutor.id),
            version=created["version"],
        )

    assert code_of(response) == CREATED, response.text
    assert response.json()["offer"]["respond_by"] == iso_z(
        ctx.tl.at(ctx.tl.monday, "16:00")
    )


def test_offering_again_closes_the_series_reassignment_item(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx)
    [first] = series_offers(module_seeded_url, created["id"])
    assert code_of(answer(client, ctx, first.id, "decline", ctx.now)) == OK
    [item] = reassignment_items(module_seeded_url, first.id)
    version = series_state(module_seeded_url, created["id"]).version

    # "Mark resolved" while the series has no tutor.
    cookie = as_user(client, ctx.owner, ctx.now)
    with frozen_clock(ctx.now):
        resolve = post(client, cookie, f"{APPROVALS}/{item.id}/approve", {})
        offered = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(ctx.tutor.id),
            version=version,
        )

    assert code_of(resolve) == (409, "reassignment_still_needed")
    assert code_of(offered) == CREATED, offered.text
    [item] = reassignment_items(module_seeded_url, first.id)
    assert item.status == "cancelled"
    assert_invariant(module_seeded_url, created["id"])


def test_mark_resolved_needs_a_tutor_on_the_series(module_seeded_url, client, ctx):
    created = make_series(client, ctx, count=2)
    [offer] = series_offers(module_seeded_url, created["id"])
    assert code_of(answer(client, ctx, offer.id, "decline", ctx.now)) == OK
    [item] = reassignment_items(module_seeded_url, offer.id)
    rows = series_rows(module_seeded_url, created["id"])

    # Both rows offered one by one (C3): none is unassigned, but the series
    # still has no tutor.
    cookie = as_user(client, ctx.owner, ctx.now)
    with frozen_clock(ctx.now):
        for row in rows:
            response = post(
                client,
                cookie,
                f"{SESSIONS}/{row.id}/offers",
                {
                    "tutor_user_id": str(ctx.tutor.id),
                    "version": row_version(module_seeded_url, row.id),
                },
            )
            assert code_of(response) == CREATED, response.text
        resolve = post(client, cookie, f"{APPROVALS}/{item.id}/approve", {})

    assert code_of(resolve) == (409, "reassignment_still_needed")
    # C3: the session route leaves the series unassigned and its item open.
    assert series_state(module_seeded_url, created["id"]).tutor_user_id is None
    [item] = reassignment_items(module_seeded_url, offer.id)
    assert item.status == "pending"
    assert_invariant(module_seeded_url, created["id"])


def row_version(url: str, session_id: Any) -> int:
    [row] = run_sql(
        url,
        "SELECT version FROM class_sessions WHERE id = :id",
        {"id": str(session_id)},
    )
    return int(row.version)


# --- B1 through the conflict approve ------------------------------------------------


def conflict_row(url: str, ctx: Ctx, series_id: Any, day: date) -> tuple[Any, Any]:
    """Block the series' student on ``day`` with a one-off, run the job one
    week later: that occurrence is a ``conflict`` row. Returns ``(the
    blocker, the request id)``."""
    blocker = existing_session(
        url,
        state="unassigned",
        tutor_user_id=None,
        student_id=ctx.student,
        starts_at=ctx.tl.at(day, "16:30"),
    )
    run_job(url, ctx.now + timedelta(weeks=1))
    [row] = run_sql(
        url,
        "SELECT r.id AS request_id FROM class_sessions s"
        " JOIN approval_requests r ON r.subject_id = s.id"
        " WHERE s.series_id = :series AND s.state = 'conflict'",
        {"series": str(series_id)},
    )
    return blocker, row.request_id


def cancel(url: str, session_id: Any) -> None:
    async def _cancel(session: AsyncSession) -> None:
        row = await session.get(ClassSession, session_id)
        await change(
            session,
            ClassSession,
            session_id,
            state="cancelled",
            cancelled_at=row.starts_at - timedelta(days=30),
            cancellation_reason="Cancelada",
        )

    in_uow(url, _cancel)


@pytest.mark.parametrize("before_from_date", [True, False])
def test_an_approved_conflict_row_takes_the_series_assignment_of_its_date(
    module_seeded_url, client, ctx, before_from_date
):
    created = make_series(client, ctx, tutor=False, count=26)
    week13 = ctx.tl.week(13)
    later = ctx.now + timedelta(weeks=1)
    from_date = ctx.tl.week(13, 3) if before_from_date else ctx.tl.week(2)
    cookie = as_user(client, ctx.owner, ctx.now)
    with frozen_clock(ctx.now):
        response = offer_series(
            client,
            cookie,
            created["id"],
            tutor_user_id=str(ctx.tutor.id),
            version=created["version"],
            from_date=from_date.isoformat(),
        )
    assert code_of(response) == CREATED, response.text
    blocker, request_id = conflict_row(module_seeded_url, ctx, created["id"], week13)
    cancel(module_seeded_url, blocker)

    cookie = as_user(client, ctx.owner, later)
    with frozen_clock(later):
        approved = post(client, cookie, f"{APPROVALS}/{request_id}/approve", {})

    assert code_of(approved) == OK, approved.text
    rows = {r.starts_at: r for r in series_rows(module_seeded_url, created["id"])}
    row = rows[ctx.tl.at(week13, "16:00")]
    if before_from_date:
        assert (row.state, row.tutor_user_id) == ("unassigned", None)
    else:
        assert (row.state, row.tutor_user_id) == ("offered", ctx.tutor.id)
    # The Wednesday after it is covered in both cases.
    assert rows[ctx.tl.at(ctx.tl.week(13, 3), "16:00")].state == "offered"
    assert_invariant(module_seeded_url, created["id"])


# --- B6: the withdraw seam ----------------------------------------------------------


def test_withdraw_series_offer(module_seeded_url, client, ctx):
    created = make_series(client, ctx, count=4)
    [offer] = series_offers(module_seeded_url, created["id"])
    series_id = uuid.UUID(created["id"])

    async def _withdraw(session: AsyncSession) -> Any:
        return await scheduling.withdraw_series_offer(
            session, series_id, by_user_id=ctx.owner.id
        )

    with frozen_clock(ctx.now):
        first = in_uow(module_seeded_url, _withdraw)
        second = in_uow(module_seeded_url, _withdraw)

    assert (first, second) == (offer.id, None)
    [offer] = series_offers(module_seeded_url, created["id"])
    assert offer.status == "withdrawn"
    assert {
        (r.state, r.tutor_user_id) for r in series_rows(module_seeded_url, series_id)
    } == {("unassigned", None)}
    assert series_state(module_seeded_url, series_id).assignment_status is None
    assert reassignment_items(module_seeded_url, offer.id) == []
    assert_invariant(module_seeded_url, series_id)


def test_withdraw_pending_offers_leaves_series_offers_alone(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx, count=2)
    rows = series_rows(module_seeded_url, created["id"])

    async def _withdraw(session: AsyncSession) -> Any:
        return await scheduling.withdraw_pending_offers(
            session, [row.id for row in rows], by_user_id=None
        )

    with frozen_clock(ctx.now):
        assert in_uow(module_seeded_url, _withdraw) == []
    [offer] = series_offers(module_seeded_url, created["id"])
    assert offer.status == "pending"


# --- F1: the tutor's list -----------------------------------------------------------


def test_a_page_of_20_series_offers_takes_at_most_seven_statements(
    module_seeded_url, client, ctx
):
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        for _ in range(20):
            response = create_series(
                client,
                cookie,
                series_body(
                    student=new_student(module_seeded_url),
                    subject=ctx.subject,
                    tutor=ctx.tutor.id,
                    starts_on=ctx.tl.monday,
                    start_time=f"{9 + _ // 2:02d}:{30 * (_ % 2):02d}",
                    duration_minutes=30,
                    count=24,
                ),
            )
            assert code_of(response) == CREATED, response.text

    count, page = count_statements(
        module_seeded_url,
        lambda session: scheduling.list_my_offers(
            session, caller_id=ctx.tutor.id, status="pending", limit=20, offset=0
        ),
    )

    assert len(page.items) == 20
    assert {i.session_count for i in page.items} == {24}
    assert {len(i.sessions) for i in page.items} == {10}
    assert {i.sessions_truncated for i in page.items} == {True}
    assert count <= 7


def test_a_session_offer_item_is_unchanged(module_seeded_url, client, ctx):
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        start = ctx.tl.at(ctx.tl.week(2), "10:00")
        response = post(
            client,
            cookie,
            SESSIONS,
            {
                "kind": "individual",
                "student_id": str(ctx.student),
                "subject_id": str(ctx.subject),
                "tutor_user_id": str(ctx.tutor.id),
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(hours=1)).isoformat(),
            },
        )
    assert code_of(response) == CREATED, response.text
    cookie = as_user(client, ctx.tutor, ctx.now)
    with frozen_clock(ctx.now):
        [item] = get(client, cookie, OFFERS).json()["items"]

    assert (item["scope"], item["series"], item["session_count"]) == (
        "session",
        None,
        1,
    )
    assert item["sessions_truncated"] is False
    assert response.json()["pending_offer"]["scope"] == "session"


def test_the_series_read_shows_no_assignment_without_a_tutor(
    module_seeded_url, client, ctx
):
    created = make_series(client, ctx, tutor=False, count=1)
    assert created["assignment"] is None
    assert created["tutor"] is None
