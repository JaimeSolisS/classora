"""The export routes (#124 B-C; ``_docs/api.md`` Exports).

All five are ``authenticated()`` plus :func:`exporter`: ``403
permission_denied`` unless the caller may export some registered dataset (a
staff role and the dataset's page permission). Tutors, students and guardians
get ``403`` everywhere. A job of another user is ``404``, the Owner included.

``POST /api/v1/exports``: ``401`` → ``403 csrf_failed`` → ``403
privacy_notice_required`` → ``403 permission_denied`` (no dataset) → ``422
validation_error`` (the body) → ``422 export_range_invalid`` → ``403
permission_denied`` (that dataset) → ``403 reauth_required`` (a personal
dataset; ``recent_auth_guard("data.personal_export")``) → ``409
export_limit_reached`` → ``202``. It queues ``reporting:run_exports`` after
the Unit of Work commits (a background task). No ``Idempotency-Key``: the job
is the unit, and the limit bounds repeats.

``GET …/{export_id}/download`` is neither reauthenticated nor audited
(#124 E5, E11) and answers ``Cache-Control: no-store``.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from procrastinate import App

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.jobs import defer_export_run, get_job_app
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.reauth import REAUTH_RESPONSES, RecentAuthGuard, recent_auth_guard
from app.core.uow import UnitOfWorkDep
from app.modules.reporting.exports import render, service
from app.modules.reporting.exports.datasets import ExportParameters
from app.modules.reporting.exports.schemas import (
    ExportDatasetList,
    ExportJobDetail,
    ExportJobStatus,
)

PERSONAL_EXPORT_ACTION: Final = "data.personal_export"

router = APIRouter(prefix="/api/v1/exports", tags=["exports"])

JobAppDep = Annotated[App, Depends(get_job_app)]

_JOB: Final = "/{export_id}"
_NOT_FOUND: Final = problem_response_doc(
    "No such export, or another user's (not_found)."
)


async def exporter(principal: Annotated[Principal, authenticated()]) -> Principal:
    """``403`` unless the caller may export some dataset, before the body."""
    service.ensure_may_export_any(principal)
    return principal


Exporter = Annotated[Principal, Depends(exporter)]
PersonalExportGuard = Annotated[
    RecentAuthGuard, recent_auth_guard(PERSONAL_EXPORT_ACTION)
]


@router.get("/datasets", responses={**PERMISSION_RESPONSES})
async def list_export_datasets(
    principal: Exporter, uow: UnitOfWorkDep
) -> ExportDatasetList:
    """The datasets the caller may export, in order, with their columns and
    labels in the caller's language."""
    return await service.list_datasets(uow.session, principal=principal)


@router.post(
    "",
    status_code=202,
    responses={
        **REAUTH_RESPONSES,
        409: problem_response_doc(
            "The caller already has 3 exports waiting (export_limit_reached;"
            " details.max)."
        ),
        422: problem_response_doc(
            "The body is not valid: an unknown dataset or column, a repeated"
            " column, a group_by that is not groupable or not selected"
            " (validation_error); a date range reversed or over 366 days"
            " (export_range_invalid)."
        ),
    },
)
async def request_export(
    principal: Exporter,
    guard: PersonalExportGuard,
    body: ExportParameters,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: JobAppDep,
) -> ExportJobDetail:
    """Ask for a CSV or Excel export of a list. It is built in the background
    from the same data the list page shows you; poll the export until it is
    `ready` (or `failed`), then download it within 24 hours. Exports that
    name people (`students`, `class_sessions`) need a recent password
    confirmation."""
    detail = await service.request_export(
        uow.session, principal=principal, parameters=body, guard=guard
    )
    # Runs after the response, so after the Unit of Work committed.
    background.add_task(defer_export_run, job_app)
    return detail


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc("A filter, limit or offset isn't valid."),
    },
)
async def list_exports(
    principal: Exporter,
    uow: UnitOfWorkDep,
    page: OffsetPagination,
    status: ExportJobStatus | None = None,
) -> OffsetPage[ExportJobDetail]:
    """Your own exports, newest first."""
    return await service.list_jobs(
        uow.session, principal=principal, status=status, page=page
    )


@router.get(_JOB, responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_export(
    principal: Exporter, export_id: uuid.UUID, uow: UnitOfWorkDep
) -> ExportJobDetail:
    """One of your exports."""
    return await service.get_job(uow.session, principal=principal, job_id=export_id)


@router.get(
    f"{_JOB}/download",
    response_class=Response,
    responses={
        200: {
            "description": "The file as an attachment.",
            "content": {
                render.CSV_MEDIA_TYPE: {"schema": {"type": "string"}},
                render.XLSX_MEDIA_TYPE: {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
        },
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The export is queued or failed: there is no file (export_not_ready)."
        ),
        410: problem_response_doc(
            "The file was deleted 24 hours after it was ready (export_expired)."
        ),
    },
)
async def download_export(
    principal: Exporter, export_id: uuid.UUID, uow: UnitOfWorkDep
) -> Response:
    """Download one of your ready exports. Repeatable until it expires."""
    file = await service.download(uow.session, principal=principal, job_id=export_id)
    return Response(
        content=file.content,
        media_type=file.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file.file_name}"',
            "Cache-Control": "no-store",
        },
    )
