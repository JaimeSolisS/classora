"""The schedule reconciliation job (#99 D1; ADR-0003, ADR-0006 E1).

**``billing:reconcile_session_charges``**, every 15 minutes (``*/15 * * *
*``), registered in every environment by :func:`app.core.jobs.create_job_app`
(through :func:`register`, imported lazily there). It lives under
``app/workflows/`` because it composes ``billing`` with ``scheduling``,
``families`` and ``administration``. Like the #91 sweeper: the only argument
is Procrastinate's ``timestamp: int``; at most one queued job
(``queueing_lock`` on the periodic task and the task, a fixed
``periodic_id``); idempotent (natural keys); one Unit of Work per item;
counts-only logs. The routes that change sessions (cancel, ``PATCH``, series
edits and cancellations, change-request approval), the billing settings
``PATCH`` and the end of ``scheduling:materialize_series`` also queue one run
after their commit (:func:`app.core.jobs.defer_schedule_reconciliation`).

The rules, the lock order and how to run it by hand are in
:mod:`app.workflows.schedule_reconciliation`. Nothing outside
``app/core/jobs.py`` imports this module.
"""

from typing import Final

from procrastinate import App

from app.core.config import get_settings
from app.core.jobs import (
    SCHEDULE_RECONCILIATION_QUEUEING_LOCK,
    SCHEDULE_RECONCILIATION_TASK,
    ids_only,
)
from app.workflows.schedule_reconciliation import run_reconcile_session_charges

TASK_NAME: Final = SCHEDULE_RECONCILIATION_TASK
CRON: Final = "*/15 * * * *"
PERIODIC_ID: Final = "billing-reconcile-session-charges"
QUEUEING_LOCK: Final = SCHEDULE_RECONCILIATION_QUEUEING_LOCK


@ids_only
async def reconcile_session_charges(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_reconcile_session_charges(get_settings())


def register(app: App) -> None:
    """Register the 15-minute schedule reconciliation on ``app`` (every
    environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=TASK_NAME, queueing_lock=QUEUEING_LOCK)(reconcile_session_charges)
    )
