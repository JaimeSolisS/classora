"""Evaluation rows (#80 B1, D, E, F).

Every function runs in the caller's Unit of Work and never commits. They
take plain ids and values: who may call them is decided by
``app.workflows.evaluations`` with the pure rules in
:mod:`~app.modules.academics.evaluation_rules`.

The evaluation row is the **version anchor** of its scores: a write that
changes only scores still bumps its ``version`` once (``updated_at``
alone). Writes take the row ``FOR UPDATE`` (:func:`evaluation_for_update`,
re-read); the automatic audit events record the diffs (spec §9). Deleted
evaluations are never returned.

The lists join caller-built, **unexecuted** selects of other modules'
tables as subqueries (the sessions' ``starts_at``, the subjects' names),
so a list and those facts come in one statement while academics never
imports scheduling or catalog (#77 E2).
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, and_, false, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.modules.academics.evaluation_rules import DRAFT, FINAL, score_totals
from app.modules.academics.models import Evaluation, EvaluationScore


@dataclass(frozen=True)
class CopiedCriterion:
    """A template criterion as copied onto a new evaluation (E2)."""

    position: int
    title: str
    description: str | None
    max_score: Decimal


@dataclass(frozen=True)
class ScoreInput:
    """One written score: ``score`` / ``comment`` given (``*_set``) or left
    as they are."""

    position: int
    score: Decimal | None
    score_set: bool
    comment: str | None
    comment_set: bool


@dataclass(frozen=True)
class WorkingRow:
    evaluation: Evaluation
    session_starts_at: datetime | None


@dataclass(frozen=True)
class WorkingPage:
    rows: list[WorkingRow]
    next_cursor: str | None


# --- Reads ----------------------------------------------------------------------------


async def evaluation_by_id(
    session: AsyncSession, evaluation_id: uuid.UUID
) -> Evaluation | None:
    """The live evaluation, or ``None`` (unknown or deleted). One statement,
    no lock."""
    return cast(
        Evaluation | None,
        await session.scalar(
            select(Evaluation).where(
                Evaluation.id == evaluation_id, Evaluation.deleted_at.is_(None)
            )
        ),
    )


async def evaluation_for_update(
    session: AsyncSession, evaluation_id: uuid.UUID
) -> Evaluation | None:
    """The live evaluation ``FOR UPDATE``, re-read, or ``None`` when it is
    gone or was deleted meanwhile."""
    return cast(
        Evaluation | None,
        await session.scalar(
            select(Evaluation)
            .where(Evaluation.id == evaluation_id, Evaluation.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


async def scores_of(
    session: AsyncSession, evaluation_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[EvaluationScore]]:
    """The scores of these evaluations in position order. One statement
    (none for no ids)."""
    found: dict[uuid.UUID, list[EvaluationScore]] = {i: [] for i in evaluation_ids}
    if not evaluation_ids:
        return found
    rows = await session.scalars(
        select(EvaluationScore)
        .where(EvaluationScore.evaluation_id.in_(set(evaluation_ids)))
        .order_by(EvaluationScore.evaluation_id, EvaluationScore.position)
        .execution_options(populate_existing=True)
    )
    for row in rows:
        found[row.evaluation_id].append(row)
    return found


def _position(cursor: str) -> tuple[datetime, uuid.UUID]:
    created_at, evaluation_id = decode_cursor(cursor, types=(str, str))
    try:
        when = datetime.fromisoformat(str(created_at))
        identifier = uuid.UUID(str(evaluation_id))
    except ValueError:
        raise InvalidCursorError() from None
    if when.tzinfo is None:
        raise InvalidCursorError()
    return when, identifier


def _readable(
    *, all_final: bool, live_final: bool, own_user_id: uuid.UUID | None
) -> ColumnElement[bool]:
    """E1: the union of what the caller's relations read."""
    parts: list[ColumnElement[bool]] = []
    if all_final:
        parts.append(Evaluation.status == FINAL)
    if live_final:
        parts.append(and_(Evaluation.status == FINAL, Evaluation.voided_at.is_(None)))
    if own_user_id is not None:
        parts.append(Evaluation.author_user_id == own_user_id)
    return or_(*parts) if parts else false()


