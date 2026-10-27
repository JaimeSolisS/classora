"""The #354 demo learning data (part 2 of the M5 demo data):
:func:`plan_demo_learning` and the ``academics.demo_learning_plans``,
``academics.demo_homework`` and ``academics.demo_progress_reports``
generators, all for ``demo.student`` (S).

**The plan (#354 G2)** is a pure function of ``T``, the center's today, on
#299's ``P(k)`` (the k-th Monday-Friday strictly before ``T``) and ``D(k)``
(the k-th Monday-Friday on or after ``T + 2``): local dates and ``HH:MM``
times only.

- ``demo.tutor``'s learning plans: LP1, active in Matemáticas (Secundaria)
  since ``P(10)``, with one milestone done, one overdue (due ``P(2)``), one in
  progress and one pending; LP2, Inglés (Secundaria), completed on ``P(3)``
  with a past-due pending milestone that is not overdue (#332);
- ``demo.tutor``'s homework for S: HW1 graded (Inglés, on H3), HW2 handed in
  and never graded (Matemáticas, on H1) and HW3 assigned (Matemáticas, no
  class). S's only graded demo homework is in Inglés, so a spec's own
  Matemáticas grade gives the Matemáticas average (#354 E1);
- progress reports R1 (on demand, ``P(13)`` … ``P(1)``, approved for the
  family by ``demo.coordinator``), R2 (weekly, Matemáticas, the week before
  ``T``'s week, submitted: one open Approvals item) and R3 (monthly, Inglés,
  a draft), and ``demo.coordinator``'s weekly report schedule for
  ``demo.tutor`` from ``T``'s Monday (no placeholder before next Monday).

**Past data goes through the services (#354 G1, G3).** The plans and the
graded and handed-in homework run the route services under
:func:`~tests.factories.demo_clock.seed_clock`, one instant at a time, in
instant order (:attr:`DemoLearningPlan.plan_steps`,
:attr:`DemoLearningPlan.homework_steps`). HW3, the reports and the schedule
run on the real clock. No override, no direct insert.

**Idempotent; a stale schedule is skipped (#354 G4).** Each generator writes
all its rows or none and returns ``0`` when its marker exists. A database
whose #299 schedule is from an earlier day gets ``skipped (demo schedule
from an earlier day; reset to add it)``. Fixed ``es_MX`` text, no Faker.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.config import get_settings
from app.modules.academics.models import (
    Assignment,
    AssignmentSubmission,
    EvaluationTemplate,
    LearningPlan,
    LearningPlanMilestone,
    LearningPlanObjective,
    ProgressReport,
    ProgressReportSchedule,
)
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.workflows import assignments, learning_plans, progress_report_schedules
from app.workflows import progress_reports as reports
from app.workflows.assignment_schemas import AssignmentCreate, GradePut, SubmissionPut
from app.workflows.learning_plan_schemas import (
    LearningPlanCreate,
    LearningPlanMilestoneInput,
    LearningPlanMilestoneProgress,
    LearningPlanMilestoneView,
    LearningPlanObjectiveInput,
    LearningPlanTransition,
    LearningPlanView,
)
from app.workflows.progress_report_schedule_schemas import (
    ProgressReportScheduleCreate,
)
from app.workflows.progress_report_schemas import ProgressReportCreate
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_academics import (
    DEMO_COORDINATOR_USERNAME,
    DIAGNOSTIC_TEMPLATE,
    held_session_id,
    plan_demo_academics,
)
from tests.factories.demo_changes import (
    STALE_SCHEDULE,
    demo_subject_ids,
    schedule_from_today,
)
from tests.factories.demo_clock import seed_clock
from tests.factories.demo_schedule import (
    ENGLISH_SECONDARY,
    MATH_SECONDARY,
    StudentKey,
    SubjectKey,
    demo_students,
    local_instant,
    past_weekdays,
    upcoming_weekdays,
)
from tests.factories.identity import (
    DEMO_ACCOUNT_APP_ENVS,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_principal,
    demo_user_id,
)

# --- Fixed es_MX texts (#354 A4) ------------------------------------------------------

LP1_TITLE: Final = "Álgebra: ecuaciones y sistemas"
LP1_DESCRIPTION: Final = (
    "Reforzar el despeje de ecuaciones y resolver sistemas de ecuaciones de dos"
    " incógnitas."
)
LP2_TITLE: Final = "Comprensión lectora en inglés"

HW1_TITLE: Final = "Lectura: textos informativos"
"""HW1, also ``academics.demo_homework``'s marker (#354 G4)."""
HW1_INSTRUCTIONS: Final = "Lee el texto de la página 12 y responde las preguntas 1 a 5."
HW1_ANSWER: Final = "Respondí las cinco preguntas sobre el texto informativo."
HW1_FEEDBACK: Final = "Muy buenas respuestas; revisa la ortografía de la pregunta 4."
HW2_TITLE: Final = "Ecuaciones con fracciones"
HW2_INSTRUCTIONS: Final = "Resuelve los ejercicios 11 a 20 de la guía."
HW2_ANSWER: Final = "Resolví los ejercicios 11 a 20; tuve dudas en el 17."
HW3_TITLE: Final = "Sistemas de ecuaciones: método de sustitución"
HW3_INSTRUCTIONS: Final = "Resuelve los ejercicios 1 a 8 de la hoja de práctica."
HW_MAX_SCORE: Final = Decimal(10)
HW1_SCORE: Final = Decimal(9)

