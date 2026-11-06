"""Background jobs on Procrastinate (spec §10, architecture §6, ADR-0003).

This module is the one place that builds the Procrastinate :class:`App` and its
connectors. Task modules expose ``register(app)``; :func:`create_job_app`
calls them, so the API process and the ``worker`` container see the same
tasks. Like ``app.core.model_registry``, it is the registry that imports the
modules' task files (lazily, inside :func:`create_job_app`), and the
workflow task files (``app.workflows.progress_report_schedule_tasks``, #316,
``app.workflows.session_charge_tasks``, #91, and
``app.workflows.enrollment_charge_tasks``, #92: jobs that compose modules);
module code never imports another module's ``tasks`` (ADR-0002).

Rules for every job (ADR-0003, ISSUE-J1 to J4):

- **Enqueue after commit.** Business code defers a job only after its Unit of
  Work has committed. Deferring is not part of the business transaction (it
  uses another driver and connection), so a crash in between leaves work with
  no job; periodic sweepers find that work later (no outbox).
- **Idempotent jobs.** A job can run more than once (a retry, a sweeper, a
  worker stopped mid-job), so running it twice must not create anything twice.
- **IDs only (ISSUE-J3).** Task arguments are identifiers, never personal data
  or free text: each parameter is annotated with one of
  :data:`ALLOWED_ARGUMENT_TYPES` (periodic tasks also get Procrastinate's
  ``timestamp: int``). The job loads everything else from the database.
  Arguments are stored as JSON, so callers pass a UUID as ``str(value)``; wrap
  every task function in :func:`ids_only` to receive it as ``uuid.UUID``.
- **No psycopg imports in ``app/``.** psycopg 3 is used only by Procrastinate.
  Connection options are plain keyword arguments to ``PsycopgConnector`` here.
  Application and ORM code use SQLAlchemy with asyncpg.
- **Alembic owns the schema.** ``migrations/versions/0002_procrastinate_*.py``
  applies it. Nothing here, in the API or in the worker applies it.

Two connector setups:

- **API side** (:func:`api_connector`, ADR-0003 (a)): defers jobs through
  PgBouncer (``Settings.database_url``, transaction mode), with psycopg's
  server-side prepared statements off (``prepare_threshold=None``).
- **Worker side** (:func:`worker_connector`, ADR-0003 (b)): everything in the
  ``worker`` container connects directly to PostgreSQL
  (``Settings.database_direct_url``). A job that needs a SQLAlchemy session
  gets it from :func:`create_worker_engine`, never from the API's pooled
  engine.

Pool sizes are small fixed defaults; sizing for the hosts is M10-04.

**Cross-module triggers** (#232 B1): a task that another module defers has
its name here, so the deferring module never imports the owner's ``tasks``
or ``service`` (ADR-0002). :func:`defer_series_materialization` is the
closure writes' after-commit trigger of ``scheduling:materialize_series``;
:func:`defer_session_charges` and :func:`defer_enrollment_charges` the
billing settings ``PATCH``'s triggers of ``billing:post_session_charges``
(#91 B4) and ``billing:post_enrollment_charges`` (#92 D1);
:func:`defer_tuition_charges` the trigger of ``billing:post_tuition_charges``
after the settings ``PATCH`` and the tuition plan writes (#93 E1);
:func:`defer_schedule_reconciliation` the trigger of
``billing:reconcile_session_charges`` after the session-changing routes,
the settings ``PATCH`` and the series materialization (#99 D9);
:func:`defer_recurring_expenses` the trigger of
``finance:generate_recurring_expenses`` after a template create, reactivation
or ``ends_on`` change (#116 E8); :func:`defer_import_run` the trigger of
``administration:run_imports`` after an import commit request (#122 D1);
:func:`defer_export_run` the trigger of ``reporting:run_exports`` after an
export request (#124 B2).

**Failed jobs** (#128; architecture ISSUE-J3): Procrastinate stores no error
text, so the worker middleware :func:`record_job_failure` writes the
sanitized error of every failed run to ``job_failures``
(:func:`sanitize_job_error`: class name, application code, SQLSTATE; never
the message, traceback or arguments). Every registered task is classified in
:data:`RETRYABLE_TASKS` or :data:`NON_RETRYABLE_TASKS`; only a retryable one
can be retried from ``POST /api/v1/jobs/{job_id}/retry``.
"""

import functools
import inspect
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Final, get_type_hints

