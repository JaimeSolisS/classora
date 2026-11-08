"""``courses`` (#550 HF-01b): the course templates, their cohorts and the
enrollments, through the courses and cohort services.

- At :data:`~.anchor.SIM_SETUP`, the Academic coordinator (``course.manage``)
  creates both templates (subjects, modules with materials, assessments; the
  service keeps the snapshot), and the Administrator (``schedule.manage``)
  creates the cohort that runs 8 Sep-22 Oct and enrolls its students; its
  tutor accepts the series offer.
- On 12 Oct the Administrator creates the cohort that runs 28 Oct-4 Dec
  (spanning the anchor); its tutor accepts, and its students enroll over the
  next days.

Cohort series are materialized through their last class at creation (the
server derives the count around the known closures). The classes are held
and closed out by the ``schedule`` module like any other.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.uow import UnitOfWork
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.billing.models import (
    BillingAccount,
    Charge,
    ChargeAdjustment,
    ChargeDiscount,
    PackageCreditTransaction,
)
from app.modules.courses import service as courses
from app.modules.courses.models import (
    CohortEnrollment,
    CourseCohort,
    CourseTemplate,
    CourseTemplateAssessment,
    CourseTemplateModule,
    CourseTemplateSnapshot,
    CourseTemplateSubject,
)
from app.modules.courses.schemas import (
    CourseTemplateAssessmentWrite,
    CourseTemplateModuleWrite,
    CourseTemplateWrite,
)
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
)
from app.workflows import cohort_enrollment, cohorts
from app.workflows.cohort_schemas import (
    CohortCreate,
    CohortEnrollmentCreate,
    CohortRecurrence,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import SIM_START, sim_clock
from tests.factories.simulation.people import principal
from tests.factories.simulation.schedule import (
    accept_series_offer,
    horizon_weeks,
    local_day,
    room_id,
    student_id,
    tutor_id,
)
from tests.factories.simulation.timeline import SimulationContext
from tests.factories.simulation.timetable import (
    COURSE_EVENTS,
    CreateCohort,
    CreateCourse,
    Enroll,
)

COURSES_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    CourseTemplate,
    CourseTemplateSubject,
    CourseTemplateModule,
    CourseTemplateAssessment,
    CourseTemplateSnapshot,
    CourseCohort,
    CohortEnrollment,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
    AssignmentOffer,
    AssignmentOfferSession,
    ApprovalRequest,
    Notification,
    # HF-01d: what the M4 hooks write in billing (package consumption at a
    # closeout approval, a later enrollment's fee) once billing is on.
    BillingAccount,
    Charge,
    ChargeDiscount,
    ChargeAdjustment,
    PackageCreditTransaction,
)
"""The courses tables, and the cohort series, sessions and offers this
module's steps create."""

ACCEPT_AFTER: Final = timedelta(minutes=20)


@dataclass
class _RunSession:
    """What ``enroll_in_cohort`` reads of its Unit of Work: the session (as
    the demo's ``courses`` generators; the run's one Unit of Work commits)."""

    session: AsyncSession


class CoursesModule:
    name = "courses"
    tables = COURSES_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (await session.scalar(select(CourseTemplate.id).limit(1))) is not None

    def _events(
        self, ctx: SimulationContext
    ) -> Iterable[CreateCourse | CreateCohort | Enroll]:
        return (e for e in ctx.plan.timetable.events if isinstance(e, COURSE_EVENTS))

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        for event in self._events(ctx):
            if local_day(event.at) < SIM_START:
                await _run(session, ctx, event)

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return {
            local_day(e.at) for e in self._events(ctx) if local_day(e.at) >= SIM_START
        }

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        for event in self._events(ctx):
            if local_day(event.at) == day:
                await _run(session, ctx, event)

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None


async def _run(
    session: AsyncSession,
    ctx: SimulationContext,
    event: CreateCourse | CreateCohort | Enroll,
) -> None:
    with sim_clock(event.at):
        if isinstance(event, CreateCourse):
            await _create_course(session, ctx, event)
        elif isinstance(event, CreateCohort):
            await _create_cohort(session, ctx, event)
        else:
            await _enroll(session, ctx, event)
    if isinstance(event, CreateCohort):
        with sim_clock(event.at + ACCEPT_AFTER):
            await accept_series_offer(
                session, ctx, event.cohort.series.key, event.cohort.series.tutor
            )


async def _create_course(
    session: AsyncSession, ctx: SimulationContext, event: CreateCourse
) -> None:
    course = event.course
    coordinator = await principal(session, ctx, ctx.plan.coordinator)
    subject = ctx.id(f"subject:{course.subject}")
    with seed_actor(session, coordinator.user_id):
        created = await courses.create_course_template(
            session,
            CourseTemplateWrite(
                name=course.name,
                description=course.description,
                subject_ids=[subject],
                session_count=course.session_count,
                session_minutes=course.minutes,
                min_attendance_percent=course.min_attendance_percent,
                modules=[
                    CourseTemplateModuleWrite(
                        title=title,
                        subject_id=subject,
                        planned_sessions=planned,
                        is_required=True,
                        materials=materials,
                    )
                    for title, planned, materials in course.modules
                ],
                assessments=[
                    CourseTemplateAssessmentWrite(
                        title=title, is_required=required, min_score_percent=score
                    )
                    for title, required, score in course.assessments
                ],
            ),
        )
    ctx.ids[f"course:{course.key}"] = created.id


async def _create_cohort(
    session: AsyncSession, ctx: SimulationContext, event: CreateCohort
) -> None:
    cohort = event.cohort
    series = cohort.series
    admin = await principal(session, ctx, ctx.plan.administrator)
    with seed_actor(session, admin.user_id):
        cohort_id = await cohorts.create_cohort(
            session,
            CohortCreate(
                name=cohort.name,
                template_id=ctx.id(f"course:{cohort.course}"),
                subject_id=ctx.id(f"subject:{series.subject}"),
                max_students=cohort.max_students,
                tutor_user_id=tutor_id(ctx, series.tutor),
                classroom_id=room_id(ctx, series.room),
                starts_on=series.starts_on,
                start_time=time.fromisoformat(series.start),
                recurrence=CohortRecurrence(weekdays=list(series.weekdays)),
            ),
            horizon_weeks=horizon_weeks(local_day(event.at)),
            actor_id=admin.user_id,
        )
    ctx.ids[f"cohort:{cohort.key}"] = cohort_id
    found = await session.scalar(
        select(ClassSeries.id).where(ClassSeries.cohort_id == cohort_id)
    )
    assert found is not None
    ctx.ids[f"series:{series.key}"] = found


async def _enroll(session: AsyncSession, ctx: SimulationContext, event: Enroll) -> None:
    admin = await principal(session, ctx, ctx.plan.administrator)
    with seed_actor(session, admin.user_id):
        await cohort_enrollment.enroll_in_cohort(
            cast(UnitOfWork, _RunSession(session)),
            ctx.id(f"cohort:{event.cohort}"),
            CohortEnrollmentCreate(student_id=student_id(ctx, event.student)),
            actor_id=admin.user_id,
        )