R1_NARRATIVE: Final = {
    "summary": "Buen avance en matemáticas e inglés: asistió a todas las clases del"
    " curso intensivo y aprobó el examen final.",
    "strengths": "Constancia y participación en clase.",
    "areas_for_improvement": "Repasar fracciones y cuidar la ortografía en inglés.",
    "next_steps": "Continuar con el plan de álgebra: sistemas de ecuaciones.",
}
R2_NARRATIVE: Final = {
    "summary": "Semana enfocada en ecuaciones con fracciones; entregó la tarea a"
    " tiempo.",
    "next_steps": "Practicar el método de sustitución.",
}
R3_NARRATIVE: Final = {"summary": "Borrador: comprensión lectora en progreso."}


# --- The plan (pure) ------------------------------------------------------------------

type MilestoneStatus = Literal["pending", "in_progress", "done"]
type PlanKey = Literal["LP1", "LP2"]
type HomeworkKey = Literal["HW1", "HW2", "HW3"]


@dataclass(frozen=True)
class PlannedMilestone:
    key: str
    title: str
    due_on: date
    status: MilestoneStatus
    """The final status (set by a :class:`PlanStep`, or ``pending``)."""
    activities: str | None = None


@dataclass(frozen=True)
class PlannedObjective:
    title: str
    milestones: tuple[PlannedMilestone, ...]


@dataclass(frozen=True)
class PlannedPlan:
    """One learning plan of S (#354, "Learning plans")."""

    key: PlanKey
    subject: SubjectKey
    title: str
    description: str | None
    starts_on: date
    target_on: date
    objectives: tuple[PlannedObjective, ...]
    status: Literal["active", "completed"]
    """The final status."""

    def milestone(self, key: str) -> PlannedMilestone:
        return next(
            m for objective in self.objectives for m in objective.milestones
            if m.key == key
        )  # fmt: skip


@dataclass(frozen=True)
class PlanStep:
    """One seed-clock write of ``academics.demo_learning_plans``: at the
    local ``day`` and ``at``, ``create`` (create and activate) a plan,
    ``progress`` (set a milestone's status) or ``complete`` it."""

    day: date
    at: str
    action: Literal["create", "progress", "complete"]
    plan: PlanKey
    milestone: str | None = None
    status: MilestoneStatus | None = None

    @property
    def local(self) -> datetime:
        return datetime.combine(self.day, time.fromisoformat(self.at))