from fastapi import Request
from procrastinate import App, JobContext, PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued, JobAborted
from procrastinate.middleware import AsyncCallNext
from procrastinate.worker import Worker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import AppEnv, Settings
from app.core.db import async_direct_database_url, create_direct_engine
from app.core.errors import AppError
from app.core.job_failures import ERROR_CLASS_MAX_LENGTH, TASK_NAME_MAX_LENGTH

logger = logging.getLogger(__name__)

# Task parameters may only have these types (ISSUE-J3): identifiers, and the
# ``timestamp: int`` Procrastinate passes to periodic tasks.
ALLOWED_ARGUMENT_TYPES: Final[frozenset[type]] = frozenset({uuid.UUID, int})

# Environments that get the demo tasks, the periodic demo and the demo route.
DEMO_ENVIRONMENTS: Final[frozenset[AppEnv]] = frozenset({"local", "dev"})

API_APPLICATION_NAME = "classora-api-jobs"
WORKER_APPLICATION_NAME = "classora-worker"
WORKER_HEALTHCHECK_APPLICATION_NAME = "classora-worker-healthcheck"

# psycopg connect timeout (seconds), so an unreachable database fails fast.
CONNECT_TIMEOUT_SECONDS = 2

MATERIALIZE_SERIES_TASK: Final = "scheduling:materialize_series"
"""The nightly series materialization (#53 F3), also deferred after a
closure write (#232 B1)."""
MATERIALIZE_SERIES_QUEUEING_LOCK: Final = "scheduling-materialize-series"
"""At most one queued ``scheduling:materialize_series`` job (the periodic
tick shares it)."""

SESSION_CHARGES_TASK: Final = "billing:post_session_charges"
"""The 15-minute session charge sweeper (#91 C1), also deferred after a
``PATCH /api/v1/billing/settings`` that sets a date (#91 B4)."""
SESSION_CHARGES_QUEUEING_LOCK: Final = "billing-post-session-charges"
"""At most one queued ``billing:post_session_charges`` job (the periodic
tick shares it)."""

ENROLLMENT_CHARGES_TASK: Final = "billing:post_enrollment_charges"
"""The 15-minute enrollment charge sweeper (#92 D1), also deferred after a
``PATCH /api/v1/billing/settings`` that sets a date."""
ENROLLMENT_CHARGES_QUEUEING_LOCK: Final = "billing-post-enrollment-charges"
"""At most one queued ``billing:post_enrollment_charges`` job (the periodic
tick shares it)."""

TUITION_CHARGES_TASK: Final = "billing:post_tuition_charges"
"""The daily tuition charge job (#93 E1, K5), also deferred after a tuition
plan create, resume or extension and after a ``PATCH
/api/v1/billing/settings`` that sets a date."""
TUITION_CHARGES_QUEUEING_LOCK: Final = "billing-post-tuition-charges"
"""At most one queued ``billing:post_tuition_charges`` job (the periodic
tick shares it)."""

PAYROLL_OPEN_PERIODS_TASK: Final = "payroll:open_periods"
"""The daily job that opens tutors' payroll periods (#106 D). Nothing else
queues it (#106 E7)."""
PAYROLL_OPEN_PERIODS_QUEUEING_LOCK: Final = "payroll-open-periods"
"""At most one queued ``payroll:open_periods`` job."""

PAYROLL_POST_ADJUSTMENTS_TASK: Final = "payroll:post_adjustments"
"""The 15-minute job that posts work after lock as payroll adjustments (#111 B1).
Nothing else queues it (#111 E10)."""
PAYROLL_POST_ADJUSTMENTS_QUEUEING_LOCK: Final = "payroll-post-adjustments"
"""At most one queued ``payroll:post_adjustments`` job."""

PACKAGE_EXPIRY_TASK: Final = "billing:expire_packages"
"""The daily package expiry with its fallback consumption (#94 F1)."""
PACKAGE_EXPIRY_QUEUEING_LOCK: Final = "billing-expire-packages"
"""At most one queued ``billing:expire_packages`` job (the periodic tick
shares it)."""

SCHEDULE_RECONCILIATION_TASK: Final = "billing:reconcile_session_charges"
"""The 15-minute schedule reconciliation (#99 D1), also deferred after the
routes that change sessions, the billing settings ``PATCH`` and the series
materialization (D9)."""
SCHEDULE_RECONCILIATION_QUEUEING_LOCK: Final = "billing-reconcile-session-charges"
"""At most one queued ``billing:reconcile_session_charges`` job (the periodic
tick shares it)."""

