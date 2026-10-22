"""Closure changes on existing series occurrences (#232 D1; ADR-0005 D2, D6).

``run_materialization`` runs against the module database with an explicit
``now`` and a frozen clock, as in the #53 J2 tests. Closures are added and
changed through the ORM (the HTTP enqueue is ``test_closures_enqueue.py``).
Every test takes its own decade; "now" is the Sunday 12:00 before the
series' first Monday (``series_support.timeline``), and the closed dates are
in week 3, so every row on them is in the future and past the 24-hour
booking notice.
"""

import asyncio
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Closure
from app.modules.families.models import Student
from app.modules.identity.permissions import GUARDIAN, STUDENT
from app.modules.scheduling import closure_changes, tasks
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.offer_records import insert_offer
from tests.conftest import frozen_clock
from tests.factories.base import persist
from tests.factories.families import GuardianFactory, link_guardian
from tests.factories.identity import persist_user
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import (
    Timeline,
    create_series,
    iso_z,
    owner_cookie,
    series_body,
    series_rows,
    timeline,
)
from tests.integration.test_class_session_create import (
    CREATED,
    change,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)

SKIPPED_TYPE = "scheduling.occurrence_skipped_closure"
APPROVALS = (
    "SELECT id, status, requester_user_id FROM approval_requests"
    " WHERE request_type = 'scheduling.occurrence_conflict' AND subject_id = :id"
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


# --- Helpers -------------------------------------------------------------------------


def run(url: str, tl_now: Any) -> tasks.MaterializationResult:
    with frozen_clock(tl_now):
        return asyncio.run(tasks.run_materialization(settings_for(url), now=tl_now))


def new_series(
    url: str,
    client: Any,
    tl: Timeline,
    *,
    tutor: bool = True,
    room: bool = False,
    student: uuid.UUID | None = None,
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """A Monday/Wednesday 16:00 series created at ``tl.now``: ``offered``
    rows under a pending series offer with a tutor, else ``unassigned``."""
    subject = new_subject(url)
    refs = {"student": student or new_student(url), "subject": subject}
    if tutor:
        refs["tutor"] = new_tutor(url, qualified_for=[subject])
    if room:
        refs["room"] = new_room(url)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = create_series(
            client,
            cookie,
            series_body(
                student=refs["student"],
                subject=subject,
                tutor=refs.get("tutor"),
                room=refs.get("room"),
                starts_on=tl.monday,
            ),
        )
    assert code_of(response) == CREATED, response.text
    return uuid.UUID(response.json()["id"]), refs


def add_closure(url: str, starts_on: date, ends_on: date | None = None) -> uuid.UUID:
    async def _add(session: AsyncSession) -> uuid.UUID:
        closure = Closure(
            name="Día festivo",
            closure_type="holiday",
            starts_on=starts_on,
            ends_on=ends_on or starts_on,
        )
        session.add(closure)
        await session.flush()
        return closure.id

    return in_uow(url, _add)


def set_closure(url: str, closure_id: uuid.UUID, **values: Any) -> None:
    async def _set(session: AsyncSession) -> None:
        await change(session, Closure, closure_id, **values)

    in_uow(url, _set)


def set_row(url: str, row_id: uuid.UUID, **values: Any) -> None:
    async def _set(session: AsyncSession) -> None:
        await change(session, ClassSession, row_id, **values)

    in_uow(url, _set)


def row_on(url: str, series_id: uuid.UUID, tl: Timeline, day: date) -> Any:
    [row] = [
        r for r in series_rows(url, series_id) if r.starts_at == tl.at(day, "16:00")
    ]
    return row


def with_family(url: str, student_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Give the student a login and a linked guardian with an account;
    returns ``(student user, guardian user)``."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        student = await session.get(Student, student_id)
        assert student is not None
        login = await persist_user(session, roles=(STUDENT,))
        student.user_id = login.id
        account = await persist_user(session, roles=(GUARDIAN,))
        guardian = await persist(session, GuardianFactory, user_id=account.id)
        await link_guardian(session, student, guardian)
        return login.id, account.id

    return in_uow(url, _make)


def skip_notes(url: str, row_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = :type AND params->>'class_session_id' = :id"
        " ORDER BY recipient_user_id",
        {"type": SKIPPED_TYPE, "id": str(row_id)},
    )


def total(url: str, table: str) -> int:
    # The table names come from the tests themselves.
    [row] = run_sql(url, f"SELECT count(*) AS n FROM {table}")  # noqa: S608
    return int(row.n)


def links_of(url: str, row_id: uuid.UUID) -> list[Any]:
    return run_sql(
        url,
        "SELECT o.id, o.status FROM assignment_offer_sessions x"
        " JOIN assignment_offers o ON o.id = x.offer_id"
        " WHERE x.class_session_id = :id",
        {"id": str(row_id)},
    )


# --- A closure added -----------------------------------------------------------------


def test_an_unassigned_row_is_skipped_and_its_room_freed(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    series_id, refs = new_series(url, client, tl, tutor=False, room=True)
    wednesday = tl.week(3, 3)
    add_closure(url, wednesday)

    result = run(url, tl.now)

    row = row_on(url, series_id, tl, wednesday)
    assert row.state == "skipped_closure"
    assert row.tutor_user_id is None
    assert row.classroom_id == refs["room"]
    assert result.closure_skipped >= 1 and result.failed == 0
    assert skip_notes(url, row.id) == []
    # The room is free: a one-off in it at the same time is accepted (the
    # room EXCLUDE covers unassigned rows too).
    existing_session(
        url,
        state="unassigned",
        classroom_id=refs["room"],
        starts_at=tl.at(wednesday, "16:00"),
        student_id=new_student(url),
    )
    # The other rows are unchanged.
    others = [r for r in series_rows(url, series_id) if r.id != row.id]
    assert {r.state for r in others} == {"unassigned"}


def test_an_offered_row_tells_only_its_tutor(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    student = new_student(url)
    with_family(url, student)
    series_id, refs = new_series(url, client, tl, student=student)
    wednesday = tl.week(3, 3)
    add_closure(url, wednesday)

    run(url, tl.now)

    row = row_on(url, series_id, tl, wednesday)
    assert row.state == "skipped_closure"
    assert row.tutor_user_id == refs["tutor"]
    [note] = skip_notes(url, row.id)
    assert note.recipient_user_id == refs["tutor"]
    assert note.params == {
        "class_session_id": str(row.id),
        "series_id": str(series_id),
        "starts_at": iso_z(tl.at(wednesday, "16:00")),
    }
    # The pending series offer stays; the row keeps its covered-session row.
    [(_, status)] = links_of(url, row.id)
    assert status == "pending"


def test_a_confirmed_row_tells_the_tutor_the_student_and_the_guardians(
    module_seeded_url, client
):
    url = module_seeded_url
    tl = timeline()
    student = new_student(url)
    student_user, guardian_user = with_family(url, student)
    # A guardian of another student is never told.
    _, stranger = with_family(url, new_student(url))
    series_id, refs = new_series(url, client, tl, student=student)
    wednesday = tl.week(3, 3)
    set_row(url, row_on(url, series_id, tl, wednesday).id, state="confirmed")
    add_closure(url, wednesday)

    run(url, tl.now)

    row = row_on(url, series_id, tl, wednesday)
    assert row.state == "skipped_closure"
    recipients = {note.recipient_user_id for note in skip_notes(url, row.id)}
    assert recipients == {refs["tutor"], student_user, guardian_user}
    assert stranger not in recipients
    assert len(skip_notes(url, row.id)) == 3


def test_a_session_offer_of_a_skipped_row_is_withdrawn(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    series_id, refs = new_series(url, client, tl, tutor=False)
    wednesday = tl.week(3, 3)
    row_id = row_on(url, series_id, tl, wednesday).id
    tutor = new_tutor(url, qualified_for=[refs["subject"]])

    async def _offer(session: AsyncSession) -> uuid.UUID:
        await change(
            session, ClassSession, row_id, state="offered", tutor_user_id=tutor
        )
        offer = await insert_offer(
            session,
            tutor_user_id=tutor,
            offered_by_user_id=tutor,
            offered_at=tl.now,
            respond_by=tl.now + timedelta(hours=48),
            class_session_ids=[row_id],
        )
        return offer.id

    offer_id = in_uow(url, _offer)
    add_closure(url, wednesday)

    run(url, tl.now)

    assert row_on(url, series_id, tl, wednesday).state == "skipped_closure"
    [offer] = run_sql(
        url,
        "SELECT status FROM assignment_offers WHERE id = :id",
        {"id": str(offer_id)},
    )
    assert offer.status == "withdrawn"
    [note] = skip_notes(url, row_id)
    assert note.recipient_user_id == tutor


def test_past_completed_cancelled_conflict_and_one_off_rows_are_left_alone(
    module_seeded_url, client
):
    url = module_seeded_url
    tl = timeline()
    first, _ = new_series(url, client, tl)
    completed, _ = new_series(url, client, tl)
    cancelled, _ = new_series(url, client, tl)
    conflict, _ = new_series(url, client, tl)
    monday, wednesday = tl.week(1), tl.week(3, 3)
    set_row(url, row_on(url, completed, tl, wednesday).id, state="completed")
    set_row(
        url,
        row_on(url, cancelled, tl, wednesday).id,
        state="cancelled",
        cancelled_at=tl.now,
        cancellation_reason="Cancelada",
    )
    set_row(
        url,
        row_on(url, conflict, tl, wednesday).id,
        state="conflict",
        conflict_codes=["tutor_overlap"],
    )
    one_off = existing_session(
        url,
        state="confirmed",
        starts_at=tl.at(wednesday, "10:00"),
        student_id=new_student(url),
    )
    add_closure(url, monday)
    add_closure(url, wednesday)
    # "Now" is after the first Monday's start: that row is in the past.
    now = tl.at(monday, "16:30")

    result = run(url, now)

    assert result.failed == 0

    assert row_on(url, first, tl, monday).state == "offered"
    assert row_on(url, first, tl, wednesday).state == "skipped_closure"
    assert row_on(url, completed, tl, wednesday).state == "completed"
    assert row_on(url, cancelled, tl, wednesday).state == "cancelled"
    held = row_on(url, conflict, tl, wednesday)
    assert held.state == "conflict"
    assert list(held.conflict_codes) == ["tutor_overlap"]
    [stored] = run_sql(
        url, "SELECT state FROM class_sessions WHERE id = :id", {"id": str(one_off)}
    )
    assert stored.state == "confirmed"


# --- A closure removed or shrunk -----------------------------------------------------


def test_a_deactivated_closure_restores_the_rows(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    offered, _ = new_series(url, client, tl)
    unassigned, _ = new_series(url, client, tl, tutor=False)
    wednesday = tl.week(3, 3)
    closure = add_closure(url, wednesday)
    run(url, tl.now)
    notes = total(url, "notifications")

    set_closure(url, closure, is_active=False)
    result = run(url, tl.now)

    restored = row_on(url, offered, tl, wednesday)
    assert restored.state == "offered"
    # Still one covered-session row, on the same pending series offer.
    [(_, status)] = links_of(url, restored.id)
    assert status == "pending"
    assert row_on(url, unassigned, tl, wednesday).state == "unassigned"
    assert result.closure_restored >= 2 and result.failed == 0
    # A restore tells nobody.
    assert total(url, "notifications") == notes


def test_a_restored_row_of_an_accepted_series_is_confirmed(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    series_id, refs = new_series(url, client, tl)
    wednesday = tl.week(3, 3)
    closure = add_closure(url, wednesday)
    run(url, tl.now)

    async def _accept(session: AsyncSession) -> None:
        from app.modules.scheduling.models import ClassSeries

        await change(session, ClassSeries, series_id, assignment_status="accepted")

    in_uow(url, _accept)
    set_closure(url, closure, is_active=False)
    run(url, tl.now)

    row = row_on(url, series_id, tl, wednesday)
    assert row.state == "confirmed"
    assert row.tutor_user_id == refs["tutor"]


def test_a_shrunk_range_restores_only_the_reopened_date(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    series_id, _ = new_series(url, client, tl)
    monday, wednesday = tl.week(3), tl.week(3, 3)
    closure = add_closure(url, monday, wednesday)
    run(url, tl.now)
    assert row_on(url, series_id, tl, monday).state == "skipped_closure"
    assert row_on(url, series_id, tl, wednesday).state == "skipped_closure"

    set_closure(url, closure, ends_on=monday)
    run(url, tl.now)

    assert row_on(url, series_id, tl, monday).state == "skipped_closure"
    assert row_on(url, series_id, tl, wednesday).state == "offered"


def test_a_restore_into_a_taken_tutor_slot_is_a_conflict(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    series_id, refs = new_series(url, client, tl)
    wednesday = tl.week(3, 3)
    closure = add_closure(url, wednesday)
    run(url, tl.now)
    # While the row is skipped, a one-off takes the tutor's slot.
    existing_session(
        url,
        state="confirmed",
        tutor_user_id=refs["tutor"],
        starts_at=tl.at(wednesday, "16:00"),
        student_id=new_student(url),
    )

    set_closure(url, closure, is_active=False)
    result = run(url, tl.now)

    row = row_on(url, series_id, tl, wednesday)
    assert row.state == "conflict"
    assert list(row.conflict_codes) == ["tutor_overlap"]
    [request] = run_sql(url, APPROVALS, {"id": str(row.id)})
    assert request.status == "pending"
    assert request.requester_user_id is None
    assert result.closure_conflicts >= 1 and result.failed == 0


def test_a_restore_into_a_student_overlap_is_a_conflict(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    series_id, refs = new_series(url, client, tl)
    wednesday = tl.week(3, 3)
    closure = add_closure(url, wednesday)
    run(url, tl.now)
    # No EXCLUDE covers students: only the re-check finds this one.
    existing_session(
        url,
        state="confirmed",
        starts_at=tl.at(wednesday, "16:00"),
        student_id=refs["student"],
    )

    set_closure(url, closure, is_active=False)
    run(url, tl.now)

    row = row_on(url, series_id, tl, wednesday)
    assert row.state == "conflict"
    assert list(row.conflict_codes) == ["student_overlap"]
    [request] = run_sql(url, APPROVALS, {"id": str(row.id)})
    assert request.status == "pending"


def test_an_exclude_race_on_a_restore_is_a_conflict(
    module_seeded_url, client, monkeypatch
):
    url = module_seeded_url
    tl = timeline()
    series_id, refs = new_series(url, client, tl)
    monday, wednesday = tl.week(3), tl.week(3, 3)
    closure = add_closure(url, monday, wednesday)
    run(url, tl.now)
    existing_session(
        url,
        state="confirmed",
        tutor_user_id=refs["tutor"],
        starts_at=tl.at(wednesday, "16:00"),
        student_id=new_student(url),
    )

    async def _no_conflicts(*args: Any, **kwargs: Any) -> list[Any]:
        return []  # the checks ran before the one-off was booked

    monkeypatch.setattr(closure_changes, "collect_conflicts", _no_conflicts)
    set_closure(url, closure, is_active=False)
    result = run(url, tl.now)

    raced = row_on(url, series_id, tl, wednesday)
    assert raced.state == "conflict"
    assert list(raced.conflict_codes) == ["tutor_overlap"]
    assert len(run_sql(url, APPROVALS, {"id": str(raced.id)})) == 1
    # The rest of the series went through.
    assert row_on(url, series_id, tl, monday).state == "offered"
    assert result.failed == 0


# --- Idempotency ---------------------------------------------------------------------


def test_reruns_change_nothing(module_seeded_url, client, caplog):
    url = module_seeded_url
    tl = timeline()
    student = new_student(url)
    with_family(url, student)
    series_id, _ = new_series(url, client, tl, student=student)
    wednesday = tl.week(3, 3)
    set_row(url, row_on(url, series_id, tl, wednesday).id, state="confirmed")
    closure = add_closure(url, wednesday)
    add_closure(url, tl.week(3))
    run(url, tl.now)

    def snapshot() -> tuple[Any, ...]:
        return (
            total(url, "audit_events"),
            total(url, "notifications"),
            total(url, "approval_requests"),
            [(r.id, r.state) for r in series_rows(url, series_id)],
        )

    before = snapshot()
    caplog.clear()
    with caplog.at_level("INFO", logger=tasks.__name__):
        again = run(url, tl.now)
    assert snapshot() == before
    assert (again.closure_skipped, again.closure_restored, again.closure_conflicts) == (
        0,
        0,
        0,
    )
    [record] = [r for r in caplog.records if r.message == "Materialized class series"]
    assert record.closure_skipped_count == 0
    assert record.closure_restored_count == 0
    assert record.closure_conflict_count == 0

    # The Wednesday reopens (restored), the Monday stays closed; then a
    # rerun again changes nothing.
    set_closure(url, closure, is_active=False)
    restored = run(url, tl.now)
    assert restored.closure_restored >= 1
    assert row_on(url, series_id, tl, wednesday).state == "offered"
    assert row_on(url, series_id, tl, tl.week(3)).state == "skipped_closure"
    after_restore = snapshot()
    again = run(url, tl.now)
    assert snapshot() == after_restore
    assert (again.closure_skipped, again.closure_restored, again.closure_conflicts) == (
        0,
        0,
        0,
    )


# --- A row that already has a conflict request (QA D1) ----------------------------

REQUEST_ROW = (
    "SELECT id, status, requester_user_id, resolved_at, resolved_by_user_id,"
    " reason, version FROM approval_requests"
    " WHERE request_type = 'scheduling.occurrence_conflict' AND subject_id = :id"
)


def test_an_approved_conflict_skipped_then_reopened_into_a_conflict_is_reopened(
    module_seeded_url, client
):
    """#53 conflict → approved → skipped by a closure → the date reopens into
    a conflict again: the row is ``conflict`` with its one request back to
    ``pending`` and actionable, the series doesn't fail and still extends,
    and approving it again works."""
    from tests.integration.catalog_support import get, post
    from tests.integration.test_occurrence_conflict_approval import (
        APPROVALS as APPROVAL_PATH,
    )
    from tests.integration.test_occurrence_conflict_approval import (
        cancel_blocker,
        conflict_setup,
        state_of,
    )

    url = module_seeded_url
    ctx = conflict_setup(url, client)
    tl, later = ctx["tl"], ctx["later"]
    row_id, request_id = ctx["session_id"], ctx["request_id"]
    cancel_blocker(url, ctx["blocker"])
    with frozen_clock(later):
        cookie = owner_cookie(url, client)
        response = post(client, cookie, f"{APPROVAL_PATH}/{request_id}/approve", {})
    assert code_of(response) == (200, None), response.text
    [approved] = run_sql(url, REQUEST_ROW, {"id": str(row_id)})
    assert approved.status == "approved"

    monday13 = tl.week(13)
    closure = add_closure(url, monday13)
    run(url, later)
    assert state_of(url, row_id).state == "skipped_closure"
    # While the row is skipped (it holds nothing), the tutor's slot is booked.
    blocker = existing_session(
        url,
        state="confirmed",
        tutor_user_id=ctx["tutor"],
        starts_at=tl.at(monday13, "16:00"),
        student_id=new_student(url),
    )
    set_closure(url, closure, is_active=False)
    rows_before = len(series_rows(url, ctx["series_id"]))
    reopen_at = later + timedelta(weeks=1)
    result = run(url, reopen_at)

    assert result.failed == 0
    assert result.closure_conflicts >= 1
    # The extension of the same series still ran (one more week of rows).
    assert len(series_rows(url, ctx["series_id"])) > rows_before
    row = state_of(url, row_id)
    assert row.state == "conflict"
    [codes] = run_sql(
        url,
        "SELECT conflict_codes FROM class_sessions WHERE id = :id",
        {"id": str(row_id)},
    )
    assert list(codes.conflict_codes) == ["tutor_overlap"]
    [request] = run_sql(url, REQUEST_ROW, {"id": str(row_id)})
    assert request.id == request_id  # the same one, reopened
    assert request.status == "pending"
    assert request.requester_user_id is None
    assert (request.resolved_at, request.resolved_by_user_id, request.reason) == (
        None,
        None,
        None,
    )
    assert request.version > approved.version
    # The reopen is audited like any change (the decision stays in the log).
    reopened = run_sql(
        url,
        "SELECT changes FROM audit_events WHERE record_id = :id"
        " AND action = 'update'"
        " AND changes -> 'status' ->> 'old' = 'approved'"
        " AND changes -> 'status' ->> 'new' = 'pending'",
        {"id": str(request_id)},
    )
    assert len(reopened) == 1

    with frozen_clock(reopen_at):
        cookie = owner_cookie(url, client)
        listed = get(client, cookie, APPROVAL_PATH)
        item = get(client, cookie, f"{APPROVAL_PATH}/{request_id}")
    assert request_id in {uuid.UUID(i["id"]) for i in listed.json()["items"]}
    assert code_of(item) == (200, None), item.text
    assert "approve" in item.json()["allowed_actions"]

    # A rerun changes nothing; then approving it again schedules the row.
    again = run(url, reopen_at)
    assert again.failed == 0 and again.closure_conflicts == 0
    cancel_blocker(url, blocker)
    with frozen_clock(reopen_at):
        cookie = owner_cookie(url, client)
        response = post(client, cookie, f"{APPROVAL_PATH}/{request_id}/approve", {})
    assert code_of(response) == (200, None), response.text
    assert state_of(url, row_id).state == "offered"
    [decided] = run_sql(url, REQUEST_ROW, {"id": str(row_id)})
    assert decided.status == "approved"


def test_a_cancelled_earlier_request_is_reopened_on_a_conflict_restore(
    module_seeded_url, client
):
    url = module_seeded_url
    tl = timeline()
    series_id, refs = new_series(url, client, tl)
    wednesday = tl.week(3, 3)
    row = row_on(url, series_id, tl, wednesday)
    earlier = uuid.uuid4()

    async def _cancelled_request(session: AsyncSession) -> None:
        from app.modules.administration.models import ApprovalRequest

        session.add(
            ApprovalRequest(
                id=earlier,
                request_type="scheduling.occurrence_conflict",
                subject_id=row.id,
                status="cancelled",
                priority="normal",
                resolved_at=tl.now - timedelta(days=1),
            )
        )
        await session.flush()

    in_uow(url, _cancelled_request)
    closure = add_closure(url, wednesday)
    run(url, tl.now)
    assert row_on(url, series_id, tl, wednesday).state == "skipped_closure"
    existing_session(
        url,
        state="confirmed",
        tutor_user_id=refs["tutor"],
        starts_at=tl.at(wednesday, "16:00"),
        student_id=new_student(url),
    )
    set_closure(url, closure, is_active=False)
    result = run(url, tl.now)

    assert result.failed == 0
    after = row_on(url, series_id, tl, wednesday)
    assert after.state == "conflict"
    [request] = run_sql(url, REQUEST_ROW, {"id": str(after.id)})
    assert (request.id, request.status, request.resolved_at) == (
        earlier,
        "pending",
        None,
    )
