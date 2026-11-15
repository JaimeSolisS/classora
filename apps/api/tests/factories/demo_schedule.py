"""The demo schedule (#299 D): :func:`plan_demo_schedule` and the
``scheduling.demo_schedule`` generator.

**The plan (#299 G2, A5)** is a pure function of ``T``, the center's today
(``administration.service.center_today``): local dates and ``HH:MM`` times,
no database. It is the single source of every date and time the scheduling
generator uses, and of the closure date of ``catalog.demo_catalog`` and the
time-off day of ``staff.demo_tutor``.

- ``D(k)``: the k-th Monday-Friday on or after ``T + 2``, so every upcoming
  start is more than 24 hours away (the default booking notice) at any time
  of day.
- ``P(k)``: the k-th Monday-Friday strictly before ``T``.
- ``Sat1``: the first Saturday on or after ``T + 2``.
- The one closure is ``D(3)`` (always in ``T + 4 … T + 6``); the tutor's
  time-off day is ``D(4) + 7``, a day with no demo session.

**The generator** writes the four past ``completed`` sessions directly with
the scheduling factory (#299 E3: no service creates a session that has
started), and everything upcoming through the domain services the routes
call, as the ``demo.owner`` actor, with the ``demo.tutor`` principal
answering the offers (#299 G1). No override is used anywhere: a conflict
raises and rolls the whole run back. Instants come from the plan's local
dates and times in the center zone read from the settings (ADR-0004).
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.catalog.models import Classroom, EducationLevel, Subject
from app.modules.families.models import (
    FamilyStudent,
    Student,
    StudentGuardian,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassGroup,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
    ClassSessionReassignmentFlag,
    GroupEnrollment,
)
from app.modules.scheduling.schemas import (
    ClassSeriesCreate,
    ClassSessionCreate,
    GroupCreate,
    SeriesRecurrence,
)
from app.modules.scheduling.service import SessionState
from app.workflows import group_enrollment
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.identity import (
    DEMO_ACCOUNT_APP_ENVS,
    DEMO_OWNER_USERNAME,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_user_id,
)
from tests.factories.scheduling import add_participant, persist_class_session

# --- Names shared with the catalog and staff generators -------------------------------

AULA_1 = "Aula 1"
AULA_2 = "Aula 2"
SALA = "Sala de usos múltiples"

MATH_PRIMARY = ("Matemáticas", "Primaria")
SPANISH_PRIMARY = ("Español", "Primaria")
MATH_SECONDARY = ("Matemáticas", "Secundaria")
ENGLISH_SECONDARY = ("Inglés", "Secundaria")
CIENCIAS_SECONDARY = ("Ciencias", "Secundaria")

GROUP_NAME = "Club de Matemáticas"
DECLINE_NOTE = "No disponible ese día"

type SubjectKey = tuple[str, str]
"""A demo subject: its name and its level's name."""

type StudentKey = Literal["S", "Sib", "A", "B"]
"""The #195 students the schedule uses (#299 D2)."""

type Outcome = Literal["accept", "offered", "decline", "unassigned"]


# --- The plan (pure) ------------------------------------------------------------------


def _weekday(day: date) -> bool:
    return day.isoweekday() <= 5


