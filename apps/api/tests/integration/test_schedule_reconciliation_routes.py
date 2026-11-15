"""The cancellation billing rule routes (#99 B, E2) and the after-commit
defers of the reconciliation job (#99 D9), with real logins through the real
app.

The rules: the seeded version, the overview, ``POST`` (``409
version_conflict``, ``422 invalid_effective_from``, ``422`` bodies),
``DELETE`` (upcoming ``204``, in force ``409``, unknown ``404``), the audit
events and the access of each role. Each test reads the latest version it
builds on.

The defers: the session cancel, the session ``PATCH``, the series edit and
cancellation, the change-request approval and the billing settings ``PATCH``
each queue one ``billing:reconcile_session_charges`` job (counted in the
module database's ``procrastinate_jobs``; no worker runs here); a refused
write queues nothing. The approval's committed outcome is what the job
classifies (C2).
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.modules.billing import service as billing
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    TUTOR,
)
from tests.integration import schedule_reconciliation_support as sr
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import get, patch, post
from tests.integration.change_request_support import (
    approve,
    create,
    created,
    make_scene,
    ok,
)
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_edit_support import (
    cancel_series,
    edit_body,
    edit_series,
    make_ctx,
    make_series,
    patch_session,
    series_row,
)
from tests.integration.test_session_cancellation import cancel, one_off

RULES = "/api/v1/billing/cancellation-rules"
OUTCOMES = {
    "excused_outcome": "credit",
    "on_time_outcome": "credit",
    "late_outcome": "charge",
    "no_show_outcome": "charge",
}
ITEM_KEYS = {"id", "effective_from", "created_by_user_id", "created_at", *OUTCOMES}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def finance(client: TestClient, module_seeded_url: str) -> tuple[SetCookie, Any]:
    user = create_user(module_seeded_url, roles=(FINANCE_STAFF,))
    return sign_in(client, user.username), user


@pytest.fixture(autouse=True)
def no_queued_job(module_seeded_url: str) -> None:
    run_sql(
        module_seeded_url,
        "DELETE FROM procrastinate_jobs WHERE task_name = :t",
        {"t": jobs.SCHEDULE_RECONCILIATION_TASK},
    )


def queued(url: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT queueing_lock, args FROM procrastinate_jobs WHERE task_name = :t",
        {"t": jobs.SCHEDULE_RECONCILIATION_TASK},
    )


def assert_one_queued(url: str) -> None:
    [job] = queued(url)
    assert job.queueing_lock == jobs.SCHEDULE_RECONCILIATION_QUEUEING_LOCK
    assert set(job.args) == {"timestamp"}


def overview(client: TestClient, cookie: SetCookie) -> dict[str, Any]:
    response = get(client, cookie, RULES)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def latest(body: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = (body["upcoming"] or [body["current"]])[-1]
    return found


def later_than(body: dict[str, Any], days: int = 1) -> str:
    base = max(
        datetime.fromisoformat(latest(body)["effective_from"]), datetime.now(UTC)
    )
    return (base + timedelta(days=days)).isoformat()


def publish(client: TestClient, cookie: SetCookie, **changes: Any) -> Any:
    body = overview(client, cookie)
    payload = {
        "based_on_id": latest(body)["id"],
        "effective_from": later_than(body),
        **OUTCOMES,
        **changes,
    }
    return post(client, cookie, RULES, payload)


# --- Rules (B, E2) -------------------------------------------------------------------


def test_the_seeded_version_and_the_overview_shape(client, finance, module_seeded_url):
    cookie, _ = finance
    body = overview(client, cookie)

    assert set(body) == {"current", "upcoming", "history"}
    assert set(body["current"]) == ITEM_KEYS
    [seed] = run_sql(
        module_seeded_url,
        "SELECT * FROM cancellation_billing_rules ORDER BY effective_from LIMIT 1",
    )
    assert seed.effective_from == datetime(2000, 1, 1, tzinfo=UTC)
    shown = [body["current"], *body["history"]]
    [item] = [v for v in shown if v["id"] == str(seed.id)]
    assert {k: item[k] for k in OUTCOMES} == OUTCOMES
    assert item["created_by_user_id"] is None


def test_publishing_and_deleting_an_upcoming_version(
    client, finance, module_seeded_url
):
    cookie, user = finance
    url = module_seeded_url

    response = publish(client, cookie, late_outcome="credit")

    assert response.status_code == 201, response.text
    item = response.json()
    assert set(item) == ITEM_KEYS
    assert (item["late_outcome"], item["created_by_user_id"]) == (
        "credit",
        str(user.id),
    )
    assert latest(overview(client, cookie))["id"] == item["id"]
    [event] = events(
        url, record_type="cancellation_billing_rules", record_id=uuid.UUID(item["id"])
    )
    assert (event.action, event.actor_user_id) == ("create", user.id)

    removed = delete(client, cookie, f"{RULES}/{item['id']}")

    assert removed.status_code == 204, removed.text
    assert all(v["id"] != item["id"] for v in overview(client, cookie)["upcoming"])
    actions = [
        e.action
        for e in events(
            url,
            record_type="cancellation_billing_rules",
            record_id=uuid.UUID(item["id"]),
        )
    ]
    assert actions == ["create", "delete"]


def test_a_stale_base_is_a_version_conflict(client, finance):
    cookie, _ = finance
    body = overview(client, cookie)
    assert publish(client, cookie).status_code == 201
    stale = post(
        client,
        cookie,
        RULES,
        {
            "based_on_id": latest(body)["id"],
            "effective_from": later_than(body, 30),
            **OUTCOMES,
        },
    )

    assert code_of(stale) == (409, "version_conflict")


@pytest.mark.parametrize("when", ["past", "not_later"])
def test_an_invalid_effective_from(client, finance, when):
    cookie, _ = finance
    if when == "not_later":
        assert publish(client, cookie).status_code == 201
    body = overview(client, cookie)
    effective = (
        (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        if when == "past"
        else latest(body)["effective_from"]
    )
    response = post(
        client,
        cookie,
        RULES,
        {"based_on_id": latest(body)["id"], "effective_from": effective, **OUTCOMES},
    )

    assert code_of(response) == (422, "invalid_effective_from")


@pytest.mark.parametrize(
    "changes",
    [
        {"late_outcome": "refund"},
        {"surprise": True},
        {"effective_from": "2099-01-01T00:00"},
    ],
)
def test_a_bad_body_is_422(client, finance, changes):
    cookie, _ = finance

    assert code_of(publish(client, cookie, **changes)) == (422, "validation_error")


def test_a_version_in_force_cant_be_deleted_and_unknown_is_404(client, finance):
    cookie, _ = finance
    current = overview(client, cookie)["current"]

    assert code_of(delete(client, cookie, f"{RULES}/{current['id']}")) == (
        409,
        "policy_version_in_effect",
    )
    assert code_of(delete(client, cookie, f"{RULES}/{uuid.uuid4()}")) == (
        404,
        "not_found",
    )


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, TUTOR])
def test_other_roles_are_refused(client, module_seeded_url, role):
    cookie = sign_in(client, create_user(module_seeded_url, roles=(role,)).username)

    assert code_of(get(client, cookie, RULES)) == (403, "permission_denied")
    assert code_of(publish_as(client, cookie)) == (403, "permission_denied")


def publish_as(client: TestClient, cookie: SetCookie) -> Any:
    return post(client, cookie, RULES, {"based_on_id": str(uuid.uuid4()), **OUTCOMES})


def test_anonymous_is_401(client):
    client.cookies.clear()

    assert client.get(RULES).status_code == 401


def test_the_rule_at_an_instant_crosses_a_version_boundary(module_seeded_url):
    url = module_seeded_url
    boundary = datetime.now(UTC) - timedelta(days=400)
    newer = sr.add_rule(url, boundary, no_show_outcome="credit")

    async def _at(session: AsyncSession, at: datetime) -> Any:
        return await billing.cancellation_rule_at(session, at)

    before = in_uow(url, lambda s: _at(s, boundary - timedelta(microseconds=1)))
    at = in_uow(url, lambda s: _at(s, boundary))
    assert before.id != newer
    assert (at.id, at.no_show_outcome) == (newer, "credit")
    with pytest.raises(ValueError, match="aware"):
        in_uow(url, lambda s: _at(s, datetime(2031, 1, 1)))  # noqa: DTZ001


# --- After-commit defers (D9) --------------------------------------------------------


def test_cancelling_a_session_queues_one_run(client, module_seeded_url):
    ctx = make_ctx(module_seeded_url, client)
    session = one_off(ctx, accept=True)

    refused = cancel(ctx, session["id"], session["version"] + 5)
    assert code_of(refused) == (409, "version_conflict")
    assert queued(module_seeded_url) == []

    assert code_of(cancel(ctx, session["id"], session["version"])) == (200, None)
    assert_one_queued(module_seeded_url)


def test_moving_a_session_queues_one_run(client, module_seeded_url):
    ctx = make_ctx(module_seeded_url, client)
    session = one_off(ctx, accept=True)
    start = datetime.fromisoformat(session["starts_at"])

    moved = patch_session(
        ctx,
        session["id"],
        ctx.tl.now,
        version=session["version"],
        starts_at=start,
        ends_at=start + timedelta(minutes=45),
    )

    assert code_of(moved) == (200, None), moved.text
    assert_one_queued(module_seeded_url)


def test_editing_and_cancelling_a_series_queue_one_run_each(client, module_seeded_url):
    ctx = make_ctx(module_seeded_url, client)
    series = make_series(ctx, tutor=True, accept=True)
    response = edit_series(
        ctx,
        series["id"],
        ctx.tl.now,
        edit_body(
            from_date=ctx.tl.week(3),
            version=series_row(ctx.url, series["id"]).version,
            duration_minutes=45,
        ),
    )
    assert code_of(response) == (200, None), response.text
    assert_one_queued(module_seeded_url)
    run_sql(
        module_seeded_url,
        "DELETE FROM procrastinate_jobs WHERE task_name = :t",
        {"t": jobs.SCHEDULE_RECONCILIATION_TASK},
    )
    [current] = run_sql(
        module_seeded_url,
        "SELECT id FROM class_series WHERE id = :id OR split_from_series_id = :id"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        {"id": series["id"]},
    )

    cancelled = cancel_series(
        ctx,
        current.id,
        ctx.tl.now,
        {
            "mode": "following",
            "from_date": ctx.tl.week(5).isoformat(),
            "reason": "Fin del ciclo",
            "version": series_row(ctx.url, current.id).version,
        },
    )

    assert code_of(cancelled) == (200, None), cancelled.text
    assert_one_queued(module_seeded_url)


def test_approving_a_cancellation_queues_a_run_and_the_job_reads_the_outcome(
    client, module_seeded_url
):
    url = module_seeded_url
    scene = make_scene(url, client)
    request = created(create(scene, scene.guardian, note="Viaje"))

    ok(approve(scene, request["id"]))

    assert_one_queued(url)
    session_id = uuid.UUID(scene.session["id"])
    assert sr.one(url, session_id, scene.ctx.student) == ["cancellation_no_charge"]
    row = sr.decision(url, session_id, scene.ctx.student)
    assert (row.initiated_by, row.change_request_id) == (
        "family",
        uuid.UUID(request["id"]),
    )
    assert (row.classification, row.outcome, row.result) == (
        "on_time",
        "credit",
        "no_charge",
    )
    [stored] = run_sql(
        url,
        "SELECT created_at FROM session_change_requests WHERE id = :id",
        {"id": request["id"]},
    )
    assert row.classified_at == stored.created_at


def test_the_billing_settings_patch_queues_a_run(client, finance, module_seeded_url):
    cookie, _ = finance
    response = get(client, cookie, "/api/v1/billing/settings")
    version = response.json()["version"]

    changed = patch(
        client,
        cookie,
        "/api/v1/billing/settings",
        {"version": version, "billing_starts_on": "2031-03-01"},
    )

    assert changed.status_code == 200, changed.text
    assert_one_queued(module_seeded_url)