CHARGE_REMINDERS_TASK: Final = "billing:send_charge_reminders"
"""The daily in-app payment reminders to families (#97 F2)."""
CHARGE_REMINDERS_QUEUEING_LOCK: Final = "billing-send-charge-reminders"
"""At most one queued ``billing:send_charge_reminders`` job (the periodic
tick shares it)."""

RECURRING_EXPENSES_TASK: Final = "finance:generate_recurring_expenses"
"""The daily recurring expense job (#116 D1), also deferred after a template
create, reactivation or ``ends_on`` change (#116 E8)."""
RECURRING_EXPENSES_QUEUEING_LOCK: Final = "finance-generate-recurring-expenses"
"""At most one queued ``finance:generate_recurring_expenses`` job (the
periodic tick shares it)."""

IMPORTS_TASK: Final = "administration:run_imports"
"""The 15-minute import run (#122 D2, E2): commits ``committing`` import jobs
in batches and expires stale ones; also deferred after ``POST
/api/v1/imports/{id}/commit``."""
IMPORTS_QUEUEING_LOCK: Final = "administration-run-imports"
"""At most one queued ``administration:run_imports`` job (the periodic tick
shares it)."""

EXPORTS_TASK: Final = "reporting:run_exports"
"""The 15-minute export run (#124 D1, D4): builds ``queued`` export jobs and
purges expired files; also deferred after ``POST /api/v1/exports``."""
EXPORTS_QUEUEING_LOCK: Final = "reporting-run-exports"
"""At most one queued ``reporting:run_exports`` job (the periodic tick shares
it)."""

RETENTION_TASK: Final = "administration:run_retention"
"""The daily retention run (#129 C2): purges deleted academic records after
their recovery window and finished background jobs. Nothing else queues
it."""
RETENTION_QUEUEING_LOCK: Final = "administration-run-retention"
"""At most one queued ``administration:run_retention`` job."""

# --- Retry classification (#128 C1, E2) --------------------------------------------

RETRYABLE_TASKS: Final[frozenset[str]] = frozenset(
    {
        # Every production task is a periodic sweeper whose only argument is
        # Procrastinate's ``timestamp`` (which none of them reads). Each run
        # recomputes its work from committed state, runs each item in its own
        # Unit of Work, and writes idempotently on a natural key or a status
        # guard, so a retry is the same as the next tick, only earlier (#128
        # E2 table):
        "identity:purge_auth_sessions",  # deletes expired rows
        "idempotency:purge_keys",  # deletes expired rows
        "ratelimit:purge_counters",  # deletes expired rows
        "scheduling:expire_offers",  # status guard: pending past respond_by
        MATERIALIZE_SERIES_TASK,  # series horizon + existing occurrences
        "academics:create_progress_report_placeholders",  # per-schedule key
        SESSION_CHARGES_TASK,  # uq_charges_session_student + anti-join
        ENROLLMENT_CHARGES_TASK,  # natural keys per enrollment kind
        TUITION_CHARGES_TASK,  # uq_charges_tuition_period
        PACKAGE_EXPIRY_TASK,  # natural key package_id (already_expired)
        CHARGE_REMINDERS_TASK,  # ON CONFLICT (charge_id, stage) DO NOTHING
        SCHEDULE_RECONCILIATION_TASK,  # decisions per (session, student)
        PAYROLL_OPEN_PERIODS_TASK,  # a rerun leaves the same periods (#106)
        PAYROLL_POST_ADJUSTMENTS_TASK,  # keys approval:<id> / cancellation:<id>
        RECURRING_EXPENSES_TASK,  # uq_expenses_template_period
        # Re-runs the sweeper over ``committing`` import jobs only (per-batch
        # dedup, an advisory lock per kind, the job status). It never resumes
        # a business-level ``failed`` import: that stays ``POST
        # /api/v1/imports/{id}/commit`` with the kind's permission (#128 C3).
        IMPORTS_TASK,
        # Builds ``queued`` exports only (``FOR UPDATE SKIP LOCKED``); a
        # business-level ``failed`` export stays failed (#128 C3).
        EXPORTS_TASK,
        # Each retention rule deletes only what has expired at the run's
        # ``now`` from committed state, in batches with ``SKIP LOCKED``; a
        # retry deletes what the failed run left (#129 C2).
        RETENTION_TASK,
        # Local/dev demo tasks: they only log (``demo:fail`` raises on purpose).
        "demo:succeed",
        "demo:fail",
        "demo:periodic",
    }
)
"""Tasks an administrator may retry (#128 C1).

A task is retryable only if (1) its arguments are only ``timestamp`` or ids,
(2) it recomputes its work from committed state, and (3) its writes are
idempotent on a natural key or a status guard. A retry re-runs the whole
task; it never replays one business item. Every registered task must be in
exactly one of :data:`RETRYABLE_TASKS` and :data:`NON_RETRYABLE_TASKS`
(pinned in ``tests/unit/test_job_rules.py``)."""