@dataclass(frozen=True)
class PlannedHomework:
    """One homework of S (#354, "Homework"): ``held`` is the #353 class it
    is linked to (``H1``/``H3``) or ``None``."""

    key: HomeworkKey
    subject: SubjectKey
    held: Literal["H1", "H3"] | None
    title: str
    instructions: str
    due_on: date
    answer: str | None = None
    score: Decimal | None = None
    feedback: str | None = None


@dataclass(frozen=True)
class HomeworkStep:
    """One seed-clock write of ``academics.demo_homework``: ``create`` (by
    ``demo.tutor``), ``submit`` (by ``demo.student``) or ``grade`` (by
    ``demo.tutor``)."""

    day: date
    at: str
    action: Literal["create", "submit", "grade"]
    homework: HomeworkKey

    @property
    def local(self) -> datetime:
        return datetime.combine(self.day, time.fromisoformat(self.at))


@dataclass(frozen=True)
class PlannedReport:
    """One progress report of S by ``demo.tutor`` (#354, "Progress
    reports"); ``period_end`` only for ``on_demand``."""

    key: Literal["R1", "R2", "R3"]
    kind: Literal["weekly", "monthly", "on_demand"]
    subject: SubjectKey | None
    period_start: date
    period_end: date | None
    narrative: dict[str, str]
    status: Literal["draft", "submitted", "approved_for_family"]


@dataclass(frozen=True)
class DemoLearningPlan:
    """Every #354 date and time for one ``today``."""

    today: date
    plans: tuple[PlannedPlan, ...]
    """LP1, LP2."""
    plan_steps: tuple[PlanStep, ...]
    """In instant order."""
    homework: tuple[PlannedHomework, ...]
    """HW1-HW3."""
    homework_steps: tuple[HomeworkStep, ...]
    """HW1 and HW2's seed-clock writes, in instant order; HW3 is created on
    the real clock afterwards."""
    reports: tuple[PlannedReport, ...]
    """R1-R3."""
    schedule_starts_on: date
    """The weekly report schedule's first period: ``T``'s Monday."""

    def plan(self, key: PlanKey) -> PlannedPlan:
        return next(p for p in self.plans if p.key == key)

    def assignment(self, key: HomeworkKey) -> PlannedHomework:
        return next(h for h in self.homework if h.key == key)


def monday_of(day: date) -> date:
    """The Monday of ``day``'s Monday-Sunday week."""
    return day - timedelta(days=day.isoweekday() - 1)


