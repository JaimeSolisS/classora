"""The demo tutors: the ``staff.demo_tutor`` (#299 C) and
``staff.demo_tutor2`` (#303 B) generators.

``demo.tutor``'s profile, qualifications (three approved, one pending),
weekly availability, one day off, limits and a preferred classroom; and
``demo.tutor2``'s two approved qualifications and weekly availability. All
written through the staff and approval services (#299 G1, #303 G1) with
principals from ``identity.service.load_principal``. Fixed ``es_MX`` text, no
Faker.
"""

import uuid
from collections.abc import Callable
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.config import get_settings
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.catalog.models import Classroom, EducationLevel, Subject
from app.modules.catalog.schemas import AvailabilityWindow
from app.modules.identity import service as identity
from app.modules.staff import service as staff
from app.modules.staff.assignment_impact import registered_availability_impact_source
from app.modules.staff.models import (
    TutorAvailabilityException,
    TutorAvailabilitySettings,
    TutorAvailabilityWindow,
    TutorPreferredClassroom,
    TutorProfile,
    TutorQualification,
)
from app.modules.staff.schemas import (
    AvailabilityExceptionCreate,
    AvailabilitySettingsUpdate,
    TutorAvailabilityDetail,
    TutorUpdate,
    WeeklyAvailabilityUpdate,
)
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_changes import demo_subject_ids
from tests.factories.demo_schedule import (
    AULA_1,
    CIENCIAS_SECONDARY,
    ENGLISH_SECONDARY,
    MATH_PRIMARY,
    MATH_SECONDARY,
    DemoSchedulePlan,
    SubjectKey,
    plan_demo_schedule,
)
from tests.factories.identity import (
    DEMO_ACCOUNT_APP_ENVS,
    DEMO_OWNER_USERNAME,
    DEMO_TUTOR2_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_principal,
    demo_user_id,
)

PROFESSIONAL_EXPERIENCE = (
    "Docente de matemáticas e inglés con experiencia en secundaria."
)

APPROVED_QUALIFICATIONS: tuple[SubjectKey, ...] = (
    MATH_PRIMARY,
    MATH_SECONDARY,
    ENGLISH_SECONDARY,
)
PENDING_QUALIFICATION: SubjectKey = ("Física", "Preparatoria")

TUTOR_WINDOWS: tuple[tuple[int, str, str], ...] = (
    *((day, "15:00", "20:00") for day in (1, 2, 3, 4, 5)),
    (6, "09:00", "13:00"),
)
"""``demo.tutor``'s weekly windows (ISO weekday, ``HH:MM``, ``HH:MM``)."""

DAILY_LIMIT_MINUTES = 240
WEEKLY_LIMIT_MINUTES = 1200
REQUIRED_BREAK_MINUTES = 10

DEMO_TUTOR_TABLES = (
    TutorProfile,
    TutorQualification,
    ApprovalRequest,
    Notification,
    TutorAvailabilitySettings,
    TutorAvailabilityWindow,
    TutorAvailabilityException,
    TutorPreferredClassroom,
)
"""The tables whose added rows ``staff.demo_tutor`` counts (#299 C)."""


def _settings_app_env() -> str:
    return get_settings().app_env