NON_RETRYABLE_TASKS: Final[frozenset[str]] = frozenset()
"""Tasks that must never be retried from the failed jobs page (#128 C1):
one whose arguments carry intent (an amount, a list, an item to replay) or
that writes without a natural key. None today."""

# --- Failure capture (#128 A1, A2) -------------------------------------------------

ERROR_CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
SQLSTATE_PATTERN: Final = re.compile(r"[0-9A-Z]{5}")

RECORD_JOB_FAILURE_QUERY: Final = """
INSERT INTO job_failures (job_id, task_name, error_class, error_code, sqlstate)
VALUES (%(job_id)s, %(task_name)s, %(error_class)s, %(error_code)s, %(sqlstate)s)
ON CONFLICT (job_id) DO UPDATE SET
    task_name = EXCLUDED.task_name,
    error_class = EXCLUDED.error_class,
    error_code = EXCLUDED.error_code,
    sqlstate = EXCLUDED.sqlstate,
    failed_at = now()
"""


def _sqlstate_of(exc: BaseException) -> str | None:
    for candidate in (exc, getattr(exc, "orig", None), exc.__cause__):
        if candidate is None:
            continue
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, str) and SQLSTATE_PATTERN.fullmatch(value):
                return value
    return None


def sanitize_job_error(exc: BaseException) -> tuple[str, str | None, str | None]:
    """``(error_class, error_code, sqlstate)`` of a failed run (#128 A2).

    - ``error_class``: ``type(exc).__qualname__``, at most 200 characters;
    - ``error_code``: ``exc.code`` when ``exc`` is an
      :class:`app.core.errors.AppError` and the code is a ``str`` matching
      ``^[a-z][a-z0-9_]{0,63}$``, else ``None`` (SQLAlchemy errors carry a
      ``code`` too: a documentation link id such as ``gkpj``, not an
      application code);
    - ``sqlstate``: the first five-character ``[0-9A-Z]{5}`` ``sqlstate`` or
      ``pgcode`` on ``exc``, ``exc.orig`` or ``exc.__cause__``, else ``None``.

    Never reads ``str(exc)``, ``exc.args`` or the traceback: driver messages
    quote key values (#128 E1).
    """
    error_class = type(exc).__qualname__[:ERROR_CLASS_MAX_LENGTH]
    code = exc.code if isinstance(exc, AppError) else None
    error_code = (
        code if isinstance(code, str) and ERROR_CODE_PATTERN.fullmatch(code) else None
    )
    return error_class, error_code, _sqlstate_of(exc)


async def record_job_failure(
    call_next: AsyncCallNext, context: JobContext, worker: Worker
) -> Any:
    """Worker middleware: record a failed run in ``job_failures`` (#128 A1).

    Passed as ``worker_middleware`` by :func:`app.worker.worker_options`. On
    an exception other than ``JobAborted`` (or a cancellation, which is not
    an ``Exception``) it upserts the job's row through the worker's own
    connector (the latest failure wins), then re-raises the **original**
    exception unchanged, so Procrastinate's status, retry strategy and
    logging are untouched. If the insert fails, its error class and the job
    id are logged at ``ERROR`` and the original exception is still raised.

    A ``TaskNotFound`` is raised before worker middleware runs, so it is
    never recorded (#128 A3).
    """
    try:
        return await call_next()
    except JobAborted:
        raise
    except Exception as exc:
        job_id = context.job.id
        try:
            error_class, error_code, sqlstate = sanitize_job_error(exc)
            await worker.app.connector.execute_query_async(
                RECORD_JOB_FAILURE_QUERY,
                job_id=job_id,
                task_name=context.job.task_name[:TASK_NAME_MAX_LENGTH],
                error_class=error_class,
                error_code=error_code,
                sqlstate=sqlstate,
            )
        except Exception as insert_error:  # noqa: BLE001 - never mask the run's error
            logger.error(
                "Recording the job failure failed",
                extra={"job_id": job_id, "error": type(insert_error).__name__},
            )
        raise


