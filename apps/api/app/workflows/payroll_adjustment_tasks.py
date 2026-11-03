"""The payroll adjustment job (#111 B1; ADR-0003, ADR-0017 E11).

**``payroll:post_adjustments``**, every 15 minutes (``*/15 * * * *``),
registered in every environment by :func:`app.core.jobs.create_job_app`
(through :func:`register`, imported lazily there). It lives under
``app/workflows/`` because it composes ``payroll`` with ``academics``,
``scheduling`` and ``administration``. The only argument is Procrastinate's
``timestamp: int``; at most one queued job (``queueing_lock`` on the periodic
task and the task, a fixed ``periodic_id``); idempotent; one Unit of Work per
adjustment; logs carry counts and ids only. Nothing else queues it (#111 E10):
no closeout route changes, and a payroll failure can't touch a closeout.

The rules, the lock order and how to run it by hand are in
:mod:`app.workflows.payroll_adjustments`. Nothing outside ``app/core/jobs.py``
imports this module.
"""

from typing import Final

from procrastinate import App

from app.core.config import get_settings
from app.core.jobs import (
    PAYROLL_POST_ADJUSTMENTS_QUEUEING_LOCK,
    PAYROLL_POST_ADJUSTMENTS_TASK,
    ids_only,
)
from app.workflows.payroll_adjustments import run_post_adjustments

TASK_NAME: Final = PAYROLL_POST_ADJUSTMENTS_TASK
CRON: Final = "*/15 * * * *"
PERIODIC_ID: Final = "payroll-post-adjustments"
QUEUEING_LOCK: Final = PAYROLL_POST_ADJUSTMENTS_QUEUEING_LOCK


@ids_only
async def post_adjustments(timestamp: int) -> None:
    """The periodic task: one run with the process's settings."""
    await run_post_adjustments(get_settings())


def register(app: App) -> None:
    """Register the 15-minute payroll adjustment job on ``app`` (every
    environment)."""
    app.periodic(cron=CRON, periodic_id=PERIODIC_ID, queueing_lock=QUEUEING_LOCK)(
        app.task(name=TASK_NAME, queueing_lock=QUEUEING_LOCK)(post_adjustments)
    )
