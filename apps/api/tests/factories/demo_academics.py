"""The #353 demo academics (part 1 of the M5 demo data, E1 (a)):
:func:`plan_demo_academics` and the ``scheduling.demo_held_sessions``,
``academics.demo_notes`` and ``academics.demo_evaluations`` generators (the
finished cohort, ``courses.demo_past_cohort``, is in
:mod:`tests.factories.demo_past_cohort`).

**The plan (#353 G2)** is a pure function of ``T``, the center's today, on
#299's ``P(k)`` (the k-th Monday-Friday strictly before ``T``, nearest
first, so ``P(k + 5) = P(k) - 7``): local dates and ``HH:MM`` times only.

- ``demo.tutor``'s held classes H1-H3 on ``P(6)`` … ``P(8)`` at 16:00, always
  at least 8 days back (outside the 7-day slot search of
  ``tests.factories.e2e_attendance``);
- the finished cohort: ``demo.tutor2``, ``Aula 2``, 18:00-19:30, weekly on the
  weekdays of ``P(13)`` and ``P(11)`` (those of ``P(3)`` and ``P(1)``), six
  classes from ``P(13)`` to ``P(1)``;
- ``T0`` = ``P(13) - 2 days`` at 12:00, when every class is created, every
  offer answered and every student enrolled;
- for each class, in date order: the attendance and the closeout at its end
  + 15 minutes, the approval at its end + 2 hours.

**Past data goes through the services (E2 (a)).** No service creates a class
that has started, so the held-classes and cohort generators call the route
services under :func:`~tests.factories.demo_clock.seed_clock`: the creates
at ``T0``, the tutor's attendance and closeout submit and
``demo.coordinator``'s approval at the plan's instants. Every service check
(overlaps, qualifications, availability, rooms, booking notice) and every
side effect (offers, approval items, closeout revisions, audit events,
notifications) applies. No override, no direct insert. Database-set columns
(``created_at``, ``audit_events.occurred_at``) keep the real run time. The
notes and evaluations run on the real clock.

**Idempotent; a stale schedule is skipped (#353 G3).** Each generator writes
all its rows or none and returns ``0`` when its marker exists. A database
whose #299 schedule is from an earlier day gets ``skipped (demo schedule
from an earlier day; reset to add it)``. Fixed ``es_MX`` text, no Faker.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.config import get_settings
from app.modules.academics.models import (
    Evaluation,
    EvaluationScore,
    EvaluationTemplate,
    EvaluationTemplateCriterion,
    SessionCloseout,
    SessionCloseoutApproval,
    SessionNote,
)
from app.modules.academics.schemas import (
    CloseoutApprove,
    CloseoutSummaryInput,
    SessionNoteCreate,
)
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.catalog.models import Classroom
from app.modules.courses import service as courses
from app.modules.courses.models import CourseCohort
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_rules import AttendanceStatus
from app.modules.scheduling.attendance_schemas import AttendanceMark
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSession,
    ClassSessionAttendance,
    ClassSessionParticipant,
)
from app.modules.scheduling.schemas import ClassSessionCreate
from app.workflows import evaluations, session_closeout, session_closeout_review
from app.workflows import session_notes as notes
from app.workflows.evaluation_schemas import (
    EvaluationCreate,
    EvaluationCriterionInput,
    EvaluationScoreInput,
    EvaluationTemplateCreate,
)
from app.workflows.session_closeout_schemas import SessionCloseoutSubmit
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_changes import (
    STALE_SCHEDULE,
    demo_subject_ids,
    no_warnings,
    schedule_from_today,
)
from tests.factories.demo_clock import seed_clock
from tests.factories.demo_schedule import (
    AULA_1,
    ENGLISH_SECONDARY,
    MATH_PRIMARY,
    MATH_SECONDARY,
    StudentKey,
    SubjectKey,
    demo_students,
    local_instant,
    past_weekdays,
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

DEMO_COORDINATOR_USERNAME: Final = "demo.coordinator"
"""The reviewer of every demo closeout and the author of the evaluation
templates (#353)."""

# --- Fixed es_MX texts (#353 A5) ------------------------------------------------------

PERFORMANCE = "Trabajó con atención y resolvió los ejercicios con pocos errores."
NEXT_STEPS = "Repasar los ejercicios de la guía antes de la siguiente clase."
H1_HOMEWORK = "Ejercicios 1 a 10 de la guía."

COHORT_NAME = "Álgebra intensiva, grupo 1"
"""The finished cohort (#353 C2); its template is
:data:`tests.factories.demo_past_cohort.TEMPLATE_NAME`."""

N1_BODY = (
    "Resolvió sin ayuda las ecuaciones de primer grado; conviene repasar"
    " fracciones en casa."
)
N2_BODY = "Participó mucho en el repaso y está lista para el examen final."
N3_BODY = "Se distrae al final de la clase; probar ejercicios más cortos."

DIAGNOSTIC_TEMPLATE = "Diagnóstico de matemáticas"
DIAGNOSTIC_CRITERIA = ("Operaciones con fracciones", "Ecuaciones de primer grado")
EXAM_TEMPLATE = "Examen escrito"
EXAM_CRITERION = "Calificación del examen"
EV1_FEEDBACK = "Domina las operaciones básicas; reforzar el despeje de ecuaciones."
EV2_FEEDBACK = "Buena comprensión lectora; cuidar la ortografía."
FINAL_S_FEEDBACK = "Excelente desempeño durante el curso."
FINAL_A_FEEDBACK = "Necesita repasar sistemas de ecuaciones antes de un nuevo intento."
MIDTERM = "Examen parcial"
FINAL = "Examen final"


# --- The plan (pure) ------------------------------------------------------------------

type CohortStudentKey = StudentKey
"""S, A or B: the cohort's students (#353 C2)."""


def _plus(hhmm: str, minutes: int) -> str:
    start = datetime.combine(date.min, time.fromisoformat(hhmm))
    return (start + timedelta(minutes=minutes)).strftime("%H:%M")


@dataclass(frozen=True)
class HeldClass:
    """One class of the plan that has already been held: local date,
    ``HH:MM`` times and what the tutor records."""

    key: str
    day: date
    start: str
    end: str
    attendance: tuple[tuple[StudentKey, AttendanceStatus], ...]
    actual_minutes: int
    topics: str
    homework: str | None = None
    student: StudentKey | None = None
    """The one student of an individual class (``None`` for a cohort's)."""
    subject: SubjectKey | None = None
    """The subject of an individual class (``None`` for a cohort's)."""

    @property
    def closeout_at(self) -> str:
        """The attendance and the closeout submit: the end + 15 minutes."""
        return _plus(self.end, 15)

    @property
    def approve_at(self) -> str:
        """``demo.coordinator``'s approval: the end + 2 hours."""
        return _plus(self.end, 120)


@dataclass(frozen=True)
class DemoAcademicsPlan:
    """Every #353 date and time for one ``today``."""

    today: date
    created_on: date
    """``T0``'s date: ``P(13) - 2`` days."""
    created_at: str
    """``T0``'s time."""
    held: tuple[HeldClass, ...]
    """H1-H3 (``demo.tutor``), in key order."""
    cohort_starts_on: date
    cohort_weekdays: tuple[int, ...]
    cohort_start: str
    cohort_end: str
    cohort: tuple[HeldClass, ...]
    """C1-C6, in date order."""


def plan_demo_academics(today: date) -> DemoAcademicsPlan:
    """The #353 plan for the center's ``today`` (#353 G2)."""
    p = past_weekdays(today, 13)

    def held(
        key: str,
        k: int,
        student: StudentKey,
        subject: SubjectKey,
        status: AttendanceStatus,
        minutes: int,
        topics: str,
        homework: str | None = None,
    ) -> HeldClass:
        return HeldClass(
            key=key,
            day=p[k - 1],
            start="16:00",
            end="17:00",
            attendance=((student, status),),
            actual_minutes=minutes,
            topics=topics,
            homework=homework,
            student=student,
            subject=subject,
        )

    cohort_rows: tuple[tuple[str, int, AttendanceStatus, AttendanceStatus,
                             AttendanceStatus, str], ...] = (
        ("C1", 13, "present", "present", "present",
         "Ecuaciones de primer grado con una incógnita"),
        ("C2", 11, "present", "present", "absent", "Ecuaciones con fracciones"),
        ("C3", 8, "late", "present", "present",
         "Examen parcial y repaso de ecuaciones"),
        ("C4", 6, "present", "absent", "present",
         "Sistemas de ecuaciones por sustitución"),
        ("C5", 3, "present", "present", "absent",
         "Sistemas de ecuaciones por igualación"),
        ("C6", 1, "present", "present", "present", "Examen final"),
    )  # fmt: skip
    cohort = tuple(
        HeldClass(
            key=key,
            day=p[k - 1],
            start="18:00",
            end="19:30",
            attendance=(("S", s), ("A", a), ("B", b)),
            actual_minutes=90,
            topics=topics,
        )
        for key, k, s, a, b, topics in cohort_rows
    )
    return DemoAcademicsPlan(
        today=today,
        created_on=p[12] - timedelta(days=2),
        created_at="12:00",
        held=(
            held(
                "H1",
                6,
                "S",
                MATH_SECONDARY,
                "present",
                60,
                "Despeje de ecuaciones de primer grado",
                H1_HOMEWORK,
            ),
            held(
                "H2", 7, "Sib", MATH_PRIMARY, "late", 55, "Sumas y restas de fracciones"
            ),
            held(
                "H3",
                8,
                "S",
                ENGLISH_SECONDARY,
                "present",
                60,
                "Comprensión de lectura: textos informativos",
            ),
        ),  # fmt: skip
        cohort_starts_on=p[12],
        cohort_weekdays=tuple(sorted({p[12].isoweekday(), p[10].isoweekday()})),
        cohort_start="18:00",
        cohort_end="19:30",
        cohort=cohort,
    )


# --- Shared by the #353 generators ----------------------------------------------------


def _settings_app_env() -> str:
    return get_settings().app_env


def summary_of(planned: HeldClass) -> CloseoutSummaryInput:
    """The closeout summary of a planned class (#353 B3, C3)."""
    return CloseoutSummaryInput(
        topics=planned.topics,
        performance=PERFORMANCE,
        homework=planned.homework,
        next_steps=NEXT_STEPS,
    )


async def hold_class(
    session: AsyncSession,
    *,
    class_session_id: uuid.UUID,
    planned: HeldClass,
    students: dict[StudentKey, uuid.UUID],
    zone: str,
    tutor: Principal,
    reviewer: Principal,
) -> None:
    """Under the seed clock: the tutor's attendance and closeout submit at
    the class's end + 15 minutes, then the reviewer's approval at its end + 2
    hours (#353 B3, C3). Versions come from the returned views."""
    with seed_clock(local_instant(planned.day, planned.closeout_at, zone)):
        version = await session.scalar(
            select(ClassSession.version).where(ClassSession.id == class_session_id)
        )
        assert version is not None
        marked = await scheduling.record_attendance(
            session,
            actor=tutor,
            class_session_id=class_session_id,
            version=version,
            marks=[
                AttendanceMark(student_id=students[key], status=status)
                for key, status in planned.attendance
            ],
        )
        submitted = await session_closeout.submit_session_closeout(
            session,
            principal=tutor,
            class_session_id=class_session_id,
            body=SessionCloseoutSubmit(
                version=marked.version,
                actual_minutes=planned.actual_minutes,
                summary=summary_of(planned),
            ),
        )
    assert submitted.closeout is not None
    with seed_clock(local_instant(planned.day, planned.approve_at, zone)):
        await session_closeout_review.approve_closeout(
            session,
            principal=reviewer,
            class_session_id=class_session_id,
            body=CloseoutApprove(version=submitted.closeout.version),
        )


async def held_session_id(
    session: AsyncSession,
    *,
    tutor_id: uuid.UUID,
    starts_at: datetime,
    student_id: uuid.UUID,
    generator: str,
    closed_out: bool = False,
) -> uuid.UUID:
    """The id of the class of ``tutor_id`` with ``student_id`` starting at
    ``starts_at`` (with a closeout when ``closed_out``); raises
    :class:`DemoPrerequisiteMissingError` naming ``generator`` without it."""
    query = (
        select(ClassSession.id)
        .join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id == ClassSession.id,
        )
        .where(ClassSession.tutor_user_id == tutor_id)
        .where(ClassSession.starts_at == starts_at)
        .where(ClassSessionParticipant.student_id == student_id)
    )
    if closed_out:
        query = query.join(
            SessionCloseout, SessionCloseout.class_session_id == ClassSession.id
        )
    found = await session.scalar(query)
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"the #353 demo class at {starts_at} must exist before {generator}"
        )
    return found


