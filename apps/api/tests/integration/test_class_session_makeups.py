"""The makeup link of a class session (#423 G4-G8), with real logins through
the real app.

Helpers: ``change_request_support``. Each test takes its own decade; "now" is
``scene.ctx.tl.now`` and the scene's confirmed session, the target by default,
starts on the second Tuesday at 10:00 (about 214 hours later). The seeded
policy allows a makeup after an on-time cancellation, with a 30-day window.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.errors import MakeupAlreadyScheduledError
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.schemas import ClassSessionCreate
from app.modules.scheduling.service import SessionKind
from app.modules.scheduling.sessions import flush_class_sessions
from tests.conftest import frozen_clock
from tests.factories.families import persist_student
from tests.factories.identity import persist_user
from tests.factories.scheduling import (
    add_participant,
    persist_class_session,
    persist_subject,
)
from tests.integration import session_charge_support as scs
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, patch, post
from tests.integration.change_request_support import (
    CONFLICT,
    CREATED,
    INVALID,
    OK,
    Scene,
    approve,
    create,
    created,
    hours,
    make_scene,
    ok,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_edit_support import as_user, make_series, rows_of
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_class_session_create import new_student, new_subject
from tests.integration.test_session_cancellation import cancel, one_off, read

SESSIONS = "/api/v1/class-sessions"
FIELD = ["body", "makeup_of_session_id"]
ALREADY = (409, "makeup_already_scheduled")
DENIED = (403, "permission_denied")
WINDOW = timedelta(days=30)
OVERRIDE = {"codes": ["outside_operating_hours"], "reason": "Autorizado"}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def scene(module_seeded_url: str, client: Any) -> Scene:
    return make_scene(module_seeded_url, client)


def z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def book_body(scene: Scene, target: Any, **changes: Any) -> dict[str, Any]:
    """A makeup of ``target`` with no tutor and no room, on the third Monday
    at 10:00 by default."""
    start = changes.pop("starts_at", scene.at(scene.ctx.tl.week(3, 1), "10:00"))
    body = {
        "kind": "individual",
        "student_id": str(scene.ctx.student),
        "subject_id": str(scene.ctx.subject),
        "tutor_user_id": None,
        "classroom_id": None,
        "starts_at": start.isoformat(),
        "ends_at": (start + hours(1)).isoformat(),
        "makeup_of_session_id": None if target is None else str(target),
    }
    return body | changes


def book(
    scene: Scene,
    target: Any,
    *,
    user: CreatedUser | None = None,
    at: datetime | None = None,
    **changes: Any,
) -> Response:
    when = at or scene.ctx.tl.now
    cookie = as_user(scene.ctx, user or scene.ctx.owner, when)
    with frozen_clock(when):
        return post(
            scene.ctx.client, cookie, SESSIONS, book_body(scene, target, **changes)
        )


def booked(response: Response) -> dict[str, Any]:
    assert code_of(response) == CREATED, response.text
    found: dict[str, Any] = response.json()
    return found


def refused(response: Response) -> list[str]:
    """The field error codes of a ``422``; the link's are on its field."""
    assert code_of(response) == INVALID, response.text
    errors = response.json()["errors"]
    for error in errors:
        if error["code"].startswith("makeup_"):
            assert error["path"] == FIELD
    return [error["code"] for error in errors]


def cancelled_target(scene: Scene) -> dict[str, Any]:
    """The scene's session after a center cancellation at "now"."""
    response = cancel(scene.ctx, scene.session["id"], scene.session["version"])
    assert code_of(response) == OK, response.text
    return read(scene.ctx, scene.session["id"])


def late_cancelled(scene: Scene, **decision: Any) -> dict[str, Any]:
    """A family cancellation 23 hours ahead (``late``), approved; returns the
    decision."""
    request = created(create(scene, scene.guardian, at=scene.start - hours(23)))
    return ok(approve(scene, request["id"], at=scene.start - hours(22), **decision))


