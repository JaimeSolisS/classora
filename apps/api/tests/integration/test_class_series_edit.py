"""Editing a series from a date on: ``POST /api/v1/class-series/{id}/edits``
(#54 C2, C3, E2; ADR-0005 D4) with real logins and a frozen clock.

Every series is Monday/Wednesday 16:00 for 24 occurrences, created at the
Sunday 12:00 before its first Monday (Mexico City), so weeks 1-12 are all
stored (:mod:`~tests.integration.series_edit_support`).
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest

from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_edit_support import (
    CONFLICT,
    INVALID,
    OK,
    SESSIONS,
    STALE,
    Ctx,
    add_closure,
    as_user,
    cancel_row,
    conflict_requests,
    edit_body,
    edit_series,
    make_conflict,
    make_ctx,
    make_series,
    notes,
    offers_of,
    on_day,
    patch_session,
    read_series_at,
    row_by_id,
    rows_of,
    run_job,
    series_row,
    snapshot,
    total,
    with_family,
)
from tests.integration.series_support import iso_z
from tests.integration.test_class_session_create import existing_session, new_student

CHANGED = "scheduling.series_changed"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def ctx(module_seeded_url: str, client: Any) -> Ctx:
    return make_ctx(module_seeded_url, client)


def scheduling_events(url: str) -> int:
    """Audit events about sessions and series (sign-ins write their own)."""
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events"
        " WHERE record_type IN ('class_sessions', 'class_series')",
    )
    return int(row.n)


def local_day(ctx: Ctx, row: Any) -> Any:
    return row.original_start.astimezone(ctx.tl.now.tzinfo).date()


def edit(ctx: Ctx, series: dict[str, Any], at: Any = None, **body: Any) -> Any:
    body.setdefault("version", series_row(ctx.url, series["id"]).version)
    return edit_series(ctx, series["id"], at or ctx.tl.now, edit_body(**body))


def ok(response: Any) -> dict[str, Any]:
    assert code_of(response) == OK, response.text
    found: dict[str, Any] = response.json()
    return found


def last_wednesday(ctx: Ctx) -> Any:
    return ctx.tl.week(12, 3)


# --- Splits --------------------------------------------------------------------


def test_other_weekdays_from_week_3_split_the_series(ctx):
    series = make_series(ctx)
    d = ctx.tl.week(3)
    before = snapshot(
        [r for r in rows_of(ctx.url, series["id"]) if local_day(ctx, r) < d]
    )

    data = ok(
        edit(
            ctx,
            series,
            from_date=d,
            weekdays=[2, 4],
            start_time="17:00",
            until=last_wednesday(ctx),
        )
    )

    assert data["split"] is True
    assert data["previous_series_id"] == series["id"]
    assert data["summary"] == {
        "moved": 0,
        "created": 19,
        "cancelled": 20,
        "skipped_closure": 0,
        "exceptions_reset": 0,
    }
    new = data["series"]
    assert new["id"] != series["id"]
    assert new["split_from_series_id"] == series["id"]
    assert new["starts_on"] == d.isoformat()
    assert new["start_time"] == "17:00"
    assert new["recurrence"] == {
        "weekdays": [2, 4],
        "interval": 1,
        "until": last_wednesday(ctx).isoformat(),
        "count": None,
    }
    assert data["warnings"] == []
    old = series_row(ctx.url, series["id"])
    assert (old.until_on, old.count) == (d - timedelta(days=1), None)
    assert old.last_occurrence_on == ctx.tl.week(2, 3)
    assert old.version == series["version"] + 1
    assert old.rrule.endswith(f"UNTIL={(d - timedelta(days=1)):%Y%m%d}T235959")

    old_rows = rows_of(ctx.url, series["id"])
    assert snapshot([r for r in old_rows if local_day(ctx, r) < d]) == before
    later = [r for r in old_rows if local_day(ctx, r) >= d]
    assert len(later) == 20
    assert {(r.state, r.cancellation_reason) for r in later} == {
        ("cancelled", "series_changed")
    }
    new_rows = rows_of(ctx.url, new["id"])
    assert len(new_rows) == 19  # the last Thursday is after `until`
    weekdays = {local_day(ctx, r).isoweekday() for r in new_rows}
    assert weekdays == {2, 4}
    assert {r.state for r in new_rows} == {"unassigned"}
    assert new_rows[0].starts_at == ctx.tl.at(ctx.tl.week(3, 2), "17:00")
    # The lineage on both reads.
    assert read_series_at(ctx, series["id"], ctx.tl.now)["split_into_series_ids"] == [
        new["id"]
    ]


def test_a_time_change_moves_every_row_with_its_id(ctx):
    series = make_series(ctx, tutor=True, accept=True)
    d = ctx.tl.week(3)
    rows = rows_of(ctx.url, series["id"])
    in_scope = [r for r in rows if local_day(ctx, r) >= d]
    exception = on_day(rows, ctx, ctx.tl.week(4))
    moved = patch_session(
        ctx,
        exception.id,
        ctx.tl.now,
        version=exception.version,
        starts_at=ctx.tl.at(ctx.tl.week(4), "18:00"),
        ends_at=ctx.tl.at(ctx.tl.week(4), "19:00"),
    )
    assert code_of(moved) == OK, moved.text

    # 16:30 overlaps every row's own 16:00-17:00: self-exclusion.
    data = ok(
        edit(ctx, series, from_date=d, start_time="16:30", until=last_wednesday(ctx))
    )

    assert data["summary"] == {
        "moved": 20,
        "created": 0,
        "cancelled": 0,
        "skipped_closure": 0,
        "exceptions_reset": 1,
    }
    new_id = data["series"]["id"]
    after = rows_of(ctx.url, new_id)
    assert [r.id for r in after] == [r.id for r in in_scope]
    for old, new in zip(in_scope, after, strict=True):
        day = local_day(ctx, old)
        assert new.starts_at == ctx.tl.at(day, "16:30")
        assert new.original_start == ctx.tl.at(day, "16:30")
        assert (new.state, new.tutor_user_id) == ("confirmed", ctx.tutor.id)
        assert new.is_exception is False
    detail = data["series"]
    assert detail["assignment"]["status"] == "accepted"
    assert detail["assignment"]["from_date"] == d.isoformat()


def test_dropping_wednesday_moves_mondays_and_cancels_wednesdays(ctx):
    series = make_series(ctx)
    d = ctx.tl.week(3)

    data = ok(edit(ctx, series, from_date=d, weekdays=[1], until=last_wednesday(ctx)))

    assert data["summary"]["moved"] == 10
    assert data["summary"]["cancelled"] == 10
    new_rows = rows_of(ctx.url, data["series"]["id"])
    assert {local_day(ctx, r).isoweekday() for r in new_rows} == {1}
    old_later = [r for r in rows_of(ctx.url, series["id"]) if local_day(ctx, r) >= d]
    assert {local_day(ctx, r).isoweekday() for r in old_later} == {3}
    assert {r.cancellation_reason for r in old_later} == {"series_changed"}


def test_all_on_a_series_with_nothing_started_is_in_place(ctx):
    series = make_series(ctx)
    ids = [r.id for r in rows_of(ctx.url, series["id"])]

    data = ok(edit(ctx, series, mode="all", start_time="17:00", count=24))

    assert data["split"] is False
    assert data["previous_series_id"] is None
    detail = data["series"]
    assert detail["id"] == series["id"]
    assert detail["split_from_series_id"] is None
    assert detail["split_into_series_ids"] == []
    assert detail["version"] == series["version"] + 1
    assert detail["starts_on"] == ctx.tl.monday.isoformat()
    assert [r.id for r in rows_of(ctx.url, series["id"])] == ids
    assert data["summary"]["moved"] == 24


def test_all_on_a_running_series_splits_at_the_next_row(ctx):
    series = make_series(ctx)
    later = ctx.tl.at(ctx.tl.week(1), "17:00")  # the first Monday has begun

    data = ok(edit(ctx, series, at=later, mode="all", start_time="17:00", count=23))

    assert data["split"] is True
    wednesday = ctx.tl.week(1, 3)
    assert data["series"]["starts_on"] == wednesday.isoformat()
    old = series_row(ctx.url, series["id"])
    assert old.until_on == wednesday - timedelta(days=1)
    assert old.last_occurrence_on == ctx.tl.week(1)
    [first] = rows_of(ctx.url, series["id"])
    assert first.starts_at == ctx.tl.at(ctx.tl.week(1), "16:00")


# --- E6, closures --------------------------------------------------------------


def test_a_future_cancelled_row_is_repointed_and_never_recreated(ctx):
    series = make_series(ctx)
    monday4 = ctx.tl.week(4)
    cancelled = on_day(rows_of(ctx.url, series["id"]), ctx, monday4)
    cancel_row(ctx.url, cancelled.id, "Viaje familiar")
    before = row_by_id(ctx.url, cancelled.id)

    data = ok(
        edit(
            ctx,
            series,
            from_date=ctx.tl.week(3),
            start_time="17:00",
            until=last_wednesday(ctx),
        )
    )

    new_id = uuid.UUID(data["series"]["id"])
    after = row_by_id(ctx.url, cancelled.id)
    assert after.series_id == new_id
    assert after.original_start == ctx.tl.at(monday4, "17:00")
    for column in (
        "state",
        "starts_at",
        "ends_at",
        "cancellation_reason",
        "cancelled_at",
        "tutor_user_id",
        "classroom_id",
        "is_exception",
    ):
        assert getattr(after, column) == getattr(before, column), column
    assert data["summary"]["moved"] == 19

    run_job(ctx.url, ctx.tl.now)

    on_monday4 = [r for r in rows_of(ctx.url, new_id) if local_day(ctx, r) == monday4]
    assert [r.id for r in on_monday4] == [cancelled.id]


def test_skipped_rows_move_when_matched_and_stay_when_not(ctx):
    add_closure(ctx.url, ctx.tl.week(5), ctx.tl.week(5, 3))
    series = make_series(ctx)
    rows = rows_of(ctx.url, series["id"])
    monday5 = on_day(rows, ctx, ctx.tl.week(5))
    wednesday5 = on_day(rows, ctx, ctx.tl.week(5, 3))
    assert {monday5.state, wednesday5.state} == {"skipped_closure"}

    data = ok(
        edit(
            ctx,
            series,
            from_date=ctx.tl.week(3),
            weekdays=[1],
            start_time="17:00",
            until=last_wednesday(ctx),
        )
    )

    moved = row_by_id(ctx.url, monday5.id)
    assert str(moved.series_id) == data["series"]["id"]
    assert (moved.state, moved.starts_at) == (
        "skipped_closure",
        ctx.tl.at(ctx.tl.week(5), "17:00"),
    )
    kept = row_by_id(ctx.url, wednesday5.id)
    assert dict(kept._mapping) == dict(wednesday5._mapping)
    assert data["summary"]["skipped_closure"] == 1


def test_a_closure_on_a_new_date_creates_a_skipped_row(ctx):
    tuesday4 = ctx.tl.week(4, 2)
    add_closure(ctx.url, tuesday4)
    series = make_series(ctx)

    data = ok(
        edit(
            ctx,
            series,
            from_date=ctx.tl.week(3),
            weekdays=[2, 4],
            until=last_wednesday(ctx),
        )
    )

    rows = rows_of(ctx.url, data["series"]["id"])
    assert on_day(rows, ctx, tuesday4).state == "skipped_closure"
    assert data["summary"]["skipped_closure"] == 1


# --- Refusals ------------------------------------------------------------------


def test_conflicts_are_listed_per_occurrence_and_nothing_is_written(ctx):
    series = make_series(ctx, tutor=True, accept=True)
    tuesday4 = ctx.tl.week(4, 2)
    blocker = existing_session(
        ctx.url,
        starts_at=ctx.tl.at(tuesday4, "11:00"),
        state="confirmed",
        tutor_user_id=ctx.tutor.id,
        student_id=new_student(ctx.url),
    )
    tables = ("class_series", "class_sessions", "notifications")
    counts = [total(ctx.url, t) for t in tables] + [scheduling_events(ctx.url)]

    # Monday 11:00 is 23 hours ahead: notice_window on the first occurrence.
    response = edit(
        ctx, series, mode="all", weekdays=[1, 2], start_time="11:00", count=24
    )

    assert code_of(response) == CONFLICT, response.text
    found = [
        (c["code"], c["details"]["occurrence_start"], c["details"]["date"])
        for c in response.json()["conflicts"]
    ]
    assert found == [
        (
            "notice_window",
            iso_z(ctx.tl.at(ctx.tl.monday, "11:00")),
            ctx.tl.monday.isoformat(),
        ),
        ("tutor_overlap", iso_z(ctx.tl.at(tuesday4, "11:00")), tuesday4.isoformat()),
    ]
    tutor = response.json()["conflicts"][1]
    assert tutor["details"]["class_session_ids"] == [str(blocker)]
    assert [total(ctx.url, t) for t in tables] + [scheduling_events(ctx.url)] == counts


def test_state_refusals(ctx):
    other = make_ctx(ctx.url, ctx.client)
    pending = make_series(other, tutor=True)
    waiting = edit(other, pending, from_date=other.tl.week(3), start_time="17:00")
    assert code_of(waiting) == (409, "series_offer_pending")
    series = make_series(ctx)
    stale = edit(ctx, series, version=0, from_date=ctx.tl.week(3), start_time="17:00")
    assert code_of(stale) == STALE
    ended = edit(
        ctx,
        series,
        from_date=last_wednesday(ctx) + timedelta(days=1),
        start_time="17:00",
    )
    assert code_of(ended) == (409, "series_ended")
    started = edit(
        ctx,
        series,
        at=ctx.tl.at(ctx.tl.week(1), "16:30"),
        from_date=ctx.tl.week(1),
        start_time="17:00",
    )
    assert code_of(started) == (409, "occurrence_started")


def errors_of(response: Any) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [(str(e["path"][-1]), e["code"]) for e in response.json()["errors"]]


@pytest.mark.parametrize(
    ("recurrence", "expected"),
    [
        ({"weekdays": []}, ("weekdays", "weekdays_empty")),
        ({"weekdays": [8]}, ("weekdays", "weekday_out_of_range")),
        ({"weekdays": [1, 1]}, ("weekdays", "weekdays_duplicate")),
        ({"interval": 5}, ("interval", "interval_out_of_range")),
        ({"count": 3, "until": "U"}, ("until", "until_and_count")),
        ({"count": 201}, ("count", "count_out_of_range")),
        ({"until": "D-1"}, ("until", "until_before_start")),
        ({"weekdays": [5], "until": "D+1"}, ("recurrence", "no_occurrences")),
    ],
)
def test_recurrence_errors(ctx, recurrence, expected):
    series = make_series(ctx)
    d = ctx.tl.week(3)
    dates = {"U": last_wednesday(ctx), "D-1": d - timedelta(1), "D+1": d + timedelta(1)}
    if "until" in recurrence:
        recurrence["until"] = dates[recurrence["until"]]

    response = edit(ctx, series, from_date=d, start_time="17:00", **recurrence)

    assert errors_of(response) == [expected]


def test_body_errors(ctx):
    series = make_series(ctx)
    d = ctx.tl.week(3)
    until = last_wednesday(ctx)

    past = edit(ctx, series, from_date=ctx.tl.now.date() - timedelta(1), until=until)
    assert errors_of(past) == [("from_date", "from_date_in_past")]
    with_date = edit(ctx, series, mode="all", from_date=d, start_time="17:00")
    assert errors_of(with_date) == [("from_date", "from_date_not_allowed")]
    without = edit(ctx, series, from_date=None, start_time="17:00")
    assert errors_of(without) == [("from_date", "from_date_required")]
    same = edit(ctx, series, from_date=d, count=24)
    assert errors_of(same) == [("body", "series_unchanged")]
    late = edit(ctx, series, from_date=d, start_time="23:30", until=until)
    assert errors_of(late) == [("start_time", "crosses_local_date")]
    room = edit(ctx, series, from_date=d, classroom_id=uuid.uuid4(), until=until)
    assert errors_of(room) == [("classroom_id", "classroom_unknown")]
    override = edit(
        ctx, series, from_date=d, start_time="17:00", override={"pairs": []}
    )
    assert code_of(override) == INVALID


def test_an_exception_makes_an_unchanged_rule_an_edit(ctx):
    series = make_series(ctx)
    row = on_day(rows_of(ctx.url, series["id"]), ctx, ctx.tl.week(4))
    moved = patch_session(
        ctx,
        row.id,
        ctx.tl.now,
        version=row.version,
        starts_at=ctx.tl.at(ctx.tl.week(4), "18:00"),
        ends_at=ctx.tl.at(ctx.tl.week(4), "19:00"),
    )
    assert code_of(moved) == OK

    data = ok(edit(ctx, series, mode="all", count=24))

    assert data["summary"]["exceptions_reset"] == 1
    assert row_by_id(ctx.url, row.id).starts_at == ctx.tl.at(ctx.tl.week(4), "16:00")


# --- Side effects --------------------------------------------------------------


def test_conflict_rows_leave_the_inbox(ctx):
    series = make_series(ctx)
    rows = rows_of(ctx.url, series["id"])
    monday4 = on_day(rows, ctx, ctx.tl.week(4))
    wednesday4 = on_day(rows, ctx, ctx.tl.week(4, 3))
    make_conflict(ctx.url, monday4.id, ["tutor_overlap"])
    make_conflict(ctx.url, wednesday4.id, ["tutor_overlap"])

    ok(
        edit(
            ctx,
            series,
            from_date=ctx.tl.week(3),
            weekdays=[1],
            until=last_wednesday(ctx),
        )
    )

    fixed = row_by_id(ctx.url, monday4.id)
    assert fixed.state == "unassigned"
    [request] = conflict_requests(ctx.url, monday4.id)
    assert request.status == "cancelled"
    gone = row_by_id(ctx.url, wednesday4.id)
    assert (gone.state, gone.cancellation_reason) == ("cancelled", "series_changed")
    [request] = conflict_requests(ctx.url, wednesday4.id)
    assert request.status == "cancelled"


def test_a_cancelled_offered_row_has_its_offer_withdrawn(ctx):
    series = make_series(ctx)
    wednesday4 = on_day(rows_of(ctx.url, series["id"]), ctx, ctx.tl.week(4, 3))
    cookie = as_user(ctx, ctx.owner, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        offered = post(
            ctx.client,
            cookie,
            f"{SESSIONS}/{wednesday4.id}/offers",
            {"tutor_user_id": str(ctx.tutor.id), "version": wednesday4.version},
        )
    assert code_of(offered) == (201, None), offered.text

    ok(edit(ctx, series, from_date=ctx.tl.week(3), weekdays=[1], count=10))

    [offer] = offers_of(ctx.url, wednesday4.id)
    assert offer.status == "withdrawn"
    assert row_by_id(ctx.url, wednesday4.id).state == "cancelled"


def test_the_named_event_and_one_notification_per_recipient(ctx):
    student_user, guardian_user = with_family(ctx.url, ctx.student)
    series = make_series(ctx, tutor=True, accept=True)
    d = ctx.tl.week(3)

    data = ok(
        edit(
            ctx,
            series,
            from_date=d,
            weekdays=[1],
            start_time="17:00",
            until=last_wednesday(ctx),
        )
    )

    new_id = data["series"]["id"]
    found = notes(ctx.url, CHANGED, "series_id", new_id)
    assert sorted(n.recipient_user_id for n in found) == sorted(
        [ctx.tutor.id, student_user, guardian_user]
    )
    assert {tuple(sorted(n.params.items())) for n in found} == {
        (
            ("cancelled", 10),
            ("changed", 10),
            ("from_date", d.isoformat()),
            ("series_id", new_id),
        )
    }
    [event] = events(
        ctx.url,
        action="scheduling.series_changed",
        record_id=uuid.UUID(series["id"]),
    )
    assert event.record_type == "class_series"
    assert event.actor_user_id == ctx.owner.id
    assert event.details == {
        "series_id": series["id"],
        "new_series_id": new_id,
        "mode": "following",
        "from_date": d.isoformat(),
        "moved": 10,
        "created": 0,
        "cancelled": 10,
        "exceptions_reset": 0,
    }
