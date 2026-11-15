"""Recently deleted academic records: recovery and purge (#129 B, C3).

Exactly the five models with :class:`~app.core.models.SoftDeleteMixin` are
eligible (:data:`KINDS`, pinned by ``tests/unit/test_soft_delete_eligibility
.py``): internal session notes, evaluation drafts, learning plan drafts,
assignments with nothing submitted and progress reports in ``draft`` or
``changes_requested``. Their ``DELETE`` routes set ``deleted_at`` and
``deleted_by_user_id``; every existing read keeps ``deleted_at IS NULL``.

- **Recovery** (B1-B3): :func:`deleted_rows` lists one user's deleted rows
  still inside the window (one statement per kind, joined with the names
  and session columns the caller passes, ADR-0002);
  :func:`deleted_for_update` is the only loader that returns a deleted row
  (``FOR UPDATE``), used by the restore and nothing else; ``restore_<kind>``
  clears both columns (the version goes up and the flush writes the
  automatic ``restore`` audit event).
- **Purge** (C3): :func:`purge_batch` hard-deletes at most
  :data:`PURGE_BATCH` rows of one kind deleted at or before the cutoff,
  ``FOR UPDATE SKIP LOCKED`` (a restore that committed first wins), through
  ORM deletes, children first, so the flush writes the ``delete`` audit
  events. A deleted #316 placeholder waits while its period is still the
  latest ended one (``placeholder_before``), or the placeholder job would
  create it again.

``app.workflows.deleted_records`` composes these with the kinds' record
policies, the center's time zone and the retention registry.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.errors import DeletedRecordRestoreConflictError
from app.modules.academics.models import (
    PROGRESS_REPORT_PERIOD_UNIQUE,
    Assignment,
    AssignmentSubmission,
    Evaluation,
    EvaluationScore,
    LearningPlan,
    LearningPlanMilestone,
    LearningPlanObjective,
    ProgressReport,
    SessionNote,
)
from app.modules.academics.progress_report_rules import (
    MONTHLY,
    ON_DEMAND,
    RETRACTED,
    WEEKLY,
)

DELETED_RECORD_RECOVERY_DAYS: Final = 30
"""How long the deleter may restore a record (#129 E4); the retention job
purges it after that."""

RECOVERY_WINDOW: Final = timedelta(days=DELETED_RECORD_RECOVERY_DAYS)

LIST_MAX: Final = 100
"""The most recent restorable records listed (B1)."""

PURGE_BATCH: Final = 100
"""Rows of one kind purged per transaction (C3)."""


class DeletedRecordKind(StrEnum):
    """The eligible kinds (#129 E2): the models with ``SoftDeleteMixin``."""

    SESSION_NOTE = "session_note"
    EVALUATION = "evaluation"
    LEARNING_PLAN = "learning_plan"
    ASSIGNMENT = "assignment"
    PROGRESS_REPORT = "progress_report"


type DeletedModel = (
    SessionNote | Evaluation | LearningPlan | Assignment | ProgressReport
)

MODELS: Final[Mapping[DeletedRecordKind, type[Any]]] = {
    DeletedRecordKind.SESSION_NOTE: SessionNote,
    DeletedRecordKind.EVALUATION: Evaluation,
    DeletedRecordKind.LEARNING_PLAN: LearningPlan,
    DeletedRecordKind.ASSIGNMENT: Assignment,
    DeletedRecordKind.PROGRESS_REPORT: ProgressReport,
}
"""Each kind's model (``tests/unit/test_soft_delete_eligibility.py`` pins
that these are every ``SoftDeleteMixin`` model)."""

PURGED_TABLES: Final = frozenset(
    {
        "session_notes",
        "evaluations",
        "evaluation_scores",
        "learning_plans",
        "learning_plan_objectives",
        "learning_plan_milestones",
        "assignments",
        "assignment_submissions",
        "progress_reports",
    }
)
"""Every table the purge deletes from (the retention rule's ``tables``)."""


def restorable_until(deleted_at: datetime) -> datetime:
    """The end of the recovery window (exclusive)."""
    return deleted_at + RECOVERY_WINDOW


# --- B1: the list ---------------------------------------------------------------------


@dataclass(frozen=True)
class DeletedRow:
    """One deleted record with what the list and its record policy need.

    ``students`` are ``(id, name)`` pairs: the record's student, or every
    recipient of an assignment ordered by name and id. ``session_starts_at``
    is a note's session start (``None`` for the other kinds)."""

    kind: DeletedRecordKind
    record: Any
    students: tuple[tuple[uuid.UUID, str], ...]
    session_starts_at: datetime | None = None


def _window(model: Any, user_id: uuid.UUID, since: datetime) -> list[Any]:
    return [model.deleted_by_user_id == user_id, model.deleted_at > since]


async def deleted_rows(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    since: datetime,
    students: Select[*tuple[Any, ...]],
    sessions: Select[*tuple[Any, ...]],
) -> list[DeletedRow]:
    """``user_id``'s deleted rows of every kind deleted after ``since``.

    ``students`` is ``families.service.student_name_columns()`` and
    ``sessions`` ``scheduling.service.note_session_columns()`` (neither
    executed alone). Five statements, one per kind; no lock."""
    names = students.subquery()
    found: list[DeletedRow] = []
    for kind, model in MODELS.items():
        if kind is DeletedRecordKind.ASSIGNMENT:
            continue
        columns: list[Any] = [model, names.c.name]
        query = select(*columns).outerjoin(names, names.c.id == model.student_id)
        if kind is DeletedRecordKind.SESSION_NOTE:
            starts = sessions.subquery()
            query = query.add_columns(starts.c.starts_at).outerjoin(
                starts, starts.c.id == SessionNote.class_session_id
            )
        rows = await session.execute(
            query.where(*_window(model, user_id, since)).execution_options(
                populate_existing=True
            )
        )
        for row in rows:
            record, name = row[0], row[1]
            found.append(
                DeletedRow(
                    kind,
                    record,
                    ((record.student_id, "" if name is None else str(name)),),
                    row[2] if kind is DeletedRecordKind.SESSION_NOTE else None,
                )
            )
    rows = await session.execute(
        select(Assignment, AssignmentSubmission.student_id, names.c.name)
        .join(
            AssignmentSubmission,
            AssignmentSubmission.assignment_id == Assignment.id,
        )
        .outerjoin(names, names.c.id == AssignmentSubmission.student_id)
        .where(*_window(Assignment, user_id, since))
        .order_by(Assignment.id, names.c.name, AssignmentSubmission.student_id)
        .execution_options(populate_existing=True)
    )
    recipients: dict[uuid.UUID, tuple[Assignment, list[tuple[uuid.UUID, str]]]] = {}
    for assignment, student_id, name in rows:
        entry = recipients.setdefault(assignment.id, (assignment, []))
        entry[1].append((student_id, "" if name is None else str(name)))
    for assignment, people in recipients.values():
        found.append(
            DeletedRow(DeletedRecordKind.ASSIGNMENT, assignment, tuple(people))
        )
    return found


# --- B2, B3: the restore --------------------------------------------------------------


async def deleted_for_update(
    session: AsyncSession, kind: DeletedRecordKind, record_id: uuid.UUID
) -> Any | None:
    """The **deleted** row of ``kind``, ``FOR UPDATE``, or ``None`` (unknown
    or live). The only loader that returns a deleted row; the restore and
    nothing else uses it (B3)."""
    model = MODELS[kind]
    found: Any | None = await session.scalar(
        select(model)
        .where(model.id == record_id, model.deleted_at.is_not(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return found


async def recipient_ids(
    session: AsyncSession, assignment_id: uuid.UUID
) -> list[uuid.UUID]:
    """An assignment's recipients (its submission rows), live or deleted.
    One statement."""
    found = await session.scalars(
        select(AssignmentSubmission.student_id)
        .where(AssignmentSubmission.assignment_id == assignment_id)
        .order_by(AssignmentSubmission.student_id)
    )
    return list(found)


async def _undelete(session: AsyncSession, record: Any) -> None:
    record.deleted_at = None
    record.deleted_by_user_id = None
    await session.flush()


async def restore_note(session: AsyncSession, note: SessionNote) -> None:
    """Back to the live, ``internal`` note it was (B2)."""
    await _undelete(session, note)


async def restore_evaluation(session: AsyncSession, evaluation: Evaluation) -> None:
    """Back to the live draft it was (B2)."""
    await _undelete(session, evaluation)


async def restore_plan(session: AsyncSession, plan: LearningPlan) -> None:
    """Back to the live draft it was (B2); its objectives and milestones
    were never touched."""
    await _undelete(session, plan)


async def restore_assignment(session: AsyncSession, assignment: Assignment) -> None:
    """Back to the live assignment it was (B2): its recipients see it again
    (no new ``assignment_set``, E3)."""
    await _undelete(session, assignment)


async def _live_period_report(
    session: AsyncSession, report: ProgressReport
) -> uuid.UUID | None:
    """Another live, non-retracted weekly or monthly report of the same
    student, author, kind, period start and subject
    (:data:`~app.modules.academics.models.PROGRESS_REPORT_PERIOD_UNIQUE`)."""
    if report.kind == ON_DEMAND:
        return None
    found: uuid.UUID | None = await session.scalar(
        select(ProgressReport.id)
        .where(
            ProgressReport.id != report.id,
            ProgressReport.student_id == report.student_id,
            ProgressReport.author_user_id == report.author_user_id,
            ProgressReport.kind == report.kind,
            ProgressReport.period_start == report.period_start,
            ProgressReport.deleted_at.is_(None),
            ProgressReport.status != RETRACTED,
            ProgressReport.subject_id.is_(None)
            if report.subject_id is None
            else ProgressReport.subject_id == report.subject_id,
        )
        .limit(1)
    )
    return found


async def restore_report(session: AsyncSession, report: ProgressReport) -> None:
    """Back to the live ``draft`` or ``changes_requested`` report it was
    (B2). ``409 deleted_record_restore_conflict`` when another live report
    holds its period (checked first; a ``UniqueViolation`` on the index maps
    to the same code)."""
    if await _live_period_report(session, report) is not None:
        raise DeletedRecordRestoreConflictError()
    try:
        async with session.begin_nested():
            report.deleted_at = None
            report.deleted_by_user_id = None
            await session.flush()
    except IntegrityError as exc:
        if PROGRESS_REPORT_PERIOD_UNIQUE in str(exc.orig):
            raise DeletedRecordRestoreConflictError() from None
        raise


# --- C3: the purge --------------------------------------------------------------------


def purgeable_report(before: Mapping[str, date]) -> ColumnElement[bool]:
    """The SQL condition of a deleted report the purge may take now (C3,
    #316): no schedule, or a period before its cadence's latest ended
    period (``before[WEEKLY]`` / ``before[MONTHLY]``)."""
    return or_(
        ProgressReport.schedule_id.is_(None),
        (ProgressReport.kind == WEEKLY)
        & (ProgressReport.period_start < before[WEEKLY]),
        (ProgressReport.kind == MONTHLY)
        & (ProgressReport.period_start < before[MONTHLY]),
    )


async def _children_first(
    session: AsyncSession, kind: DeletedRecordKind, ids: list[uuid.UUID]
) -> None:
    """Delete the children of ``ids`` through the ORM (audited), deepest
    first, each step flushed."""
    steps: list[Select[Any]] = []
    if kind is DeletedRecordKind.EVALUATION:
        steps = [select(EvaluationScore).where(EvaluationScore.evaluation_id.in_(ids))]
    elif kind is DeletedRecordKind.LEARNING_PLAN:
        steps = [
            select(LearningPlanMilestone).where(LearningPlanMilestone.plan_id.in_(ids)),
            select(LearningPlanObjective).where(LearningPlanObjective.plan_id.in_(ids)),
        ]
    elif kind is DeletedRecordKind.ASSIGNMENT:
        steps = [
            select(AssignmentSubmission).where(
                AssignmentSubmission.assignment_id.in_(ids)
            )
        ]
    for step in steps:
        for child in await session.scalars(step):
            await session.delete(child)
        await session.flush()


async def purge_batch(
    session: AsyncSession,
    kind: DeletedRecordKind,
    *,
    cutoff: datetime,
    placeholder_before: Mapping[str, date],
) -> int:
    """C3: hard-delete at most :data:`PURGE_BATCH` rows of ``kind`` deleted
    at or before ``cutoff``, children first, through ORM deletes (the flush
    writes each ``delete`` audit event with the Unit of Work's actor).

    The rows are taken ``FOR UPDATE SKIP LOCKED`` with ``deleted_at`` checked
    in the locking statement, so a restore that committed first wins and one
    in flight is skipped. ``placeholder_before`` holds the start of the
    latest ended weekly and monthly periods: a deleted #316 placeholder
    (``schedule_id`` set) of that period or later waits. Returns the number
    of records (not children) deleted."""
    model = MODELS[kind]
    query = (
        select(model)
        .where(model.deleted_at.is_not(None), model.deleted_at <= cutoff)
        .order_by(model.deleted_at, model.id)
        .limit(PURGE_BATCH)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if kind is DeletedRecordKind.PROGRESS_REPORT:
        query = query.where(purgeable_report(placeholder_before))
    records = list(await session.scalars(query))
    if not records:
        return 0
    await _children_first(session, kind, [record.id for record in records])
    for record in records:
        await session.delete(record)
    await session.flush()
    return len(records)


__all__ = [
    "DELETED_RECORD_RECOVERY_DAYS",
    "LIST_MAX",
    "MODELS",
    "PURGED_TABLES",
    "PURGE_BATCH",
    "RECOVERY_WINDOW",
    "DeletedModel",
    "DeletedRecordKind",
    "DeletedRow",
    "deleted_for_update",
    "deleted_rows",
    "purge_batch",
    "purgeable_report",
    "recipient_ids",
    "restorable_until",
    "restore_assignment",
    "restore_evaluation",
    "restore_note",
    "restore_plan",
    "restore_report",
]
