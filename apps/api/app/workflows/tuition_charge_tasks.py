"""The daily tuition charge job (#93 E1, K5; ADR-0003, ADR-0006 E1).

**``billing:post_tuition_charges``**, daily at 07:30 UTC (``30 7 * * *``,
01:30 in Mexico City), registered in every environment by
:func:`app.core.jobs.create_job_app` (through :func:`register`, imported
lazily there). It lives under ``app/workflows/`` because it composes
``billing`` with ``families``, ``scheduling`` and ``administration`` (the
#91 precedent). The only argument is Procrastinate's ``timestamp: int``; at
most one queued job (``queueing_lock`` on the periodic task and the task, a
fixed ``periodic_id``); idempotent; one Unit of Work per plan; counts-only
logs. A tuition plan create, resume or ``ends_on`` extension and a ``PATCH
/api/v1/billing/settings`` that sets a date also queue one run after their
commit (:func:`app.core.jobs.defer_tuition_charges`).

The rules, the lock order and how to run it by hand are in
:mod:`app.workflows.tuition_charges`. Nothing outside ``app/core/jobs.py``
imports this module.
"""

from typing import Final

from procrastinate import App

from app.core.config import get_settings
from app.core.jobs import (
    TUITION_CHARGES_QUEUEING_LOCK,
    TUITION_CHARGES_TASK,
    ids_only,
)
from app.workflows.tuition_charges import run_post_tuition_charges

TASK_NAME: Final = TUITION_CHARGES_TASK
CRON: Final = "30 7 * * *"
PERIODIC_ID: Final = "billing-post-tuition-charges"
QUEUEING_LOCK: Final = TUITION_CHARGES_QUEUEING_LOCK


@ids_only
async def post_tuition_charges(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_post_tuition_charges(get_settings())


def register(app: App) -> None:
    """Register the daily tuition charge job on ``app`` (every
    environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=TUITION_CHARGES_TASK, queueing_lock=QUEUEING_LOCK)(
            post_tuition_charges
        )
    )
