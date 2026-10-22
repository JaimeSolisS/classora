"""Overriding the soft conflicts of a series edit (#236 F, I2) with real
logins and a frozen clock.

"Now" is the Sunday 12:00 before the first Monday (Mexico City,
:mod:`~tests.integration.series_edit_support`); the seeded hours close on
Sundays, so every Sunday occurrence has one soft conflict,
``outside_operating_hours``.
"""

import uuid
from typing import Any

import pytest
from httpx import Response

from app.modules.identity.permissions import ADMINISTRATOR
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_edit_support import (
    CONFLICT,
    INVALID,
    OK,
    SERIES,
    Ctx,
    as_user,
    cancel_series,
    edit_body,
    edit_series,
    make_ctx,
    make_series,
    owner_call,
    rows_of,
    series_row,
    snapshot,
)
from tests.integration.series_support import iso_z
from tests.integration.test_class_session_create import CREATED, existing_session

HOURS = "outside_operating_hours"
REASON = "Sesiones de fin de semana acordadas."
FORBIDDEN = (403, "permission_denied")


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def ctx(module_seeded_url: str, client: Any) -> Ctx:
    return make_ctx(module_seeded_url, client)


def override(pairs: list[tuple[str, str]], reason: str = REASON) -> dict[str, Any]:
    return {
        "pairs": [{"occurrence_start": s, "code": c} for s, c in pairs],
        "reason": reason,
    }


def soft_pairs(response: Response) -> list[tuple[str, str]]:
    assert code_of(response) == CONFLICT, response.text
    return [
        (c["details"]["occurrence_start"], c["code"])
        for c in response.json()["conflicts"]
        if c["severity"] == "soft"
    ]


def edit(ctx: Ctx, series_id: Any, **body: Any) -> Response:
    body.setdefault("version", series_row(ctx.url, series_id).version)
    return edit_series(ctx, series_id, ctx.tl.now, edit_body(**body))


def overrides_of(url: str, series_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM class_session_overrides WHERE class_series_id = :id"
        " ORDER BY created_at, id",
        {"id": str(series_id)},
    )


def sunday_series(ctx: Ctx) -> dict[str, Any]:
    """A Sunday 10:00 series of 3 created with overrides on all three."""
    body = ctx.body(start_time="10:00", weekdays=[7], count=3)
    body["starts_on"] = ctx.tl.week(1, 7).isoformat()

    def create(payload: Any) -> Response:
        return owner_call(
            ctx, ctx.tl.now, lambda cookie: post(ctx.client, cookie, SERIES, payload)
        )

    pairs = soft_pairs(create(body))
    created = create(body | {"override": override(pairs)})
    assert code_of(created) == CREATED, created.text
    found: dict[str, Any] = created.json()
    return found


def sunday_rule(ctx: Ctx, **changes: Any) -> dict[str, Any]:
    """The full rule of :func:`sunday_series` (for an in-place edit)."""
    return {
        "mode": "following",
        "from_date": ctx.tl.week(1, 7),
        "start_time": "10:00",
        "weekdays": [7],
        "count": 3,
    } | changes


# --- A split to Sundays -----------------------------------------------------------


def test_moving_to_sundays_needs_a_pair_per_new_occurrence(ctx):
    series = make_series(ctx)
    d = ctx.tl.week(3)
    until = ctx.tl.week(12, 7)
    rule = {
        "from_date": d,
        "start_time": "10:00",
        "weekdays": [7],
        "until": until,
    }

    refused = edit(ctx, series["id"], **rule)
    pairs = soft_pairs(refused)
    data = edit(ctx, series["id"], **rule, override=override(pairs))

    assert pairs == [
        (iso_z(ctx.tl.at(ctx.tl.week(n, 7), "10:00")), HOURS) for n in range(3, 13)
    ]
    assert code_of(data) == OK, data.text
    result = data.json()
    assert result["split"] is True
    new_id = result["series"]["id"]
    assert new_id != series["id"]
    stored = overrides_of(ctx.url, new_id)
    new_rows = rows_of(ctx.url, new_id)
    sundays = [r for r in new_rows if r.state == "unassigned"]
    assert len(stored) == len(sundays) == 10
    assert {o.class_session_id for o in stored} == {r.id for r in sundays}
    assert {o.class_series_id for o in stored} == {uuid.UUID(new_id)}
    assert overrides_of(ctx.url, series["id"]) == []
    [changed] = events(
        ctx.url, record_id=uuid.UUID(series["id"]), action="scheduling.series_changed"
    )
    [event] = events(
        ctx.url, record_id=uuid.UUID(new_id), action="scheduling.series_override"
    )
    assert event.request_id == changed.request_id
    assert event.details["write"] == "edit"
    assert event.details["occurrence_count"] == 10
    assert [o["occurrence_start"] for o in event.details["occurrences"]] == [
        p[0] for p in pairs
    ]
    assert all(o["overridden"] for o in result["series"]["occurrences"])