# --- scheduling.demo_held_sessions (#353 B) -------------------------------------------

DEMO_HELD_SESSIONS_TABLES = (
    ClassSession,
    ClassSessionParticipant,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSessionAttendance,
    SessionCloseout,
    SessionCloseoutApproval,
    ApprovalRequest,
    Notification,
)
"""The tables whose added rows ``scheduling.demo_held_sessions`` counts
(#353 B)."""


class DemoHeldSessionsGenerator:
    """``scheduling.demo_held_sessions`` (#353 B): ``demo.tutor``'s held
    classes H1-H3 (``Aula 1``, dated by :func:`plan_demo_academics`),
    created at ``T0`` by ``demo.owner`` and accepted by ``demo.tutor``, each
    with the tutor's attendance and closeout and ``demo.coordinator``'s
    approval, through the route services under the seed clock.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once a class the ``demo.student`` profile takes part in has a closeout.
    Skipped when #299's schedule is from an earlier day; raises when a demo
    account, a #195 student, a demo subject, ``Aula 1`` or #299's schedule is
    missing.
    """

    name = "scheduling.demo_held_sessions"

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
        tutor = await demo_principal(session, DEMO_TUTOR_USERNAME, self.name)
        reviewer = await demo_principal(session, DEMO_COORDINATOR_USERNAME, self.name)
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if await _closed_out(session, students["S"]):
            return 0
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        subjects = await demo_subject_ids(
            session, (MATH_SECONDARY, MATH_PRIMARY, ENGLISH_SECONDARY), self.name
        )
        aula_1 = await classroom_id(session, AULA_1, self.name)
        zone = await administration.get_center_time_zone(session)
        plan = self._plan(await administration.center_today(session))
        before = await count_rows(session, DEMO_HELD_SESSIONS_TABLES)

        # B2: H1-H3 created at T0 by the Owner; demo.tutor accepts each offer.
        created: dict[str, uuid.UUID] = {}
        with seed_clock(local_instant(plan.created_on, plan.created_at, zone)):
            for planned in plan.held:
                assert planned.student is not None and planned.subject is not None
                result = await scheduling.create_class_session(
                    session,
                    ClassSessionCreate(
                        kind="individual",
                        student_id=students[planned.student],
                        subject_id=subjects[planned.subject],
                        tutor_user_id=tutor.user_id,
                        classroom_id=aula_1,
                        starts_at=local_instant(planned.day, planned.start, zone),
                        ends_at=local_instant(planned.day, planned.end, zone),
                    ),
                    actor_id=owner_id,
                )
                no_warnings(planned.key, list(result.warnings))
                assert result.pending_offer is not None
                await scheduling.answer_offer(
                    session,
                    result.pending_offer.id,
                    caller_id=tutor.user_id,
                    accept=True,
                )
                created[planned.key] = result.id

        # B3: in date order, attendance and closeout, then the approval.
        for planned in sorted(plan.held, key=lambda h: h.day):
            await hold_class(
                session,
                class_session_id=created[planned.key],
                planned=planned,
                students=students,
                zone=zone,
                tutor=tutor,
                reviewer=reviewer,
            )
        return await count_rows(session, DEMO_HELD_SESSIONS_TABLES) - before


