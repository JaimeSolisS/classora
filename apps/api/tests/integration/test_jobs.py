"""Procrastinate jobs against a real PostgreSQL (M1-16 F1-F6, E3).

Each test migrates its own fresh database to head with **Alembic** (which
applies the Procrastinate schema; nothing else does). Workers run in-process:
``run_worker_async(wait=False)`` processes what is queued and returns, and the
long-running cases run a worker task that the test stops. Test-only tasks are
registered on apps built with ``app_env="prod"``, so the demo tasks and the
periodic demo stay out of the way.
"""

import asyncio
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from procrastinate import App, RetryStrategy
from sqlalchemy import make_url

from app import worker
from app.core import jobs
from app.core.config import Settings
from app.core.demo_jobs import FAIL_ATTEMPTS, FAIL_RETRY
from app.main import create_app
from app.worker import run as run_worker_process
from tests.integration.conftest import run_sql_async, settings_for
from tests.integration.test_migrations import alembic_config

pytestmark = pytest.mark.anyio

# Short polling so workers notice new jobs and stop requests quickly.
FAST = {
    "install_signal_handlers": False,
    "fetch_job_polling_interval": 0.2,
    "abort_job_polling_interval": 0.2,
}


@pytest.fixture
def jobs_url(fresh_database_url: str) -> str:
    """A fresh database migrated to head (Procrastinate schema included)."""
    command.upgrade(alembic_config(fresh_database_url), "head")
    return fresh_database_url


@pytest.fixture
def settings(jobs_url: str) -> Settings:
    return settings_for(jobs_url)


async def job_rows(dsn: str, where: str = "true") -> list[Any]:
    # The WHERE clause comes from the tests themselves, never from user input.
    return await run_sql_async(
        dsn,
        "SELECT id, task_name, status::text AS status, attempts, args "  # noqa: S608
        f"FROM procrastinate_jobs WHERE {where} ORDER BY id",
    )


async def job_status(dsn: str, job_id: int) -> tuple[str, int]:
    (row,) = await job_rows(dsn, f"id = {int(job_id)}")
    return row.status, row.attempts


