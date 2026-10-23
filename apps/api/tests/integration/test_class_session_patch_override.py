"""Overrides and workload warnings on a move (#58 A, C; G1, G2):
``PATCH /api/v1/class-sessions/{id}`` with ``override`` through the real
app with real logins.

Timelines as in :mod:`~tests.integration.series_edit_support`: each test
takes its own decade; "now" is the Sunday 12:00 (Mexico City) before
``tl.monday``; weekdays are open 09:00-20:00 and Sunday is closed, so a move
to a Sunday has exactly one soft conflict, ``outside_operating_hours``,
unless a test adds more.
"""

import uuid
from datetime import date, time, timedelta
from typing import Any

import pytest
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service as identity
from app.modules.identity.permissions import ADMINISTRATOR
from app.modules.scheduling import service as scheduling
from app.modules.scheduling import session_edit
from app.modules.scheduling.schemas import ClassSessionUpdate, SchedulingWarning
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, patch
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_edit_support import (
    CONFLICT,
    INVALID,
    OK,
    SESSIONS,
    STALE,
    Ctx,
    add_closure,
    as_user,
    conflict_requests,
    make_conflict,
    make_ctx,
    make_series,
    on_day,
    owner_call,
    patch_session,
    row_by_id,
    rows_of,
)
from tests.integration.series_support import iso_z
from tests.integration.test_class_session_create import (
    existing_session,
    new_room,
    new_student,
    new_tutor,
)

FORBIDDEN = (403, "permission_denied")
EVENT = "scheduling.session_override"
REASON = "La familia lo pidió; el tutor está de acuerdo."
SUNDAY = 7


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def ctx(module_seeded_url: str, client: Any) -> Ctx:
    return make_ctx(module_seeded_url, client)


# --- Helpers -------------------------------------------------------------------------


def one_off(
    ctx: Ctx,
    day: date,
    start: str = "10:00",
    *,
    minutes: int = 60,
    state: str = "unassigned",
    tutor: uuid.UUID | None = None,
    room: uuid.UUID | None = None,
    student: uuid.UUID | None = None,
) -> Any:
    """A stored one-off session of the context's student and subject."""
    session_id = existing_session(
        ctx.url,
        starts_at=ctx.tl.at(day, start),
        minutes=minutes,
        state=state,
        student_id=student or ctx.student,
        subject_id=ctx.subject,
        tutor_user_id=tutor,
        classroom_id=room,
        tutor=tutor is not None,
    )
    return row_by_id(ctx.url, session_id)


def override(*codes: str, reason: Any = REASON) -> dict[str, Any]:
    return {"codes": list(codes), "reason": reason}


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


def move_as(
    ctx: Ctx, user: CreatedUser, row: Any, day: date, start: str, end: str, **extra: Any
) -> Response:
    cookie = as_user(ctx, user, ctx.tl.now)
    payload = {
        "version": row.version,
        "starts_at": ctx.tl.at(day, start).isoformat(),
        "ends_at": ctx.tl.at(day, end).isoformat(),
        **extra,
    }
    with frozen_clock(ctx.tl.now):
        response: Response = patch(ctx.client, cookie, f"{SESSIONS}/{row.id}", payload)
    return response


def sunday(ctx: Ctx, week: int = 2) -> date:
    return ctx.tl.week(week, SUNDAY)


def codes_of(response: Response) -> list[tuple[str, str]]:
    assert code_of(response) == CONFLICT, response.text
    return [(c["code"], c["severity"]) for c in response.json()["conflicts"]]


def override_rows(url: str, session_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, codes, conflicts, reason, actor_user_id, class_series_id"
        " FROM class_session_overrides WHERE class_session_id = :id"
        " ORDER BY created_at",
        {"id": str(session_id)},
    )


def override_events(url: str, session_id: Any) -> list[Any]:
    return events(url, record_id=uuid.UUID(str(session_id)), action=EVENT)


def snapshot(url: str, row: Any) -> tuple[Any, ...]:
    """What a refused move must leave unchanged: the row's times, version
    and ``updated_at``, its override rows and its audit events."""
    stored = row_by_id(url, row.id)
    return (
        stored.starts_at,
        stored.ends_at,
        stored.version,
        stored.updated_at,
        len(override_rows(url, row.id)),
        len(events(url, record_id=row.id)),
    )


def field_errors(response: Response) -> list[tuple[list[Any], str]]:
    assert code_of(response) == INVALID, response.text
    return [(e["path"], e["code"]) for e in response.json()["errors"]]


