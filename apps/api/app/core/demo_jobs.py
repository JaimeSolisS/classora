"""Demo tasks and the demo endpoint (local and DEV only).

They prove the job path end to end: the API defers through PgBouncer, the
``worker`` container runs the job over its direct connection, and failed jobs
stay in ``procrastinate_jobs``. Real business jobs come with their own issues.

- ``demo:succeed`` logs one line and succeeds.
- ``demo:fail`` always raises :class:`DemoJobError`; it runs
  :data:`FAIL_ATTEMPTS` times (:data:`FAIL_RETRY`), then ends ``failed``.
- ``demo:periodic`` runs every minute (one job per tick, even with several
  workers: ``queueing_lock`` plus Procrastinate's periodic deduplication).
- ``POST /api/v1/dev/demo-jobs`` defers ``succeed`` or ``fail``.

:func:`app.core.jobs.create_job_app` registers the tasks, and
``app.main.create_app`` mounts the route, only when ``APP_ENV`` is ``local``
or ``dev``.
"""

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, status
from procrastinate import App, JobContext, RetryStrategy
from procrastinate.exceptions import AlreadyEnqueued
from pydantic import BaseModel, ConfigDict

from app.core.authz import public
from app.core.errors import AppError
from app.core.jobs import get_job_app, ids_only

logger = logging.getLogger(__name__)

# demo:fail runs FAIL_ATTEMPTS times in all (the `attempts` column), 2 s apart,
# then ends failed. Procrastinate 3.10's max_attempts counts the retries after
# the first run.
FAIL_ATTEMPTS = 3
FAIL_RETRY = RetryStrategy(max_attempts=FAIL_ATTEMPTS - 1, wait=2)
PERIODIC_CRON = "* * * * *"
PERIODIC_ID = "demo"

# One queued (todo) demo job per outcome at a time: the flood guard for the
# unauthenticated demo route.
QUEUEING_LOCKS = {"succeed": "demo-succeed", "fail": "demo-fail"}
PERIODIC_QUEUEING_LOCK = "demo-periodic"

Outcome = Literal["succeed", "fail"]


class DemoJobError(Exception):
    """The deliberate failure of ``demo:fail`` (no personal data)."""

    def __init__(self) -> None:
        super().__init__("demo job failed on purpose")


@ids_only
async def succeed(context: JobContext, demo_id: uuid.UUID) -> None:
    logger.info(
        "Demo job succeeded",
        extra={"job_id": context.job.id, "demo_id": str(demo_id)},
    )


@ids_only
async def fail(demo_id: uuid.UUID) -> None:
    raise DemoJobError()


@ids_only
async def periodic(context: JobContext, timestamp: int) -> None:
    logger.info(
        "Periodic demo job ran",
        extra={"job_id": context.job.id, "periodic_timestamp": timestamp},
    )


def register(app: App) -> None:
    """Register the demo tasks and the periodic demo on ``app``."""
    app.task(
        name="demo:succeed", pass_context=True, queueing_lock=QUEUEING_LOCKS["succeed"]
    )(succeed)
    app.task(name="demo:fail", retry=FAIL_RETRY, queueing_lock=QUEUEING_LOCKS["fail"])(
        fail
    )
    app.periodic(
        cron=PERIODIC_CRON,
        periodic_id=PERIODIC_ID,
        queueing_lock=PERIODIC_QUEUEING_LOCK,
    )(
        app.task(
            name="demo:periodic",
            pass_context=True,
            queueing_lock=PERIODIC_QUEUEING_LOCK,
        )(periodic)
    )


# --- endpoint -----------------------------------------------------------------


class DemoJobAlreadyQueuedError(AppError):
    status = 409
    code = "demo_job_already_queued"
    detail = "A demo job with this outcome is already waiting to run."


class DemoJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome = "succeed"


class DemoJobResponse(BaseModel):
    job_id: int
    demo_id: uuid.UUID


router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


@router.post(
    "/demo-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        public(
            reason="Local/DEV-only demo job route, never mounted outside local or dev"
        )
    ],
)
async def create_demo_job(
    job_app: Annotated[App, Depends(get_job_app)],
    body: Annotated[DemoJobRequest | None, Body()] = None,
) -> DemoJobResponse:
    """Defer a demo job through the API-side connector (PgBouncer).

    No Unit of Work here. Business code follows the after-commit rule::

        async with uow:
            ...  # write the records
        await task.defer_async(record_id=record.id)  # only after the commit
    """
    outcome: Outcome = body.outcome if body is not None else "succeed"
    demo_id = uuid.uuid7()
    try:
        job_id = await job_app.configure_task(
            f"demo:{outcome}", allow_unknown=False
        ).defer_async(demo_id=str(demo_id))
    except AlreadyEnqueued:
        raise DemoJobAlreadyQueuedError() from None
    return DemoJobResponse(job_id=job_id, demo_id=demo_id)