# --- Finished job pruning (#129 C4) ------------------------------------------------

FINISHED_JOB_RETENTION_DAYS: Final = 30
"""How long a succeeded, cancelled or aborted job is kept after its latest
event (#129 C4)."""
FINISHED_JOB_BATCH: Final = 5000
"""Jobs deleted per transaction."""
FINISHED_JOB_STATUSES: Final = ("succeeded", "cancelled", "aborted")
"""The statuses pruned: ``failed`` jobs (with their ``job_failures`` row),
``todo``, ``doing`` and ``aborting`` are kept."""
FINISHED_JOBS_RULE_CODE: Final = "jobs.finished_jobs"
FINISHED_JOBS_TABLES: Final = frozenset(
    {"procrastinate_jobs", "procrastinate_events", "job_failures"}
)
"""What the delete reaches: the jobs, their events (``ON DELETE CASCADE``)
and the ``job_failures`` row a succeeded job kept from an earlier failed
attempt (``ON DELETE CASCADE``). Procrastinate's unlink trigger clears
``procrastinate_periodic_defers.job_id``."""

PURGE_FINISHED_JOBS_QUERY: Final = """
WITH doomed AS (
    SELECT j.id
    FROM procrastinate_jobs j
    WHERE j.status IN ('succeeded', 'cancelled', 'aborted')
      AND (SELECT max(e.at) FROM procrastinate_events e WHERE e.job_id = j.id)
          < :cutoff
    ORDER BY j.id
    LIMIT :batch
    FOR UPDATE OF j SKIP LOCKED
)
DELETE FROM procrastinate_jobs WHERE id IN (SELECT id FROM doomed) RETURNING id
"""


async def purge_finished_jobs(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime
) -> int:
    """The ``jobs.finished_jobs`` retention rule (#129 C4): delete the jobs
    in :data:`FINISHED_JOB_STATUSES` whose latest event is more than
    :data:`FINISHED_JOB_RETENTION_DAYS` days before ``now``, at most
    :data:`FINISHED_JOB_BATCH` per transaction. ``procrastinate_jobs`` is
    not audited, so plain SQL is allowed. Returns the number deleted."""
    cutoff = now - timedelta(days=FINISHED_JOB_RETENTION_DAYS)
    total = 0
    while True:
        async with sessionmaker() as session, session.begin():
            result = await session.execute(
                text(PURGE_FINISHED_JOBS_QUERY),
                {"cutoff": cutoff, "batch": FINISHED_JOB_BATCH},
            )
            count = len(result.all())
        total += count
        if count < FINISHED_JOB_BATCH:
            return total


# Worker concurrency is fixed at 1 here; making it configurable and sizing it
# (with the pools below and mem_limit) is M10-04 (ISSUE-I8).
WORKER_CONCURRENCY = 1


def _pool_options(
    dsn: str, *, application_name: str, max_size: int, timeout: float
) -> dict[str, Any]:
    # min_size=0: opening the pool never connects, so the API starts (and the
    # worker waits) while the database is down.
    return {
        "conninfo": dsn,
        "min_size": 0,
        "max_size": max_size,
        "timeout": timeout,
        "name": application_name,
        "kwargs": {
            "application_name": application_name,
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        },
    }


def api_connector(settings: Settings) -> PsycopgConnector:
    """Connector for deferring jobs from the API: pooled URL, through PgBouncer.

    PgBouncer runs in transaction mode with prepared-statement tracking off,
    so psycopg must never prepare statements: ``prepare_threshold=None``.
    """
    options = _pool_options(
        settings.database_url.get_secret_value(),
        application_name=API_APPLICATION_NAME,
        max_size=2,
        timeout=5.0,
    )
    options["kwargs"]["prepare_threshold"] = None
    return PsycopgConnector(**options)


def worker_connector(settings: Settings) -> PsycopgConnector:
    """Connector for the worker: direct URL to PostgreSQL (never PgBouncer).

    Procrastinate's ``LISTEN`` connection is opened next to this pool with the
    same options.
    """
    return PsycopgConnector(
        **_pool_options(
            settings.database_direct_url.get_secret_value(),
            application_name=WORKER_APPLICATION_NAME,
            max_size=3,
            timeout=30.0,
        ),
    )


def worker_healthcheck_connector(settings: Settings) -> PsycopgConnector:
    """One-connection connector for the ``worker`` container healthcheck."""
    return PsycopgConnector(
        **_pool_options(
            settings.database_direct_url.get_secret_value(),
            application_name=WORKER_HEALTHCHECK_APPLICATION_NAME,
            max_size=1,
            timeout=5.0,
        ),
    )


