"""The export pipeline (#124; spec §8 reports and exports; architecture §6,
§7.3).

**Request** (:func:`request_export`, ``POST /api/v1/exports``): the body is
already a valid :data:`~.datasets.ExportParameters` → ``422
export_range_invalid`` → ``403 permission_denied`` unless
:func:`~.registry.may_export` → ``403 reauth_required`` for a personal
dataset without a recent reauthentication (``data.personal_export``) →
``409 export_limit_reached`` with :data:`~.errors.MAX_QUEUED_PER_USER`
queued (under a per-user advisory lock) → one ``queued`` job with the
normalized parameters, the caller's stored language and the file name. The
route queues ``reporting:run_exports`` after the Unit of Work commits.

**Reads and download**: only the requester's own jobs exist for them; any
other job is ``404 not_found``, the Owner included (C1-C3). The download
checks ``may_export`` again (``403``), then ``409 export_not_ready`` for
``queued``/``failed`` and ``410 export_expired`` for ``expired`` or a
``ready`` job past ``expires_at``.

**Run** (:func:`run_job`, one Unit of Work per job acting as the requester;
``tasks.py``): ``REPEATABLE READ`` → the job ``FOR UPDATE SKIP LOCKED``
(skipped when locked or no longer ``queued``) → the requester's principal,
reloaded (``permission_lost`` when inactive or no longer allowed) → the
dataset's rows (``row_limit_exceeded`` past :data:`MAX_ROWS`) → grouping
and rendering (``size_limit_exceeded`` past :data:`MAX_FILE_BYTES`) →
``export_files`` + ``ready`` + ``reporting.export_ready``, or ``failed`` +
``reporting.export_failed`` without a file. An unexpected exception rolls
the job's transaction back; the task then marks it ``failed`` (``error``) in
a new one (:func:`mark_job_failed`).

**Purge** (:func:`purge_expired`): ``ready`` jobs with ``expires_at ≤ now``
lose their file and become ``expired`` (no actor: a system job).

The bytes never reach a log line, a problem detail, a notification or an
audit event (``export_files`` is not audited). Every function runs in the
caller's Unit of Work and never commits.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.i18n import Language, resolve_language
from app.core.pagination import OffsetPage, OffsetParams
from app.core.reauth import RecentAuthGuard
from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.identity.service import STAFF_ROLES
from app.modules.reporting.exports import render
from app.modules.reporting.exports.datasets import BUILT_IN_DATASETS, ExportParameters
from app.modules.reporting.exports.errors import (
    MAX_QUEUED_PER_USER,
    ExportExpiredError,
    ExportLimitReachedError,
    ExportNotReadyError,
)
from app.modules.reporting.exports.models import (
    ERROR,
    EXPIRED,
    FAILED,
    PERMISSION_LOST,
    QUEUED,
    READY,
    ROW_LIMIT_EXCEEDED,
    SIZE_LIMIT_EXCEEDED,
    XLSX,
    ExportFile,
    ExportJob,
)
from app.modules.reporting.exports.registry import (
    ExportDataset,
    ExportParametersBase,
    RowLimitExceeded,
    export_dataset,
    exportable_datasets,
    may_export,
    register_export_dataset,
)
from app.modules.reporting.exports.schemas import (
    ExportColumnItem,
    ExportDatasetItem,
    ExportDatasetList,
    ExportJobDetail,
    ExportJobStatus,
)

logger = logging.getLogger(__name__)

MAX_ROWS: Final = 10_000
"""The most rows an export may have (#124 E6); more fails the job."""
MAX_FILE_BYTES: Final = 16 * 1024 * 1024
"""The largest file (16 MiB, #124 E6); larger fails the job."""
RETENTION: Final = timedelta(hours=24)
"""How long a ready file is kept (#124 E1)."""
PURGE_BATCH: Final = 100
"""Ready jobs expired per purge transaction (#124 D4)."""

EXPORT_READY: Final = "reporting.export_ready"
EXPORT_FAILED: Final = "reporting.export_failed"


class ExportReadyParams(BaseModel):
    """``reporting.export_ready``: ids and codes only (#124 G1)."""

    model_config = ConfigDict(extra="forbid")

    export_id: str
    dataset: str
    format: str
    row_count: int


class ExportFailedParams(BaseModel):
    """``reporting.export_failed``: ids and codes only (#124 G1)."""

    model_config = ConfigDict(extra="forbid")

    export_id: str
    dataset: str
    error_code: str


def register_export_wiring() -> None:
    """The three datasets and the two notification types (#124 A3, G1);
    ``app.wiring`` calls it in the API, the CLI and the worker. Registering
    again changes nothing."""
    for dataset in BUILT_IN_DATASETS:
        register_export_dataset(dataset)
    for code, model in (
        (EXPORT_READY, ExportReadyParams),
        (EXPORT_FAILED, ExportFailedParams),
    ):
        administration.register_notification_type(
            code, model, mandatory=False, recipient_roles=STAFF_ROLES
        )


def ensure_may_export_any(principal: Principal) -> None:
    """``403`` unless the caller may export some registered dataset."""
    if not exportable_datasets(principal):
        raise PermissionDeniedError()


async def _language(session: AsyncSession, user_id: uuid.UUID) -> Language:
    preferences = await identity.get_user_preferences(session, user_id)
    return resolve_language(preferences.language)


# --- Datasets (B1) --------------------------------------------------------------------


async def list_datasets(
    session: AsyncSession, *, principal: Principal
) -> ExportDatasetList:
    """The datasets the caller may export, labelled in their language."""
    datasets = exportable_datasets(principal)
    if not datasets:
        raise PermissionDeniedError()
    language = await _language(session, principal.user_id)
    return ExportDatasetList(
        items=[
            ExportDatasetItem(
                code=dataset.code,
                label=render.dataset_label(language, dataset.code),
                personal=dataset.personal,
                date_filter=dataset.date_filter,
                columns=[
                    ExportColumnItem(
                        code=column.code,
                        kind=column.kind,
                        groupable=column.groupable,
                        label=render.header_label(language, dataset, column.code),
                    )
                    for column in dataset.columns
                ],
            )
            for dataset in datasets
        ]
    )


# --- Request (B2) ---------------------------------------------------------------------


def _detail(job: ExportJob) -> ExportJobDetail:
    return ExportJobDetail.model_validate(
        {
            "id": job.id,
            "dataset": job.dataset,
            "format": job.format,
            "status": job.status,
            "parameters": job.parameters,
            "language": job.language,
            "row_count": job.row_count,
            "byte_size": job.byte_size,
            "file_name": job.file_name,
            "error_code": job.error_code,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "expires_at": job.expires_at,
        }
    )


async def _lock_requester(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Serialize one user's requests, so two can't both pass the limit."""
    key = func.hashtextextended(f"reporting.exports:{user_id}", 0)
    await session.execute(select(func.pg_advisory_xact_lock(key)))


async def request_export(
    session: AsyncSession,
    *,
    principal: Principal,
    parameters: ExportParameters,
    guard: RecentAuthGuard,
) -> ExportJobDetail:
    """Queue an export (#124 B2, steps 6-10)."""
    dataset = export_dataset(parameters.dataset)
    if dataset is None:  # pragma: no cover - the body names a built-in dataset
        raise RecordNotFoundError()
    if dataset.check_filters is not None:
        dataset.check_filters(parameters.filter_values())
    if not may_export(principal, dataset):
        raise PermissionDeniedError()
    if dataset.personal:
        guard.ensure()
    await _lock_requester(session, principal.user_id)
    queued = await session.scalar(
        select(func.count())
        .select_from(ExportJob)
        .where(
            ExportJob.requested_by_user_id == principal.user_id,
            ExportJob.status == QUEUED,
        )
    )
    if (queued or 0) >= MAX_QUEUED_PER_USER:
        raise ExportLimitReachedError()
    zone = ZoneInfo(await administration.get_center_time_zone(session))
    job = ExportJob(
        requested_by_user_id=principal.user_id,
        dataset=dataset.code,
        format=parameters.export_format(),
        status=QUEUED,
        parameters=parameters.model_dump(mode="json", by_alias=True),
        language=await _language(session, principal.user_id),
        file_name=render.file_name(
            dataset.code, parameters.export_format(), clock.now().astimezone(zone)
        ),
    )
    session.add(job)
    await session.flush()
    return _detail(job)


# --- Reads and download (C) -----------------------------------------------------------


async def list_jobs(
    session: AsyncSession,
    *,
    principal: Principal,
    status: ExportJobStatus | None,
    page: OffsetParams,
) -> OffsetPage[ExportJobDetail]:
    """The caller's own exports, newest first."""
    query = select(ExportJob).where(ExportJob.requested_by_user_id == principal.user_id)
    if status is not None:
        query = query.where(ExportJob.status == status)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    jobs = list(
        await session.scalars(
            query.order_by(ExportJob.created_at.desc(), ExportJob.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    return OffsetPage[ExportJobDetail](
        items=[_detail(job) for job in jobs],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def _own_job(
    session: AsyncSession, principal: Principal, job_id: uuid.UUID
) -> ExportJob:
    job = await session.scalar(
        select(ExportJob).where(
            ExportJob.id == job_id,
            ExportJob.requested_by_user_id == principal.user_id,
        )
    )
    if job is None:
        raise RecordNotFoundError()
    return job


async def get_job(
    session: AsyncSession, *, principal: Principal, job_id: uuid.UUID
) -> ExportJobDetail:
    """The caller's export; anyone else's is ``404``."""
    return _detail(await _own_job(session, principal, job_id))


@dataclass(frozen=True)
class ExportDownload:
    """The file of a ready export."""

    content: bytes
    file_name: str
    media_type: str


async def download(
    session: AsyncSession, *, principal: Principal, job_id: uuid.UUID
) -> ExportDownload:
    """The caller's ready file (#124 C3): ``404`` → ``403`` → ``409`` →
    ``410``."""
    job = await _own_job(session, principal, job_id)
    dataset = export_dataset(job.dataset)
    if dataset is None or not may_export(principal, dataset):
        raise PermissionDeniedError()
    if job.status in (QUEUED, FAILED):
        raise ExportNotReadyError()
    if job.status == EXPIRED or job.expires_at is None or job.expires_at <= clock.now():
        raise ExportExpiredError()
    content = await session.scalar(
        select(ExportFile.content).where(ExportFile.job_id == job.id)
    )
    if content is None:
        raise ExportExpiredError()
    return ExportDownload(
        content=content,
        file_name=job.file_name,
        media_type=render.XLSX_MEDIA_TYPE
        if job.format == XLSX
        else (render.CSV_MEDIA_TYPE),
    )


# --- Run (D) --------------------------------------------------------------------------


@dataclass(frozen=True)
class QueuedJob:
    id: uuid.UUID
    requested_by_user_id: uuid.UUID


async def queued_jobs(session: AsyncSession) -> list[QueuedJob]:
    """Every ``queued`` job, oldest first (no lock)."""
    rows = await session.execute(
        select(ExportJob.id, ExportJob.requested_by_user_id)
        .where(ExportJob.status == QUEUED)
        .order_by(ExportJob.created_at, ExportJob.id)
    )
    return [QueuedJob(row.id, row.requested_by_user_id) for row in rows]


async def _notify(session: AsyncSession, job: ExportJob) -> None:
    params: BaseModel
    if job.status == READY:
        params = ExportReadyParams(
            export_id=str(job.id),
            dataset=job.dataset,
            format=job.format,
            row_count=job.row_count or 0,
        )
        notification_type = EXPORT_READY
    else:
        params = ExportFailedParams(
            export_id=str(job.id),
            dataset=job.dataset,
            error_code=job.error_code or ERROR,
        )
        notification_type = EXPORT_FAILED
    await administration.create_notification(
        session,
        recipient_user_id=job.requested_by_user_id,
        notification_type=notification_type,
        params=params,
    )


async def _fail(session: AsyncSession, job: ExportJob, error_code: str) -> None:
    job.status = FAILED
    job.error_code = error_code
    job.finished_at = clock.now()
    await session.flush()
    await _notify(session, job)


def _render(
    job: ExportJob, dataset: ExportDataset, parameters: ExportParametersBase, rows: Any
) -> bytes:
    ordered = render.grouped(rows, dataset, parameters.grouping(), job.language)
    columns = parameters.selected_columns()
    if job.format == XLSX:
        return render.render_xlsx(job.language, dataset, columns, ordered)
    return render.render_csv(job.language, dataset, columns, ordered)


async def run_job(session: AsyncSession, job_id: uuid.UUID) -> str | None:
    """Build one job in the caller's (fresh) Unit of Work (#124 D1, D2).
    Returns the job's new status, or ``None`` when it was skipped (locked by
    another run, or no longer ``queued``)."""
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    job = await session.scalar(
        select(ExportJob)
        .where(ExportJob.id == job_id, ExportJob.status == QUEUED)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    dataset = export_dataset(job.dataset)
    if dataset is None:
        await _fail(session, job, ERROR)
        return FAILED
    principal = await identity.load_principal(session, job.requested_by_user_id)
    if principal is None or not may_export(principal, dataset):
        await _fail(session, job, PERMISSION_LOST)
        return FAILED
    parameters = dataset.parameters_model.model_validate(job.parameters)
    try:
        rows = await dataset.rows(
            session,
            principal,
            parameters.filter_values(),
            limit=MAX_ROWS,
            language=job.language,
        )
    except RowLimitExceeded:
        await _fail(session, job, ROW_LIMIT_EXCEEDED)
        return FAILED
    content = _render(job, dataset, parameters, rows)
    if len(content) > MAX_FILE_BYTES:
        await _fail(session, job, SIZE_LIMIT_EXCEEDED)
        return FAILED
    finished = clock.now()
    session.add(ExportFile(job_id=job.id, content=content))
    job.status = READY
    job.row_count = len(rows)
    job.byte_size = len(content)
    job.finished_at = finished
    job.expires_at = finished + RETENTION
    await session.flush()
    await _notify(session, job)
    return READY


async def mark_job_failed(session: AsyncSession, job_id: uuid.UUID) -> bool:
    """After an unexpected exception: a still ``queued`` job becomes
    ``failed`` (``error``) with its notification, so it never loops (D2).
    Returns whether it changed."""
    job = await session.scalar(
        select(ExportJob)
        .where(ExportJob.id == job_id, ExportJob.status == QUEUED)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return False
    await _fail(session, job, ERROR)
    return True


async def purge_expired(session: AsyncSession) -> int:
    """Expire up to :data:`PURGE_BATCH` ``ready`` jobs whose ``expires_at``
    is now or earlier, deleting their files (D4). Returns how many."""
    jobs = list(
        await session.scalars(
            select(ExportJob)
            .where(ExportJob.status == READY, ExportJob.expires_at <= clock.now())
            .order_by(ExportJob.expires_at, ExportJob.id)
            .limit(PURGE_BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    if not jobs:
        return 0
    await session.execute(
        delete(ExportFile).where(ExportFile.job_id.in_([job.id for job in jobs]))
    )
    for job in jobs:
        job.status = EXPIRED
    await session.flush()
    return len(jobs)
