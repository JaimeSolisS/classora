"""Scheduling background jobs (#53 F3, F4): the nightly series
materialization.

``scheduling:materialize_series`` is a periodic Procrastinate task
registered in **every** environment by :func:`app.core.jobs.create_job_app`
(through :func:`register`). It runs at 08:00 UTC (``0 8 * * *``: Procrastinate
evaluates the cron on UTC timestamps, whatever the process time zone) and
extends every series that is not finished to the rolling horizon
(``SCHEDULING_HORIZON_WEEKS``, ADR-0005 D4) with
:func:`~app.modules.scheduling.materialize.materialize_series` in ``extend``
mode.

Rules (ADR-0003, ISSUE-J1 to J4): the only argument is Procrastinate's
``timestamp: int``; one job per tick (``queueing_lock`` and a fixed
``periodic_id``); the run is idempotent (a rerun inserts nothing). It runs
in the worker over a direct connection
(:func:`app.core.jobs.create_worker_engine`), **one Unit of Work per series**
(the system acts: ``actor_id=None``), so one failing series never blocks the
others: its error is logged with the series id and the error class, and the
next run retries it. One summary line with counts only (no personal data).

:func:`run_materialization` builds its own engine and disposes it, so it can
also be run by hand::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.modules.scheduling.tasks import run_materialization; \\
    print(asyncio.run(run_materialization(get_settings())))"

Nothing outside ``app/core/jobs.py`` imports this module (ADR-0002).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from procrastinate import App
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine, ids_only
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.scheduling.materialize import materialize_series
from app.modules.scheduling.models import ClassSeries
from app.modules.scheduling.occurrence_approval import OCCURRENCE_CONFLICT_HANDLER

logger = logging.getLogger(__name__)

TASK_NAME: Final = "scheduling:materialize_series"
MATERIALIZE_CRON: Final = "0 8 * * *"
MATERIALIZE_PERIODIC_ID: Final = "scheduling-materialize-series"
MATERIALIZE_QUEUEING_LOCK: Final = "scheduling-materialize-series"


@dataclass(frozen=True)
class MaterializationResult:
    """The counts of one run (also its log line)."""

    series: int = 0
    created: int = 0
    skipped_closure: int = 0
    conflicts: int = 0
    failed: int = 0


async def series_to_materialize(
    session: AsyncSession, now: datetime
) -> list[uuid.UUID]:
    """The ids of the series that may still get occurrences: open-ended, or
    whose last occurrence is on or after ``now``'s local date in the center
    zone, by id. One statement after the zone read."""
    zone = ZoneInfo(await administration.get_center_time_zone(session))
    today = now.astimezone(zone).date()
    rows = await session.scalars(
        select(ClassSeries.id)
        .where(
            or_(
                ClassSeries.last_occurrence_on.is_(None),
                ClassSeries.last_occurrence_on >= today,
            )
        )
        .order_by(ClassSeries.id)
    )
    return list(rows)


async def run_materialization(
    settings: Settings, *, now: datetime | None = None
) -> MaterializationResult:
    """Extend every unfinished series to ``now + SCHEDULING_HORIZON_WEEKS``
    (``now`` defaults to :func:`app.core.clock.now`), one Unit of Work per
    series. Returns the counts."""
    at = clock.now() if now is None else now
    # The worker and the CLI register it through app.wiring; a run by hand
    # may not have: registering again changes nothing.
    administration.register_approval_handler(OCCURRENCE_CONFLICT_HANDLER)
    engine = create_worker_engine(settings)
    series = created = skipped = conflicts = failed = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            ids = await series_to_materialize(uow.session, at)
        for series_id in ids:
            series += 1
            try:
                async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                    counts = await materialize_series(
                        uow.session,
                        series_id,
                        now=at,
                        horizon_weeks=settings.scheduling_horizon_weeks,
                        mode="extend",
                    )
            except Exception as exc:  # noqa: BLE001 - one series never blocks the rest
                failed += 1
                logger.error(
                    "Series materialization failed",
                    extra={"series_id": str(series_id), "error": type(exc).__name__},
                )
                continue
            created += counts.created
            skipped += counts.skipped_closure
            conflicts += counts.conflicts
    finally:
        await engine.dispose()
    result = MaterializationResult(
        series=series,
        created=created,
        skipped_closure=skipped,
        conflicts=conflicts,
        failed=failed,
    )
    logger.info(
        "Materialized class series",
        # ``created`` is a LogRecord attribute: the keys carry a suffix.
        extra={
            "series_count": result.series,
            "created_count": result.created,
            "skipped_closure_count": result.skipped_closure,
            "conflict_count": result.conflicts,
            "failed_count": result.failed,
        },
    )
    return result


@ids_only
async def materialize_class_series(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_materialization(get_settings())


def register(app: App) -> None:
    """Register the nightly materialization on ``app`` (every environment)."""
    app.periodic(
        cron=MATERIALIZE_CRON,
        periodic_id=MATERIALIZE_PERIODIC_ID,
        queueing_lock=MATERIALIZE_QUEUEING_LOCK,
    )(
        app.task(name=TASK_NAME, queueing_lock=MATERIALIZE_QUEUEING_LOCK)(
            materialize_class_series
        )
    )
