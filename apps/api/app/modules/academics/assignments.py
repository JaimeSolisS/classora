"""Homework assignment rows (#82 B1, C, D, E, F, G).

Every function runs in the caller's Unit of Work and never commits. They
take plain ids and values: who may call them is decided by
``app.workflows.assignments`` with the pure rules in
:mod:`~app.modules.academics.assignment_rules`.

**Lock order: the assignment, then its submission rows** (by
``student_id``) everywhere (:func:`get_assignment_for_update`,
:func:`get_submission_for_update`, :func:`submissions_for_update`). Locked
rows are re-read (``populate_existing``). The automatic audit events record
the diffs (spec §9). Deleted assignments are never returned.

The reads join caller-built, **unexecuted** selects as subqueries (the
subjects' ``(id, name)``; the class sessions' ``(id, starts_at, …)``), so an
assignment, its subject's name and its session's start come in one
statement while academics imports neither catalog nor scheduling (#77 E2).
"""

import uuid
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import ColumnElement, Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.modules.academics.models import Assignment, AssignmentSubmission

type Due = Literal["upcoming", "past"]


@dataclass(frozen=True)
class AssignmentRow:
    """A live assignment with its subject's name and its session's start
    (``None`` without a session link)."""

    assignment: Assignment
    subject_name: str
    session_starts_at: datetime | None


@dataclass(frozen=True)
class StudentFact:
    """What a per-student list needs to compute states, the summary and the
    order (E3) without loading any text."""

    assignment_id: uuid.UUID
    subject_id: uuid.UUID
    due_on: date
    due_time: time | None
    submitted_at: datetime | None
    graded_at: datetime | None


@dataclass(frozen=True)
class StudentRow:
    """One assignment with one student's submission row."""

    row: AssignmentRow
    submission: AssignmentSubmission


@dataclass(frozen=True)
class SubmissionFact:
    """What the working list's counts need of one submission row (F1)."""

    assignment_id: uuid.UUID
    student_id: uuid.UUID
    submitted_at: datetime | None
    graded_at: datetime | None


@dataclass(frozen=True)
class AssignmentWorkingPage:
    rows: list[AssignmentRow]
    next_cursor: str | None


@dataclass(frozen=True)
class GradeRow:
    """A graded, scored submission of a live assignment (G1)."""

    submission: AssignmentSubmission
    assignment: Assignment
    subject_name: str


# --- Reads ----------------------------------------------------------------------------


def _joined(
    subjects: Select[*tuple[Any, ...]], sessions: Select[*tuple[Any, ...]]
) -> tuple[Any, Any, Select[Any]]:
    subject = subjects.subquery()
    sessions_q = sessions.subquery()
    query = (
        select(Assignment, subject.c.name, sessions_q.c.starts_at)
        .outerjoin(subject, subject.c.id == Assignment.subject_id)
        .outerjoin(sessions_q, sessions_q.c.id == Assignment.class_session_id)
    )
    return subject, sessions_q, query


def _row(assignment: Assignment, name: Any, starts_at: Any) -> AssignmentRow:
    return AssignmentRow(
        assignment, "" if name is None else str(name), cast(datetime | None, starts_at)
    )


async def assignment_with_subject(
    session: AsyncSession,
    assignment_id: uuid.UUID,
    *,
    subjects: Select[*tuple[Any, ...]],
    sessions: Select[*tuple[Any, ...]],
) -> AssignmentRow | None:
    """The live assignment with its subject's name and session start, or
    ``None`` (unknown or deleted). One statement, no lock."""
    _, _, query = _joined(subjects, sessions)
    found = (
        await session.execute(
            query.where(
                Assignment.id == assignment_id, Assignment.deleted_at.is_(None)
            ).execution_options(populate_existing=True)
        )
    ).first()
    if found is None:
        return None
    return _row(*found)


async def submissions_of(
    session: AsyncSession, assignment_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[AssignmentSubmission]]:
    """The submission rows of these assignments, by ``student_id``. One
    statement (none for no ids)."""
    found: dict[uuid.UUID, list[AssignmentSubmission]] = {i: [] for i in assignment_ids}
    if not assignment_ids:
        return found
    rows = await session.scalars(
        select(AssignmentSubmission)
        .where(AssignmentSubmission.assignment_id.in_(set(assignment_ids)))
        .order_by(AssignmentSubmission.assignment_id, AssignmentSubmission.student_id)
        .execution_options(populate_existing=True)
    )
    for row in rows:
        found[row.assignment_id].append(row)
    return found


