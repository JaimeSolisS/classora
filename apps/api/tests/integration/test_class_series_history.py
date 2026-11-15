"""History is never rewritten (#54 E3; spec W1.5; ADR-0005 D4).

One accepted Monday/Wednesday series per case, then "now" moves to Monday
16:30 of week 2, so the series has:

- a ``completed`` row (Monday, week 1);
- a past ``confirmed`` row (Wednesday, week 1);
- a started row (Monday, week 2, 16:00);
- a future ``cancelled`` row on a date the new rule drops (Wednesday,
  week 4);
- a future ``skipped_closure`` row on a date the new rule drops
  (Wednesday, week 5).

Each write (an edit from week 3, an ``all`` edit and a cancellation) leaves
those rows identical column by column (``version`` and ``updated_at``
included), and writes no ``update`` audit event about them.
"""

from typing import Any

import pytest

from tests.integration.auth_support import auth_client
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_edit_support import (
    OK,
    Ctx,
    add_closure,
    cancel_row,
    cancel_series,
    edit_body,
    edit_series,
    make_ctx,
    make_series,
    on_day,
    rows_of,
    series_row,
    set_row,
    snapshot,
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def history(ctx: Ctx) -> tuple[dict[str, Any], list[Any]]:
    add_closure(ctx.url, ctx.tl.week(5, 3))
    series = make_series(ctx, tutor=True, accept=True)
    rows = rows_of(ctx.url, series["id"])
    completed = on_day(rows, ctx, ctx.tl.week(1))
    set_row(ctx.url, completed.id, state="completed")
    cancelled = on_day(rows, ctx, ctx.tl.week(4, 3))
    cancel_row(ctx.url, cancelled.id)
    past = on_day(rows, ctx, ctx.tl.week(1, 3))
    started = on_day(rows, ctx, ctx.tl.week(2))
    skipped = on_day(rows, ctx, ctx.tl.week(5, 3))
    assert skipped.state == "skipped_closure"
    return series, [completed.id, past.id, started.id, cancelled.id, skipped.id]


def update_events(ctx: Ctx, ids: list[Any]) -> int:
    return sum(
        len(events(ctx.url, record_type="class_sessions", record_id=i, action="update"))
        for i in ids
    )


@pytest.mark.parametrize("write", ["following", "all", "cancel"])
def test_past_started_completed_and_cancelled_rows_are_never_rewritten(
    module_seeded_url, client, write
):
    ctx = make_ctx(module_seeded_url, client)
    series, ids = history(ctx)
    now = ctx.tl.at(ctx.tl.week(2), "16:30")
    before = {
        i: row
        for i, row in snapshot(rows_of(ctx.url, series["id"])).items()
        if i in ids
    }
    assert len(before) == 5
    updates = update_events(ctx, ids)
    version = series_row(ctx.url, series["id"]).version

    if write == "cancel":
        response = cancel_series(
            ctx,
            series["id"],
            now,
            {
                "mode": "following",
                "from_date": ctx.tl.week(3).isoformat(),
                "reason": "Fin del ciclo",
                "version": version,
            },
        )
    else:
        response = edit_series(
            ctx,
            series["id"],
            now,
            edit_body(
                mode=write,
                from_date=ctx.tl.week(3) if write == "following" else None,
                version=version,
                start_time="17:00",
                weekdays=[1],
                until=ctx.tl.week(12, 3),
            ),
        )

    assert code_of(response) == OK, response.text
    after = {
        i: row
        for i, row in snapshot(rows_of(ctx.url, series["id"])).items()
        if i in ids
    }
    assert after == before
    assert update_events(ctx, ids) == updates
