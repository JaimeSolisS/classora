"""Background jobs on Procrastinate (spec §10, architecture §6, ADR-0003).

This module is the one place that builds the Procrastinate :class:`App` and its
connectors. Task modules expose ``register(app)``; :func:`create_job_app`
calls them, so the API process and the ``worker`` container see the same
tasks. Like ``app.core.model_registry``, it is the registry that imports the
modules' task files (lazily, inside :func:`create_job_app`); module code never
imports another module's ``tasks`` (ADR-0002).

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
"""

import functools
import inspect
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final, get_type_hints

from fastapi import Request
from procrastinate import App, PsycopgConnector
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import AppEnv, Settings
from app.core.db import async_direct_database_url, create_direct_engine

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

    Module tasks (the hourly session purge, #25) are registered in every
    environment. The demo tasks (and the periodic demo) are registered only
    in :data:`DEMO_ENVIRONMENTS`. Does not open the connector.
    """
    # Imported here: task modules import this module for ids_only.
    from app.core import demo_jobs
    from app.modules.identity import tasks as identity_tasks

    app = App(connector=connector)
    identity_tasks.register(app)
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
