"""The failed jobs routes (#128 D1-D4, E3) over HTTP.

Jobs and failures are inserted with raw SQL (no worker runs here; the real
worker path is ``test_failed_jobs_retry.py``). Each test starts from an empty
job table (deleting a job cascades to its ``job_failures`` row).
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.pagination import CursorParams
from app.modules.administration import jobs as service
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.authz.csrf import ORIGIN_HEADER
from tests.integration.auth_support import cookie_header, signed_headers
from tests.integration.conftest import run_sql
from tests.integration.export_support import Actor, actor

JOBS: Final = "/api/v1/jobs"
TASKS: Final = "/api/v1/jobs/tasks"
CHARGES: Final = "billing:post_session_charges"
CHARGES_LOCK: Final = "billing-post-session-charges"
EXPIRY: Final = "billing:expire_packages"
DENIED: Final = (ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN)
ITEM_FIELDS: Final = {
    "id",
    "task_name",
    "queue_name",
    "status",
    "attempts",
    "error_class",
    "error_code",
    "sqlstate",
    "deferred_at",
    "failed_at",
    "retried_count",
    "last_retried_at",
    "retryable",
}
T0: Final = datetime(2031, 5, 4, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    from tests.integration.auth_support import auth_client

    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def empty_jobs(module_seeded_url: str) -> Iterator[None]:
    run_sql(module_seeded_url, "DELETE FROM procrastinate_jobs")
    yield
    run_sql(module_seeded_url, "DELETE FROM procrastinate_jobs")


@pytest.fixture(scope="module")
def admin(module_seeded_url: str, client: TestClient) -> Actor:
    return actor(module_seeded_url, client, ADMINISTRATOR)


def code_of(response: Any) -> tuple[int, str | None]:
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json().get("code")


def get(client: TestClient, who: Actor, path: str, **params: Any) -> Any:
    client.cookies.clear()
    return client.get(path, params=params, headers=signed_headers(who.cookie))


def retry(client: TestClient, who: Actor, job_id: int) -> Any:
    client.cookies.clear()
    return client.post(f"{JOBS}/{job_id}/retry", headers=signed_headers(who.cookie))


def insert_job(
    url: str,
    task: str = CHARGES,
    *,
    status: str = "failed",
    attempts: int = 1,
    queueing_lock: str | None = None,
    args: str = '{"timestamp": 1}',
) -> int:
    """A job inserted ``todo`` (Procrastinate's trigger writes the
    ``deferred`` event), then moved to ``status`` through ``doing``."""
    [row] = run_sql(
        url,
        "INSERT INTO procrastinate_jobs (queue_name, task_name, queueing_lock,"
        " args, attempts) VALUES ('default', :t, :q, CAST(:a AS jsonb), :n)"
        " RETURNING id",
        {"t": task, "q": queueing_lock, "a": args, "n": attempts},
    )
    job_id = int(row.id)
    path = {"todo": [], "doing": ["doing"]}.get(status, ["doing", status])
    for step in path:
        run_sql(
            url,
            "UPDATE procrastinate_jobs"
            " SET status = CAST(:s AS procrastinate_job_status) WHERE id = :j",
            {"s": step, "j": job_id},
        )
    return job_id


def insert_failure(
    url: str,
    job_id: int,
    task: str = CHARGES,
    *,
    failed_at: datetime = T0,
    error_class: str | None = "RuntimeError",
    error_code: str | None = None,
    sqlstate: str | None = None,
) -> None:
    run_sql(
        url,
        "INSERT INTO job_failures (job_id, task_name, error_class, error_code,"
        " sqlstate, failed_at) VALUES (:j, :t, :c, :e, :s, :f)",
        {
            "j": job_id,
            "t": task,
            "c": error_class,
            "e": error_code,
            "s": sqlstate,
            "f": failed_at,
        },
    )


def failed_job(url: str, task: str = CHARGES, **kw: Any) -> int:
    queueing_lock = kw.pop("queueing_lock", None)
    job_id = insert_job(url, task, queueing_lock=queueing_lock)
    insert_failure(url, job_id, task, **kw)
    return job_id


def job_state(url: str, job_id: int) -> tuple[str, int]:
    [row] = run_sql(
        url,
        "SELECT status::text AS status, attempts FROM procrastinate_jobs WHERE id = :j",
        {"j": job_id},
    )
    return row.status, row.attempts


def retried_events(url: str, job_id: int) -> list[Any]:
    return run_sql(
        url,
        "SELECT action, actor_user_id, details FROM audit_events"
        " WHERE action = 'administration.job_retried'"
        " AND (details ->> 'job_id')::bigint = :j",
        {"j": job_id},
    )


def ids(body: dict[str, Any]) -> list[int]:
    return [item["id"] for item in body["items"]]


# --- E3: access ----------------------------------------------------------------------


def test_anonymous_is_401_and_a_post_without_csrf_is_403(module_seeded_url, client):
    client.cookies.clear()
    assert code_of(client.get(JOBS)) == (401, "authentication_required")
    assert code_of(client.get(TASKS)) == (401, "authentication_required")
    assert code_of(client.post(f"{JOBS}/1/retry", headers=ORIGIN_HEADER)) == (
        401,
        "authentication_required",
    )
    owner = actor(module_seeded_url, client, OWNER)
    job_id = failed_job(module_seeded_url)

    client.cookies.clear()
    response = client.post(
        f"{JOBS}/{job_id}/retry",
        headers={**ORIGIN_HEADER, **cookie_header(owner.cookie)},
    )

    assert code_of(response) == (403, "csrf_failed")
    assert job_state(module_seeded_url, job_id) == ("failed", 1)


def test_owner_and_administrator_may_list_and_retry(module_seeded_url, client, admin):
    owner = actor(module_seeded_url, client, OWNER)
    for who in (owner, admin):
        job_id = failed_job(module_seeded_url)
        assert code_of(get(client, who, JOBS)) == (200, None)
        assert code_of(get(client, who, TASKS)) == (200, None)
        assert code_of(retry(client, who, job_id)) == (202, None)


@pytest.mark.parametrize("role", DENIED)
def test_every_other_role_is_403(module_seeded_url, client, role):
    who = actor(module_seeded_url, client, role)
    job_id = failed_job(module_seeded_url)

    assert code_of(get(client, who, JOBS)) == (403, "permission_denied")
    assert code_of(get(client, who, TASKS)) == (403, "permission_denied")
    assert code_of(retry(client, who, job_id)) == (403, "permission_denied")
    assert job_state(module_seeded_url, job_id) == ("failed", 1)
    assert retried_events(module_seeded_url, job_id) == []


# --- D1, D2: the list ----------------------------------------------------------------


def test_the_list_is_newest_failure_first_with_ties_by_job_id(
    module_seeded_url, client, admin
):
    url = module_seeded_url
    older = failed_job(url, failed_at=T0 - timedelta(hours=1))
    tie_low = failed_job(url, failed_at=T0)
    tie_high = failed_job(url, failed_at=T0)
    newest = failed_job(url, failed_at=T0 + timedelta(hours=1))

    first = get(client, admin, JOBS, limit=2).json()
    second = get(client, admin, JOBS, limit=2, cursor=first["next_cursor"]).json()

    assert ids(first) == [newest, tie_high]
    assert ids(second) == [tie_low, older]
    assert second["next_cursor"] is None
    assert code_of(get(client, admin, JOBS, cursor="not-a-cursor")) == (
        400,
        "invalid_cursor",
    )


def test_an_item_has_exactly_the_safe_fields(module_seeded_url, client, admin):
    url = module_seeded_url
    secret = str(uuid.uuid4())
    job_id = insert_job(url, args=f'{{"timestamp": 1, "note": "{secret}"}}')
    insert_failure(
        url, job_id, error_class="IntegrityError", error_code=None, sqlstate="23505"
    )

    response = get(client, admin, JOBS)

    assert response.status_code == 200
    [item] = response.json()["items"]
    assert set(item) == ITEM_FIELDS
    assert secret not in response.text
    assert item["id"] == job_id
    assert item["task_name"] == CHARGES
    assert item["queue_name"] == "default"
    assert (item["status"], item["attempts"]) == ("failed", 1)
    assert (item["error_class"], item["error_code"], item["sqlstate"]) == (
        "IntegrityError",
        None,
        "23505",
    )
    assert item["deferred_at"] is not None
    assert datetime.fromisoformat(item["failed_at"]) == T0
    assert (item["retried_count"], item["last_retried_at"]) == (0, None)
    assert item["retryable"] is True


def test_filters_by_status_task_and_failed_time(module_seeded_url, client, admin):
    url = module_seeded_url
    failed_charge = failed_job(url, failed_at=T0)
    failed_expiry = failed_job(url, EXPIRY, failed_at=T0 + timedelta(days=1))
    succeeded = insert_job(url, EXPIRY, status="succeeded")
    insert_failure(url, succeeded, EXPIRY, failed_at=T0 - timedelta(days=1))
    never_failed = insert_job(url, status="failed")

    assert ids(get(client, admin, JOBS).json()) == [failed_expiry, failed_charge]
    assert ids(get(client, admin, JOBS, status="any").json()) == [
        failed_expiry,
        failed_charge,
        succeeded,
    ]
    assert ids(get(client, admin, JOBS, status="succeeded").json()) == [succeeded]
    assert ids(get(client, admin, JOBS, task=EXPIRY).json()) == [failed_expiry]
    assert ids(get(client, admin, JOBS, task="billing:no_such_task").json()) == []
    window = {"from": T0.isoformat(), "to": (T0 + timedelta(days=1)).isoformat()}
    assert ids(get(client, admin, JOBS, status="any", **window).json()) == [
        failed_charge
    ]
    assert never_failed not in ids(get(client, admin, JOBS, status="any").json())
    # The succeeded job is never retryable.
    [item] = get(client, admin, JOBS, status="succeeded").json()["items"]
    assert item["retryable"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"task": "Billing:charges"},
        {"task": "no-colon"},
        {"task": "a:" + "b" * 127},
        {"status": "lost"},
        {"from": "2031-05-04T12:00:00"},
        {"from": T0.isoformat(), "to": T0.isoformat()},
        {"from": T0.isoformat(), "to": (T0 - timedelta(seconds=1)).isoformat()},
    ],
)
def test_invalid_filters_are_422(client, admin, params):
    assert code_of(get(client, admin, JOBS, **params)) == (422, "validation_error")


def test_a_page_is_two_statements_whatever_its_size(module_seeded_url):
    # The rows, then one aggregated statement for the event times (#128 D2).
    from tests.integration.catalog_support import count_statements

    url = module_seeded_url
    for offset in range(5):
        failed_job(url, failed_at=T0 + timedelta(minutes=offset))

    def page(session: Any) -> Any:
        return service.list_failed_jobs(
            session,
            service.JobFilters(),
            CursorParams(25, None),
            registered=frozenset({CHARGES}),
        )

    count, result = count_statements(url, page)

    assert count == 2
    assert len(result.items) == 5
    assert all(item.deferred_at is not None for item in result.items)


def test_the_list_plan_never_scans_the_job_table(module_seeded_url):
    url = module_seeded_url
    run_sql(
        url,
        "INSERT INTO procrastinate_jobs (queue_name, task_name, status)"
        " SELECT 'default', 'billing:expire_packages', 'succeeded'"
        " FROM generate_series(1, 1980)",
    )
    for offset in range(20):
        failed_job(url, failed_at=T0 + timedelta(minutes=offset))
    run_sql(url, "ANALYZE procrastinate_jobs")
    run_sql(url, "ANALYZE job_failures")
    statements = [
        service.list_jobs_statement(service.JobFilters(), CursorParams(25, None)),
        service.list_jobs_statement(
            service.JobFilters(task=CHARGES, status="any"), CursorParams(25, None)
        ),
    ]
    for statement in statements:
        sql = str(
            statement.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        plan = "\n".join(
            row[0]
            for row in run_sql(url, f"EXPLAIN {sql}")  # noqa: S608
        )

        assert "Seq Scan on procrastinate_jobs" not in plan, plan
        assert "procrastinate_jobs_pkey" in plan, plan


# --- D3: tasks -----------------------------------------------------------------------


def test_the_tasks_are_the_registered_ones_sorted(client, admin):
    from app.core import jobs

    body = get(client, admin, TASKS).json()
    names = [item["task_name"] for item in body["items"]]

    assert names == sorted(names)
    assert CHARGES in names
    assert not any(name.startswith("builtin:") for name in names)
    assert set(names) <= jobs.RETRYABLE_TASKS | jobs.NON_RETRYABLE_TASKS
    assert all(
        item["retryable"] == (item["task_name"] in jobs.RETRYABLE_TASKS)
        for item in body["items"]
    )


# --- D4: retry -----------------------------------------------------------------------


def test_a_retry_requeues_the_job_and_audits_it(module_seeded_url, client, admin):
    url = module_seeded_url
    job_id = failed_job(url, queueing_lock=CHARGES_LOCK)

    response = retry(client, admin, job_id)

    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == ITEM_FIELDS
    assert (body["id"], body["status"], body["attempts"]) == (job_id, "todo", 2)
    assert body["retryable"] is False
    assert body["retried_count"] == 1
    assert body["last_retried_at"] is not None
    assert job_state(url, job_id) == ("todo", 2)
    [event] = retried_events(url, job_id)
    assert event.actor_user_id == admin.user.id
    assert event.details == {"job_id": job_id, "task_name": CHARGES, "attempts": 1}
    # Listed again with status=any, with its retry.
    [item] = get(client, admin, JOBS, status="any").json()["items"]
    assert (item["status"], item["retried_count"]) == ("todo", 1)
    # A double click.
    assert code_of(retry(client, admin, job_id)) == (409, "job_not_failed")
    assert len(retried_events(url, job_id)) == 1


def test_a_job_that_is_missing_or_never_failed_is_404(module_seeded_url, client, admin):
    never_failed = insert_job(module_seeded_url)

    assert code_of(retry(client, admin, never_failed)) == (404, "not_found")
    assert code_of(retry(client, admin, never_failed + 10_000)) == (404, "not_found")
    assert job_state(module_seeded_url, never_failed) == ("failed", 1)


@pytest.mark.parametrize("job_id", ["0", "-1", "abc", str(2**63)])
def test_an_invalid_job_id_is_422(client, admin, job_id):
    client.cookies.clear()
    response = client.post(
        f"{JOBS}/{job_id}/retry", headers=signed_headers(admin.cookie)
    )

    assert code_of(response) == (422, "validation_error")


def test_the_conflicts_come_in_order(module_seeded_url, client, admin, monkeypatch):
    url = module_seeded_url
    # A task no longer registered, and not failed: job_not_retryable first.
    removed = insert_job(url, "legacy:removed_task", status="succeeded")
    insert_failure(url, removed, "legacy:removed_task")
    # Retryable but not failed.
    todo = insert_job(url, EXPIRY, status="todo")
    insert_failure(url, todo, EXPIRY)
    # Not classified as retryable (monkeypatched) even though failed.
    unclassified = failed_job(url, EXPIRY)

    assert code_of(retry(client, admin, removed)) == (409, "job_not_retryable")
    assert code_of(retry(client, admin, todo)) == (409, "job_not_failed")
    monkeypatch.setattr(service, "RETRYABLE_TASKS", frozenset({CHARGES}))
    assert code_of(retry(client, admin, unclassified)) == (409, "job_not_retryable")
    [item] = [
        i
        for i in get(client, admin, JOBS, status="any").json()["items"]
        if i["id"] == unclassified
    ]
    assert item["retryable"] is False
    for job_id in (removed, todo, unclassified):
        assert retried_events(url, job_id) == []


def test_a_queued_run_with_the_same_lock_is_409(module_seeded_url, client, admin):
    url = module_seeded_url
    job_id = failed_job(url, queueing_lock=CHARGES_LOCK)
    insert_job(url, status="todo", queueing_lock=CHARGES_LOCK)

    assert code_of(retry(client, admin, job_id)) == (409, "job_already_queued")
    assert job_state(url, job_id) == ("failed", 1)
    assert retried_events(url, job_id) == []


def test_losing_the_race_on_the_lock_index_is_409_without_audit(
    module_seeded_url, client, admin, monkeypatch
):
    # The pre-check misses a tick that queued in between: the unique index
    # refuses the retry, and the audit event rolls back with it.
    url = module_seeded_url
    job_id = failed_job(url, queueing_lock=CHARGES_LOCK)
    insert_job(url, status="todo", queueing_lock=CHARGES_LOCK)

    async def missed(*_args: Any) -> bool:
        return False

    monkeypatch.setattr(service, "queued_with_lock", missed)

    assert code_of(retry(client, admin, job_id)) == (409, "job_already_queued")
    assert job_state(url, job_id) == ("failed", 1)
    assert retried_events(url, job_id) == []
