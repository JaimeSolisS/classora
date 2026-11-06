"""The M8 demo data (M8-demo #539): :func:`plan_demo_admin` and the
``administration.demo_receipt_message``, ``administration.demo_admin``,
``administration.demo_imports``, ``reporting.demo_setups`` and
``academics.demo_deleted`` generators.

**The plan ("The plan (pure)")** is a pure function of ``T``, the center's
today, in local dates only (ADR-0004): the receipt message (es and en), the
CSV rows of the four import jobs, the schedules row's ``starts_on`` (the
first weekday on or after ``T + 2`` whose four weekly dates miss ``D(1)``,
``D(2)``, the closure ``D(3)`` and the tutor's day off ``D(4) + 7``, so the
slot is free of every #299/#303 ``demo.tutor`` session), the opening
balances' dates (``service_date = T``, due ``T + 14`` and ``T + 30``), the
Finance setup's range (the 1st of ``T``'s month to ``T``) and the deleted
learning plan. Two inputs are read from the database at run time, never
hard-coded (:class:`StudentName`, :class:`BookedSlot`): the students job's
duplicate (a demo student's stored name, upper-cased and without accents,
with the same birthdate) and the schedules job's conflict (an existing
future ``demo.tutor`` session).

**Every write goes through the function the route calls (E7), with the real
principal and** :func:`~tests.factories.demo_clock.seed_actor` **set to the
same user**, on the real clock (no past-dated rows, so no seed clock):

- G1 ``administration.demo_receipt_message``: ``demo.owner`` sets the
  receipt message through ``update_business_settings`` (its guard is never
  ``ensure()``d: the time zone doesn't change). It sits before
  ``billing.demo_pricing`` (E3), so the three demo receipts carry it.
- G2 ``administration.demo_admin``: ``demo.coordinator`` mutes
  ``administration.approval_assigned`` (E8), then ``demo.owner`` assigns
  ``demo.tutor``'s pending qualification item to ``demo.admin`` (notified)
  and R2's review item to ``demo.coordinator`` (muted: no notification).
- G3 ``administration.demo_imports``: four import jobs (E2, E10). Each is
  staged with ``create_import_job``; the committed ones then go through
  ``request_commit`` and ``commit_next_batch`` until done, in the seed's
  Unit of Work, so no Procrastinate row is written.
- G4 ``reporting.demo_setups``: two saved export setups (``create_setup``).
- G5 ``academics.demo_deleted``: ``demo.tutor`` creates a learning-plan
  draft and deletes it (E9).

**Idempotent; stale and E2E databases are skipped.** In this order:
``APP_ENV`` other than ``local`` (``skipped (APP_ENV=…; local only)``),
``--without-billing`` (``skipped (--without-billing)``, the session
untouched, E1), the generator's marker (``0 rows``), a #299 schedule from an
earlier day (the stale skip), and for G1 a different receipt message already
set (:data:`RECEIPT_MESSAGE_CONFIGURED`). A missing demo account, R2's or
the qualification's open item raises
:class:`~tests.factories.identity.DemoPrerequisiteMissingError`. Fixed
``es_MX`` text, no Faker; the receipt message is set in both languages.
"""

