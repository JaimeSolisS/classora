"""The daily recurring expense job (#116 D1; ADR-0003, ADR-0017 E17).

**``finance:generate_recurring_expenses``**, daily at 07:55 UTC (``55 7 * * *``,
01:55 in Mexico City), registered in every environment by
:func:`app.core.jobs.create_job_app` (through :func:`register`, imported
lazily there). It lives under ``app/workflows/`` because it composes
``finance`` with ``administration``. The only argument is Procrastinate's
``timestamp: int``; at most one queued job (``queueing_lock`` on the periodic
task and the task, a fixed ``periodic_id``); idempotent; one Unit of Work per
template; counts-only logs. A template create, reactivation or ``ends_on``
change also queues one run after its commit
(:func:`app.core.jobs.defer_recurring_expenses`, #116 E8); nothing else does.

The rules, the lock order and how to run it by hand are in
:mod:`app.workflows.recurring_expenses`. Nothing outside ``app/core/jobs.py``
imports this module.
"""

from typing import Final

from procrastinate import App

from app.core.config import get_settings
from app.core.jobs import (
    RECURRING_EXPENSES_QUEUEING_LOCK,
    RECURRING_EXPENSES_TASK,
    ids_only,
)
from app.workflows.recurring_expenses import run_generate_recurring_expenses

TASK_NAME: Final = RECURRING_EXPENSES_TASK
CRON: Final = "55 7 * * *"
PERIODIC_ID: Final = "finance-generate-recurring-expenses"
QUEUEING_LOCK: Final = RECURRING_EXPENSES_QUEUEING_LOCK


@ids_only
async def generate_recurring_expenses(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_generate_recurring_expenses(get_settings())


def register(app: App) -> None:
    """Register the daily recurring expense job on ``app`` (every
    environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=RECURRING_EXPENSES_TASK, queueing_lock=QUEUEING_LOCK)(
            generate_recurring_expenses
        )
    )