async def _closed_out(session: AsyncSession, s_id: uuid.UUID) -> bool:
    """The marker (#353 B1): a closeout of a class S takes part in."""
    found = await session.scalar(
        select(SessionCloseout.id)
        .join(
            ClassSessionParticipant,
            ClassSessionParticipant.class_session_id
            == SessionCloseout.class_session_id,
        )
        .where(ClassSessionParticipant.student_id == s_id)
        .limit(1)
    )
    return found is not None


async def classroom_id(session: AsyncSession, name: str, generator: str) -> uuid.UUID:
    """The id of the demo classroom ``name``."""
    found = await session.scalar(select(Classroom.id).where(Classroom.name == name))
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"the {name} classroom must exist before {generator}"
        )
    return found


async def past_cohort_id(session: AsyncSession, generator: str) -> uuid.UUID:
    """The id of the finished cohort (#353 C)."""
    found = await session.scalar(
        select(CourseCohort.id).where(CourseCohort.name == COHORT_NAME)
    )
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"the courses.demo_past_cohort cohort must exist before {generator}"
        )
    return found


async def cohort_session_id(
    session: AsyncSession, cohort_id: uuid.UUID, starts_at: datetime, generator: str
) -> uuid.UUID:
    """The id of the cohort's class at ``starts_at``, closed out."""
    found = await session.scalar(
        select(ClassSession.id)
        .join(SessionCloseout, SessionCloseout.class_session_id == ClassSession.id)
        .where(ClassSession.cohort_id == cohort_id)
        .where(ClassSession.starts_at == starts_at)
    )
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"the courses.demo_past_cohort class at {starts_at} must exist"
            f" before {generator}"
        )
    return found