async def submission_facts(
    session: AsyncSession, assignment_ids: Collection[uuid.UUID]
) -> list[SubmissionFact]:
    """The ``(assignment, student, submitted_at, graded_at)`` of every recipient of
    these assignments (the working list's counts). One statement (none for
    no ids)."""
    if not assignment_ids:
        return []
    rows = await session.execute(
        select(
            AssignmentSubmission.assignment_id,
            AssignmentSubmission.student_id,
            AssignmentSubmission.submitted_at,
            AssignmentSubmission.graded_at,
        ).where(AssignmentSubmission.assignment_id.in_(set(assignment_ids)))
    )
    return [SubmissionFact(*row) for row in rows]


async def submission_for(
    session: AsyncSession, assignment_id: uuid.UUID, student_id: uuid.UUID
) -> AssignmentSubmission | None:
    """One recipient's row, or ``None`` (not a recipient). One statement."""
    return cast(
        AssignmentSubmission | None,
        await session.scalar(
            select(AssignmentSubmission)
            .where(
                AssignmentSubmission.assignment_id == assignment_id,
                AssignmentSubmission.student_id == student_id,
            )
            .execution_options(populate_existing=True)
        ),
    )


async def get_assignment_for_update(
    session: AsyncSession, assignment_id: uuid.UUID
) -> Assignment | None:
    """The live assignment ``FOR UPDATE``, re-read, or ``None`` when it is
    gone or was deleted meanwhile."""
    return cast(
        Assignment | None,
        await session.scalar(
            select(Assignment)
            .where(Assignment.id == assignment_id, Assignment.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


async def get_submission_for_update(
    session: AsyncSession, assignment_id: uuid.UUID, student_id: uuid.UUID
) -> AssignmentSubmission | None:
    """One recipient's row ``FOR UPDATE`` (after the assignment), re-read, or
    ``None`` when the recipient was removed meanwhile."""
    return cast(
        AssignmentSubmission | None,
        await session.scalar(
            select(AssignmentSubmission)
            .where(
                AssignmentSubmission.assignment_id == assignment_id,
                AssignmentSubmission.student_id == student_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


async def submissions_for_update(
    session: AsyncSession, assignment_id: uuid.UUID
) -> list[AssignmentSubmission]:
    """Every recipient's row ``FOR UPDATE`` by ``student_id`` (after the
    assignment), re-read."""
    rows = await session.scalars(
        select(AssignmentSubmission)
        .where(AssignmentSubmission.assignment_id == assignment_id)
        .order_by(AssignmentSubmission.student_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def student_facts(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    creator_user_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    from_: date | None,
    to: date | None,
) -> list[StudentFact]:
    """E3: the student's live assignments (only one creator's when given),
    with ``subject_id``, ``from``/``to`` on ``due_on`` applied, as facts
    (no text). One statement. ``deleted_at IS NULL`` is always applied, and
    only the student's own submission row is read."""
    query = (
        select(
            Assignment.id,
            Assignment.subject_id,
            Assignment.due_on,
            Assignment.due_time,
            AssignmentSubmission.submitted_at,
            AssignmentSubmission.graded_at,
        )
        .join(AssignmentSubmission, AssignmentSubmission.assignment_id == Assignment.id)
        .where(
            AssignmentSubmission.student_id == student_id,
            Assignment.deleted_at.is_(None),
        )
    )
    if creator_user_id is not None:
        query = query.where(Assignment.creator_user_id == creator_user_id)
    if subject_id is not None:
        query = query.where(Assignment.subject_id == subject_id)
    if from_ is not None:
        query = query.where(Assignment.due_on >= from_)
    if to is not None:
        query = query.where(Assignment.due_on <= to)
    rows = await session.execute(query)
    return [StudentFact(*row) for row in rows]


async def student_rows(
    session: AsyncSession,
    student_id: uuid.UUID,
    assignment_ids: Collection[uuid.UUID],
    *,
    subjects: Select[*tuple[Any, ...]],
    sessions: Select[*tuple[Any, ...]],
) -> dict[uuid.UUID, StudentRow]:
    """These live assignments, each with the student's own submission row
    (never another recipient's), by assignment id. One statement (none for
    no ids)."""
    if not assignment_ids:
        return {}
    _, _, query = _joined(subjects, sessions)
    rows = await session.execute(
        query.add_columns(AssignmentSubmission)
        .join(AssignmentSubmission, AssignmentSubmission.assignment_id == Assignment.id)
        .where(
            Assignment.id.in_(set(assignment_ids)),
            Assignment.deleted_at.is_(None),
            AssignmentSubmission.student_id == student_id,
        )
        .execution_options(populate_existing=True)
    )
    return {
        assignment.id: StudentRow(_row(assignment, name, starts_at), submission)
        for assignment, name, starts_at, submission in rows
    }


async def has_own_assignment(
    session: AsyncSession, student_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Whether the user created a live assignment the student is a
    recipient of (a former creator's access, E1). One statement."""
    found = await session.scalar(
        select(Assignment.id)
        .join(AssignmentSubmission, AssignmentSubmission.assignment_id == Assignment.id)
        .where(
            AssignmentSubmission.student_id == student_id,
            Assignment.creator_user_id == user_id,
            Assignment.deleted_at.is_(None),
        )
        .limit(1)
    )
    return found is not None


def _working_position(cursor: str) -> tuple[date, uuid.UUID]:
    due_on, assignment_id = decode_cursor(cursor, types=(str, str))
    try:
        return date.fromisoformat(str(due_on)), uuid.UUID(str(assignment_id))
    except ValueError:
        raise InvalidCursorError() from None


async def assignment_working_page(
    session: AsyncSession,
    *,
    subjects: Select[*tuple[Any, ...]],
    sessions: Select[*tuple[Any, ...]],
    creator_user_id: uuid.UUID | None,
    student_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    class_session_id: uuid.UUID | None,
    due: Due | None,
    today: date,
    cursor: str | None,
    limit: int,
) -> AssignmentWorkingPage:
    """F1: live assignments (only one creator's when ``creator_user_id`` is
    given; every one otherwise), newest ``due_on`` first, then ``id``
    descending; ``due=upcoming`` is ``due_on`` on or after ``today``,
    ``past`` before it. One statement."""
    _, _, query = _joined(subjects, sessions)
    query = query.where(Assignment.deleted_at.is_(None))
    if creator_user_id is not None:
        query = query.where(Assignment.creator_user_id == creator_user_id)
    if student_id is not None:
        query = query.where(
            select(AssignmentSubmission.id)
            .where(
                AssignmentSubmission.assignment_id == Assignment.id,
                AssignmentSubmission.student_id == student_id,
            )
            .exists()
        )
    if subject_id is not None:
        query = query.where(Assignment.subject_id == subject_id)
    if class_session_id is not None:
        query = query.where(Assignment.class_session_id == class_session_id)
    if due == "upcoming":
        query = query.where(Assignment.due_on >= today)
    elif due == "past":
        query = query.where(Assignment.due_on < today)
    if cursor is not None:
        due_on, identifier = _working_position(cursor)
        query = query.where(
            tuple_(Assignment.due_on, Assignment.id) < tuple_(due_on, identifier)
        )
    result = await session.execute(
        query.order_by(Assignment.due_on.desc(), Assignment.id.desc()).limit(limit + 1)
    )
    rows = [_row(*found) for found in result]
    items = rows[:limit]
    next_cursor = (
        encode_cursor(
            [items[-1].assignment.due_on.isoformat(), str(items[-1].assignment.id)]
        )
        if len(rows) > limit and items
        else None
    )
    return AssignmentWorkingPage(rows=items, next_cursor=next_cursor)


def graded_condition() -> ColumnElement[bool]:
    """G1: a scored grade (feedback-only grades stay out of the history)."""
    return AssignmentSubmission.score.is_not(None)


async def history_grades(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    subjects: Select[*tuple[Any, ...]],
    creator_user_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    from_: date | None,
    to: date | None,
    limit: int,
) -> list[GradeRow]:
    """G1: the student's scored grades of live assignments (only one
    creator's when given), newest first by ``graded_on``, then
    ``graded_at``, then id, at most ``limit``, each with the subject's
    name. One statement. ``score IS NOT NULL`` and ``deleted_at IS NULL``
    are always applied here."""
    subject = subjects.subquery()
    query = (
        select(AssignmentSubmission, Assignment, subject.c.name)
        .join(Assignment, Assignment.id == AssignmentSubmission.assignment_id)
        .outerjoin(subject, subject.c.id == Assignment.subject_id)
        .where(
            AssignmentSubmission.student_id == student_id,
            graded_condition(),
            Assignment.deleted_at.is_(None),
        )
    )
    if creator_user_id is not None:
        query = query.where(Assignment.creator_user_id == creator_user_id)
    if subject_id is not None:
        query = query.where(Assignment.subject_id == subject_id)
    if from_ is not None:
        query = query.where(AssignmentSubmission.graded_on >= from_)
    if to is not None:
        query = query.where(AssignmentSubmission.graded_on <= to)
    rows = await session.execute(
        query.order_by(
            AssignmentSubmission.graded_on.desc(),
            AssignmentSubmission.graded_at.desc(),
            AssignmentSubmission.id.desc(),
        ).limit(limit)
    )
    return [
        GradeRow(submission, assignment, "" if name is None else str(name))
        for submission, assignment, name in rows
    ]


# --- Writes ---------------------------------------------------------------------------


async def create_assignment(
    session: AsyncSession,
    *,
    creator_user_id: uuid.UUID,
    subject_id: uuid.UUID,
    class_session_id: uuid.UUID | None,
    title: str,
    instructions: str,
    due_on: date,
    due_time: time | None,
    max_score: Decimal | None,
    student_ids: Iterable[uuid.UUID],
) -> tuple[Assignment, list[AssignmentSubmission]]:
    """C2 step 8: the assignment and one empty submission row per recipient
    (by ``student_id``); the automatic audit events record the create."""
    assignment = Assignment(
        id=uuid.uuid7(),
        creator_user_id=creator_user_id,
        subject_id=subject_id,
        class_session_id=class_session_id,
        title=title,
        instructions=instructions,
        due_on=due_on,
        due_time=due_time,
        max_score=max_score,
    )
    session.add(assignment)
    await session.flush()
    rows = await add_recipients(session, assignment.id, student_ids)
    return assignment, rows


async def add_recipients(
    session: AsyncSession, assignment_id: uuid.UUID, student_ids: Iterable[uuid.UUID]
) -> list[AssignmentSubmission]:
    """New empty submission rows (by ``student_id``)."""
    rows = [
        AssignmentSubmission(
            id=uuid.uuid7(), assignment_id=assignment_id, student_id=student_id
        )
        for student_id in sorted(set(student_ids))
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def remove_recipients(
    session: AsyncSession, rows: Iterable[AssignmentSubmission]
) -> None:
    """Deletes these (unsubmitted, checked by the caller) rows; the audit
    keeps them."""
    for row in rows:
        await session.delete(row)
    await session.flush()


async def change_assignment(
    session: AsyncSession, assignment: Assignment, changes: Mapping[str, Any]
) -> None:
    """C4 on the locked row: the changed fields; a recipients-only change
    still bumps the version once (``updated_at``)."""
    if changes:
        for key, value in changes.items():
            setattr(assignment, key, value)
    else:
        assignment.updated_at = func.now()
    await session.flush()
    await session.refresh(assignment, ["updated_at"])


async def soft_delete_assignment(
    session: AsyncSession,
    assignment: Assignment,
    *,
    actor_id: uuid.UUID,
    now: datetime,
) -> None:
    """C5: the soft delete (the caller checked nothing is submitted)."""
    assignment.deleted_at = now
    assignment.deleted_by_user_id = actor_id
    await session.flush()


async def record_submission(
    session: AsyncSession,
    row: AssignmentSubmission,
    *,
    text: str | None,
    url: str | None,
    via: str,
    actor_id: uuid.UUID,
    now: datetime,
) -> None:
    """D1 on the locked row: the content, who, how and when (a replacement
    overwrites the previous one; the audit keeps it)."""
    row.text = text
    row.url = url
    row.submitted_via = via
    row.submitted_by_user_id = actor_id
    row.submitted_at = now
    await session.flush()


async def grade_submission(
    session: AsyncSession,
    row: AssignmentSubmission,
    *,
    score: Decimal | None,
    score_percent: Decimal | None,
    feedback: str | None,
    actor_id: uuid.UUID,
    now: datetime,
    today: date,
    change_reason: str | None,
) -> bool:
    """D2 on the locked row. A first grade sets ``graded_at``,
    ``graded_on`` and ``graded_by_user_id``; a re-grade (``change_reason``)
    sets ``grade_amended_at`` and ``last_change_reason`` and keeps
    ``graded_on``. ``False`` (no write) for a re-grade that changes
    nothing."""
    first = row.graded_at is None
    if not first and row.score == score and row.feedback == feedback:
        return False
    row.score = score
    row.score_percent = score_percent
    row.feedback = feedback
    if first:
        row.graded_at = now
        row.graded_on = today
        row.graded_by_user_id = actor_id
    else:
        row.grade_amended_at = now
        row.last_change_reason = change_reason
    await session.flush()
    return True
