"""The finished demo course (#353 C): the ``courses.demo_past_cohort``
generator.

A six-class course cohort taught by ``demo.tutor2`` that is already over,
with S, A and B enrolled, every class held, closed out and approved, dated by
:func:`~tests.factories.demo_academics.plan_demo_academics`. Like
``scheduling.demo_held_sessions``, it calls the services the routes call
under :func:`~tests.factories.demo_clock.seed_clock` (#353 E2 (a)): the
template, the cohort, the series offer's answer and the enrollments at
``T0``, then each class's attendance and closeout at its end + 15 minutes
and ``demo.coordinator``'s approval at its end + 2 hours. Fixed ``es_MX``
text, no Faker.
"""

import uuid
from collections.abc import Callable
from datetime import date, time
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.uow import UnitOfWork
from app.modules.academics.models import SessionCloseout, SessionCloseoutApproval
from app.modules.administration import service as administration
from app.modules.courses import service as courses
from app.modules.courses.models import CourseTemplate
from app.modules.courses.schemas import (
    CourseTemplateAssessmentWrite,
    CourseTemplateModuleWrite,
    CourseTemplateWrite,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import (
    AssignmentOffer,
    ClassSeries,
    ClassSession,
    ClassSessionAttendance,
)
from app.modules.staff.models import TutorQualification
from app.workflows import cohort_enrollment, cohorts
from app.workflows.cohort_schemas import (
    CohortCreate,
    CohortEnrollmentCreate,
    CohortRecurrence,
)
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_academics import (
    COHORT_NAME,
    DEMO_COORDINATOR_USERNAME,
    DemoAcademicsPlan,
    classroom_id,
    hold_class,
    plan_demo_academics,
)
from tests.factories.demo_changes import (
    STALE_SCHEDULE,
    demo_subject_ids,
    schedule_from_today,
)
from tests.factories.demo_clock import seed_clock
from tests.factories.demo_courses import DEMO_COURSES_TABLES, _RunSession
from tests.factories.demo_schedule import (
    AULA_2,
    MATH_SECONDARY,
    StudentKey,
    demo_students,
    local_instant,
)
from tests.factories.identity import (
    DEMO_ACCOUNT_APP_ENVS,
    DEMO_OWNER_USERNAME,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR2_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_principal,
    demo_user_id,
)

# --- The finished course (#353 C2) ----------------------------------------------------

TEMPLATE_NAME = "Álgebra intensiva"
TEMPLATE_DESCRIPTION = "Repaso de ecuaciones y sistemas de ecuaciones para secundaria."
SESSION_COUNT = 6
SESSION_MINUTES = 90
MIN_ATTENDANCE_PERCENT = 80
MODULES: tuple[tuple[str, int, str | None], ...] = (
    ("Ecuaciones lineales", 3, "Cuaderno de ejercicios"),
    ("Sistemas de ecuaciones", 3, None),
)
"""Title, planned sessions and materials; Matemáticas (Secundaria), all
required (#353 C2)."""
ASSESSMENTS: tuple[tuple[str, bool, int | None], ...] = (
    ("Examen parcial", False, None),
    ("Examen final", True, 60),
)
"""Title, required and minimum score; both for the whole course (#353 C2)."""
COHORT_MAX_STUDENTS = 6
ENROLLED: tuple[StudentKey, ...] = ("S", "A", "B")

DEMO_PAST_COHORT_TABLES = (
    *DEMO_COURSES_TABLES,
    ClassSessionAttendance,
    SessionCloseout,
    SessionCloseoutApproval,
)
"""The tables whose added rows ``courses.demo_past_cohort`` counts (#353 C):
#303 C's course and scheduling tables, the attendance and the closeouts."""


def _settings_app_env() -> str:
    return get_settings().app_env


class DemoPastCohortGenerator:
    """``courses.demo_past_cohort`` (#353 C): the course template
    ``Álgebra intensiva`` and its finished cohort ``Álgebra intensiva, grupo
    1`` (``demo.tutor2``, ``Aula 2``), whose series offer ``demo.tutor2``
    accepts, with S, A and B enrolled; every class held, closed out by
    ``demo.tutor2`` and approved by ``demo.coordinator``.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once the template exists, in any state. Skipped when #299's schedule is
    from an earlier day; raises when a demo account, a #195 student, the
    demo subject, ``Aula 2``, #299's schedule or ``demo.tutor2``'s
    qualification is missing.
    """

    name = "courses.demo_past_cohort"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoAcademicsPlan] = plan_demo_academics,
    ) -> None:
        self._app_env = app_env
        self._plan = plan

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        owner_id = await demo_user_id(session, DEMO_OWNER_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        tutor2 = await demo_principal(session, DEMO_TUTOR2_USERNAME, self.name)
        reviewer = await demo_principal(session, DEMO_COORDINATOR_USERNAME, self.name)
        if await session.scalar(
            select(CourseTemplate.id).where(CourseTemplate.name == TEMPLATE_NAME)
        ):
            return 0
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if not await schedule_from_today(session, tutor_id, students["S"], self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        subjects = await demo_subject_ids(session, (MATH_SECONDARY,), self.name)
        aula_2 = await classroom_id(session, AULA_2, self.name)
        if not await session.scalar(
            select(TutorQualification.id)
            .where(TutorQualification.tutor_user_id == tutor2.user_id)
            .where(TutorQualification.subject_id == subjects[MATH_SECONDARY])
            .where(TutorQualification.status == "approved")
        ):
            raise DemoPrerequisiteMissingError(
                f"the staff.demo_tutor2 qualifications must exist before {self.name}"
            )
        zone = await administration.get_center_time_zone(session)
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_PAST_COHORT_TABLES)

        with seed_clock(local_instant(plan.created_on, plan.created_at, zone)):
            cohort_id = await self._create(
                session,
                plan,
                owner_id,
                tutor2.user_id,
                subjects[MATH_SECONDARY],
                aula_2,
            )
            # C2: S, A, then B, as the Owner.
            run = cast(UnitOfWork, _RunSession(session))
            for key in ENROLLED:
                await cohort_enrollment.enroll_in_cohort(
                    run,
                    cohort_id,
                    CohortEnrollmentCreate(student_id=students[key]),
                    actor_id=owner_id,
                )

        # C3: each class in date order, closed out and approved.
        rows = await session.execute(
            select(ClassSession.starts_at, ClassSession.id).where(
                ClassSession.cohort_id == cohort_id
            )
        )
        sessions = {row.starts_at: row.id for row in rows}
        for planned in plan.cohort:
            starts_at = local_instant(planned.day, planned.start, zone)
            class_session_id = sessions.get(starts_at)
            if class_session_id is None:
                raise DemoPrerequisiteMissingError(
                    f"{self.name}: no cohort class at {starts_at} (the plan has a bug)"
                )
            await hold_class(
                session,
                class_session_id=class_session_id,
                planned=planned,
                students=students,
                zone=zone,
                tutor=tutor2,
                reviewer=reviewer,
            )
        return await count_rows(session, DEMO_PAST_COHORT_TABLES) - before

    async def _create(
        self,
        session: AsyncSession,
        plan: DemoAcademicsPlan,
        owner_id: uuid.UUID,
        tutor2_id: uuid.UUID,
        subject_id: uuid.UUID,
        aula_2: uuid.UUID,
    ) -> uuid.UUID:
        """C2: the template, the cohort (as the Owner) and ``demo.tutor2``'s
        answer to its series offer."""
        template = await courses.create_course_template(
            session,
            CourseTemplateWrite(
                name=TEMPLATE_NAME,
                description=TEMPLATE_DESCRIPTION,
                subject_ids=[subject_id],
                session_count=SESSION_COUNT,
                session_minutes=SESSION_MINUTES,
                min_attendance_percent=MIN_ATTENDANCE_PERCENT,
                modules=[
                    CourseTemplateModuleWrite(
                        title=title,
                        subject_id=subject_id,
                        planned_sessions=planned,
                        is_required=True,
                        materials=materials,
                    )
                    for title, planned, materials in MODULES
                ],
                assessments=[
                    CourseTemplateAssessmentWrite(
                        title=title, is_required=required, min_score_percent=score
                    )
                    for title, required, score in ASSESSMENTS
                ],
            ),
        )
        cohort_id = await cohorts.create_cohort(
            session,
            CohortCreate(
                name=COHORT_NAME,
                template_id=template.id,
                subject_id=subject_id,
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
        return cohort_id
