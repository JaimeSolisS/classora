"""The demo course (#303 C): the ``courses.demo_courses`` generator.

A course template and a cohort taught by ``demo.tutor2``, with two #195
students enrolled, written through the services the routes call (#303 G1)
and dated by :func:`~tests.factories.demo_changes.plan_demo_changes`. Fixed
``es_MX`` text, no Faker.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.catalog.models import Classroom
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
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
)
from app.modules.staff.models import TutorQualification
from app.workflows import cohort_enrollment, cohorts
from app.workflows.cohort_schemas import (
    CohortCreate,
    CohortEnrollmentCreate,
    CohortRecurrence,
)
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_changes import (
    COHORT_MINUTES,
    COHORT_NAME,
    COHORT_SESSIONS,
    STALE_SCHEDULE,
    DemoChangesPlan,
    demo_subject_ids,
    plan_demo_changes,
    schedule_from_today,
)
from tests.factories.demo_schedule import (
    AULA_2,
    CIENCIAS_SECONDARY,
    MATH_SECONDARY,
    demo_students,
)
from tests.factories.identity import (
    DEMO_ACCOUNT_APP_ENVS,
    DEMO_OWNER_USERNAME,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR2_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_user_id,
)

# --- The demo course (#303 C) ---------------------------------------------------------

TEMPLATE_NAME = "Preparación para el examen de ingreso"
TEMPLATE_DESCRIPTION = (
    "Repaso de matemáticas y ciencias para el examen de ingreso a la preparatoria."
)
MIN_ATTENDANCE_PERCENT = 80
MODULES: tuple[tuple[str, tuple[str, str], int, str | None], ...] = (
    ("Aritmética y álgebra", MATH_SECONDARY, 3, "Guía de ejercicios, unidad 1"),
    ("Geometría", MATH_SECONDARY, 2, None),
    ("Ciencias naturales", CIENCIAS_SECONDARY, 2, None),
)
"""Title, subject, planned sessions and materials; all required (#303 C2)."""
ASSESSMENTS: tuple[tuple[str, bool, int | None], ...] = (
    ("Examen diagnóstico", False, None),
    ("Simulacro final", True, 60),
)
"""Title, required and minimum score; both for the whole course (#303 C2)."""
COHORT_MAX_STUDENTS = 6

DEMO_COURSES_TABLES = (
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
)
"""The tables whose added rows ``courses.demo_courses`` counts (#303 C)."""


def _settings_app_env() -> str:
    return get_settings().app_env


@dataclass
class _RunSession:
    """What ``enroll_in_cohort`` reads of its Unit of Work: the session
    (#303 G1). The demo run's one Unit of Work commits."""

    session: AsyncSession


class DemoCoursesGenerator:
    """``courses.demo_courses`` (#303 C): the course template ``Preparación
    para el examen de ingreso`` and its cohort ``Examen de ingreso, grupo 1``
    (``demo.tutor2``, ``Aula 2``, dated by
    :func:`~tests.factories.demo_changes.plan_demo_changes`), whose series
    offer ``demo.tutor2`` accepts, with S and A enrolled.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once the template exists, in any state. Skipped when #299's schedule is
    from an earlier day; raises when a demo account, a #195 student, a demo
    subject, ``Aula 2``, #299's schedule or ``demo.tutor2``'s qualifications
    are missing.
    """

    name = "courses.demo_courses"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoChangesPlan] = plan_demo_changes,
    ) -> None:
        self._app_env = app_env
        self._plan = plan

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        owner_id = await demo_user_id(session, DEMO_OWNER_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        tutor2_id = await demo_user_id(session, DEMO_TUTOR2_USERNAME, self.name)
        if await session.scalar(
            select(CourseTemplate.id).where(CourseTemplate.name == TEMPLATE_NAME)
        ):
            return 0
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        subjects = await demo_subject_ids(
            session, (MATH_SECONDARY, CIENCIAS_SECONDARY), self.name
        )
        aula_2 = await session.scalar(
            select(Classroom.id).where(Classroom.name == AULA_2)
        )
        if aula_2 is None:
            raise DemoPrerequisiteMissingError(
                f"the {AULA_2} classroom must exist before {self.name}"
            )
        approved = set(
            await session.scalars(
                select(TutorQualification.subject_id)
                .where(TutorQualification.tutor_user_id == tutor2_id)
                .where(TutorQualification.status == "approved")
            )
        )
        if not set(subjects.values()) <= approved:
            raise DemoPrerequisiteMissingError(
                f"the staff.demo_tutor2 qualifications must exist before {self.name}"
            )
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_COURSES_TABLES)

        # C2: the template.
        template = await courses.create_course_template(
            session,
            CourseTemplateWrite(
                name=TEMPLATE_NAME,
                description=TEMPLATE_DESCRIPTION,
                subject_ids=[subjects[MATH_SECONDARY], subjects[CIENCIAS_SECONDARY]],
                session_count=COHORT_SESSIONS,
                session_minutes=COHORT_MINUTES,
                min_attendance_percent=MIN_ATTENDANCE_PERCENT,
                modules=[
                    CourseTemplateModuleWrite(
                        title=title,
                        subject_id=subjects[subject],
                        planned_sessions=planned,
                        is_required=True,
                        materials=materials,
                    )
                    for title, subject, planned, materials in MODULES
                ],
                assessments=[
                    CourseTemplateAssessmentWrite(
                        title=title, is_required=required, min_score_percent=score
                    )
                    for title, required, score in ASSESSMENTS
                ],
            ),
        )

        # C3: the cohort, as the Owner; demo.tutor2 accepts its series offer.
        cohort_id = await cohorts.create_cohort(
            session,
            CohortCreate(
                name=COHORT_NAME,
                template_id=template.id,
                subject_id=subjects[MATH_SECONDARY],
                max_students=COHORT_MAX_STUDENTS,
                tutor_user_id=tutor2_id,
                classroom_id=aula_2,
                starts_on=plan.cohort_starts_on,
                start_time=time.fromisoformat(plan.cohort_start),
                recurrence=CohortRecurrence(weekdays=list(plan.cohort_weekdays)),
            ),
            horizon_weeks=get_settings().scheduling_horizon_weeks,
            actor_id=owner_id,
        )
        offer_id = await session.scalar(
            select(AssignmentOffer.id)
            .join(ClassSeries, ClassSeries.id == AssignmentOffer.class_series_id)
            .where(ClassSeries.cohort_id == cohort_id)
            .where(AssignmentOffer.status == "pending")
        )
        assert offer_id is not None
        await scheduling.answer_offer(
            session, offer_id, caller_id=tutor2_id, accept=True
        )

        # C4: S, then A, as the Owner.
        run = cast(UnitOfWork, _RunSession(session))
        for key in ("S", "A"):
            await cohort_enrollment.enroll_in_cohort(
                run,
                cohort_id,
                CohortEnrollmentCreate(student_id=students[key]),
                actor_id=owner_id,
            )
        return await count_rows(session, DEMO_COURSES_TABLES) - before
