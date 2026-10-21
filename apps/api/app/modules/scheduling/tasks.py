"""Scheduling background jobs (#52 E3; ADR-0003, ADR-0005 D3).

``scheduling:expire_offers`` is a periodic Procrastinate task, every five
minutes, registered in every environment by
:func:`app.core.jobs.create_job_app` (through :func:`register`). It expires
the pending offers whose ``respond_by`` has passed: their still-``offered``
sessions go back to ``unassigned`` (the tutor cleared), the offer becomes
``expired``, and a reassignment inbox item and the ``scheduling.
offer_expired`` notifications are made (``offer_flow.expire_offer``).

Rules (ADR-0003, the ``identity/tasks.py`` pattern): the only argument is
Procrastinate's ``timestamp: int``; one job per tick (``queueing_lock`` and a
fixed ``periodic_id``). Each run selects at most :data:`BATCH_SIZE` overdue
offers (oldest deadline first) and handles **each offer in its own Unit of
Work**, re-checking under the lock order that it is still pending and
overdue; an offer answered meanwhile is skipped, so a concurrent answer wins
and a second run changes nothing (idempotent). Audit events carry no actor.
The run logs the counts only.
"""

import logging
from dataclasses import dataclass
from typing import Final

from procrastinate import App

from app.core import clock
from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine, ids_only
from app.core.uow import UnitOfWork
from app.modules.scheduling.offer_flow import expire_offer, overdue_offer_ids

logger = logging.getLogger(__name__)

TASK_NAME: Final = "scheduling:expire_offers"
EXPIRE_CRON: Final = "*/5 * * * *"
EXPIRE_PERIODIC_ID: Final = "scheduling-expire-offers"
EXPIRE_QUEUEING_LOCK: Final = "scheduling-expire-offers"
BATCH_SIZE: Final = 200


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


def register(app: App) -> None:
    """Register the five-minute expiry on ``app`` (every environment)."""
    app.periodic(
        cron=EXPIRE_CRON,
        periodic_id=EXPIRE_PERIODIC_ID,
        queueing_lock=EXPIRE_QUEUEING_LOCK,
    )(app.task(name=TASK_NAME, queueing_lock=EXPIRE_QUEUEING_LOCK)(expire_offers))
