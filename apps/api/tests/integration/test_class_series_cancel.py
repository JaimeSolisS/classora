"""Cancelling a series from a date on: ``POST
/api/v1/class-series/{id}/cancellations`` (#54 C5, E5; ADR-0005 D2, D3)
with real logins and a frozen clock (timelines as in
:mod:`~tests.integration.series_edit_support`).
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling import tasks
from tests.integration.auth_support import auth_client
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_edit_support import (
    INVALID,
    OK,
    Ctx,
    add_closure,
    cancel_series,
    lift_closure,
    make_ctx,
    make_series,
    notes,
    offers_of,
    on_day,
    read_series_at,
    row_by_id,
    rows_of,
    run_job,
    series_row,
    with_family,
)

CANCELLED = "scheduling.series_cancelled"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def ctx(module_seeded_url: str, client: Any) -> Ctx:
    return make_ctx(module_seeded_url, client)


def body(ctx: Ctx, series: dict[str, Any], **changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "following",
        "from_date": ctx.tl.week(3).isoformat(),
        "reason": "  Fin del ciclo escolar  ",
        "version": series_row(ctx.url, series["id"]).version,
    }
    return payload | changes


def cancel(ctx: Ctx, series: dict[str, Any], at: Any = None, **changes: Any) -> Any:
    return cancel_series(
        ctx, series["id"], at or ctx.tl.now, body(ctx, series, **changes)
    )


def local_day(ctx: Ctx, row: Any) -> Any:
    return row.original_start.astimezone(ctx.tl.now.tzinfo).date()


def selected(url: str, at: Any) -> list[uuid.UUID]:
    async def _ids(session: AsyncSession) -> list[uuid.UUID]:
        return await tasks.series_to_materialize(session, at)

    return in_uow(url, _ids)


def test_following_cancels_the_rows_keeps_skipped_ones_and_ends_the_series(ctx):
    add_closure(ctx.url, ctx.tl.week(5))
    # Open-ended: the job would extend it, until the cancellation ends it.
    series = make_series(ctx, count=None)
    d = ctx.tl.week(3)
    count_before = len(rows_of(ctx.url, series["id"]))

    response = cancel(ctx, series)

    assert code_of(response) == OK, response.text
    data = response.json()
    assert data["summary"] == {"cancelled": 19, "skipped_closure_kept": 1}
    rows = rows_of(ctx.url, series["id"])
    later = [r for r in rows if local_day(ctx, r) >= d]
    assert {(r.state, r.cancellation_reason) for r in later} == {
        ("cancelled", "Fin del ciclo escolar"),
        ("skipped_closure", None),
    }
    assert {r.state for r in rows if local_day(ctx, r) < d} == {"unassigned"}
    stored = series_row(ctx.url, series["id"])
    end = d - timedelta(days=1)
    assert (stored.until_on, stored.last_occurrence_on, stored.count) == (
        end,
        end,
        None,
    )
    assert data["series"]["last_occurrence_on"] == end.isoformat()
    assert data["series"]["ended"] is False
    assert read_series_at(ctx, series["id"], ctx.tl.at(d, "08:00"))["ended"] is True

    later_now = ctx.tl.now + timedelta(weeks=4)
    run_job(ctx.url, later_now)

    assert len(rows_of(ctx.url, series["id"])) == count_before
    assert uuid.UUID(series["id"]) not in selected(ctx.url, ctx.tl.at(d, "08:00"))
    [event] = events(
        ctx.url,
        action="scheduling.series_cancelled",
        record_id=uuid.UUID(series["id"]),
    )
    assert event.details == {
        "series_id": series["id"],
        "from_date": d.isoformat(),
        "cancelled": 19,
    }


def test_all_before_anything_started_ends_before_the_start(ctx):
    series = make_series(ctx)

    response = cancel(ctx, series, mode="all", from_date=None)

    assert code_of(response) == OK, response.text
    stored = series_row(ctx.url, series["id"])
    assert stored.until_on == ctx.tl.monday - timedelta(days=1)
    assert stored.until_on < stored.starts_on
    assert {r.state for r in rows_of(ctx.url, series["id"])} == {"cancelled"}
    assert uuid.UUID(series["id"]) not in selected(
        ctx.url, ctx.tl.at(ctx.tl.monday, "08:00")
    )
    again = cancel(ctx, series, mode="all", from_date=None)
    assert code_of(again) == (409, "series_ended")


def test_a_repeated_cancellation_is_series_ended(ctx):
    series = make_series(ctx)
    assert code_of(cancel(ctx, series)) == OK

    again = cancel(ctx, series)

    assert code_of(again) == (409, "series_ended")


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        (None, "reason_required"),
        ("   ", "reason_required"),
        ("x" * 1001, "reason_too_long"),
    ],
)
def test_the_reason_is_required_and_short(ctx, reason, code):
    series = make_series(ctx)
    payload = body(ctx, series, reason=reason)
    if reason is None:
        del payload["reason"]

    response = cancel_series(ctx, series["id"], ctx.tl.now, payload)

    assert code_of(response) == INVALID, response.text
    assert [(e["path"][-1], e["code"]) for e in response.json()["errors"]] == [
        ("reason", code)
    ]


def test_a_pending_series_offer_is_withdrawn(ctx):
    student_user, guardian_user = with_family(ctx.url, ctx.student)
    series = make_series(ctx, tutor=True)
    rows = rows_of(ctx.url, series["id"])
    first = on_day(rows, ctx, ctx.tl.week(1))
    [offer] = offers_of(ctx.url, first.id)

    response = cancel(ctx, series)

    assert code_of(response) == OK, response.text
    [offer] = offers_of(ctx.url, first.id)
    assert offer.status == "withdrawn"
    stored = series_row(ctx.url, series["id"])
    assert (stored.tutor_user_id, stored.assignment_status) == (None, None)
    rows = rows_of(ctx.url, series["id"])
    d = ctx.tl.week(3)
    assert {r.state for r in rows if local_day(ctx, r) < d} == {"unassigned"}
    assert {r.state for r in rows if local_day(ctx, r) >= d} == {"cancelled"}
    # Offered rows: the tutor only.
    found = notes(ctx.url, CANCELLED, "series_id", series["id"])
    assert [n.recipient_user_id for n in found] == [ctx.tutor.id]
    assert found[0].params == {"series_id": series["id"], "from_date": d.isoformat()}


def test_confirmed_rows_tell_the_tutor_and_the_family_once(ctx):
    student_user, guardian_user = with_family(ctx.url, ctx.student)
    series = make_series(ctx, tutor=True, accept=True)

    response = cancel(ctx, series)

    assert code_of(response) == OK, response.text
    found = notes(ctx.url, CANCELLED, "series_id", series["id"])
    assert sorted(n.recipient_user_id for n in found) == sorted(
        [ctx.tutor.id, student_user, guardian_user]
    )


def test_a_lifted_closure_never_restores_a_row_after_the_cancellation(ctx):
    # #54 QA D1 (a): the kept skipped row is after the series' new end.
    closure = add_closure(ctx.url, ctx.tl.week(5, 3))
    series = make_series(ctx, tutor=True, accept=True)
    skipped = on_day(rows_of(ctx.url, series["id"]), ctx, ctx.tl.week(5, 3))
    assert code_of(cancel(ctx, series)) == OK
    lift_closure(ctx.url, closure)

    run_job(ctx.url, ctx.tl.now)

    after = row_by_id(ctx.url, skipped.id)
    assert (after.state, after.version) == ("skipped_closure", skipped.version)
