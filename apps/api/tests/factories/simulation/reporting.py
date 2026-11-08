"""``reporting`` (#554 HF-01f): saved report setups, exports, dashboard
layouts and recently deleted records, after ``administration``.

**The plan** (:func:`build_reporting`, pure):

- report setups (#125, ``create_setup``): the Administrator's private
  "active students" (6 Oct), the Academic coordinator's students of one
  level (7 Oct) and Finance's "monthly expenses" for November, shared with
  the Owner role (3 Nov);
- dashboard layouts (#127, ``save_layout``): the Owner and Finance reorder
  their widgets and hide one (12 Oct); the Administrator and the Academic
  coordinator keep the role defaults;
- exports (#124, ``request_export`` + ``run_job`` in the run's Unit of Work,
  so no Procrastinate row): Finance's October expenses (3 Nov 10:00), made
  ``ready`` then purged by ``purge_expired`` on 5 Nov (``expired``); the
  Owner's class sessions of 1-18 Nov (18 Nov 17:00, signed in moments
  before: a personal dataset needs a recent sign-in), ``ready`` at the
  anchor and expiring 24 hours later in simulated time (E9);
- recently deleted (#129) on 12 Nov: a tutor creates a learning-plan draft
  for a student they teach and deletes it, and writes an internal note
  about that student's last class and deletes it (report setups are hard
  deleted, so they aren't a #129 kind). Both are restorable at the anchor
  (30 days).

Failed jobs (#128): none. The audit log needs nothing here: every audited
write already recorded its events (E7: at the real run time).
"""

