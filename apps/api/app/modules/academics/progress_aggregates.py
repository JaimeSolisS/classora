"""The academic reads of the progress dashboard (#84 M5-17 D2, E6), shared
with the progress report facts (#83 B2,
:mod:`~app.modules.academics.progress_report_facts`), so a live dashboard
number and a report compiled for the same scope never disagree (#84 B4):

- :func:`counted_evaluation_conditions`: final, non-voided, live evaluations
  of the student with ``evaluated_on`` in a range (the facts use it too);
  :func:`evaluation_scores` reads their date, subject and percentage (one
  statement);
- :func:`homework_conditions`: the student's own rows of live assignments;
  :func:`homework_rows` reads those due in a range **or** graded (scored) in
  it (one statement); :func:`homework_state_counts` counts the #82 states at
  an instant (the facts count with it too); :func:`is_counted_grade`;
- :func:`dashboard_plans`: the student's live ``active`` and ``completed``
  plans (one statement; the milestones come from
  :func:`~app.modules.academics.learning_plans.milestone_facts`);
  :func:`plan_counts` gives a plan's objective and milestone numbers with the
  #81 rules.

Only numbers, dates, subjects and plan titles leave this module: never
feedback, comments, instructions, submission text, descriptions or reasons
(#84 B6). Every function runs in the caller's Unit of Work and never
commits.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics import assignment_rules, learning_plan_rules
from app.modules.academics.evaluation_rules import FINAL
from app.modules.academics.learning_plans import MilestoneFact
from app.modules.academics.models import (
    Assignment,
    AssignmentSubmission,
    Evaluation,
    LearningPlan,
)

DASHBOARD_PLAN_STATUSES = (learning_plan_rules.ACTIVE, learning_plan_rules.COMPLETED)
"""E6: the family-visible plans the dashboard lists (never a draft, a
discontinued or a deleted plan)."""


@dataclass(frozen=True)
class ScoreRow:
    """One counted evaluation (E6)."""

    evaluated_on: date
    subject_id: uuid.UUID
    subject_name: str
    score_percent: Decimal


@dataclass(frozen=True)
class HomeworkRow:
    """One of the student's homework rows (E6): the deadline parts, the
    submission and grade instants, the grade's local date and percentage."""

    subject_id: uuid.UUID
    subject_name: str
    due_on: date
    due_time: time | None
    submitted_at: datetime | None
    graded_at: datetime | None
    graded_on: date | None
    score_percent: Decimal | None


@dataclass(frozen=True)
class PlanCounts:
    """A plan's numbers with the #81 rules."""

    progress_percent: Decimal
    objectives_complete: int
    objectives_total: int
    milestones_done: int
    milestones_total: int
    milestones_overdue: int


# --- Evaluations ----------------------------------------------------------------------


def counted_evaluation_conditions(
    student_id: uuid.UUID, start: date, end: date
) -> tuple[ColumnElement[bool], ...]:
    """The evaluations a dashboard or a report counts: the student's final,
    non-voided, live evaluations with ``evaluated_on`` in ``[start, end]``."""
    return (
        Evaluation.student_id == student_id,
        Evaluation.status == FINAL,
        Evaluation.voided_at.is_(None),
        Evaluation.deleted_at.is_(None),
        Evaluation.evaluated_on >= start,
        Evaluation.evaluated_on <= end,
    )


async def evaluation_scores(
    session: AsyncSession,
    student_id: uuid.UUID,
    start: date,
    end: date,
    *,
    subjects: Select[*tuple[Any, ...]],
) -> list[ScoreRow]:
    """The counted evaluations (every subject: the caller filters), oldest
    first (``subjects``: an unexecuted ``(id, name)`` select). One
    statement."""
    joined = subjects.subquery()
    rows = await session.execute(
        select(
            Evaluation.evaluated_on,
            Evaluation.subject_id,
            joined.c.name,
            Evaluation.score_percent,
        )
        .outerjoin(joined, joined.c.id == Evaluation.subject_id)
        .where(*counted_evaluation_conditions(student_id, start, end))
        .order_by(Evaluation.evaluated_on, Evaluation.id)
    )
    return [
        ScoreRow(
            evaluated_on=row.evaluated_on,
            subject_id=row.subject_id,
            subject_name="" if row.name is None else str(row.name),
            score_percent=row.score_percent,
        )
        for row in rows
    ]


# --- Homework -------------------------------------------------------------------------


def homework_conditions(student_id: uuid.UUID) -> tuple[ColumnElement[bool], ...]:
    """The student's own rows (a removed recipient's row is deleted) of live
    assignments; the caller joins :class:`Assignment` to
    :class:`AssignmentSubmission`."""
    return (
        AssignmentSubmission.student_id == student_id,
        Assignment.deleted_at.is_(None),
    )