async def wait_until(
    check: Callable[[], Any], *, timeout: float = 10.0, interval: float = 0.1
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not reached in time")


def make_app(settings: Settings, **tasks: Any) -> App:
    """A worker-side app with no demo tasks plus ``tasks`` (name -> task kwargs)."""
    app = jobs.create_job_app(jobs.worker_connector(settings), app_env="prod")
    for name, (func, options) in tasks.items():
        app.task(name=name, **options)(func)
    return app


# --- test-only tasks ------------------------------------------------------------

RUNS: list[int] = []


@jobs.ids_only
async def record(item: int) -> None:
    RUNS.append(item)


@jobs.ids_only
async def always_fails(item: int) -> None:
    raise RuntimeError("test failure")


@jobs.ids_only
async def sleeps(seconds: int) -> None:
    await asyncio.sleep(seconds)


@jobs.ids_only
async def tick(timestamp: int) -> None:
    pass


@pytest.fixture(autouse=True)
def clear_runs() -> Iterator[None]:
    RUNS.clear()
    yield
    RUNS.clear()


# --- F1 defer and execute ---------------------------------------------------------


async def test_demo_job_deferred_by_the_api_side_succeeds(settings, jobs_url):
    api_app = jobs.create_job_app(jobs.api_connector(settings), app_env="local")
    worker_app = jobs.create_job_app(jobs.worker_connector(settings), app_env="local")
    demo_id = uuid.uuid7()

    async with api_app.open_async():
        job_id = await api_app.configure_task(
            "demo:succeed", allow_unknown=False
        ).defer_async(demo_id=str(demo_id))
    assert await job_status(jobs_url, job_id) == ("todo", 0)

    async with worker_app.open_async():
        await worker_app.run_worker_async(wait=False, **FAST)

    assert await job_status(jobs_url, job_id) == ("succeeded", 1)
    (row,) = await job_rows(jobs_url, f"id = {job_id}")
    assert row.args == {"demo_id": str(demo_id)}


async def test_jobs_deferred_without_a_worker_run_when_one_starts(settings, jobs_url):
    app = make_app(settings, **{"test:record": (record, {})})
    async with app.open_async():
        job_ids = [
            await app.configure_task("test:record").defer_async(item=item)
            for item in range(3)
        ]
    # No worker yet: the jobs wait in the database.
    assert [(r.status, r.attempts) for r in await job_rows(jobs_url)] == [
        ("todo", 0)
    ] * 3

    later = make_app(settings, **{"test:record": (record, {})})
    async with later.open_async():
        await later.run_worker_async(wait=False, **FAST)

    assert sorted(RUNS) == [0, 1, 2]
    for job_id in job_ids:
        assert await job_status(jobs_url, job_id) == ("succeeded", 1)


# --- F2 retry then fail -------------------------------------------------------------


async def test_failing_job_is_retried_then_failed_and_stays_failed(settings, jobs_url):
    retry = RetryStrategy(max_attempts=FAIL_RETRY.max_attempts, wait=0)
    tasks = {"test:fails": (always_fails, {"retry": retry})}
    app = make_app(settings, **tasks)
    async with app.open_async():
        job_id = await app.configure_task("test:fails").defer_async(item=1)
        await app.run_worker_async(wait=False, **FAST)

    # `attempts` counts every run; max_attempts counts the retries after the
    # first run (Procrastinate 3.10). demo:fail uses the same policy.
    expected_runs = FAIL_ATTEMPTS
    assert await job_status(jobs_url, job_id) == ("failed", expected_runs)

    # A new worker instance leaves the failed job alone.
    again = make_app(settings, **tasks)
    async with again.open_async():
        await again.run_worker_async(wait=False, **FAST)
    assert await job_status(jobs_url, job_id) == ("failed", expected_runs)


# --- F3 periodic deduplication ------------------------------------------------


def periodic_app(settings: Settings) -> App:
    app = make_app(settings)
    app.periodic(cron="* * * * * *", periodic_id="test", queueing_lock="test-tick")(
        app.task(name="test:tick", queueing_lock="test-tick")(tick)
    )
    return app


async def run_workers_for(apps: list[App], seconds: float) -> None:
    async with asyncio.TaskGroup() as group:
        for app in apps:
            await app.open_async()
        tasks = [group.create_task(app.run_worker_async(**FAST)) for app in apps]
        await asyncio.sleep(seconds)
        for task in tasks:
            task.cancel()
    for app in apps:
        await app.close_async()


async def periodic_ticks(dsn: str) -> tuple[list[Any], list[Any]]:
    defers = await run_sql_async(
        dsn,
        "SELECT task_name, periodic_id, defer_timestamp, count(*) AS n "
        "FROM procrastinate_periodic_defers GROUP BY 1, 2, 3 ORDER BY 3",
    )
    per_tick = await run_sql_async(
        dsn,
        "SELECT (args->>'timestamp')::bigint AS ts, count(*) AS n "
        "FROM procrastinate_jobs WHERE task_name = 'test:tick' GROUP BY 1 ORDER BY 1",
    )
    return defers, per_tick


@pytest.mark.parametrize("workers", [2, 1])
async def test_periodic_task_runs_once_per_tick(settings, jobs_url, workers):
    await run_workers_for([periodic_app(settings) for _ in range(workers)], 4.5)

    defers, per_tick = await periodic_ticks(jobs_url)
    # One job per tick (its `timestamp` argument), for at least 3 ticks.
    assert len(per_tick) >= 3
    assert all(row.n == 1 for row in per_tick)
    # procrastinate_periodic_defers keeps only the latest defer per task and
    # periodic_id; it never holds a duplicate.
    assert defers and all(row.n == 1 for row in defers)


# --- F4 graceful shutdown with a running job ----------------------------------


@pytest.fixture
def worker_process(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Settings, App], Any]:
    """Runs ``app.worker.run`` (the container's code path) on a given app."""

    async def start(settings: Settings, app: App) -> tuple[asyncio.Task[int], Any]:
        monkeypatch.setattr("app.worker.create_worker_job_app", lambda _: app)
        stop = asyncio.Event()
        task = asyncio.create_task(run_worker_process(settings, stop))
        return task, stop

    return start


