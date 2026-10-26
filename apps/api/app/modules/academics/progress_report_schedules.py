"""Progress report schedule rows and placeholders (#316 B2, B3, D2).

Every function runs in the caller's Unit of Work and never commits. They take
plain ids and values: who may call them and what is checked first is decided
by ``app.workflows.progress_report_schedules`` (the routes) and
``app.workflows.progress_report_schedule_tasks`` (the job), with the pure
rules in :mod:`~app.modules.academics.progress_report_schedule_rules`.
Writes take the schedule ``FOR UPDATE`` (:func:`get_schedule_for_update`,
re-read with ``populate_existing``); the automatic audit events record the
schedule diffs (spec §9).

:func:`insert_placeholder` is the job's one write on ``progress_reports``:
``INSERT … ON CONFLICT DO NOTHING RETURNING id`` with no conflict target, so
both the schedule's per-period index (a placeholder is never created twice,
even after a delete or a retraction) and #83's live-period index (the tutor
already wrote the report) make it a no-op.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from sqlalchemy import Table, and_, case, exists, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.errors import ProgressReportScheduleExistsError
from app.modules.academics.models import (
    PROGRESS_REPORT_SCHEDULE_ACTIVE_UNIQUE,
    ProgressReport,
    ProgressReportSchedule,
)
from app.modules.academics.progress_report_rules import DRAFT, RETRACTED, WEEKLY

# --- Reads ----------------------------------------------------------------------------


async def schedules_for_student(
    session: AsyncSession, student_id: uuid.UUID, *, limit: int
) -> list[ProgressReportSchedule]:
    """S1: the student's schedules, active first, then the newest created,
    at most ``limit``. One statement."""
    rows = await session.scalars(
        select(ProgressReportSchedule)
        .where(ProgressReportSchedule.student_id == student_id)
        .order_by(
            ProgressReportSchedule.is_active.desc(),
            ProgressReportSchedule.created_at.desc(),
            ProgressReportSchedule.id.desc(),
        )
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def get_schedule_for_update(
    session: AsyncSession, schedule_id: uuid.UUID
) -> ProgressReportSchedule | None:
    """The schedule ``FOR UPDATE``, re-read, or ``None`` when unknown."""
    return cast(
        ProgressReportSchedule | None,
        await session.scalar(
            select(ProgressReportSchedule)
            .where(ProgressReportSchedule.id == schedule_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


@dataclass(frozen=True)
class _Key:
    id: uuid.UUID
    student_id: uuid.UUID
    author_user_id: uuid.UUID
    cadence: str
    subject_id: uuid.UUID | None


async def _active_schedule_id(session: AsyncSession, key: _Key) -> uuid.UUID | None:
    """Another active schedule with the key (no autoflush: the refused
    change must not be sent again)."""
    subject = (
        ProgressReportSchedule.subject_id.is_(None)
        if key.subject_id is None
        else ProgressReportSchedule.subject_id == key.subject_id
    )
    with session.no_autoflush:
        found: uuid.UUID | None = await session.scalar(
            select(ProgressReportSchedule.id)
            .where(
                ProgressReportSchedule.student_id == key.student_id,
                ProgressReportSchedule.author_user_id == key.author_user_id,
                ProgressReportSchedule.cadence == key.cadence,
                ProgressReportSchedule.is_active.is_(True),
                ProgressReportSchedule.id != key.id,
                subject,
            )
            .limit(1)
        )
    return found


async def _flush_unique(
    session: AsyncSession,
    schedule: ProgressReportSchedule,
    changes: Mapping[str, Any] | None = None,
) -> None:
    """Add or change the schedule and flush, in a savepoint (the changes are
    made inside it, so a refused flush leaves the transaction usable); the
    active-key index raises :class:`ProgressReportScheduleExistsError`
    naming the other schedule."""
    key = _Key(
        id=schedule.id,
        student_id=schedule.student_id,
        author_user_id=schedule.author_user_id,
        cadence=schedule.cadence,
        subject_id=schedule.subject_id,
    )
    try:
        async with session.begin_nested():
            session.add(schedule)
            for name, value in (changes or {}).items():
                setattr(schedule, name, value)
            await session.flush()
    except IntegrityError as exc:
        if PROGRESS_REPORT_SCHEDULE_ACTIVE_UNIQUE in str(exc.orig):
            existing = await _active_schedule_id(session, key)
            raise ProgressReportScheduleExistsError(
                None if existing is None else str(existing)
            ) from None
        raise


async def create_schedule(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    author_user_id: uuid.UUID,
    cadence: str,
    subject_id: uuid.UUID | None,
    starts_on: date,
    created_by_user_id: uuid.UUID,
) -> ProgressReportSchedule:
    """S2: a new active schedule, flushed in a savepoint
    (:class:`ProgressReportScheduleExistsError` when the tutor already has an
    active one with the same cadence and subject for the student)."""
    schedule = ProgressReportSchedule(
        id=uuid.uuid7(),
        student_id=student_id,
        author_user_id=author_user_id,
        cadence=cadence,
        subject_id=subject_id,
        starts_on=starts_on,
        is_active=True,
        created_by_user_id=created_by_user_id,
    )
    await _flush_unique(session, schedule)
    return schedule


async def deactivate_schedule(
    session: AsyncSession, schedule: ProgressReportSchedule
) -> None:
    """S3 on the locked schedule: no new placeholders; reports stay."""
    schedule.is_active = False
    await session.flush()


async def reactivate_schedule(
    session: AsyncSession, schedule: ProgressReportSchedule, *, starts_on: date
) -> None:
    """S3 on the locked schedule: active again from ``starts_on``
    (:class:`ProgressReportScheduleExistsError` when another active schedule
    holds the key)."""
    await _flush_unique(session, schedule, {"is_active": True, "starts_on": starts_on})


# --- B3: what exists for a period -----------------------------------------------------


@dataclass(frozen=True)
class PeriodKey:
    """One schedule's latest ended period (B3)."""

    schedule_id: uuid.UUID
    student_id: uuid.UUID
    author_user_id: uuid.UUID
    kind: str
    subject_id: uuid.UUID | None
    period_start: date