# --- F3: an edit needs its own override --------------------------------------------


def test_an_overridden_row_rechecked_by_an_edit_needs_a_new_pair(ctx):
    series = sunday_series(ctx)
    old = snapshot(overrides_of(ctx.url, series["id"]))
    assert len(old) == 3

    refused = edit(ctx, series["id"], **sunday_rule(ctx), classroom_id=ctx.room)
    pairs = soft_pairs(refused)
    done = edit(
        ctx,
        series["id"],
        **sunday_rule(ctx),
        classroom_id=ctx.room,
        override=override(pairs, "Ahora en el aula grande."),
    )

    assert pairs == [
        (iso_z(ctx.tl.at(ctx.tl.week(n, 7), "10:00")), HOURS) for n in (1, 2, 3)
    ]
    assert code_of(done) == OK, done.text
    assert done.json()["split"] is False
    stored = overrides_of(ctx.url, series["id"])
    assert len(stored) == 6
    assert snapshot([o for o in stored if o.id in old]) == old
    newer = [o for o in stored if o.id not in old]
    assert {o.reason for o in newer} == {"Ahora en el aula grande."}
    assert {o.class_session_id for o in newer} == {
        o["class_session_id"] for o in old.values()
    }


# --- F2: pairs name the new instants -----------------------------------------------


def test_pairs_name_the_new_instants(ctx):
    series = sunday_series(ctx)
    old_pairs = [
        (iso_z(ctx.tl.at(ctx.tl.week(n, 7), "10:00")), HOURS) for n in (1, 2, 3)
    ]
    rule = sunday_rule(ctx, start_time="09:00")

    with_old = edit(ctx, series["id"], **rule, override=override(old_pairs))
    new_pairs = soft_pairs(with_old)
    with_new = edit(ctx, series["id"], **rule, override=override(new_pairs))

    assert new_pairs == [
        (iso_z(ctx.tl.at(ctx.tl.week(n, 7), "09:00")), HOURS) for n in (1, 2, 3)
    ]
    assert code_of(with_new) == OK, with_new.text
    assert len(overrides_of(ctx.url, series["id"])) == 6


# --- Refusals and the order ---------------------------------------------------------


def test_series_unchanged_and_cancellations_stay_422(ctx):
    series = sunday_series(ctx)
    pairs = [(iso_z(ctx.tl.at(ctx.tl.week(1, 7), "10:00")), HOURS)]

    unchanged = edit(ctx, series["id"], **sunday_rule(ctx), override=override(pairs))
    cancelled = cancel_series(
        ctx,
        series["id"],
        ctx.tl.now,
        {
            "mode": "all",
            "from_date": None,
            "reason": "Fin del curso",
            "version": series_row(ctx.url, series["id"]).version,
            "override": override(pairs),
        },
    )

    assert code_of(unchanged) == INVALID
    assert [(e["path"], e["code"]) for e in unchanged.json()["errors"]] == [
        (["body"], "series_unchanged")
    ]
    assert code_of(cancelled) == INVALID
    assert [(e["path"], e["code"]) for e in cancelled.json()["errors"]] == [
        (["body", "override"], "extra_forbidden")
    ]


def test_an_administrator_without_the_delegation_gets_403_before_404(ctx):
    admin = create_user(ctx.url, roles=(ADMINISTRATOR,))
    cookie = as_user(ctx, admin, ctx.tl.now)
    pairs = [(iso_z(ctx.tl.at(ctx.tl.week(1, 7), "10:00")), HOURS)]
    body = edit_body(
        mode="all", start_time="10:00", weekdays=[7], override=override(pairs)
    )

    with frozen_clock(ctx.tl.now):
        unknown = post(ctx.client, cookie, f"{SERIES}/{uuid.uuid4()}/edits", body)
        plain = post(
            ctx.client,
            cookie,
            f"{SERIES}/{uuid.uuid4()}/edits",
            {k: v for k, v in body.items() if k != "override"},
        )

    assert code_of(unknown) == FORBIDDEN
    assert code_of(plain) == (404, "not_found")


def test_a_hard_conflict_is_never_overridden_by_an_edit(ctx):
    series = sunday_series(ctx)
    existing_session(
        ctx.url,
        state="unassigned",
        tutor=False,
        student_id=ctx.student,
        starts_at=ctx.tl.at(ctx.tl.week(2, 7), "09:00"),
    )
    rule = sunday_rule(ctx, start_time="09:30")

    refused = edit(ctx, series["id"], **rule)
    codes = [c["code"] for c in refused.json()["conflicts"]]

    assert code_of(refused) == CONFLICT
    assert "student_overlap" in codes
    pairs = soft_pairs(refused)
    again = edit(ctx, series["id"], **rule, override=override(pairs))
    assert code_of(again) == CONFLICT
    assert again.json()["conflicts"] == refused.json()["conflicts"]