async def test_shutdown_waits_for_a_job_shorter_than_the_timeout(
    jobs_url, worker_process
):
    settings = settings_for(jobs_url).model_copy(
        update={"worker_shutdown_timeout_seconds": 5}
    )
    app = make_app(settings, **{"test:sleeps": (sleeps, {})})
    async with app.open_async():
        job_id = await app.configure_task("test:sleeps").defer_async(seconds=1)
    task, stop = await worker_process(settings, app)
    await wait_until(lambda: _status_is(jobs_url, job_id, "doing"))

    started = time.monotonic()
    stop.set()
    assert await asyncio.wait_for(task, 10) == 0

    assert time.monotonic() - started < 5
    assert await job_status(jobs_url, job_id) == ("succeeded", 1)


@pytest.mark.parametrize(
    ("retry", "final"),
    [(None, ("aborted", 1)), (RetryStrategy(max_attempts=3), ("todo", 1))],
    ids=["no-retry-policy", "retry-policy"],
)
async def test_shutdown_aborts_a_job_longer_than_the_timeout(
    jobs_url, worker_process, retry, final
):
    timeout = 1
    settings = settings_for(jobs_url).model_copy(
        update={"worker_shutdown_timeout_seconds": timeout}
    )
    options = {"retry": retry} if retry else {}
    app = make_app(settings, **{"test:sleeps": (sleeps, options)})
    async with app.open_async():
        job_id = await app.configure_task("test:sleeps").defer_async(seconds=60)
    task, stop = await worker_process(settings, app)
    await wait_until(lambda: _status_is(jobs_url, job_id, "doing"))

    started = time.monotonic()
    stop.set()
    assert await asyncio.wait_for(task, timeout + 10) == 0

    assert time.monotonic() - started < timeout + 5
    # Never left "doing": aborted, or (with a retry policy) queued again.
    assert await job_status(jobs_url, job_id) == final


async def _status_is(dsn: str, job_id: int, status: str) -> bool:
    return (await job_status(dsn, job_id))[0] == status


# --- E3 waiting for the schema ------------------------------------------------------


async def test_worker_waits_for_the_schema_then_starts(
    fresh_database_url, caplog, monkeypatch
):
    monkeypatch.setattr("app.worker.SCHEMA_POLL_SECONDS", 0.2)
    config = alembic_config(fresh_database_url)
    await asyncio.to_thread(command.upgrade, config, "0001")
    settings = settings_for(fresh_database_url)
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker_process(settings, stop))

    await asyncio.sleep(1)
    assert not task.done()
    waiting = [r for r in caplog.records if r.name == "app.worker"]
    assert len(waiting) == 1  # at most one line per WAITING_LOG_SECONDS
    assert "make migrate" in waiting[0].getMessage()
    # The worker created nothing.
    assert (
        await run_sql_async(
            fresh_database_url,
            "SELECT count(*) AS n FROM pg_class WHERE relname LIKE 'procrastinate%'",
        )
    )[0].n == 0

    await asyncio.to_thread(command.upgrade, config, "head")
    api_app = jobs.create_job_app(jobs.api_connector(settings), app_env="local")
    async with api_app.open_async():
        job_id = await api_app.configure_task("demo:succeed").defer_async(
            demo_id=str(uuid.uuid7())
        )
    await wait_until(lambda: _status_is(fresh_database_url, job_id, "succeeded"))

    stop.set()
    assert await asyncio.wait_for(task, 10) == 0