def on_row(scene: Scene, *, kind: str, state: str = "cancelled") -> uuid.UUID:
    """A session of ``scene``'s student and subject written straight to the
    table: the kinds and states the create can't make."""

    async def _make(session: AsyncSession) -> uuid.UUID:
        values: dict[str, Any] = (
            {"cancelled_at": scene.start, "cancellation_reason": "Sin clase"}
            if state == "cancelled"
            else {"first_confirmed_at": scene.start - hours(1), "tutor": True}
        )
        row = await persist_class_session(
            session,
            subject_id=scene.ctx.subject,
            kind=kind,
            state=state,
            starts_at=scene.start,
            ends_at=scene.start + hours(1),
            **values,
        )
        await add_participant(session, row, scene.ctx.student)
        return row.id

    return in_uow(scene.url, _make)


def live_link(url: str, session_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT makeup_of_session_id FROM class_sessions WHERE id = :id",
        {"id": str(session_id)},
    )
    return row.makeup_of_session_id


# --- B: the create -------------------------------------------------------------------


def test_a_makeup_stores_the_link_and_audits_it(scene):
    target = cancelled_target(scene)

    made = booked(book(scene, target["id"]))

    assert made["makeup_of_session_id"] == target["id"]
    assert made["makeup"] is None
    assert made["state"] == "unassigned"
    assert str(live_link(scene.url, made["id"])) == target["id"]
    [event] = events(
        scene.url,
        record_type="class_sessions",
        record_id=uuid.UUID(made["id"]),
        action="create",
    )
    assert event.changes["makeup_of_session_id"] == {"old": None, "new": target["id"]}


def test_without_the_field_nothing_changes(scene):
    made = booked(book(scene, None))

    assert made["makeup_of_session_id"] is None
    assert made["makeup"] is None


def test_an_unknown_target_is_a_field_error(scene):
    assert refused(book(scene, uuid.uuid4())) == ["makeup_target_unknown"]


def test_a_live_target_is_refused(scene):
    assert refused(book(scene, scene.session["id"])) == ["makeup_target_not_cancelled"]


def test_a_skipped_closure_target_is_refused(scene):
    target = on_row(scene, kind="individual", state="skipped_closure")

    assert refused(book(scene, target)) == ["makeup_target_not_cancelled"]


@pytest.mark.parametrize("kind", [SessionKind.GROUP, SessionKind.COURSE])
def test_a_group_or_course_target_is_refused(scene, kind):
    target = on_row(scene, kind=kind.value)

    assert refused(book(scene, target)) == ["makeup_target_not_individual"]


def test_a_late_cancellation_allows_no_makeup(scene):
    decided = late_cancelled(scene)
    assert decided["change_request"]["final_outcome"]["makeup_allowed"] is False

    assert refused(book(scene, scene.session["id"], at=scene.start - hours(21))) == [
        "makeup_not_allowed"
    ]


def test_an_exception_passes_with_its_stored_date(scene):
    decided = late_cancelled(scene, exception=True, reason="Enfermedad")
    until = decided["change_request"]["final_outcome"]["makeup_until"]
    deadline = datetime.fromisoformat(until)
    at = scene.start - hours(21)

    assert read(scene.ctx, scene.session["id"])["makeup"] == {
        "allowed": True,
        "until": until,
        "session_id": None,
    }
    assert refused(
        book(scene, scene.session["id"], at=at, starts_at=deadline + timedelta(hours=1))
    ) == ["makeup_window_passed"]
    booked(book(scene, scene.session["id"], at=at, starts_at=deadline))


def test_an_on_time_family_cancellation_passes(scene):
    request = created(create(scene, scene.guardian))
    decided = ok(approve(scene, request["id"]))
    assert decided["change_request"]["final_outcome"]["makeup_allowed"] is True

    booked(book(scene, scene.session["id"]))


def test_another_student_is_refused(scene):
    target = cancelled_target(scene)
    other = new_student(scene.url)

    assert refused(book(scene, target["id"], student_id=str(other))) == [
        "makeup_student_mismatch"
    ]


