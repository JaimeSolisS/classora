"""``academics`` (#551 HF-01c): the academic record of the term through the
academics workflows, after ``courses`` (so each day's classes are held and
closed out first).

The plan (:mod:`.coursework`) is a list of dated steps; this module runs
each under :func:`~.anchor.sim_clock` at its instant and :func:`seed_actor`
of the account that acts:

- the Academic coordinator (``evaluation.manage``,
  ``progress_report.review``) creates the evaluation templates at setup and
  the report schedules on 4 Sep, and approves (or sends back) the reports;
- each tutor writes and shares notes, records and finalizes evaluations,
  builds and tracks learning plans, sets and grades homework (recording
  work handed in on paper) and writes, fills and submits progress reports;
- students with a login hand in their own homework;
- the progress-report placeholder job's per-schedule step
  (``progress_report_schedule_tasks.place_one``) runs in-process at its
  01:00 tick with no actor (E12), so at :data:`~.anchor.SIM_NOW` the job
  has nothing to create.

Nothing is dated on or after the anchor: the pending items (homework handed
in early, the weekly reports waiting for review) are written on 17 and 18
Nov, so :meth:`AcademicsModule.at_anchor` has nothing to do. Any unexpected
answer fails the run (one Unit of Work, so nothing is written).
"""

import uuid
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.audit import AuditEvent
from app.core.authz import Principal
from app.core.errors import AppError
from app.modules.academics.models import (
    Assignment,
    AssignmentSubmission,
    Evaluation,
    EvaluationScore,
    EvaluationTemplate,
    EvaluationTemplateCriterion,
    LearningPlan,
    LearningPlanMilestone,
    LearningPlanObjective,
    ProgressReport,
    ProgressReportSchedule,
    SessionNote,
)
from app.modules.academics.schemas import SessionNoteCreate
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.courses import service as courses
from app.workflows import assignments, evaluations, learning_plans
from app.workflows import progress_report_schedules as report_schedules
from app.workflows import progress_reports as reports
from app.workflows import session_notes as notes
from app.workflows.assignment_schemas import AssignmentCreate, GradePut, SubmissionPut
from app.workflows.evaluation_schemas import (
    EvaluationCreate,
    EvaluationCriterionInput,
    EvaluationScoreInput,
    EvaluationTemplateCreate,
)
from app.workflows.learning_plan_schemas import (
    LearningPlanCreate,
    LearningPlanMilestoneInput,
    LearningPlanMilestoneProgress,
    LearningPlanMilestoneReplace,
    LearningPlanObjectiveInput,
    LearningPlanObjectiveReplace,
    LearningPlanReplace,
    LearningPlanTransition,
)
from app.workflows.progress_report_schedule_schemas import (
    ProgressReportScheduleCreate,
)
from app.workflows.progress_report_schedule_tasks import due_schedules, place_one
from app.workflows.progress_report_schemas import (
    ProgressReportCreate,
    ProgressReportReason,
    ProgressReportUpdate,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import SIM_START, sim_clock
from tests.factories.simulation.coursework import (
    MAX_SCORE,
    Coursework,
    EvaluationPlan,
    HomeworkPlan,
    ReportPlan,
    Step,
    local_day,
)
from tests.factories.simulation.people import principal
from tests.factories.simulation.schedule import (
    SimulationStepError,
    class_id,
    student_id,
    tutor_id,
    tutor_principal,
)
from tests.factories.simulation.timeline import SimulationContext

ACADEMICS_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    SessionNote,
    EvaluationTemplate,
    EvaluationTemplateCriterion,
    Evaluation,
    EvaluationScore,
    LearningPlan,
    LearningPlanObjective,
    LearningPlanMilestone,
    Assignment,
    AssignmentSubmission,
    ProgressReportSchedule,
    ProgressReport,
    ApprovalRequest,
    Notification,
)
"""The academics tables, and the approval requests and notifications this
module's steps create."""

