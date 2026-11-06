"""A failed charge job retried through the route, with a real worker (#128 F1-F4).

The worker is Procrastinate's, built like the ``worker`` container's (the
production ``worker_options``, so the failure-capture middleware runs), and
processes what is queued with ``run_worker_async(wait=False)``. Its task
reads the process settings; the test points them at the module database.
Periodic schedules are cleared, so only the jobs a test defers run.
"""

import asyncio
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from procrastinate import App

from app import worker
from app.core import jobs
from app.core.config import Settings
from app.core.money import Money
from app.modules.administration import jobs as service
from app.modules.billing import balances
from app.modules.identity.permissions import ADMINISTRATOR
from app.workflows import session_charge_tasks, session_charges
from tests.integration.auth_support import signed_headers
from tests.integration.conftest import run_sql, settings_for
from tests.integration.export_support import Actor, actor
from tests.integration.lifecycle_support import in_uow
from tests.integration.session_charge_support import (
    FLOOR,
    item,
    local,
    reset,
    set_floor,
)

TASK: Final = jobs.SESSION_CHARGES_TASK
LOCK: Final = jobs.SESSION_CHARGES_QUEUEING_LOCK
JOBS: Final = "/api/v1/jobs"
FAST: Final = {"fetch_job_polling_interval": 0.2, "abort_job_polling_interval": 0.2}
PRICE: Final = Decimal("250.00")


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    from tests.integration.auth_support import auth_client

    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def admin(module_seeded_url: str, client: TestClient) -> Actor:
    return actor(module_seeded_url, client, ADMINISTRATOR)