def test_another_subject_is_refused(scene):
    target = cancelled_target(scene)
    other = new_subject(scene.url)

    assert refused(book(scene, target["id"], subject_id=str(other))) == [
        "makeup_subject_mismatch"
    ]


def test_the_deadline_is_inclusive_with_no_lower_bound(scene):
    target = cancelled_target(scene)
    deadline = scene.start + WINDOW

    assert refused(
        book(scene, target["id"], starts_at=deadline + timedelta(minutes=1))
    ) == ["makeup_window_passed"]
    first = booked(book(scene, target["id"], starts_at=deadline))
    assert cancel(scene.ctx, first["id"], first["version"]).status_code == 200
    # Before the missed slot.
    booked(book(scene, target["id"], starts_at=scene.start - hours(24)))


def test_a_later_policy_moves_no_deadline(scene):
    target = cancelled_target(scene)
    effective = scene.ctx.tl.now + hours(1)
    run_sql(
        scene.url,
        "INSERT INTO scheduling_policies (effective_from, booking_notice_hours,"
        " change_notice_hours, response_hours, makeup_after_on_time,"
        " makeup_after_late, makeup_after_no_show, makeup_window_days)"
        " VALUES (:at, 24, 24, 48, true, false, false, 90)",
        {"at": effective},
    )
    try:
        late = book(
            scene,
            target["id"],
            at=effective + hours(1),
            starts_at=scene.start + WINDOW + timedelta(minutes=1),
        )
        assert refused(late) == ["makeup_window_passed"]
        assert read(scene.ctx, target["id"])["makeup"]["until"] == z(
            scene.start + WINDOW
        )
    finally:
        run_sql(
            scene.url,
            "DELETE FROM scheduling_policies WHERE effective_from = :at",
            {"at": effective},
        )


def test_a_series_occurrence_is_a_valid_target(scene):
    series = make_series(scene.ctx, weekdays=[3], start_time="16:00")
    row = rows_of(scene.url, series["id"])[0]
    response = cancel(scene.ctx, row.id, row.version)
    assert code_of(response) == OK, response.text

    made = booked(book(scene, row.id))

    assert made["makeup_of_session_id"] == str(row.id)


def test_a_never_confirmed_target_passes(scene):
    other = one_off(
        scene.ctx,
        tutor=False,
        starts_at=scene.at(scene.ctx.tl.week(2, 3), "10:00"),
        room=scene.ctx.other_room,
    )
    assert cancel(scene.ctx, other["id"], other["version"]).status_code == 200

    booked(book(scene, other["id"]))


def test_another_tutor_passes_and_is_offered(scene):
    target = cancelled_target(scene)
    other = new_tutor_user(scene.url, qualified_for=[scene.ctx.subject])

    made = booked(book(scene, target["id"], tutor_user_id=str(other.id)))

    assert made["state"] == "offered"
    assert made["tutor"]["id"] == str(other.id)
    assert made["pending_offer"] is not None


def test_a_makeup_of_a_makeup_is_allowed(scene):
    target = cancelled_target(scene)
    first = booked(book(scene, target["id"]))
    assert cancel(scene.ctx, first["id"], first["version"]).status_code == 200

    second = booked(book(scene, first["id"]))

    assert second["makeup_of_session_id"] == first["id"]


# --- B4: the order, and the reference errors -----------------------------------------


def test_the_first_refusal_wins_in_order(scene):
    group = on_row(scene, kind="group")
    other_student = str(new_student(scene.url))
    other_subject = str(new_subject(scene.url))
    target = cancelled_target(scene)["id"]
    past = scene.start + WINDOW + hours(1)

    assert refused(book(scene, group, student_id=other_student)) == [
        "makeup_target_not_individual"
    ]
    assert refused(
        book(
            scene,
            target,
            student_id=other_student,
            subject_id=other_subject,
            starts_at=past,
        )
    ) == ["makeup_student_mismatch"]
    assert refused(book(scene, target, subject_id=other_subject, starts_at=past)) == [
        "makeup_subject_mismatch"
    ]


