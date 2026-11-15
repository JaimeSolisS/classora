"""The facts of a progress report (#83 B2, E2).

A **family-safe aggregate snapshot** of one student's academic records for
one period (and optionally one subject), compiled by the server and frozen
on the report at each submit. Its sources are records the student's family
can already see, and only aggregates of them:

- evaluations (#80): final, non-voided, live ones with ``evaluated_on`` in
  the period: the count, the average percentage, per subject, and at most
  :data:`~app.modules.academics.progress_report_rules.FACT_EVALUATIONS_MAX`
  entries (date, subject, kind, template name, percentage; never feedback,
  comments, reasons or the author);
- homework (#82): the student's own rows of live assignments due in the
  period: the counts per state as computed **now** (``pending``,
  ``missing``, ``submitted``, ``graded``), the late count and the average
  percentage of the scored grades (never titles, text, links or feedback);
- learning plans (#81): the family-visible plans (active, completed,
  discontinued after an activation) that started by the period's end and
  were active during it or are active now: title, subject, status and
  progress (never descriptions, objectives' or milestones' text);
- attendance (#76) comes from ``scheduling.service.student_attendance_summary``
  (the workflow adds it: academics never imports scheduling).

Never: notes (#79), closeout summaries (#77), other students, names or ids
of people, reasons of any kind.

The evaluation and homework conditions and the homework state counts are
shared with the progress dashboard (#84 B4,
:mod:`~app.modules.academics.progress_aggregates`).

:func:`academic_facts` reads the three academic sources in at most four
statements, whatever the number of rows; :func:`facts_document` assembles
the closed JSON (``schema_version`` 1; :data:`FACT_KEYS` and friends are
its exact key sets). Decimals are strings with two places (percentages of
plans: whole numbers), dates ISO strings, ids strings.
"""

import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics import assignment_rules, learning_plan_rules
from app.modules.academics.evaluation_rules import average_percent
from app.modules.academics.learning_plans import (
    family_visible_condition,
    milestone_facts,
)
from app.modules.academics.models import (
    Assignment,
    AssignmentSubmission,
    Evaluation,
    LearningPlan,
)
from app.modules.academics.progress_aggregates import (
    counted_evaluation_conditions,
    homework_conditions,
    homework_state_counts,
)
from app.modules.academics.progress_report_rules import (
    FACT_EVALUATIONS_MAX,
    FACT_PLANS_MAX,
)

SCHEMA_VERSION: Final = 1

FACT_KEYS: Final = frozenset(
    {
        "schema_version",
        "period",
        "subject",
        "attendance",
        "evaluations",
        "assignments",
        "learning_plans",
        "learning_plans_truncated",
    }
)
PERIOD_KEYS: Final = frozenset({"start", "end"})
SUBJECT_KEYS: Final = frozenset({"id", "name"})
ATTENDANCE_KEYS: Final = frozenset(
    {"sessions", "present", "late", "absent", "not_recorded"}
)
EVALUATION_KEYS: Final = frozenset(
    {"count", "average_percent", "by_subject", "entries", "truncated"}
)
EVALUATION_SUBJECT_KEYS: Final = frozenset({"subject", "count", "average_percent"})
EVALUATION_ENTRY_KEYS: Final = frozenset(
    {"evaluated_on", "subject", "evaluation_kind", "template_name", "score_percent"}
)
ASSIGNMENT_KEYS: Final = frozenset(
    {"count", "pending", "missing", "submitted", "graded", "late", "average_percent"}
)
PLAN_KEYS: Final = frozenset(
    {
        "title",
        "subject",
        "status",
        "progress_percent",
        "objectives_completed",
        "objectives_total",
        "milestones_done",
        "milestones_total",
    }
)


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _subject(subject_id: uuid.UUID, names: Mapping[uuid.UUID, str]) -> dict[str, str]:
    return {"id": str(subject_id), "name": names.get(subject_id, "")}


async def _evaluations(
    session: AsyncSession,
    student_id: uuid.UUID,
    start: date,
    end: date,
    subject_id: uuid.UUID | None,
    subjects: Select[*tuple[Any, ...]],
) -> dict[str, Any]:
    joined = subjects.subquery()
    query = (
        select(
            Evaluation.evaluated_on,
            Evaluation.subject_id,
            joined.c.name,
            Evaluation.kind,
            Evaluation.template_name,
            Evaluation.score_percent,
        )
        .outerjoin(joined, joined.c.id == Evaluation.subject_id)
        .where(*counted_evaluation_conditions(student_id, start, end))
        .order_by(
            Evaluation.evaluated_on.desc(),
            Evaluation.finalized_at.desc(),
            Evaluation.id.desc(),
        )
    )
    if subject_id is not None:
        query = query.where(Evaluation.subject_id == subject_id)
    rows = list(await session.execute(query))
    names = {row.subject_id: "" if row.name is None else str(row.name) for row in rows}
    percents: list[Decimal] = [row.score_percent for row in rows]
    by_subject: dict[uuid.UUID, list[Decimal]] = defaultdict(list)
    for row in rows:
        by_subject[row.subject_id].append(row.score_percent)
    ordered = sorted(by_subject, key=lambda s: (names[s].casefold(), str(s)))
    return {
        "count": len(rows),
        "average_percent": _decimal(average_percent(percents)),
        "by_subject": [
            {
                "subject": _subject(s, names),
                "count": len(by_subject[s]),
                "average_percent": _decimal(average_percent(by_subject[s])),
            }
            for s in ordered
        ],
        "entries": [
            {
                "evaluated_on": row.evaluated_on.isoformat(),
                "subject": _subject(row.subject_id, names),
                "evaluation_kind": row.kind,
                "template_name": row.template_name,
                "score_percent": _decimal(row.score_percent),
            }
            for row in rows[:FACT_EVALUATIONS_MAX]
        ],
        "truncated": len(rows) > FACT_EVALUATIONS_MAX,
    }