import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal
from app.core.reauth import RecentAuthGuard
from app.modules.academics.models import (
    LearningPlan,
    LearningPlanMilestone,
    LearningPlanObjective,
    ProgressReport,
)
from app.modules.administration import service as administration
from app.modules.administration.approvals import (
    APPROVAL_ASSIGNED,
    OPEN_STATUSES,
    assign_approval_request,
)
from app.modules.administration.imports import service as imports
from app.modules.administration.imports.models import ImportJob, ImportRow
from app.modules.administration.models import (
    ApprovalRequest,
    Notification,
    NotificationOptOut,
)
from app.modules.administration.notification_preferences import (
    update_notification_preferences,
)
from app.modules.administration.schemas import (
    BusinessSettingsUpdate,
    NotificationPreferencesBody,
)
from app.modules.administration.settings import update_business_settings
from app.modules.administration.settings_router import TIME_ZONE_ACTION
from app.modules.billing import imports as balances_kind
from app.modules.billing.models import Charge
from app.modules.families import imports as students_kind
from app.modules.families.models import Student, StudentEmergencyContact
from app.modules.identity import imports as tutors_kind
from app.modules.reporting.setups.models import ReportSetup
from app.modules.reporting.setups.schemas import ReportSetupCreate
from app.modules.reporting.setups.service import create_setup
from app.modules.scheduling import imports as schedules_kind
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
)
from app.modules.staff.models import TutorQualification
from app.workflows import learning_plans
from app.workflows.learning_plan_schemas import (
    LearningPlanCreate,
    LearningPlanMilestoneInput,
    LearningPlanObjectiveInput,
)
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_academics import DEMO_COORDINATOR_USERNAME
from tests.factories.demo_billing import (
    DEMO_FINANCE_USERNAME,
    _early_skip,
    _settings_app_env,
    billing_skipped,
    billing_students,
)
from tests.factories.demo_changes import STALE_SCHEDULE, demo_subject_ids
from tests.factories.demo_clock import seed_actor
from tests.factories.demo_finance import _stale
from tests.factories.demo_schedule import (
    MATH_SECONDARY,
    plan_demo_schedule,
    upcoming_weekdays,
)
from tests.factories.identity import (
    DEMO_OWNER_USERNAME,
    DEMO_STUDENT_USERNAME,
    DEMO_TUTOR_USERNAME,
    DemoPrerequisiteMissingError,
    demo_principal,
    demo_user_id,
)

DEMO_ADMIN_USERNAME: Final = "demo.admin"

RECEIPT_MESSAGE_CONFIGURED: Final = "receipt message already set; reset to add it"
"""G1's skip reason when a different receipt message was set by hand."""

# --- Fixed es_MX texts (A6) -----------------------------------------------------------

RECEIPT_MESSAGE_ES: Final = "Gracias por su pago. Centro de tutorías Classora (demo)."
RECEIPT_MESSAGE_EN: Final = (
    "Thank you for your payment. Classora tutoring center (demo)."
)

STUDENTS_FILE: Final = "demo-students.csv"
TUTORS_FILE: Final = "demo-tutors.csv"
SCHEDULES_FILE: Final = "demo-schedules.csv"
BALANCES_FILE: Final = "demo-balances.csv"
IMPORT_FILES: Final = (STUDENTS_FILE, TUTORS_FILE, SCHEDULES_FILE, BALANCES_FILE)
"""G3's markers: a job with one of these file names exists (any status)."""
IMPORTED_USERNAME_PREFIX: Final = "demo-import-"

FINANCE_SETUP_NAME: Final = "Gastos del mes (demo)"
ADMIN_SETUP_NAME: Final = "Alumnos activos (demo)"

DELETED_PLAN_TITLE: Final = "Borrador eliminado (demo)"
DELETED_PLAN_OBJECTIVE: Final = "Repasar fracciones equivalentes"
DELETED_PLAN_MILESTONE: Final = "Ejercicios de simplificación"

SERIES_START: Final = "19:00"
SERIES_MINUTES: Final = 60
SERIES_COUNT: Final = 4
"""The imported series: four weekly 19:00-20:00 classes (``demo.tutor``'s
window ends at 20:00; 60 minutes after the #299 series ends)."""

STUDENT_COLUMNS: Final = tuple(column.name for column in students_kind.COLUMNS)
TUTOR_COLUMNS: Final = tuple(column.name for column in tutors_kind.COLUMNS)
SCHEDULE_COLUMNS: Final = tuple(column.name for column in schedules_kind.COLUMNS)
BALANCE_COLUMNS: Final = tuple(column.name for column in balances_kind.COLUMNS)


