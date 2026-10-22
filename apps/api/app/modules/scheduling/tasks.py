"""Scheduling background jobs (#52 E3, #53 F3/F4; ADR-0003, ADR-0005 D3, D4).

Two periodic Procrastinate tasks, registered in every environment by
:func:`app.core.jobs.create_job_app` (through :func:`register`). Both follow
the ``identity/tasks.py`` rules: the only argument is Procrastinate's
``timestamp: int``; one job per tick (``queueing_lock`` and a fixed
``periodic_id``); idempotent; one Unit of Work per item; counts-only logs.
Procrastinate evaluates the crons on UTC timestamps, whatever the process
time zone.

**``scheduling:expire_offers``** (#52), every five minutes: expires the
pending offers whose ``respond_by`` has passed. Their still-``offered``
sessions go back to ``unassigned`` (the tutor is cleared), the offer becomes
``expired``, and a reassignment inbox item and the ``scheduling.
offer_expired`` notifications are made (``offer_flow.expire_offer``). Each
run selects at most :data:`BATCH_SIZE` overdue offers (oldest deadline
first) and handles each one in its own Unit of Work, re-checking under the
lock order that it is still pending and overdue. An offer answered
meanwhile is skipped, so a concurrent answer wins. Audit events carry no
actor.

**``scheduling:materialize_series``** (#53), every day at 08:00 UTC
(``0 8 * * *``): extends every unfinished series to the rolling horizon
(``SCHEDULING_HORIZON_WEEKS``, ADR-0005 D4) with
:func:`~app.modules.scheduling.materialize.materialize_series` in
``extend`` mode, one Unit of Work per series (the system acts:
``actor_id=None``). In that same transaction, right before the extension,
:func:`~app.modules.scheduling.closure_changes.reconcile_series_closures`
(#232) skips the series' future rows on dates closed since they were
created and re-checks the skipped ones whose date reopened. A closure write
also queues this task after its commit
(``app.core.jobs.defer_series_materialization``). A failing series never
blocks the others: its error is logged with the series id and the error
class, and the next run retries it. A **group** series whose participants
changed between the participant read and the series lock (an enrollment
committed in between, #56 C2) is rolled back and retried at once, at most
:data:`GROUP_RETRIES` times in all, then counted as failed.

Both ``run_*`` functions build their own direct engine and dispose of it, so
they can also be run by hand::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.modules.scheduling.tasks import run_materialization; \\
    print(asyncio.run(run_materialization(get_settings())))"

Nothing outside ``app/core/jobs.py`` imports this module (ADR-0002).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from procrastinate import App
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import (
    MATERIALIZE_SERIES_QUEUEING_LOCK,
    MATERIALIZE_SERIES_TASK,
    create_worker_engine,
    ids_only,
)
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.scheduling.closure_changes import (
    ReconcileCounts,
    reconcile_series_closures,
    register_closure_notifications,
)
from app.modules.scheduling.group_lock import SeriesParticipantsChangedError
from app.modules.scheduling.materialize import MaterializeCounts, materialize_series
from app.modules.scheduling.models import ClassSeries
from app.modules.scheduling.occurrence_approval import OCCURRENCE_CONFLICT_HANDLER
from app.modules.scheduling.offer_flow import expire_offer, overdue_offer_ids

logger = logging.getLogger(__name__)

TASK_NAME: Final = "scheduling:expire_offers"
EXPIRE_CRON: Final = "*/5 * * * *"
EXPIRE_PERIODIC_ID: Final = "scheduling-expire-offers"
EXPIRE_QUEUEING_LOCK: Final = "scheduling-expire-offers"
BATCH_SIZE: Final = 200

# The name and the lock live in app.core.jobs: the closure writes defer it
# too (#232 B1), and catalog never imports scheduling (ADR-0002).
MATERIALIZE_TASK_NAME: Final = MATERIALIZE_SERIES_TASK
MATERIALIZE_CRON: Final = "0 8 * * *"
MATERIALIZE_PERIODIC_ID: Final = "scheduling-materialize-series"
MATERIALIZE_QUEUEING_LOCK: Final = MATERIALIZE_SERIES_QUEUEING_LOCK
GROUP_RETRIES: Final = 3
"""How many times one run tries a group series whose participants changed
under it (#56 C2)."""


@dataclass(frozen=True)
class ExpiryResult:
    """What one run did."""

    expired: int
    skipped: int


async def run_expire_offers(settings: Settings) -> ExpiryResult:
    """Expire the overdue pending offers at :func:`app.core.clock.now`.

    Builds its own direct engine and disposes it, so it can also be run by
    hand."""
    now = clock.now()
    engine = create_worker_engine(settings)
    expired = skipped = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            offer_ids = await overdue_offer_ids(uow.session, now, limit=BATCH_SIZE)
        for offer_id in offer_ids:
            async with UnitOfWork(sessionmaker) as uow:
                changed = await expire_offer(uow.session, offer_id, now)
            if changed:
                expired += 1
            else:
                skipped += 1
    finally:
        await engine.dispose()
    logger.info(
        "Expired assignment offers", extra={"expired": expired, "skipped": skipped}
    )
    return ExpiryResult(expired=expired, skipped=skipped)


@ids_only
async def expire_offers(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_expire_offers(get_settings())


@dataclass(frozen=True)
class MaterializationResult:
    """The counts of one run (also its log line)."""

    series: int = 0
    created: int = 0
    skipped_closure: int = 0
    conflicts: int = 0
    failed: int = 0
    closure_skipped: int = 0
    """Existing rows moved to ``skipped_closure`` (#232 A2)."""
    closure_restored: int = 0
    """Skipped rows restored on a reopened date (#232 A3)."""
    closure_conflicts: int = 0
    """Skipped rows that became ``conflict`` on a reopened date (#232 A3)."""


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


async def _materialize_one(
    sessionmaker: Any, series_id: uuid.UUID, *, now: datetime, settings: Settings
) -> tuple[ReconcileCounts, MaterializeCounts]:
    """One series in its own Unit of Work: the closure reconcile, then the
    extension. A group series whose participants changed under the locks is
    rolled back and tried again (#56 C2), at most :data:`GROUP_RETRIES`
    times; the last failure propagates."""
    for attempt in range(1, GROUP_RETRIES + 1):
        try:
            async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                reconciled = await reconcile_series_closures(
                    uow.session, series_id, now=now
                )
                counts = await materialize_series(
                    uow.session,
                    series_id,
                    now=now,
                    horizon_weeks=settings.scheduling_horizon_weeks,
                    mode="extend",
                )
        except SeriesParticipantsChangedError:
            if attempt == GROUP_RETRIES:
                raise
            continue
        return reconciled, counts
    raise AssertionError("unreachable")  # pragma: no cover


async def run_materialization(
    settings: Settings, *, now: datetime | None = None
) -> MaterializationResult:
    """Reconcile the closures of (#232), then extend, every unfinished series
    to ``now + SCHEDULING_HORIZON_WEEKS`` (``now`` defaults to
    :func:`app.core.clock.now`), one Unit of Work per series. Returns the
    counts."""
    at = clock.now() if now is None else now
    # The worker and the CLI register them through app.wiring; a run by hand
    # may not have: registering again changes nothing.
    administration.register_approval_handler(OCCURRENCE_CONFLICT_HANDLER)
    register_closure_notifications()
    engine = create_worker_engine(settings)
    series = created = skipped = conflicts = failed = 0
    closure_skipped = closure_restored = closure_conflicts = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            ids = await series_to_materialize(uow.session, at)
        for series_id in ids:
            series += 1
            try:
                reconciled, counts = await _materialize_one(
                    sessionmaker, series_id, now=at, settings=settings
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
            closure_skipped += reconciled.skipped
            closure_restored += reconciled.restored
            closure_conflicts += reconciled.conflicts
    finally:
        await engine.dispose()
    result = MaterializationResult(
        series=series,
        created=created,
        skipped_closure=skipped,
        conflicts=conflicts,
        failed=failed,
        closure_skipped=closure_skipped,
        closure_restored=closure_restored,
        closure_conflicts=closure_conflicts,
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
            "closure_skipped_count": result.closure_skipped,
            "closure_restored_count": result.closure_restored,
            "closure_conflict_count": result.closure_conflicts,
        },
    )
    return result


@ids_only
async def materialize_class_series(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_materialization(get_settings())


def register(app: App) -> None:
    """Register the five-minute offer expiry and the nightly series
    materialization on ``app`` (every environment)."""
    app.periodic(
        cron=EXPIRE_CRON,
        periodic_id=EXPIRE_PERIODIC_ID,
        queueing_lock=EXPIRE_QUEUEING_LOCK,
    )(app.task(name=TASK_NAME, queueing_lock=EXPIRE_QUEUEING_LOCK)(expire_offers))
    app.periodic(
        cron=MATERIALIZE_CRON,
        periodic_id=MATERIALIZE_PERIODIC_ID,
        queueing_lock=MATERIALIZE_QUEUEING_LOCK,
    )(
        app.task(name=MATERIALIZE_TASK_NAME, queueing_lock=MATERIALIZE_QUEUEING_LOCK)(
            materialize_class_series
        )
    )
