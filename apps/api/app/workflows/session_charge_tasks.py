"""The session charge sweeper job (#91 C1; ADR-0003, ADR-0006 E1).

**``billing:post_session_charges``**, every 15 minutes (``*/15 * * * *``),
registered in every environment by :func:`app.core.jobs.create_job_app`
(through :func:`register`, imported lazily there). It lives under
``app/workflows/`` because it composes ``billing`` with ``scheduling``,
``families``, ``catalog`` and ``administration`` (the #316 precedent). Like
the ``scheduling`` tasks: the only argument is Procrastinate's ``timestamp:
int``; at most one queued job (``queueing_lock`` on the periodic task and
the task, a fixed ``periodic_id``); idempotent; one Unit of Work per item;
counts-only logs. The ``PATCH /api/v1/billing/settings`` route also queues
one run after its commit (:func:`app.core.jobs.defer_session_charges`).

The rules, the lock order and how to run it by hand are in
:mod:`app.workflows.session_charges`. Nothing outside ``app/core/jobs.py``
imports this module.
"""

from typing import Final

from procrastinate import App

from app.core.config import get_settings
from app.core.jobs import (
    SESSION_CHARGES_QUEUEING_LOCK,
    SESSION_CHARGES_TASK,
    ids_only,
)
from app.workflows.session_charges import run_post_session_charges

TASK_NAME: Final = SESSION_CHARGES_TASK
CRON: Final = "*/15 * * * *"
PERIODIC_ID: Final = "billing-post-session-charges"
QUEUEING_LOCK: Final = SESSION_CHARGES_QUEUEING_LOCK


@ids_only
async def post_session_charges(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_post_session_charges(get_settings())


def register(app: App) -> None:
    """Register the 15-minute session charge sweeper on ``app`` (every
    environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=TASK_NAME, queueing_lock=QUEUEING_LOCK)(post_session_charges)
    )
