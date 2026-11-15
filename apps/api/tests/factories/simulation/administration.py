"""``administration`` (#554 HF-01f): imports, approvals, notification
preferences and read state, a business-settings change and a scheduling
policy version, after ``finance``.

**The plan** (:func:`build_administration`, pure: no clock, no database; its
own Faker and RNG streams, so no earlier name or date moves):

- 2 Sep, the Administrator: a **students** import of three rows (#122): the
  two younger children of an existing family (created, then added to the
  family and linked to its guardians) and the family's first student again,
  upper-cased without accents (``duplicate``). Their guardian's paper
  consent is recorded with the other minors' on 19 Oct;
- 2 Sep, Finance: an **opening balances** import (#123) of two charges for
  two families, due on each family's first pay day after the import (so they
  are paid with that day's payment);
- 14 Sep: the first imported child starts a **trial** of two weekly
  classes in the second half of September with a tutor who teaches the
  subject, then stops: the "lost" student of the October retention KPI
  (K5). Held, closed out and approved like every other class; billed per
  class and paid by the family;
- 5 Oct, the Owner: the center's contact email changes (business settings
  history);
- 26 Oct: a **schedules** import (#123) by the Administrator: a weekly
  series of two classes from 23 Nov (inside every scheduling rule: the
  timetable's :class:`~.timetable.Calendar`), accepted by its tutor on 27
  Oct, and a row at one of that tutor's existing classes (``failed``
  ``scheduling_conflict``); the Owner publishes a scheduling policy version
  effective 1 Nov (the booking notice lowered from 24 h to 12 h: it can't
  refuse any planned booking);
- 18 Nov, the Administrator: a **tutors** import left ``staged`` (one ready
  row, one with an invalid email); it expires 7 days after its last change,
  a database-set real time (E9);
- at :data:`~.anchor.SIM_SETUP`: two accounts turn off one optional
  notification type each (#119): a tutor ``academics.closeout_approved``
  and a guardian ``academics.assignment_set``;
- at :data:`~.anchor.SIM_NOW`: the Owner assigns the pending qualification
  item to the Administrator and one progress-report review to the Academic
  coordinator (#118; the other open items stay unassigned), then every
  account reads the notifications whose business event is more than 7 days
  before the anchor (:data:`READ_BEFORE`).

Notifications carry only database-set times (E7: the whole run is one
transaction, so every ``created_at`` is the same), so the module notes the
ids that exist at the end of :data:`SNAPSHOT_DAY` (it runs after every other
module that day, and ``reporting`` writes nothing that day) and marks those
read at the anchor through ``mark_notification_read``.

The imports run ``create_import_job`` → ``request_commit`` →
``commit_next_batch`` in the run's Unit of Work, as the M8 demo does (#539
E2): no Procrastinate row.
"""

import csv
import io
import random
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.authz import Principal
from app.core.errors import AppError
from app.core.reauth import RecentAuthGuard
from app.modules.academics.models import (
    ProgressReport,
    SessionCloseout,
    SessionCloseoutApproval,
)
from app.modules.academics.schemas import CloseoutApprove, CloseoutSummaryInput
from app.modules.administration import service as administration
from app.modules.administration.approvals import (
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
from app.modules.administration.notifications import mark_notification_read
from app.modules.administration.schemas import (
    BusinessSettingsUpdate,
    NotificationPreferencesBody,
    SchedulingPolicyCreate,
    SchedulingPolicyRules,
)
from app.modules.administration.settings import update_business_settings
from app.modules.administration.settings_models import SchedulingPolicy
from app.modules.administration.settings_router import TIME_ZONE_ACTION
from app.modules.billing import imports as balances_kind
from app.modules.billing.models import Charge, ChargeDiscount
from app.modules.catalog.models import Subject
from app.modules.families import consent as families_consent
from app.modules.families import groupings, guardians
from app.modules.families import imports as students_kind
from app.modules.families.assignments import get_tutor_assignment_source
from app.modules.families.models import (
    ConsentRecord,
    FamilyStudent,
    Student,
    StudentEmergencyContact,
    StudentGuardian,
)
from app.modules.families.schemas import ConsentCreate, StudentGuardianCreate
from app.modules.identity import imports as tutors_kind
from app.modules.identity.models import User
from app.modules.scheduling import imports as schedules_kind
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_schemas import AttendanceMark
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionAttendance,
    ClassSessionParticipant,
)
from app.modules.scheduling.schemas import ClassSeriesCreate, SeriesRecurrence
from app.modules.staff.models import TutorQualification
from app.workflows import session_closeout, session_closeout_review
from app.workflows.session_closeout_schemas import SessionCloseoutSubmit
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_NOW,
    SIM_SETUP,
    SIM_SETUP_DAY,
    ZONE,
    local_instant,
    sim_clock,
)
from tests.factories.simulation.people import principal, user_key
from tests.factories.simulation.plan import (
    AGES,
    CONSENT_AT,
    SimulationPlan,
    _email,
    _Names,
    age_on,
    make_faker,
    qualified_tutors,
)
from tests.factories.simulation.schedule import (
    SimulationStepError,
    horizon_weeks,
    pending_offer,
    pick,
    tutor_principal,
)
from tests.factories.simulation.texts import TextPool
from tests.factories.simulation.timeline import SimulationContext
from tests.factories.simulation.timetable import (
    APPROVE_AFTER,
    CLOSEOUT_AFTER,
    GRID,
    MINOR_EARLIEST,
    OPERATING_HOURS,
    Calendar,
    Placement,
    hhmm,
    mins,
)