import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core import clock
from app.core.authz import Principal
from app.core.errors import AppError
from app.core.reauth import RecentAuthGuard
from app.modules.academics.models import (
    LearningPlan,
    LearningPlanMilestone,
    LearningPlanObjective,
    SessionNote,
)
from app.modules.academics.schemas import SessionNoteCreate
from app.modules.administration.models import Notification
from app.modules.catalog.models import EducationLevel
from app.modules.reporting.dashboard.models import DashboardLayout
from app.modules.reporting.dashboard.registry import available_widgets
from app.modules.reporting.dashboard.schemas import DashboardLayoutUpdate
from app.modules.reporting.dashboard.service import save_layout
from app.modules.reporting.exports import service as exports
from app.modules.reporting.exports.datasets import ExportParameters
from app.modules.reporting.exports.models import ExportFile, ExportJob
from app.modules.reporting.exports.router import PERSONAL_EXPORT_ACTION
from app.modules.reporting.setups.models import ReportSetup
from app.modules.reporting.setups.schemas import ReportSetupCreate
from app.modules.reporting.setups.service import create_setup
from app.workflows import learning_plans, session_notes
from app.workflows.learning_plan_schemas import (
    LearningPlanCreate,
    LearningPlanMilestoneInput,
    LearningPlanObjectiveInput,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.administration import SNAPSHOT_DAY, local_day
from tests.factories.simulation.anchor import SIM_ANCHOR, local_instant, sim_clock
from tests.factories.simulation.people import principal
from tests.factories.simulation.plan import AccountPlan, SimulationPlan
from tests.factories.simulation.schedule import (
    SimulationStepError,
    class_id,
    tutor_principal,
)
from tests.factories.simulation.texts import TextPool
from tests.factories.simulation.timeline import SimulationContext

DELETED_ON: Final = date(2026, 11, 12)
PLAN_CREATED_AT: Final = local_instant(DELETED_ON, "10:00")
PLAN_DELETED_AT: Final = local_instant(DELETED_ON, "10:20")
NOTE_CREATED_AT: Final = local_instant(DELETED_ON, "18:00")
NOTE_DELETED_AT: Final = local_instant(DELETED_ON, "18:10")
FINANCE_EXPORT_AT: Final = local_instant(date(2026, 11, 3), "10:00")
FINANCE_EXPORT_RUN_AT: Final = local_instant(date(2026, 11, 3), "10:01")
PURGE_AT: Final = local_instant(date(2026, 11, 5), "08:00")
OWNER_EXPORT_AT: Final = local_instant(date(2026, 11, 18), "17:00")
OWNER_EXPORT_RUN_AT: Final = local_instant(date(2026, 11, 18), "17:01")
LAYOUTS_AT: Final = local_instant(date(2026, 10, 12), "09:00")

PARAMETERS: Final = TypeAdapter(ExportParameters)


# --- The plan (pure) ------------------------------------------------------------------


@dataclass(frozen=True)
class SetupPlan:
    creator: str
    """``owner``, ``admin``, ``coordinator`` or ``finance``."""
    name: str
    parameters: dict[str, Any]
    """The export parameters; ``education_level`` (a level key) is replaced
    by its id at run time."""
    shared_role: str | None
    at: datetime


@dataclass(frozen=True)
class ExportPlan:
    requester: str
    parameters: dict[str, Any]
    at: datetime
    run_at: datetime


@dataclass(frozen=True)
class LayoutPlan:
    user: str
    order: tuple[str, ...]
    """The widgets in display order (those the user can't see are left out
    at run time; any left out of the list are appended, shown)."""
    hidden: str


@dataclass(frozen=True)
class DeletedPlan:
    tutor: str
    student: str
    subject: str
    cls: str
    """The class the deleted internal note is about."""
    title: str
    objective: str
    milestone: str
    note: str


@dataclass(frozen=True)
class Reporting:
    setups: tuple[SetupPlan, ...]
    exports: tuple[ExportPlan, ...]
    layouts: tuple[LayoutPlan, ...]
    deleted: DeletedPlan

    def days(self) -> set[date]:
        instants = (
            *(s.at for s in self.setups),
            *(e.at for e in self.exports),
            PURGE_AT,
            LAYOUTS_AT,
            PLAN_CREATED_AT,
            NOTE_CREATED_AT,
        )
        return {local_day(at) for at in instants}


def build_reporting(plan: SimulationPlan) -> Reporting:
    """HF-01f's reporting plan for ``plan`` (pure)."""
    texts = TextPool(plan.language)
    names = texts.mapping("report_setups")
    setups = (
        SetupPlan(
            "admin",
            names["admin"],
            {"dataset": "students", "format": "xlsx", "filters": {"status": "active"}},
            None,
            local_instant(date(2026, 10, 6), "09:00"),
        ),
        SetupPlan(
            "coordinator",
            names["coordinator"],
            {
                "dataset": "students",
                "format": "csv",
                "filters": {"status": "active", "education_level": "middle_school"},
            },
            None,
            local_instant(date(2026, 10, 7), "09:00"),
        ),
        SetupPlan(
            "finance",
            names["finance"],
            {
                "dataset": "expenses",
                "format": "csv",
                "filters": {"incurred_from": "2026-11-01", "incurred_to": "2026-11-30"},
            },
            "owner",
            local_instant(date(2026, 11, 3), "09:30"),
        ),
    )
    exports_ = (
        ExportPlan(
            "finance",
            {
                "dataset": "expenses",
                "format": "csv",
                "filters": {"incurred_from": "2026-10-01", "incurred_to": "2026-10-31"},
            },
            FINANCE_EXPORT_AT,
            FINANCE_EXPORT_RUN_AT,
        ),
        ExportPlan(
            "owner",
            {
                "dataset": "class_sessions",
                "format": "xlsx",
                "filters": {"from": "2026-11-01", "to": "2026-11-18"},
            },
            OWNER_EXPORT_AT,
            OWNER_EXPORT_RUN_AT,
        ),
    )
    layouts = (
        LayoutPlan(
            "owner",
            (
                "tutor_performance",
                "revenue_trend",
                "student_retention",
                "room_utilization",
                "balances",
                "course_profitability",
            ),
            "course_profitability",
        ),
        LayoutPlan(
            "finance",
            ("balances", "revenue_trend", "course_profitability"),
            "course_profitability",
        ),
    )

    # The deleted records: a tutor's individual student taught through the
    # anchor, and that student's last class with them before 12 Nov.
    timetable = plan.timetable
    deleted: DeletedPlan | None = None
    for series in timetable.series:
        if (
            series.kind != "individual"
            or series.starts_on > DELETED_ON
            or series.until < SIM_ANCHOR
        ):
            continue
        held = [
            c
            for c in timetable.classes
            if c.series == series.key
            and c.status == "completed"
            and c.final.tutor == series.tutor
            and c.final.day < DELETED_ON
        ]
        if not held:
            continue
        last = max(held, key=lambda c: c.final.starts_at)
        words = texts.mapping("deleted_records")
        deleted = DeletedPlan(
            tutor=series.tutor,
            student=series.students[0],
            subject=series.subject,
            cls=last.key,
            title=words["plan_title"],
            objective=words["plan_objective"],
            milestone=words["plan_milestone"],
            note=words["note"],
        )
        break
    if deleted is None:
        raise RuntimeError("no class for the deleted note")
    result = Reporting(setups, exports_, layouts, deleted)
    if SNAPSHOT_DAY in result.days():  # pragma: no cover - a plan invariant
        raise RuntimeError("reporting must write nothing on the read snapshot day")
    return result


# --- The module -----------------------------------------------------------------------

REPORTING_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    ReportSetup,
    ExportJob,
    ExportFile,
    DashboardLayout,
    LearningPlan,
    LearningPlanObjective,
    LearningPlanMilestone,
    SessionNote,
    Notification,
)
"""The tables whose rows ``reporting`` counts (the purge deletes one export
file; the soft deletes are updates)."""

type Action = Callable[[AsyncSession, SimulationContext], Awaitable[None]]


def _account(ctx: SimulationContext, role: str) -> AccountPlan:
    return {
        "owner": ctx.plan.owner,
        "admin": ctx.plan.administrator,
        "coordinator": ctx.plan.coordinator,
        "finance": ctx.plan.finance,
    }[role]


async def _staff(session: AsyncSession, ctx: SimulationContext, role: str) -> Principal:
    return await principal(session, ctx, _account(ctx, role))