class DemoTutorGenerator:
    """``staff.demo_tutor`` (#299 C): ``demo.tutor``'s profile, four
    qualification requests (three approved by ``demo.owner``, Física
    pending), weekly availability, a whole-day time off on the plan's
    ``time_off_day``, limits and ``Aula 1`` as the preferred classroom.

    Runs before any demo session exists, so no availability change becomes a
    change request. Writes nothing outside ``APP_ENV=local`` (skipped), and
    nothing (0 rows) once ``demo.tutor`` has a profile or any qualification.
    Raises when ``demo.owner``, ``demo.tutor``, a demo subject or ``Aula 1``
    is missing.
    """

    name = "staff.demo_tutor"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoSchedulePlan] = plan_demo_schedule,
    ) -> None:
        self._app_env = app_env
        self._plan = plan

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        owner = await self._principal(session, DEMO_OWNER_USERNAME)
        tutor = await self._principal(session, DEMO_TUTOR_USERNAME)
        if await _already_set_up(session, tutor.user_id):
            return 0
        subjects = await self._subjects(session)
        aula_1 = await session.scalar(
            select(Classroom.id).where(Classroom.name == AULA_1)
        )
        if aula_1 is None:
            raise DemoPrerequisiteMissingError(
                f"the {AULA_1} classroom must exist before {self.name}"
            )
        before = await count_rows(session, DEMO_TUTOR_TABLES)
        plan = self._plan(await administration.center_today(session))

        # C2: the profile (version 0: none yet), as the Owner.
        await staff.update_tutor(
            session,
            principal=owner,
            user_id=tutor.user_id,
            body=TutorUpdate(
                version=0, professional_experience=PROFESSIONAL_EXPERIENCE
            ),
        )

        # C3: requested by the tutor; three approved by the Owner.
        for key in (*APPROVED_QUALIFICATIONS, PENDING_QUALIFICATION):
            item = await staff.request_qualification(
                session,
                principal=tutor,
                user_id=tutor.user_id,
                subject_id=subjects[key],
            )
            if key == PENDING_QUALIFICATION:
                continue
            request_id = await session.scalar(
                select(ApprovalRequest.id).where(ApprovalRequest.subject_id == item.id)
            )
            assert request_id is not None
            await administration.decide_approval_request(
                session, principal=owner, request_id=request_id, action="approve"
            )

        # C4: availability before any session, as the Owner.
        impact = registered_availability_impact_source()
        weekly = await staff.replace_weekly_availability(
            session,
            principal=owner,
            user_id=tutor.user_id,
            body=WeeklyAvailabilityUpdate(
                version=0,
                windows=[
                    AvailabilityWindow(weekday=day, starts_at=start, ends_at=end)
                    for day, start, end in TUTOR_WINDOWS
                ],
            ),
            impact=impact,
        )
        if not isinstance(weekly, TutorAvailabilityDetail):
            raise DemoPrerequisiteMissingError(
                f"{self.name} must run before any demo session exists"
            )
        await staff.create_exception(
            session,
            principal=owner,
            user_id=tutor.user_id,
            body=AvailabilityExceptionCreate(
                kind="unavailable",
                starts_on=plan.time_off_day,
                ends_on=plan.time_off_day,
            ),
            impact=impact,
        )
        await staff.update_availability_settings(
            session,
            principal=owner,
            user_id=tutor.user_id,
            body=AvailabilitySettingsUpdate(
                version=weekly.version,
                daily_limit_minutes=DAILY_LIMIT_MINUTES,
                weekly_limit_minutes=WEEKLY_LIMIT_MINUTES,
                required_break_minutes=REQUIRED_BREAK_MINUTES,
                preferred_classroom_ids=[aula_1],
            ),
        )
        return await count_rows(session, DEMO_TUTOR_TABLES) - before

    async def _principal(self, session: AsyncSession, username: str) -> Principal:
        user_id = await demo_user_id(session, username, self.name)
        principal = await identity.load_principal(session, user_id)
        if principal is None:
            raise DemoPrerequisiteMissingError(
                f"{username} must be active before {self.name}"
            )
        return principal

    async def _subjects(self, session: AsyncSession) -> dict[SubjectKey, uuid.UUID]:
        rows = await session.execute(
            select(Subject.name, EducationLevel.name, Subject.id).join(
                EducationLevel, EducationLevel.id == Subject.education_level_id
            )
        )
        found = {(name, level): subject_id for name, level, subject_id in rows}
        wanted = (*APPROVED_QUALIFICATIONS, PENDING_QUALIFICATION)
        if any(key not in found for key in wanted):
            raise DemoPrerequisiteMissingError(
                f"the catalog.demo_catalog subjects must exist before {self.name}"
            )
        return {key: found[key] for key in wanted}


async def _already_set_up(session: AsyncSession, tutor_id: uuid.UUID) -> bool:
    """The marker (#299 C1): a profile or any qualification of ``demo.tutor``."""
    profile = await session.scalar(
        select(TutorProfile.id).where(TutorProfile.user_id == tutor_id)
    )
    qualification = await session.scalar(
        select(TutorQualification.id)
        .where(TutorQualification.tutor_user_id == tutor_id)
        .limit(1)
    )
    return profile is not None or qualification is not None