ADMIN_STREAM: Final = 554_100
"""HF-01f's own Faker and RNG streams (every earlier name and date stays)."""

IMPORTS_AT: Final = local_instant(date(2026, 9, 2), "10:00")
BALANCES_AT: Final = local_instant(date(2026, 9, 2), "11:00")
TRIAL_CREATED_AT: Final = local_instant(date(2026, 9, 14), "12:00")
TRIAL_ACCEPTED_AT: Final = local_instant(date(2026, 9, 14), "13:00")
TRIAL_FROM: Final = date(2026, 9, 15)
TRIAL_CLASSES: Final = 2
TRIAL_UNTIL: Final = date(2026, 9, 30)
"""The trial ends in September, so the child is "lost" in October (K5)."""
SETTINGS_AT: Final = local_instant(date(2026, 10, 5), "10:00")
SCHEDULES_AT: Final = local_instant(date(2026, 10, 26), "10:00")
POLICY_AT: Final = local_instant(date(2026, 10, 26), "18:00")
POLICY_EFFECTIVE: Final = local_instant(date(2026, 11, 1), "00:00")
BOOKING_NOTICE_HOURS: Final = 12
SERIES_ACCEPTED_AT: Final = local_instant(date(2026, 10, 27), "09:00")
SERIES_FROM: Final = date(2026, 11, 23)
SERIES_CLASSES: Final = 2
TUTORS_AT: Final = local_instant(date(2026, 11, 18), "16:00")
SNAPSHOT_DAY: Final = date(2026, 11, 11)
READ_BEFORE: Final = SIM_ANCHOR - timedelta(days=7)
"""Notifications whose business event is before this day (more than 7 days
before the anchor) are read at :data:`~.anchor.SIM_NOW`; later ones stay
unread."""
BALANCE_SERVICE_DATE: Final = date(2026, 8, 31)
ADULT_AGE: Final = 18

CLOSEOUT_APPROVED: Final = "academics.closeout_approved"
ASSIGNMENT_SET: Final = "academics.assignment_set"
QUALIFICATION_ITEM: Final = "staff.qualification"
REPORT_ITEM: Final = "academics.progress_report"


# --- The plan (pure) ------------------------------------------------------------------


@dataclass(frozen=True)
class ImportedStudent:
    key: str
    given_names: str
    family_names: str
    birthdate: date
    level: str
    contact_name: str
    contact_relationship: str
    contact_phone: str


@dataclass(frozen=True)
class OpeningBalance:
    family: str
    student: str
    description: str
    amount: str
    due_date: date


@dataclass(frozen=True)
class Trial:
    """The first imported child's two weekly trial classes."""

    student: str
    tutor: str
    subject: str
    start: str
    minutes: int
    dates: tuple[date, ...]

    def placements(self) -> tuple[Placement, ...]:
        return tuple(
            Placement(day, self.start, self.minutes, self.tutor, None)
            for day in self.dates
        )


@dataclass(frozen=True)
class ImportedSeries:
    """The schedules import's created row and its conflicting row."""

    student: str
    subject: str
    tutor: str
    start: str
    minutes: int
    dates: tuple[date, ...]
    conflict: Placement
    """One of the tutor's existing future classes (``failed``)."""


@dataclass(frozen=True)
class ImportedTutor:
    username: str
    display_name: str
    email: str


@dataclass(frozen=True)
class OptOut:
    username: str
    notification_type: str


@dataclass(frozen=True)
class Administration:
    family: str
    """The family the imported children join."""
    students: tuple[ImportedStudent, ...]
    duplicate: str
    """The plan student the students file repeats."""
    balances: tuple[OpeningBalance, ...]
    trial: Trial
    series: ImportedSeries
    tutors: tuple[ImportedTutor, ...]
    opt_outs: tuple[OptOut, ...]
    contact_email: str
    files: dict[str, str]

    def days(self) -> set[date]:
        instants = (
            IMPORTS_AT,
            BALANCES_AT,
            TRIAL_CREATED_AT,
            SETTINGS_AT,
            SCHEDULES_AT,
            POLICY_AT,
            SERIES_ACCEPTED_AT,
            TUTORS_AT,
            CONSENT_AT,
        )
        return {
            *(local_day(at) for at in instants),
            *self.trial.dates,
            SNAPSHOT_DAY,
        }


def local_day(at: datetime) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