# --- academics.demo_notes (#353 D) ----------------------------------------------------

DEMO_NOTES_TABLES = (SessionNote, Notification)
"""The tables whose added rows ``academics.demo_notes`` counts (#353 D)."""


class DemoNotesGenerator:
    """``academics.demo_notes`` (#353 D): N1 (``demo.tutor`` on H1 for S)
    and N2 (``demo.tutor2`` on C6 for S), shared with the family, and N3
    (``demo.tutor`` on H2 for Sib), internal, on the real clock.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once a note about S or Sib exists. Skipped when #299's schedule is from
    an earlier day; raises when a demo account, a #195 student, H1, H2 or C6
    is missing.
    """

    name = "academics.demo_notes"

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
        tutor = await demo_principal(session, DEMO_TUTOR_USERNAME, self.name)
        tutor2 = await demo_principal(session, DEMO_TUTOR2_USERNAME, self.name)
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if await session.scalar(
            select(SessionNote.id)
            .where(SessionNote.student_id.in_([students["S"], students["Sib"]]))
            .limit(1)
        ):
            return 0
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        zone = await administration.get_center_time_zone(session)
        plan = self._plan(await administration.center_today(session))
        h1, h2 = plan.held[0], plan.held[1]
        c6 = plan.cohort[-1]
        h1_id = await held_session_id(
            session,
            tutor_id=tutor.user_id,
            starts_at=local_instant(h1.day, h1.start, zone),
            student_id=students["S"],
            generator=self.name,
        )
        h2_id = await held_session_id(
            session,
            tutor_id=tutor.user_id,
            starts_at=local_instant(h2.day, h2.start, zone),
            student_id=students["Sib"],
            generator=self.name,
        )
        c6_id = await cohort_session_id(
            session,
            await past_cohort_id(session, self.name),
            local_instant(c6.day, c6.start, zone),
            self.name,
        )
        before = await count_rows(session, DEMO_NOTES_TABLES)

        # D2: N1 and N2 published, N3 internal.
        for author, class_session_id, student, body, publish in (
            (tutor, h1_id, students["S"], N1_BODY, True),
            (tutor2, c6_id, students["S"], N2_BODY, True),
            (tutor, h2_id, students["Sib"], N3_BODY, False),
        ):
            note = await notes.create_note(
                session,
                principal=author,
                class_session_id=class_session_id,
                body=SessionNoteCreate(student_id=student, body=body),
            )
            if publish:
                await notes.publish_note(
                    session, principal=author, note_id=note.id, version=note.version
                )
        return await count_rows(session, DEMO_NOTES_TABLES) - before


