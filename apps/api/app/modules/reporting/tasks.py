"""The export run (#124 D1-D4, E8; ADR-0003; architecture §6).

**``reporting:run_exports``**, every 15 minutes (``*/15 * * * *``) and
queued after ``POST /api/v1/exports`` (:func:`app.core.jobs.defer_export_run`),
registered in every environment by :func:`app.core.jobs.create_job_app`. The
only argument is Procrastinate's ``timestamp: int``; at most one queued job
(``queueing_lock`` on the periodic task and the task, a fixed
``periodic_id``); idempotent: a run builds what is ``queued`` now and purges
what has expired now, and running it again on a ``ready`` job changes
nothing.

1. Every ``queued`` job, oldest first, in **one Unit of Work per job** acting
   as the requester (``UnitOfWork(actor_id=requested_by_user_id)``), through
   :func:`~app.modules.reporting.exports.service.run_job`: ``REPEATABLE
   READ``, the job ``FOR UPDATE SKIP LOCKED``, rows, file, status and
   notification in that one transaction. A crash leaves the job ``queued``
   for the next run; an exception rolls it back and the job becomes
   ``failed`` (``error``) in a new Unit of Work, so a poison job never loops.
2. The purge: ``ready`` jobs whose ``expires_at`` has passed lose their file
   and become ``expired``, at most 100 per Unit of Work, with no actor.

The datasets must be registered in the process (``app.worker`` calls
``app.wiring.register_approval_wiring``); a job of an unregistered dataset
fails with ``error``. Log lines carry job ids, counts and error class names
only, never a value.

**Run by hand** (:func:`run_exports` builds its own direct engine and
disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.wiring import register_approval_wiring as w; w(); \\
    from app.modules.reporting.tasks import run_exports; \\
    print(asyncio.run(run_exports(get_settings())))"
"""

import logging
from dataclasses import dataclass
from typing import Final

from procrastinate import App
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import (
    EXPORTS_QUEUEING_LOCK,
    EXPORTS_TASK,
    create_worker_engine,
    ids_only,
)
from app.core.uow import UnitOfWork
from app.modules.reporting.exports import service
from app.modules.reporting.exports.models import FAILED, READY

logger = logging.getLogger(__name__)

TASK_NAME: Final = EXPORTS_TASK
CRON: Final = "*/15 * * * *"
PERIODIC_ID: Final = "reporting-run-exports"
QUEUEING_LOCK: Final = EXPORTS_QUEUEING_LOCK


@dataclass(frozen=True)
class ExportRunResult:
    """The counts of one run (also its log line)."""

    jobs: int = 0
    ready: int = 0
    failed: int = 0
    skipped: int = 0
    expired: int = 0


async def run_exports_with(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> ExportRunResult:
    """One run on ``sessionmaker`` (tests call it with their own engine)."""
    async with UnitOfWork(sessionmaker) as uow:
        jobs = await service.queued_jobs(uow.session)
    ready = failed = skipped = 0
    for job in jobs:
        try:
            async with UnitOfWork(
                sessionmaker, actor_id=job.requested_by_user_id
            ) as uow:
                status = await service.run_job(uow.session, job.id)
        except Exception as exc:  # noqa: BLE001 - one job never blocks the rest
            logger.error(
                "Building an export failed",
                extra={"export_job_id": str(job.id), "error": type(exc).__name__},
            )
            async with UnitOfWork(
                sessionmaker, actor_id=job.requested_by_user_id
            ) as uow:
                changed = await service.mark_job_failed(uow.session, job.id)
            failed += int(changed)
            skipped += int(not changed)
            continue
        if status == READY:
            ready += 1
        elif status == FAILED:
            failed += 1
        else:
            skipped += 1
    expired = 0
    while True:
        async with UnitOfWork(sessionmaker) as uow:
            count = await service.purge_expired(uow.session)
        expired += count
        if count < service.PURGE_BATCH:
            break
    result = ExportRunResult(
        jobs=len(jobs), ready=ready, failed=failed, skipped=skipped, expired=expired
    )
    logger.info(
        "Ran exports",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result


async def run_exports(settings: Settings) -> ExportRunResult:
    """One run with its own direct engine (the worker's)."""
    engine = create_worker_engine(settings)
    try:
        return await run_exports_with(create_sessionmaker(engine))
    finally:
        await engine.dispose()


@ids_only
async def run_exports_task(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_exports(get_settings())


def register(app: App) -> None:
    """Register the 15-minute export run on ``app`` (every environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=EXPORTS_TASK, queueing_lock=QUEUEING_LOCK)(run_exports_task)
    )