def is_counted_grade(row: HomeworkRow, start: date, end: date) -> bool:
    """E6: a scored grade (feedback-only grades have no percentage) whose
    local grading date is in ``[start, end]``."""
    return (
        row.graded_at is not None
        and row.score_percent is not None
        and row.graded_on is not None
        and start <= row.graded_on <= end
    )


async def homework_rows(
    session: AsyncSession,
    student_id: uuid.UUID,
    start: date,
    end: date,
    *,
    subjects: Select[*tuple[Any, ...]],
) -> list[HomeworkRow]:
    """The student's homework rows **due** in ``[start, end]`` (the state
    counts) or **graded** with a score in it (the averages), every subject
    (``subjects``: an unexecuted ``(id, name)`` select). One statement."""
    joined = subjects.subquery()
    rows = await session.execute(
        select(
            Assignment.subject_id,
            joined.c.name,
            Assignment.due_on,
            Assignment.due_time,
            AssignmentSubmission.submitted_at,
            AssignmentSubmission.graded_at,
            AssignmentSubmission.graded_on,
            AssignmentSubmission.score_percent,
        )
        .join(AssignmentSubmission, AssignmentSubmission.assignment_id == Assignment.id)
        .outerjoin(joined, joined.c.id == Assignment.subject_id)
        .where(
            *homework_conditions(student_id),
            or_(
                and_(Assignment.due_on >= start, Assignment.due_on <= end),
                and_(
                    AssignmentSubmission.score_percent.is_not(None),
                    AssignmentSubmission.graded_on >= start,
                    AssignmentSubmission.graded_on <= end,
                ),
            ),
        )
        .order_by(AssignmentSubmission.id)
    )
    return [
        HomeworkRow(
            subject_id=row.subject_id,
            subject_name="" if row.name is None else str(row.name),
            due_on=row.due_on,
            due_time=row.due_time,
            submitted_at=row.submitted_at,
            graded_at=row.graded_at,
            graded_on=row.graded_on,
            score_percent=row.score_percent,
        )
        for row in rows
    ]


def homework_state_counts(
    rows: Iterable[Any], *, zone: str, now: datetime
) -> dict[str, int]:
    """The #82 states of these rows at ``now`` (``pending``, ``missing``,
    ``submitted``, ``graded``) and the ``late`` count; each row has
    ``due_on``, ``due_time``, ``submitted_at`` and ``graded_at``."""
    counts = dict.fromkeys(assignment_rules.STATES, 0)
    late = 0
    for row in rows:
        deadline = assignment_rules.deadline_instant(row.due_on, row.due_time, zone)
        state = assignment_rules.submission_state(
            row.submitted_at, row.graded_at, deadline, now
        )
        counts[state] += 1
        if assignment_rules.is_late(row.submitted_at, deadline):
            late += 1
    return {**counts, "late": late}


# --- Learning plans -------------------------------------------------------------------


async def dashboard_plans(
    session: AsyncSession, student_id: uuid.UUID, *, subjects: Select[*tuple[Any, ...]]
) -> list[tuple[LearningPlan, str]]:
    """The student's live ``active`` and ``completed`` plans with their
    subject's name, every subject (``subjects``: an unexecuted ``(id,
    name)`` select). One statement."""
    joined = subjects.subquery()
    rows = await session.execute(
        select(LearningPlan, joined.c.name)
        .outerjoin(joined, joined.c.id == LearningPlan.subject_id)
        .where(
            LearningPlan.student_id == student_id,
            LearningPlan.deleted_at.is_(None),
            LearningPlan.status.in_(DASHBOARD_PLAN_STATUSES),
        )
    )
    return [(plan, "" if name is None else str(name)) for plan, name in rows]


def plan_counts(milestones: Iterable[MilestoneFact], today: date) -> PlanCounts:
    """#81: the progress percentage (whole number), the objectives whose
    milestones are all done, and the done and overdue milestones (at the
    center's ``today``)."""
    facts = list(milestones)
    by_objective: dict[uuid.UUID, list[str]] = defaultdict(list)
    for fact in facts:
        by_objective[fact.objective_id].append(fact.status)
    done, total, progress = learning_plan_rules.progress(f.status for f in facts)
    return PlanCounts(
        progress_percent=progress,
        objectives_complete=sum(
            1
            for statuses in by_objective.values()
            if all(s == learning_plan_rules.DONE for s in statuses)
        ),
        objectives_total=len(by_objective),
        milestones_done=done,
        milestones_total=total,
        milestones_overdue=sum(
            1
            for f in facts
            if learning_plan_rules.is_overdue(f.status, f.due_on, today)
        ),
    )
