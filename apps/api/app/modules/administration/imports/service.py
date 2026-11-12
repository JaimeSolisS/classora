"""The import pipeline (#122; spec §9 imports; architecture ISSUE-L1).

**Create** (:func:`create_import_job`, ``POST /api/v1/imports``): ``403``
unless the caller may import the kind → parse the CSV in memory
(:mod:`.parsing`; ``422 import_file_invalid`` / ``import_too_many_rows``,
nothing written) → the kind's bulk :meth:`~.registry.ImportKind.prepare`
→ one ``import_jobs`` insert and **one** bulk insert of the staging rows. The
statement count doesn't depend on the row count (B7). The text itself is
never stored (E1).

**Reads**: a job of a kind the caller may not import is ``404 not_found``,
the same as an unknown id (C2); the list shows only the caller's kinds (C1).

**Commit** (:func:`request_commit`): the job ``FOR UPDATE`` → ``404`` →
``409 version_conflict`` → ``409 import_not_committable`` (not ``staged`` or
``failed``) → ``422 import_nothing_to_commit`` → ``committing`` with the
committer. The route queues ``administration:run_imports`` after the Unit of
Work commits; the task (``tasks.py``) then calls :func:`commit_next_batch`
in one Unit of Work per batch, acting as the committer:

1. the job ``FOR UPDATE`` (a job no longer ``committing`` is left alone);
2. ``pg_advisory_xact_lock`` on the kind, so two jobs of one kind never race
   a natural key;
3. the kind's ``prepare`` again on the batch's ``ready`` rows (``principal
   None``): a ``duplicate`` is ``skipped``, an ``error`` is ``failed``;
4. ``commit_row`` per remaining row in a savepoint: ``created``, ``skipped``
   or ``failed``; an ``AppError`` it raises is ``failed`` with its code. Any
   other exception escapes: the batch rolls back and the task marks the job
   ``failed`` (:func:`mark_job_failed`) in a new transaction.

Before step 2 the committer is re-checked (#640 E3): their current
principal through the loader the composition roots register
(:func:`~.registry.register_import_principal_loader`) and the kind's
``may_import``. Lost (not ``active``, or no longer allowed) →
:class:`ImportCommitterLostError`: nothing of the batch is written, and the
task marks the job ``failed``; a user who may import the kind resumes it
with the commit route, as the new committer.

When no ``ready`` row is left, the job is ``committed`` and every row's
``values`` is cleared in the same transaction (D3, E9).

**Discard** (:func:`discard_job`) and **expiry** (:func:`expire_jobs`, 7 days
after a ``staged`` or ``failed`` job's last change) delete the job's rows.

Staged values never reach a log line, a problem detail or an audit event:
``import_rows`` is not audited and errors carry codes only (F2).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, exists, func, insert, null, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.errors import AppError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration.approvals import _account_names
from app.modules.administration.imports.errors import (
    ImportNotCommittableError,
    ImportNotDiscardableError,
    ImportNothingToCommitError,
)
from app.modules.administration.imports.models import (
    COMMITTED,
    COMMITTING,
    CREATED,
    DISCARDED,
    DUPLICATE,
    ERROR,
    EXPIRABLE_STATUSES,
    EXPIRED,
    FAILED,
    OPEN_STATUSES,
    READY,
    ROW_FAILED,
    SKIPPED,
    STAGED,
    ImportJob,
    ImportRow,
)
from app.modules.administration.imports.parsing import parse_csv
from app.modules.administration.imports.registry import (
    CommitOutcome,
    ImportKind,
    ImportRowRef,
    RowIssue,
    RowResult,
    declared_row_code,
    import_kind,
    importable_kinds,
    load_committer,
)
from app.modules.administration.imports.schemas import (
    ImportCounts,
    ImportJobDetail,
    ImportJobStatus,
    ImportRowItem,
    ImportRowStatus,
)
from app.modules.administration.schemas import AccountName

RETENTION: Final = timedelta(days=7)
"""How long a ``staged`` or ``failed`` job keeps its rows after its last
change (E2)."""
EXPIRY_BATCH: Final = 100
"""Jobs expired per transaction."""

TOO_LONG: Final = "too_long"

COMMITTABLE_STATUSES: Final = (STAGED, FAILED)
"""The statuses a commit or a discard starts from."""


# --- Helpers -------------------------------------------------------------------------


def _issues(items: Iterable[RowIssue]) -> list[dict[str, Any]]:
    return [{"column": item.column, "code": item.code} for item in items]


def _counts(job: ImportJob) -> ImportCounts:
    return ImportCounts(
        total=job.total_rows,
        ready=job.ready_rows,
        duplicate=job.duplicate_rows,
        error=job.error_rows,
        created=job.created_rows,
        skipped=job.skipped_rows,
        failed=job.failed_rows,
    )


def _set_counts(job: ImportJob, by_status: Mapping[str, int]) -> None:
    job.ready_rows = by_status.get(READY, 0)
    job.duplicate_rows = by_status.get(DUPLICATE, 0)
    job.error_rows = by_status.get(ERROR, 0)
    job.created_rows = by_status.get(CREATED, 0)
    job.skipped_rows = by_status.get(SKIPPED, 0)
    job.failed_rows = by_status.get(ROW_FAILED, 0)
    job.total_rows = sum(by_status.values())


async def _refresh_counts(session: AsyncSession, job: ImportJob) -> None:
    rows = await session.execute(
        select(ImportRow.status, func.count())
        .where(ImportRow.job_id == job.id)
        .group_by(ImportRow.status)
    )
    _set_counts(job, {status: count for status, count in rows})


def _expires_at(job: ImportJob) -> datetime | None:
    if job.status in EXPIRABLE_STATUSES:
        return job.updated_at + RETENTION
    return None


def _detail(job: ImportJob, names: Mapping[uuid.UUID, AccountName]) -> ImportJobDetail:
    committer = job.committed_by_user_id
    return ImportJobDetail(
        id=job.id,
        kind=job.kind,
        status=job.status,  # type: ignore[arg-type]
        file_name=job.file_name,
        ignored_columns=list(job.ignored_columns),
        counts=_counts(job),
        created_by=names[job.created_by_user_id],
        created_at=job.created_at,
        committed_by=None if committer is None else names.get(committer),
        committed_at=job.committed_at,
        expires_at=_expires_at(job),
        version=job.version,
    )


async def _job_detail(session: AsyncSession, job: ImportJob) -> ImportJobDetail:
    names = await _account_names(
        session, (job.created_by_user_id, job.committed_by_user_id)
    )
    return _detail(job, names)


def _kind_or_none(principal: Principal, code: str) -> ImportKind | None:
    kind = import_kind(code)
    if kind is None or not kind.may_import(principal):
        return None
    return kind


async def _visible_job(
    session: AsyncSession, principal: Principal, job_id: uuid.UUID, *, lock: bool
) -> tuple[ImportJob, ImportKind]:
    """The job, or ``404`` when it's missing or of a kind the caller may not
    import (hidden looks like missing)."""
    query = select(ImportJob).where(ImportJob.id == job_id)
    if lock:
        query = query.with_for_update()
    job = await session.scalar(query.execution_options(populate_existing=True))
    if job is None:
        raise RecordNotFoundError()
    kind = _kind_or_none(principal, job.kind)
    if kind is None:
        raise RecordNotFoundError()
    return job, kind


def ensure_may_import_any(principal: Principal) -> None:
    """``403 permission_denied`` unless the caller may import some kind."""
    if not importable_kinds(principal):
        raise PermissionDeniedError()


# --- Create (B) ----------------------------------------------------------------------


def _with_too_long(result: RowResult, too_long: Sequence[str]) -> RowResult:
    """Add the pipeline's ``too_long`` errors (B2) to the kind's verdict."""
    if not too_long:
        return result
    flagged = {
        (issue.column, issue.code) for issue in result.errors if issue.code == TOO_LONG
    }
    extra = tuple(
        RowIssue(column, TOO_LONG)
        for column in too_long
        if (column, TOO_LONG) not in flagged
    )
    return RowResult(
        status="error", errors=result.errors + extra, warnings=result.warnings
    )


async def create_import_job(
    session: AsyncSession,
    *,
    principal: Principal,
    kind_code: str,
    file_name: str | None,
    csv_text: str,
) -> ImportJobDetail:
    """Parse, validate and stage a CSV (B1-B8). ``kind_code`` is registered
    (the route answers ``422`` otherwise)."""
    kind = _kind_or_none(principal, kind_code)
    if kind is None:
        raise PermissionDeniedError()
    parsed = parse_csv(csv_text, kind.columns)
    results = await kind.prepare(
        session, [row.values for row in parsed.rows], principal
    )
    if len(results) != len(parsed.rows):
        raise RuntimeError(f"import kind {kind.code!r} returned the wrong row count")
    results = [
        _with_too_long(result, row.too_long)
        for row, result in zip(parsed.rows, results, strict=True)
    ]
    job = ImportJob(
        id=uuid.uuid7(),
        kind=kind.code,
        status=STAGED,
        file_name=file_name,
        ignored_columns=list(parsed.ignored_columns),
        created_by_user_id=principal.user_id,
    )
    _set_counts(job, Counter(result.status for result in results))
    session.add(job)
    await session.flush()
    await session.execute(
        insert(ImportRow),
        [
            {
                "job_id": job.id,
                "row_number": row.row_number,
                "status": result.status,
                "values": row.values,
                "errors": _issues(result.errors),
                "warnings": _issues(result.warnings),
                "record_id": None,
            }
            for row, result in zip(parsed.rows, results, strict=True)
        ],
    )
    return await _job_detail(session, job)


# --- Reads (C) -----------------------------------------------------------------------


async def list_import_jobs(
    session: AsyncSession,
    *,
    principal: Principal,
    kind: str | None,
    status: ImportJobStatus | None,
    page: OffsetParams,
) -> OffsetPage[ImportJobDetail]:
    """The jobs of the kinds the caller may import, newest first."""
    kinds = importable_kinds(principal)
    if not kinds:
        raise PermissionDeniedError()
    query = select(ImportJob).where(ImportJob.kind.in_(kinds))
    if kind is not None:
        query = query.where(ImportJob.kind == kind)
    if status is not None:
        query = query.where(ImportJob.status == status)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    jobs = list(
        await session.scalars(
            query.order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    names = await _account_names(
        session,
        (
            user_id
            for job in jobs
            for user_id in (job.created_by_user_id, job.committed_by_user_id)
        ),
    )
    return OffsetPage[ImportJobDetail](
        items=[_detail(job, names) for job in jobs],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def get_import_job(
    session: AsyncSession, *, principal: Principal, job_id: uuid.UUID
) -> ImportJobDetail:
    job, _ = await _visible_job(session, principal, job_id, lock=False)
    return await _job_detail(session, job)


def _row_item(row: ImportRow, *, open_job: bool) -> ImportRowItem:
    return ImportRowItem.model_validate(
        {
            "row_number": row.row_number,
            "status": row.status,
            "values": row.values if open_job else None,
            "errors": row.errors,
            "warnings": row.warnings,
            "record_id": row.record_id,
        }
    )


async def list_import_rows(
    session: AsyncSession,
    *,
    principal: Principal,
    job_id: uuid.UUID,
    status: ImportRowStatus | None,
    page: OffsetParams,
) -> OffsetPage[ImportRowItem]:
    """A job's rows in file order; ``values`` only while the job is open."""
    job, _ = await _visible_job(session, principal, job_id, lock=False)
    query = select(ImportRow).where(ImportRow.job_id == job.id)
    if status is not None:
        query = query.where(ImportRow.status == status)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(
        query.order_by(ImportRow.row_number).limit(page.limit).offset(page.offset)
    )
    open_job = job.status in OPEN_STATUSES
    return OffsetPage[ImportRowItem](
        items=[_row_item(row, open_job=open_job) for row in rows],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


# --- Commit request (D1) and discard (E1) --------------------------------------------


async def request_commit(
    session: AsyncSession, *, principal: Principal, job_id: uuid.UUID, version: int
) -> ImportJobDetail:
    """Mark the job ``committing`` (the task does the work)."""
    job, _ = await _visible_job(session, principal, job_id, lock=True)
    ensure_version(job, version)
    if job.status not in COMMITTABLE_STATUSES:
        raise ImportNotCommittableError()
    has_ready = await session.scalar(
        select(exists().where(ImportRow.job_id == job.id, ImportRow.status == READY))
    )
    if not has_ready:
        raise ImportNothingToCommitError()
    job.status = COMMITTING
    job.committed_by_user_id = principal.user_id
    await session.flush()
    return await _job_detail(session, job)


async def _delete_rows(session: AsyncSession, job_ids: Sequence[uuid.UUID]) -> None:
    if job_ids:
        await session.execute(delete(ImportRow).where(ImportRow.job_id.in_(job_ids)))


async def discard_job(
    session: AsyncSession, *, principal: Principal, job_id: uuid.UUID, version: int
) -> ImportJobDetail:
    """Discard a ``staged`` or ``failed`` job and delete its rows (records a
    failed job already created stay)."""
    job, _ = await _visible_job(session, principal, job_id, lock=True)
    ensure_version(job, version)
    if job.status not in COMMITTABLE_STATUSES:
        raise ImportNotDiscardableError()
    await _delete_rows(session, [job.id])
    job.status = DISCARDED
    await session.flush()
    return await _job_detail(session, job)


# --- The task's units (D2, D3, E2) ----------------------------------------------------


class ImportCommitterLostError(RuntimeError):
    """The committer may no longer import the job's kind (#640 E3): unknown
    or not ``active`` now, or ``kind.may_import`` is false. The batch is not
    committed; the run marks the job ``failed``."""

    def __init__(self, job_id: uuid.UUID, kind_code: str) -> None:
        super().__init__(f"import job {job_id}: the committer lost access")
        self.job_id = job_id
        self.kind_code = kind_code


@dataclass(frozen=True)
class CommittingJob:
    id: uuid.UUID
    committed_by_user_id: uuid.UUID


async def committing_jobs(session: AsyncSession) -> list[CommittingJob]:
    """The ``committing`` jobs, oldest first (no lock)."""
    rows = await session.execute(
        select(ImportJob.id, ImportJob.committed_by_user_id)
        .where(ImportJob.status == COMMITTING)
        .order_by(ImportJob.created_at, ImportJob.id)
    )
    return [
        CommittingJob(id=job_id, committed_by_user_id=user_id)
        for job_id, user_id in rows
        if user_id is not None
    ]


@dataclass(frozen=True)
class _RowUpdate:
    row_number: int
    status: str
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    record_id: uuid.UUID | None = None


def _outcome_update(
    row_number: int, warnings: list[dict[str, Any]], outcome: CommitOutcome
) -> _RowUpdate:
    code_issue = (
        [] if outcome.code is None else [{"column": None, "code": outcome.code}]
    )
    if outcome.status == "created":
        if outcome.record_id is None:
            raise RuntimeError("a created outcome needs a record_id")
        return _RowUpdate(row_number, CREATED, [], warnings, outcome.record_id)
    if outcome.status == "skipped":
        return _RowUpdate(row_number, SKIPPED, [], warnings + code_issue)
    return _RowUpdate(
        row_number, ROW_FAILED, code_issue + _issues(outcome.errors), warnings
    )


async def _commit_one(
    session: AsyncSession,
    kind: ImportKind,
    values: Mapping[str, str],
    ref: ImportRowRef,
    actor_user_id: uuid.UUID,
) -> CommitOutcome:
    """``commit_row`` in a savepoint: kept only for ``created``."""
    savepoint = await session.begin_nested()
    try:
        outcome = await kind.commit_row(session, values, ref, actor_user_id)
    except AppError as exc:
        await savepoint.rollback()
        # A refusal without a row label (#130 B1) shows as ``invalid``.
        return CommitOutcome.failed(declared_row_code(exc.code))
    except BaseException:
        await savepoint.rollback()
        raise
    if outcome.status == "created":
        await savepoint.commit()
    else:
        await savepoint.rollback()
    return outcome


async def commit_next_batch(session: AsyncSession, job_id: uuid.UUID) -> bool:
    """Commit the job's next batch of ``ready`` rows; ``True`` when the job is
    done (``committed`` now, or no longer ``committing``). Raises
    :class:`ImportCommitterLostError`, before writing anything, when the
    committer may no longer import the kind (#640 E3)."""
    job = await session.scalar(
        select(ImportJob)
        .where(ImportJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status != COMMITTING:
        return True
    actor = job.committed_by_user_id
    kind = import_kind(job.kind)
    if kind is None or actor is None:
        raise RuntimeError(f"import job {job.id} can't be committed")
    # #640 E3: the committer's current principal and the kind's full check,
    # before every batch (the privacy notice is not re-checked).
    committer = await load_committer(session, actor)
    if committer is None or not kind.may_import(committer):
        raise ImportCommitterLostError(job.id, kind.code)
    await session.execute(
        select(
            func.pg_advisory_xact_lock(func.hashtextextended(f"import:{kind.code}", 0))
        )
    )
    batch = (
        await session.execute(
            select(ImportRow.row_number, ImportRow.values, ImportRow.warnings)
            .where(ImportRow.job_id == job.id, ImportRow.status == READY)
            .order_by(ImportRow.row_number)
            .limit(kind.batch_size)
        )
    ).all()
    if not batch:
        job.status = COMMITTED
        job.committed_at = clock.now()
        await session.execute(
            update(ImportRow).where(ImportRow.job_id == job.id).values(values=null())
        )
        await _refresh_counts(session, job)
        await session.flush()
        return True
    job_ref = job.id
    checks = await kind.prepare(
        session, [dict(values or {}) for _, values, _ in batch], None
    )
    updates: list[_RowUpdate] = []
    for (row_number, values, warnings), check in zip(batch, checks, strict=True):
        if check.status == DUPLICATE:
            updates.append(_RowUpdate(row_number, SKIPPED, [], _issues(check.warnings)))
            continue
        if check.status == ERROR:
            updates.append(
                _RowUpdate(row_number, ROW_FAILED, _issues(check.errors), warnings)
            )
            continue
        outcome = await _commit_one(
            session, kind, dict(values or {}), ImportRowRef(job_ref, row_number), actor
        )
        updates.append(_outcome_update(row_number, list(warnings), outcome))
    await session.execute(
        update(ImportRow),
        [
            {
                "job_id": job_ref,
                "row_number": item.row_number,
                "status": item.status,
                "errors": item.errors,
                "warnings": item.warnings,
                "record_id": item.record_id,
            }
            for item in updates
        ],
    )
    job = await session.scalar(
        select(ImportJob)
        .where(ImportJob.id == job_ref)
        .execution_options(populate_existing=True)
    )
    if job is None:  # pragma: no cover - the row is locked
        raise RuntimeError("import job vanished")
    await _refresh_counts(session, job)
    await session.flush()
    return False


async def mark_job_failed(session: AsyncSession, job_id: uuid.UUID) -> None:
    """After a batch failed: a ``committing`` job becomes ``failed`` (its
    ready rows wait for a retry)."""
    job = await session.scalar(
        select(ImportJob)
        .where(ImportJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status != COMMITTING:
        return
    job.status = FAILED
    await _refresh_counts(session, job)
    await session.flush()


async def expire_jobs(session: AsyncSession) -> int:
    """Expire up to :data:`EXPIRY_BATCH` ``staged`` or ``failed`` jobs whose
    last change is over :data:`RETENTION` old, deleting their rows. Returns
    how many."""
    cutoff = clock.now() - RETENTION
    jobs = list(
        await session.scalars(
            select(ImportJob)
            .where(
                ImportJob.status.in_(EXPIRABLE_STATUSES),
                ImportJob.updated_at < cutoff,
            )
            .order_by(ImportJob.updated_at, ImportJob.id)
            .limit(EXPIRY_BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    await _delete_rows(session, [job.id for job in jobs])
    for job in jobs:
        job.status = EXPIRED
    await session.flush()
    return len(jobs)
