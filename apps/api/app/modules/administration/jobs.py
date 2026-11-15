"""Failed background jobs: the list and the retry (#128 D; architecture
ISSUE-J3).

The list is driven by ``job_failures`` (one row per job that ever failed,
written by the worker middleware :func:`app.core.jobs.record_job_failure`)
joined to Procrastinate's ``procrastinate_jobs`` by primary key, so it never
scans the job table (#128 E8). The event times (first ``deferred``, the
``retried`` events) come from one aggregated statement per page.

A retry runs in the caller's Unit of Work (#128 E5): the job row is locked
``FOR UPDATE``, the checks run, Procrastinate's own
``procrastinate_retry_job_v2`` puts the job back to ``todo`` (same id,
``attempts + 1``, a ``retried`` event from its trigger) and the audit event
``administration.job_retried`` is written, all in one transaction. The
function sends no ``NOTIFY``: the worker picks the job up on its polling
interval (#128 D5).

Procrastinate's tables are not models (ADR-0003); they are read here through
lightweight ``table()`` constructs. This module imports no other module.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    String,
    and_,
    bindparam,
    cast,
    column,
    func,
    select,
    table,
    text,
    tuple_,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit_event
from app.core.authz import RecordNotFoundError
from app.core.job_failures import JobFailure
from app.core.jobs import RETRYABLE_TASKS
from app.core.pagination import (
    CursorPage,
    CursorParams,
    InvalidCursorError,
    build_cursor_page,
    decode_cursor,
)
from app.modules.administration.errors import (
    JobAlreadyQueuedError,
    JobNotFailedError,
    JobNotRetryableError,
)
from app.modules.administration.schemas import (
    FailedJobItem,
    JobStatusFilter,
    JobTaskItem,
    JobTaskList,
)

JOB_RETRIED_ACTION: Final = "administration.job_retried"
QUEUEING_LOCK_INDEX: Final = "procrastinate_jobs_queueing_lock_idx_v1"

_jobs = table(
    "procrastinate_jobs",
    column("id", BigInteger),
    column("queue_name", String),
    column("task_name", String),
    column("status", String),
    column("attempts", Integer),
    column("queueing_lock", String),
)
_events = table(
    "procrastinate_events",
    column("job_id", BigInteger),
    column("type", String),
    column("at", DateTime(timezone=True)),
)
# The enum columns compared as text (no index on status is needed: the rows
# are reached by primary key).
_job_status = cast(_jobs.c.status, String)
_event_type = cast(_events.c.type, String)

_RETRY: Final = text(
    "SELECT procrastinate_retry_job_v2(:job_id, now(), NULL, NULL, NULL)"
).bindparams(bindparam("job_id", type_=BigInteger))


@dataclass(frozen=True)
class JobFilters:
    """The list filters (#128 D1): ``failed_at`` in ``[failed_from, failed_to)``."""

    task: str | None = None
    status: JobStatusFilter = "failed"
    failed_from: datetime | None = None
    failed_to: datetime | None = None


@dataclass(frozen=True)
class _EventTimes:
    deferred_at: datetime | None
    retried_count: int
    last_retried_at: datetime | None


_NO_EVENTS: Final = _EventTimes(None, 0, None)


def _is_retryable(task_name: str, registered: frozenset[str]) -> bool:
    return task_name in RETRYABLE_TASKS and task_name in registered


def _position(cursor: str) -> tuple[datetime, int]:
    failed_at, job_id = decode_cursor(cursor, types=(str, int))
    try:
        when = datetime.fromisoformat(str(failed_at))
    except ValueError:
        raise InvalidCursorError() from None
    if when.tzinfo is None:
        raise InvalidCursorError()
    return when, int(job_id)  # type: ignore[arg-type]


def _rows_query() -> Any:
    return select(
        JobFailure.job_id,
        JobFailure.task_name,
        JobFailure.error_class,
        JobFailure.error_code,
        JobFailure.sqlstate,
        JobFailure.failed_at,
        _jobs.c.queue_name,
        _job_status.label("status"),
        _jobs.c.attempts,
    ).join(_jobs, _jobs.c.id == JobFailure.job_id)


async def _event_times(
    session: AsyncSession, job_ids: list[int]
) -> dict[int, _EventTimes]:
    """The first ``deferred`` and the ``retried`` events of ``job_ids`` (one
    statement)."""
    if not job_ids:
        return {}
    query = (
        select(
            _events.c.job_id,
            func.min(_events.c.at).filter(_event_type == "deferred"),
            func.count().filter(_event_type == "retried"),
            func.max(_events.c.at).filter(_event_type == "retried"),
        )
        .where(_events.c.job_id.in_(job_ids))
        .group_by(_events.c.job_id)
    )
    return {
        row[0]: _EventTimes(row[1], int(row[2]), row[3])
        for row in (await session.execute(query)).all()
    }


def _item(row: Any, events: _EventTimes, registered: frozenset[str]) -> FailedJobItem:
    return FailedJobItem(
        id=row.job_id,
        task_name=row.task_name,
        queue_name=row.queue_name,
        status=row.status,
        attempts=row.attempts,
        error_class=row.error_class,
        error_code=row.error_code,
        sqlstate=row.sqlstate,
        deferred_at=events.deferred_at,
        failed_at=row.failed_at,
        retried_count=events.retried_count,
        last_retried_at=events.last_retried_at,
        retryable=row.status == "failed" and _is_retryable(row.task_name, registered),
    )


def list_jobs_statement(filters: JobFilters, page: CursorParams) -> Any:
    """The page statement (also used by the plan test, #128 D1)."""
    query = _rows_query()
    if filters.status != "any":
        query = query.where(_job_status == filters.status)
    if filters.task is not None:
        query = query.where(JobFailure.task_name == filters.task)
    if filters.failed_from is not None:
        query = query.where(JobFailure.failed_at >= filters.failed_from)
    if filters.failed_to is not None:
        query = query.where(JobFailure.failed_at < filters.failed_to)
    if page.cursor is not None:
        when, job_id = _position(page.cursor)
        query = query.where(
            tuple_(JobFailure.failed_at, JobFailure.job_id) < tuple_(when, job_id)
        )
    return query.order_by(JobFailure.failed_at.desc(), JobFailure.job_id.desc()).limit(
        page.limit + 1
    )


async def list_failed_jobs(
    session: AsyncSession,
    filters: JobFilters,
    page: CursorParams,
    *,
    registered: frozenset[str],
) -> CursorPage[FailedJobItem]:
    """One page of jobs that failed at least once, newest failure first
    (ties by job id), with their event times (#128 D1, D2)."""
    rows = list((await session.execute(list_jobs_statement(filters, page))).all())
    events = await _event_times(session, [row.job_id for row in rows])
    items = [_item(row, events.get(row.job_id, _NO_EVENTS), registered) for row in rows]
    return build_cursor_page(
        items,
        limit=page.limit,
        key=lambda item: [item.failed_at.isoformat(), item.id],
    )


def list_job_tasks(registered: frozenset[str]) -> JobTaskList:
    """The registered tasks, sorted, with whether each may be retried (#128 D3)."""
    return JobTaskList(
        items=[
            JobTaskItem(task_name=name, retryable=name in RETRYABLE_TASKS)
            for name in sorted(registered)
        ]
    )


async def _failed_job(session: AsyncSession, job_id: int) -> FailedJobItem:
    row = (
        await session.execute(_rows_query().where(JobFailure.job_id == job_id))
    ).one()
    events = await _event_times(session, [job_id])
    # Only called right after a retry: the job is ``todo``, never retryable.
    return _item(row, events.get(job_id, _NO_EVENTS), frozenset())


async def queued_with_lock(
    session: AsyncSession, queueing_lock: str, job_id: int
) -> bool:
    """Whether another ``todo`` job holds ``queueing_lock`` (#128 D4 step 5)."""
    queued = await session.scalar(
        select(_jobs.c.id)
        .where(
            and_(
                _jobs.c.queueing_lock == queueing_lock,
                _job_status == "todo",
                _jobs.c.id != job_id,
            )
        )
        .limit(1)
    )
    return queued is not None


async def retry_job(
    session: AsyncSession, job_id: int, *, registered: frozenset[str]
) -> FailedJobItem:
    """Put a failed job back in the queue, audited (#128 D4).

    In order: ``404`` (no job, or a job that never failed) → lock the job
    row → ``409 job_not_retryable`` → ``409 job_not_failed`` → ``409
    job_already_queued`` (another ``todo`` job holds the same queueing lock)
    → ``procrastinate_retry_job_v2`` → the audit event. A race with a
    periodic tick on the queueing-lock index is ``409 job_already_queued``
    too, and the transaction rolls back (no audit event).
    """
    locked = (
        await session.execute(
            select(
                _jobs.c.task_name,
                _job_status.label("status"),
                _jobs.c.attempts,
                _jobs.c.queueing_lock,
            )
            .select_from(_jobs)
            .join(JobFailure, JobFailure.job_id == _jobs.c.id)
            .where(_jobs.c.id == job_id)
            .with_for_update(of=_jobs)
        )
    ).one_or_none()
    if locked is None:
        raise RecordNotFoundError()
    if not _is_retryable(locked.task_name, registered):
        raise JobNotRetryableError()
    if locked.status != "failed":
        raise JobNotFailedError()
    if locked.queueing_lock is not None and await queued_with_lock(
        session, locked.queueing_lock, job_id
    ):
        raise JobAlreadyQueuedError()
    try:
        async with session.begin_nested():
            await session.execute(_RETRY, {"job_id": job_id})
    except IntegrityError as exc:
        if QUEUEING_LOCK_INDEX in str(exc.orig):
            raise JobAlreadyQueuedError() from None
        raise
    record_audit_event(
        session,
        action=JOB_RETRIED_ACTION,
        details={
            "job_id": job_id,
            "task_name": locked.task_name,
            "attempts": locked.attempts,
        },
    )
    # The row was locked above, so it is still there.
    return await _failed_job(session, job_id)