@dataclass(frozen=True)
class PeriodReportRow:
    id: uuid.UUID
    schedule_id: uuid.UUID | None
    student_id: uuid.UUID
    author_user_id: uuid.UUID
    kind: str
    subject_id: uuid.UUID | None
    period_start: date
    status: str
    deleted: bool


async def period_reports(
    session: AsyncSession, keys: Collection[PeriodKey]
) -> list[PeriodReportRow]:
    """The reports of these periods: each schedule's own placeholders
    (deleted and retracted ones too) and the live, non-retracted reports of
    the same student, author, kind and period start (the subject is matched
    by the caller). One statement (none for no keys)."""
    if not keys:
        return []
    own = tuple_(ProgressReport.schedule_id, ProgressReport.period_start).in_(
        [(key.schedule_id, key.period_start) for key in keys]
    )
    matching = and_(
        tuple_(
            ProgressReport.student_id,
            ProgressReport.author_user_id,
            ProgressReport.kind,
            ProgressReport.period_start,
        ).in_(
            [
                (key.student_id, key.author_user_id, key.kind, key.period_start)
                for key in keys
            ]
        ),
        ProgressReport.deleted_at.is_(None),
        ProgressReport.status != RETRACTED,
    )
    rows = await session.execute(
        select(
            ProgressReport.id,
            ProgressReport.schedule_id,
            ProgressReport.student_id,
            ProgressReport.author_user_id,
            ProgressReport.kind,
            ProgressReport.subject_id,
            ProgressReport.period_start,
            ProgressReport.status,
            ProgressReport.deleted_at,
        ).where(or_(own, matching))
    )
    return [
        PeriodReportRow(
            id=row.id,
            schedule_id=row.schedule_id,
            student_id=row.student_id,
            author_user_id=row.author_user_id,
            kind=row.kind,
            subject_id=row.subject_id,
            period_start=row.period_start,
            status=row.status,
            deleted=row.deleted_at is not None,
        )
        for row in rows
    ]