@pytest.fixture(autouse=True)
def world(module_seeded_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No queued job, billing on, and the task reading the module database."""
    url = module_seeded_url
    run_sql(url, "DELETE FROM procrastinate_jobs")
    reset(url)
    set_floor(url, FLOOR)
    monkeypatch.setattr(session_charge_tasks, "get_settings", lambda: settings_for(url))
    yield
    run_sql(url, "DELETE FROM procrastinate_jobs")
    reset(url)


def worker_app(settings: Settings) -> App:
    app = jobs.create_job_app(jobs.worker_connector(settings), app_env="prod")
    app.periodic_registry.periodic_tasks.clear()
    return app


async def _defer(settings: Settings) -> int:
    app = worker_app(settings)
    async with app.open_async():
        job_id = await app.configure_task(
            TASK, allow_unknown=False, queueing_lock=LOCK
        ).defer_async(timestamp=int(time.time()))
    return int(job_id)


async def _work(settings: Settings) -> None:
    app = worker_app(settings)
    options = {**worker.worker_options(settings), **FAST}
    async with app.open_async():
        await app.run_worker_async(wait=False, **options)  # type: ignore[arg-type]


def defer(url: str) -> int:
    return asyncio.run(_defer(settings_for(url)))


def work(url: str) -> None:
    asyncio.run(_work(settings_for(url)))


def job_state(url: str, job_id: int) -> tuple[str, int]:
    [row] = run_sql(
        url,
        "SELECT status::text AS status, attempts FROM procrastinate_jobs WHERE id = :j",
        {"j": job_id},
    )
    return row.status, row.attempts


def failure_of(url: str, job_id: int) -> Any:
    [row] = run_sql(
        url,
        "SELECT job_id, task_name, error_class, error_code, sqlstate,"
        " failed_at::text AS failed_at FROM job_failures WHERE job_id = :j",
        {"j": job_id},
    )
    return row


def charges_for(url: str, sessions: list[uuid.UUID]) -> list[Any]:
    return run_sql(
        url,
        "SELECT class_session_id, student_id, billing_account_id, amount"
        " FROM charges WHERE kind = 'session' AND class_session_id = ANY(:s)"
        " ORDER BY class_session_id",
        {"s": sessions},
    )


def retry(client: TestClient, who: Actor, job_id: int) -> Any:
    client.cookies.clear()
    return client.post(f"{JOBS}/{job_id}/retry", headers=signed_headers(who.cookie))


def retried_events(url: str, job_id: int) -> list[Any]:
    return run_sql(
        url,
        "SELECT actor_user_id, details FROM audit_events"
        " WHERE action = 'administration.job_retried'"
        " AND (details ->> 'job_id')::bigint = :j",
        {"j": job_id},
    )


def student_name(url: str, student: uuid.UUID) -> str:
    [row] = run_sql(
        url,
        "SELECT given_names || ' ' || family_names AS name FROM students WHERE id = :s",
        {"s": student},
    )
    return str(row.name)


def three_sessions(url: str) -> list[Any]:
    """Three confirmed sessions of one student (one family, one account),
    one priced subject."""
    first = item(url, starts_at=local(FLOOR + timedelta(days=1)))
    rest = [
        item(
            url,
            starts_at=local(FLOOR + timedelta(days=day)),
            subject=first.subject,
            price=None,
            student=first.student,
            family=False,
        )
        for day in (2, 3)
    ]
    return [first, *rest]


def break_the_run_after_one_item(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    """Pages of one candidate; the second page load raises (a run-level
    error: per-item errors are caught by design)."""
    original = session_charges.candidate_page
    calls = {"n": 0}

    async def failing(session: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError(message)
        return await original(session, **{**kwargs, "limit": 1})

    monkeypatch.setattr(session_charges, "candidate_page", failing)


# --- F1, F4: fail, retry, finish ---------------------------------------------------


def test_a_failed_charge_run_retried_by_an_admin_posts_each_charge_once(
    module_seeded_url, client, admin, monkeypatch
):
    url = module_seeded_url
    items = three_sessions(url)
    sessions = [it.session for it in items]
    name = student_name(url, items[0].student)
    secret = str(uuid.uuid4())
    break_the_run_after_one_item(monkeypatch, f"Run lost for {name} ({secret})")

    job_id = defer(url)
    work(url)

    assert job_state(url, job_id) == ("failed", 1)
    failure = failure_of(url, job_id)
    assert (failure.task_name, failure.error_class) == (TASK, "RuntimeError")
    assert (failure.error_code, failure.sqlstate) == (None, None)
    assert len(charges_for(url, sessions)) == 1
    # F4: no value of the failing world in any column, nor in the list.
    stored = " ".join(str(value) for value in failure)
    for value in (name, secret, *name.split()):
        assert value not in stored
    client.cookies.clear()
    listed = client.get(JOBS, headers=signed_headers(admin.cookie))
    assert [i["id"] for i in listed.json()["items"]] == [job_id]
    assert name not in listed.text and secret not in listed.text

    monkeypatch.undo()
    monkeypatch.setattr(session_charge_tasks, "get_settings", lambda: settings_for(url))
    response = retry(client, admin, job_id)

    assert response.status_code == 202, response.text
    assert (response.json()["status"], response.json()["attempts"]) == ("todo", 2)
    assert name not in response.text and secret not in response.text
    [event] = retried_events(url, job_id)
    assert event.actor_user_id == admin.user.id
    assert event.details == {"job_id": job_id, "task_name": TASK, "attempts": 1}

    work(url)

    # Procrastinate counts the manual retry as an attempt (its
    # procrastinate_retry_job_v2 adds one, #128 D5) and the run another.
    assert job_state(url, job_id) == ("succeeded", 3)
    charges = charges_for(url, sessions)
    assert sorted((c.class_session_id, c.student_id) for c in charges) == sorted(
        (it.session, it.student) for it in items
    )
    [account] = {c.billing_account_id for c in charges}
    assert sum((c.amount for c in charges), Decimal(0)) == 3 * PRICE
    balance = in_uow(url, lambda s: balances.account_balance(s, account))
    assert balance.outstanding == Money(3 * PRICE)

    # A second retry, then one more periodic run: still three charges.
    assert retry(client, admin, job_id).json()["code"] == "job_not_failed"
    assert len(retried_events(url, job_id)) == 1
    again = defer(url)
    work(url)
    assert job_state(url, again) == ("succeeded", 1)
    assert len(charges_for(url, sessions)) == 3


# --- F2: a queued run covers the work ----------------------------------------------


def test_a_failed_run_with_a_queued_run_behind_it_is_409(
    module_seeded_url, client, admin, monkeypatch
):
    url = module_seeded_url
    three_sessions(url)
    break_the_run_after_one_item(monkeypatch, "run lost")
    job_id = defer(url)
    work(url)
    assert job_state(url, job_id) == ("failed", 1)
    queued = defer(url)

    response = retry(client, admin, job_id)

    assert (response.status_code, response.json()["code"]) == (
        409,
        "job_already_queued",
    )
    assert job_state(url, job_id) == ("failed", 1)
    assert job_state(url, queued) == ("todo", 0)
    assert retried_events(url, job_id) == []


# --- F3: not retryable --------------------------------------------------------------


def test_a_task_outside_the_retryable_set_is_409(
    module_seeded_url, client, admin, monkeypatch
):
    url = module_seeded_url
    three_sessions(url)
    break_the_run_after_one_item(monkeypatch, "run lost")
    job_id = defer(url)
    work(url)
    assert job_state(url, job_id) == ("failed", 1)
    [unregistered] = run_sql(
        url,
        "INSERT INTO procrastinate_jobs (queue_name, task_name, status)"
        " VALUES ('default', 'legacy:renamed_task', 'failed') RETURNING id",
    )
    run_sql(
        url,
        "INSERT INTO job_failures (job_id, task_name)"
        " VALUES (:j, 'legacy:renamed_task')",
        {"j": unregistered.id},
    )
    # A task no longer registered is never retryable.
    response = retry(client, admin, unregistered.id)
    assert (response.status_code, response.json()["code"]) == (
        409,
        "job_not_retryable",
    )
    # Nor is a registered one taken out of RETRYABLE_TASKS.
    monkeypatch.setattr(service, "RETRYABLE_TASKS", frozenset())
    response = retry(client, admin, job_id)
    assert (response.status_code, response.json()["code"]) == (
        409,
        "job_not_retryable",
    )

    assert retried_events(url, job_id) == []
    assert retried_events(url, unregistered.id) == []
    assert job_state(url, job_id) == ("failed", 1)