def _birthdate(rng: random.Random, level: str, taken: set[date]) -> date:
    low, high = AGES[level]
    while True:
        age = rng.randint(low, high)
        latest = SIM_SETUP_DAY.replace(year=SIM_SETUP_DAY.year - age)
        earliest = latest.replace(year=latest.year - 1) + timedelta(days=1)
        found = earliest + timedelta(days=rng.randint(0, (latest - earliest).days))
        # A minor through the horizon (the guardian's consent applies).
        if found not in taken and age_on(found, SIM_HORIZON) < ADULT_AGE:
            return found


def calendar_of(plan: SimulationPlan) -> Calendar:
    """Every time the timetable ever takes, conservatively: each class at its
    first and at its final placement (whatever its status: the services see
    the first booking until it moves or is cancelled), every time off
    (approved, rejected or pending), the session requests' slots and the
    change requests' proposed slots."""
    timetable = plan.timetable
    cal = Calendar(plan)
    for off in timetable.time_offs:
        cal.time_off[(off.tutor, off.day)].append((mins(off.start), mins(off.end)))
    for c in timetable.classes:
        cal.book(c.key, c.students, c.placed)
        if c.final != c.placed:
            cal.book(f"{c.key}'", c.students, c.final)
    for r in timetable.session_requests:
        cal.book(r.key, (r.student,), r.placement)
    for change in timetable.change_requests:
        if change.proposed is not None:
            c = timetable.cls(change.cls)
            tutor = c.final.tutor
            p = change.proposed
            cal.book(
                change.key,
                c.students,
                Placement(p.day, p.start, p.minutes, p.tutor or tutor, p.room),
            )
    return cal


def _free_slot(
    cal: Calendar,
    tutors: Sequence[str],
    subject: str,
    minutes: int,
    first: date,
    count: int,
    *,
    minor: bool,
    students: Sequence[str] = (),
    until: date = SIM_HORIZON,
    avoid: Sequence[Placement] = (),
) -> tuple[str, str, tuple[date, ...]] | None:
    """The first (tutor, start, dates) of ``count`` weekly classes from the
    first weekday on or after ``first`` that break no rule and overlap none
    of ``avoid`` (the session requests' slots: a request needs a qualified
    tutor free when it is made)."""
    for offset in range(10):
        day = first + timedelta(days=offset)
        if day.isoweekday() > 5:
            continue
        dates = tuple(day + timedelta(weeks=k) for k in range(count))
        if dates[-1] > until:
            continue
        opens, closes = OPERATING_HOURS[day.isoweekday()]
        earliest = max(opens, MINOR_EARLIEST) if minor else opens
        for tutor in tutors:
            for lo in range(earliest, closes - minutes + 1, GRID):
                hi = lo + minutes
                ok = all(
                    cal.center_error(d, lo, hi) is None
                    and cal.tutor_error(tutor, subject, d, lo, hi) is None
                    and not any(
                        olo < hi and lo < ohi
                        for s in students
                        for olo, ohi, _ in cal.student[(s, d)]
                    )
                    and not any(a.day == d and a.lo < hi and lo < a.hi for a in avoid)
                    for d in dates
                )
                if ok:
                    return tutor, hhmm(lo), dates
    return None


def _students_of(plan: SimulationPlan, family: str) -> list[str]:
    return [s.key for f in plan.families if f.key == family for s in f.students]


def _pays_on_time(plan: SimulationPlan, family: str, dates: Sequence[date]) -> bool:
    """Each class is paid within a week of its date (billing B2 allows 20
    days late): by a payment of the family made once the charges exist
    (billing's session step posts them the evening after the series is
    created) whose cutoff covers the date."""
    posted = local_day(TRIAL_CREATED_AT) + timedelta(days=2)
    payments = [p for p in plan.billing.payments if p.family == family]
    return all(
        any(
            local_day(p.at) >= posted
            and d <= p.cutoff
            and local_day(p.at) - d <= timedelta(days=7)
            for p in payments
        )
        for d in dates
    )


