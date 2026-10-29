"""The daily package expiry job (#94 F1; ADR-0003, ADR-0006 E7, E10).

**``billing:expire_packages``**, daily at 07:40 UTC (``40 7 * * *``, 01:40
in Mexico City, after the 07:30 tuition charges), registered in every
environment by :func:`app.core.jobs.create_job_app` (through
:func:`register`, imported lazily there). It lives under ``app/workflows/``
because it composes ``billing`` with ``scheduling``, ``academics`` and
``administration`` (the #91 precedent). The only argument is
Procrastinate's ``timestamp: int``; at most one queued job
(``queueing_lock`` on the periodic task and the task, a fixed
``periodic_id``); idempotent (one ``expire`` row per package, one
consumption per key); counts-only logs.

The rules, the lock order and how to run it by hand are in
:mod:`app.workflows.package_expiry`. Nothing outside ``app/core/jobs.py``
imports this module.
"""

from typing import Final

from procrastinate import App

from app.core.config import get_settings
from app.core.jobs import (
    PACKAGE_EXPIRY_QUEUEING_LOCK,
    PACKAGE_EXPIRY_TASK,
    ids_only,
)
from app.workflows.package_expiry import run_expire_packages

TASK_NAME: Final = PACKAGE_EXPIRY_TASK
CRON: Final = "40 7 * * *"
PERIODIC_ID: Final = "billing-expire-packages"
QUEUEING_LOCK: Final = PACKAGE_EXPIRY_QUEUEING_LOCK


@ids_only
async def expire_packages(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_expire_packages(get_settings())


def register(app: App) -> None:
    """Register the daily package expiry job on ``app`` (every
    environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=PACKAGE_EXPIRY_TASK, queueing_lock=QUEUEING_LOCK)(expire_packages)
    )