def test_not_allowed_comes_before_the_student(scene):
    late_cancelled(scene)
    at = scene.start - hours(21)
    other = str(new_student(scene.url))

    assert refused(book(scene, scene.session["id"], at=at, student_id=other)) == [
        "makeup_not_allowed"
    ]


def test_the_refusal_joins_the_reference_errors(scene):
    target = cancelled_target(scene)

    both = book(scene, target["id"], subject_id=str(uuid.uuid4()))
    assert refused(both) == ["subject_unknown", "makeup_subject_mismatch"]

    alone = book(scene, target["id"], tutor_user_id=str(uuid.uuid4()))
    assert refused(alone) == ["tutor_unknown"]


def test_override_cannot_pass_a_refusal(scene):
    target = cancelled_target(scene)

    late = book(
        scene,
        target["id"],
        starts_at=scene.start + WINDOW + hours(1),
        override=OVERRIDE,
    )

    assert refused(late) == ["makeup_window_passed"]


def test_the_conflict_checks_still_run(scene):
    target = cancelled_target(scene)
    start = scene.at(scene.ctx.tl.week(3, 2), "12:00")
    booked(book(scene, None, starts_at=start))

    response = book(scene, target["id"], starts_at=start)

    assert code_of(response) == CONFLICT
    codes = [item["code"] for item in response.json()["conflicts"]]
    assert "student_overlap" in codes


# --- B5, B6: one live makeup [E5] ----------------------------------------------------


def test_a_second_live_makeup_is_refused_until_the_first_is_cancelled(scene):
    target = cancelled_target(scene)
    first = booked(book(scene, target["id"]))
    later = scene.at(scene.ctx.tl.week(3, 2), "10:00")

    second = book(scene, target["id"], starts_at=later)

    assert code_of(second) == ALREADY
    assert second.json()["details"] == {"class_session_id": first["id"]}

    assert cancel(scene.ctx, first["id"], first["version"]).status_code == 200
    third = booked(book(scene, target["id"], starts_at=later))

    assert read(scene.ctx, target["id"])["makeup"]["session_id"] == third["id"]
    assert read(scene.ctx, first["id"])["makeup_of_session_id"] == target["id"]


def test_the_refusals_come_before_the_live_makeup(scene):
    target = cancelled_target(scene)
    booked(book(scene, target["id"]))

    assert refused(
        book(scene, target["id"], starts_at=scene.start + WINDOW + hours(1))
    ) == ["makeup_window_passed"]


def test_the_index_backstop_maps_to_the_same_error(scene):
    target = cancelled_target(scene)
    made = booked(book(scene, target["id"]))

    async def _second(session: AsyncSession) -> None:
        first = await session.get(ClassSession, uuid.UUID(made["id"]))
        assert first is not None
        session.add(
            ClassSession(
                kind="individual",
                state="unassigned",
                subject_id=first.subject_id,
                starts_at=first.starts_at + hours(24),
                ends_at=first.ends_at + hours(24),
                scheduled_minutes=first.scheduled_minutes,
                makeup_of_session_id=first.makeup_of_session_id,
            )
        )
        await flush_class_sessions(session)

    with pytest.raises(MakeupAlreadyScheduledError) as caught:
        in_uow(scene.url, _second)

    assert caught.value.details is None


async def _create_makeup(
    url: str, body: ClassSessionCreate, actor: uuid.UUID, after: Any = None
) -> str:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await scheduling.create_class_session(uow.session, body, actor_id=actor)
            if after is not None:
                await after()
    except MakeupAlreadyScheduledError:
        return "already"
    finally:
        await engine.dispose()
    return "created"