class ReportingModule:
    """HF-01f's ``reporting`` module (module docstring)."""

    name = "reporting"
    tables = REPORTING_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (await session.scalar(select(ReportSetup.id).limit(1))) is not None

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return ctx.plan.reporting.days()

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        for at, action in sorted(_actions(ctx, day), key=lambda item: item[0]):
            with sim_clock(at):
                try:
                    await action(session, ctx)
                except AppError as exc:
                    raise SimulationStepError(
                        f"reporting {at}: {exc.code} {exc.errors or exc.details}"
                    ) from exc
        await session.flush()
        session.expunge_all()

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None


def _actions(ctx: SimulationContext, day: date) -> list[tuple[datetime, Action]]:
    plan = ctx.plan.reporting
    planned: list[tuple[datetime, Action]] = [
        *((s.at, _setup(s)) for s in plan.setups),
        *((e.at, _export(e)) for e in plan.exports),
        (PURGE_AT, _purge),
        (LAYOUTS_AT, _layouts),
        (PLAN_CREATED_AT, _deleted_plan),
        (NOTE_CREATED_AT, _deleted_note),
    ]
    return [(at, action) for at, action in planned if local_day(at) == day]


def _setup(item: SetupPlan) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await _staff(session, ctx, item.creator)
        parameters = dict(item.parameters)
        filters = dict(parameters["filters"])
        level = filters.pop("education_level", None)
        if level is not None:
            filters["education_level_id"] = str(
                await session.scalar(
                    select(EducationLevel.id).where(EducationLevel.seed_key == level)
                )
            )
        parameters["filters"] = filters
        with seed_actor(session, me.user_id):
            await create_setup(
                session,
                principal=me,
                body=ReportSetupCreate.model_validate(
                    {
                        "name": item.name,
                        "parameters": parameters,
                        "shared_role": item.shared_role,
                    }
                ),
            )

    return run


def _export(item: ExportPlan) -> Action:
    """Request, then build it a minute later (the worker's step)."""

    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        me = await _staff(session, ctx, item.requester)
        with seed_actor(session, me.user_id):
            job = await exports.request_export(
                session,
                principal=me,
                parameters=PARAMETERS.validate_python(item.parameters),
                # Signed in moments before (a personal dataset checks it).
                guard=RecentAuthGuard(PERSONAL_EXPORT_ACTION, clock.now()),
            )
        with sim_clock(item.run_at), seed_actor(session, me.user_id):
            status = await exports.run_job(session, job.id)
        if status != "ready":
            raise SimulationStepError(f"export {item.parameters}: {status}")

    return run


async def _purge(session: AsyncSession, ctx: SimulationContext) -> None:
    if await exports.purge_expired(session) != 1:
        raise SimulationStepError("the October expenses export didn't expire")


async def _layouts(session: AsyncSession, ctx: SimulationContext) -> None:
    for layout in ctx.plan.reporting.layouts:
        me = await _staff(session, ctx, layout.user)
        available = set(available_widgets(me))
        widgets = [
            {"widget": code, "visible": code != layout.hidden}
            for code in layout.order
            if code in available
        ]
        with seed_actor(session, me.user_id):
            await save_layout(
                session,
                principal=me,
                body=DashboardLayoutUpdate.model_validate({"widgets": widgets}),
            )


async def _deleted_plan(session: AsyncSession, ctx: SimulationContext) -> None:
    d = ctx.plan.reporting.deleted
    me = await tutor_principal(session, ctx, d.tutor)
    with seed_actor(session, me.user_id):
        draft = await learning_plans.create_plan(
            session,
            principal=me,
            student_id=ctx.id(f"student:{d.student}"),
            body=LearningPlanCreate(
                subject_id=ctx.id(f"subject:{d.subject}"),
                title=d.title,
                objectives=[
                    LearningPlanObjectiveInput(
                        title=d.objective,
                        milestones=[LearningPlanMilestoneInput(title=d.milestone)],
                    )
                ],
            ),
        )
    with sim_clock(PLAN_DELETED_AT), seed_actor(session, me.user_id):
        await learning_plans.delete_plan(
            session, principal=me, plan_id=draft.id, version=draft.version
        )
    ctx.ids["deleted:plan"] = draft.id


async def _deleted_note(session: AsyncSession, ctx: SimulationContext) -> None:
    d = ctx.plan.reporting.deleted
    me = await tutor_principal(session, ctx, d.tutor)
    target: uuid.UUID = await class_id(session, ctx, d.cls)
    with seed_actor(session, me.user_id):
        note = await session_notes.create_note(
            session,
            principal=me,
            class_session_id=target,
            body=SessionNoteCreate(
                student_id=ctx.id(f"student:{d.student}"), body=d.note
            ),
        )
    with sim_clock(NOTE_DELETED_AT), seed_actor(session, me.user_id):
        await session_notes.delete_note(
            session, principal=me, note_id=note.id, version=note.version
        )
    ctx.ids["deleted:note"] = note.id