# --- demo.tutor2 (#303 B) ---------------------------------------------------------

TUTOR2_QUALIFICATIONS: tuple[SubjectKey, ...] = (MATH_SECONDARY, CIENCIAS_SECONDARY)
"""``demo.tutor2``'s qualifications, both approved by ``demo.owner`` (#303 B2)."""

TUTOR2_WINDOWS: tuple[tuple[int, str, str], ...] = tuple(
    (day, "15:00", "20:00") for day in (1, 2, 3, 4, 5)
)
"""``demo.tutor2``'s weekly windows, Monday-Friday 15:00-20:00 (#303 B3)."""

DEMO_TUTOR2_TABLES = (
    TutorQualification,
    ApprovalRequest,
    Notification,
    TutorAvailabilitySettings,
    TutorAvailabilityWindow,
)
"""The tables whose added rows ``staff.demo_tutor2`` counts (#303 B)."""


class DemoSecondTutorGenerator:
    """``staff.demo_tutor2`` (#303 B): ``demo.tutor2``'s two qualifications
    (Matemáticas and Ciencias, Secundaria), each requested by the tutor and
    approved by ``demo.owner``, and its weekly availability (Monday-Friday
    15:00-20:00, set by the Owner). No profile, exception, limits or
    preferred classroom.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once ``demo.tutor2`` has any qualification or an availability settings
    row. Raises when ``demo.owner``, ``demo.tutor2`` or a demo subject is
    missing. It writes no dates, so it also runs on a database whose #299
    schedule is from an earlier day.
    """

    name = "staff.demo_tutor2"

    def __init__(self, app_env: Callable[[], str] = _settings_app_env) -> None:
        self._app_env = app_env

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        owner = await demo_principal(session, DEMO_OWNER_USERNAME, self.name)
        tutor = await demo_principal(session, DEMO_TUTOR2_USERNAME, self.name)
        if await _tutor2_set_up(session, tutor.user_id):
            return 0
        subjects = await demo_subject_ids(session, TUTOR2_QUALIFICATIONS, self.name)
        before = await count_rows(session, DEMO_TUTOR2_TABLES)

        # B2: requested by the tutor, approved by the Owner.
        for key in TUTOR2_QUALIFICATIONS:
            item = await staff.request_qualification(
                session,
                principal=tutor,
                user_id=tutor.user_id,
                subject_id=subjects[key],
            )
            request_id = await session.scalar(
                select(ApprovalRequest.id).where(ApprovalRequest.subject_id == item.id)
            )
            assert request_id is not None
            await administration.decide_approval_request(
                session, principal=owner, request_id=request_id, action="approve"
            )

        # B3: the weekly windows (version 0: no settings row yet), as the Owner.
        weekly = await staff.replace_weekly_availability(
            session,
            principal=owner,
            user_id=tutor.user_id,
            body=WeeklyAvailabilityUpdate(
                version=0,
                windows=[
                    AvailabilityWindow(weekday=day, starts_at=start, ends_at=end)
                    for day, start, end in TUTOR2_WINDOWS
                ],
            ),
            impact=registered_availability_impact_source(),
        )
        if not isinstance(weekly, TutorAvailabilityDetail):
            raise DemoPrerequisiteMissingError(
                f"{self.name} must run before {DEMO_TUTOR2_USERNAME} teaches"
            )
        return await count_rows(session, DEMO_TUTOR2_TABLES) - before


async def _tutor2_set_up(session: AsyncSession, tutor_id: uuid.UUID) -> bool:
    """The marker (#303 B1): any qualification or an availability settings
    row of ``demo.tutor2``."""
    qualification = await session.scalar(
        select(TutorQualification.id)
        .where(TutorQualification.tutor_user_id == tutor_id)
        .limit(1)
    )
    settings = await session.scalar(
        select(TutorAvailabilitySettings.id).where(
            TutorAvailabilitySettings.user_id == tutor_id
        )
    )
    return qualification is not None or settings is not None