# --- The plan (pure) ------------------------------------------------------------------


@dataclass(frozen=True)
class StudentName:
    """A stored student's natural key (#122 E6), read at run time."""

    given_names: str
    family_names: str
    birthdate: date


@dataclass(frozen=True)
class BookedSlot:
    """An existing future ``demo.tutor`` session (local date and time), read
    at run time: the schedules job's conflicting row (E10)."""

    day: date
    start: str
    minutes: int


@dataclass(frozen=True)
class PlannedBalance:
    description: str
    amount: str
    service_date: date
    due_date: date


@dataclass(frozen=True)
class DemoAdminPlan:
    """Every M8-demo date and text for one ``today`` (center-local)."""

    today: date
    receipt_message_es: str
    receipt_message_en: str
    new_students: tuple[dict[str, str], ...]
    """The students job's two new fictional students (CSV rows)."""
    tutors: tuple[dict[str, str], ...]
    """The tutors job's rows: one ready, one with an invalid email."""
    series_starts_on: date
    balances: tuple[PlannedBalance, ...]
    incurred_from: date
    incurred_to: date
    plan_title: str = DELETED_PLAN_TITLE

    def students_csv(self, duplicate: StudentName) -> str:
        """The two new students, then ``duplicate`` upper-cased without
        accents (a ``duplicate`` row at staging, E10)."""
        rows = [
            *self.new_students,
            {
                "given_names": _shouted(duplicate.given_names),
                "family_names": _shouted(duplicate.family_names),
                "birthdate": duplicate.birthdate.isoformat(),
                "contact_name": "Contacto duplicado (demo)",
                "contact_relationship": "Madre",
                "contact_phone": "55 5555 0103",
            },
        ]
        return _csv(STUDENT_COLUMNS, rows)

    def tutors_csv(self) -> str:
        return _csv(TUTOR_COLUMNS, self.tutors)

    def schedules_csv(self, student: StudentName, conflict: BookedSlot) -> str:
        """Row 1: a weekly series for ``student`` with ``demo.tutor`` from
        :attr:`series_starts_on`; row 2: the same tutor at ``conflict``'s
        time (``failed`` ``scheduling_conflict`` at commit)."""
        subject, level = MATH_SECONDARY
        common = {
            "student_given_names": student.given_names,
            "student_family_names": student.family_names,
            "student_birthdate": student.birthdate.isoformat(),
            "subject": subject,
            "education_level": level,
            "tutor_username": DEMO_TUTOR_USERNAME,
        }
        rows = [
            {
                **common,
                "starts_on": self.series_starts_on.isoformat(),
                "start_time": SERIES_START,
                "duration_minutes": str(SERIES_MINUTES),
                "weekdays": str(self.series_starts_on.isoweekday()),
                "count": str(SERIES_COUNT),
            },
            {
                **common,
                "starts_on": conflict.day.isoformat(),
                "start_time": conflict.start,
                "duration_minutes": str(conflict.minutes),
                "weekdays": str(conflict.day.isoweekday()),
                "count": "1",
            },
        ]
        return _csv(SCHEDULE_COLUMNS, rows)

    def balances_csv(self, student: StudentName) -> str:
        rows = [
            {
                "student_given_names": student.given_names,
                "student_family_names": student.family_names,
                "student_birthdate": student.birthdate.isoformat(),
                "description": balance.description,
                "amount": balance.amount,
                "service_date": balance.service_date.isoformat(),
                "due_date": balance.due_date.isoformat(),
            }
            for balance in self.balances
        ]
        return _csv(BALANCE_COLUMNS, rows)

    def finance_setup(self) -> ReportSetupCreate:
        """``demo.finance``'s setup: this month's expenses, CSV, shared with
        the Owner role."""
        return ReportSetupCreate.model_validate(
            {
                "name": FINANCE_SETUP_NAME,
                "parameters": {
                    "dataset": "expenses",
                    "format": "csv",
                    "filters": {
                        "incurred_from": self.incurred_from.isoformat(),
                        "incurred_to": self.incurred_to.isoformat(),
                    },
                },
                "shared_role": "owner",
            }
        )

    def admin_setup(self) -> ReportSetupCreate:
        """``demo.admin``'s private setup: the active students, XLSX."""
        return ReportSetupCreate.model_validate(
            {
                "name": ADMIN_SETUP_NAME,
                "parameters": {
                    "dataset": "students",
                    "format": "xlsx",
                    "filters": {"status": "active"},
                },
            }
        )

    def plan_body(self, subject_id: uuid.UUID) -> LearningPlanCreate:
        return LearningPlanCreate(
            subject_id=subject_id,
            title=self.plan_title,
            objectives=[
                LearningPlanObjectiveInput(
                    title=DELETED_PLAN_OBJECTIVE,
                    milestones=[
                        LearningPlanMilestoneInput(title=DELETED_PLAN_MILESTONE)
                    ],
                )
            ],
        )