def plan_demo_learning(today: date) -> DemoLearningPlan:
    """The #354 plan for the center's ``today`` (#354 G2)."""
    p = past_weekdays(today, 13)
    d = upcoming_weekdays(today, 8)

    def past(k: int) -> date:
        return p[k - 1]

    lp1 = PlannedPlan(
        key="LP1",
        subject=MATH_SECONDARY,
        title=LP1_TITLE,
        description=LP1_DESCRIPTION,
        starts_on=past(10),
        target_on=today + timedelta(days=42),
        objectives=(
            PlannedObjective(
                "Ecuaciones de primer grado",
                (
                    PlannedMilestone("M1", "Despejar la incógnita", past(8), "done",
                                     "Ejercicios 1 a 10 de la guía."),
                    PlannedMilestone("M2", "Ecuaciones con fracciones", past(2),
                                     "pending", "Tarea de ecuaciones con fracciones."),
                ),
            ),
            PlannedObjective(
                "Sistemas de ecuaciones 2×2",
                (
                    PlannedMilestone("M3", "Método de sustitución", d[2],
                                     "in_progress"),
                    PlannedMilestone("M4", "Método de igualación", d[7], "pending"),
                ),
            ),
        ),
        status="active",
    )  # fmt: skip
    lp2 = PlannedPlan(
        key="LP2",
        subject=ENGLISH_SECONDARY,
        title=LP2_TITLE,
        description=None,
        starts_on=past(13),
        target_on=past(1),
        objectives=(
            PlannedObjective(
                "Leer textos informativos",
                (
                    PlannedMilestone("M1", "Identificar la idea principal", past(10),
                                     "done"),
                    PlannedMilestone("M2", "Vocabulario clave del texto", past(8),
                                     "done"),
                    PlannedMilestone("M3", "Resumen escrito de un texto", past(4),
                                     "pending"),
                ),
            ),
        ),
        status="completed",
    )  # fmt: skip
    plan_steps = (
        PlanStep(past(13), "17:00", "create", "LP2"),
        PlanStep(past(10), "17:30", "progress", "LP2", "M1", "done"),
        PlanStep(past(10), "18:00", "create", "LP1"),
        PlanStep(past(8), "17:45", "progress", "LP2", "M2", "done"),
        PlanStep(past(7), "17:30", "progress", "LP1", "M1", "done"),
        PlanStep(past(3), "17:30", "complete", "LP2"),
        PlanStep(past(2), "17:30", "progress", "LP1", "M3", "in_progress"),
    )
    homework = (
        PlannedHomework(
            key="HW1",
            subject=ENGLISH_SECONDARY,
            held="H3",
            title=HW1_TITLE,
            instructions=HW1_INSTRUCTIONS,
            due_on=past(6),
            answer=HW1_ANSWER,
            score=HW1_SCORE,
            feedback=HW1_FEEDBACK,
        ),
        PlannedHomework(
            key="HW2",
            subject=MATH_SECONDARY,
            held="H1",
            title=HW2_TITLE,
            instructions=HW2_INSTRUCTIONS,
            due_on=past(2),
            answer=HW2_ANSWER,
        ),
        PlannedHomework(
            key="HW3",
            subject=MATH_SECONDARY,
            held=None,
            title=HW3_TITLE,
            instructions=HW3_INSTRUCTIONS,
            due_on=d[2],
        ),
    )
    homework_steps = (
        HomeworkStep(past(8), "17:30", "create", "HW1"),
        HomeworkStep(past(7), "20:30", "submit", "HW1"),
        HomeworkStep(past(6), "17:30", "create", "HW2"),
        HomeworkStep(past(5), "17:30", "grade", "HW1"),
        HomeworkStep(past(3), "20:30", "submit", "HW2"),
    )
    this_monday = monday_of(today)
    reports_ = (
        PlannedReport("R1", "on_demand", None, past(13), past(1), R1_NARRATIVE,
                      "approved_for_family"),
        PlannedReport("R2", "weekly", MATH_SECONDARY,
                      this_monday - timedelta(days=7), None, R2_NARRATIVE,
                      "submitted"),
        PlannedReport("R3", "monthly", ENGLISH_SECONDARY, today.replace(day=1), None,
                      R3_NARRATIVE, "draft"),
    )  # fmt: skip
    return DemoLearningPlan(
        today=today,
        plans=(lp1, lp2),
        plan_steps=plan_steps,
        homework=homework,
        homework_steps=homework_steps,
        reports=reports_,
        schedule_starts_on=this_monday,
    )


# --- Shared by the #354 generators ----------------------------------------------------


def _settings_app_env() -> str:
    return get_settings().app_env


@dataclass(frozen=True)
class _Context:
    """What every #354 generator looks up before writing."""

    tutor: Principal
    students: dict[StudentKey, uuid.UUID]
    subjects: dict[SubjectKey, uuid.UUID]
    zone: str
    plan: DemoLearningPlan


async def _accounts(
    session: AsyncSession, generator: str
) -> tuple[Principal, dict[StudentKey, uuid.UUID]]:
    tutor = await demo_principal(session, DEMO_TUTOR_USERNAME, generator)
    student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, generator)
    students = await demo_students(session, student_user_id, generator)
    return tutor, students


async def _context(
    session: AsyncSession,
    tutor: Principal,
    students: dict[StudentKey, uuid.UUID],
    plan: Callable[[date], DemoLearningPlan],
    generator: str,
) -> _Context:
    subjects = await demo_subject_ids(
        session, (MATH_SECONDARY, ENGLISH_SECONDARY), generator
    )
    zone = await administration.get_center_time_zone(session)
    return _Context(
        tutor=tutor,
        students=students,
        subjects=subjects,
        zone=zone,
        plan=plan(await administration.center_today(session)),
    )