def split_period_reports(
    key: PeriodKey, rows: Sequence[PeriodReportRow]
) -> tuple[PeriodReportRow | None, PeriodReportRow | None]:
    """``(the schedule's placeholder, a matching report)`` of one period
    among :func:`period_reports`' rows."""
    placeholder = manual = None
    for row in rows:
        if row.schedule_id == key.schedule_id and row.period_start == key.period_start:
            placeholder = row
        elif (
            not row.deleted
            and row.status != RETRACTED
            and row.student_id == key.student_id
            and row.author_user_id == key.author_user_id
            and row.kind == key.kind
            and row.period_start == key.period_start
            and row.subject_id == key.subject_id
        ):
            manual = row
    return placeholder, manual


# --- D2, B2: the job ------------------------------------------------------------------


async def due_schedule_ids(
    session: AsyncSession, *, weekly_start: date, monthly_start: date, limit: int
) -> list[uuid.UUID]:
    """D2: the active schedules whose latest ended period (``weekly_start``
    or ``monthly_start``, by cadence) is covered (``>= starts_on``) and has
    neither their own placeholder nor a live, non-retracted report of the
    same student, author, kind, period and subject; by id, at most
    ``limit``. One statement."""
    schedule = ProgressReportSchedule
    period_start = case(
        (schedule.cadence == WEEKLY, literal(weekly_start)),
        else_=literal(monthly_start),
    )
    report = ProgressReport
    own = exists().where(
        report.schedule_id == schedule.id, report.period_start == period_start
    )
    matching = exists().where(
        report.student_id == schedule.student_id,
        report.author_user_id == schedule.author_user_id,
        report.kind == schedule.cadence,
        report.period_start == period_start,
        report.subject_id.is_not_distinct_from(schedule.subject_id),
        report.deleted_at.is_(None),
        report.status != RETRACTED,
    )
    rows = await session.scalars(
        select(schedule.id)
        .where(
            schedule.is_active.is_(True),
            period_start >= schedule.starts_on,
            ~own,
            ~matching,
        )
        .order_by(schedule.id)
        .limit(limit)
    )
    return list(rows)


async def insert_placeholder(
    session: AsyncSession,
    schedule: ProgressReportSchedule,
    *,
    period_start: date,
    period_end: date,
) -> uuid.UUID | None:
    """B2: the schedule's empty ``draft`` for the period, or ``None`` when
    one of the unique indexes already holds it (its own placeholder, ever,
    or a live report the tutor wrote). One statement.

    The ``INSERT … ON CONFLICT DO NOTHING`` runs on the session's connection,
    not as an ORM statement: the audit guard refuses ORM bulk statements on
    audited tables because they skip the automatic events. The caller
    records the placeholder's own audit event
    (``academics.progress_report_placeholder_created``, B5) in the same
    transaction whenever a row comes back, so no insert is unaudited."""
    table = cast(Table, ProgressReport.__table__)
    statement = (
        insert(table)
        .values(
            id=uuid.uuid7(),
            student_id=schedule.student_id,
            author_user_id=schedule.author_user_id,
            subject_id=schedule.subject_id,
            kind=schedule.cadence,
            period_start=period_start,
            period_end=period_end,
            status=DRAFT,
            schedule_id=schedule.id,
        )
        .on_conflict_do_nothing()
        .returning(table.c.id)
    )
    connection = await session.connection()
    report_id: uuid.UUID | None = (await connection.execute(statement)).scalar()
    return report_id