@pytest.mark.parametrize("attempt", range(3))
def test_two_concurrent_creates_store_one_makeup(module_seeded_url, attempt):
    url = module_seeded_url
    zone = ZoneInfo("America/Mexico_City")
    start = datetime(2031, 1, 8, 10, 0, tzinfo=zone)

    async def _scene(session: AsyncSession) -> tuple[Any, ...]:
        subject = (await persist_subject(session)).id
        student = (await persist_student(session, contacts=0)).id
        actor = (await persist_user(session, roles=("owner",))).id
        target = await persist_class_session(
            session,
            subject_id=subject,
            state="cancelled",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            cancelled_at=start,
            cancellation_reason="Sin clase",
        )
        await add_participant(session, target, student)
        return subject, student, actor, target.id

    subject, student, actor, target = in_uow(url, _scene)

    def body(day: int) -> ClassSessionCreate:
        starts_at = start + timedelta(days=day)
        return ClassSessionCreate(
            kind="individual",
            student_id=student,
            subject_id=subject,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            makeup_of_session_id=target,
        )

    async def _race() -> list[str]:
        flushed = asyncio.Event()

        async def hold() -> None:
            flushed.set()
            await asyncio.sleep(0.5)

        async def first() -> str:
            try:
                return await _create_makeup(url, body(1), actor, hold)
            finally:
                flushed.set()

        async def second() -> str:
            await flushed.wait()
            return await _create_makeup(url, body(2), actor)

        return list(await asyncio.gather(first(), second()))

    assert asyncio.run(_race()) == ["created", "already"]
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM class_sessions WHERE makeup_of_session_id = :id",
        {"id": str(target)},
    )
    assert row.n == 1


# --- D1: who can link ----------------------------------------------------------------


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, FINANCE_STAFF])
def test_staff_without_schedule_manage_get_403(scene, role):
    target = cancelled_target(scene)
    user = create_user(scene.url, roles=(role,))

    assert code_of(book(scene, target["id"], user=user)) == DENIED


def test_tutors_and_families_get_403(scene):
    target = cancelled_target(scene)

    assert code_of(book(scene, target["id"], user=scene.ctx.tutor)) == DENIED
    assert code_of(book(scene, target["id"], user=scene.guardian)) == DENIED


def test_an_administrator_can_link(scene):
    target = cancelled_target(scene)
    user = create_user(scene.url, roles=(ADMINISTRATOR,))

    booked(book(scene, target["id"], user=user))