# --- academics.demo_learning_plans (#354 B) -------------------------------------------

DEMO_LEARNING_PLANS_TABLES = (
    LearningPlan,
    LearningPlanObjective,
    LearningPlanMilestone,
    Notification,
)
"""The tables whose added rows ``academics.demo_learning_plans`` counts
(#354 B)."""


def _create_body(planned: PlannedPlan, subject_id: uuid.UUID) -> LearningPlanCreate:
    return LearningPlanCreate(
        subject_id=subject_id,
        title=planned.title,
        description=planned.description,
        starts_on=planned.starts_on,
        target_on=planned.target_on,
        objectives=[
            LearningPlanObjectiveInput(
                title=objective.title,
                milestones=[
                    LearningPlanMilestoneInput(
                        title=m.title, activities=m.activities, due_on=m.due_on
                    )
                    for m in objective.milestones
                ],
            )
            for objective in planned.objectives
        ],
    )


def _milestone_view(
    view: LearningPlanView, planned: PlannedPlan, key: str
) -> LearningPlanMilestoneView:
    title = planned.milestone(key).title
    return next(
        m for objective in view.objectives for m in objective.milestones
        if m.title == title
    )  # fmt: skip


class DemoLearningPlansGenerator:
    """``academics.demo_learning_plans`` (#354 B): ``demo.tutor``'s plans for
    S, LP1 (active, one milestone overdue) and LP2 (completed, a past-due
    milestone not overdue), through the route services under the seed clock
    (:attr:`DemoLearningPlan.plan_steps`).

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once S has any learning plan (any status, deleted included). Skipped when
    #299's schedule is from an earlier day; raises when a demo account, a
    #195 student, a demo subject or #299's schedule is missing.
    """

    name = "academics.demo_learning_plans"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoLearningPlan] = plan_demo_learning,
    ) -> None:
        self._app_env = app_env
        self._plan = plan

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        tutor, students = await _accounts(session, self.name)
        if await session.scalar(
            select(LearningPlan.id)
            .where(LearningPlan.student_id == students["S"])
            .limit(1)
        ):
            return 0
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        ctx = await _context(session, tutor, students, self._plan, self.name)
        before = await count_rows(session, DEMO_LEARNING_PLANS_TABLES)

        # B2: each step at its seed instant, in instant order.
        views: dict[PlanKey, LearningPlanView] = {}
        for step in ctx.plan.plan_steps:
            planned = ctx.plan.plan(step.plan)
            with seed_clock(local_instant(step.day, step.at, ctx.zone)):
                if step.action == "create":
                    draft = await learning_plans.create_plan(
                        session,
                        principal=tutor,
                        student_id=students["S"],
                        body=_create_body(planned, ctx.subjects[planned.subject]),
                    )
                    views[step.plan] = await learning_plans.transition_plan(
                        session,
                        principal=tutor,
                        plan_id=draft.id,
                        body=LearningPlanTransition(
                            version=draft.version, action="activate"
                        ),
                    )
                elif step.action == "complete":
                    view = views[step.plan]
                    views[step.plan] = await learning_plans.transition_plan(
                        session,
                        principal=tutor,
                        plan_id=view.id,
                        body=LearningPlanTransition(
                            version=view.version, action="complete"
                        ),
                    )
                else:
                    assert step.milestone is not None and step.status is not None
                    milestone = _milestone_view(
                        views[step.plan], planned, step.milestone
                    )
                    views[step.plan] = await learning_plans.set_milestone_progress(
                        session,
                        principal=tutor,
                        milestone_id=milestone.id,
                        body=LearningPlanMilestoneProgress(
                            version=milestone.version,
                            status=step.status,
                        ),
                    )
        return await count_rows(session, DEMO_LEARNING_PLANS_TABLES) - before


# --- academics.demo_homework (#354 C) -------------------------------------------------

DEMO_HOMEWORK_TABLES = (Assignment, AssignmentSubmission, Notification)
"""The tables whose added rows ``academics.demo_homework`` counts (#354 C)."""


