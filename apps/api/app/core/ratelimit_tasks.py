"""The hourly purge of ended rate-limit windows (#27 F; ADR-0008 D15).

``ratelimit:purge_counters`` is a periodic Procrastinate task registered in
**every** environment by :func:`app.core.jobs.create_job_app` (through
:func:`register`). It deletes ``rate_limit_counters`` rows whose window ended
at least :data:`PURGE_GRACE` ago (``window_end <= now - 1 hour``, with ``now``
from :func:`app.core.clock.now`), so an operator can still look at a flood that
just ended. A row of a window that is still open is never deleted.

Rules (ADR-0003, M1-16): the only argument is Procrastinate's
``timestamp: int``; the task is idempotent (it deletes what is purgeable now);
one job per tick (``queueing_lock`` and a fixed ``periodic_id``). It runs in
the worker over a direct connection (:func:`app.core.jobs.create_worker_engine`)
in batches of at most :data:`BATCH_SIZE` rows, one transaction each, touches
no other table, writes no audit events and logs one line with the count only
(never a key or key hash).
"""

import logging
from datetime import timedelta
from typing import Final

from procrastinate import App
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

from app.core import clock
from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine, ids_only
from app.core.ratelimit_models import RateLimitCounter
from app.core.uow import UnitOfWork

logger = logging.getLogger(__name__)

TASK_NAME: Final = "ratelimit:purge_counters"
PURGE_CRON: Final = "23 * * * *"
PURGE_PERIODIC_ID: Final = "ratelimit-purge-counters"
PURGE_QUEUEING_LOCK: Final = "ratelimit-purge-counters"

PURGE_GRACE: Final = timedelta(hours=1)
BATCH_SIZE: Final = 1000


async def run_purge(settings: Settings, *, batch_size: int | None = None) -> int:
    """Delete every counter whose window ended at least :data:`PURGE_GRACE` ago.

    Builds its own direct engine and disposes it, so it can also be run by
    hand. Deletes in batches (one transaction each) until a batch is short.
    Returns the number of deleted rows.
    """
    size = BATCH_SIZE if batch_size is None else batch_size
    cutoff = clock.now() - PURGE_GRACE
    batch = (
        select(RateLimitCounter.id)
        .where(RateLimitCounter.window_end <= cutoff)
        .limit(size)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    statement = (
        delete(RateLimitCounter)
        .where(RateLimitCounter.id.in_(batch))
        .execution_options(synchronize_session=False)
    )
    engine = create_worker_engine(settings)
    total = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        while True:
            async with UnitOfWork(sessionmaker) as uow:
                result: CursorResult[tuple[()]] = await uow.session.execute(  # type: ignore[assignment]
                    statement
                )
                deleted = int(result.rowcount)
            total += deleted
            if deleted < size:
                break
    finally:
        await engine.dispose()
    logger.info("Purged ended rate-limit windows", extra={"deleted": total})
    return total


@ids_only
async def purge_counters(timestamp: int) -> None:
    """The periodic task: one purge with the process's settings."""
    await run_purge(get_settings())


def register(app: App) -> None:
    """Register the hourly purge on ``app`` (every environment)."""
    app.periodic(
        cron=PURGE_CRON,
        periodic_id=PURGE_PERIODIC_ID,
        queueing_lock=PURGE_QUEUEING_LOCK,
    )(app.task(name=TASK_NAME, queueing_lock=PURGE_QUEUEING_LOCK)(purge_counters))
