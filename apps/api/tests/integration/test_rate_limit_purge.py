"""The hourly purge of ended rate-limit windows (#27 F) with the ``clock``
fixture."""

import asyncio
import logging
from datetime import datetime, timedelta

import pytest
from procrastinate import App

from app.core import jobs, ratelimit_tasks
from app.core.config import get_settings
from app.core.ratelimit import key_hash
from tests.authz.csrf import TEST_APP_ORIGIN
from tests.conftest import CLOCK_START
from tests.integration.conftest import run_sql, settings_for

QUARTER = timedelta(minutes=15)
HOUR = timedelta(hours=1)
SECOND = timedelta(seconds=1)
CUTOFF = CLOCK_START - HOUR


def insert_counter(
    url: str, policy: str, key: str, start: datetime, length: timedelta
) -> str:
    """A counter row; returns its key (rows are told apart by their key)."""
    run_sql(
        url,
        "INSERT INTO rate_limit_counters"
        " (policy, key_hash, window_start, window_end, count)"
        " VALUES (:p, :h, :s, :e, 3)",
        {"p": policy, "h": key_hash(policy, key), "s": start, "e": start + length},
    )
    return key


def remaining(url: str) -> set[bytes]:
    return {
        bytes(row.key_hash)
        for row in run_sql(url, "SELECT key_hash FROM rate_limit_counters")
    }


def hashes(policy: str, *keys: str) -> set[bytes]:
    return {key_hash(policy, key) for key in keys}


def purge(url: str, **kwargs: int) -> int:
    return asyncio.run(ratelimit_tasks.run_purge(settings_for(url), **kwargs))


def test_exactly_the_windows_that_ended_an_hour_ago_are_deleted(head_url, clock):
    # 15-minute windows (login) and one-hour windows (registration).
    gone = [
        insert_counter(head_url, "login.ip", "q-at-cutoff", CUTOFF - QUARTER, QUARTER),
        insert_counter(head_url, "login.ip", "q-old", CUTOFF - 4 * HOUR, QUARTER),
        insert_counter(head_url, "registration.ip", "h-at-cutoff", CUTOFF - HOUR, HOUR),
    ]
    kept_quarter = [
        # Ended 59 minutes ago (inside the grace).
        insert_counter(
            head_url, "login.ip", "q-grace", CUTOFF - QUARTER + 60 * SECOND, QUARTER
        ),
        # The open window.
        insert_counter(head_url, "login.ip", "q-open", CLOCK_START, QUARTER),
    ]
    kept_hour = [
        insert_counter(
            head_url, "registration.ip", "h-grace", CUTOFF - HOUR + SECOND, HOUR
        ),
        insert_counter(head_url, "registration.ip", "h-open", CLOCK_START, HOUR),
    ]
    users_before = run_sql(head_url, "SELECT count(*) FROM users")

    assert purge(head_url) == 3

    expected = hashes("login.ip", *kept_quarter) | hashes("registration.ip", *kept_hour)
    assert remaining(head_url) == expected
    assert run_sql(head_url, "SELECT count(*) FROM users") == users_before
    assert not remaining(head_url) & (
        hashes("login.ip", *gone[:2]) | hashes("registration.ip", gone[2])
    )
    # Idempotent: a second run deletes nothing.
    assert purge(head_url) == 0
    assert remaining(head_url) == expected


def test_an_open_window_is_never_deleted(head_url, clock):
    insert_counter(head_url, "registration.ip", "open", CLOCK_START, HOUR)

    assert purge(head_url) == 0
    # The window ends at 13:00; the grace ends at 14:00.
    clock.advance(2 * HOUR - SECOND)
    assert purge(head_url) == 0
    clock.advance(SECOND)
    assert purge(head_url) == 1


def test_purgeable_rows_beyond_one_batch_are_all_deleted(head_url, clock):
    for index in range(5):
        insert_counter(head_url, "login.ip", f"old-{index}", CUTOFF - QUARTER, QUARTER)
    keep = insert_counter(head_url, "login.ip", "open", CLOCK_START, QUARTER)

    assert purge(head_url, batch_size=2) == 5

    assert remaining(head_url) == hashes("login.ip", keep)


def test_the_batch_size_and_grace():
    assert ratelimit_tasks.BATCH_SIZE == 1000
    assert ratelimit_tasks.PURGE_GRACE == HOUR


def test_one_log_line_with_the_count_and_no_keys(head_url, clock, caplog):
    insert_counter(head_url, "login.username", "demo.owner", CUTOFF - QUARTER, QUARTER)
    caplog.set_level(logging.INFO, logger=ratelimit_tasks.__name__)

    purge(head_url)

    [record] = [r for r in caplog.records if r.name == ratelimit_tasks.__name__]
    assert "purge" in record.getMessage().lower()
    assert record.deleted == 1  # type: ignore[attr-defined]
    text = f"{record.getMessage()} {record.__dict__}"
    assert "demo.owner" not in text
    assert key_hash("login.username", "demo.owner").hex() not in text


# --- F1: registration ---------------------------------------------------------------


def worker_app(app_env: str = "prod") -> App:
    placeholder = settings_for("postgresql://u:p@127.0.0.1:1/db")
    return jobs.create_job_app(jobs.worker_connector(placeholder), app_env=app_env)  # type: ignore[arg-type]


@pytest.mark.parametrize("app_env", ["local", "dev", "prod"])
def test_the_purge_is_periodic_in_every_environment(app_env):
    app = worker_app(app_env)

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "ratelimit:purge_counters"
    ]
    assert periodic.cron == "23 * * * *"
    assert periodic.periodic_id == "ratelimit-purge-counters"
    assert periodic.configure_kwargs["queueing_lock"] == "ratelimit-purge-counters"
    task = app.tasks["ratelimit:purge_counters"]
    assert task.queueing_lock == "ratelimit-purge-counters"
    assert jobs.task_parameter_types(app)["ratelimit:purge_counters"] == {
        "timestamp": int
    }


def test_a_deferred_purge_runs_in_a_worker_and_succeeds(head_url, clock, monkeypatch):
    insert_counter(head_url, "login.ip", "old", CUTOFF - QUARTER, QUARTER)
    keep = insert_counter(head_url, "login.ip", "open", CLOCK_START, QUARTER)
    # The task reads the process settings, as in the worker container.
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_ORIGIN", TEST_APP_ORIGIN)
    monkeypatch.setenv("DATABASE_URL", head_url)
    monkeypatch.setenv("DATABASE_DIRECT_URL", head_url)
    get_settings.cache_clear()
    app = jobs.create_job_app(jobs.worker_connector(get_settings()), app_env="prod")
    app.periodic_registry.periodic_tasks.clear()  # only the deferred job runs

    async def defer_and_work() -> int:
        async with app.open_async():
            job_id = await app.configure_task(ratelimit_tasks.TASK_NAME).defer_async(
                timestamp=int(CLOCK_START.timestamp())
            )
            await app.run_worker_async(
                wait=False,
                install_signal_handlers=False,
                fetch_job_polling_interval=0.2,
                abort_job_polling_interval=0.2,
            )
        return job_id

    job_id = asyncio.run(defer_and_work())

    [row] = run_sql(
        head_url,
        "SELECT status::text AS status FROM procrastinate_jobs WHERE id = :id",
        {"id": job_id},
    )
    assert row.status == "succeeded"
    assert remaining(head_url) == hashes("login.ip", keep)
