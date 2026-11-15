"""Moving one session: ``PATCH /api/v1/class-sessions/{id}`` (#54 C1, E1;
ADR-0005 D2, D3, D4) with real logins through the real app.

Timelines as in :mod:`~tests.integration.series_edit_support`: "now" is the
Sunday 12:00 before the series' first Monday (Mexico City); weekdays are
open 09:00-20:00.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from app.modules.identity.permissions import OWNER
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import patch, post
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.series_edit_support import (
    CONFLICT,
    INVALID,
    NOT_FOUND,
    OK,
    SESSIONS,
    STALE,
    Ctx,
    add_closure,
    conflict_requests,
    make_conflict,
    make_ctx,
    make_series,
    notes,
    offers_of,
    on_day,
    owner_call,
    patch_session,
    row_by_id,
    rows_of,
    run_job,
    set_row,
    with_family,
)
from tests.integration.series_support import iso_z
from tests.integration.test_class_session_create import (
    CREATED,
    existing_session,
    field_errors,
    new_student,
    new_subject,
)
from tests.integration.test_class_session_create import body as session_body

RESCHEDULED = "scheduling.session_rescheduled"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def ctx(module_seeded_url: str, client: Any) -> Ctx:
    return make_ctx(module_seeded_url, client)


def codes(response: Any) -> list[str]:
    assert code_of(response) == CONFLICT, response.text
    return [c["code"] for c in response.json()["conflicts"]]


def week_row(ctx: Ctx, series: dict[str, Any], week: int, weekday: int = 1) -> Any:
    return on_day(rows_of(ctx.url, series["id"]), ctx, ctx.tl.week(week, weekday))


def move(ctx: Ctx, row: Any, day: date, start: str, end: str, **extra: Any) -> Any:
    return patch_session(
        ctx,
        row.id,
        ctx.tl.now,
        version=row.version,
        starts_at=ctx.tl.at(day, start),
        ends_at=ctx.tl.at(day, end),
        **extra,
    )


# --- An occurrence becomes an exception -------------------------------------------


def test_a_time_change_makes_an_exception_that_survives_regeneration(ctx):
    series = make_series(ctx)
    row = week_row(ctx, series, 2)
    day = ctx.tl.week(2)

    response = move(ctx, row, day, "18:00", "19:00")

    assert code_of(response) == OK, response.text
    data = response.json()
    assert data["is_exception"] is True
    assert data["original_start"] == iso_z(row.original_start)
    assert data["starts_at"] == iso_z(ctx.tl.at(day, "18:00"))
    assert data["ends_at"] == iso_z(ctx.tl.at(day, "19:00"))
    assert data["state"] == "unassigned"
    assert data["warnings"] == []
    assert data["version"] == row.version + 1
    moved = row_by_id(ctx.url, row.id)

    run_job(ctx.url, ctx.tl.now)
    run_job(ctx.url, ctx.tl.now)

    after = row_by_id(ctx.url, row.id)
    assert (after.version, after.updated_at) == (moved.version, moved.updated_at)
    rows = rows_of(ctx.url, series["id"])
    assert len(rows) == 24
    assert [r.id for r in rows if r.original_start == row.original_start] == [row.id]


def test_a_move_to_another_weekday(ctx):
    series = make_series(ctx)
    row = week_row(ctx, series, 2)
    tuesday = ctx.tl.week(2, 2)

    response = move(ctx, row, tuesday, "16:00", "17:00")

    assert code_of(response) == OK, response.text
    stored = row_by_id(ctx.url, row.id)
    assert stored.starts_at == ctx.tl.at(tuesday, "16:00")
    assert stored.original_start == row.original_start
    assert stored.is_exception is True


def test_a_room_change_and_no_room(ctx):
    series = make_series(ctx, room=True)
    row = week_row(ctx, series, 2)
    day = ctx.tl.week(2)

    changed = move(ctx, row, day, "16:00", "17:00", classroom_id=ctx.other_room)

    assert code_of(changed) == OK, changed.text
    assert changed.json()["classroom"]["id"] == str(ctx.other_room)
    removed = patch_session(
        ctx,
        row.id,
        ctx.tl.now,
        version=changed.json()["version"],
        starts_at=ctx.tl.at(day, "16:00"),
        ends_at=ctx.tl.at(day, "17:00"),
        classroom_id=None,
    )
    assert code_of(removed) == OK, removed.text
    assert removed.json()["classroom"] is None
    assert row_by_id(ctx.url, row.id).classroom_id is None
    # Absent keeps the room (now none) and the time moves.
    kept = patch_session(
        ctx,
        row.id,
        ctx.tl.now,
        version=removed.json()["version"],
        starts_at=ctx.tl.at(day, "17:00"),
        ends_at=ctx.tl.at(day, "18:00"),
    )
    assert code_of(kept) == OK, kept.text
    assert kept.json()["classroom"] is None


def test_a_one_off_session_is_moved_without_series_fields(ctx):
    day = ctx.tl.week(2)
    created = owner_call(
        ctx,
        ctx.tl.now,
        lambda cookie: post(
            ctx.client,
            cookie,
            SESSIONS,
            session_body(
                student_id=ctx.student,
                subject_id=ctx.subject,
                starts_at=ctx.tl.at(day, "10:00"),
                ends_at=ctx.tl.at(day, "11:00"),
            ),
        ),
    )
    assert code_of(created) == CREATED, created.text
    row = row_by_id(ctx.url, created.json()["id"])

    response = move(ctx, row, day, "11:00", "12:30")

    assert code_of(response) == OK, response.text
    data = response.json()
    assert (data["series_id"], data["original_start"], data["is_exception"]) == (
        None,
        None,
        False,
    )
    assert data["scheduled_minutes"] == 90


def test_a_fixed_conflict_row_is_scheduled_and_its_request_cancelled(ctx):
    series = make_series(ctx)
    row = week_row(ctx, series, 3)
    make_conflict(ctx.url, row.id, ["tutor_overlap"])
    row = row_by_id(ctx.url, row.id)
    [request] = conflict_requests(ctx.url, row.id)
    assert request.status == "pending"

    response = move(ctx, row, ctx.tl.week(3), "18:00", "19:00")

    assert code_of(response) == OK, response.text
    assert response.json()["state"] == "unassigned"
    assert response.json()["conflict_codes"] == ["tutor_overlap"]
    [request] = conflict_requests(ctx.url, row.id)
    assert request.status == "cancelled"


def test_a_fixed_conflict_row_of_an_accepted_series_is_confirmed(ctx):
    series = make_series(ctx, tutor=True, accept=True)
    row = week_row(ctx, series, 3)
    make_conflict(ctx.url, row.id, ["tutor_unavailable"])
    set_row(ctx.url, row.id, tutor_user_id=None)
    row = row_by_id(ctx.url, row.id)

    response = move(ctx, row, ctx.tl.week(3), "18:00", "19:00")

    assert code_of(response) == OK, response.text
    assert response.json()["state"] == "confirmed"
    assert response.json()["tutor"]["id"] == str(ctx.tutor.id)


# --- Refusals ------------------------------------------------------------------------


def test_overlaps_closures_and_versions_are_refused(ctx):
    series = make_series(ctx, tutor=True, accept=True, room=True)
    row = week_row(ctx, series, 2)
    day = ctx.tl.week(2)
    # The tutor, the student and the room are each busy at another time.
    existing_session(
        ctx.url,
        starts_at=ctx.tl.at(day, "18:00"),
        state="confirmed",
        tutor_user_id=ctx.tutor.id,
        student_id=new_student(ctx.url),
    )
    existing_session(
        ctx.url,
        starts_at=ctx.tl.at(day, "12:00"),
        state="unassigned",
        student_id=ctx.student,
    )
    existing_session(
        ctx.url,
        starts_at=ctx.tl.at(day, "10:00"),
        state="unassigned",
        classroom_id=ctx.room,
        student_id=new_student(ctx.url),
    )
    tuesday = ctx.tl.week(2, 2)
    add_closure(ctx.url, tuesday)
    before = row_by_id(ctx.url, row.id)

    assert codes(move(ctx, row, day, "18:00", "19:00")) == ["tutor_overlap"]
    assert codes(move(ctx, row, day, "12:00", "13:00")) == ["student_overlap"]
    assert codes(move(ctx, row, day, "10:00", "11:00")) == ["room_overlap"]
    assert codes(move(ctx, row, tuesday, "16:00", "17:00")) == ["center_closed"]
    stale = patch_session(
        ctx,
        row.id,
        ctx.tl.now,
        version=row.version - 1,
        starts_at=ctx.tl.at(day, "17:00"),
        ends_at=ctx.tl.at(day, "18:00"),
    )
    assert code_of(stale) == STALE
    after = row_by_id(ctx.url, row.id)
    assert (after.version, after.starts_at) == (before.version, before.starts_at)
    # The row's own slot never conflicts with itself: 16:30 overlaps 16:00.
    assert code_of(move(ctx, row, day, "16:30", "17:30")) == OK


@pytest.mark.parametrize("state", ["completed", "cancelled", "skipped_closure"])
def test_rows_in_final_or_skipped_states_are_not_editable(ctx, state):
    series = make_series(ctx, tutor=True, accept=True)
    row = week_row(ctx, series, 2)
    values: dict[str, Any] = {"state": state}
    if state == "cancelled":
        values |= {"cancelled_at": ctx.tl.now, "cancellation_reason": "Sin clase"}
    set_row(ctx.url, row.id, **values)
    row = row_by_id(ctx.url, row.id)

    response = move(ctx, row, ctx.tl.week(2), "18:00", "19:00")

    assert code_of(response) == (409, "session_not_editable")
    assert response.json()["details"] == {"state": state}


def test_a_started_row_is_not_editable(ctx):
    series = make_series(ctx)
    row = week_row(ctx, series, 1)
    started = ctx.tl.at(ctx.tl.week(1), "16:00")

    response = patch_session(
        ctx,
        row.id,
        started,
        version=row.version,
        starts_at=ctx.tl.at(ctx.tl.week(1), "18:00"),
        ends_at=ctx.tl.at(ctx.tl.week(1), "19:00"),
    )

    assert code_of(response) == (409, "session_not_editable")
    assert response.json()["details"] == {"reason": "started"}


def test_body_errors(ctx):
    series = make_series(ctx)
    row = week_row(ctx, series, 2)
    day = ctx.tl.week(2)

    backwards = move(ctx, row, day, "17:00", "16:00")
    assert field_errors(backwards) == [("ends_at", "ends_before_start")]
    same = move(ctx, row, day, "16:00", "17:00")
    assert field_errors(same) == [("body", "session_unchanged")]
    # #58 A1: `override` is the #49 type (its 422s are in
    # test_class_session_patch_override.py).
    override = move(
        ctx, row, day, "18:00", "19:00", override={"codes": [], "reason": "x"}
    )
    assert field_errors(override) == [("codes", "codes_required")]
    tutor = move(ctx, row, day, "18:00", "19:00", tutor_user_id=str(ctx.tutor.id))
    assert code_of(tutor) == INVALID
    missing = patch_session(ctx, uuid.uuid4(), ctx.tl.now, **_valid(ctx, day))
    assert code_of(missing) == NOT_FOUND
    unknown_room = move(ctx, row, day, "18:00", "19:00", classroom_id=uuid.uuid4())
    assert field_errors(unknown_room) == [("classroom_id", "classroom_unknown")]


def _valid(ctx: Ctx, day: date) -> dict[str, Any]:
    return {
        "version": 1,
        "starts_at": ctx.tl.at(day, "18:00"),
        "ends_at": ctx.tl.at(day, "19:00"),
    }


def test_crossing_the_local_date_in_tijuana(seeded_url):
    owner = create_user(seeded_url, roles=(OWNER,))
    monday = date(2031, 1, 6)
    with auth_client(seeded_url) as client:
        cookie = sign_in(client, owner.username)
        reauthenticate(client, cookie)
        zone = patch(
            client,
            cookie,
            "/api/v1/business-settings",
            {"version": 1, "time_zone": "America/Tijuana"},
        )
        assert code_of(zone) == OK, zone.text
        start = monday.isoformat()
        created = post(
            client,
            cookie,
            SESSIONS,
            session_body(
                student_id=new_student(seeded_url),
                subject_id=new_subject(seeded_url),
                starts_at=f"{start}T10:00:00-08:00",
                ends_at=f"{start}T11:00:00-08:00",
            ),
        )
        assert code_of(created) == CREATED, created.text
        session_id = created.json()["id"]

        late = (
            f"{start}T23:30:00-08:00",
            (monday + timedelta(days=1)).isoformat() + "T00:30:00-08:00",
        )
        crossing = patch(
            client,
            cookie,
            f"{SESSIONS}/{session_id}",
            {"version": 1, "starts_at": late[0], "ends_at": late[1]},
        )

    assert field_errors(crossing) == [("ends_at", "crosses_local_date")]


# --- Offers and notifications --------------------------------------------------------


def test_an_offered_session_moved_before_its_deadline_gets_it_capped(ctx):
    monday2 = ctx.tl.week(2)
    created = owner_call(
        ctx,
        ctx.tl.now,
        lambda cookie: post(
            ctx.client,
            cookie,
            SESSIONS,
            session_body(
                student_id=ctx.student,
                subject_id=ctx.subject,
                tutor_user_id=ctx.tutor.id,
                starts_at=ctx.tl.at(monday2, "10:00"),
                ends_at=ctx.tl.at(monday2, "11:00"),
            ),
        ),
    )
    assert code_of(created) == CREATED, created.text
    row = row_by_id(ctx.url, created.json()["id"])
    [offer] = offers_of(ctx.url, row.id)
    assert offer.respond_by == ctx.tl.now + timedelta(hours=48)

    monday1 = ctx.tl.week(1)
    response = move(ctx, row, monday1, "18:00", "19:00")

    assert code_of(response) == OK, response.text
    assert response.json()["state"] == "offered"
    [offer] = offers_of(ctx.url, row.id)
    assert offer.status == "pending"
    assert offer.respond_by == ctx.tl.at(monday1, "18:00")


def test_an_offered_row_tells_only_the_tutor(ctx):
    student_user, guardian_user = with_family(ctx.url, ctx.student)
    series = make_series(ctx, tutor=True)
    row = week_row(ctx, series, 2)

    response = move(ctx, row, ctx.tl.week(2), "18:00", "19:00")

    assert code_of(response) == OK, response.text
    found = notes(ctx.url, RESCHEDULED, "class_session_id", row.id)
    assert [n.recipient_user_id for n in found] == [ctx.tutor.id]
    assert found[0].params == {
        "class_session_id": str(row.id),
        "starts_at": iso_z(ctx.tl.at(ctx.tl.week(2), "18:00")),
    }
    assert student_user not in {n.recipient_user_id for n in found}
    assert guardian_user not in {n.recipient_user_id for n in found}


def test_a_confirmed_row_tells_the_tutor_and_the_family(ctx):
    student_user, guardian_user = with_family(ctx.url, ctx.student)
    other_student = new_student(ctx.url)
    _, other_guardian = with_family(ctx.url, other_student)
    series = make_series(ctx, tutor=True, accept=True)
    row = week_row(ctx, series, 2)

    response = move(ctx, row, ctx.tl.week(2), "18:00", "19:00")

    assert code_of(response) == OK, response.text
    found = notes(ctx.url, RESCHEDULED, "class_session_id", row.id)
    assert sorted(n.recipient_user_id for n in found) == sorted(
        [ctx.tutor.id, student_user, guardian_user]
    )
    assert other_guardian not in {n.recipient_user_id for n in found}
    assert ctx.owner.id not in {n.recipient_user_id for n in found}


def test_an_unassigned_row_tells_nobody(ctx):
    with_family(ctx.url, ctx.student)
    series = make_series(ctx)
    row = week_row(ctx, series, 2)

    response = move(ctx, row, ctx.tl.week(2), "18:00", "19:00")

    assert code_of(response) == OK, response.text
    assert notes(ctx.url, RESCHEDULED, "class_session_id", row.id) == []