def upcoming_weekdays(today: date, n: int) -> list[date]:
    """``D(1)`` … ``D(n)``: the first ``n`` Monday-Fridays on or after
    ``today + 2``."""
    days: list[date] = []
    day = today + timedelta(days=2)
    while len(days) < n:
        if _weekday(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def past_weekdays(today: date, n: int) -> list[date]:
    """``P(1)`` … ``P(n)``: the ``n`` Monday-Fridays before ``today``, the
    nearest first."""
    days: list[date] = []
    day = today - timedelta(days=1)
    while len(days) < n:
        if _weekday(day):
            days.append(day)
        day -= timedelta(days=1)
    return days


def first_saturday(today: date) -> date:
    """``Sat1``: the first Saturday on or after ``today + 2``."""
    day = today + timedelta(days=2)
    return day + timedelta(days=(6 - day.isoweekday()) % 7)


def school_year_start(today: date) -> int:
    """``Y``: the school year ``Y``-``Y+1`` (1 August to 31 July) contains
    ``today``."""
    return today.year if (today.month, today.day) >= (8, 1) else today.year - 1


@dataclass(frozen=True)
class PlannedSession:
    """One one-off session of the plan: local date and ``HH:MM`` times."""

    key: str
    student: StudentKey
    subject: SubjectKey
    tutor: bool
    classroom: str | None
    day: date
    start: str
    end: str
    outcome: Outcome | None = None
    """What happens after the create (upcoming only; ``None`` for the past
    ``completed`` sessions)."""


@dataclass(frozen=True)
class PlannedRecurrence:
    """A weekly rule of the plan (the series, or the group's)."""

    subject: SubjectKey
    classroom: str
    weekday: int
    starts_on: date
    start: str
    duration_minutes: int
    count: int
    students: tuple[StudentKey, ...]

    def dates(self) -> list[date]:
        """The rule's ``count`` local dates (a closure date included)."""
        offset = (self.weekday - self.starts_on.isoweekday()) % 7
        first = self.starts_on + timedelta(days=offset)
        return [first + timedelta(weeks=k) for k in range(self.count)]

    @property
    def end(self) -> str:
        start = datetime.combine(date.min, time.fromisoformat(self.start))
        return (start + timedelta(minutes=self.duration_minutes)).strftime("%H:%M")


@dataclass(frozen=True)
class DemoSchedulePlan:
    """Every date and time of the demo data, for one ``today``."""

    today: date
    past: tuple[PlannedSession, ...]
    upcoming: tuple[PlannedSession, ...]
    series: PlannedRecurrence
    group: PlannedRecurrence
    closure_day: date
    time_off_day: date
    period_start_year: int


def plan_demo_schedule(today: date) -> DemoSchedulePlan:
    """The demo plan for the center's ``today`` (#299 G2, D3-D6)."""
    d = upcoming_weekdays(today, 5)
    p = past_weekdays(today, 4)
    past = (
        PlannedSession("P1", "S", MATH_SECONDARY, True, AULA_1, p[0], "16:00", "17:00"),
        PlannedSession("P2", "Sib", MATH_PRIMARY, True, AULA_1, p[1], "16:00", "17:00"),
        PlannedSession("P3", "S", MATH_SECONDARY, True, AULA_1, p[2], "16:00", "17:00"),
        PlannedSession(
            "P4", "S", ENGLISH_SECONDARY, True, AULA_1, p[3], "17:00", "18:00"
        ),
    )
    upcoming = (
        PlannedSession(
            "U1", "S", MATH_SECONDARY, True, AULA_1, d[0], "16:00", "17:00", "accept"
        ),
        PlannedSession(
            "U2", "Sib", MATH_PRIMARY, True, AULA_1, d[1], "16:00", "17:00", "accept"
        ),
        PlannedSession(
            "U3", "S", ENGLISH_SECONDARY, True, None, d[1], "18:00", "19:00", "offered"
        ),
        PlannedSession(
            "U4", "A", MATH_SECONDARY, True, AULA_2, d[3], "17:00", "18:00", "decline"
        ),
        PlannedSession(
            "U5",
            "B",
            SPANISH_PRIMARY,
            False,
            AULA_2,
            d[4],
            "15:00",
            "16:00",
            "unassigned",
        ),
    )
    series = PlannedRecurrence(
        subject=MATH_SECONDARY,
        classroom=SALA,
        weekday=d[2].isoweekday(),
        starts_on=d[0],
        start="17:00",
        duration_minutes=60,
        count=8,
        students=("S",),
    )
    group = PlannedRecurrence(
        subject=MATH_SECONDARY,
        classroom=SALA,
        weekday=6,
        starts_on=first_saturday(today),
        start="10:00",
        duration_minutes=90,
        count=8,
        students=("S", "Sib", "A"),
    )
    return DemoSchedulePlan(
        today=today,
        past=past,
        upcoming=upcoming,
        series=series,
        group=group,
        closure_day=d[2],
        time_off_day=d[3] + timedelta(days=7),
        period_start_year=school_year_start(today),
    )


def local_instant(day: date, hhmm: str, zone: str) -> datetime:
    """The instant of the local ``day`` and ``HH:MM`` in ``zone``."""
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=ZoneInfo(zone))


# --- The generator --------------------------------------------------------------------

DEMO_SCHEDULE_TABLES = (
    ClassSession,
    ClassSessionParticipant,
    ClassSeries,
    ClassSeriesParticipant,
    ClassGroup,
    GroupEnrollment,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSessionReassignmentFlag,
    ApprovalRequest,
    Notification,
)
"""The tables whose added rows ``scheduling.demo_schedule`` counts (#299 D)."""


def _settings_app_env() -> str:
    return get_settings().app_env


def _settings_horizon_weeks() -> int:
    return get_settings().scheduling_horizon_weeks


