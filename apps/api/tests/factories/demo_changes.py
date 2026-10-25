"""The #303 demo data on top of the #299 schedule: :func:`plan_demo_changes`
and the ``scheduling.demo_changes`` generator.

**The plan (#303 G2, A4)** is a pure function of ``T``, the center's today,
built on #299's (:func:`~tests.factories.demo_schedule.plan_demo_schedule`,
with its ``D(k)`` and ``Sat1``). It is the single source of every #303 date
and time: the cohort of ``courses.demo_courses`` and the changes below.
Every slot is free of #299's sessions for any ``T``, because ``D(k + 5) =
D(k) + 7`` and ``D(1)`` … ``D(5)`` fall on five different weekdays:

- the cohort: weekly on the weekdays of ``D(2)`` and ``D(4)``, from ``D(2)``,
  15:00-16:30 (8 sessions, to ``D(19)``), never on the closure ``D(3)``;
- the substitution: #299's series occurrence on ``D(8)`` (``D(3) + 7``);
- the cancelled class: the cohort session on ``D(7)``;
- the session request: ``D(6)`` 16:00, 60 minutes;
- the reschedule request: #299's ``U1`` (``D(1)`` 16:00) to ``D(1)`` 18:00;
- the time off: ``demo.tutor`` on ``Sat1 + 7``, a group occurrence.

**The generator** writes through the services the routes call (#303 G1):
the substitution as ``demo.owner`` then accepted by ``demo.tutor2``, the
cancellation as ``demo.owner``, the session request and the reschedule
request as ``demo.guardian``, the time off as ``demo.tutor``. No override:
a refusal raises and rolls the whole run back.

**A schedule from an earlier day (#303 G3).** The course and change
generators need #299's sessions at today's plan instants. When ``demo.tutor``
teaches no session, the #299 schedule is missing (raise). When ``U1`` is not
at today's instant, the schedule is from an earlier day: they write nothing
and print ``skipped (demo schedule from an earlier day; reset to add it)``.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.catalog.models import EducationLevel, Subject
from app.modules.courses.models import CourseCohort
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.change_request_schemas import ChangeRequestCreate
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSession,
    ClassSessionParticipant,
    ClassSessionSubstitution,
    SessionChangeRequest,
    SessionRequest,
)
from app.modules.scheduling.request_schemas import SessionRequestCreate
from app.modules.scheduling.schemas import SubstitutionCreate
from app.modules.staff import service as staff
from app.modules.staff.assignment_impact import registered_availability_impact_source
from app.modules.staff.models import TutorAvailabilityChangeRequest
from app.modules.staff.schemas import (
    AvailabilityChangeRequestItem,
    AvailabilityExceptionCreate,
)
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_schedule import (
    CIENCIAS_SECONDARY,
    DemoScheduleWarningError,
    demo_students,
    first_saturday,
    local_instant,
    plan_demo_schedule,
    upcoming_weekdays,
)
from tests.factories.identity import (
    DEMO_ACCOUNT_APP_ENVS,
    DEMO_GUARDIAN_USERNAME,
    DEMO_OWNER_USERNAME,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR2_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_principal,
    demo_user_id,
)

# --- Fixed es_MX texts (#303 A3) ------------------------------------------------------

COHORT_NAME = "Examen de ingreso, grupo 1"
SUBSTITUTION_REASON = "El tutor asiste a una capacitación ese día."
CANCELLATION_REASON = "Junta de padres de familia en el centro."
SESSION_REQUEST_NOTE = "Necesita repasar para su examen de ciencias."
CHANGE_REQUEST_NOTE = "¿Podemos pasarla a las 18:00? Tenemos una cita médica."
TIME_OFF_NOTE = "Compromiso familiar"

STALE_SCHEDULE = "demo schedule from an earlier day; reset to add it"
"""The skip reason of the course and change generators (#303 G3)."""

COHORT_SESSIONS = 8
COHORT_MINUTES = 90


# --- The plan (pure) ------------------------------------------------------------------


@dataclass(frozen=True)
class DemoChangesPlan:
    """Every #303 date and time for one ``today``: local dates and ``HH:MM``
    times."""

    today: date
    cohort_starts_on: date
    cohort_weekdays: tuple[int, ...]
    cohort_start: str
    cohort_end: str
    cohort_dates: tuple[date, ...]
    """The cohort rule's first :data:`COHORT_SESSIONS` dates (no closure
    among them)."""
    cancelled_day: date
    substitution_day: date
    substitution_start: str
    substitution_end: str
    request_day: date
    request_start: str
    request_minutes: int
    reschedule_day: date
    reschedule_from: str
    reschedule_to: str
    time_off_day: date


def _rule_dates(starts_on: date, weekdays: tuple[int, ...], n: int) -> list[date]:
    days: list[date] = []
    day = starts_on
    while len(days) < n:
        if day.isoweekday() in weekdays:
            days.append(day)
        day += timedelta(days=1)
    return days


def plan_demo_changes(today: date) -> DemoChangesPlan:
    """The #303 plan for the center's ``today`` (#303 G2)."""
    d = upcoming_weekdays(today, 8)
    schedule = plan_demo_schedule(today)
    u1 = schedule.upcoming[0]
    series = schedule.series
    weekdays = tuple(sorted({d[1].isoweekday(), d[3].isoweekday()}))
    cohort_dates = tuple(_rule_dates(d[1], weekdays, COHORT_SESSIONS))
    return DemoChangesPlan(
        today=today,
        cohort_starts_on=d[1],
        cohort_weekdays=weekdays,
        cohort_start="15:00",
        cohort_end="16:30",
        cohort_dates=cohort_dates,
        cancelled_day=d[6],
        substitution_day=series.dates()[1],
        substitution_start=series.start,
        substitution_end=series.end,
        request_day=d[5],
        request_start="16:00",
        request_minutes=60,
        reschedule_day=u1.day,
        reschedule_from=u1.start,
        reschedule_to="18:00",
        time_off_day=first_saturday(today) + timedelta(days=7),
    )


# --- Shared by the course and change generators ---------------------------------------


def _settings_app_env() -> str:
    return get_settings().app_env


async def schedule_from_today(
    session: AsyncSession, tutor_id: uuid.UUID, s_id: uuid.UUID, generator: str
) -> bool:
    """Whether #299's schedule is from today's plan (#303 G3): ``U1`` (taught
    by ``demo.tutor``, with S) starts at today's instant. Raises
    :class:`DemoPrerequisiteMissingError` naming ``generator`` when
    ``demo.tutor`` teaches no session at all."""
    any_session = await session.scalar(
        select(ClassSession.id).where(ClassSession.tutor_user_id == tutor_id).limit(1)
    )
    if any_session is None:
        raise DemoPrerequisiteMissingError(
            f"the scheduling.demo_schedule sessions must exist before {generator}"
        )
    zone = await administration.get_center_time_zone(session)
    u1 = plan_demo_schedule(await administration.center_today(session)).upcoming[0]
    found = await session.scalar(
        select(ClassSession.id)
        .join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
        .where(ClassSession.tutor_user_id == tutor_id)
        .where(ClassSession.starts_at == local_instant(u1.day, u1.start, zone))
        .where(ClassSessionParticipant.student_id == s_id)
    )
    return found is not None


async def demo_subject_ids(
    session: AsyncSession, wanted: tuple[tuple[str, str], ...], generator: str
) -> dict[tuple[str, str], uuid.UUID]:
    """The ids of the demo subjects ``wanted`` (name, level name)."""
    rows = await session.execute(
        select(Subject.name, EducationLevel.name, Subject.id).join(
            EducationLevel, EducationLevel.id == Subject.education_level_id
        )
    )
    found = {(name, level): subject_id for name, level, subject_id in rows}
    if any(key not in found for key in wanted):
        raise DemoPrerequisiteMissingError(
            f"the catalog.demo_catalog subjects must exist before {generator}"
        )
    return {key: found[key] for key in wanted}


def no_warnings(key: str, warnings: list[object]) -> None:
    """A workload warning means the plan has a bug (#303 D3)."""
    if warnings:
        raise DemoScheduleWarningError(f"{key}: {warnings}")


# --- The generator --------------------------------------------------------------------

DEMO_CHANGES_TABLES = (
    ClassSessionSubstitution,
    AssignmentOffer,
    AssignmentOfferSession,
    SessionRequest,
    SessionChangeRequest,
    TutorAvailabilityChangeRequest,
    ApprovalRequest,
    Notification,
)
"""The tables whose added rows ``scheduling.demo_changes`` counts (#303 D)."""


class DemoChangesGenerator:
    """``scheduling.demo_changes`` (#303 D): an accepted substitution of a
    ``demo.tutor`` class by ``demo.tutor2``, a cancelled cohort class,
    ``demo.guardian``'s pending session request and reschedule request, and
    ``demo.tutor``'s pending time off, dated by :func:`plan_demo_changes`.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once a ``demo.tutor`` session has a substitution or ``demo.guardian`` made
    a session request. Skipped when #299's schedule is from an earlier day;
    raises when a demo account, a #195 student, a demo subject, #299's
    schedule or the demo cohort is missing.
    """

    name = "scheduling.demo_changes"

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
        tutor = await demo_principal(session, DEMO_TUTOR_USERNAME, self.name)
        tutor2_id = await demo_user_id(session, DEMO_TUTOR2_USERNAME, self.name)
        guardian = await demo_principal(session, DEMO_GUARDIAN_USERNAME, self.name)
        if await _changes_made(session, tutor.user_id, guardian.user_id):
            return 0
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        cohort_id = await session.scalar(
            select(CourseCohort.id).where(CourseCohort.name == COHORT_NAME)
        )
        if cohort_id is None:
            raise DemoPrerequisiteMissingError(
                f"the courses.demo_courses cohort must exist before {self.name}"
            )
        subjects = await demo_subject_ids(session, (CIENCIAS_SECONDARY,), self.name)
        zone = await administration.get_center_time_zone(session)
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_CHANGES_TABLES)

        # D3: the substitution of the D(8) series occurrence, then accepted.
        d8_id, d8_version = await _session_at(
            session,
            local_instant(plan.substitution_day, plan.substitution_start, zone),
            ClassSession.tutor_user_id == tutor.user_id,
            self.name,
        )
        created = await scheduling.substitute_class_session(
            session,
            d8_id,
            SubstitutionCreate(
                substitute_tutor_user_id=tutor2_id,
                version=d8_version,
                reason=SUBSTITUTION_REASON,
            ),
            actor_id=owner_id,
        )
        no_warnings("substitution", list(created.warnings))
        await scheduling.answer_offer(
            session, created.substitution.offer_id, caller_id=tutor2_id, accept=True
        )

        # D4: the cohort session of D(7) cancelled.
        cancelled_id, cancelled_version = await _session_at(
            session,
            local_instant(plan.cancelled_day, plan.cohort_start, zone),
            ClassSession.cohort_id == cohort_id,
            self.name,
        )
        await scheduling.cancel_class_session(
            session,
            cancelled_id,
            expected_version=cancelled_version,
            reason=CANCELLATION_REASON,
            by_user_id=owner_id,
        )

        # D5: the guardian's session request for S.
        await scheduling.create_session_request(
            session,
            guardian,
            SessionRequestCreate(
                student_id=students["S"],
                subject_id=subjects[CIENCIAS_SECONDARY],
                starts_at=local_instant(plan.request_day, plan.request_start, zone),
                duration_minutes=plan.request_minutes,  # type: ignore[arg-type]
                note=SESSION_REQUEST_NOTE,
            ),
        )

        # D6: the guardian's reschedule request for U1.
        u1_id, _ = await _session_at(
            session,
            local_instant(plan.reschedule_day, plan.reschedule_from, zone),
            ClassSession.tutor_user_id == tutor.user_id,
            self.name,
        )
        await scheduling.create_change_request(
            session,
            guardian,
            ChangeRequestCreate(
                class_session_id=u1_id,
                kind="reschedule",
                proposed_starts_at=local_instant(
                    plan.reschedule_day, plan.reschedule_to, zone
                ),
                note=CHANGE_REQUEST_NOTE,
            ),
        )

        # D7: demo.tutor's time off over a group occurrence: a pending change.
        result = await staff.create_exception(
            session,
            principal=tutor,
            user_id=tutor.user_id,
            body=AvailabilityExceptionCreate(
                kind="unavailable",
                starts_on=plan.time_off_day,
                ends_on=plan.time_off_day,
                note=TIME_OFF_NOTE,
            ),
            impact=registered_availability_impact_source(),
        )
        if not isinstance(result, AvailabilityChangeRequestItem):
            raise DemoScheduleWarningError(
                f"{self.name}: the time off on {plan.time_off_day} affects no session"
            )
        return await count_rows(session, DEMO_CHANGES_TABLES) - before


async def _changes_made(
    session: AsyncSession, tutor_id: uuid.UUID, guardian_id: uuid.UUID
) -> bool:
    """The marker (#303 D1): a substitution of a ``demo.tutor`` session, or a
    session request made by ``demo.guardian``."""
    substitution = await session.scalar(
        select(ClassSessionSubstitution.id)
        .where(ClassSessionSubstitution.original_tutor_user_id == tutor_id)
        .limit(1)
    )
    request = await session.scalar(
        select(SessionRequest.id)
        .where(SessionRequest.requested_by_user_id == guardian_id)
        .limit(1)
    )
    return substitution is not None or request is not None


async def _session_at(
    session: AsyncSession,
    starts_at: datetime,
    condition: ColumnElement[bool],
    generator: str,
) -> tuple[uuid.UUID, int]:
    """The id and current version of the session starting at ``starts_at``
    that matches ``condition``."""
    found = (
        await session.execute(
            select(ClassSession.id, ClassSession.version)
            .where(ClassSession.starts_at == starts_at)
            .where(condition)
        )
    ).first()
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"the demo session at {starts_at} must exist before {generator}"
        )
    return found[0], found[1]
