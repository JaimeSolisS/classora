"""Learning plan rows (#81 B1, C, D, E).

Every function runs in the caller's Unit of Work and never commits. They
take plain ids and values: who may call them is decided by
``app.workflows.learning_plans`` with the pure rules in
:mod:`~app.modules.academics.learning_plan_rules`.

The plan row is the **version anchor** of its objectives and milestone
definitions: a content write that changes only them still bumps the plan's
``version`` once (``updated_at`` alone). Each milestone has its own
``version`` for progress (D2). Writes take the plan ``FOR UPDATE``
(:func:`get_plan_for_update`; a progress write takes it ``FOR SHARE``,
:func:`get_plan_for_share`); the automatic audit events record the diffs
(spec §9). Deleted plans are never returned.

The reads join a caller-built, **unexecuted** ``(id, name)`` select of the
subjects as a subquery, so a plan and its subject's name come in one
statement while academics never imports catalog (#77 E2).
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.errors import ActivePlanExistsError
from app.modules.academics.learning_plan_rules import (
    ACTIVE,
    COMPLETED,
    DISCONTINUED,
    DONE,
    DRAFT,
    PENDING,
    TransitionAction,
)
from app.modules.academics.models import (
    ACTIVE_PLAN_UNIQUE,
    LearningPlan,
    LearningPlanMilestone,
    LearningPlanObjective,
)


@dataclass(frozen=True)
class MilestoneValues:
    """One milestone as written (``id`` ``None``: a new one)."""

    id: uuid.UUID | None
    title: str
    activities: str | None
    due_on: date | None


@dataclass(frozen=True)
class ObjectiveValues:
    """One objective as written (``id`` ``None``: a new one), with its
    milestones in order."""

    id: uuid.UUID | None
    title: str
    description: str | None
    milestones: Sequence[MilestoneValues]


@dataclass(frozen=True)
class ObjectiveRow:
    objective: LearningPlanObjective
    milestones: list[LearningPlanMilestone]


@dataclass(frozen=True)
class PlanRow:
    plan: LearningPlan
    subject_name: str


@dataclass(frozen=True)
class MilestoneFact:
    """What a list summary needs of one milestone (E2)."""

    plan_id: uuid.UUID
    objective_id: uuid.UUID
    status: str
    due_on: date | None


# --- Reads ----------------------------------------------------------------------------


def family_visible_condition() -> ColumnElement[bool]:
    """E3 as SQL (:func:`~app.modules.academics.learning_plan_rules
    .family_visible`): active, completed, or discontinued after an
    activation; never a draft. The caller also excludes deleted rows."""
    return or_(
        LearningPlan.status.in_((ACTIVE, COMPLETED)),
        and_(
            LearningPlan.status == DISCONTINUED,
            LearningPlan.activated_at.is_not(None),
        ),
    )


async def plan_with_subject(
    session: AsyncSession, plan_id: uuid.UUID, *, subjects: Select[*tuple[Any, ...]]
) -> PlanRow | None:
    """The live plan with its subject's name (``subjects``: an unexecuted
    ``(id, name)`` select), or ``None`` (unknown or deleted). One
    statement, no lock."""
    joined = subjects.subquery()
    row = (
        await session.execute(
            select(LearningPlan, joined.c.name)
            .outerjoin(joined, joined.c.id == LearningPlan.subject_id)
            .where(LearningPlan.id == plan_id, LearningPlan.deleted_at.is_(None))
        )
    ).first()
    if row is None:
        return None
    plan, name = row
    return PlanRow(plan, "" if name is None else str(name))


async def get_plan_for_update(
    session: AsyncSession, plan_id: uuid.UUID
) -> LearningPlan | None:
    """The live plan ``FOR UPDATE``, re-read, or ``None`` when it is gone or
    was deleted meanwhile."""
    return cast(
        LearningPlan | None,
        await session.scalar(
            select(LearningPlan)
            .where(LearningPlan.id == plan_id, LearningPlan.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


async def get_plan_for_share(
    session: AsyncSession, plan_id: uuid.UUID
) -> LearningPlan | None:
    """The live plan ``FOR SHARE``, re-read (D2: a concurrent content write
    can't delete the milestone meanwhile), or ``None``."""
    return cast(
        LearningPlan | None,
        await session.scalar(
            select(LearningPlan)
            .where(LearningPlan.id == plan_id, LearningPlan.deleted_at.is_(None))
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ),
    )


async def plan_items(session: AsyncSession, plan_id: uuid.UUID) -> list[ObjectiveRow]:
    """The plan's objectives in position order, each with its milestones in
    position order. One statement whatever their number."""
    rows = await session.execute(
        select(LearningPlanObjective, LearningPlanMilestone)
        .outerjoin(
            LearningPlanMilestone,
            LearningPlanMilestone.objective_id == LearningPlanObjective.id,
        )
        .where(LearningPlanObjective.plan_id == plan_id)
        .order_by(LearningPlanObjective.position, LearningPlanMilestone.position)
        .execution_options(populate_existing=True)
    )
    found: dict[uuid.UUID, ObjectiveRow] = {}
    for objective, milestone in rows:
        item = found.setdefault(objective.id, ObjectiveRow(objective, []))
        if milestone is not None:
            item.milestones.append(milestone)
    return list(found.values())


async def milestone_facts(
    session: AsyncSession, plan_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[MilestoneFact]]:
    """The milestones of these plans (status, due date, objective), for the
    list summaries. One statement (none for no ids)."""
    found: dict[uuid.UUID, list[MilestoneFact]] = {i: [] for i in plan_ids}
    if not plan_ids:
        return found
    rows = await session.execute(
        select(
            LearningPlanMilestone.plan_id,
            LearningPlanMilestone.objective_id,
            LearningPlanMilestone.status,
            LearningPlanMilestone.due_on,
        ).where(LearningPlanMilestone.plan_id.in_(set(plan_ids)))
    )
    for plan_id, objective_id, status, due_on in rows:
        found[plan_id].append(MilestoneFact(plan_id, objective_id, status, due_on))
    return found


_STATUS_ORDER = case(
    (LearningPlan.status == ACTIVE, 0), (LearningPlan.status == DRAFT, 1), else_=2
)


async def list_plans(
    session: AsyncSession,
    student_id: uuid.UUID,
    *,
    subjects: Select[*tuple[Any, ...]],
    created_by_user_id: uuid.UUID | None,
    family_only: bool,
    statuses: Collection[str],
    subject_id: uuid.UUID | None,
    limit: int,
) -> list[PlanRow]:
    """E1, E2: the student's live plans (only one creator's when
    ``created_by_user_id`` is given; only :func:`family_visible_condition`
    rows when ``family_only``), ``active`` first, then ``draft``, then
    closed; within each ``starts_on`` descending, then ``id``; at most
    ``limit``, each with its subject's name. One statement. ``deleted_at
    IS NULL`` is always applied, and ``family_only`` is applied here, so a
    status filter never widens a family's rows."""
    joined = subjects.subquery()
    query = (
        select(LearningPlan, joined.c.name)
        .outerjoin(joined, joined.c.id == LearningPlan.subject_id)
        .where(LearningPlan.student_id == student_id, LearningPlan.deleted_at.is_(None))
    )
    if family_only:
        query = query.where(family_visible_condition())
    if created_by_user_id is not None:
        query = query.where(LearningPlan.created_by_user_id == created_by_user_id)
    if statuses:
        query = query.where(LearningPlan.status.in_(sorted(set(statuses))))
    if subject_id is not None:
        query = query.where(LearningPlan.subject_id == subject_id)
    result = await session.execute(
        query.order_by(
            _STATUS_ORDER, LearningPlan.starts_on.desc(), LearningPlan.id
        ).limit(limit)
    )
    return [PlanRow(plan, "" if name is None else str(name)) for plan, name in result]


async def has_own_plan(
    session: AsyncSession, student_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Whether the user created a live plan of the student (a former
    creator's access, E1). One statement."""
    found = await session.scalar(
        select(LearningPlan.id)
        .where(
            LearningPlan.student_id == student_id,
            LearningPlan.created_by_user_id == user_id,
            LearningPlan.deleted_at.is_(None),
        )
        .limit(1)
    )
    return found is not None


async def milestone_plan_id(
    session: AsyncSession, milestone_id: uuid.UUID
) -> uuid.UUID | None:
    """The plan of a milestone, or ``None`` (unknown). One statement."""
    return cast(
        uuid.UUID | None,
        await session.scalar(
            select(LearningPlanMilestone.plan_id).where(
                LearningPlanMilestone.id == milestone_id
            )
        ),
    )


async def get_milestone_for_update(
    session: AsyncSession, milestone_id: uuid.UUID, plan_id: uuid.UUID
) -> LearningPlanMilestone | None:
    """The milestone ``FOR UPDATE``, re-read, or ``None`` when a content
    write deleted it meanwhile."""
    return cast(
        LearningPlanMilestone | None,
        await session.scalar(
            select(LearningPlanMilestone)
            .where(
                LearningPlanMilestone.id == milestone_id,
                LearningPlanMilestone.plan_id == plan_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


async def other_active_plan_exists(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    subject_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> bool:
    """D1: another live ``active`` plan of the student in the subject. One
    statement (the partial unique index backs a race)."""
    found = await session.scalar(
        select(LearningPlan.id)
        .where(
            LearningPlan.student_id == student_id,
            LearningPlan.subject_id == subject_id,
            LearningPlan.status == ACTIVE,
            LearningPlan.deleted_at.is_(None),
            LearningPlan.id != plan_id,
        )
        .limit(1)
    )
    return found is not None


# --- Writes ---------------------------------------------------------------------------


def _milestone(
    plan_id: uuid.UUID, objective_id: uuid.UUID, position: int, values: MilestoneValues
) -> LearningPlanMilestone:
    return LearningPlanMilestone(
        id=uuid.uuid7(),
        plan_id=plan_id,
        objective_id=objective_id,
        position=position,
        title=values.title,
        activities=values.activities,
        due_on=values.due_on,
        status=PENDING,
    )


async def create_plan(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    subject_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    title: str,
    description: str | None,
    starts_on: date,
    target_on: date | None,
    objectives: Sequence[ObjectiveValues],
) -> LearningPlan:
    """C3 step 7: a ``draft`` with its objectives and milestones at
    positions 1…n (every milestone ``pending``); the automatic audit events
    record the create."""
    plan = LearningPlan(
        id=uuid.uuid7(),
        student_id=student_id,
        subject_id=subject_id,
        created_by_user_id=created_by_user_id,
        title=title,
        description=description,
        starts_on=starts_on,
        target_on=target_on,
        status=DRAFT,
    )
    session.add(plan)
    await session.flush()
    rows: list[LearningPlanObjective] = []
    milestones: list[LearningPlanMilestone] = []
    for index, values in enumerate(objectives, start=1):
        objective = LearningPlanObjective(
            id=uuid.uuid7(),
            plan_id=plan.id,
            position=index,
            title=values.title,
            description=values.description,
        )
        rows.append(objective)
        milestones += [
            _milestone(plan.id, objective.id, position, item)
            for position, item in enumerate(values.milestones, start=1)
        ]
    session.add_all(rows)
    await session.flush()
    session.add_all(milestones)
    await session.flush()
    return plan


def _changed(row: Any, wanted: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in wanted.items() if getattr(row, key) != value}


async def replace_plan_content(
    session: AsyncSession,
    plan: LearningPlan,
    stored: Sequence[ObjectiveRow],
    *,
    title: str,
    description: str | None,
    starts_on: date,
    target_on: date | None,
    objectives: Sequence[ObjectiveValues],
) -> bool:
    """C4 step 9 on the locked plan (``stored``: its current items; the
    caller checked every ``id`` belongs to this plan, and each milestone
    ``id`` to that objective). Items with an ``id`` are updated in place
    (a milestone's progress is kept), items without one are inserted
    (``pending``), stored items missing from the body are deleted (with
    the milestones of a deleted objective). The plan's version goes up
    exactly once. ``False`` when nothing changes (no write, no version
    bump)."""
    plan_changes = _changed(
        plan,
        {
            "title": title,
            "description": description,
            "starts_on": starts_on,
            "target_on": target_on,
        },
    )
    stored_objectives = {row.objective.id: row.objective for row in stored}
    stored_milestones = {m.id: m for row in stored for m in row.milestones}
    kept_objectives: set[uuid.UUID] = set()
    kept_milestones: set[uuid.UUID] = set()
    objective_updates: list[tuple[LearningPlanObjective, dict[str, Any]]] = []
    objective_inserts: list[LearningPlanObjective] = []
    milestone_updates: list[tuple[LearningPlanMilestone, dict[str, Any]]] = []
    milestone_inserts: list[LearningPlanMilestone] = []
    for position, values in enumerate(objectives, start=1):
        wanted = {
            "position": position,
            "title": values.title,
            "description": values.description,
        }
        if values.id is None:
            objective = LearningPlanObjective(
                id=uuid.uuid7(), plan_id=plan.id, **wanted
            )
            objective_inserts.append(objective)
        else:
            objective = stored_objectives[values.id]
            kept_objectives.add(objective.id)
            change = _changed(objective, wanted)
            if change:
                objective_updates.append((objective, change))
        for index, item in enumerate(values.milestones, start=1):
            if item.id is None:
                milestone_inserts.append(_milestone(plan.id, objective.id, index, item))
                continue
            milestone = stored_milestones[item.id]
            kept_milestones.add(milestone.id)
            change = _changed(
                milestone,
                {
                    "position": index,
                    "title": item.title,
                    "activities": item.activities,
                    "due_on": item.due_on,
                },
            )
            if change:
                milestone_updates.append((milestone, change))
    removed_milestones = [
        m for m in stored_milestones.values() if m.id not in kept_milestones
    ]
    removed_objectives = [
        o for o in stored_objectives.values() if o.id not in kept_objectives
    ]
    if not (
        plan_changes
        or objective_updates
        or objective_inserts
        or milestone_updates
        or milestone_inserts
        or removed_milestones
        or removed_objectives
    ):
        return False
    if plan_changes:
        for key, value in plan_changes.items():
            setattr(plan, key, value)
    else:
        plan.updated_at = func.now()
    await session.flush()
    await session.refresh(plan, ["updated_at"])
    # Children first, parents last for deletes; parents first for inserts.
    for milestone in removed_milestones:
        await session.delete(milestone)
    await session.flush()
    for objective in removed_objectives:
        await session.delete(objective)
    await session.flush()
    for objective, change in objective_updates:
        for key, value in change.items():
            setattr(objective, key, value)
    session.add_all(objective_inserts)
    await session.flush()
    for milestone, change in milestone_updates:
        for key, value in change.items():
            setattr(milestone, key, value)
    session.add_all(milestone_inserts)
    await session.flush()
    return True


async def transition_plan(
    session: AsyncSession,
    plan: LearningPlan,
    action: TransitionAction,
    *,
    now: datetime,
) -> None:
    """D1 on the locked plan (the caller checked the transition): ``activate``
    sets ``active`` and ``activated_at``; ``complete`` / ``discontinue``
    close it (``closed_at``). A concurrent activation of another plan of the
    student in the subject surfaces as ``409 active_plan_exists`` (the
    partial unique index), never a ``500``."""
    if action == "activate":
        plan.status = ACTIVE
        plan.activated_at = now
    else:
        plan.status = COMPLETED if action == "complete" else DISCONTINUED
        plan.closed_at = now
    try:
        await session.flush()
    except IntegrityError as exc:
        if ACTIVE_PLAN_UNIQUE in str(exc.orig):
            raise ActivePlanExistsError() from None
        raise


async def set_milestone_status(
    session: AsyncSession,
    milestone: LearningPlanMilestone,
    status: str,
    *,
    actor_id: uuid.UUID,
    now: datetime,
) -> bool:
    """D2 on the locked milestone: ``done`` stores when and by whom; any
    other status clears them. ``False`` (no write) for the same status."""
    if milestone.status == status:
        return False
    milestone.status = status
    if status == DONE:
        milestone.completed_at = now
        milestone.completed_by_user_id = actor_id
    else:
        milestone.completed_at = None
        milestone.completed_by_user_id = None
    await session.flush()
    return True


async def soft_delete_plan(
    session: AsyncSession, plan: LearningPlan, *, actor_id: uuid.UUID, now: datetime
) -> None:
    """D3: soft delete of a draft (the CHECK backs it up)."""
    plan.deleted_at = now
    plan.deleted_by_user_id = actor_id
    await session.flush()
