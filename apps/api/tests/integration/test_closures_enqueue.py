"""Closure writes queue the series reconciliation (#232 B, D2; ADR-0003
"enqueue after commit", ``queueing_lock``).

The real app defers through its own job app (the API-side connector) into
the module database's ``procrastinate_jobs``. No worker runs here: each test
first removes the queued ``scheduling:materialize_series`` jobs, then counts
the ones its writes queued.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import jobs
from app.modules.catalog import periods_router
from app.modules.identity.permissions import OWNER
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import (
    CLOSURES,
    closure_path,
    patch,
    post,
    unique_year,
)
from tests.integration.conftest import run_sql, run_sql_async
from tests.integration.invitation_support import sign_in

TASK = jobs.MATERIALIZE_SERIES_TASK


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def cookie(client: TestClient, owner: CreatedUser) -> Any:
    return sign_in(client, owner.username)


@pytest.fixture(autouse=True)
def no_queued_job(module_seeded_url: str) -> None:
    run_sql(
        module_seeded_url,
        "DELETE FROM procrastinate_jobs WHERE task_name = :task",
        {"task": TASK},
    )


def queued(url: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, status::text AS status, queueing_lock, args"
        " FROM procrastinate_jobs WHERE task_name = :task ORDER BY id",
        {"task": TASK},
    )


def closure_body(year: int, name: str | None = None) -> dict[str, Any]:
    return {
        "name": name or f"Cierre {uuid.uuid4().hex[:8]}",
        "closure_type": "holiday",
        "starts_on": f"{year}-03-10",
        "ends_on": f"{year}-03-11",
    }


def create(client: TestClient, cookie: Any, **changes: Any) -> dict[str, Any]:
    body = {**closure_body(unique_year()), **changes}
    response = post(client, cookie, CLOSURES, body)
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def test_a_created_closure_queues_one_job_after_the_commit(
    module_seeded_url, client, cookie, monkeypatch
):
    seen: list[bool] = []
    real = periods_router.defer_series_materialization
    body = closure_body(unique_year())

    async def _check_then_defer(job_app: Any) -> None:
        # Another connection already sees the closure: it was committed.
        rows = await run_sql_async(
            module_seeded_url,
            "SELECT id FROM closures WHERE name = :name",
            {"name": body["name"]},
        )
        seen.append(len(rows) == 1)
        await real(job_app)

    monkeypatch.setattr(
        periods_router, "defer_series_materialization", _check_then_defer
    )

    response = post(client, cookie, CLOSURES, body)

    assert response.status_code == 201, response.text
    assert seen == [True]
    [job] = queued(module_seeded_url)
    assert job.status == "todo"
    assert job.queueing_lock == jobs.MATERIALIZE_SERIES_QUEUEING_LOCK
    assert set(job.args) == {"timestamp"}


@pytest.mark.parametrize(
    "changes",
    [
        {"starts_on": "{year}-03-09"},
        {"ends_on": "{year}-03-12"},
        {"is_active": False},
    ],
)
def test_a_date_or_activity_change_queues_a_job(
    module_seeded_url, client, cookie, changes
):
    year = unique_year()
    created = create(client, cookie, **closure_body(year))
    run_sql(
        module_seeded_url,
        "DELETE FROM procrastinate_jobs WHERE task_name = :task",
        {"task": TASK},
    )
    body = {
        key: value.format(year=year) if isinstance(value, str) else value
        for key, value in changes.items()
    }

    response = patch(
        client,
        cookie,
        closure_path(created["id"]),
        {"version": created["version"], **body},
    )

    assert response.status_code == 200, response.text
    assert len(queued(module_seeded_url)) == 1


def test_a_rename_or_an_unchanged_date_queues_nothing(
    module_seeded_url, client, cookie
):
    created = create(client, cookie)
    run_sql(
        module_seeded_url,
        "DELETE FROM procrastinate_jobs WHERE task_name = :task",
        {"task": TASK},
    )

    renamed = patch(
        client,
        cookie,
        closure_path(created["id"]),
        {"version": created["version"], "name": f"Otro {uuid.uuid4().hex[:8]}"},
    )
    same = patch(
        client,
        cookie,
        closure_path(created["id"]),
        {"version": renamed.json()["version"], "starts_on": created["starts_on"]},
    )

    assert renamed.status_code == 200, renamed.text
    assert same.status_code == 200, same.text
    assert queued(module_seeded_url) == []


def test_two_writes_back_to_back_queue_one_job(module_seeded_url, client, cookie):
    create(client, cookie)
    create(client, cookie)

    assert len(queued(module_seeded_url)) == 1


def test_a_failing_deferral_still_answers_and_stores(
    module_seeded_url, client, cookie, monkeypatch, caplog
):
    def _unreachable(*args: Any, **kwargs: Any) -> Any:
        raise OSError("the database is unreachable")

    monkeypatch.setattr(client.app.state.job_app, "configure_task", _unreachable)
    body = closure_body(unique_year())

    with caplog.at_level("ERROR", logger=jobs.__name__):
        response = post(client, cookie, CLOSURES, body)

    assert response.status_code == 201, response.text
    stored = run_sql(
        module_seeded_url,
        "SELECT id FROM closures WHERE name = :name",
        {"name": body["name"]},
    )
    assert len(stored) == 1
    assert queued(module_seeded_url) == []
    [record] = [
        r
        for r in caplog.records
        if r.message == "Deferring the series materialization failed"
    ]
    assert record.error == "OSError"

    created = response.json()
    with caplog.at_level("ERROR", logger=jobs.__name__):
        updated = patch(
            client,
            cookie,
            closure_path(created["id"]),
            {"version": created["version"], "is_active": False},
        )
    assert updated.status_code == 200, updated.text
    assert updated.json()["is_active"] is False