def _delegate(ctx: Ctx, admin: CreatedUser, *, grant: bool) -> None:
    async def _change(session: AsyncSession) -> None:
        operation = identity.grant_delegation if grant else identity.revoke_delegation
        await operation(
            session,
            actor_id=ctx.owner.id,
            user_id=admin.id,
            permission="schedule.override",
        )

    in_uow(ctx.url, _change, actor_id=ctx.owner.id)


# --- G1: an allowed override ---------------------------------------------------------


def test_the_owner_moves_a_session_to_a_sunday_with_an_override(ctx):
    row = one_off(ctx, ctx.tl.week(2))
    day = sunday(ctx)
    assert codes_of(move(ctx, row, day, "10:00", "11:00")) == [
        ("outside_operating_hours", "soft")
    ]

    response = move(
        ctx,
        row,
        day,
        "10:00",
        "11:00",
        override=override("outside_operating_hours", reason=f"  {REASON}  "),
    )

    assert code_of(response) == OK, response.text
    data = response.json()
    assert data["starts_at"] == iso_z(ctx.tl.at(day, "10:00"))
    assert data["version"] == row.version + 1
    assert data["warnings"] == []
    [stored] = override_rows(ctx.url, row.id)
    assert stored.codes == ["outside_operating_hours"]
    assert stored.conflicts == [
        {
            "code": "outside_operating_hours",
            "details": {"date": day.isoformat(), "opens_at": None, "closes_at": None},
        }
    ]
    assert stored.reason == REASON
    assert stored.actor_user_id == ctx.owner.id
    assert stored.class_series_id is None
    [event] = override_events(ctx.url, row.id)
    assert event.actor_user_id == ctx.owner.id
    assert event.details == {
        "override_id": str(stored.id),
        "codes": ["outside_operating_hours"],
        "conflicts": stored.conflicts,
        "reason": REASON,
    }
    [update] = events(ctx.url, record_id=row.id, action="update")
    assert event.request_id and event.request_id == update.request_id
    [item] = data["overrides"]
    assert (item["id"], item["codes"], item["reason"]) == (
        str(stored.id),
        ["outside_operating_hours"],
        REASON,
    )


def _tutor_unavailable(ctx: Ctx) -> tuple[Any, date, str, str]:
    tutor = new_tutor(
        ctx.url, qualified_for=[ctx.subject], windows=[(1, time(16), time(20))]
    )
    row = one_off(ctx, ctx.tl.week(2), "16:00", state="confirmed", tutor=tutor)
    return row, ctx.tl.week(2), "10:00", "11:00"


def _room_unavailable(ctx: Ctx) -> tuple[Any, date, str, str]:
    room = new_room(ctx.url, windows=[(1, time(16), time(20))])
    row = one_off(ctx, ctx.tl.week(2), "16:00", room=room)
    return row, ctx.tl.week(2), "10:00", "11:00"


def _center_closed(ctx: Ctx) -> tuple[Any, date, str, str]:
    tuesday = ctx.tl.week(2, 2)
    add_closure(ctx.url, tuesday)
    return one_off(ctx, ctx.tl.week(2)), tuesday, "10:00", "11:00"


def _outside_operating_hours(ctx: Ctx) -> tuple[Any, date, str, str]:
    return one_off(ctx, ctx.tl.week(2)), sunday(ctx), "10:00", "11:00"


def _tutor_not_qualified(ctx: Ctx) -> tuple[Any, date, str, str]:
    row = one_off(ctx, ctx.tl.week(2), state="confirmed", tutor=new_tutor(ctx.url))
    return row, ctx.tl.week(2), "12:00", "13:00"


def _notice_window(ctx: Ctx) -> tuple[Any, date, str, str]:
    # "Now" is Sunday 12:00: Monday 10:00 of week 1 is 22 hours ahead.
    return one_off(ctx, ctx.tl.week(1, 2)), ctx.tl.week(1), "10:00", "11:00"


SOFT_CASES = {
    "tutor_unavailable": _tutor_unavailable,
    "room_unavailable": _room_unavailable,
    "center_closed": _center_closed,
    "outside_operating_hours": _outside_operating_hours,
    "tutor_not_qualified": _tutor_not_qualified,
    "notice_window": _notice_window,
}


