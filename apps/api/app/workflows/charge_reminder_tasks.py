"""The daily charge reminder job (#97 F2; N4 (a); ADR-0003, ADR-0006 E11).

**``billing:send_charge_reminders``**, daily at 15:00 UTC (``0 15 * * *``,
09:00 in Mexico City, standard time: daytime for families), registered in
every environment by :func:`app.core.jobs.create_job_app` (through
:func:`register`, imported lazily there). It lives under ``app/workflows/``
because it composes ``billing`` with ``families`` and ``administration``
(the #91 precedent). The only argument is Procrastinate's ``timestamp:
int``; at most one queued job (``queueing_lock`` on the periodic task and
the task, a fixed ``periodic_id``); idempotent (one ``charge_reminders`` row
per charge and stage); counts-only logs.

The rules, the lock order and how to run it by hand are in
:mod:`app.workflows.charge_reminders`. Nothing outside ``app/core/jobs.py``
imports this module.
"""

from typing import Final

from procrastinate import App

from app.core.config import get_settings
from app.core.jobs import (
    CHARGE_REMINDERS_QUEUEING_LOCK,
    CHARGE_REMINDERS_TASK,
    ids_only,
)
from app.workflows.charge_reminders import run_send_charge_reminders

TASK_NAME: Final = CHARGE_REMINDERS_TASK
CRON: Final = "0 15 * * *"
PERIODIC_ID: Final = "billing-send-charge-reminders"
QUEUEING_LOCK: Final = CHARGE_REMINDERS_QUEUEING_LOCK


@ids_only
async def send_charge_reminders(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_send_charge_reminders(get_settings())


def register(app: App) -> None:
    """Register the daily charge reminder job on ``app`` (every
    environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=CHARGE_REMINDERS_TASK, queueing_lock=QUEUEING_LOCK)(
            send_charge_reminders
        )
    )