def build_administration(plan: SimulationPlan, seed: int) -> Administration:
    """HF-01f's administration plan for ``plan`` (pure)."""
    timetable = plan.timetable
    billing = plan.billing
    texts = TextPool(plan.language)
    # Deterministic synthetic data, not security: a seeded generator.
    rng = random.Random(seed + ADMIN_STREAM)  # noqa: S311
    names = _Names(plan.language, make_faker(plan.language, seed + ADMIN_STREAM))
    names.full_names = {
        *(f"{s.given_names} {s.family_names}" for s in plan.students),
        *(f"{g.given_names} {g.family_names}" for g in plan.guardians),
        *(f"{t.given_names} {t.family_names}" for t in plan.tutors),
    }
    names.usernames |= {a.username for a in plan.accounts} | {
        plan.pending_registration.username,
        plan.pending_tutor.username,
    }
    individual = [s for s in timetable.series if s.kind == "individual"]
    excluded = {*billing.overdue_families, *billing.ahead_families}
    cal = calendar_of(plan)
    requested = [r.placement for r in timetable.session_requests]

    # The family: enrolled at setup, not registered, regularly paying, with a
    # minor in an individual series; the trial is its first slot that fits.
    trial: Trial | None = None
    family_key = ""
    for family in plan.families:
        if (
            family.registered
            or family.enrolls_at != SIM_SETUP
            or family.key in excluded
            or not family.guardians
        ):
            continue
        minors = [s for s in family.students if not s.is_adult]
        series = next(
            (s for s in individual if minors and s.students[0] == minors[0].key), None
        )
        if series is None:
            continue
        tutors = [
            series.tutor,
            *(t for t in qualified_tutors(plan, series.subject, TRIAL_FROM)),
        ]
        found = _free_slot(
            cal,
            list(dict.fromkeys(tutors)),
            series.subject,
            series.minutes,
            TRIAL_FROM,
            TRIAL_CLASSES,
            minor=True,
            until=TRIAL_UNTIL,
            avoid=requested,
        )
        if found is None or not _pays_on_time(plan, family.key, found[2]):
            continue
        tutor, start, dates = found
        family_key = family.key
        trial = Trial("imp1", tutor, series.subject, start, series.minutes, dates)
        sibling = minors[0]
        break
    if trial is None:
        raise RuntimeError("no family fits the imported children's trial")
    for k, where in enumerate(trial.placements()):
        cal.book(f"trial{k}", (trial.student,), where)

    relationships = texts.choices("contact_relationships")
    taken = {s.birthdate for s in plan.students}
    students = []
    for n, key in enumerate(("imp1", "imp2")):
        given, family_names = names.person(rng.random() < 0.5, sibling.family_names)
        contact_given = names.given(rng.random() < 0.5)
        birthdate = _birthdate(rng, sibling.level, taken)
        taken.add(birthdate)
        students.append(
            ImportedStudent(
                key=key,
                given_names=given,
                family_names=family_names,
                birthdate=birthdate,
                level=sibling.level,
                contact_name=f"{contact_given} {family_names}",
                contact_relationship=relationships[n % len(relationships)],
                contact_phone=f"555-01{90 + n:02d}",
            )
        )

    # Opening balances: two other regular families, due on their first pay
    # day after the import (the payment that day settles them).
    descriptions = texts.choices("opening_balance_descriptions")
    amounts = ("850.00", "1240.00")
    balances: list[OpeningBalance] = []
    for family in plan.families:
        if len(balances) == 2:
            break
        if (
            family.key in excluded
            or family.key == family_key
            or family.enrolls_at != SIM_SETUP
            or not family.students
        ):
            continue
        pay_days = sorted(
            local_day(p.at)
            for p in billing.payments
            if p.family == family.key and local_day(p.at) > local_day(BALANCES_AT)
        )
        if not pay_days:
            continue
        n = len(balances)
        balances.append(
            OpeningBalance(
                family.key,
                family.students[0].key,
                descriptions[n],
                amounts[n],
                pay_days[0],
            )
        )

    # The schedules import: a per-class student's second weekly slot from 23
    # Nov, with their tutor or another qualified one (not the one asking to
    # change availability).
    series_plan: ImportedSeries | None = None
    weekly_tutor = timetable.weekly_change.tutor
    for s in individual:
        student = s.students[0]
        if (
            student in {trial.student}
            or billing.mode_on(student, SERIES_FROM) != "per_class"
            or s.until < SIM_HORIZON
        ):
            continue
        family = next(f for f in plan.families if student in _students_of(plan, f.key))
        if family.key in excluded:
            continue
        minor = not next(x for x in plan.students if x.key == student).is_adult
        tutors = [
            t
            for t in dict.fromkeys(
                [s.tutor, *qualified_tutors(plan, s.subject, SERIES_FROM)]
            )
            if t != weekly_tutor
        ]
        found = _free_slot(
            cal,
            tutors,
            s.subject,
            s.minutes,
            SERIES_FROM,
            SERIES_CLASSES,
            minor=minor,
            students=(student,),
            avoid=requested,
        )
        if found is None:
            continue
        tutor, start, dates = found
        conflict = next(
            (
                c.placed
                for c in timetable.classes
                if c.final.tutor == tutor
                and c.placed == c.final
                and c.status == "confirmed"
                and c.placed.day > SERIES_FROM
                and c.series is not None
                and not c.rescheduled
                and not c.substituted
            ),
            None,
        )
        if conflict is None:
            continue
        series_plan = ImportedSeries(
            student, s.subject, tutor, start, s.minutes, dates, conflict
        )
        break
    if series_plan is None:
        raise RuntimeError("no slot for the imported series")

    # The tutors file: one ready row and one with an invalid email.
    imported_tutors: list[ImportedTutor] = []
    for n in range(2):
        given, family_names = names.person(
            rng.random() < 0.5, " ".join(names.surname() for _ in range(1))
        )
        username = names.username(given, family_names)
        email = (
            _email(given, family_names, 90 + n)
            if n == 0
            else texts.text("invalid_email")
        )
        imported_tutors.append(
            ImportedTutor(username, f"{given} {family_names}", email)
        )

    # Opt-outs at setup: a tutor with closeouts and a guardian whose child
    # gets homework, both with accounts at setup.
    homework_students = {h.student for h in plan.academics.homework}
    guardian = next(
        g.account.username
        for f in plan.families
        if f.enrolls_at == SIM_SETUP and f.key != family_key
        for g in f.guardians
        if g.account is not None
        and g.account.created_at <= SIM_SETUP
        and any(s.key in homework_students for s in f.students)
    )
    tutor_account = next(
        t.account.username for t in plan.tutors if t.joins_at <= SIM_SETUP
    )
    return Administration(
        family=family_key,
        students=tuple(students),
        duplicate=sibling.key,
        balances=tuple(balances),
        trial=trial,
        series=series_plan,
        tutors=tuple(imported_tutors),
        opt_outs=(
            OptOut(tutor_account, CLOSEOUT_APPROVED),
            OptOut(guardian, ASSIGNMENT_SET),
        ),
        contact_email=texts.text("contact_email_updated"),
        files=texts.mapping("import_files"),
    )


