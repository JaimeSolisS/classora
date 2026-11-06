"""The failed jobs routes (#128 D; ``_docs/api.md`` Jobs), all behind
``jobs.manage`` (Owner, Administrator; not delegable):

- ``GET /api/v1/jobs``: jobs that failed at least once, newest failure first;
- ``GET /api/v1/jobs/tasks``: the registered tasks (the filter's values);
- ``POST /api/v1/jobs/{job_id}/retry``: put a failed job back in the queue,
  audited (``administration.job_retried``). No reauthentication and no
  ``Idempotency-Key``: the status guard makes a repeat a ``409``.
"""

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path, Query
from fastapi.exceptions import RequestValidationError
from procrastinate import App
from pydantic import AwareDatetime

from app.core.authz import PERMISSION_RESPONSES, require
from app.core.errors import problem_response_doc
from app.core.jobs import get_job_app, task_parameter_types
from app.core.pagination import CursorPage, CursorPagination
from app.core.uow import UnitOfWorkDep
from app.modules.administration import jobs
from app.modules.administration.schemas import (
    FailedJobItem,
    JobStatusFilter,
    JobTaskList,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

TASK_NAME_PATTERN: Final = "^[a-z_]+:[a-z_]+$"


def registered_tasks(job_app: Annotated[App, Depends(get_job_app)]) -> frozenset[str]:
    """The tasks registered on the API's job app, without ``builtin:`` ones."""
    return frozenset(task_parameter_types(job_app))


RegisteredTasks = Annotated[frozenset[str], Depends(registered_tasks)]


def job_filters(
    task: Annotated[
        str | None,
        Query(pattern=TASK_NAME_PATTERN, max_length=128, description="A task name."),
    ] = None,
    status: Annotated[
        JobStatusFilter, Query(description="The job's current status, or `any`.")
    ] = "failed",
    failed_from: Annotated[
        AwareDatetime | None,
        Query(alias="from", description="Failed at or after (inclusive)."),
    ] = None,
    failed_to: Annotated[
        AwareDatetime | None,
        Query(alias="to", description="Failed before (exclusive)."),
    ] = None,
) -> jobs.JobFilters:
    """The filters, with ``422`` when ``to`` is not after ``from``."""
    if failed_from is not None and failed_to is not None and failed_to <= failed_from:
        errors: list[dict[str, Any]] = [
            {
                "loc": ("query", "to"),
                "type": "value_error",
                "msg": "to must be later than from",
            }
        ]
        raise RequestValidationError(errors)
    return jobs.JobFilters(
        task=task, status=status, failed_from=failed_from, failed_to=failed_to
    )


@router.get(
    "",
    dependencies=[require("jobs.manage")],
    responses={
        **PERMISSION_RESPONSES,
        400: problem_response_doc("The pagination cursor is not valid."),
    },
)
async def list_jobs(
    filters: Annotated[jobs.JobFilters, Depends(job_filters)],
    page: CursorPagination,
    uow: UnitOfWorkDep,
    registered: RegisteredTasks,
) -> CursorPage[FailedJobItem]:
    """Background jobs that failed at least once (by default, those still
    `failed`), newest failure first. The error is its class, application code
    and SQLSTATE only; job arguments are never shown."""
    return await jobs.list_failed_jobs(
        uow.session, filters, page, registered=registered
    )


@router.get(
    "/tasks",
    dependencies=[require("jobs.manage")],
    responses=PERMISSION_RESPONSES,
)
async def list_tasks(registered: RegisteredTasks) -> JobTaskList:
    """The registered background tasks, sorted, and whether each may be
    retried."""
    return jobs.list_job_tasks(registered)


@router.post(
    "/{job_id}/retry",
    status_code=202,
    dependencies=[require("jobs.manage")],
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such job, or one that never failed."),
        409: problem_response_doc(
            "The task can't be retried (job_not_retryable); the job isn't failed"
            " (job_not_failed); a queued run of the task already covers it"
            " (job_already_queued)."
        ),
    },
)
async def retry_job(
    job_id: Annotated[int, Path(gt=0, le=2**63 - 1)],
    uow: UnitOfWorkDep,
    registered: RegisteredTasks,
) -> FailedJobItem:
    """Put a failed job back in the queue (the same job, one more attempt).
    The task re-checks everything pending, so a retry never duplicates
    charges or records. The worker picks it up on its next poll."""
    return await jobs.retry_job(uow.session, job_id, registered=registered)