def _shouted(text: str) -> str:
    """``text`` upper-cased without accents (the natural key still matches:
    ``f_unaccent(lower(…))``)."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def _csv(columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> str:
    """Comma-separated, a header line then one line per row (no value holds a
    comma or a quote)."""
    lines = [",".join(columns)]
    lines.extend(",".join(row.get(column, "") for column in columns) for row in rows)
    return "\n".join(lines) + "\n"


def series_starts_on(today: date) -> date:
    """The first weekday on or after ``T + 2`` (more than 24 hours away at
    any time of day) whose :data:`SERIES_COUNT` weekly dates miss ``D(1)``,
    ``D(2)`` (#299's ``U1``-``U3``: the 10-minute break after ``U3``),
    ``D(3)`` (the closure) and ``D(4) + 7`` (the tutor's day off)."""
    schedule = plan_demo_schedule(today)
    d = upcoming_weekdays(today, 3)
    taken = {*d, schedule.closure_day, schedule.time_off_day}
    day = today + timedelta(days=2)
    while True:
        dates = {day + timedelta(weeks=k) for k in range(SERIES_COUNT)}
        if day.isoweekday() <= 5 and not dates & taken:
            return day
        day += timedelta(days=1)


def plan_demo_admin(today: date) -> DemoAdminPlan:
    """The M8-demo plan for the center's ``today``."""
    family = "Ejemplo Importado"
    contact = {"contact_relationship": "Madre", "contact_name": f"Lucía {family}"}
    return DemoAdminPlan(
        today=today,
        receipt_message_es=RECEIPT_MESSAGE_ES,
        receipt_message_en=RECEIPT_MESSAGE_EN,
        new_students=(
            {
                "given_names": "Renata",
                "family_names": family,
                "birthdate": "2013-05-20",
                "school_grade": "6",
                "education_level": "Primaria",
                **contact,
                "contact_phone": "55 5555 0101",
            },
            {
                "given_names": "Joaquín",
                "family_names": family,
                "birthdate": "2011-09-02",
                "school_grade": "2",
                "education_level": "Secundaria",
                **contact,
                "contact_phone": "55 5555 0102",
            },
        ),
        tutors=(
            {
                "username": f"{IMPORTED_USERNAME_PREFIX}tutor-1",
                "display_name": "Tutor importado uno (demo)",
                "email": "tutor-1@importacion.example",
            },
            {
                "username": f"{IMPORTED_USERNAME_PREFIX}tutor-2",
                "display_name": "Tutor importado dos (demo)",
                "email": "correo-sin-arroba",
            },
        ),
        series_starts_on=series_starts_on(today),
        balances=(
            PlannedBalance(
                "Saldo anterior: colegiatura (demo)",
                "1250.00",
                today,
                today + timedelta(days=14),
            ),
            PlannedBalance(
                "Saldo anterior: libros (demo)",
                "480.00",
                today,
                today + timedelta(days=30),
            ),
        ),
        incurred_from=today.replace(day=1),
        incurred_to=today,
    )


# --- Shared by the five generators ----------------------------------------------------


class _Generator:
    """The shared constructor (the plan and the two early checks injectable)."""

    name: str

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoAdminPlan] = plan_demo_admin,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        self._app_env = app_env
        self._plan = plan
        self._skipped = skipped

    async def _plan_for(self, session: AsyncSession) -> DemoAdminPlan:
        return self._plan(await administration.center_today(session))


async def _student_name(session: AsyncSession, student_id: uuid.UUID) -> StudentName:
    row = (
        await session.execute(
            select(Student.given_names, Student.family_names, Student.birthdate).where(
                Student.id == student_id
            )
        )
    ).one()
    return StudentName(row.given_names, row.family_names, row.birthdate)


# --- administration.demo_receipt_message (G1) -----------------------------------------


class DemoReceiptMessageGenerator(_Generator):
    """``administration.demo_receipt_message`` (G1, #121): ``demo.owner`` sets
    the receipt message in Spanish and English through
    ``update_business_settings``. Placed before ``billing.demo_pricing`` (E3),
    so every demo receipt is issued with it.

    The update adds no row (``0 rows``, as the #501 rename). Writes nothing
    outside ``APP_ENV=local`` or under ``--without-billing`` (skipped), and
    nothing once both messages equal the demo texts. Skipped when #299's
    schedule is from an earlier day or another message is already set.
    """

    name = "administration.demo_receipt_message"

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        owner = await demo_principal(session, DEMO_OWNER_USERNAME, self.name)
        plan = await self._plan_for(session)
        current = await administration.get_business_settings(session)
        stored = (current.receipt_message_es, current.receipt_message_en)
        if stored == (plan.receipt_message_es, plan.receipt_message_en):
            return 0
        if await _stale(session, self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        if stored != (None, None):
            return GeneratorSkipped(RECEIPT_MESSAGE_CONFIGURED)
        with seed_actor(session, owner.user_id):
            await update_business_settings(
                session,
                BusinessSettingsUpdate(
                    version=current.version,
                    receipt_message_es=plan.receipt_message_es,
                    receipt_message_en=plan.receipt_message_en,
                ),
                # No time-zone change, so the guard is never ensure()d.
                guard=RecentAuthGuard(TIME_ZONE_ACTION, None),
            )
        return 0


# --- administration.demo_admin (G2) ---------------------------------------------------

DEMO_ADMIN_TABLES = (NotificationOptOut, Notification)
"""The tables whose added rows ``administration.demo_admin`` counts (the two
assignments are updates, not counted)."""


async def _opted_out(session: AsyncSession, user_id: uuid.UUID) -> bool:
    found = await session.scalar(
        select(NotificationOptOut.id).where(
            NotificationOptOut.user_id == user_id,
            NotificationOptOut.notification_type == APPROVAL_ASSIGNED,
        )
    )
    return found is not None


async def qualification_item(session: AsyncSession, generator: str) -> ApprovalRequest:
    """The open ``staff.qualification`` item of ``demo.tutor``'s pending
    qualification (#299)."""
    tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, generator)
    found = await session.scalar(
        select(ApprovalRequest)
        .join(TutorQualification, TutorQualification.id == ApprovalRequest.subject_id)
        .where(
            ApprovalRequest.request_type == "staff.qualification",
            ApprovalRequest.status.in_(OPEN_STATUSES),
            TutorQualification.tutor_user_id == tutor_id,
        )
        .execution_options(populate_existing=True)
    )
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"demo.tutor's open qualification item must exist before {generator}"
        )
    return found