# --- CSV ------------------------------------------------------------------------------


def _shouted(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def to_csv(columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return out.getvalue()


def _key_of(given: str, family: str, birthdate: date) -> dict[str, str]:
    return {
        "student_given_names": given,
        "student_family_names": family,
        "student_birthdate": birthdate.isoformat(),
    }


def students_csv(ctx: SimulationContext) -> str:
    admin = ctx.plan.administration
    levels = ctx.texts.mapping("education_levels")
    rows = [
        {
            "given_names": s.given_names,
            "family_names": s.family_names,
            "birthdate": s.birthdate.isoformat(),
            "education_level": levels[s.level],
            "contact_name": s.contact_name,
            "contact_relationship": s.contact_relationship,
            "contact_phone": s.contact_phone,
        }
        for s in admin.students
    ]
    dup = next(s for s in ctx.plan.students if s.key == admin.duplicate)
    rows.append(
        {
            "given_names": _shouted(dup.given_names),
            "family_names": _shouted(dup.family_names),
            "birthdate": dup.birthdate.isoformat(),
            "education_level": levels[dup.level],
            "contact_name": dup.contacts[0].name,
            "contact_relationship": dup.contacts[0].relationship,
            "contact_phone": dup.contacts[0].phone,
        }
    )
    return to_csv([c.name for c in students_kind.COLUMNS], rows)


def balances_csv(ctx: SimulationContext) -> str:
    rows = []
    for b in ctx.plan.administration.balances:
        s = next(x for x in ctx.plan.students if x.key == b.student)
        rows.append(
            {
                **_key_of(s.given_names, s.family_names, s.birthdate),
                "description": b.description,
                "amount": b.amount,
                "service_date": BALANCE_SERVICE_DATE.isoformat(),
                "due_date": b.due_date.isoformat(),
            }
        )
    return to_csv([c.name for c in balances_kind.COLUMNS], rows)


def schedules_csv(ctx: SimulationContext) -> str:
    series = ctx.plan.administration.series
    s = next(x for x in ctx.plan.students if x.key == series.student)
    subject = next(x for x in ctx.plan.subjects if x.key == series.subject)
    common = {
        **_key_of(s.given_names, s.family_names, s.birthdate),
        "subject": subject.name,
        "education_level": ctx.texts.mapping("education_levels")[subject.level],
        "tutor_username": ctx.plan.tutor(series.tutor).account.username,
    }
    rows = [
        {
            **common,
            "starts_on": series.dates[0].isoformat(),
            "start_time": series.start,
            "duration_minutes": str(series.minutes),
            "weekdays": str(series.dates[0].isoweekday()),
            "count": str(len(series.dates)),
        },
        {
            **common,
            "starts_on": series.conflict.day.isoformat(),
            "start_time": series.conflict.start,
            "duration_minutes": str(series.conflict.minutes),
            "weekdays": str(series.conflict.day.isoweekday()),
            "count": "1",
        },
    ]
    return to_csv([c.name for c in schedules_kind.COLUMNS], rows)


def tutors_csv(ctx: SimulationContext) -> str:
    rows = [
        {"username": t.username, "display_name": t.display_name, "email": t.email}
        for t in ctx.plan.administration.tutors
    ]
    return to_csv([c.name for c in tutors_kind.COLUMNS], rows)


# --- The module -----------------------------------------------------------------------

ADMINISTRATION_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    ImportJob,
    ImportRow,
    Student,
    StudentEmergencyContact,
    FamilyStudent,
    StudentGuardian,
    ConsentRecord,
    Charge,
    ChargeDiscount,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSessionAttendance,
    SessionCloseout,
    SessionCloseoutApproval,
    ApprovalRequest,
    Notification,
    NotificationOptOut,
    SchedulingPolicy,
)
"""The tables whose rows ``administration`` counts (the settings change,
the assignments and the read marks are updates)."""

type Action = Callable[[AsyncSession, SimulationContext], Awaitable[None]]


async def _staff(session: AsyncSession, ctx: SimulationContext, role: str) -> Principal:
    account = {
        "owner": ctx.plan.owner,
        "admin": ctx.plan.administrator,
        "coordinator": ctx.plan.coordinator,
        "finance": ctx.plan.finance,
    }[role]
    return await principal(session, ctx, account)


async def _by_username(
    session: AsyncSession, ctx: SimulationContext, username: str
) -> Principal:
    account = next(a for a in ctx.plan.accounts if a.username == username)
    return await principal(session, ctx, account)


class AdministrationModule:
    """HF-01f's ``administration`` module (module docstring)."""

    name = "administration"
    tables = ADMINISTRATION_TABLES

    def __init__(self) -> None:
        self.snapshot: list[uuid.UUID] = []

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (await session.scalar(select(ImportJob.id).limit(1))) is not None

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return ctx.plan.administration.days()

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        self.snapshot = []
        for opt_out in ctx.plan.administration.opt_outs:
            me = await _by_username(session, ctx, opt_out.username)
            with seed_actor(session, me.user_id):
                await update_notification_preferences(
                    session,
                    principal=me,
                    body=NotificationPreferencesBody.model_validate(
                        {
                            "items": [
                                {
                                    "notification_type": opt_out.notification_type,
                                    "enabled": False,
                                }
                            ]
                        }
                    ),
                )

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        for at, action in sorted(_actions(ctx, day), key=lambda item: item[0]):
            with sim_clock(at):
                try:
                    await action(session, ctx)
                except AppError as exc:
                    raise SimulationStepError(
                        f"administration {at}: {exc.code}"
                        f" {exc.conflicts or exc.errors or exc.details}"
                    ) from exc
        if day == SNAPSHOT_DAY:
            # Every notification so far has a business event on or before
            # this day (this module runs after the others; ``reporting``
            # writes nothing today).
            self.snapshot = list(
                await session.scalars(
                    select(Notification.id).where(Notification.read_at.is_(None))
                )
            )
        await session.flush()
        session.expunge_all()

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        owner = await _staff(session, ctx, "owner")
        targets = (
            (QUALIFICATION_ITEM, await _staff(session, ctx, "admin")),
            (REPORT_ITEM, await _staff(session, ctx, "coordinator")),
        )
        with seed_actor(session, owner.user_id):
            for request_type, assignee in targets:
                item = await _first_open_item(session, request_type)
                if item is None:
                    raise SimulationStepError(f"no open {request_type} item")
                await assign_approval_request(
                    session,
                    principal=owner,
                    request_id=item.id,
                    assignee_user_id=assignee.user_id,
                    version=item.version,
                )
        recipients = dict(
            (
                await session.execute(
                    select(Notification.id, Notification.recipient_user_id).where(
                        Notification.id.in_(self.snapshot)
                    )
                )
            ).all()
        )
        for notification_id in sorted(self.snapshot):
            await mark_notification_read(
                session,
                recipient_user_id=recipients[notification_id],
                notification_id=notification_id,
            )
        await session.flush()


async def _first_open_item(
    session: AsyncSession, request_type: str
) -> ApprovalRequest | None:
    """The open, unassigned item of ``request_type`` first by its subject's
    natural key: ids and ``created_at`` can't order them (one transaction:
    one ``created_at``; ids made in the same millisecond have a random
    order), and the choice must be the same on every run (C4)."""
    if request_type == REPORT_ITEM:
        key: tuple[Any, ...] = (
            ProgressReport.period_start,
            Student.family_names,
            Student.given_names,
            ProgressReport.kind,
        )
        query = (
            select(ApprovalRequest)
            .join(ProgressReport, ProgressReport.id == ApprovalRequest.subject_id)
            .join(Student, Student.id == ProgressReport.student_id)
        )
    else:
        key = (User.username, Subject.name)
        query = (
            select(ApprovalRequest)
            .join(
                TutorQualification,
                TutorQualification.id == ApprovalRequest.subject_id,
            )
            .join(User, User.id == TutorQualification.tutor_user_id)
            .join(Subject, Subject.id == TutorQualification.subject_id)
        )
    result: ApprovalRequest | None = await session.scalar(
        query.where(
            ApprovalRequest.request_type == request_type,
            ApprovalRequest.status.in_(OPEN_STATUSES),
            ApprovalRequest.assignee_user_id.is_(None),
        )
        .order_by(*key)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return result


# --- The dated actions ----------------------------------------------------------------


def _actions(ctx: SimulationContext, day: date) -> list[tuple[datetime, Action]]:
    admin = ctx.plan.administration
    planned: list[tuple[datetime, Action]] = [
        (IMPORTS_AT, _import_students),
        (BALANCES_AT, _import_balances),
        (TRIAL_CREATED_AT, _create_trial),
        (TRIAL_ACCEPTED_AT, _accept_trial),
        (CONSENT_AT, _consents),
        (SETTINGS_AT, _change_settings),
        (SCHEDULES_AT, _import_schedules),
        (POLICY_AT, _publish_policy),
        (SERIES_ACCEPTED_AT, _accept_series),
        (TUTORS_AT, _import_tutors),
    ]
    for k, where in enumerate(admin.trial.placements()):
        planned.append((where.ends_at + CLOSEOUT_AFTER, _hold(k)))
        planned.append((where.ends_at + APPROVE_AFTER, _approve(k)))
    return [(at, action) for at, action in planned if local_day(at) == day]


async def _job(
    session: AsyncSession,
    me: Principal,
    kind: str,
    file_name: str,
    text: str,
    *,
    commit: bool = True,
) -> uuid.UUID:
    """Stage; with ``commit``, request the commit and run every batch (#539
    E2: in this Unit of Work, no Procrastinate row)."""
    with seed_actor(session, me.user_id):
        job = await imports.create_import_job(
            session, principal=me, kind_code=kind, file_name=file_name, csv_text=text
        )
        if commit:
            job = await imports.request_commit(
                session, principal=me, job_id=job.id, version=job.version
            )
            while not await imports.commit_next_batch(session, job.id):
                pass
    return job.id


async def _created(session: AsyncSession, job_id: uuid.UUID) -> list[uuid.UUID]:
    found = await session.scalars(
        select(ImportRow.record_id)
        .where(ImportRow.job_id == job_id, ImportRow.status == "created")
        .order_by(ImportRow.row_number)
    )
    return [r for r in found if r is not None]


async def _import_students(session: AsyncSession, ctx: SimulationContext) -> None:
    """The Administrator imports the children, adds them to the family and
    links its guardians."""
    plan = ctx.plan
    admin_plan = plan.administration
    me = await _staff(session, ctx, "admin")
    job = await _job(
        session, me, students_kind.KIND, admin_plan.files["students"], students_csv(ctx)
    )
    created = await _created(session, job)
    if len(created) != len(admin_plan.students):
        raise SimulationStepError(f"students import created {len(created)}")
    family = next(f for f in plan.families if f.key == admin_plan.family)
    source = get_tutor_assignment_source()
    with seed_actor(session, me.user_id):
        for student, student_id in zip(admin_plan.students, created, strict=True):
            ctx.ids[f"student:{student.key}"] = student_id
            await groupings.add_student(
                session,
                principal=me,
                family_id=ctx.id(f"family:{family.key}"),
                student_id=student_id,
            )
            for guardian in family.guardians:
                await guardians.link_guardian(
                    session,
                    principal=me,
                    source=source,
                    student_id=student_id,
                    body=StudentGuardianCreate(
                        guardian_id=ctx.id(f"guardian:{guardian.key}"),
                        relationship=guardian.relationship,  # type: ignore[arg-type]
                    ),
                )


async def _import_balances(session: AsyncSession, ctx: SimulationContext) -> None:
    me = await _staff(session, ctx, "finance")
    job = await _job(
        session,
        me,
        balances_kind.KIND,
        ctx.plan.administration.files["balances"],
        balances_csv(ctx),
    )
    if len(await _created(session, job)) != len(ctx.plan.administration.balances):
        raise SimulationStepError("opening balances import")


async def _create_trial(session: AsyncSession, ctx: SimulationContext) -> None:
    trial = ctx.plan.administration.trial
    admin = await _staff(session, ctx, "admin")
    with seed_actor(session, admin.user_id):
        created = await scheduling.create_class_series(
            session,
            ClassSeriesCreate(
                kind="individual",
                student_id=ctx.id(f"student:{trial.student}"),
                subject_id=ctx.id(f"subject:{trial.subject}"),
                tutor_user_id=ctx.id(
                    user_key(ctx.plan.tutor(trial.tutor).account.username)
                ),
                classroom_id=None,
                starts_on=trial.dates[0],
                start_time=time.fromisoformat(trial.start),
                duration_minutes=trial.minutes,
                recurrence=SeriesRecurrence(
                    weekdays=[trial.dates[0].isoweekday()],
                    interval=1,
                    until=trial.dates[-1],
                ),
            ),
            horizon_weeks=horizon_weeks(local_day(TRIAL_CREATED_AT)),
            actor_id=admin.user_id,
        )
    if created.warnings:
        raise SimulationStepError(f"trial: {list(created.warnings)}")
    ctx.ids["series:trial"] = created.id


async def _accept(
    session: AsyncSession, ctx: SimulationContext, tutor: str, series_id: uuid.UUID
) -> None:
    me = await tutor_principal(session, ctx, tutor)
    offer = await pending_offer(session, tutor=me.user_id, series_id=series_id)
    with seed_actor(session, me.user_id):
        await scheduling.answer_offer(session, offer, caller_id=me.user_id, accept=True)


async def _accept_trial(session: AsyncSession, ctx: SimulationContext) -> None:
    await _accept(
        session, ctx, ctx.plan.administration.trial.tutor, ctx.id("series:trial")
    )


async def _trial_class(
    session: AsyncSession, ctx: SimulationContext, k: int
) -> uuid.UUID:
    where = ctx.plan.administration.trial.placements()[k]
    found = await session.scalar(
        select(ClassSession.id).where(
            ClassSession.series_id == ctx.id("series:trial"),
            ClassSession.original_start == where.starts_at,
        )
    )
    if found is None:
        raise SimulationStepError(f"no trial class at {where.starts_at}")
    return found


def _hold(k: int) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        trial = ctx.plan.administration.trial
        texts = ctx.texts
        key = f"trial{k}"
        me = await tutor_principal(session, ctx, trial.tutor)
        target = await _trial_class(session, ctx, k)
        version = await session.scalar(
            select(ClassSession.version).where(ClassSession.id == target)
        )
        assert version is not None  # noqa: S101 - the class exists
        with seed_actor(session, me.user_id):
            marked = await scheduling.record_attendance(
                session,
                actor=me,
                class_session_id=target,
                version=version,
                marks=[
                    AttendanceMark(
                        student_id=ctx.id(f"student:{trial.student}"),
                        status="present",
                    )
                ],
            )
            await session_closeout.submit_session_closeout(
                session,
                principal=me,
                class_session_id=target,
                body=SessionCloseoutSubmit(
                    version=marked.version,
                    actual_minutes=trial.minutes,
                    summary=CloseoutSummaryInput(
                        topics=pick(texts.lists("closeout_topics")[trial.subject], key),
                        performance=pick(texts.choices("closeout_performance"), key),
                        homework=pick(texts.choices("closeout_homework"), key),
                        next_steps=pick(texts.choices("closeout_next_steps"), key),
                    ),
                ),
            )

    return run


def _approve(k: int) -> Action:
    async def run(session: AsyncSession, ctx: SimulationContext) -> None:
        coordinator = await _staff(session, ctx, "coordinator")
        target = await _trial_class(session, ctx, k)
        version = await session.scalar(
            select(SessionCloseout.version).where(
                SessionCloseout.class_session_id == target
            )
        )
        assert version is not None  # noqa: S101 - submitted at the hold
        with seed_actor(session, coordinator.user_id):
            await session_closeout_review.approve_closeout(
                session,
                principal=coordinator,
                class_session_id=target,
                body=CloseoutApprove(version=version),
            )

    return run


async def _consents(session: AsyncSession, ctx: SimulationContext) -> None:
    """The family's paper consent for the imported minors, with the other
    minors' (19 Oct, after the privacy notice is published)."""
    family = next(
        f for f in ctx.plan.families if f.key == ctx.plan.administration.family
    )
    guardian = family.guardians[0]
    admin = await _staff(session, ctx, "admin")
    with seed_actor(session, admin.user_id):
        for student in ctx.plan.administration.students:
            await families_consent.record_consent(
                session,
                principal=admin,
                source=get_tutor_assignment_source(),
                student_id=ctx.id(f"student:{student.key}"),
                body=ConsentCreate(
                    scope="student_login",
                    guardian_id=ctx.id(f"guardian:{guardian.key}"),
                    method="paper",
                ),
            )


async def _change_settings(session: AsyncSession, ctx: SimulationContext) -> None:
    owner = await _staff(session, ctx, "owner")
    current = await administration.get_business_settings(session)
    with seed_actor(session, owner.user_id):
        await update_business_settings(
            session,
            BusinessSettingsUpdate(
                version=current.version,
                contact_email=ctx.plan.administration.contact_email,
            ),
            # No time-zone change, so the guard is never ensure()d.
            guard=RecentAuthGuard(TIME_ZONE_ACTION, None),
        )


async def _import_schedules(session: AsyncSession, ctx: SimulationContext) -> None:
    me = await _staff(session, ctx, "admin")
    job = await _job(
        session,
        me,
        schedules_kind.KIND,
        ctx.plan.administration.files["schedules"],
        schedules_csv(ctx),
    )
    created = await _created(session, job)
    if len(created) != 1:
        raise SimulationStepError(f"schedules import created {len(created)}")
    ctx.ids["series:imported"] = created[0]


async def _accept_series(session: AsyncSession, ctx: SimulationContext) -> None:
    await _accept(
        session,
        ctx,
        ctx.plan.administration.series.tutor,
        ctx.id("series:imported"),
    )


async def _publish_policy(session: AsyncSession, ctx: SimulationContext) -> None:
    owner = await _staff(session, ctx, "owner")
    current = await administration.get_scheduling_policy(session, SIM_NOW)
    rules = SchedulingPolicyRules.model_validate(current, from_attributes=True)
    with seed_actor(session, owner.user_id):
        await administration.create_scheduling_policy(
            session,
            SchedulingPolicyCreate(
                **{
                    **rules.model_dump(),
                    "booking_notice_hours": BOOKING_NOTICE_HOURS,
                },
                based_on_id=current.id,
                effective_from=POLICY_EFFECTIVE,
            ),
        )


async def _import_tutors(session: AsyncSession, ctx: SimulationContext) -> None:
    me = await _staff(session, ctx, "admin")
    await _job(
        session,
        me,
        tutors_kind.KIND,
        ctx.plan.administration.files["tutors"],
        tutors_csv(ctx),
        commit=False,
    )