class DemoHomeworkGenerator:
    """``academics.demo_homework`` (#354 C): ``demo.tutor``'s homework for S,
    HW1 (Inglés, on H3: handed in by ``demo.student`` and graded 9/10) and
    HW2 (Matemáticas, on H1: handed in, never graded) under the seed clock
    (:attr:`DemoLearningPlan.homework_steps`), and HW3 (Matemáticas, no
    class: assigned) on the real clock.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once an assignment titled :data:`HW1_TITLE` exists (deleted included).
    Skipped when #299's schedule is from an earlier day; raises when a demo
    account, a #195 student, a demo subject, #299's schedule or H1 and H3
    with their closeouts are missing.
    """

    name = "academics.demo_homework"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoLearningPlan] = plan_demo_learning,
    ) -> None:
        self._app_env = app_env
        self._plan = plan

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        tutor, students = await _accounts(session, self.name)
        student = await demo_principal(session, DEMO_STUDENT_USERNAME, self.name)
        if await session.scalar(
            select(Assignment.id).where(Assignment.title == HW1_TITLE).limit(1)
        ):
            return 0
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        ctx = await _context(session, tutor, students, self._plan, self.name)
        # H1 and H3 (#353), closed out: the classes HW2 and HW1 are linked to.
        academics_plan = plan_demo_academics(ctx.plan.today)
        held: dict[str, uuid.UUID] = {}
        for planned in academics_plan.held:
            if planned.key in ("H1", "H3"):
                assert planned.student is not None
                held[planned.key] = await held_session_id(
                    session,
                    tutor_id=tutor.user_id,
                    starts_at=local_instant(planned.day, planned.start, ctx.zone),
                    student_id=students[planned.student],
                    generator=self.name,
                    closed_out=True,
                )
        before = await count_rows(session, DEMO_HOMEWORK_TABLES)

        # C2: HW1 and HW2, each step at its seed instant, in instant order.
        created: dict[HomeworkKey, uuid.UUID] = {}
        versions: dict[HomeworkKey, int] = {}
        for step in ctx.plan.homework_steps:
            planned_hw = ctx.plan.assignment(step.homework)
            with seed_clock(local_instant(step.day, step.at, ctx.zone)):
                if step.action == "create":
                    created[step.homework] = await _assign(
                        session, ctx, planned_hw, held
                    )
                elif step.action == "submit":
                    item = await assignments.submit(
                        session,
                        principal=student,
                        assignment_id=created[step.homework],
                        student_id=students["S"],
                        body=SubmissionPut(text=planned_hw.answer),
                    )
                    versions[step.homework] = item.submission.version
                else:
                    await assignments.grade(
                        session,
                        principal=tutor,
                        assignment_id=created[step.homework],
                        student_id=students["S"],
                        body=GradePut(
                            version=versions[step.homework],
                            score=planned_hw.score,
                            feedback=planned_hw.feedback,
                        ),
                    )
        # HW3 on the real clock.
        await _assign(session, ctx, ctx.plan.assignment("HW3"), held)
        return await count_rows(session, DEMO_HOMEWORK_TABLES) - before


async def _assign(
    session: AsyncSession,
    ctx: _Context,
    planned: PlannedHomework,
    held: dict[str, uuid.UUID],
) -> uuid.UUID:
    view = await assignments.create_assignment(
        session,
        principal=ctx.tutor,
        body=AssignmentCreate(
            subject_id=ctx.subjects[planned.subject],
            title=planned.title,
            instructions=planned.instructions,
            due_on=planned.due_on,
            max_score=HW_MAX_SCORE,
            class_session_id=None if planned.held is None else held[planned.held],
            student_ids=[ctx.students["S"]],
        ),
    )
    return view.id


# --- academics.demo_progress_reports (#354 D) -----------------------------------------

DEMO_PROGRESS_REPORTS_TABLES = (
    ProgressReport,
    ApprovalRequest,
    ProgressReportSchedule,
    Notification,
)
"""The tables whose added rows ``academics.demo_progress_reports`` counts
(#354 D)."""