async def r2_item(session: AsyncSession, generator: str) -> ApprovalRequest:
    """R2's open ``academics.progress_report`` review item (#354: the weekly
    report ``demo.tutor`` submitted)."""
    tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, generator)
    found = await session.scalar(
        select(ApprovalRequest)
        .join(ProgressReport, ProgressReport.id == ApprovalRequest.subject_id)
        .where(
            ApprovalRequest.request_type == "academics.progress_report",
            ApprovalRequest.status.in_(OPEN_STATUSES),
            ProgressReport.author_user_id == tutor_id,
            ProgressReport.kind == "weekly",
        )
        .execution_options(populate_existing=True)
    )
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"R2's open review item must exist before {generator}"
        )
    return found


class DemoAdminGenerator(_Generator):
    """``administration.demo_admin`` (G2, #119 then #118): ``demo.coordinator``
    mutes ``administration.approval_assigned`` (E8); then ``demo.owner``
    assigns ``demo.tutor``'s pending qualification item to ``demo.admin``
    (who is notified) and R2's review item to ``demo.coordinator`` (muted,
    so not notified). Every other open item stays unassigned.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once ``demo.coordinator``'s opt-out
    exists. Skipped when #299's schedule is from an earlier day; raises when
    a demo account or either open item is missing.
    """

    name = "administration.demo_admin"

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        owner = await demo_principal(session, DEMO_OWNER_USERNAME, self.name)
        admin = await demo_principal(session, DEMO_ADMIN_USERNAME, self.name)
        coordinator = await demo_principal(
            session, DEMO_COORDINATOR_USERNAME, self.name
        )
        if await _opted_out(session, coordinator.user_id):
            return 0
        if await _stale(session, self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        qualification = await qualification_item(session, self.name)
        r2 = await r2_item(session, self.name)
        before = await count_rows(session, DEMO_ADMIN_TABLES)

        # E8: the coordinator's mute, through the preferences route's function.
        with seed_actor(session, coordinator.user_id):
            await update_notification_preferences(
                session,
                principal=coordinator,
                body=NotificationPreferencesBody.model_validate(
                    {
                        "items": [
                            {"notification_type": APPROVAL_ASSIGNED, "enabled": False}
                        ]
                    }
                ),
            )
        # #118: the Owner assigns the two items.
        with seed_actor(session, owner.user_id):
            for item, assignee in ((qualification, admin), (r2, coordinator)):
                await assign_approval_request(
                    session,
                    principal=owner,
                    request_id=item.id,
                    assignee_user_id=assignee.user_id,
                    version=item.version,
                )
        return await count_rows(session, DEMO_ADMIN_TABLES) - before


# --- administration.demo_imports (G3) -------------------------------------------------

DEMO_IMPORTS_TABLES = (
    ImportJob,
    ImportRow,
    Student,
    StudentEmergencyContact,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
    AssignmentOffer,
    AssignmentOfferSession,
    Notification,
    Charge,
)
"""The tables whose added rows ``administration.demo_imports`` counts."""


async def _imports_marker(session: AsyncSession) -> bool:
    found = await session.scalar(
        select(ImportJob.id).where(ImportJob.file_name.in_(IMPORT_FILES)).limit(1)
    )
    return found is not None


async def booked_slot(
    session: AsyncSession, tutor_id: uuid.UUID, generator: str
) -> BookedSlot:
    """``demo.tutor``'s first confirmed session starting more than a day
    from now (#299's ``U1``), in local time."""
    zone = await administration.get_center_time_zone(session)
    found = await session.scalar(
        select(ClassSession)
        .where(
            ClassSession.tutor_user_id == tutor_id,
            ClassSession.state == "confirmed",
            ClassSession.starts_at > clock.now() + timedelta(days=1),
        )
        .order_by(ClassSession.starts_at)
        .limit(1)
    )
    if found is None:
        raise DemoPrerequisiteMissingError(
            f"a future demo.tutor session must exist before {generator}"
        )
    local = found.starts_at.astimezone(ZoneInfo(zone))
    minutes = int((found.ends_at - found.starts_at).total_seconds() // 60)
    return BookedSlot(local.date(), local.strftime("%H:%M"), minutes)


async def _committed(
    session: AsyncSession, principal: Principal, kind: str, file_name: str, text: str
) -> uuid.UUID:
    """Stage, request the commit and run every batch, as the importer (E2)."""
    with seed_actor(session, principal.user_id):
        job = await imports.create_import_job(
            session,
            principal=principal,
            kind_code=kind,
            file_name=file_name,
            csv_text=text,
        )
        job = await imports.request_commit(
            session, principal=principal, job_id=job.id, version=job.version
        )
        while not await imports.commit_next_batch(session, job.id):
            pass
    return job.id


@dataclass(frozen=True)
class ImportedJobs:
    """The four jobs' ids of the last run (read by the tests)."""

    students: uuid.UUID
    tutors: uuid.UUID
    schedules: uuid.UUID
    balances: uuid.UUID


class DemoImportsGenerator(_Generator):
    """``administration.demo_imports`` (G3, #122/#123): ``demo.admin``
    imports three students (two new, one duplicate; committed), two tutors
    (one with an invalid email; left ``staged``) and two schedule rows (a
    weekly series for F3's student with ``demo.tutor``, which gets a pending
    offer, and a row at ``U1``'s time: ``failed`` ``scheduling_conflict``;
    committed); ``demo.finance`` imports two opening-balance charges for F3's
    student (committed). The commits run ``commit_next_batch`` in this Unit
    of Work (E2): no Procrastinate row.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once a job with a demo file name exists.
    Skipped when #299's schedule is from an earlier day; raises when a demo
    account or student is missing.
    """

    name = "administration.demo_imports"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoAdminPlan] = plan_demo_admin,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        super().__init__(app_env, plan, skipped)
        self.jobs: ImportedJobs | None = None

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        admin = await demo_principal(session, DEMO_ADMIN_USERNAME, self.name)
        finance = await demo_principal(session, DEMO_FINANCE_USERNAME, self.name)
        tutor_id = await demo_user_id(session, DEMO_TUTOR_USERNAME, self.name)
        if await _imports_marker(session):
            return 0
        if await _stale(session, self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        students = await billing_students(session, self.name)
        duplicate = await _student_name(session, students["A"])
        f3 = await _student_name(session, students["F3"])
        conflict = await booked_slot(session, tutor_id, self.name)
        plan = await self._plan_for(session)
        before = await count_rows(session, DEMO_IMPORTS_TABLES)

        students_job = await _committed(
            session,
            admin,
            students_kind.KIND,
            STUDENTS_FILE,
            plan.students_csv(duplicate),
        )
        with seed_actor(session, admin.user_id):
            tutors_job = await imports.create_import_job(
                session,
                principal=admin,
                kind_code=tutors_kind.KIND,
                file_name=TUTORS_FILE,
                csv_text=plan.tutors_csv(),
            )
        schedules_job = await _committed(
            session,
            admin,
            schedules_kind.KIND,
            SCHEDULES_FILE,
            plan.schedules_csv(f3, conflict),
        )
        balances_job = await _committed(
            session, finance, balances_kind.KIND, BALANCES_FILE, plan.balances_csv(f3)
        )
        self.jobs = ImportedJobs(
            students=students_job,
            tutors=tutors_job.id,
            schedules=schedules_job,
            balances=balances_job,
        )
        return await count_rows(session, DEMO_IMPORTS_TABLES) - before


# --- reporting.demo_setups (G4) -------------------------------------------------------


async def _setup_exists(session: AsyncSession, user_id: uuid.UUID, name: str) -> bool:
    found = await session.scalar(
        select(ReportSetup.id).where(
            ReportSetup.created_by_user_id == user_id,
            func.lower(ReportSetup.name) == name.lower(),
        )
    )
    return found is not None


class DemoSetupsGenerator(_Generator):
    """``reporting.demo_setups`` (G4, #125): ``demo.finance`` saves "Gastos del
    mes (demo)" (expenses, CSV, the month so far) shared with the Owner role;
    ``demo.admin`` saves "Alumnos activos (demo)" (students, XLSX, active),
    private. Through ``create_setup``.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped); each setup is written only when its creator has none with that
    name (``0 rows`` on a rerun). Skipped when #299's schedule is from an
    earlier day; raises when a demo account is missing.
    """

    name = "reporting.demo_setups"

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        finance = await demo_principal(session, DEMO_FINANCE_USERNAME, self.name)
        admin = await demo_principal(session, DEMO_ADMIN_USERNAME, self.name)
        plan = await self._plan_for(session)
        todo = [
            (who, body)
            for who, body in (
                (finance, plan.finance_setup()),
                (admin, plan.admin_setup()),
            )
            if not await _setup_exists(session, who.user_id, body.name)
        ]
        if not todo:
            return 0
        if await _stale(session, self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        before = await count_rows(session, (ReportSetup,))
        for who, body in todo:
            with seed_actor(session, who.user_id):
                await create_setup(session, principal=who, body=body)
        return await count_rows(session, (ReportSetup,)) - before


# --- academics.demo_deleted (G5) ------------------------------------------------------

DEMO_DELETED_TABLES = (LearningPlan, LearningPlanObjective, LearningPlanMilestone)
"""The tables whose added rows ``academics.demo_deleted`` counts (the soft
delete is an update)."""


class DemoDeletedGenerator(_Generator):
    """``academics.demo_deleted`` (G5, #129, E9): ``demo.tutor`` creates the
    learning-plan draft :data:`DELETED_PLAN_TITLE` for ``demo.student`` in
    Matemáticas (Secundaria), then deletes it (soft delete, on the real
    clock), so "Eliminados recientemente" lists it for 30 days.

    Writes nothing outside ``APP_ENV=local`` or under ``--without-billing``
    (skipped), and nothing (0 rows) once a plan with that title exists
    (deleted or not). Skipped when #299's schedule is from an earlier day;
    raises when a demo account, ``demo.student``'s profile or the subject is
    missing.
    """

    name = "academics.demo_deleted"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoAdminPlan] = plan_demo_admin,
        skipped: Callable[[], bool] = billing_skipped,
    ) -> None:
        super().__init__(app_env, plan, skipped)
        self.plan_id: uuid.UUID | None = None

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        early = _early_skip(self._app_env, self._skipped)
        if early is not None:
            return early
        tutor = await demo_principal(session, DEMO_TUTOR_USERNAME, self.name)
        plan = await self._plan_for(session)
        found = await session.scalar(
            select(LearningPlan.id).where(LearningPlan.title == plan.plan_title)
        )
        if found is not None:
            return 0
        if await _stale(session, self.name):
            return GeneratorSkipped(STALE_SCHEDULE)
        student_user_id = await demo_user_id(session, DEMO_STUDENT_USERNAME, self.name)
        student_id = await session.scalar(
            select(Student.id).where(Student.user_id == student_user_id)
        )
        if student_id is None:
            raise DemoPrerequisiteMissingError(
                f"the {DEMO_STUDENT_USERNAME} profile must exist before {self.name}"
            )
        subjects = await demo_subject_ids(session, (MATH_SECONDARY,), self.name)
        before = await count_rows(session, DEMO_DELETED_TABLES)
        with seed_actor(session, tutor.user_id):
            draft = await learning_plans.create_plan(
                session,
                principal=tutor,
                student_id=student_id,
                body=plan.plan_body(subjects[MATH_SECONDARY]),
            )
            await learning_plans.delete_plan(
                session, principal=tutor, plan_id=draft.id, version=draft.version
            )
        self.plan_id = draft.id
        return await count_rows(session, DEMO_DELETED_TABLES) - before
