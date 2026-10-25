"""Learning plans (#81 M5-14; spec §2, §4 "structured learning plans with
objectives, milestones, due dates, activities and progress visible to
students/guardians", §9, §16, W5.3, W5.4; architecture §4.1, §4.2).

Staff with ``learning_plan.manage`` and the tutors who currently teach a
student in a subject build that student's learning plan in the subject:
ordered objectives, ordered milestones with activities and due dates, and
per-milestone progress. A plan starts as a **draft** (never returned to a
family); **activating** it shows it to the student and to the guardians
with academic access (the #67 rule), who are notified, as they are when
it is **completed**. ``tests/integration/test_session_notes_leak.py`` (the
shared family leak walk) proves no ``GET`` route carries a draft, a deleted
plan or a never-activated discontinued plan to a family.

The routes are in :mod:`app.workflows.learning_plans_router`; the bodies and
views in :mod:`app.workflows.learning_plan_schemas`.

**Who** (E4; the pure rules in ``academics.learning_plan_rules``):

- ``learning_plan.manage`` holders read every plan and write any plan;
- a ``tutor``-role user who currently teaches the student
  (``scheduling.service.subject_ids_for_tutor_student``: not empty) reads
  every plan of the student and writes those in a subject they teach the
  student (``403 plan_subject_not_assigned`` otherwise);
- a tutor who no longer teaches the student reads the plans they created,
  read-only (``403 plan_read_only``);
- the student and the linked guardians with academic access
  (``scheduling.service.ensure_family_student_access``: ``403
  student_consent_required`` for a linked guardian of an adult without the
  consent) read the family-visible plans (E3) through the same routes;
- anyone else, and an unknown id, the ``404`` of an unknown id.

**Orders:** create C3 (``404`` → ``422 subject_unknown`` → ``403`` →
``422`` content → the draft); content C4 and delete D3 (``404`` → ``403``
→ the plan ``FOR UPDATE`` → ``409 version_conflict`` → ``409`` state →
``422`` content → write); transitions D1 (… → ``409`` transition → ``409
active_plan_exists``); progress D2 (``404`` → ``403`` → the plan ``FOR
SHARE`` → the milestone ``FOR UPDATE`` → ``409 version_conflict`` →
``409`` state → write).
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.workflows.learning_plan_schemas import (
    LearningPlanCreate,
    LearningPlanList,
    LearningPlanMilestoneInput,
    LearningPlanMilestoneProgress,
    LearningPlanMilestoneReplace,
    LearningPlanMilestoneView,
    LearningPlanObjectiveInput,
    LearningPlanObjectiveView,
    LearningPlanPerson,
    LearningPlanProgress,
    LearningPlanReplace,
    LearningPlanSubject,
    LearningPlanSummary,
    LearningPlanTransition,
    LearningPlanView,
    MilestoneStatus,
    PlanStatus,
)

rules = academics.learning_plan_rules
Relation = rules.PlanRelation

ACTIVATED_NOTIFICATION: Final = "academics.learning_plan_activated"
COMPLETED_NOTIFICATION: Final = "academics.learning_plan_completed"


class LearningPlanNotificationParams(BaseModel):
    """``academics.learning_plan_activated`` / ``_completed`` (E6): ids
    only, never a title, description, activities text or tutor name."""

    student_id: str
    plan_id: str
    subject_id: str


def register_learning_plan_notifications() -> None:
    """Register both types (again: a no-op); ``app.wiring`` calls it."""
    administration.register_notification_type(
        ACTIVATED_NOTIFICATION, LearningPlanNotificationParams
    )
    administration.register_notification_type(
        COMPLETED_NOTIFICATION, LearningPlanNotificationParams
    )


type FieldIssue = tuple[list[str | int], str, str]


def _issue(path: Sequence[str | int], code: str, message: str) -> FieldIssue:
    return (list(path), code, message)


# --- Access ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Caller:
    """The caller's facts about one student: ``subjects`` are the subjects
    in which they currently teach the student (empty without the ``tutor``
    role, or when the student isn't one of their current assignments)."""

    principal: Principal
    subjects: frozenset[uuid.UUID]

    @property
    def manager(self) -> bool:
        return self.principal.has(rules.LEARNING_PLAN_MANAGE)

    @property
    def tutor(self) -> bool:
        return rules.TUTOR_ROLE in self.principal.roles

    @property
    def assigned(self) -> bool:
        return self.tutor and bool(self.subjects)

    def relation(self, plan: academics.LearningPlan, *, family: bool) -> Relation:
        return rules.plan_relation(
            manager=self.manager,
            tutor=self.tutor,
            student_assigned=self.assigned,
            subject_assigned=plan.subject_id in self.subjects,
            is_creator=plan.created_by_user_id == self.principal.user_id,
            family=family,
        )


async def _caller(
    session: AsyncSession, principal: Principal, student_id: uuid.UUID
) -> _Caller:
    """One statement for a tutor-role user who doesn't hold
    ``learning_plan.manage``; none otherwise."""
    subjects: frozenset[uuid.UUID] = frozenset()
    if rules.TUTOR_ROLE in principal.roles and not principal.has(
        rules.LEARNING_PLAN_MANAGE
    ):
        subjects = await scheduling.subject_ids_for_tutor_student(
            session, principal.user_id, student_id, now=clock.now()
        )
    return _Caller(principal, subjects)


async def _plan_relation(
    session: AsyncSession, caller: _Caller, plan: academics.LearningPlan
) -> Relation:
    """The caller's relation to a live plan. The family rule is checked
    only when no staff relation applies (``403 student_consent_required``
    for a linked guardian of an adult without the consent)."""
    relation = caller.relation(plan, family=False)
    if relation is not Relation.NONE:
        return relation
    try:
        await scheduling.ensure_family_student_access(
            session, caller.principal, plan.student_id
        )
    except RecordNotFoundError:
        return Relation.NONE
    return Relation.FAMILY


def _readable(relation: Relation, plan: academics.LearningPlan) -> bool:
    return rules.can_read(
        relation,
        status=plan.status,
        activated_at=plan.activated_at,
        deleted=plan.deleted_at is not None,
    )


async def _readable_plan(
    session: AsyncSession,
    principal: Principal,
    plan_id: uuid.UUID,
    *,
    write: bool = False,
) -> tuple[_Caller, Relation, academics.PlanRow]:
    """The ``404`` step of every route on one plan (E1). On a ``write`` the
    family rule isn't checked: a family member never writes, so they get
    the ``404`` of an unknown id, never ``403 student_consent_required``
    (the matrix; #81 QA D1)."""
    row = await academics.plan_with_subject(
        session, plan_id, subjects=catalog.subject_name_columns()
    )
    if row is None:
        raise RecordNotFoundError()
    caller = await _caller(session, principal, row.plan.student_id)
    relation = (
        caller.relation(row.plan, family=False)
        if write
        else await _plan_relation(session, caller, row.plan)
    )
    if not _readable(relation, row.plan):
        raise RecordNotFoundError()
    return caller, relation, row


_ACCESS_ERRORS: Final = {
    rules.NOT_FOUND: RecordNotFoundError,
    rules.PLAN_SUBJECT_NOT_ASSIGNED: academics.PlanSubjectNotAssignedError,
    rules.PLAN_READ_ONLY: academics.PlanReadOnlyError,
}
_STATE_ERRORS: Final = {
    rules.PLAN_CLOSED: academics.PlanClosedError,
    rules.PLAN_NOT_ACTIVE: academics.PlanNotActiveError,
    rules.PLAN_NOT_DRAFT: academics.PlanNotDraftError,
}


def _ensure_access(relation: Relation) -> None:
    blocked = rules.access_block(relation)
    if blocked is not None:
        raise _ACCESS_ERRORS[blocked]()


def _ensure_state(status: str, action: rules.PlanAction) -> None:
    blocked = rules.state_block(status, action)
    if blocked is not None:
        raise _STATE_ERRORS[blocked]()


async def _locked(
    session: AsyncSession,
    principal: Principal,
    plan_id: uuid.UUID,
    action: rules.PlanAction,
    version: int,
) -> tuple[_Caller, Relation, academics.LearningPlan, str]:
    """C4 / D1 / D3 steps 3-7: readable, allowed, the plan ``FOR UPDATE``,
    the version, the state. Returns the subject's name too."""
    caller, relation, row = await _readable_plan(
        session, principal, plan_id, write=True
    )
    _ensure_access(relation)
    plan = await academics.get_plan_for_update(session, plan_id)
    if plan is None:
        raise RecordNotFoundError()
    ensure_version(plan, version)
    _ensure_state(plan.status, action)
    return caller, relation, plan, row.subject_name


# --- Views ----------------------------------------------------------------------------


def _progress(statuses: Iterable[str]) -> LearningPlanProgress:
    done, total, percent = rules.progress(statuses)
    return LearningPlanProgress(done=done, total=total, percent=percent)


def _person(people: Mapping[uuid.UUID, Any], user_id: uuid.UUID) -> LearningPlanPerson:
    account = people.get(user_id)
    return LearningPlanPerson(
        display_name="" if account is None else account.display_name
    )


def _summary_fields(
    plan: academics.LearningPlan,
    subject_name: str,
    people: Mapping[uuid.UUID, Any],
    facts: Sequence[tuple[uuid.UUID, str, date | None]],
    today: date,
) -> dict[str, Any]:
    """F2 from the plan and its milestones' ``(objective id, status,
    due_on)``."""
    by_objective: dict[uuid.UUID, list[str]] = defaultdict(list)
    for objective_id, status, _ in facts:
        by_objective[objective_id].append(status)
    return {
        "id": plan.id,
        "version": plan.version,
        "student_id": plan.student_id,
        "subject": LearningPlanSubject(id=plan.subject_id, name=subject_name),
        "title": plan.title,
        "status": cast(PlanStatus, plan.status),
        "starts_on": plan.starts_on,
        "target_on": plan.target_on,
        "activated_at": plan.activated_at,
        "closed_at": plan.closed_at,
        "created_by": _person(people, plan.created_by_user_id),
        "progress": _progress(status for _, status, _ in facts),
        "objectives_complete": sum(
            1
            for statuses in by_objective.values()
            if all(s == rules.DONE for s in statuses)
        ),
        "objectives_total": len(by_objective),
        "overdue_milestones": sum(
            1 for _, status, due_on in facts if rules.is_overdue(status, due_on, today)
        ),
        "updated_at": plan.updated_at,
    }


def _can(
    relation: Relation, plan: academics.LearningPlan, action: rules.PlanAction
) -> bool:
    return (
        rules.write_block(
            relation,
            status=plan.status,
            deleted=plan.deleted_at is not None,
            action=action,
        )
        is None
    )


async def _view(
    session: AsyncSession,
    relation: Relation,
    plan: academics.LearningPlan,
    subject_name: str,
) -> LearningPlanView:
    """F3: the items, the people and the center's today in at most three
    statements whatever the number of objectives and milestones."""
    items = await academics.plan_items(session, plan.id)
    people = await identity.get_account_references(
        session,
        {plan.created_by_user_id}
        | {
            m.completed_by_user_id
            for item in items
            for m in item.milestones
            if m.completed_by_user_id is not None
        },
    )
    today = await administration.center_today(session)
    facts = [
        (item.objective.id, m.status, m.due_on)
        for item in items
        for m in item.milestones
    ]
    objectives = []
    for item in items:
        statuses = [m.status for m in item.milestones]
        objectives.append(
            LearningPlanObjectiveView(
                id=item.objective.id,
                position=item.objective.position,
                title=item.objective.title,
                description=item.objective.description,
                complete=bool(statuses) and all(s == rules.DONE for s in statuses),
                progress=_progress(statuses),
                milestones=[
                    LearningPlanMilestoneView(
                        id=m.id,
                        version=m.version,
                        position=m.position,
                        title=m.title,
                        activities=m.activities,
                        due_on=m.due_on,
                        status=cast(MilestoneStatus, m.status),
                        overdue=rules.is_overdue(m.status, m.due_on, today),
                        completed_at=m.completed_at,
                        completed_by=(
                            None
                            if m.completed_by_user_id is None
                            else _person(people, m.completed_by_user_id)
                        ),
                    )
                    for m in item.milestones
                ],
            )
        )
    return LearningPlanView(
        **_summary_fields(plan, subject_name, people, facts, today),
        description=plan.description,
        objectives=objectives,
        can_edit=_can(relation, plan, "edit"),
        can_activate=_can(relation, plan, "activate"),
        can_complete=_can(relation, plan, "complete"),
        can_discontinue=_can(relation, plan, "discontinue"),
        can_delete=_can(relation, plan, "delete"),
        can_update_progress=_can(relation, plan, "progress"),
    )


# --- C2: content ----------------------------------------------------------------------


def _text_issues(
    path: list[str | int],
    value: str | None,
    maximum: int,
    *,
    required: bool,
) -> list[FieldIssue]:
    if value is None:
        if required:
            return [_issue(path, "title_required", "This field is required.")]
        return []
    if len(value) > maximum:
        return [_issue(path, "too_long", f"At most {maximum} characters.")]
    return []


def _content_issues(
    body: LearningPlanCreate | LearningPlanReplace, starts_on: date
) -> list[FieldIssue]:
    """C2 without stored data: the texts, the dates and the limits."""
    issues: list[FieldIssue] = [
        *_text_issues(["body", "title"], body.title, rules.TITLE_MAX, required=True),
        *_text_issues(
            ["body", "description"],
            body.description,
            rules.DESCRIPTION_MAX,
            required=False,
        ),
    ]
    if body.target_on is not None and body.target_on < starts_on:
        issues.append(
            _issue(
                ["body", "target_on"],
                "target_before_start",
                "The target date is before the start date.",
            )
        )
    objectives: Sequence[LearningPlanObjectiveInput] = body.objectives
    if not objectives:
        issues.append(
            _issue(["body", "objectives"], "objectives_required", "Add an objective.")
        )
    elif len(objectives) > rules.OBJECTIVES_MAX:
        issues.append(
            _issue(
                ["body", "objectives"],
                "objectives_too_many",
                f"At most {rules.OBJECTIVES_MAX} objectives.",
            )
        )
    total = 0
    for index, objective in enumerate(objectives):
        path: list[str | int] = ["body", "objectives", index]
        issues += _text_issues(
            [*path, "title"], objective.title, rules.ITEM_TITLE_MAX, required=True
        )
        issues += _text_issues(
            [*path, "description"],
            objective.description,
            rules.OBJECTIVE_DESCRIPTION_MAX,
            required=False,
        )
        milestones: Sequence[LearningPlanMilestoneInput] = objective.milestones
        total += len(milestones)
        if not milestones:
            issues.append(
                _issue([*path, "milestones"], "milestones_required", "Add a milestone.")
            )
        elif len(milestones) > rules.MILESTONES_PER_OBJECTIVE_MAX:
            issues.append(
                _issue(
                    [*path, "milestones"],
                    "milestones_too_many",
                    f"At most {rules.MILESTONES_PER_OBJECTIVE_MAX} milestones"
                    " per objective.",
                )
            )
        for position, milestone in enumerate(milestones):
            where: list[str | int] = [*path, "milestones", position]
            issues += _text_issues(
                [*where, "title"],
                milestone.title,
                rules.ITEM_TITLE_MAX,
                required=True,
            )
            issues += _text_issues(
                [*where, "activities"],
                milestone.activities,
                rules.ACTIVITIES_MAX,
                required=False,
            )
            if milestone.due_on is not None and milestone.due_on < rules.EARLIEST_DATE:
                issues.append(
                    _issue(
                        [*where, "due_on"],
                        "due_on_invalid",
                        "The date is before 2000-01-01.",
                    )
                )
    if total > rules.MILESTONES_PER_PLAN_MAX:
        issues.append(
            _issue(
                ["body", "objectives"],
                "milestones_too_many",
                f"At most {rules.MILESTONES_PER_PLAN_MAX} milestones in a plan.",
            )
        )
    return issues


def _id_issues(
    body: LearningPlanReplace, stored: Sequence[academics.ObjectiveRow]
) -> list[FieldIssue]:
    """C2 against the stored plan: every ``id`` is one of this plan's, used
    once, and a milestone stays under its objective (moves aren't
    allowed)."""
    objective_ids = {row.objective.id for row in stored}
    milestone_parent = {
        m.id: row.objective.id for row in stored for m in row.milestones
    }
    issues: list[FieldIssue] = []
    seen_objectives: set[uuid.UUID] = set()
    seen_milestones: set[uuid.UUID] = set()
    for index, objective in enumerate(body.objectives):
        path: list[str | int] = ["body", "objectives", index]
        objective_id = objective.id
        if objective_id is not None:
            if objective_id not in objective_ids or objective_id in seen_objectives:
                issues.append(
                    _issue(
                        [*path, "id"],
                        "objective_unknown",
                        "This plan has no such objective.",
                    )
                )
                objective_id = None
            else:
                seen_objectives.add(objective_id)
        milestones: Sequence[LearningPlanMilestoneReplace] = objective.milestones
        for position, milestone in enumerate(milestones):
            if milestone.id is None:
                continue
            if (
                milestone.id in seen_milestones
                or objective_id is None
                or milestone_parent.get(milestone.id) != objective_id
            ):
                issues.append(
                    _issue(
                        [*path, "milestones", position, "id"],
                        "milestone_unknown",
                        "This objective has no such milestone.",
                    )
                )
            seen_milestones.add(milestone.id)
    return issues


def _values(
    objectives: Sequence[LearningPlanObjectiveInput],
) -> list[academics.ObjectiveValues]:
    """The checked content (every title present) as plain values."""
    found = []
    for objective in objectives:
        assert objective.title is not None  # noqa: S101 (checked: title_required)
        milestones = []
        for milestone in objective.milestones:
            assert milestone.title is not None  # noqa: S101 (checked)
            milestones.append(
                academics.MilestoneValues(
                    id=getattr(milestone, "id", None),
                    title=milestone.title,
                    activities=milestone.activities,
                    due_on=milestone.due_on,
                )
            )
        found.append(
            academics.ObjectiveValues(
                id=getattr(objective, "id", None),
                title=objective.title,
                description=objective.description,
                milestones=milestones,
            )
        )
    return found


async def _active_subject(session: AsyncSession, subject_id: uuid.UUID) -> Any | None:
    """The subject when it and its level are active (``FOR SHARE``), else
    ``None``."""
    subject = await catalog.get_subject_for_reference(session, subject_id, lock=True)
    if (
        subject is None
        or not subject.is_active
        or not subject.education_level_is_active
    ):
        return None
    return subject


# --- C: create and replace ------------------------------------------------------------


async def create_plan(
    session: AsyncSession,
    *,
    principal: Principal,
    student_id: uuid.UUID,
    body: LearningPlanCreate,
) -> LearningPlanView:
    """``POST /api/v1/students/{id}/learning-plans`` (C3): ``404`` (an
    unknown student, or a caller who is neither a holder nor a tutor
    currently teaching the student) → ``422 subject_unknown`` → ``403
    plan_subject_not_assigned`` (tutors) → ``422`` content → the draft.
    Nobody is notified."""
    if not await families.student_references(session, [student_id]):
        raise RecordNotFoundError()
    caller = await _caller(session, principal, student_id)
    if not caller.manager and not caller.assigned:
        raise RecordNotFoundError()
    subject = await _active_subject(session, body.subject_id)
    if subject is None:
        raise academics.LearningPlanFieldError(
            [(["body", "subject_id"], "subject_unknown", "Choose an active subject.")]
        )
    if not caller.manager and body.subject_id not in caller.subjects:
        raise academics.PlanSubjectNotAssignedError()
    starts_on = body.starts_on or await administration.center_today(session)
    issues = _content_issues(body, starts_on)
    if issues:
        raise academics.LearningPlanFieldError(issues)
    assert body.title is not None  # noqa: S101 (checked: title_required)
    plan = await academics.create_plan(
        session,
        student_id=student_id,
        subject_id=body.subject_id,
        created_by_user_id=principal.user_id,
        title=body.title,
        description=body.description,
        starts_on=starts_on,
        target_on=body.target_on,
        objectives=_values(body.objectives),
    )
    relation = caller.relation(plan, family=False)
    return await _view(session, relation, plan, subject.name)


async def replace_plan(
    session: AsyncSession,
    *,
    principal: Principal,
    plan_id: uuid.UUID,
    body: LearningPlanReplace,
) -> LearningPlanView:
    """``PUT /api/v1/learning-plans/{id}`` (C4): objectives and milestones
    with an ``id`` are updated in place (progress kept), new ones inserted,
    missing ones deleted; the version goes up once. A body that changes
    nothing writes nothing."""
    _, relation, plan, subject_name = await _locked(
        session, principal, plan_id, "edit", body.version
    )
    stored = await academics.plan_items(session, plan.id)
    issues = [*_content_issues(body, body.starts_on), *_id_issues(body, stored)]
    if issues:
        raise academics.LearningPlanFieldError(issues)
    assert body.title is not None  # noqa: S101 (checked: title_required)
    await academics.replace_plan_content(
        session,
        plan,
        stored,
        title=body.title,
        description=body.description,
        starts_on=body.starts_on,
        target_on=body.target_on,
        objectives=_values(body.objectives),
    )
    return await _view(session, relation, plan, subject_name)


# --- D: transitions, progress, delete -------------------------------------------------


async def transition_plan(
    session: AsyncSession,
    *,
    principal: Principal,
    plan_id: uuid.UUID,
    body: LearningPlanTransition,
) -> LearningPlanView:
    """``POST /api/v1/learning-plans/{id}/transition`` (D1). ``activate``
    and ``complete`` notify the student and the guardians with academic
    access (ids only); ``discontinue`` notifies nobody."""
    _, relation, plan, subject_name = await _locked(
        session, principal, plan_id, body.action, body.version
    )
    if body.action == "activate" and await academics.other_active_plan_exists(
        session, student_id=plan.student_id, subject_id=plan.subject_id, plan_id=plan.id
    ):
        raise academics.ActivePlanExistsError()
    await academics.transition_plan(session, plan, body.action, now=clock.now())
    if body.action in ("activate", "complete"):
        recipients = await families.academic_notification_recipients(
            session, plan.student_id, today=await administration.center_today(session)
        )
        params = LearningPlanNotificationParams(
            student_id=str(plan.student_id),
            plan_id=str(plan.id),
            subject_id=str(plan.subject_id),
        )
        notification = (
            ACTIVATED_NOTIFICATION
            if body.action == "activate"
            else COMPLETED_NOTIFICATION
        )
        for user_id in recipients:
            await administration.create_notification(
                session,
                recipient_user_id=user_id,
                notification_type=notification,
                params=params,
            )
    return await _view(session, relation, plan, subject_name)


async def set_milestone_progress(
    session: AsyncSession,
    *,
    principal: Principal,
    milestone_id: uuid.UUID,
    body: LearningPlanMilestoneProgress,
) -> LearningPlanView:
    """``PATCH /api/v1/learning-plan-milestones/{id}`` (D2): the whole plan
    comes back so the totals refresh. The same status writes nothing."""
    plan_id = await academics.milestone_plan_id(session, milestone_id)
    if plan_id is None:
        raise RecordNotFoundError()
    _, relation, row = await _readable_plan(session, principal, plan_id, write=True)
    _ensure_access(relation)
    plan = await academics.get_plan_for_share(session, plan_id)
    if plan is None:
        raise RecordNotFoundError()
    milestone = await academics.get_milestone_for_update(session, milestone_id, plan_id)
    if milestone is None:
        raise RecordNotFoundError()
    ensure_version(milestone, body.version)
    _ensure_state(plan.status, "progress")
    await academics.set_milestone_status(
        session,
        milestone,
        body.status,
        actor_id=principal.user_id,
        now=clock.now(),
    )
    return await _view(session, relation, plan, row.subject_name)


async def delete_plan(
    session: AsyncSession, *, principal: Principal, plan_id: uuid.UUID, version: int
) -> None:
    """``DELETE /api/v1/learning-plans/{id}?version=`` (D3): a draft only;
    every route then answers ``404`` for it."""
    _, _, plan, _ = await _locked(session, principal, plan_id, "delete", version)
    await academics.soft_delete_plan(
        session, plan, actor_id=principal.user_id, now=clock.now()
    )


# --- E: reads -------------------------------------------------------------------------


async def get_plan(
    session: AsyncSession, principal: Principal, plan_id: uuid.UUID
) -> LearningPlanView:
    """``GET /api/v1/learning-plans/{id}`` (E3): at most 6 statements
    whatever the number of objectives and milestones."""
    _, relation, row = await _readable_plan(session, principal, plan_id)
    return await _view(session, relation, row.plan, row.subject_name)


@dataclass(frozen=True)
class _ListScope:
    family_only: bool = False
    created_by_user_id: uuid.UUID | None = None


async def _list_scope(
    session: AsyncSession, caller: _Caller, student_id: uuid.UUID
) -> _ListScope:
    """E1: holders and tutors currently teaching the student read every
    plan; the family the family-visible ones; a former creator their own
    (``404`` when they have none); anyone else ``404``."""
    if caller.manager:
        if not await families.student_references(session, [student_id]):
            raise RecordNotFoundError()
        return _ListScope()
    if caller.assigned:
        return _ListScope()
    try:
        await scheduling.ensure_family_student_access(
            session, caller.principal, student_id
        )
        return _ListScope(family_only=True)
    except RecordNotFoundError:
        if not caller.tutor:
            raise
    if await academics.has_own_plan(session, student_id, caller.principal.user_id):
        return _ListScope(created_by_user_id=caller.principal.user_id)
    raise RecordNotFoundError()


async def _creatable(
    session: AsyncSession, caller: _Caller
) -> tuple[bool, list[LearningPlanSubject] | None]:
    """AM1: holders any active subject (``null``); a tutor currently teaching
    the student the active subjects they teach the student, by name; anyone
    else none. At most one statement."""
    if caller.manager:
        return True, None
    if not caller.assigned:
        return False, []
    found = await catalog.subject_references(session, caller.subjects)
    subjects = sorted(
        (
            LearningPlanSubject(id=ref.id, name=ref.name)
            for ref in found.values()
            if ref.is_active and ref.education_level_is_active
        ),
        key=lambda s: (s.name.casefold(), str(s.id)),
    )
    return bool(subjects), subjects


async def list_student_plans(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    statuses: Sequence[str],
    subject_id: uuid.UUID | None,
) -> LearningPlanList:
    """``GET /api/v1/students/{id}/learning-plans`` (E1, E2, AM1): at most 7
    statements, the same for 1 and 100 plans."""
    caller = await _caller(session, principal, student_id)
    scope = await _list_scope(session, caller, student_id)
    found = await academics.list_plans(
        session,
        student_id,
        subjects=catalog.subject_name_columns(),
        created_by_user_id=scope.created_by_user_id,
        family_only=scope.family_only,
        statuses=statuses,
        subject_id=subject_id,
        limit=rules.LIST_MAX + 1,
    )
    truncated = len(found) > rules.LIST_MAX
    found = found[: rules.LIST_MAX]
    items: list[LearningPlanSummary] = []
    if found:
        facts = await academics.milestone_facts(session, [r.plan.id for r in found])
        people = await identity.get_account_references(
            session, {r.plan.created_by_user_id for r in found}
        )
        today = await administration.center_today(session)
        items = [
            LearningPlanSummary(
                **_summary_fields(
                    r.plan,
                    r.subject_name,
                    people,
                    [(f.objective_id, f.status, f.due_on) for f in facts[r.plan.id]],
                    today,
                )
            )
            for r in found
        ]
    can_create, creatable = await _creatable(session, caller)
    return LearningPlanList(
        items=items,
        truncated=truncated,
        can_create=can_create,
        creatable_subjects=creatable,
    )
