"""The hourly purge of ended sessions (#25 H) with the ``clock`` fixture."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

import pytest
from procrastinate import App

from app.core import jobs
from app.core.config import get_settings
from app.modules.identity import tasks
from tests.authz.csrf import TEST_APP_ORIGIN
from tests.conftest import CLOCK_START
from tests.integration.auth_support import create_user
from tests.integration.conftest import run_sql, settings_for

CUTOFF = CLOCK_START - timedelta(hours=24)
SECOND = timedelta(seconds=1)
LIVE_END = CLOCK_START + timedelta(hours=12)


def insert_session(
    url: str,
    user_id: uuid.UUID,
    *,
    last_seen_at: datetime,
    expires_at: datetime = LIVE_END,
    idle_seconds: int = 1800,
    revoked_at: datetime | None = None,
) -> uuid.UUID:
    [row] = run_sql(
        url,
        "INSERT INTO auth_sessions"
        " (user_id, token_hash, last_seen_at, expires_at, idle_timeout_seconds,"
        " revoked_at)"
        " VALUES (:user_id, :hash, :seen, :expires, :idle, :revoked) RETURNING id",
        {
            "user_id": user_id,
            "hash": uuid.uuid4().bytes * 2,
            "seen": last_seen_at,
            "expires": expires_at,
            "idle": idle_seconds,
            "revoked": revoked_at,
        },
    )
    return row.id


def remaining(url: str) -> set[uuid.UUID]:
    return {row.id for row in run_sql(url, "SELECT id FROM auth_sessions")}


def purge(url: str, **kwargs: int) -> int:
    return asyncio.run(tasks.run_purge(settings_for(url), **kwargs))


def recent() -> datetime:
    return CLOCK_START - timedelta(minutes=1)


def test_exactly_the_sessions_that_ended_24_hours_ago_are_deleted(seeded_url, clock):
    user = create_user(seeded_url).id
    staff_idle = timedelta(seconds=1800)
    at_cutoff = {
        "revoked": insert_session(
            seeded_url, user, last_seen_at=recent(), revoked_at=CUTOFF
        ),
        "absolute": insert_session(
            seeded_url, user, last_seen_at=CUTOFF - staff_idle / 2, expires_at=CUTOFF
        ),
        "idle": insert_session(seeded_url, user, last_seen_at=CUTOFF - staff_idle),
    }
    inside_grace = {
        "revoked": insert_session(
            seeded_url, user, last_seen_at=recent(), revoked_at=CUTOFF + SECOND
        ),
        "absolute": insert_session(
            seeded_url,
            user,
            last_seen_at=CUTOFF - staff_idle / 2,
            expires_at=CUTOFF + SECOND,
        ),
        "idle": insert_session(
            seeded_url, user, last_seen_at=CUTOFF - staff_idle + SECOND
        ),
        "idle_23h59m_ago": insert_session(
            seeded_url,
            user,
            last_seen_at=CLOCK_START - timedelta(hours=23, minutes=59) - staff_idle,
        ),
    }
    live = insert_session(seeded_url, user, last_seen_at=recent())
    extended_live = insert_session(
        seeded_url,
        user,
        last_seen_at=CLOCK_START - timedelta(days=6),
        expires_at=CLOCK_START + timedelta(days=20),
        idle_seconds=604800,
    )
    other_tables = run_sql(
        seeded_url,
        "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM audit_events)",
    )

    assert purge(seeded_url) == 3

    assert remaining(seeded_url) == {*inside_grace.values(), live, extended_live}
    assert (
        run_sql(
            seeded_url,
            "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM audit_events)",
        )
        == other_tables
    )
    # Idempotent: a second run deletes nothing.
    assert purge(seeded_url) == 0
    assert remaining(seeded_url) == {*inside_grace.values(), live, extended_live}
    assert remaining(seeded_url).isdisjoint(at_cutoff.values())


def test_the_grace_follows_the_clock(seeded_url, clock):
    user = create_user(seeded_url).id
    revoked = insert_session(
        seeded_url, user, last_seen_at=recent(), revoked_at=CLOCK_START
    )

    clock.advance(timedelta(hours=24) - SECOND)
    assert purge(seeded_url) == 0
    clock.advance(SECOND)
    assert purge(seeded_url) == 1
    assert revoked not in remaining(seeded_url)


def test_purgeable_rows_beyond_one_batch_are_all_deleted(seeded_url, clock):
    user = create_user(seeded_url).id
    for _ in range(5):
        insert_session(seeded_url, user, last_seen_at=recent(), revoked_at=CUTOFF)
    keep = insert_session(seeded_url, user, last_seen_at=recent())

    assert purge(seeded_url, batch_size=2) == 5

    assert remaining(seeded_url) == {keep}


def test_the_batch_size_is_1000():
    assert tasks.BATCH_SIZE == 1000
    assert tasks.PURGE_GRACE == timedelta(hours=24)


def test_one_log_line_with_the_count_and_no_ids(seeded_url, clock, caplog):
    user = create_user(seeded_url).id
    row_id = insert_session(seeded_url, user, last_seen_at=recent(), revoked_at=CUTOFF)
    caplog.set_level(logging.INFO, logger=tasks.__name__)

    purge(seeded_url)

    [record] = [r for r in caplog.records if r.name == tasks.__name__]
    assert "purge" in record.getMessage().lower()
    assert record.deleted == 1  # type: ignore[attr-defined]
    text = f"{record.getMessage()} {record.__dict__}"
    assert str(row_id) not in text
    assert str(user) not in text


# --- H1: registration ---------------------------------------------------------------


def worker_app(app_env: str = "prod") -> App:
    placeholder = settings_for("postgresql://u:p@127.0.0.1:1/db")
    return jobs.create_job_app(jobs.worker_connector(placeholder), app_env=app_env)  # type: ignore[arg-type]


@pytest.mark.parametrize("app_env", ["local", "dev", "prod"])
def test_the_purge_is_periodic_in_every_environment(app_env):
    app = worker_app(app_env)

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "identity:purge_auth_sessions"
    ]
    assert periodic.cron == "7 * * * *"
    assert periodic.periodic_id == "identity-purge-auth-sessions"
    assert periodic.configure_kwargs["queueing_lock"] == "identity-purge-auth-sessions"
    task = app.tasks["identity:purge_auth_sessions"]
    assert task.queueing_lock == "identity-purge-auth-sessions"
    assert jobs.task_parameter_types(app)["identity:purge_auth_sessions"] == {
        "timestamp": int
    }


# --- H4: one run through an in-process worker ----------------------------------------


def test_a_deferred_purge_runs_in_a_worker_and_succeeds(seeded_url, clock, monkeypatch):
    user = create_user(seeded_url).id
    insert_session(seeded_url, user, last_seen_at=recent(), revoked_at=CUTOFF)
    keep = insert_session(seeded_url, user, last_seen_at=recent())
    # The task reads the process settings, as in the worker container.
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_ORIGIN", TEST_APP_ORIGIN)
    monkeypatch.setenv("DATABASE_URL", seeded_url)
    monkeypatch.setenv("DATABASE_DIRECT_URL", seeded_url)
    get_settings.cache_clear()
    app = jobs.create_job_app(jobs.worker_connector(get_settings()), app_env="prod")
    app.periodic_registry.periodic_tasks.clear()  # only the deferred job runs

    async def defer_and_work() -> int:
        async with app.open_async():
            job_id = await app.configure_task(tasks.TASK_NAME).defer_async(
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
        seeded_url,
        "SELECT status::text AS status FROM procrastinate_jobs WHERE id = :id",
        {"id": job_id},
    )
    assert row.status == "succeeded"
    assert remaining(seeded_url) == {keep}