async def test_worker_recovers_when_the_schema_disappears_and_returns(
    jobs_url, caplog, monkeypatch
):
    # Fast polling and heartbeats so the lost schema is noticed quickly.
    options = worker.worker_options
    monkeypatch.setattr(
        "app.worker.worker_options",
        lambda s: {**options(s), **FAST, "update_heartbeat_interval": 0.3},
    )
    monkeypatch.setattr("app.worker.SCHEMA_POLL_SECONDS", 0.2)
    config = alembic_config(jobs_url)
    settings = settings_for(jobs_url)
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker_process(settings, stop))
    await wait_until(lambda: _worker_registered(jobs_url))

    # The schema goes away under the running worker (a downgrade, a reset).
    await asyncio.to_thread(command.downgrade, config, "0001")
    await wait_until(
        lambda: _logged(caplog, "worker_restart"), timeout=10, interval=0.2
    )
    assert not task.done()

    await asyncio.to_thread(command.upgrade, config, "head")
    api_app = jobs.create_job_app(jobs.api_connector(settings), app_env="local")
    async with api_app.open_async():
        job_id = await api_app.configure_task("demo:succeed").defer_async(
            demo_id=str(uuid.uuid7())
        )
    await wait_until(lambda: _status_is(jobs_url, job_id, "succeeded"))

    stop.set()
    assert await asyncio.wait_for(task, 10) == 0


async def _worker_registered(dsn: str) -> bool:
    rows = await run_sql_async(dsn, "SELECT count(*) AS n FROM procrastinate_workers")
    return rows[0].n > 0


async def _logged(caplog: pytest.LogCaptureFixture, action: str) -> bool:
    return any(getattr(r, "action", None) == action for r in caplog.records)


async def test_worker_stopped_while_waiting_exits_0(fresh_database_url):
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker_process(settings_for(fresh_database_url), stop)
    )
    await asyncio.sleep(0.5)

    stop.set()

    assert await asyncio.wait_for(task, 5) == 0


# --- F5 API deferral through PgBouncer -----------------------------------------------


def through_pgbouncer(pgbouncer_url: str, direct_url: str) -> str:
    database = make_url(direct_url).database
    return (
        make_url(pgbouncer_url)
        .set(database=database)
        .render_as_string(hide_password=False)
    )


async def test_api_connector_defers_through_pgbouncer(pgbouncer_url, jobs_url):
    settings = settings_for(
        through_pgbouncer(pgbouncer_url, jobs_url), direct_dsn=jobs_url
    )
    app = App(connector=jobs.api_connector(settings))
    app.task(name="test:record")(record)

    async with app.open_async():
        deferrer = app.configure_task("test:record")
        # Sequential: the same pooled connection, far past psycopg's default
        # prepare threshold (5 executions).
        sequential = [await deferrer.defer_async(item=i) for i in range(25)]
        concurrent = await asyncio.gather(
            *(deferrer.defer_async(item=i) for i in range(25, 50))
        )

    rows = await job_rows(jobs_url)
    assert len(set(sequential) | set(concurrent)) == 50
    assert len(rows) == 50
    assert sorted(row.args["item"] for row in rows) == list(range(50))


# --- F6 demo endpoint ----------------------------------------------------------------


@pytest.fixture
def api_client(
    pgbouncer_url: str, jobs_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", through_pgbouncer(pgbouncer_url, jobs_url))
    monkeypatch.setenv("DATABASE_DIRECT_URL", jobs_url)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


async def test_demo_endpoint_defers_then_guards_the_queue(api_client, jobs_url):
    response = await asyncio.to_thread(api_client.post, "/api/v1/dev/demo-jobs")

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"job_id", "demo_id"}
    assert uuid.UUID(body["demo_id"]).version == 7
    (row,) = await job_rows(jobs_url)
    assert (row.id, row.task_name, row.status) == (
        body["job_id"],
        "demo:succeed",
        "todo",
    )
    assert row.args == {"demo_id": body["demo_id"]}

    again = await asyncio.to_thread(api_client.post, "/api/v1/dev/demo-jobs")
    assert again.status_code == 409
    assert again.headers["content-type"] == "application/problem+json"
    assert again.json()["code"] == "demo_job_already_queued"
    assert len(await job_rows(jobs_url)) == 1

    # The other outcome has its own lock.
    failing = await asyncio.to_thread(
        api_client.post, "/api/v1/dev/demo-jobs", json={"outcome": "fail"}
    )
    assert failing.status_code == 202
    (_, fail_row) = await job_rows(jobs_url)
    assert fail_row.task_name == "demo:fail"

    invalid = await asyncio.to_thread(
        api_client.post, "/api/v1/dev/demo-jobs", json={"outcome": "maybe"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"
    assert len(await job_rows(jobs_url)) == 2
