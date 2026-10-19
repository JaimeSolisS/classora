"""Identity background jobs (#25): the hourly purge of ended sessions.

``identity:purge_auth_sessions`` is a periodic Procrastinate task registered
in **every** environment by :func:`app.core.jobs.create_job_app` (through
:func:`register`). It deletes ``auth_sessions`` rows that ended at least
:data:`PURGE_GRACE` ago: revoked, past ``expires_at``, or idle past
``last_seen_at + idle_timeout_seconds``. During the grace period an expired
session still answers ``session_expired`` rather than
``authentication_required``.

Rules (ADR-0003, M1-16): the only argument is Procrastinate's
``timestamp: int``; the task is idempotent (it deletes what is purgeable now);
one job per tick (``queueing_lock`` and a fixed ``periodic_id``). It runs in
the worker over a direct connection (:func:`app.core.jobs.create_worker_engine`)
in batches of at most :data:`BATCH_SIZE` rows, one transaction each, touches
no other table, writes no audit events and logs one line with the count only.
"""

import logging
from datetime import timedelta
from typing import Final

from procrastinate import App
from sqlalchemy import Interval, delete, literal_column, or_, select
from sqlalchemy.engine import CursorResult

from app.core import clock
from app.core.config import Settings, get_settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine, ids_only
from app.core.uow import UnitOfWork
from app.modules.identity.models import AuthSession

logger = logging.getLogger(__name__)

TASK_NAME: Final = "identity:purge_auth_sessions"
PURGE_CRON: Final = "7 * * * *"
PURGE_PERIODIC_ID: Final = "identity-purge-auth-sessions"
PURGE_QUEUEING_LOCK: Final = "identity-purge-auth-sessions"

PURGE_GRACE: Final = timedelta(hours=24)
BATCH_SIZE: Final = 1000

_ONE_SECOND = literal_column("interval '1 second'", type_=Interval)


async def run_purge(settings: Settings, *, batch_size: int | None = None) -> int:
    """Delete every session that ended at least :data:`PURGE_GRACE` ago.

    Builds its own direct engine and disposes it, so it can also be run by
    hand. Deletes in batches (one transaction each) until a batch is short.
    Returns the number of deleted rows.
    """
    size = BATCH_SIZE if batch_size is None else batch_size
    cutoff = clock.now() - PURGE_GRACE
    ended = or_(
        AuthSession.revoked_at <= cutoff,
        AuthSession.expires_at <= cutoff,
        AuthSession.last_seen_at + AuthSession.idle_timeout_seconds * _ONE_SECOND
        <= cutoff,
    )
    batch = (
        select(AuthSession.id)
        .where(ended)
        .limit(size)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    statement = (
        delete(AuthSession)
        .where(AuthSession.id.in_(batch))
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
    logger.info("Purged ended sessions", extra={"deleted": total})
    return total


@ids_only
async def purge_auth_sessions(timestamp: int) -> None:
    """The periodic task: one purge with the process's settings."""
    await run_purge(get_settings())


def register(app: App) -> None:
    """Register the hourly purge on ``app`` (every environment)."""
    app.periodic(
        cron=PURGE_CRON,
        periodic_id=PURGE_PERIODIC_ID,
        queueing_lock=PURGE_QUEUEING_LOCK,
    )(app.task(name=TASK_NAME, queueing_lock=PURGE_QUEUEING_LOCK)(purge_auth_sessions))