ANALYZED_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    *ACADEMICS_TABLES,
    AuditEvent,
)
ANALYZE_EVERY: Final = 150
"""Steps between two ``ANALYZE`` of :data:`ANALYZED_TABLES` (as
``schedule``: the run is one transaction, so autovacuum never sees its
rows and the planner would plan the services' reads for empty tables)."""
_steps_since_analyze = [0]

MAX: Final = Decimal(MAX_SCORE)


class AcademicsModule:
    name = "academics"
    tables = ACADEMICS_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (
            await session.scalar(select(EvaluationTemplate.id).limit(1))
        ) is not None

    def _steps(self, ctx: SimulationContext) -> tuple[Step, ...]:
        return ctx.plan.academics.steps

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        _steps_since_analyze[0] = 0  # the same points in every run
        for step in self._steps(ctx):
            if local_day(step.at) < SIM_START:
                await run_step(session, ctx, step)

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return {
            local_day(s.at) for s in self._steps(ctx) if local_day(s.at) >= SIM_START
        }

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        for step in self._steps(ctx):
            if local_day(step.at) == day:
                await run_step(session, ctx, step)
                await _refresh_statistics(session)
        # As ``schedule``: nothing is read back from the identity map across
        # days, so release the day's objects.
        await session.flush()
        session.expunge_all()

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None


async def _refresh_statistics(session: AsyncSession) -> None:
    _steps_since_analyze[0] += 1
    if _steps_since_analyze[0] < ANALYZE_EVERY:
        return
    _steps_since_analyze[0] = 0
    names = ", ".join(f'"{model.__tablename__}"' for model in ANALYZED_TABLES)
    await session.execute(text(f"ANALYZE {names}"))


