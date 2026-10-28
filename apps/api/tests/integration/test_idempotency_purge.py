"""The hourly purge of expired idempotency keys (#88 D) with the ``clock``
fixture."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

import pytest
from procrastinate import App

from app.core import idempotency_tasks, jobs
from app.core.idempotency import RETENTION
from tests.conftest import CLOCK_START
from tests.integration.auth_support import create_user
from tests.integration.conftest import run_sql, settings_for

MICROSECOND = timedelta(microseconds=1)


def insert_key(url: str, user_id: uuid.UUID, expires_at: datetime) -> str:
    """A committed key row; returns its key (rows are told apart by it)."""
    key = uuid.uuid4().hex
    run_sql(
        url,
        "INSERT INTO idempotency_keys (user_id, endpoint, key, request_hash,"
        " response_status, response_body, expires_at)"
        " VALUES (:u, 'POST /api/v1/test', :k, :h, 201, '{\"secret\": 1}', :e)",
        {"u": user_id, "k": key, "h": b"\x01" * 32, "e": expires_at},
    )
    return key


def remaining(url: str) -> set[str]:
    return {row.key for row in run_sql(url, "SELECT key FROM idempotency_keys")}


def purge(url: str, **kwargs: int) -> int:
    return asyncio.run(idempotency_tasks.run_purge(settings_for(url), **kwargs))


@pytest.fixture
def user_id(seeded_url: str) -> uuid.UUID:
    return create_user(seeded_url).id


def test_exactly_the_expired_keys_are_deleted(seeded_url, user_id, clock):
    gone = {
        insert_key(seeded_url, user_id, CLOCK_START),
        insert_key(seeded_url, user_id, CLOCK_START - RETENTION),
    }
    kept = {
        insert_key(seeded_url, user_id, CLOCK_START + MICROSECOND),
        insert_key(seeded_url, user_id, CLOCK_START + RETENTION),
    }
    users_before = run_sql(seeded_url, "SELECT count(*) FROM users")

    assert purge(seeded_url) == 2

    assert remaining(seeded_url) == kept
    assert not remaining(seeded_url) & gone
    assert run_sql(seeded_url, "SELECT count(*) FROM users") == users_before
    # Idempotent: a second run deletes nothing.
    assert purge(seeded_url) == 0
    assert remaining(seeded_url) == kept


def test_a_live_key_is_deleted_once_it_expires(seeded_url, user_id, clock):
    insert_key(seeded_url, user_id, CLOCK_START + RETENTION)

    clock.advance(RETENTION - MICROSECOND)
    assert purge(seeded_url) == 0
    clock.advance(MICROSECOND)
    assert purge(seeded_url) == 1


def test_expired_keys_beyond_one_batch_are_all_deleted(seeded_url, user_id, clock):
    for _ in range(5):
        insert_key(seeded_url, user_id, CLOCK_START - MICROSECOND)
    keep = insert_key(seeded_url, user_id, CLOCK_START + RETENTION)

    assert purge(seeded_url, batch_size=2) == 5

    assert remaining(seeded_url) == {keep}


def test_the_batch_size():
    assert idempotency_tasks.BATCH_SIZE == 1000


def test_one_log_line_with_the_count_and_no_keys(seeded_url, user_id, clock, caplog):
    key = insert_key(seeded_url, user_id, CLOCK_START)
    caplog.set_level(logging.INFO, logger=idempotency_tasks.__name__)

    purge(seeded_url)

    [record] = [r for r in caplog.records if r.name == idempotency_tasks.__name__]
    assert "purge" in record.getMessage().lower()
    assert record.deleted == 1  # type: ignore[attr-defined]
    text = f"{record.getMessage()} {record.__dict__}"
    assert key not in text
    assert "secret" not in text
    assert ("01" * 32) not in text


def test_the_user_foreign_key_cascades(seeded_url):
    [constraint] = run_sql(
        seeded_url,
        "SELECT confdeltype::text AS confdeltype FROM pg_constraint"
        " WHERE conname = 'fk_idempotency_keys_user_id_users'",
    )

    assert constraint.confdeltype == "c"  # ON DELETE CASCADE


# --- D1: registration -----------------------------------------------------------------


def worker_app(app_env: str = "prod") -> App:
    placeholder = settings_for("postgresql://u:p@127.0.0.1:1/db")
    return jobs.create_job_app(jobs.worker_connector(placeholder), app_env=app_env)  # type: ignore[arg-type]


@pytest.mark.parametrize("app_env", ["local", "dev", "prod"])
def test_the_purge_is_periodic_in_every_environment(app_env):
    app = worker_app(app_env)

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "idempotency:purge_keys"
    ]
    assert periodic.cron == "41 * * * *"
    assert periodic.periodic_id == "idempotency-purge-keys"
    assert periodic.configure_kwargs["queueing_lock"] == "idempotency-purge-keys"
    task = app.tasks["idempotency:purge_keys"]
    assert task.queueing_lock == "idempotency-purge-keys"
    assert jobs.task_parameter_types(app)["idempotency:purge_keys"] == {
        "timestamp": int
    }


def test_no_other_periodic_task_uses_minute_41():
    crons = [
        p.cron
        for p in worker_app("local").periodic_registry.periodic_tasks.values()
        if p.task.name != "idempotency:purge_keys"
    ]

    assert not [c for c in crons if c.split()[0] == "41"]