class DemoProgressReportsGenerator:
    """``academics.demo_progress_reports`` (#354 D): ``demo.tutor``'s
    progress reports about S, R1 (submitted, approved for the family by
    ``demo.coordinator``), R2 (submitted: an open Approvals item) and R3 (a
    draft), and ``demo.coordinator``'s weekly report schedule for
    ``demo.tutor`` from ``T``'s Monday, on the real clock.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once S has any progress report (any status, deleted included) or report
    schedule. Skipped when #299's schedule is from an earlier day; raises
    when a demo account, a #195 student, a demo subject, #299's schedule, the
    #353 template ``Diagnóstico de matemáticas``, LP1 or HW1 is missing.
    """

    name = "academics.demo_progress_reports"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoLearningPlan] = plan_demo_learning,
    ) -> None:
        self._app_env = app_env
        self._plan = plan

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        tutor, students = await _accounts(session, self.name)
        reviewer = await demo_principal(session, DEMO_COORDINATOR_USERNAME, self.name)
        if await _reported(session, students["S"]):
            return 0
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        ctx = await _context(session, tutor, students, self._plan, self.name)
        await _prerequisites(session, students["S"], self.name)
        before = await count_rows(session, DEMO_PROGRESS_REPORTS_TABLES)

        # D2: R1 submitted and approved, R2 submitted, R3 a draft.
        for planned in ctx.plan.reports:
            view = await reports.create_report(
                session,
                principal=tutor,
                body=ProgressReportCreate(
                    student_id=students["S"],
                    kind=planned.kind,
                    period_start=planned.period_start,
                    period_end=planned.period_end,
                    subject_id=(
                        None
                        if planned.subject is None
                        else ctx.subjects[planned.subject]
                    ),
                    **planned.narrative,
                ),
            )
            if planned.status == "draft":
                continue
            view = await reports.submit_report(
                session, principal=tutor, report_id=view.id, version=view.version
            )
            if planned.status == "approved_for_family":
                await reports.approve_report(
                    session, principal=reviewer, report_id=view.id, version=view.version
                )

        # D2: the weekly schedule, from T's Monday.
        await progress_report_schedules.create_schedule(
            session,
            principal=reviewer,
            student_id=students["S"],
            body=ProgressReportScheduleCreate(
                author_user_id=tutor.user_id,
                cadence="weekly",
                subject_id=ctx.subjects[MATH_SECONDARY],
                starts_on=ctx.plan.schedule_starts_on,
            ),
        )
        return await count_rows(session, DEMO_PROGRESS_REPORTS_TABLES) - before


async def _reported(session: AsyncSession, s_id: uuid.UUID) -> bool:
    """The marker (#354 D1): a progress report or a report schedule of S."""
    found = await session.scalar(
        select(ProgressReport.id).where(ProgressReport.student_id == s_id).limit(1)
    )
    if found is not None:
        return True
    found = await session.scalar(
        select(ProgressReportSchedule.id)
        .where(ProgressReportSchedule.student_id == s_id)
        .limit(1)
    )
    return found is not None


async def _prerequisites(
    session: AsyncSession, s_id: uuid.UUID, generator: str
) -> None:
    """D1: the #353 evaluations (their template), LP1 and HW1."""
    checks = (
        (
            "academics.demo_evaluations",
            select(EvaluationTemplate.id).where(
                EvaluationTemplate.name == DIAGNOSTIC_TEMPLATE
            ),
        ),
        (
            "academics.demo_learning_plans",
            select(LearningPlan.id).where(
                LearningPlan.student_id == s_id, LearningPlan.title == LP1_TITLE
            ),
        ),
        (
            "academics.demo_homework",
            select(Assignment.id).where(Assignment.title == HW1_TITLE),
        ),
    )
    for earlier, query in checks:
        if await session.scalar(query.limit(1)) is None:
            raise DemoPrerequisiteMissingError(
                f"the {earlier} data must exist before {generator}"
            )
