"""The daily payroll period job (#106 D; ADR-0003, ADR-0017 E6).

**``payroll:open_periods``**, daily at 07:50 UTC (``50 7 * * *``, 01:50 in
Mexico City), registered in every environment by
:func:`app.core.jobs.create_job_app` (through :func:`register`, imported
lazily there). It lives under ``app/workflows/`` because it composes
``payroll`` with ``administration``. The only argument is Procrastinate's
``timestamp: int``; at most one queued job (``queueing_lock`` on the periodic
task and the task, a fixed ``periodic_id``); idempotent; one Unit of Work per
tutor; logs carry counts and ids only. Nothing else queues it (#106 E7).

The rules and how to run it by hand are in
:mod:`app.workflows.payroll_periods`. Nothing outside ``app/core/jobs.py``
imports this module.
"""

from typing import Final

from procrastinate import App

from app.core.config import get_settings
from app.core.jobs import (
    PAYROLL_OPEN_PERIODS_QUEUEING_LOCK,
    PAYROLL_OPEN_PERIODS_TASK,
    ids_only,
)
from app.workflows.payroll_periods import run_open_periods

TASK_NAME: Final = PAYROLL_OPEN_PERIODS_TASK
CRON: Final = "50 7 * * *"
PERIODIC_ID: Final = "payroll-open-periods"
QUEUEING_LOCK: Final = PAYROLL_OPEN_PERIODS_QUEUEING_LOCK


@ids_only
async def open_periods(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_open_periods(get_settings())


def register(app: App) -> None:
    """Register the daily payroll period job on ``app`` (every environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=PAYROLL_OPEN_PERIODS_TASK, queueing_lock=QUEUEING_LOCK)(
            open_periods
        )
    )