async def working_page(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    sessions: Select[*tuple[Any, ...]],
    all_final: bool,
    live_final: bool,
    own_user_id: uuid.UUID | None,
    status: str | None,
    subject_id: uuid.UUID | None,
    class_session_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> WorkingPage:
    """E1, E2: the student's live evaluations the caller reads (reviewers
    every final one, voided too: ``all_final``; tutors with the student
    assigned every final, non-voided one: ``live_final``; an author their
    own in any state: ``own_user_id``), newest first by ``(created_at,
    id)``, with each linked session's start (``sessions``: an unexecuted
    ``(id, starts_at, …)`` select, outer-joined). One statement."""
    joined = sessions.subquery()
    query = (
        select(Evaluation, joined.c.starts_at)
        .outerjoin(joined, joined.c.id == Evaluation.class_session_id)
        .where(
            Evaluation.student_id == student_id,
            Evaluation.deleted_at.is_(None),
            _readable(
                all_final=all_final, live_final=live_final, own_user_id=own_user_id
            ),
        )
    )
    if status == DRAFT:
        query = query.where(Evaluation.status == DRAFT)
    elif status == FINAL:
        query = query.where(Evaluation.status == FINAL, Evaluation.voided_at.is_(None))
    elif status == "voided":
        query = query.where(Evaluation.voided_at.is_not(None))
    if subject_id is not None:
        query = query.where(Evaluation.subject_id == subject_id)
    if class_session_id is not None:
        query = query.where(Evaluation.class_session_id == class_session_id)
    if cursor is not None:
        when, identifier = _position(cursor)
        query = query.where(
            tuple_(Evaluation.created_at, Evaluation.id) < tuple_(when, identifier)
        )
    result = await session.execute(
        query.order_by(Evaluation.created_at.desc(), Evaluation.id.desc()).limit(
            limit + 1
        )
    )
    rows = [WorkingRow(evaluation, starts_at) for evaluation, starts_at in result]
    items = rows[:limit]
    next_cursor = (
        encode_cursor(
            [
                items[-1].evaluation.created_at.isoformat(),
                str(items[-1].evaluation.id),
            ]
        )
        if len(rows) > limit and items
        else None
    )
    return WorkingPage(rows=items, next_cursor=next_cursor)


async def has_own_evaluation(
    session: AsyncSession, student_id: uuid.UUID, author_user_id: uuid.UUID
) -> bool:
    """Whether the author has a live evaluation of the student (a former
    author's access, E1). One statement."""
    found = await session.scalar(
        select(Evaluation.id)
        .where(
            Evaluation.student_id == student_id,
            Evaluation.author_user_id == author_user_id,
            Evaluation.deleted_at.is_(None),
        )
        .limit(1)
    )
    return found is not None


async def history_rows(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    subjects: Select[*tuple[Any, ...]],
    author_user_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    from_: date | None,
    to: date | None,
    limit: int,
) -> list[tuple[Evaluation, str]]:
    """F2: the student's **final, non-voided, live** evaluations (only one
    author's when given), newest first by ``evaluated_on``, then
    ``finalized_at``, then ``id``, at most ``limit``, each with its
    subject's name (``subjects``: an unexecuted ``(id, name)`` select). One
    statement. ``status = 'final'``, ``voided_at IS NULL`` and ``deleted_at
    IS NULL`` are always applied here: drafts and voided evaluations never
    leave this function."""
    joined = subjects.subquery()
    query = (
        select(Evaluation, joined.c.name)
        .join(joined, joined.c.id == Evaluation.subject_id)
        .where(
            Evaluation.student_id == student_id,
            Evaluation.status == FINAL,
            Evaluation.voided_at.is_(None),
            Evaluation.deleted_at.is_(None),
        )
    )
    if author_user_id is not None:
        query = query.where(Evaluation.author_user_id == author_user_id)
    if subject_id is not None:
        query = query.where(Evaluation.subject_id == subject_id)
    if from_ is not None:
        query = query.where(Evaluation.evaluated_on >= from_)
    if to is not None:
        query = query.where(Evaluation.evaluated_on <= to)
    result = await session.execute(
        query.order_by(
            Evaluation.evaluated_on.desc(),
            Evaluation.finalized_at.desc(),
            Evaluation.id.desc(),
        ).limit(limit)
    )
    return [(evaluation, str(name)) for evaluation, name in result]


# --- Writes ---------------------------------------------------------------------------


async def create_evaluation(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    subject_id: uuid.UUID,
    author_user_id: uuid.UUID,
    template_id: uuid.UUID,
    template_version: int,
    template_name: str,
    kind: str,
    criteria: Sequence[CopiedCriterion],
    evaluated_on: date,
    class_session_id: uuid.UUID | None,
    cohort_id: uuid.UUID | None,
    course_assessment_id: uuid.UUID | None,
    course_assessment_title: str | None,
    scores: Mapping[int, ScoreInput],
    feedback: str | None,
) -> tuple[Evaluation, list[EvaluationScore]]:
    """D2 step 11: a ``draft`` with every criterion copied (an unscored one
    has a ``NULL`` score); the automatic audit events record the create."""
    evaluation = Evaluation(
        id=uuid.uuid7(),
        student_id=student_id,
        subject_id=subject_id,
        author_user_id=author_user_id,
        template_id=template_id,
        template_version=template_version,
        template_name=template_name,
        kind=kind,
        evaluated_on=evaluated_on,
        class_session_id=class_session_id,
        cohort_id=cohort_id,
        course_assessment_id=course_assessment_id,
        course_assessment_title=course_assessment_title,
        status=DRAFT,
        feedback=feedback,
    )
    session.add(evaluation)
    await session.flush()
    rows = []
    for criterion in criteria:
        written = scores.get(criterion.position)
        rows.append(
            EvaluationScore(
                id=uuid.uuid7(),
                evaluation_id=evaluation.id,
                position=criterion.position,
                criterion_title=criterion.title,
                criterion_description=criterion.description,
                max_score=criterion.max_score,
                score=None if written is None else written.score,
                comment=None if written is None else written.comment,
            )
        )
    session.add_all(rows)
    await session.flush()
    return evaluation, rows


def _totals(rows: Sequence[EvaluationScore]) -> tuple[Decimal, Decimal, Decimal]:
    pairs = []
    for row in rows:
        assert row.score is not None  # noqa: S101 (checked by the caller)
        pairs.append((Decimal(row.score), Decimal(row.max_score)))
    return score_totals(pairs)


async def change_evaluation(
    session: AsyncSession,
    evaluation: Evaluation,
    rows: Sequence[EvaluationScore],
    *,
    evaluated_on: date | None,
    scores: Sequence[ScoreInput],
    feedback: str | None,
    feedback_set: bool,
    amend_reason: str | None,
    now: datetime,
) -> bool:
    """D4 on the locked row: the changed date, scores and feedback. On a
    final evaluation (``amend_reason`` given) the totals are recomputed and
    ``amended_at`` / ``last_change_reason`` set. ``False`` when nothing
    changes (no write, no version bump)."""
    changes: dict[str, Any] = {}
    if evaluated_on is not None and evaluated_on != evaluation.evaluated_on:
        changes["evaluated_on"] = evaluated_on
    if feedback_set and feedback != evaluation.feedback:
        changes["feedback"] = feedback
    by_position = {row.position: row for row in rows}
    updates: list[tuple[EvaluationScore, dict[str, Any]]] = []
    for written in scores:
        row = by_position[written.position]
        update: dict[str, Any] = {}
        if written.score_set and written.score != row.score:
            update["score"] = written.score
        if written.comment_set and written.comment != row.comment:
            update["comment"] = written.comment
        if update:
            updates.append((row, update))
    if not changes and not updates:
        return False
    for row, update in updates:
        for key, value in update.items():
            setattr(row, key, value)
    if amend_reason is not None:
        total, maximum, percent = _totals(rows)
        changes |= {
            "score_total": total,
            "max_total": maximum,
            "score_percent": percent,
            "amended_at": now,
            "last_change_reason": amend_reason,
        }
    for key, value in changes.items():
        setattr(evaluation, key, value)
    if not changes:
        evaluation.updated_at = func.now()
    await session.flush()
    await session.refresh(evaluation, ["updated_at"])
    return True


async def finalize_evaluation(
    session: AsyncSession,
    evaluation: Evaluation,
    rows: Sequence[EvaluationScore],
    *,
    now: datetime,
) -> None:
    """D5: ``draft`` → ``final`` with the totals (every criterion scored:
    checked by the caller)."""
    total, maximum, percent = _totals(rows)
    evaluation.status = FINAL
    evaluation.finalized_at = now
    evaluation.score_total = total
    evaluation.max_total = maximum
    evaluation.score_percent = percent
    await session.flush()


async def void_evaluation(
    session: AsyncSession,
    evaluation: Evaluation,
    *,
    actor_id: uuid.UUID,
    reason: str,
    now: datetime,
) -> None:
    """D6: the void (who, when, why)."""
    evaluation.voided_at = now
    evaluation.voided_by_user_id = actor_id
    evaluation.void_reason = reason
    await session.flush()


async def soft_delete_evaluation(
    session: AsyncSession, evaluation: Evaluation, *, actor_id: uuid.UUID, now: datetime
) -> None:
    """D7: soft delete of a draft (the CHECK backs it up)."""
    evaluation.deleted_at = now
    evaluation.deleted_by_user_id = actor_id
    await session.flush()