def test_only_the_create_takes_the_key(scene):
    target = cancelled_target(scene)
    made = booked(book(scene, None))
    ctx = scene.ctx
    cookie = as_user(ctx, ctx.owner, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        moved = patch(
            ctx.client,
            cookie,
            f"{SESSIONS}/{made['id']}",
            {"version": made["version"], "makeup_of_session_id": target["id"]},
        )
        suggested = post(
            ctx.client,
            cookie,
            f"{SESSIONS}/suggestions",
            book_body(scene, target["id"]),
        )

    assert code_of(moved) == INVALID
    assert code_of(suggested) == INVALID
    assert live_link(scene.url, made["id"]) is None


# --- G5: later moves keep the link [E6] ----------------------------------------------


def test_a_patch_past_the_deadline_keeps_the_link(scene):
    target = cancelled_target(scene)
    made = booked(book(scene, target["id"]))
    late = scene.start + WINDOW + timedelta(days=5)
    ctx = scene.ctx
    cookie = as_user(ctx, ctx.owner, ctx.tl.now)

    with frozen_clock(ctx.tl.now):
        moved = patch(
            ctx.client,
            cookie,
            f"{SESSIONS}/{made['id']}",
            {
                "version": made["version"],
                "starts_at": late.isoformat(),
                "ends_at": (late + hours(1)).isoformat(),
            },
        )

    assert code_of(moved) == OK, moved.text
    assert moved.json()["makeup_of_session_id"] == target["id"]


def test_an_approved_reschedule_past_the_deadline_keeps_the_link(scene):
    target = cancelled_target(scene)
    ctx = scene.ctx
    made = booked(book(scene, target["id"], tutor_user_id=str(ctx.tutor.id)))
    cookie = as_user(ctx, ctx.tutor, ctx.tl.now)
    offer = made["pending_offer"]["id"]
    with frozen_clock(ctx.tl.now):
        accepted = post(
            ctx.client, cookie, f"/api/v1/assignment-offers/{offer}/accept", None
        )
    assert code_of(accepted) == OK, accepted.text
    late = scene.start + WINDOW + timedelta(days=5)

    request = created(
        create(
            scene,
            scene.guardian,
            kind="reschedule",
            session_id=made["id"],
            proposed_starts_at=late,
        )
    )
    decided = ok(approve(scene, request["id"]))

    moved = decided["class_session"]
    assert moved["starts_at"] == z(late)
    assert moved["makeup_of_session_id"] == target["id"]


# --- G6: the reads [E7] --------------------------------------------------------------


def test_the_reads_of_each_kind_of_session(scene):
    assert scene.session["makeup_of_session_id"] is None
    assert scene.session["makeup"] is None

    skipped = read(scene.ctx, on_row(scene, kind="individual", state="skipped_closure"))
    assert skipped["makeup"] is None

    target = cancelled_target(scene)
    assert target["makeup"] == {
        "allowed": True,
        "until": z(scene.start + WINDOW),
        "session_id": None,
    }
    made = booked(book(scene, target["id"]))
    assert read(scene.ctx, made["id"])["makeup"] is None
    assert read(scene.ctx, made["id"])["makeup_of_session_id"] == target["id"]
    assert read(scene.ctx, target["id"])["makeup"]["session_id"] == made["id"]


def test_a_cancellation_without_a_makeup_reads_as_not_allowed(scene):
    late_cancelled(scene)

    assert read(scene.ctx, scene.session["id"])["makeup"] == {
        "allowed": False,
        "until": None,
        "session_id": None,
    }


def test_the_cancelled_read_adds_at_most_three_statements(scene):
    session_id = uuid.UUID(scene.session["id"])

    def count() -> int:
        found, _ = count_statements(
            scene.url, lambda session: scheduling.get_class_session(session, session_id)
        )
        return found

    plain = count()
    assert plain <= 7
    target = cancelled_target(scene)
    booked(book(scene, target["id"]))

    assert count() <= plain + 3


# --- G7: billing never reads the link ------------------------------------------------


def test_a_linked_and_an_unlinked_session_are_charged_alike(module_seeded_url):
    url = module_seeded_url
    scs.reset(url)
    day = scs.FLOOR + timedelta(days=30)
    linked = scs.item(url, starts_at=scs.local(day, 10))
    plain = scs.item(
        url, starts_at=scs.local(day, 12), subject=linked.subject, price=None
    )

    async def _target(session: AsyncSession) -> uuid.UUID:
        row = await persist_class_session(
            session,
            subject_id=linked.subject,
            state="cancelled",
            starts_at=scs.local(day - timedelta(days=1)),
            ends_at=scs.local(day - timedelta(days=1), 11),
            cancelled_at=scs.local(day - timedelta(days=2)),
            cancellation_reason="Sin clase",
        )
        await add_participant(session, row, linked.student)
        return row.id

    target = in_uow(url, _target)
    run_sql(
        url,
        "UPDATE class_sessions SET makeup_of_session_id = :t WHERE id = :id",
        {"t": str(target), "id": str(linked.session)},
    )
    scs.set_floor(url, scs.FLOOR)
    try:
        assert scs.post_one(url, linked) == "posted"
        assert scs.post_one(url, plain) == "posted"
    finally:
        scs.set_floor(url, None)

    [charged], [other] = (
        scs.charges_of(url, linked.session),
        scs.charges_of(url, plain.session),
    )
    assert (charged.list_amount, charged.discount_amount, charged.amount) == (
        other.list_amount,
        other.discount_amount,
        other.amount,
    )
    assert scs.charges_of(url, target) == []