class DemoScheduleGenerator:
    """``scheduling.demo_schedule`` (#299 D): past ``completed`` sessions,
    upcoming one-off sessions (confirmed, offered, declined, unassigned), a
    weekly series and a tutoring group for ``demo.tutor`` and the #195
    students, dated by :func:`plan_demo_schedule`.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once ``demo.tutor`` teaches a session or the ``demo.student`` profile takes
    part in one. Raises when a demo account, a #195 student, a demo subject or
    a demo classroom is missing.
    """

    name = "scheduling.demo_schedule"

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
        owner_id = await demo_user_id(session, DEMO_OWNER_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await self._students(session, student_user_id)
        if await _already_scheduled(session, tutor_id, students["S"]):
            return 0
        before = await count_rows(session, DEMO_SCHEDULE_TABLES)

        writer = _ScheduleWriter(
            session,
            plan=self._plan(await administration.center_today(session)),
            zone=await administration.get_center_time_zone(session),
            owner_id=owner_id,
            tutor_id=tutor_id,
            students=students,
            subjects=await _subjects(session, self.name),
            classrooms=await _classrooms(session, self.name),
        )
        await writer.past()
        await writer.upcoming()
        await writer.series(_settings_horizon_weeks())
        await writer.group(_settings_horizon_weeks())
        return await count_rows(session, DEMO_SCHEDULE_TABLES) - before

    async def _students(
        self, session: AsyncSession, student_user_id: uuid.UUID
    ) -> dict[StudentKey, uuid.UUID]:
        """S, Sib, A and B (#299 D2), looked up, never created."""
        return await demo_students(session, student_user_id, self.name)


async def demo_students(
    session: AsyncSession, student_user_id: uuid.UUID, generator: str
) -> dict[StudentKey, uuid.UUID]:
    """S, Sib, A and B (#299 D2), looked up, never created; raises
    :class:`DemoPrerequisiteMissingError` naming ``generator`` without them
    (also used by the #303 generators)."""
    s = await session.scalar(
        select(Student.id).where(Student.user_id == student_user_id)
    )
    if s is None:
        raise DemoPrerequisiteMissingError(
            f"the {DEMO_STUDENT_USERNAME} profile must exist before {generator}"
        )
    family = select(FamilyStudent.family_id).where(FamilyStudent.student_id == s)
    siblings = list(
        await session.scalars(
            select(FamilyStudent.student_id)
            .where(FamilyStudent.family_id.in_(family))
            .where(FamilyStudent.student_id != s)
        )
    )
    # The family with a mother and a father (#195 B: the only father).
    pair = list(
        await session.scalars(
            select(Student.id)
            .join(StudentGuardian, StudentGuardian.student_id == Student.id)
            .where(StudentGuardian.relationship == "father")
            .order_by(Student.given_names, Student.id)
        )
    )
    if len(siblings) != 1 or len(pair) != 2:
        raise DemoPrerequisiteMissingError(
            f"the families.demo_families students must exist before {generator}"
        )
    return {"S": s, "Sib": siblings[0], "A": pair[0], "B": pair[1]}


async def _already_scheduled(
    session: AsyncSession, tutor_id: uuid.UUID, student_id: uuid.UUID
) -> bool:
    """The marker (#299 D1): a session taught by ``demo.tutor`` or one the
    ``demo.student`` profile takes part in."""
    found = await session.scalar(
        select(ClassSession.id)
        .outerjoin(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
        .where(
            or_(
                ClassSession.tutor_user_id == tutor_id,
                ClassSessionParticipant.student_id == student_id,
            )
        )
        .limit(1)
    )
    return found is not None


async def _subjects(
    session: AsyncSession, generator: str
) -> dict[SubjectKey, uuid.UUID]:
    wanted = [MATH_PRIMARY, SPANISH_PRIMARY, MATH_SECONDARY, ENGLISH_SECONDARY]
    rows = await session.execute(
        select(Subject.name, EducationLevel.name, Subject.id).join(
            EducationLevel, EducationLevel.id == Subject.education_level_id
        )
    )
    found = {(name, level): subject_id for name, level, subject_id in rows}
    missing = [key for key in wanted if key not in found]
    if missing:
        raise DemoPrerequisiteMissingError(
            f"the catalog.demo_catalog subjects must exist before {generator}"
        )
    return {key: found[key] for key in wanted}


async def _classrooms(session: AsyncSession, generator: str) -> dict[str, uuid.UUID]:
    wanted = [AULA_1, AULA_2, SALA]
    rows = await session.execute(
        select(Classroom.name, Classroom.id).where(Classroom.name.in_(wanted))
    )
    found = {name: classroom_id for name, classroom_id in rows}
    if set(found) != set(wanted):
        raise DemoPrerequisiteMissingError(
            f"the catalog.demo_catalog classrooms must exist before {generator}"
        )
    return found


@dataclass
class _ScheduleWriter:
    session: AsyncSession
    plan: DemoSchedulePlan
    zone: str
    owner_id: uuid.UUID
    tutor_id: uuid.UUID
    students: dict[StudentKey, uuid.UUID]
    subjects: dict[SubjectKey, uuid.UUID]
    classrooms: dict[str, uuid.UUID]

    def _instant(self, day: date, hhmm: str) -> datetime:
        return local_instant(day, hhmm, self.zone)

    def _room(self, name: str | None) -> uuid.UUID | None:
        return None if name is None else self.classrooms[name]

    async def past(self) -> None:
        """D3: ``completed`` sessions written directly (#299 E3)."""
        for planned in self.plan.past:
            row = await persist_class_session(
                self.session,
                state=SessionState.COMPLETED.value,
                subject_id=self.subjects[planned.subject],
                tutor_user_id=self.tutor_id,
                classroom_id=self._room(planned.classroom),
                starts_at=self._instant(planned.day, planned.start),
                ends_at=self._instant(planned.day, planned.end),
            )
            await add_participant(self.session, row, self.students[planned.student])

    async def upcoming(self) -> None:
        """D4: one-off sessions through ``create_class_session``, then the
        tutor's answer."""
        for planned in self.plan.upcoming:
            created = await scheduling.create_class_session(
                self.session,
                ClassSessionCreate(
                    kind="individual",
                    student_id=self.students[planned.student],
                    subject_id=self.subjects[planned.subject],
                    tutor_user_id=self.tutor_id if planned.tutor else None,
                    classroom_id=self._room(planned.classroom),
                    starts_at=self._instant(planned.day, planned.start),
                    ends_at=self._instant(planned.day, planned.end),
                ),
                actor_id=self.owner_id,
            )
            _no_warnings(planned.key, created.warnings)
            if planned.outcome in {"accept", "decline"}:
                assert created.pending_offer is not None
                await scheduling.answer_offer(
                    self.session,
                    created.pending_offer.id,
                    caller_id=self.tutor_id,
                    accept=planned.outcome == "accept",
                    note=DECLINE_NOTE if planned.outcome == "decline" else None,
                )

    def _recurrence(self, planned: PlannedRecurrence) -> dict[str, Any]:
        return {
            "subject_id": self.subjects[planned.subject],
            "tutor_user_id": self.tutor_id,
            "classroom_id": self._room(planned.classroom),
            "starts_on": planned.starts_on,
            "start_time": planned.start,
            "duration_minutes": planned.duration_minutes,
            "recurrence": SeriesRecurrence(
                weekdays=[planned.weekday], interval=1, count=planned.count
            ),
        }

    async def series(self, horizon_weeks: int) -> None:
        """D5: the weekly series; the tutor accepts its offer."""
        planned = self.plan.series
        created = await scheduling.create_class_series(
            self.session,
            ClassSeriesCreate(
                kind="individual",
                student_id=self.students[planned.students[0]],
                **self._recurrence(planned),
            ),
            horizon_weeks=horizon_weeks,
            actor_id=self.owner_id,
        )
        _no_warnings("series", created.warnings)
        await self._accept_series_offer(created.id)

    async def group(self, horizon_weeks: int) -> None:
        """D6: the tutoring group (the route's workflow); the tutor accepts
        its offer."""
        planned = self.plan.group
        group_id = await group_enrollment.create_group(
            self.session,
            GroupCreate(
                name=GROUP_NAME,
                max_students=6,
                student_ids=[self.students[key] for key in planned.students],
                **self._recurrence(planned),
            ),
            horizon_weeks=horizon_weeks,
            actor_id=self.owner_id,
        )
        series_id = await self.session.scalar(
            select(ClassSeries.id).where(ClassSeries.group_id == group_id)
        )
        assert series_id is not None
        await self._accept_series_offer(series_id)

    async def _accept_series_offer(self, series_id: uuid.UUID) -> None:
        offer_id = await self.session.scalar(
            select(AssignmentOffer.id)
            .where(AssignmentOffer.class_series_id == series_id)
            .where(AssignmentOffer.status == "pending")
        )
        assert offer_id is not None
        await scheduling.answer_offer(
            self.session, offer_id, caller_id=self.tutor_id, accept=True
        )


class DemoScheduleWarningError(RuntimeError):
    """A service returned a workload warning: the plan has a bug (#299 D7)."""


def _no_warnings(key: str, warnings: list[Any]) -> None:
    if warnings:
        raise DemoScheduleWarningError(f"{key}: {warnings}")