@pytest.mark.parametrize("code", sorted(SOFT_CASES))
def test_each_soft_code_a_move_can_meet_is_overridable(ctx, code):
    row, day, start, end = SOFT_CASES[code](ctx)
    assert codes_of(move(ctx, row, day, start, end)) == [(code, "soft")]

    response = move(ctx, row, day, start, end, override=override(code))

    assert code_of(response) == OK, response.text
    assert [o["codes"] for o in response.json()["overrides"]] == [[code]]
    [stored] = override_rows(ctx.url, row.id)
    assert stored.codes == [code]


# --- G1: refusals --------------------------------------------------------------------


@pytest.mark.parametrize("hard", ["student_overlap", "tutor_overlap", "room_overlap"])
def test_a_hard_conflict_refuses_whatever_the_override_says(ctx, hard):
    row = one_off(
        ctx, ctx.tl.week(2), state="confirmed", tutor=ctx.tutor.id, room=ctx.room
    )
    day = sunday(ctx)
    holder: dict[str, Any] = {
        "student_overlap": {"student_id": ctx.student},
        "tutor_overlap": {
            "tutor_user_id": ctx.tutor.id,
            "student_id": new_student(ctx.url),
            "state": "offered",
        },
        "room_overlap": {"classroom_id": ctx.room, "student_id": new_student(ctx.url)},
    }[hard]
    holder.setdefault("state", "unassigned")
    holder.setdefault("tutor", "tutor_user_id" in holder)
    existing_session(ctx.url, starts_at=ctx.tl.at(day, "10:30"), **holder)
    before = snapshot(ctx.url, row)

    response = move(
        ctx, row, day, "10:00", "11:00", override=override("outside_operating_hours")
    )

    assert codes_of(response) == [(hard, "hard"), ("outside_operating_hours", "soft")]
    assert "override" not in response.json()
    assert snapshot(ctx.url, row) == before


def test_a_partial_override_refuses_with_the_complete_list(ctx):
    row = one_off(ctx, ctx.tl.week(2), state="confirmed", tutor=new_tutor(ctx.url))
    before = snapshot(ctx.url, row)

    response = move(
        ctx,
        row,
        sunday(ctx),
        "10:00",
        "11:00",
        override=override("tutor_not_qualified"),
    )

    assert codes_of(response) == [
        ("outside_operating_hours", "soft"),
        ("tutor_not_qualified", "soft"),
    ]
    assert snapshot(ctx.url, row) == before


@pytest.mark.parametrize("conflicting", [True, False])
def test_an_administrator_without_the_delegation_is_403(ctx, conflicting):
    admin = create_user(ctx.url, roles=(ADMINISTRATOR,))
    row = one_off(ctx, ctx.tl.week(2))
    day = sunday(ctx) if conflicting else ctx.tl.week(2)
    before = snapshot(ctx.url, row)

    response = move_as(
        ctx,
        admin,
        row,
        day,
        "15:00",
        "16:00",
        override=override("outside_operating_hours"),
    )

    assert code_of(response) == FORBIDDEN
    assert snapshot(ctx.url, row) == before


def test_the_403_comes_before_the_404(ctx):
    admin = create_user(ctx.url, roles=(ADMINISTRATOR,))
    ghost = type("Row", (), {"id": uuid.uuid4(), "version": 1})()

    response = move_as(
        ctx,
        admin,
        ghost,
        sunday(ctx),
        "10:00",
        "11:00",
        override=override("outside_operating_hours"),
    )

    assert code_of(response) == FORBIDDEN


def test_a_delegated_administrator_overrides_until_revoked(ctx):
    admin = create_user(ctx.url, roles=(ADMINISTRATOR,))
    first = one_off(ctx, ctx.tl.week(2))
    second = one_off(ctx, ctx.tl.week(2), student=new_student(ctx.url))
    body = {"override": override("outside_operating_hours")}

    _delegate(ctx, admin, grant=True)
    allowed = move_as(ctx, admin, first, sunday(ctx), "10:00", "11:00", **body)
    _delegate(ctx, admin, grant=False)
    revoked = move_as(ctx, admin, second, sunday(ctx), "12:00", "13:00", **body)

    assert code_of(allowed) == OK, allowed.text
    [stored] = override_rows(ctx.url, first.id)
    assert stored.actor_user_id == admin.id
    assert code_of(revoked) == FORBIDDEN
    assert override_rows(ctx.url, second.id) == []


def test_listed_codes_that_do_not_occur_are_ignored(ctx):
    row = one_off(ctx, ctx.tl.week(2))

    response = move(
        ctx,
        row,
        sunday(ctx),
        "10:00",
        "11:00",
        override=override("center_closed", "outside_operating_hours", "notice_window"),
    )

    assert code_of(response) == OK, response.text
    [stored] = override_rows(ctx.url, row.id)
    assert stored.codes == ["outside_operating_hours"]
    [event] = override_events(ctx.url, row.id)
    assert event.details["codes"] == ["outside_operating_hours"]


