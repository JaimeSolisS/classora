"""The retention job (#129 C2-C6) against the module database:
``administration.retention.run_retention_with`` with the wired rules.

The 30-day window boundary per kind with the children and their ``delete``
audit events (no actor), a purge that skips a row a restore holds, the #316
placeholder guard, finished-job pruning (``failed``, ``todo`` and ``doing``
kept; events cascaded; periodic defers unlinked), protected tables
untouched, and the summary line, the ``administration.retention_run`` audit
event and an idempotent rerun.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.db import create_sessionmaker
from app.modules.administration import retention
from app.modules.identity.permissions import ACADEMIC_COORDINATOR
from app.wiring import register_approval_wiring
from app.workflows import progress_report_schedule_tasks as placeholder_tasks
from tests.conftest import FrozenClock, frozen_clock
from tests.factories import billing as billing_factories
from tests.integration.auth_support import auth_client, create_user
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import events, in_uow
from tests.integration.report_schedule_support import (
    LAST_MONDAY,
    insert_report,
    insert_schedule,
    placeholders,
)
from tests.integration.report_schedule_support import teaching as report_teaching
from tests.integration.test_deleted_records import TABLES, deleted

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
WINDOW = timedelta(days=30)

CHILDREN = {
    "evaluation": [("evaluation_scores", "evaluation_id")],
    "learning_plan": [
        ("learning_plan_milestones", "plan_id"),
        ("learning_plan_objectives", "plan_id"),
    ],
    "assignment": [("assignment_submissions", "assignment_id")],
}


@pytest.fixture(scope="module")
def frozen() -> Any:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Any:
    frozen.set(NOW)
    yield frozen
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Any:
    register_approval_wiring()
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(client: Any, module_seeded_url: str) -> Any:
    coordinator = create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))
    return sign_in(client, coordinator.username)


def run(url: str, now: datetime = NOW) -> retention.RetentionResult:
    async def _run() -> retention.RetentionResult:
        engine = short_lived_engine(url)
        try:
            return await retention.run_retention_with(
                create_sessionmaker(engine), now=now
            )
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def set_deleted_at(url: str, kind: str, record_id: Any, at: datetime) -> None:
    run_sql(
        url,
        f"UPDATE {TABLES[kind]} SET deleted_at = :at WHERE id = :id",  # noqa: S608
        {"at": at, "id": record_id},
    )


def exists(url: str, table: str, record_id: Any) -> bool:
    return bool(
        run_sql(url, f"SELECT 1 FROM {table} WHERE id = :id", {"id": record_id})  # noqa: S608
    )


def children(url: str, kind: str, record_id: Any) -> dict[str, list[Any]]:
    return {
        table: [
            r.id
            for r in run_sql(
                url,
                f"SELECT id FROM {table} WHERE {column} = :id",  # noqa: S608
                {"id": record_id},
            )
        ]
        for table, column in CHILDREN.get(kind, [])
    }


# --- C3: the window and the children --------------------------------------------------


def test_the_window_boundary_and_the_children(module_seeded_url, client, staff):
    url = module_seeded_url
    purged: dict[str, Any] = {}
    kept: dict[str, Any] = {}
    for kind in TABLES:
        purged[kind] = deleted(client, url, staff, kind)
        set_deleted_at(url, kind, purged[kind].id, NOW - WINDOW)
        kept[kind] = deleted(client, url, staff, kind)
        set_deleted_at(url, kind, kept[kind].id, NOW - WINDOW + timedelta(hours=1))
    owned = {kind: children(url, kind, purged[kind].id) for kind in TABLES}
    assert all(ids for found in owned.values() for ids in found.values())

    result = run(url)

    assert result.failed == ()
    assert result.counts["academics.deleted_records"] >= len(TABLES)
    for kind, table in TABLES.items():
        record_id = uuid.UUID(purged[kind].id)
        assert not exists(url, table, record_id), kind
        assert exists(url, table, kept[kind].id), kind
        [event] = events(url, action="delete", record_id=record_id)
        assert (event.record_type, event.actor_user_id) == (table, None)
        for child_table, ids in owned[kind].items():
            assert children(url, kind, record_id)[child_table] == []
            for child_id in ids:
                [child] = events(url, action="delete", record_id=child_id)
                assert (child.record_type, child.actor_user_id) == (child_table, None)


def test_a_row_a_restore_holds_is_skipped(module_seeded_url, client, staff):
    url = module_seeded_url
    record = deleted(client, url, staff, "session_note")
    set_deleted_at(url, "session_note", record.id, NOW - WINDOW - timedelta(days=1))

    async def _held() -> int:
        holder = short_lived_engine(url)
        try:
            async with holder.connect() as connection, connection.begin():
                await connection.exec_driver_sql(
                    "SELECT id FROM session_notes WHERE id = $1 FOR UPDATE",
                    (uuid.UUID(record.id),),
                )
                engine = short_lived_engine(url)
                try:
                    result = await retention.run_retention_with(
                        create_sessionmaker(engine), now=NOW
                    )
                finally:
                    await engine.dispose()
                return result.counts["academics.deleted_records"]
        finally:
            await holder.dispose()

    asyncio.run(_held())

    assert exists(url, "session_notes", record.id)
    run(url)
    assert not exists(url, "session_notes", record.id)


def test_a_deleted_placeholder_waits_for_a_later_period(module_seeded_url, client):
    url = module_seeded_url
    tutor, student, _ = report_teaching(url)
    schedule = insert_schedule(url, student=student, author=tutor.id)
    report = insert_report(
        url,
        student=student,
        author=tutor.id,
        period_start=LAST_MONDAY,
        schedule=schedule,
        deleted=True,
    )
    set_deleted_at(url, "progress_report", report, NOW - WINDOW - timedelta(days=10))

    # At NOW (Wednesday 5 March) the week of 24 February is still the latest
    # ended week, whose placeholder the job would create again.
    run(url)
    assert exists(url, "progress_reports", report)

    later = NOW + timedelta(days=7)
    run(url, later)
    assert not exists(url, "progress_reports", report)
    asyncio.run(
        placeholder_tasks.run_progress_report_placeholders(settings_for(url), now=later)
    )
    assert LAST_MONDAY not in {r.period_start for r in placeholders(url, schedule)}


# --- C4: finished jobs ----------------------------------------------------------------


def job(url: str, status: str, at: datetime, task: str = "demo:succeed") -> int:
    [found] = run_sql(
        url,
        "INSERT INTO procrastinate_jobs (queue_name, task_name, status)"
        " VALUES ('default', :t, CAST(:s AS procrastinate_job_status)) RETURNING id",
        {"t": task, "s": status},
    )
    job_id = int(found.id)
    run_sql(url, "DELETE FROM procrastinate_events WHERE job_id = :j", {"j": job_id})
    run_sql(
        url,
        "INSERT INTO procrastinate_events (job_id, type, at)"
        " VALUES (:j, 'deferred', :early), (:j, 'started', :at)",
        {"j": job_id, "early": at - timedelta(hours=1), "at": at},
    )
    return job_id


def failure(url: str, job_id: int) -> None:
    run_sql(
        url,
        "INSERT INTO job_failures (job_id, task_name, error_class)"
        " VALUES (:j, 'demo:succeed', 'RuntimeError')",
        {"j": job_id},
    )


def jobs_left(url: str, ids: list[int]) -> set[int]:
    return {
        r.id
        for r in run_sql(
            url, "SELECT id FROM procrastinate_jobs WHERE id = ANY(:ids)", {"ids": ids}
        )
    }


def test_finished_jobs_are_pruned_after_30_days(module_seeded_url, client):
    url = module_seeded_url
    old = NOW - WINDOW - timedelta(minutes=1)
    recent = NOW - WINDOW + timedelta(minutes=1)
    gone = [job(url, s, old) for s in ("succeeded", "cancelled", "aborted")]
    once_failed = job(url, "succeeded", old)
    failure(url, once_failed)
    failed = job(url, "failed", old)
    failure(url, failed)
    kept = [
        job(url, "succeeded", recent),
        failed,
        job(url, "todo", old),
        job(url, "doing", old),
    ]
    periodic = gone[0]
    run_sql(
        url,
        "INSERT INTO procrastinate_periodic_defers"
        " (task_name, periodic_id, defer_timestamp, job_id)"
        " VALUES ('demo:periodic', 'retention-test', 1, :j)",
        {"j": periodic},
    )

    result = run(url)

    assert result.counts["jobs.finished_jobs"] >= 4
    assert jobs_left(url, [*gone, once_failed, *kept]) == set(kept)
    assert not run_sql(
        url,
        "SELECT 1 FROM procrastinate_events WHERE job_id = ANY(:ids)",
        {"ids": [*gone, once_failed]},
    )
    failures = {r.job_id for r in run_sql(url, "SELECT job_id FROM job_failures")}
    assert failed in failures
    assert once_failed not in failures
    [defer] = run_sql(
        url,
        "SELECT job_id FROM procrastinate_periodic_defers"
        " WHERE periodic_id = 'retention-test'",
    )
    assert defer.job_id is None
    # A later periodic defer still works.
    later = job(url, "todo", NOW)
    run_sql(
        url,
        "INSERT INTO procrastinate_periodic_defers"
        " (task_name, periodic_id, defer_timestamp, job_id)"
        " VALUES ('demo:periodic', 'retention-test', 2, :j)",
        {"j": later},
    )


# --- C5: protected data ---------------------------------------------------------------

EXTRA_KEPT = ("class_sessions", "class_session_participants", "students", "users")


def counts(url: str, tables: list[str]) -> dict[str, int]:
    return {
        table: run_sql(url, f"SELECT count(*) AS n FROM {table}")[0].n  # noqa: S608
        for table in tables
    }


def failed_job_failures(url: str) -> int:
    [found] = run_sql(
        url,
        "SELECT count(*) AS n FROM job_failures f"
        " JOIN procrastinate_jobs j ON j.id = f.job_id WHERE j.status = 'failed'",
    )
    return int(found.n)


def test_protected_data_is_never_touched(module_seeded_url, client, staff):
    url = module_seeded_url

    async def _money(session: Any) -> None:
        family, (student,) = await billing_factories.family_with_students(session)
        account = await billing_factories.billing_account(session, family)
        posted = await billing_factories.charge(session, student)
        by = await billing_factories.staff_user_id(session)
        paid = await billing_factories.payment(
            session, account, "500.00", recorded_by=by
        )
        await billing_factories.allocation(session, paid, posted.charge_id, "500.00")

    in_uow(url, _money)
    deleted(client, url, staff, "evaluation")
    job(url, "failed", NOW - timedelta(days=3650))
    tables = sorted(
        (retention.protected_tables() - {"job_failures", "audit_events"})
        | set(EXTRA_KEPT)
    )
    before = counts(url, tables)
    assert before["charges"] > 0
    assert before["payments"] > 0
    [audit_before] = run_sql(url, "SELECT count(*) AS n FROM audit_events")
    failures_before = failed_job_failures(url)

    result = run(url, NOW + timedelta(days=3650))

    assert result.failed == ()
    assert result.total > 0
    assert counts(url, tables) == before
    assert failed_job_failures(url) == failures_before
    [audit_after] = run_sql(url, "SELECT count(*) AS n FROM audit_events")
    assert audit_after.n > audit_before.n  # delete events added, none removed


# --- C6: the summary ------------------------------------------------------------------


def test_the_summary_line_one_audit_event_and_an_idempotent_rerun(
    module_seeded_url, client, staff, caplog
):
    url = module_seeded_url
    record = deleted(client, url, staff, "learning_plan")
    set_deleted_at(url, "learning_plan", record.id, NOW - WINDOW)
    later = NOW + timedelta(days=1)
    caplog.set_level(logging.INFO, logger=retention.__name__)
    before = len(events(url, action=retention.AUDIT_ACTION))
    caplog.clear()

    first = run(url, later)

    assert first.counts["academics.deleted_records"] >= 1
    summaries = [r for r in caplog.records if r.message == retention.RUN_COMPLETED]
    [summary] = summaries
    assert summary.levelno == logging.INFO
    assert summary.counts == dict(sorted(first.counts.items()))
    assert summary.failed_rules == []
    assert isinstance(summary.duration_ms, int)
    # What the run logged (the retention logger only; the test's own HTTP
    # request lines name the record and are not the run's): no id or value.
    logged = [r for r in caplog.records if r.name == retention.__name__]
    assert logged
    for entry in logged:
        assert record.id not in entry.getMessage()
        assert record.id not in repr(vars(entry))
    runs = events(url, action=retention.AUDIT_ACTION)
    assert len(runs) == before + 1
    assert (runs[-1].actor_user_id, runs[-1].record_type) == (None, None)
    assert runs[-1].details == {"counts": dict(sorted(first.counts.items()))}

    second = run(url, later)

    assert second.total == 0
    assert len(events(url, action=retention.AUDIT_ACTION)) == before + 1
