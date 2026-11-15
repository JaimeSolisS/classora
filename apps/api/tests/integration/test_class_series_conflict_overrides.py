"""No carry-forward of series overrides, and overriding one ``conflict``
occurrence: ``POST /api/v1/class-sessions/{id}/conflict-overrides`` (#236
G1-G3, I3) with real logins and a frozen clock.

"Now" is the Sunday 12:00 before the first Monday (Mexico City,
:mod:`~tests.integration.series_edit_support`); the seeded hours close on
Sundays, so a Sunday 10:00 occurrence has one soft conflict,
``outside_operating_hours``.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service as identity
from app.modules.identity.permissions import ADMINISTRATOR
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_edit_support import (
    CONFLICT,
    INVALID,
    NOT_FOUND,
    OK,
    SERIES,
    SESSIONS,
    STALE,
    Ctx,
    as_user,
    conflict_requests,
    make_conflict,
    make_ctx,
    make_series,
    offers_of,
    owner_call,
    row_by_id,
    rows_of,
    run_job,
    snapshot,
)
from tests.integration.series_support import iso_z
from tests.integration.test_class_session_create import CREATED, existing_session

HOURS = "outside_operating_hours"
REASON = "La familia confirmó los domingos."
FORBIDDEN = (403, "permission_denied")
APPROVALS = "/api/v1/approval-requests"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def ctx(module_seeded_url: str, client: Any) -> Ctx:
    return make_ctx(module_seeded_url, client)


def sunday_series(ctx: Ctx, *, count: int | None = 3) -> dict[str, Any]:
    """A Sunday 10:00 series without a tutor, created at ``tl.now`` with
    every horizon occurrence overridden."""
    body = ctx.body(start_time="10:00", weekdays=[7])
    body["recurrence"]["count"] = count
    body["starts_on"] = ctx.tl.week(1, 7).isoformat()

    def create(payload: Any) -> Response:
        return owner_call(
            ctx, ctx.tl.now, lambda cookie: post(ctx.client, cookie, SERIES, payload)
        )

    refused = create(body)
    assert code_of(refused) == CONFLICT, refused.text
    pairs = [
        {"occurrence_start": c["details"]["occurrence_start"], "code": c["code"]}
        for c in refused.json()["conflicts"]
    ]
    created = create(body | {"override": {"pairs": pairs, "reason": REASON}})
    assert code_of(created) == CREATED, created.text
    found: dict[str, Any] = created.json()
    return found


def override_body(
    version: int, codes: list[str] | None = None, reason: Any = REASON
) -> dict[str, Any]:
    return {
        "version": version,
        "override": {"codes": [HOURS] if codes is None else codes, "reason": reason},
    }


def conflict_override(
    ctx: Ctx, row_id: Any, at: datetime, body: dict[str, Any]
) -> Response:
    return owner_call(
        ctx,
        at,
        lambda cookie: post(
            ctx.client, cookie, f"{SESSIONS}/{row_id}/conflict-overrides", body
        ),
    )


def overrides_on(url: str, row_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM class_session_overrides WHERE class_session_id = :id"
        " ORDER BY created_at, id",
        {"id": str(row_id)},
    )


def all_overrides(url: str, series_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT o.* FROM class_session_overrides o"
        " JOIN class_sessions s ON s.id = o.class_session_id"
        " WHERE s.series_id = :id",
        {"id": str(series_id)},
    )


def week2_conflict(ctx: Ctx, codes: list[str] | None = None) -> tuple[Any, Any]:
    """A Sunday series and its week-2 row put into ``conflict`` with an open
    inbox request; returns ``(row, request id)``."""
    series = sunday_series(ctx)
    row = rows_of(ctx.url, series["id"])[1]
    make_conflict(ctx.url, row.id, codes or [HOURS])
    [request] = conflict_requests(ctx.url, row.id)
    return row_by_id(ctx.url, row.id), request.id


# --- G1: no carry-forward -----------------------------------------------------------


def test_the_nightly_job_never_reuses_earlier_overrides(ctx):
    series = sunday_series(ctx, count=None)
    before_rows = snapshot(rows_of(ctx.url, series["id"]))
    before_overrides = snapshot(all_overrides(ctx.url, series["id"]))
    assert len(before_rows) == len(before_overrides) == 12
    later = ctx.tl.now + timedelta(weeks=1)

    run_job(ctx.url, later)

    rows = rows_of(ctx.url, series["id"])
    [new] = [r for r in rows if r.id not in before_rows]
    assert new.starts_at == ctx.tl.at(ctx.tl.week(13, 7), "10:00")
    assert new.state == "conflict"
    assert list(new.conflict_codes) == [HOURS]
    assert [r.status for r in conflict_requests(ctx.url, new.id)] == ["pending"]
    assert snapshot(all_overrides(ctx.url, series["id"])) == before_overrides
    assert snapshot([r for r in rows if r.id in before_rows]) == before_rows

    run_job(ctx.url, later)

    assert len(rows_of(ctx.url, series["id"])) == 13
    assert len(conflict_requests(ctx.url, new.id)) == 1

    # G3: the conflict row is overridden on its own and scheduled.
    response = conflict_override(ctx, new.id, later, override_body(new.version))

    assert code_of(response) == OK, response.text
    detail = response.json()
    assert detail["state"] == "unassigned"
    assert detail["conflict_codes"] == [HOURS]
    assert [o["codes"] for o in detail["overrides"]] == [[HOURS]]
    [stored] = overrides_on(ctx.url, new.id)
    assert stored.class_series_id == uuid.UUID(series["id"])
    assert stored.reason == REASON
    assert stored.actor_user_id == ctx.owner.id
    assert stored.conflicts == [
        {
            "code": HOURS,
            "details": {
                "date": ctx.tl.week(13, 7).isoformat(),
                "opens_at": None,
                "closes_at": None,
            },
        }
    ]
    [event] = events(ctx.url, record_id=new.id, action="scheduling.session_override")
    assert event.details["override_id"] == str(stored.id)
    assert [r.status for r in conflict_requests(ctx.url, new.id)] == ["cancelled"]


# --- G3 refusals -------------------------------------------------------------------


def test_a_hard_conflict_still_present_is_409(ctx):
    row, request_id = week2_conflict(ctx)
    existing_session(
        ctx.url,
        state="unassigned",
        tutor=False,
        student_id=ctx.student,
        starts_at=ctx.tl.at(ctx.tl.week(2, 7), "10:30"),
    )

    response = conflict_override(ctx, row.id, ctx.tl.now, override_body(row.version))

    assert code_of(response) == CONFLICT
    found = response.json()["conflicts"]
    assert [c["code"] for c in found] == ["student_overlap", HOURS]
    assert all("occurrence_start" not in c["details"] for c in found)
    assert row_by_id(ctx.url, row.id).state == "conflict"
    assert len(overrides_on(ctx.url, row.id)) == 1  # the create's only
    assert [r.status for r in conflict_requests(ctx.url, row.id)] == ["pending"]
    del request_id


def test_a_reference_code_is_422_and_never_overridable(ctx):
    series = make_series(ctx, tutor=True, accept=True)
    row = rows_of(ctx.url, series["id"])[2]
    make_conflict(ctx.url, row.id, ["tutor_inactive"])
    run_sql(
        ctx.url,
        "UPDATE users SET state = 'suspended' WHERE id = :id",
        {"id": str(ctx.tutor.id)},
    )
    row = row_by_id(ctx.url, row.id)

    response = conflict_override(ctx, row.id, ctx.tl.now, override_body(row.version))

    assert code_of(response) == INVALID
    assert [e["code"] for e in response.json()["errors"]] == ["tutor_inactive"]
    assert row_by_id(ctx.url, row.id).state == "conflict"


def test_state_start_version_and_id_refusals(ctx):
    row, _ = week2_conflict(ctx)
    clean = rows_of(ctx.url, row.series_id)[0]
    started_at = ctx.tl.at(ctx.tl.week(2, 7), "10:15")

    not_conflict = conflict_override(
        ctx, clean.id, ctx.tl.now, override_body(clean.version)
    )
    stale = conflict_override(ctx, row.id, ctx.tl.now, override_body(row.version - 1))
    started = conflict_override(ctx, row.id, started_at, override_body(row.version))
    unknown = conflict_override(ctx, uuid.uuid4(), ctx.tl.now, override_body(1))
    no_reason = conflict_override(
        ctx, row.id, ctx.tl.now, override_body(row.version, reason="  ")
    )
    hard_code = conflict_override(
        ctx, row.id, ctx.tl.now, override_body(row.version, codes=["tutor_overlap"])
    )
    missing = conflict_override(ctx, row.id, ctx.tl.now, {"version": row.version})

    assert code_of(not_conflict) == (409, "session_not_in_conflict")
    assert not_conflict.json()["details"] == {"state": "unassigned"}
    assert code_of(stale) == STALE
    assert code_of(started) == (409, "session_not_editable")
    assert started.json()["details"] == {"reason": "started"}
    assert code_of(unknown) == NOT_FOUND
    assert [(e["path"], e["code"]) for e in no_reason.json()["errors"]] == [
        (["body", "override", "reason"], "reason_required")
    ]
    assert [(e["path"], e["code"]) for e in hard_code.json()["errors"]] == [
        (["body", "override", "codes", 0], "code_not_overridable")
    ]
    assert code_of(missing) == INVALID
    assert row_by_id(ctx.url, row.id).state == "conflict"
    assert [r.status for r in conflict_requests(ctx.url, row.id)] == ["pending"]


def test_an_administrator_needs_the_delegation_after_the_404(ctx):
    row, _ = week2_conflict(ctx)
    admin = create_user(ctx.url, roles=(ADMINISTRATOR,))

    def as_admin(row_id: Any) -> Response:
        cookie = as_user(ctx, admin, ctx.tl.now)
        with frozen_clock(ctx.tl.now):
            return post(
                ctx.client,
                cookie,
                f"{SESSIONS}/{row_id}/conflict-overrides",
                override_body(row.version),
            )

    denied = as_admin(row.id)
    unknown = as_admin(uuid.uuid4())

    async def _grant(session: AsyncSession) -> None:
        await identity.grant_delegation(
            session,
            actor_id=ctx.owner.id,
            user_id=admin.id,
            permission="schedule.override",
        )

    in_uow(ctx.url, _grant, actor_id=ctx.owner.id)
    allowed = as_admin(row.id)

    assert code_of(denied) == FORBIDDEN
    assert code_of(unknown) == NOT_FOUND
    assert code_of(allowed) == OK, allowed.text
    [_, stored] = overrides_on(ctx.url, row.id)
    assert stored.actor_user_id == admin.id


def test_codes_that_no_longer_occur_schedule_it_without_a_row(ctx):
    series = make_series(ctx)
    row = rows_of(ctx.url, series["id"])[3]
    make_conflict(ctx.url, row.id, ["tutor_unavailable"])
    row = row_by_id(ctx.url, row.id)

    response = conflict_override(
        ctx, row.id, ctx.tl.now, override_body(row.version, codes=["tutor_unavailable"])
    )

    assert code_of(response) == OK, response.text
    assert response.json()["state"] == "unassigned"
    assert response.json()["overrides"] == []
    assert overrides_on(ctx.url, row.id) == []
    assert events(ctx.url, record_id=row.id, action="scheduling.session_override") == []
    assert [r.status for r in conflict_requests(ctx.url, row.id)] == ["cancelled"]


def test_a_row_of_a_series_offered_to_a_tutor_joins_the_offer(ctx):
    body = ctx.body(tutor=True, start_time="10:00", weekdays=[7])
    body["recurrence"]["count"] = None
    body["starts_on"] = ctx.tl.week(1, 7).isoformat()

    def create(payload: Any) -> Response:
        return owner_call(
            ctx, ctx.tl.now, lambda cookie: post(ctx.client, cookie, SERIES, payload)
        )

    pairs = [
        {
            "occurrence_start": iso_z(ctx.tl.at(ctx.tl.week(n, 7), "10:00")),
            "code": HOURS,
        }
        for n in range(1, 13)
    ]
    created = create(body | {"override": {"pairs": pairs, "reason": REASON}})
    assert code_of(created) == CREATED, created.text
    offer_id = created.json()["assignment"]["offer_id"]
    later = ctx.tl.now + timedelta(weeks=1)
    run_job(ctx.url, later)
    new = rows_of(ctx.url, created.json()["id"])[-1]
    assert (new.state, list(new.conflict_codes)) == ("conflict", [HOURS])

    response = conflict_override(ctx, new.id, later, override_body(new.version))

    assert code_of(response) == OK, response.text
    assert response.json()["state"] == "offered"
    assert response.json()["tutor"]["id"] == str(ctx.tutor.id)
    assert [o.id for o in offers_of(ctx.url, new.id)] == [uuid.UUID(offer_id)]


# --- G2: the inbox item is unchanged --------------------------------------------


def test_approving_the_inbox_item_still_only_rechecks(ctx):
    row, request_id = week2_conflict(ctx)

    response = owner_call(
        ctx,
        ctx.tl.now,
        lambda cookie: post(
            ctx.client, cookie, f"{APPROVALS}/{request_id}/approve", {}
        ),
    )

    assert code_of(response) == CONFLICT
    assert [c["code"] for c in response.json()["conflicts"]] == [HOURS]
    assert row_by_id(ctx.url, row.id).state == "conflict"
    assert [r.status for r in conflict_requests(ctx.url, row.id)] == ["pending"]