def test_a_conflict_free_move_with_an_override_stores_nothing(ctx):
    row = one_off(ctx, ctx.tl.week(2))

    response = move(
        ctx, row, ctx.tl.week(2), "15:00", "16:00", override=override("center_closed")
    )

    assert code_of(response) == OK, response.text
    assert response.json()["overrides"] == []
    assert override_rows(ctx.url, row.id) == []
    assert override_events(ctx.url, row.id) == []


def test_a_stale_version_with_a_valid_override_stores_nothing(ctx):
    row = one_off(ctx, ctx.tl.week(2))
    assert code_of(move(ctx, row, ctx.tl.week(2), "15:00", "16:00")) == OK
    before = snapshot(ctx.url, row)

    response = move(
        ctx,
        row,
        sunday(ctx),
        "10:00",
        "11:00",
        override=override("outside_operating_hours"),
    )

    assert code_of(response) == STALE
    assert snapshot(ctx.url, row) == before


def test_an_unchanged_body_with_an_override_is_session_unchanged(ctx):
    row = one_off(ctx, ctx.tl.week(2))

    response = move(
        ctx,
        row,
        ctx.tl.week(2),
        "10:00",
        "11:00",
        override=override("outside_operating_hours"),
    )

    assert field_errors(response) == [(["body"], "session_unchanged")]


def test_an_earlier_override_never_carries_forward(ctx):
    # A3: overridden onto a Sunday, then moved an hour later on that Sunday.
    row = one_off(ctx, ctx.tl.week(2))
    day = sunday(ctx)
    first = move(
        ctx, row, day, "10:00", "11:00", override=override("outside_operating_hours")
    )
    assert code_of(first) == OK, first.text
    moved = row_by_id(ctx.url, row.id)

    again = move(ctx, moved, day, "11:00", "12:00")

    assert codes_of(again) == [("outside_operating_hours", "soft")]
    assert len(override_rows(ctx.url, row.id)) == 1
    assert row_by_id(ctx.url, row.id).starts_at == ctx.tl.at(day, "10:00")


def test_an_occurrence_moved_with_an_override(ctx):
    series = make_series(ctx)
    row = on_day(rows_of(ctx.url, series["id"]), ctx, ctx.tl.week(2))

    response = move(
        ctx,
        row,
        sunday(ctx),
        "16:00",
        "17:00",
        override=override("outside_operating_hours"),
    )

    assert code_of(response) == OK, response.text
    data = response.json()
    assert data["is_exception"] is True
    assert data["original_start"] == iso_z(row.original_start)
    [stored] = override_rows(ctx.url, row.id)
    assert str(stored.class_series_id) == series["id"]


def test_a_conflict_row_moved_with_an_override_is_scheduled(ctx):
    series = make_series(ctx)
    row = on_day(rows_of(ctx.url, series["id"]), ctx, ctx.tl.week(3))
    make_conflict(ctx.url, row.id, ["tutor_overlap"])
    row = row_by_id(ctx.url, row.id)

    response = move(
        ctx,
        row,
        sunday(ctx, 3),
        "16:00",
        "17:00",
        override=override("outside_operating_hours"),
    )

    assert code_of(response) == OK, response.text
    assert response.json()["state"] == "unassigned"
    [request] = conflict_requests(ctx.url, row.id)
    assert request.status == "cancelled"
    [stored] = override_rows(ctx.url, row.id)
    assert stored.codes == ["outside_operating_hours"]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ({"codes": ["outside_operating_hours"]}, (["reason"], "reason_required")),
        (
            override("outside_operating_hours", reason="  "),
            (["reason"], "reason_required"),
        ),
        (
            override("outside_operating_hours", reason="a" * 1001),
            (["reason"], "reason_too_long"),
        ),
        (override(), (["codes"], "codes_required")),
        (
            override("outside_operating_hours", "outside_operating_hours"),
            (["codes"], "duplicate_code"),
        ),
        (
            override("outside_operating_hours", "student_overlap"),
            (["codes", 1], "code_not_overridable"),
        ),
        (override("whatever"), (["codes", 0], "code_unknown")),
    ],
)
def test_override_body_errors_are_422(ctx, value, error):
    row = one_off(ctx, ctx.tl.week(2))
    path, code = error

    response = move(ctx, row, sunday(ctx), "10:00", "11:00", override=value)

    assert field_errors(response) == [(["body", "override", *path], code)]
    assert override_rows(ctx.url, row.id) == []


