"""The import run (#122 D2, D3, E2; ADR-0003; architecture ISSUE-L1).

**``administration:run_imports``**, every 15 minutes (``*/15 * * * *``) and
queued after ``POST /api/v1/imports/{id}/commit``
(:func:`app.core.jobs.defer_import_run`), registered in every environment by
:func:`app.core.jobs.create_job_app`. The only argument is Procrastinate's
``timestamp: int``; at most one queued job (``queueing_lock`` on the
periodic task and the task, a fixed ``periodic_id``); idempotent: a run
commits what is ``committing`` now and expires what is stale now, and
re-running it on a ``committed`` job changes nothing.

1. Every ``committing`` job, oldest first: batches of the kind's
   ``batch_size`` ready rows, **one Unit of Work per batch** acting as the
   committer (``UnitOfWork(actor_id=committed_by_user_id)``, so the records
   it creates are audited with that actor), through
   :func:`~app.modules.administration.imports.service.commit_next_batch`.
   An exception escaping a batch rolls that batch back; the job becomes
   ``failed`` in a new Unit of Work (the committed batches stay).
2. Expire ``staged`` and ``failed`` jobs whose last change is over 7 days
   old (their rows are deleted).

The kinds must be registered in the process (``app.worker`` calls
``app.wiring.register_approval_wiring``); a job of an unregistered kind fails.
Log lines carry job ids, counts and error class names only, never a value.

**Run by hand** (:func:`run_imports` builds its own direct engine and
disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.wiring import register_approval_wiring as w; w(); \\
    from app.modules.administration.imports.tasks import run_imports; \\
    print(asyncio.run(run_imports(get_settings())))"
"""

import logging
from dataclasses import dataclass
from typing import Final

from procrastinate import App
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import (
    IMPORTS_QUEUEING_LOCK,
    IMPORTS_TASK,
    create_worker_engine,
    ids_only,
)
from app.core.uow import UnitOfWork
from app.modules.administration.imports import service
from app.modules.administration.imports.registry import RUN_SETTINGS_KEY

logger = logging.getLogger(__name__)

TASK_NAME: Final = IMPORTS_TASK
CRON: Final = "*/15 * * * *"
PERIODIC_ID: Final = "administration-run-imports"
QUEUEING_LOCK: Final = IMPORTS_QUEUEING_LOCK


@dataclass(frozen=True)
class ImportRunResult:
    """The counts of one run (also its log line)."""

    jobs: int = 0
    batches: int = 0
    committed: int = 0
    failed: int = 0
    expired: int = 0


async def run_imports_with(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
) -> ImportRunResult:
    """One run on ``sessionmaker`` (tests call it with their own engine).
    ``settings`` (the run's) is put in each batch session's ``info`` for the
    kinds (:func:`~app.modules.administration.imports.registry.run_settings`,
    #123 B4)."""
    async with UnitOfWork(sessionmaker) as uow:
        jobs = await service.committing_jobs(uow.session)
    batches = committed = failed = 0
    for job in jobs:
        while True:
            try:
                async with UnitOfWork(
                    sessionmaker, actor_id=job.committed_by_user_id
                ) as uow:
                    if settings is not None:
                        uow.session.info[RUN_SETTINGS_KEY] = settings
                    done = await service.commit_next_batch(uow.session, job.id)
            except Exception as exc:  # noqa: BLE001 - one job never blocks the rest
                failed += 1
                logger.error(
                    "Committing an import batch failed",
                    extra={"import_job_id": str(job.id), "error": type(exc).__name__},
                )
                async with UnitOfWork(
                    sessionmaker, actor_id=job.committed_by_user_id
                ) as uow:
                    await service.mark_job_failed(uow.session, job.id)
                break
            if done:
                committed += 1
                break
            batches += 1
    expired = 0
    while True:
        async with UnitOfWork(sessionmaker) as uow:
            count = await service.expire_jobs(uow.session)
        expired += count
        if count < service.EXPIRY_BATCH:
            break
    result = ImportRunResult(
        jobs=len(jobs),
        batches=batches,
        committed=committed,
        failed=failed,
        expired=expired,
    )
    logger.info(
        "Ran imports",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result


async def run_imports(settings: Settings) -> ImportRunResult:
    """One run with its own direct engine (the worker's)."""
    engine = create_worker_engine(settings)
    try:
        return await run_imports_with(create_sessionmaker(engine), settings)
    finally:
        await engine.dispose()


@ids_only
async def run_imports_task(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_imports(get_settings())


def register(app: App) -> None:
    """Register the 15-minute import run on ``app`` (every environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=IMPORTS_TASK, queueing_lock=QUEUEING_LOCK)(run_imports_task)
    )
