"""Progress report rows (#83 B1, C, D, E, F).

Every function runs in the caller's Unit of Work and never commits. They
take plain ids and values: who may call them is decided by
``app.workflows.progress_reports`` with the pure rules in
:mod:`~app.modules.academics.progress_report_rules`. Writes take the report
``FOR UPDATE`` (:func:`get_report_for_update`, re-read with
``populate_existing``); the automatic audit events record the diffs (spec
§9). Deleted reports are never returned.

The reads join a caller-built, **unexecuted** ``(id, name)`` select of the
subjects as a subquery, so a report and its subject's name come in one
statement while academics never imports catalog (#77 E2).

**Family reads** (:func:`approved_page`, :func:`approved_report`) return
only ``approved_for_family`` rows: drafts, submitted, sent-back, retracted
and deleted reports never leave this module through them.
"""

import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import Select, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.modules.academics.errors import ProgressReportExistsError
from app.modules.academics.models import (
    PROGRESS_REPORT_PERIOD_UNIQUE,
    ProgressReport,
)
from app.modules.academics.progress_report_rules import (
    APPROVED,
    CHANGES_REQUESTED,
    DRAFT,
    ON_DEMAND,
    RETRACTED,
    SUBMITTED,
)

NARRATIVE: tuple[str, ...] = (
    "summary",
    "strengths",
    "areas_for_improvement",
    "next_steps",
)


@dataclass(frozen=True)
class ReportRow:
    report: ProgressReport
    subject_name: str | None
    """``None`` for a whole-student report (no subject)."""


@dataclass(frozen=True)
class ReportPage:
    rows: list[ReportRow]
    next_cursor: str | None


def _joined(subjects: Select[*tuple[Any, ...]]) -> Select[Any]:
    subject = subjects.subquery()
    return select(ProgressReport, subject.c.name).outerjoin(
        subject, subject.c.id == ProgressReport.subject_id
    )


def _row(report: ProgressReport, name: Any) -> ReportRow:
    return ReportRow(
        report,
        None if report.subject_id is None else ("" if name is None else str(name)),
    )


# --- Reads ----------------------------------------------------------------------------