def create_worker_engine(settings: Settings, **kwargs: Any) -> AsyncEngine:
    """SQLAlchemy engine for job code in the worker: direct URL (ADR-0003 (b)).

    Built with :func:`app.core.db.create_direct_engine`. The job disposes it
    when it ends (first user: ``identity:purge_auth_sessions``, #25).
    """
    return create_direct_engine(async_direct_database_url(settings), **kwargs)


def ids_only[**P, R](
    func: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Wrap an async task so its ``uuid.UUID`` parameters arrive as ``UUID``.

    Job arguments are stored as JSON, so a UUID comes back from the database as
    a string. The wrapper converts it back; ``inspect.signature`` and
    ``typing.get_type_hints`` still see the original parameters.
    """
    uuid_parameters = frozenset(
        name
        for name, hint in get_type_hints(func).items()
        if name != "return" and hint is uuid.UUID
    )

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        for name in uuid_parameters & kwargs.keys():
            value = kwargs[name]
            if not isinstance(value, uuid.UUID):
                kwargs[name] = uuid.UUID(str(value))
        return await func(*args, **kwargs)

    return wrapper


def task_parameter_types(app: App) -> dict[str, dict[str, object]]:
    """``{task name: {parameter: annotation}}`` for every task on ``app``.

    Procrastinate's own ``builtin:`` tasks (also registered under an alias
    without the prefix) are left out. An unannotated parameter maps to
    :data:`inspect.Parameter.empty`.
    """
    result: dict[str, dict[str, object]] = {}
    for name, task in app.tasks.items():
        if task.name.startswith("builtin:"):
            continue
        func = inspect.unwrap(task.func)
        hints = get_type_hints(func)
        result[name] = {
            parameter: hints.get(parameter, inspect.Parameter.empty)
            for parameter in inspect.signature(func).parameters
        }
    return result


def create_job_app(connector: PsycopgConnector, *, app_env: AppEnv) -> App:
    """A Procrastinate ``App`` on ``connector`` with every task registered.

    Core and module tasks (the hourly purges of rate-limit counters, #27, of
    sessions, #25, and of expired idempotency keys, #88; the five-minute
    offer expiry, #52; the nightly series materialization, #53; the daily
    progress report placeholders, #316, the 15-minute session and
    enrollment charge sweepers, #91 and #92, the daily tuition charges,
    #93, the daily package expiry, #94, and the daily charge reminders,
    #97, the 15-minute schedule reconciliation, #99, the daily payroll
    periods, #106, the 15-minute payroll adjustments, #111, and the daily
    recurring expenses, #116, whose task files are
    ``app/workflows/progress_report_schedule_tasks.py``,
    ``app/workflows/session_charge_tasks.py``,
    ``app/workflows/enrollment_charge_tasks.py``,
    ``app/workflows/tuition_charge_tasks.py``,
    ``app/workflows/package_expiry_tasks.py``,
    ``app/workflows/charge_reminder_tasks.py``,
    ``app/workflows/schedule_reconciliation_tasks.py``,
    ``app/workflows/payroll_period_tasks.py``,
    ``app/workflows/payroll_adjustment_tasks.py`` and
    ``app/workflows/recurring_expense_tasks.py`` because they compose
    modules)
    are registered in every environment, as is the 15-minute import run
    ``administration:run_imports`` (#122, ``app/modules/administration/
    imports/tasks.py``) and the 15-minute export run
    ``reporting:run_exports`` (#124, ``app/modules/reporting/tasks.py``),
    and the daily retention run ``administration:run_retention`` (#129,
    ``app/modules/administration/retention.py``).
    The demo tasks
    (and the periodic demo) are registered only in :data:`DEMO_ENVIRONMENTS`.
    Does not open the connector.
    """
    # Imported here: task modules import this module for ids_only.
    from app.core import demo_jobs, idempotency_tasks, ratelimit_tasks
    from app.modules.administration import retention
    from app.modules.administration.imports import tasks as import_tasks
    from app.modules.identity import tasks as identity_tasks
    from app.modules.reporting import tasks as reporting_tasks
    from app.modules.scheduling import tasks as scheduling_tasks
    from app.workflows import (
        charge_reminder_tasks,
        enrollment_charge_tasks,
        package_expiry_tasks,
        payroll_adjustment_tasks,
        payroll_period_tasks,
        progress_report_schedule_tasks,
        recurring_expense_tasks,
        schedule_reconciliation_tasks,
        session_charge_tasks,
        tuition_charge_tasks,
    )

    app = App(connector=connector)
    ratelimit_tasks.register(app)
    idempotency_tasks.register(app)
    identity_tasks.register(app)
    scheduling_tasks.register(app)
    progress_report_schedule_tasks.register(app)
    session_charge_tasks.register(app)
    enrollment_charge_tasks.register(app)
    tuition_charge_tasks.register(app)
    package_expiry_tasks.register(app)
    charge_reminder_tasks.register(app)
    schedule_reconciliation_tasks.register(app)
    payroll_period_tasks.register(app)
    payroll_adjustment_tasks.register(app)
    recurring_expense_tasks.register(app)
    import_tasks.register(app)
    reporting_tasks.register(app)
    retention.register(app)
    if app_env in DEMO_ENVIRONMENTS:
        demo_jobs.register(app)
    return app


def create_api_job_app(settings: Settings) -> App:
    """The API process's job app (defers through PgBouncer)."""
    return create_job_app(api_connector(settings), app_env=settings.app_env)


def create_worker_job_app(settings: Settings) -> App:
    """The worker's job app (direct connection)."""
    return create_job_app(worker_connector(settings), app_env=settings.app_env)


def get_job_app(request: Request) -> App:
    """FastAPI dependency: the job app opened by the API lifespan."""
    job_app: App = request.app.state.job_app
    return job_app


async def defer_series_materialization(job_app: App) -> None:
    """Queue one ``scheduling:materialize_series`` run now (#232 B1-B3).

    Call it only **after** the Unit of Work has committed (a closure route
    adds it as a background task, which runs after the response). A run
    already queued makes this a no-op (``queueing_lock``: Procrastinate's
    ``AlreadyEnqueued`` is ignored). Any other failure (the database is
    unreachable) is logged and swallowed: the write already succeeded, and
    the nightly run catches up (no outbox).
    """
    try:
        await job_app.configure_task(
            MATERIALIZE_SERIES_TASK,
            allow_unknown=False,
            queueing_lock=MATERIALIZE_SERIES_QUEUEING_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except AlreadyEnqueued:
        return
    except Exception as exc:  # noqa: BLE001 - the write must still answer 2xx
        logger.error(
            "Deferring the series materialization failed",
            extra={"task_name": MATERIALIZE_SERIES_TASK, "error": type(exc).__name__},
        )


async def defer_session_charges(job_app: App) -> None:
    """Queue one ``billing:post_session_charges`` run now (#91 B4).

    Call it only **after** the Unit of Work has committed (the billing
    settings route adds it as a background task, which runs after the
    response). A run already queued makes this a no-op (``queueing_lock``:
    ``AlreadyEnqueued`` is ignored). Any other failure is logged and
    swallowed: the write already succeeded, and the next 15-minute tick
    posts the charges (no outbox).
    """
    try:
        await job_app.configure_task(
            SESSION_CHARGES_TASK,
            allow_unknown=False,
            queueing_lock=SESSION_CHARGES_QUEUEING_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except AlreadyEnqueued:
        return
    except Exception as exc:  # noqa: BLE001 - the write must still answer 2xx
        logger.error(
            "Deferring the session charges failed",
            extra={"task_name": SESSION_CHARGES_TASK, "error": type(exc).__name__},
        )


async def defer_enrollment_charges(job_app: App) -> None:
    """Queue one ``billing:post_enrollment_charges`` run now (#92 D1).

    The same contract as :func:`defer_session_charges`: only after the Unit
    of Work has committed; a run already queued is a no-op
    (``AlreadyEnqueued`` is ignored); any other failure is logged and
    swallowed (the next 15-minute tick posts the charges).
    """
    try:
        await job_app.configure_task(
            ENROLLMENT_CHARGES_TASK,
            allow_unknown=False,
            queueing_lock=ENROLLMENT_CHARGES_QUEUEING_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except AlreadyEnqueued:
        return
    except Exception as exc:  # noqa: BLE001 - the write must still answer 2xx
        logger.error(
            "Deferring the enrollment charges failed",
            extra={"task_name": ENROLLMENT_CHARGES_TASK, "error": type(exc).__name__},
        )


async def defer_tuition_charges(job_app: App) -> None:
    """Queue one ``billing:post_tuition_charges`` run now (#93 E1).

    The same contract as :func:`defer_session_charges`: only after the Unit
    of Work has committed (a tuition plan create, resume or extension, or a
    billing settings ``PATCH`` that sets a date); a run already queued is a
    no-op (``AlreadyEnqueued`` is ignored); any other failure is logged and
    swallowed (the next daily run posts the periods).
    """
    try:
        await job_app.configure_task(
            TUITION_CHARGES_TASK,
            allow_unknown=False,
            queueing_lock=TUITION_CHARGES_QUEUEING_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except AlreadyEnqueued:
        return
    except Exception as exc:  # noqa: BLE001 - the write must still answer 2xx
        logger.error(
            "Deferring the tuition charges failed",
            extra={"task_name": TUITION_CHARGES_TASK, "error": type(exc).__name__},
        )


async def defer_schedule_reconciliation(job_app: App) -> None:
    """Queue one ``billing:reconcile_session_charges`` run now (#99 D9).

    The same contract as :func:`defer_session_charges`: only after the Unit
    of Work has committed (the session-changing routes and the billing
    settings ``PATCH`` add it as a background task; the series
    materialization calls it after its run); a run already queued is a
    no-op (``AlreadyEnqueued`` is ignored); any other failure is logged and
    swallowed (the next 15-minute tick reconciles).
    """
    try:
        await job_app.configure_task(
            SCHEDULE_RECONCILIATION_TASK,
            allow_unknown=False,
            queueing_lock=SCHEDULE_RECONCILIATION_QUEUEING_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except AlreadyEnqueued:
        return
    except Exception as exc:  # noqa: BLE001 - the write must still answer 2xx
        logger.error(
            "Deferring the schedule reconciliation failed",
            extra={
                "task_name": SCHEDULE_RECONCILIATION_TASK,
                "error": type(exc).__name__,
            },
        )


async def defer_recurring_expenses(job_app: App) -> None:
    """Queue one ``finance:generate_recurring_expenses`` run now (#116 E8).

    The same contract as :func:`defer_tuition_charges`: only after the Unit
    of Work has committed (a template create, reactivation or ``ends_on``
    change adds it as a background task, which runs after the response); a
    run already queued is a no-op (``AlreadyEnqueued`` is ignored); any other
    failure is logged and swallowed (the next daily run generates the
    periods).
    """
    try:
        await job_app.configure_task(
            RECURRING_EXPENSES_TASK,
            allow_unknown=False,
            queueing_lock=RECURRING_EXPENSES_QUEUEING_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except AlreadyEnqueued:
        return
    except Exception as exc:  # noqa: BLE001 - the write must still answer 2xx
        logger.error(
            "Deferring the recurring expenses failed",
            extra={"task_name": RECURRING_EXPENSES_TASK, "error": type(exc).__name__},
        )


async def defer_import_run(job_app: App) -> None:
    """Queue one ``administration:run_imports`` run now (#122 D1).

    The same contract as :func:`defer_session_charges`: only after the Unit
    of Work has committed (``POST /api/v1/imports/{id}/commit`` adds it as a
    background task); a run already queued is a no-op (``AlreadyEnqueued``
    is ignored); any other failure is logged and swallowed (the next
    15-minute tick commits the job).
    """
    try:
        await job_app.configure_task(
            IMPORTS_TASK,
            allow_unknown=False,
            queueing_lock=IMPORTS_QUEUEING_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except AlreadyEnqueued:
        return
    except Exception as exc:  # noqa: BLE001 - the write must still answer 2xx
        logger.error(
            "Deferring the import run failed",
            extra={"task_name": IMPORTS_TASK, "error": type(exc).__name__},
        )


async def defer_export_run(job_app: App) -> None:
    """Queue one ``reporting:run_exports`` run now (#124 B2).

    The same contract as :func:`defer_session_charges`: only after the Unit
    of Work has committed (``POST /api/v1/exports`` adds it as a background
    task); a run already queued is a no-op (``AlreadyEnqueued`` is ignored);
    any other failure is logged and swallowed (the next 15-minute tick builds
    the export).
    """
    try:
        await job_app.configure_task(
            EXPORTS_TASK,
            allow_unknown=False,
            queueing_lock=EXPORTS_QUEUEING_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except AlreadyEnqueued:
        return
    except Exception as exc:  # noqa: BLE001 - the request must still answer 2xx
        logger.error(
            "Deferring the export run failed",
            extra={"task_name": EXPORTS_TASK, "error": type(exc).__name__},
        )