async def _assignments(
    session: AsyncSession,
    student_id: uuid.UUID,
    start: date,
    end: date,
    subject_id: uuid.UUID | None,
    *,
    zone: str,
    now: datetime,
) -> dict[str, Any]:
    query = (
        select(
            Assignment.due_on,
            Assignment.due_time,
            AssignmentSubmission.submitted_at,
            AssignmentSubmission.graded_at,
            AssignmentSubmission.score_percent,
        )
        .join(AssignmentSubmission, AssignmentSubmission.assignment_id == Assignment.id)
        .where(
            *homework_conditions(student_id),
            Assignment.due_on >= start,
            Assignment.due_on <= end,
        )
    )
    if subject_id is not None:
        query = query.where(Assignment.subject_id == subject_id)
    rows = list(await session.execute(query))
    scored: list[Decimal] = [
        row.score_percent
        for row in rows
        if row.graded_at is not None and row.score_percent is not None
    ]
    return {
        "count": len(rows),
        **homework_state_counts(rows, zone=zone, now=now),
        "average_percent": _decimal(assignment_rules.average_percent(scored)),
    }


def _active_during(plan: LearningPlan, start: date, end: date, zone: ZoneInfo) -> bool:
    """Active now, or active at some point of ``[start, end]`` (center-local
    dates of the activation and the closing)."""
    if plan.status == learning_plan_rules.ACTIVE:
        return True
    if plan.activated_at is None:
        return False
    if plan.activated_at.astimezone(zone).date() > end:
        return False
    return plan.closed_at is None or plan.closed_at.astimezone(zone).date() >= start


async def _plans(
    session: AsyncSession,
    student_id: uuid.UUID,
    start: date,
    end: date,
    subject_id: uuid.UUID | None,
    subjects: Select[*tuple[Any, ...]],
    *,
    zone: str,
) -> tuple[list[dict[str, Any]], bool]:
    joined = subjects.subquery()
    query = (
        select(LearningPlan, joined.c.name)
        .outerjoin(joined, joined.c.id == LearningPlan.subject_id)
        .where(
            LearningPlan.student_id == student_id,
            LearningPlan.deleted_at.is_(None),
            family_visible_condition(),
            LearningPlan.starts_on <= end,
        )
    )
    if subject_id is not None:
        query = query.where(LearningPlan.subject_id == subject_id)
    local = ZoneInfo(zone)
    found = [
        (plan, "" if name is None else str(name))
        for plan, name in await session.execute(query)
        if _active_during(plan, start, end, local)
    ]
    found.sort(
        key=lambda item: (
            item[0].status != learning_plan_rules.ACTIVE,
            -item[0].starts_on.toordinal(),
            str(item[0].id),
        )
    )
    truncated = len(found) > FACT_PLANS_MAX
    found = found[:FACT_PLANS_MAX]
    facts = await milestone_facts(session, [plan.id for plan, _ in found])
    plans = []
    for plan, name in found:
        milestones = facts[plan.id]
        by_objective: dict[uuid.UUID, list[str]] = defaultdict(list)
        for fact in milestones:
            by_objective[fact.objective_id].append(fact.status)
        done, total, percent = learning_plan_rules.progress(
            fact.status for fact in milestones
        )
        plans.append(
            {
                "title": plan.title,
                "subject": {"id": str(plan.subject_id), "name": name},
                "status": plan.status,
                "progress_percent": str(percent),
                "objectives_completed": sum(
                    1
                    for statuses in by_objective.values()
                    if all(s == learning_plan_rules.DONE for s in statuses)
                ),
                "objectives_total": len(by_objective),
                "milestones_done": done,
                "milestones_total": total,
            }
        )
    return plans, truncated


async def academic_facts(
    session: AsyncSession,
    student_id: uuid.UUID,
    start: date,
    end: date,
    subject_id: uuid.UUID | None,
    *,
    subjects: Select[*tuple[Any, ...]],
    zone: str,
    now: datetime,
) -> dict[str, Any]:
    """The ``evaluations``, ``assignments``, ``learning_plans`` and
    ``learning_plans_truncated`` sections for the student, the local period
    ``[start, end]`` and the optional subject (``subjects``: an unexecuted
    ``(id, name)`` select; ``zone``: the center's time zone; ``now``: when
    the homework states are computed). At most four statements."""
    evaluations = await _evaluations(
        session, student_id, start, end, subject_id, subjects
    )
    assignments = await _assignments(
        session, student_id, start, end, subject_id, zone=zone, now=now
    )
    plans, truncated = await _plans(
        session, student_id, start, end, subject_id, subjects, zone=zone
    )
    return {
        "evaluations": evaluations,
        "assignments": assignments,
        "learning_plans": plans,
        "learning_plans_truncated": truncated,
    }


def facts_document(
    *,
    start: date,
    end: date,
    subject: tuple[uuid.UUID, str] | None,
    attendance: Mapping[str, int],
    academic: Mapping[str, Any],
) -> dict[str, Any]:
    """The closed facts JSON (schema version 1) from the period, the report's
    subject ``(id, name)`` or ``None``, the attendance counts and
    :func:`academic_facts`."""
    return {
        "schema_version": SCHEMA_VERSION,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "subject": (
            None if subject is None else {"id": str(subject[0]), "name": subject[1]}
        ),
        "attendance": {key: int(attendance[key]) for key in sorted(ATTENDANCE_KEYS)},
        "evaluations": academic["evaluations"],
        "assignments": academic["assignments"],
        "learning_plans": list(academic["learning_plans"]),
        "learning_plans_truncated": bool(academic["learning_plans_truncated"]),
    }