async def report_with_subject(
    session: AsyncSession, report_id: uuid.UUID, *, subjects: Select[*tuple[Any, ...]]
) -> ReportRow | None:
    """The live report with its subject's name, or ``None`` (unknown or
    deleted). One statement, no lock."""
    found = (
        await session.execute(
            _joined(subjects)
            .where(ProgressReport.id == report_id, ProgressReport.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
    ).first()
    return None if found is None else _row(*found)


async def reports_by_id(
    session: AsyncSession,
    report_ids: Collection[uuid.UUID],
    *,
    subjects: Select[*tuple[Any, ...]],
) -> dict[uuid.UUID, ReportRow]:
    """These reports (deleted ones too: the inbox summary of a cancelled
    request), with their subjects' names, re-read. One statement (none for
    no ids)."""
    if not report_ids:
        return {}
    rows = await session.execute(
        _joined(subjects)
        .where(ProgressReport.id.in_(set(report_ids)))
        .execution_options(populate_existing=True)
    )
    return {report.id: _row(report, name) for report, name in rows}


async def get_report_for_update(
    session: AsyncSession, report_id: uuid.UUID
) -> ProgressReport | None:
    """The live report ``FOR UPDATE``, re-read, or ``None`` when it is gone
    or was deleted meanwhile."""
    return cast(
        ProgressReport | None,
        await session.scalar(
            select(ProgressReport)
            .where(ProgressReport.id == report_id, ProgressReport.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


def _position(cursor: str) -> tuple[datetime, uuid.UUID]:
    instant, identifier = decode_cursor(cursor, types=(str, str))
    try:
        return datetime.fromisoformat(str(instant)), uuid.UUID(str(identifier))
    except ValueError:
        raise InvalidCursorError() from None


async def progress_report_working_page(
    session: AsyncSession,
    *,
    subjects: Select[*tuple[Any, ...]],
    viewer_user_id: uuid.UUID,
    reviewer: bool,
    mine: bool,
    status: str | None,
    student_id: uuid.UUID | None,
    kind: str | None,
    cursor: str | None,
    limit: int,
) -> ReportPage:
    """E2: live reports. ``mine`` (or not ``reviewer``): the viewer's own
    reports in any status; a reviewer otherwise every non-draft report plus
    their own drafts. ``status=submitted`` lists the queue oldest submission
    first; anything else newest created first. One statement."""
    query = _joined(subjects).where(ProgressReport.deleted_at.is_(None))
    if mine or not reviewer:
        query = query.where(ProgressReport.author_user_id == viewer_user_id)
    else:
        query = query.where(
            or_(
                ProgressReport.status != DRAFT,
                ProgressReport.author_user_id == viewer_user_id,
            )
        )
    if status is not None:
        query = query.where(ProgressReport.status == status)
    if student_id is not None:
        query = query.where(ProgressReport.student_id == student_id)
    if kind is not None:
        query = query.where(ProgressReport.kind == kind)
    queue = status == SUBMITTED
    key = ProgressReport.submitted_at if queue else ProgressReport.created_at
    if cursor is not None:
        instant, identifier = _position(cursor)
        if queue:
            query = query.where(
                tuple_(key, ProgressReport.id) > tuple_(instant, identifier)
            )
        else:
            query = query.where(
                tuple_(key, ProgressReport.id) < tuple_(instant, identifier)
            )
    order = (
        (key.asc(), ProgressReport.id.asc())
        if queue
        else (key.desc(), ProgressReport.id.desc())
    )
    rows = [
        _row(*found)
        for found in await session.execute(query.order_by(*order).limit(limit + 1))
    ]
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit and items:
        last = items[-1].report
        value = last.submitted_at if queue else last.created_at
        assert value is not None  # noqa: S101 (submitted rows have submitted_at)
        next_cursor = encode_cursor([value.isoformat(), str(last.id)])
    return ReportPage(rows=items, next_cursor=next_cursor)


def _approved(
    subjects: Select[*tuple[Any, ...]],
    student_id: uuid.UUID,
    author_user_id: uuid.UUID | None,
) -> Select[Any]:
    query = _joined(subjects).where(
        ProgressReport.student_id == student_id,
        ProgressReport.status == APPROVED,
        ProgressReport.deleted_at.is_(None),
    )
    if author_user_id is not None:
        query = query.where(ProgressReport.author_user_id == author_user_id)
    return query


def _approved_position(cursor: str) -> tuple[date, datetime, uuid.UUID]:
    period_end, approved_at, identifier = decode_cursor(cursor, types=(str, str, str))
    try:
        return (
            date.fromisoformat(str(period_end)),
            datetime.fromisoformat(str(approved_at)),
            uuid.UUID(str(identifier)),
        )
    except ValueError:
        raise InvalidCursorError() from None


async def approved_page(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    subjects: Select[*tuple[Any, ...]],
    author_user_id: uuid.UUID | None,
    kind: str | None,
    subject_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> ReportPage:
    """F3: the student's **approved** reports (only one author's when
    given), ``period_end`` descending, then ``approved_at`` descending,
    then ``id``. ``status = 'approved_for_family'`` and ``deleted_at IS
    NULL`` are always applied. One statement."""
    query = _approved(subjects, student_id, author_user_id)
    if kind is not None:
        query = query.where(ProgressReport.kind == kind)
    if subject_id is not None:
        query = query.where(ProgressReport.subject_id == subject_id)
    if cursor is not None:
        period_end, approved_at, identifier = _approved_position(cursor)
        query = query.where(
            tuple_(
                ProgressReport.period_end,
                ProgressReport.approved_at,
                ProgressReport.id,
            )
            < tuple_(period_end, approved_at, identifier)
        )
    rows = [
        _row(*found)
        for found in await session.execute(
            query.order_by(
                ProgressReport.period_end.desc(),
                ProgressReport.approved_at.desc(),
                ProgressReport.id.desc(),
            ).limit(limit + 1)
        )
    ]
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit and items:
        last = items[-1].report
        assert last.approved_at is not None  # noqa: S101 (approved rows)
        next_cursor = encode_cursor(
            [last.period_end.isoformat(), last.approved_at.isoformat(), str(last.id)]
        )
    return ReportPage(rows=items, next_cursor=next_cursor)


async def approved_report(
    session: AsyncSession,
    student_id: uuid.UUID,
    report_id: uuid.UUID,
    *,
    subjects: Select[*tuple[Any, ...]],
    author_user_id: uuid.UUID | None,
) -> ReportRow | None:
    """F1: the student's approved report (only one author's when given), or
    ``None`` (another student's, not approved, retracted, deleted or
    unknown). One statement."""
    found = (
        await session.execute(
            _approved(subjects, student_id, author_user_id).where(
                ProgressReport.id == report_id
            )
        )
    ).first()
    return None if found is None else _row(*found)


async def has_own_approved_report(
    session: AsyncSession, student_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Whether the user authored an approved report of the student (a former
    author's access to R10, F1). One statement."""
    found = await session.scalar(
        select(ProgressReport.id)
        .where(
            ProgressReport.student_id == student_id,
            ProgressReport.author_user_id == user_id,
            ProgressReport.status == APPROVED,
            ProgressReport.deleted_at.is_(None),
        )
        .limit(1)
    )
    return found is not None


# --- Writes ---------------------------------------------------------------------------


async def _existing_period_report(
    session: AsyncSession, report: ProgressReport
) -> uuid.UUID | None:
    query = select(ProgressReport.id).where(
        ProgressReport.student_id == report.student_id,
        ProgressReport.author_user_id == report.author_user_id,
        ProgressReport.kind == report.kind,
        ProgressReport.period_start == report.period_start,
        ProgressReport.deleted_at.is_(None),
        ProgressReport.status != RETRACTED,
    )
    query = query.where(
        ProgressReport.subject_id.is_(None)
        if report.subject_id is None
        else ProgressReport.subject_id == report.subject_id
    )
    found: uuid.UUID | None = await session.scalar(query.limit(1))
    return found


async def create_report(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    author_user_id: uuid.UUID,
    subject_id: uuid.UUID | None,
    kind: str,
    period_start: date,
    period_end: date,
    narrative: Mapping[str, str | None],
) -> ProgressReport:
    """A new ``draft``, flushed in a savepoint. A live, non-retracted weekly
    or monthly report of the same author, student, period and subject
    (:data:`~app.modules.academics.models.PROGRESS_REPORT_PERIOD_UNIQUE`)
    raises :class:`ProgressReportExistsError` naming it."""
    report = ProgressReport(
        id=uuid.uuid7(),
        student_id=student_id,
        author_user_id=author_user_id,
        subject_id=subject_id,
        kind=kind,
        period_start=period_start,
        period_end=period_end,
        status=DRAFT,
        **{field: narrative.get(field) for field in NARRATIVE},
    )
    try:
        async with session.begin_nested():
            session.add(report)
    except IntegrityError as exc:
        if kind != ON_DEMAND and PROGRESS_REPORT_PERIOD_UNIQUE in str(exc.orig):
            existing = await _existing_period_report(session, report)
            raise ProgressReportExistsError(
                None if existing is None else str(existing)
            ) from None
        raise
    return report


async def change_narrative(
    session: AsyncSession, report: ProgressReport, values: Mapping[str, str | None]
) -> bool:
    """C3 on the locked report: the narrative fields given (``None`` clears
    one). ``False`` (no write) when nothing changes."""
    changed = False
    for field, value in values.items():
        if field in NARRATIVE and getattr(report, field) != value:
            setattr(report, field, value)
            changed = True
    if changed:
        await session.flush()
    return changed


async def submit_report(
    session: AsyncSession,
    report: ProgressReport,
    *,
    facts: dict[str, Any],
    now: datetime,
) -> None:
    """D2 on the locked report: facts frozen, ``submitted``. The last
    request for changes stays (the author reads it) until approval."""
    report.facts = facts
    report.facts_computed_at = now
    report.submitted_at = now
    report.status = SUBMITTED
    await session.flush()


async def approve_report(
    session: AsyncSession,
    report: ProgressReport,
    *,
    by_user_id: uuid.UUID,
    now: datetime,
) -> None:
    """D3 on the locked report: ``approved_for_family``; the last request
    for changes is cleared (the audit keeps it)."""
    report.status = APPROVED
    report.approved_at = now
    report.approved_by_user_id = by_user_id
    report.changes_requested_at = None
    report.changes_requested_by_user_id = None
    report.changes_requested_reason = None
    await session.flush()


async def request_report_changes(
    session: AsyncSession,
    report: ProgressReport,
    *,
    by_user_id: uuid.UUID,
    reason: str,
    now: datetime,
) -> None:
    """D4 on the locked report: back to the author with the reason."""
    report.status = CHANGES_REQUESTED
    report.changes_requested_at = now
    report.changes_requested_by_user_id = by_user_id
    report.changes_requested_reason = reason
    await session.flush()


async def retract_report(
    session: AsyncSession,
    report: ProgressReport,
    *,
    by_user_id: uuid.UUID,
    reason: str,
    now: datetime,
) -> None:
    """D5 on the locked report: ``retracted`` (final); only the status, the
    ``retracted_*`` columns, the version and ``updated_at`` change (what
    the immutability trigger allows on an approved report)."""
    report.status = RETRACTED
    report.retracted_at = now
    report.retracted_by_user_id = by_user_id
    report.retract_reason = reason
    await session.flush()


async def soft_delete_report(
    session: AsyncSession,
    report: ProgressReport,
    *,
    by_user_id: uuid.UUID,
    now: datetime,
) -> None:
    """C4 on the locked report (``draft`` or ``changes_requested``)."""
    report.deleted_at = now
    report.deleted_by_user_id = by_user_id
    await session.flush()