async def run_step(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    """One planned step under the simulation clock at its instant."""
    handler = HANDLERS[step.action]
    with sim_clock(step.at):
        try:
            await handler(session, ctx, step)
        except AppError as exc:
            raise SimulationStepError(
                f"{step}: {exc.code} {exc.conflicts or exc.errors or exc.details}"
            ) from exc


# --- Lookups --------------------------------------------------------------------------


def _work(ctx: SimulationContext) -> Coursework:
    return ctx.plan.academics


def _by_key[T](items: Iterable[T], key: str) -> T:
    return next(item for item in items if item.key == key)  # type: ignore[attr-defined]


async def _coordinator(session: AsyncSession, ctx: SimulationContext) -> Principal:
    return await principal(session, ctx, ctx.plan.coordinator)


async def _version(session: AsyncSession, model: Any, row_id: uuid.UUID) -> int:
    version = await session.scalar(select(model.version).where(model.id == row_id))
    assert version is not None
    return int(version)


# --- Evaluations ----------------------------------------------------------------------


async def _template(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    template = _by_key(_work(ctx).templates, step.key)
    coordinator = await _coordinator(session, ctx)
    with seed_actor(session, coordinator.user_id):
        created = await evaluations.create_template(
            session,
            body=EvaluationTemplateCreate(
                name=template.name,
                kind=template.kind,
                description=template.description,
                criteria=[
                    EvaluationCriterionInput(title=title, max_score=MAX)
                    for title in template.criteria
                ],
            ),
        )
    ctx.ids[f"template:{template.key}"] = created.id


async def _assessment_id(
    session: AsyncSession, ctx: SimulationContext, evaluation: EvaluationPlan
) -> uuid.UUID | None:
    if evaluation.assessment is None:
        return None
    cohort, index = evaluation.assessment
    found = await courses.cohort_assessments(session, ctx.id(f"cohort:{cohort}"))
    assert found is not None
    return found.assessments[index].id


async def _evaluate(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    e = _by_key(_work(ctx).evaluations, step.key)
    me = await tutor_principal(session, ctx, e.tutor)
    with seed_actor(session, me.user_id):
        created = await evaluations.create_evaluation(
            session,
            principal=me,
            body=EvaluationCreate(
                student_id=student_id(ctx, e.student),
                template_id=ctx.id(f"template:{e.template}"),
                subject_id=ctx.id(f"subject:{e.subject}"),
                class_session_id=await class_id(session, ctx, e.cls),
                course_assessment_id=await _assessment_id(session, ctx, e),
                scores=[
                    EvaluationScoreInput(
                        position=position, score=Decimal(score), comment=comment
                    )
                    for position, (score, comment) in enumerate(
                        zip(e.scores, e.comments, strict=True), start=1
                    )
                ],
                feedback=e.feedback,
            ),
        )
        await evaluations.finalize_evaluation(
            session, principal=me, evaluation_id=created.id, version=created.version
        )


# --- Notes ----------------------------------------------------------------------------


async def _note(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    n = _by_key(_work(ctx).notes, step.key)
    me = await tutor_principal(session, ctx, n.tutor)
    with seed_actor(session, me.user_id):
        created = await notes.create_note(
            session,
            principal=me,
            class_session_id=await class_id(session, ctx, n.cls),
            body=SessionNoteCreate(student_id=student_id(ctx, n.student), body=n.body),
        )
        if n.shared:
            await notes.publish_note(
                session, principal=me, note_id=created.id, version=created.version
            )


# --- Learning plans -------------------------------------------------------------------


async def _plan_create(
    session: AsyncSession, ctx: SimulationContext, step: Step
) -> None:
    p = _work(ctx).plan(step.key)
    me = await tutor_principal(session, ctx, p.tutor)
    with seed_actor(session, me.user_id):
        draft = await learning_plans.create_plan(
            session,
            principal=me,
            student_id=student_id(ctx, p.student),
            body=LearningPlanCreate(
                subject_id=ctx.id(f"subject:{p.subject}"),
                title=p.title,
                description=p.description,
                starts_on=p.starts_on,
                target_on=p.target_on,
                objectives=[
                    LearningPlanObjectiveInput(
                        title=o.title,
                        milestones=[
                            LearningPlanMilestoneInput(
                                title=m.title, activities=m.activities, due_on=m.due_on
                            )
                            for m in o.milestones
                        ],
                    )
                    for o in p.objectives
                ],
            ),
        )
        active = await learning_plans.transition_plan(
            session,
            principal=me,
            plan_id=draft.id,
            body=LearningPlanTransition(version=draft.version, action="activate"),
        )
    ctx.ids[f"plan:{p.key}"] = active.id
    for o, objective in enumerate(active.objectives):
        for m, milestone in enumerate(objective.milestones):
            ctx.ids[f"milestone:{p.key}:{o}:{m}"] = milestone.id


async def _milestone(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    p = _work(ctx).plan(step.key)
    assert step.milestone is not None and step.status is not None
    o, m = step.milestone
    me = await tutor_principal(session, ctx, p.tutor)
    milestone_id = ctx.id(f"milestone:{p.key}:{o}:{m}")
    with seed_actor(session, me.user_id):
        await learning_plans.set_milestone_progress(
            session,
            principal=me,
            milestone_id=milestone_id,
            body=LearningPlanMilestoneProgress(
                version=await _version(session, LearningPlanMilestone, milestone_id),
                status=step.status,
            ),
        )


async def _plan_close(
    session: AsyncSession, ctx: SimulationContext, step: Step
) -> None:
    p = _work(ctx).plan(step.key)
    me = await tutor_principal(session, ctx, p.tutor)
    plan_id = ctx.id(f"plan:{p.key}")
    with seed_actor(session, me.user_id):
        if p.outcome == "discontinued":
            # ``learning_plans`` has no reason column: the tutor adds the
            # reason to the description, then discontinues the plan.
            assert p.reason is not None
            view = await learning_plans.get_plan(session, me, plan_id)
            await learning_plans.replace_plan(
                session,
                principal=me,
                plan_id=plan_id,
                body=LearningPlanReplace(
                    version=view.version,
                    title=view.title,
                    description=f"{p.description} {p.reason}",
                    starts_on=view.starts_on,
                    target_on=view.target_on,
                    objectives=[
                        LearningPlanObjectiveReplace(
                            id=o.id,
                            title=o.title,
                            description=o.description,
                            milestones=[
                                LearningPlanMilestoneReplace(
                                    id=m.id,
                                    title=m.title,
                                    activities=m.activities,
                                    due_on=m.due_on,
                                )
                                for m in o.milestones
                            ],
                        )
                        for o in view.objectives
                    ],
                ),
            )
        await learning_plans.transition_plan(
            session,
            principal=me,
            plan_id=plan_id,
            body=LearningPlanTransition(
                version=await _version(session, LearningPlan, plan_id),
                action="complete" if p.outcome == "completed" else "discontinue",
            ),
        )


# --- Homework -------------------------------------------------------------------------


async def _homework(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    h = _by_key(_work(ctx).homework, step.key)
    me = await tutor_principal(session, ctx, h.tutor)
    with seed_actor(session, me.user_id):
        created = await assignments.create_assignment(
            session,
            principal=me,
            body=AssignmentCreate(
                subject_id=ctx.id(f"subject:{h.subject}"),
                title=h.title,
                instructions=h.instructions,
                due_on=h.due_on,
                max_score=MAX,
                class_session_id=await class_id(session, ctx, h.cls),
                student_ids=[student_id(ctx, h.student)],
            ),
        )
    ctx.ids[f"homework:{h.key}"] = created.id


async def _submitter(
    session: AsyncSession, ctx: SimulationContext, h: HomeworkPlan
) -> Principal:
    if h.by_student:
        account = next(s for s in ctx.plan.students if s.key == h.student).account
        assert account is not None
        return await principal(session, ctx, account)
    return await tutor_principal(session, ctx, h.tutor)


async def _submit(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    h = _by_key(_work(ctx).homework, step.key)
    me = await _submitter(session, ctx, h)
    with seed_actor(session, me.user_id):
        await assignments.submit(
            session,
            principal=me,
            assignment_id=ctx.id(f"homework:{h.key}"),
            student_id=student_id(ctx, h.student),
            body=SubmissionPut(text=h.answer),
        )


async def _grade(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    h = _by_key(_work(ctx).homework, step.key)
    assert h.score is not None
    me = await tutor_principal(session, ctx, h.tutor)
    assignment = ctx.id(f"homework:{h.key}")
    version = await session.scalar(
        select(AssignmentSubmission.version).where(
            AssignmentSubmission.assignment_id == assignment,
            AssignmentSubmission.student_id == student_id(ctx, h.student),
        )
    )
    assert version is not None
    with seed_actor(session, me.user_id):
        await assignments.grade(
            session,
            principal=me,
            assignment_id=assignment,
            student_id=student_id(ctx, h.student),
            body=GradePut(version=version, score=Decimal(h.score), feedback=h.feedback),
        )


# --- Progress reports -----------------------------------------------------------------


async def _schedule(session: AsyncSession, ctx: SimulationContext, step: Step) -> None:
    s = _by_key(_work(ctx).schedules, step.key)
    coordinator = await _coordinator(session, ctx)
    with seed_actor(session, coordinator.user_id):
        created = await report_schedules.create_schedule(
            session,
            principal=coordinator,
            student_id=student_id(ctx, s.student),
            body=ProgressReportScheduleCreate(
                author_user_id=tutor_id(ctx, s.tutor),
                cadence=s.cadence,
                starts_on=s.starts_on,
            ),
        )
    ctx.ids[f"report_schedule:{s.key}"] = created.id


async def _placeholders(
    session: AsyncSession, ctx: SimulationContext, step: Step
) -> None:
    """The ``academics:create_progress_report_placeholders`` run at its tick,
    in-process with no actor (E12): every due schedule's placeholder."""
    today, ids = await due_schedules(session, step.at)
    if not ids:
        raise SimulationStepError(f"{step}: no schedule is due")
    for schedule_id in ids:
        outcome = await place_one(session, schedule_id, now=step.at, today=today)
        if outcome != "created":
            raise SimulationStepError(f"{step}: {outcome}")


async def _report_id(
    session: AsyncSession, ctx: SimulationContext, r: ReportPlan
) -> uuid.UUID:
    found = ctx.ids.get(f"report:{r.key}")
    if found is not None:
        return found
    assert r.schedule is not None
    placeholder = await session.scalar(
        select(ProgressReport.id).where(
            ProgressReport.schedule_id == ctx.id(f"report_schedule:{r.schedule}"),
            ProgressReport.period_start == r.period_start,
        )
    )
    if placeholder is None:
        raise SimulationStepError(f"{r.key}: no placeholder")
    ctx.ids[f"report:{r.key}"] = placeholder
    return placeholder


async def _report_write(
    session: AsyncSession, ctx: SimulationContext, step: Step
) -> None:
    """The tutor creates the report (or fills the schedule's placeholder)
    and submits it."""
    r = _by_key(_work(ctx).reports, step.key)
    me = await tutor_principal(session, ctx, r.tutor)
    with seed_actor(session, me.user_id):
        if r.schedule is None:
            created = await reports.create_report(
                session,
                principal=me,
                body=ProgressReportCreate(
                    student_id=student_id(ctx, r.student),
                    kind=r.kind,
                    period_start=r.period_start,
                    period_end=r.period_end if r.kind == "on_demand" else None,
                    **r.narrative,  # type: ignore[arg-type]
                ),
            )
            ctx.ids[f"report:{r.key}"] = created.id
            version = created.version
        else:
            report_id = await _report_id(session, ctx, r)
            filled = await reports.update_report(
                session,
                principal=me,
                report_id=report_id,
                body=ProgressReportUpdate(
                    version=await _version(session, ProgressReport, report_id),
                    **r.narrative,
                ),
            )
            version = filled.version
        await reports.submit_report(
            session,
            principal=me,
            report_id=ctx.id(f"report:{r.key}"),
            version=version,
        )


async def _report_changes(
    session: AsyncSession, ctx: SimulationContext, step: Step
) -> None:
    r = _by_key(_work(ctx).reports, step.key)
    assert r.changes_reason is not None
    coordinator = await _coordinator(session, ctx)
    report_id = ctx.id(f"report:{r.key}")
    with seed_actor(session, coordinator.user_id):
        await reports.request_changes(
            session,
            principal=coordinator,
            report_id=report_id,
            body=ProgressReportReason(
                version=await _version(session, ProgressReport, report_id),
                reason=r.changes_reason,
            ),
        )


async def _report_resubmit(
    session: AsyncSession, ctx: SimulationContext, step: Step
) -> None:
    r = _by_key(_work(ctx).reports, step.key)
    me = await tutor_principal(session, ctx, r.tutor)
    report_id = ctx.id(f"report:{r.key}")
    with seed_actor(session, me.user_id):
        await reports.submit_report(
            session,
            principal=me,
            report_id=report_id,
            version=await _version(session, ProgressReport, report_id),
        )


async def _report_approve(
    session: AsyncSession, ctx: SimulationContext, step: Step
) -> None:
    r = _by_key(_work(ctx).reports, step.key)
    coordinator = await _coordinator(session, ctx)
    report_id = ctx.id(f"report:{r.key}")
    with seed_actor(session, coordinator.user_id):
        await reports.approve_report(
            session,
            principal=coordinator,
            report_id=report_id,
            version=await _version(session, ProgressReport, report_id),
        )


HANDLERS: Final[dict[str, Any]] = {
    "template": _template,
    "note": _note,
    "evaluate": _evaluate,
    "plan_create": _plan_create,
    "milestone": _milestone,
    "plan_close": _plan_close,
    "homework": _homework,
    "submit": _submit,
    "grade": _grade,
    "schedule": _schedule,
    "placeholders": _placeholders,
    "report_write": _report_write,
    "report_changes": _report_changes,
    "report_resubmit": _report_resubmit,
    "report_approve": _report_approve,
}
"""The ``academics`` module's step handlers, by action."""