# --- G2: workload warnings -----------------------------------------------------------


def limits(
    ctx: Ctx,
    tutor: uuid.UUID,
    *,
    daily: int | None = None,
    weekly: int | None = None,
    rest: int | None = None,
) -> None:
    response = owner_call(
        ctx,
        ctx.tl.now,
        lambda cookie: patch(
            ctx.client,
            cookie,
            f"/api/v1/tutors/{tutor}/availability/settings",
            {
                "version": 0,
                "daily_limit_minutes": daily,
                "weekly_limit_minutes": weekly,
                "required_break_minutes": rest,
            },
        ),
    )
    assert code_of(response) == OK, response.text


def tutor_session(ctx: Ctx, day: date, start: str, **fields: Any) -> Any:
    """A confirmed session of the context's tutor, with another student."""
    fields.setdefault("student", new_student(ctx.url))
    return one_off(ctx, day, start, state="confirmed", tutor=ctx.tutor.id, **fields)


def warning_codes(response: Response) -> list[str]:
    assert code_of(response) == OK, response.text
    return [w["code"] for w in response.json()["warnings"]]


def test_a_move_that_leaves_too_short_a_break(ctx):
    limits(ctx, ctx.tutor.id, rest=30)
    day = ctx.tl.week(2)
    tutor_session(ctx, day, "12:00")
    row = tutor_session(ctx, day, "15:00")

    response = move(ctx, row, day, "13:15", "14:15")

    assert warning_codes(response) == ["break_too_short"]


def test_a_move_past_the_daily_limit(ctx):
    limits(ctx, ctx.tutor.id, daily=90)
    day = ctx.tl.week(2)
    tutor_session(ctx, day, "08:00")
    row = tutor_session(ctx, ctx.tl.week(2, 2), "10:00")

    response = move(ctx, row, day, "10:00", "11:00")

    assert warning_codes(response) == ["daily_limit_exceeded"]
    [warning] = response.json()["warnings"]
    assert warning["details"]["scheduled_minutes"] == 120


def test_a_moved_session_does_not_count_itself(ctx):
    # Exactly at the limit: 60 + 60 minutes. Counting the session's old row
    # too would make it 180 and warn.
    limits(ctx, ctx.tutor.id, daily=120)
    day = ctx.tl.week(2)
    tutor_session(ctx, day, "08:00")
    row = tutor_session(ctx, day, "10:00")

    response = move(ctx, row, day, "10:15", "11:15")

    assert warning_codes(response) == []


def test_a_session_without_a_tutor_has_no_warnings(ctx):
    limits(ctx, ctx.tutor.id, daily=30)
    tutor_session(ctx, ctx.tl.week(2), "08:00")
    row = one_off(ctx, ctx.tl.week(2))

    assert warning_codes(move(ctx, row, ctx.tl.week(2), "12:00", "13:00")) == []


def test_warnings_never_come_in_a_409(ctx):
    limits(ctx, ctx.tutor.id, daily=30)
    row = tutor_session(ctx, ctx.tl.week(2), "10:00")

    response = move(ctx, row, sunday(ctx), "10:00", "11:00")

    assert codes_of(response) == [("outside_operating_hours", "soft")]
    assert "warnings" not in response.json()


def test_the_warnings_add_at_most_two_statements(ctx, monkeypatch):
    limits(ctx, ctx.tutor.id, daily=30)
    day = ctx.tl.week(2)
    tutor_session(ctx, day, "08:00")
    rows = [tutor_session(ctx, day, start) for start in ("10:00", "13:00")]

    def run(row: Any) -> tuple[int, Any]:
        body = ClassSessionUpdate(
            version=row.version,
            starts_at=row.starts_at + timedelta(minutes=15),
            ends_at=row.ends_at + timedelta(minutes=15),
        )
        with frozen_clock(ctx.tl.now):
            return count_statements(
                ctx.url,
                lambda session: scheduling.update_class_session(
                    session, row.id, body, actor_id=ctx.owner.id
                ),
            )

    with_warnings, updated = run(rows[0])

    async def no_warnings(*args: Any, **kwargs: Any) -> list[SchedulingWarning]:
        return []

    monkeypatch.setattr(session_edit, "session_warnings", no_warnings)
    without, _ = run(rows[1])

    assert [w.code for w in updated.warnings] == ["daily_limit_exceeded"]
    assert with_warnings - without <= 2