# --- academics.demo_evaluations (#353 E) ----------------------------------------------

DEMO_EVALUATIONS_TABLES = (
    EvaluationTemplate,
    EvaluationTemplateCriterion,
    Evaluation,
    EvaluationScore,
    Notification,
)
"""The tables whose added rows ``academics.demo_evaluations`` counts
(#353 E)."""

COURSE_SCORES: tuple[tuple[str, str, tuple[tuple[CohortStudentKey, int,
                                                  str | None], ...]], ...] = (
    ("C3", MIDTERM, (("S", 90, None), ("A", 55, None), ("B", 70, None))),
    ("C6", FINAL, (("S", 85, FINAL_S_FEEDBACK), ("A", 45, FINAL_A_FEEDBACK),
                   ("B", 72, None))),
)  # fmt: skip
"""The course evaluations (#353 E4): the class, the assessment, and each
student's score and feedback."""


class DemoEvaluationsGenerator:
    """``academics.demo_evaluations`` (#353 E): the templates ``Diagnóstico
    de matemáticas`` and ``Examen escrito`` (``demo.coordinator``),
    ``demo.tutor``'s evaluations Ev1 and Ev2 (final) and Ev3 (a draft), and
    ``demo.tutor2``'s course evaluations of the finished cohort (``Examen
    parcial`` and ``Examen final``), on the real clock.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once the template ``Diagnóstico de matemáticas`` exists. Skipped when
    #299's schedule is from an earlier day; raises when a demo account, a
    #195 student, a demo subject, H1-H3, the finished cohort or their
    closeouts are missing.
    """

    name = "academics.demo_evaluations"

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
        # The templates' author: the route needs evaluation.manage only, so
        # the service takes no principal (#353 E2).
        await demo_principal(session, DEMO_COORDINATOR_USERNAME, self.name)
        tutor = await demo_principal(session, DEMO_TUTOR_USERNAME, self.name)
        tutor2 = await demo_principal(session, DEMO_TUTOR2_USERNAME, self.name)
        if await session.scalar(
            select(EvaluationTemplate.id).where(
                EvaluationTemplate.name == DIAGNOSTIC_TEMPLATE
            )
        ):
            return 0
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        students = await demo_students(session, student_user_id, self.name)
        if not await schedule_from_today(
            session, tutor.user_id, students["S"], self.name
        ):
            return GeneratorSkipped(STALE_SCHEDULE)
        subjects = await demo_subject_ids(
            session, (MATH_SECONDARY, MATH_PRIMARY, ENGLISH_SECONDARY), self.name
        )
        zone = await administration.get_center_time_zone(session)
        plan = self._plan(await administration.center_today(session))
        held: dict[str, uuid.UUID] = {}
        for planned in plan.held:
            assert planned.student is not None
            held[planned.key] = await held_session_id(
                session,
                tutor_id=tutor.user_id,
                starts_at=local_instant(planned.day, planned.start, zone),
                student_id=students[planned.student],
                generator=self.name,
                closed_out=True,
            )
        cohort_id = await past_cohort_id(session, self.name)
        cohort: dict[str, uuid.UUID] = {
            planned.key: await cohort_session_id(
                session,
                cohort_id,
                local_instant(planned.day, planned.start, zone),
                self.name,
            )
            for planned in plan.cohort
        }
        found = await courses.cohort_assessments(session, cohort_id)
        assert found is not None
        assessments = {a.title: a.id for a in found.assessments}
        before = await count_rows(session, DEMO_EVALUATIONS_TABLES)

        # E2: the templates, as demo.coordinator.
        diagnostic = await evaluations.create_template(
            session,
            body=EvaluationTemplateCreate(
                name=DIAGNOSTIC_TEMPLATE,
                kind="diagnostic",
                subject_id=subjects[MATH_SECONDARY],
                criteria=[
                    EvaluationCriterionInput(title=title, max_score=Decimal(10))
                    for title in DIAGNOSTIC_CRITERIA
                ],
            ),
        )
        exam = await evaluations.create_template(
            session,
            body=EvaluationTemplateCreate(
                name=EXAM_TEMPLATE,
                kind="ongoing",
                criteria=[
                    EvaluationCriterionInput(
                        title=EXAM_CRITERION, max_score=Decimal(100)
                    )
                ],
            ),
        )

        # E3: demo.tutor's evaluations; Ev3 stays a draft.
        individual: tuple[
            tuple[StudentKey, uuid.UUID, uuid.UUID | None, str, tuple[int, ...],
                  str | None, bool], ...
        ] = (
            ("S", diagnostic.id, None, "H1", (7, 8), EV1_FEEDBACK, True),
            ("S", exam.id, subjects[ENGLISH_SECONDARY], "H3", (88,), EV2_FEEDBACK,
             True),
            ("Sib", exam.id, subjects[MATH_PRIMARY], "H2", (80,), None, False),
        )  # fmt: skip
        for (
            key,
            template_id,
            subject_id,
            held_key,
            scores,
            feedback,
            final,
        ) in individual:
            await _evaluate(
                session,
                tutor,
                EvaluationCreate(
                    student_id=students[key],
                    template_id=template_id,
                    subject_id=subject_id,
                    class_session_id=held[held_key],
                    scores=_scores(scores),
                    feedback=feedback,
                ),
                finalize=final,
            )

        # E4: demo.tutor2's course evaluations, all final.
        for class_key, title, rows in COURSE_SCORES:
            for key, score, feedback in rows:
                await _evaluate(
                    session,
                    tutor2,
                    EvaluationCreate(
                        student_id=students[key],
                        template_id=exam.id,
                        subject_id=subjects[MATH_SECONDARY],
                        class_session_id=cohort[class_key],
                        course_assessment_id=assessments[title],
                        scores=_scores((score,)),
                        feedback=feedback,
                    ),
                    finalize=True,
                )
        return await count_rows(session, DEMO_EVALUATIONS_TABLES) - before


def _scores(values: tuple[int, ...]) -> list[EvaluationScoreInput]:
    return [
        EvaluationScoreInput(position=position, score=Decimal(value))
        for position, value in enumerate(values, start=1)
    ]


async def _evaluate(
    session: AsyncSession,
    author: Principal,
    body: EvaluationCreate,
    *,
    finalize: bool,
) -> None:
    created = await evaluations.create_evaluation(session, principal=author, body=body)
    if finalize:
        await evaluations.finalize_evaluation(
            session, principal=author, evaluation_id=created.id, version=created.version
        )
