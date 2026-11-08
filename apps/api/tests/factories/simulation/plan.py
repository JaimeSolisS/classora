"""The pure plan of the simulation (#543): :func:`plan_simulation`.

``plan_simulation(language, profile, seed)`` returns frozen dataclasses:
the staff, the tutors and their profiles, the families with guardians and
students, the catalog, and the dated people events (registrations,
delegations, the pending invitation). It uses a ``random.Random(seed)`` and
a Faker instance (``es_MX`` or ``en_US``) seeded with ``seed``, so the same
arguments give the same plan on any day. It never reads ``clock.now()`` or
the database. HF-01b extends it with the timetable and its outcomes.

**Profiles (E4).** :data:`FULL` (40 students in 30 families, 42 guardians,
12 tutors, 4 staff) is what the make targets run; :data:`SMALL` (10
students in 8 families, 10 guardians, 4 tutors) is for the tests. The
structure of each profile is fixed here (family shapes, the tutors'
qualification matrix, archetypes, limits, exceptions); Faker gives only the
person names.

**Qualification matrix (owner update 2026-11-07).** Each subject has at
least two approved tutors (also before tutor 12 joins); in :data:`FULL`,
five subjects have three or more with different availability archetypes,
Calculus has exactly one tutor available in the evening, two tutors are
generalists (five subjects across two levels) and two are specialists (two
subjects at one level). One more qualification is rejected (September),
one approved and then revoked on 2026-10-20 (#198), and one is pending
(requested 2026-11-18). :func:`available_tutors` answers "who could teach
this subject in this slot" from the plan alone (D7b).
"""

import random
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

import faker

from tests.factories.base import RESERVED_EMAIL_DOMAINS
from tests.factories.simulation.anchor import (
    SIM_NOW,
    SIM_SETUP,
    SIM_SETUP_DAY,
    local_instant,
)
from tests.factories.simulation.texts import Language, TextPool

if TYPE_CHECKING:
    from tests.factories.simulation.billing_plan import Billing
    from tests.factories.simulation.coursework import Coursework
    from tests.factories.simulation.expenses_plan import Expenses
    from tests.factories.simulation.payroll_plan import Payroll
    from tests.factories.simulation.timetable import Timetable

type ProfileName = Literal["full", "small"]
type Archetype = Literal["mornings", "afternoons", "evenings", "full_days"]
type Window = tuple[int, str, str]
"""ISO weekday (1 = Monday), ``HH:MM`` start, ``HH:MM`` end (local)."""

FAKER_LOCALES: Final[dict[Language, str]] = {"es": "es_MX", "en": "en_US"}

ELEMENTARY: Final = "elementary"
MIDDLE_SCHOOL: Final = "middle_school"
HIGH_SCHOOL: Final = "high_school"
UNIVERSITY: Final = "university"
LEVELS: Final = (ELEMENTARY, MIDDLE_SCHOOL, HIGH_SCHOOL, UNIVERSITY)
"""The ``seed_key`` of each reference education level."""

AGES: Final[dict[str, tuple[int, int]]] = {
    ELEMENTARY: (6, 11),
    MIDDLE_SCHOOL: (12, 14),
    HIGH_SCHOOL: (15, 17),
    UNIVERSITY: (18, 22),
}
"""The age range on :data:`SIM_SETUP_DAY` of a student at each level."""

SUBJECT_LEVELS: Final[dict[str, str]] = {
    "math_e": ELEMENTARY,
    "reading_e": ELEMENTARY,
    "english_e": ELEMENTARY,
    "math_m": MIDDLE_SCHOOL,
    "english_m": MIDDLE_SCHOOL,
    "science_m": MIDDLE_SCHOOL,
    "math_h": HIGH_SCHOOL,
    "physics_h": HIGH_SCHOOL,
    "chemistry_h": HIGH_SCHOOL,
    "english_h": HIGH_SCHOOL,
    "calculus_u": UNIVERSITY,
    "programming_u": UNIVERSITY,
}

# --- Passwords and fixed usernames (E6; ADR-0007 amendment) -------------------

# Local development placeholders, never real credentials: the simulation runs
# only with APP_ENV=local (E5). Documented in the README ("Simulated
# operations data").
OWNER_PASSWORD: Final = "classora-sim-owner"
ADMIN_PASSWORD: Final = "classora-sim-admin"
COORDINATOR_PASSWORD: Final = "classora-sim-coordinator"
FINANCE_PASSWORD: Final = "classora-sim-finance"
TUTOR_PASSWORD: Final = "classora-sim-tutor"
GUARDIAN_PASSWORD: Final = "classora-sim-guardian"
STUDENT_PASSWORD: Final = "classora-sim-student"

STAFF_USERNAMES: Final[dict[Language, dict[str, str]]] = {
    "es": {
        "owner": "direccion",
        "administrator": "administracion",
        "academic_coordinator": "coordinacion",
        "finance_staff": "finanzas",
    },
    "en": {
        "owner": "owner",
        "administrator": "admin",
        "academic_coordinator": "coordinator",
        "finance_staff": "finance",
    },
}
"""The four staff usernames per language, by role code."""

STAFF_PASSWORDS: Final[dict[str, str]] = {
    "owner": OWNER_PASSWORD,
    "administrator": ADMIN_PASSWORD,
    "academic_coordinator": COORDINATOR_PASSWORD,
    "finance_staff": FINANCE_PASSWORD,
}

THEMES: Final[tuple[str | None, ...]] = ("light", "dark", None)
"""The login accounts' themes, in turn (``None``: left unset)."""

# --- The dated people events --------------------------------------------------

REGISTRATION_SUBMITTED: Final = local_instant(date(2026, 9, 7), "09:00")
REGISTRATION_APPROVED: Final = local_instant(date(2026, 9, 7), "12:00")
PENDING_REGISTRATION_AT: Final = local_instant(date(2026, 11, 18), "17:00")
PRIVACY_DRIVE_DAY: Final = date(2026, 10, 19)
"""The first day after privacy-notice version 1 is published (migration
0017: 2026-10-18): the login accounts accept it and the minors' consents
are recorded (#543 D2: no consent or acceptance can exist before)."""
PRIVACY_ACCEPTED_AT: Final = local_instant(PRIVACY_DRIVE_DAY, "09:00")
CONSENT_AT: Final = local_instant(PRIVACY_DRIVE_DAY, "09:05")
TEEN_LOGIN_AT: Final = local_instant(PRIVACY_DRIVE_DAY, "09:30")
TUTOR_JOINS_ON: Final = date(2026, 10, 1)
QUALIFICATION_REJECT_REQUESTED: Final = date(2026, 9, 8)
QUALIFICATION_REJECTED: Final = date(2026, 9, 10)
QUALIFICATION_REVOKED: Final = date(2026, 10, 20)
QUALIFICATION_PENDING_REQUESTED: Final = date(2026, 11, 18)
ROOM_DEACTIVATED_ON: Final = date(2026, 10, 30)

PAYROLL_FINALIZE: Final = "payroll.finalize"
FINANCE_REFUND_APPROVE: Final = "finance.refund.approve"


# --- Profiles -------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyShape:
    """One family: how many students and guardians, an adult student (no
    guardian in the shape; HF-01d adds the parent who pays,
    :func:`_adult_payers`), its enrollment day (``None``: :data:`SIM_SETUP`) and whether
    its first guardian self-registered (the approved registration)."""

    students: int
    guardians: int
    adult: bool = False
    enrolls_on: date | None = None
    registered: bool = False


@dataclass(frozen=True)
class TutorShape:
    key: str
    archetype: Archetype
    windows: tuple[Window, ...]
    subjects: tuple[str, ...]
    """The approved qualifications (the matrix)."""
    daily_limit: int | None = None
    weekly_limit: int | None = None
    required_break: int | None = None
    preferred: tuple[str, ...] = ()
    """Preferred classroom keys (never the room deactivated on 30 Oct)."""
    joins_on: date | None = None
    """``None``: an account from :data:`SIM_SETUP`."""


@dataclass(frozen=True)
class QualificationEvent:
    """A qualification beyond the matrix: ``rejected`` (requested and
    decided in September), ``revoked`` (approved at setup, revoked on 20 Oct)
    or ``pending`` (requested on 18 Nov)."""

    tutor: str
    subject: str
    status: Literal["rejected", "revoked", "pending"]


@dataclass(frozen=True)
class ExceptionShape:
    tutor: str
    kind: Literal["unavailable", "available"]
    starts_on: date
    ends_on: date
    starts_at: str | None
    ends_at: str | None
    note: int
    """An index into the pool's ``exception_notes``."""
    created_on: date


@dataclass(frozen=True)
class ClassroomShape:
    key: str
    capacity: int
    windows: tuple[Window, ...] = ()
    deactivated_on: date | None = None


@dataclass(frozen=True)
class Profile:
    name: ProfileName
    families: tuple[FamilyShape, ...]
    tutors: tuple[TutorShape, ...]
    subjects: tuple[str, ...]
    classrooms: tuple[ClassroomShape, ...]
    qualification_events: tuple[QualificationEvent, ...]
    exceptions: tuple[ExceptionShape, ...]
    guardian_logins: int
    teen_logins: int


def _week(days: Iterable[int], *spans: tuple[str, str]) -> tuple[Window, ...]:
    return tuple((day, start, end) for day in days for start, end in spans)


MON_FRI: Final = (1, 2, 3, 4, 5)
SATURDAY: Final = 6

WEEKDAY_CLASSROOM_WINDOWS: Final = _week(MON_FRI, ("14:00", "20:00"))

FULL_TUTORS: Final[tuple[TutorShape, ...]] = (
    TutorShape(
        "t01",
        "mornings",
        _week(MON_FRI, ("09:00", "13:00")) + ((SATURDAY, "09:00", "13:00"),),
        ("math_e", "reading_e", "english_e", "math_m", "english_m"),
        daily_limit=240,
        required_break=10,
        preferred=("room1",),
    ),
    TutorShape(
        "t02",
        "mornings",
        _week((1, 3, 5), ("09:00", "12:00")) + ((SATURDAY, "09:00", "14:00"),),
        ("math_h", "physics_h"),
        preferred=("room1", "room5"),
    ),
    TutorShape(
        "t03",
        "mornings",
        _week((2, 4), ("09:00", "13:00")) + ((SATURDAY, "10:00", "14:00"),),
        ("calculus_u", "programming_u", "math_h"),
        weekly_limit=600,
    ),
    TutorShape(
        "t04",
        "afternoons",
        _week(MON_FRI, ("14:00", "20:00")),
        ("math_m", "science_m", "math_h", "physics_h", "chemistry_h"),
        daily_limit=300,
        weekly_limit=1200,
        required_break=15,
        preferred=("room2",),
    ),
    TutorShape(
        "t05",
        "afternoons",
        _week((1, 2, 3, 4), ("14:00", "20:00")),
        ("english_e", "english_m", "english_h"),
        daily_limit=240,
        required_break=10,
        preferred=("room2", "room4"),
    ),
    TutorShape(
        "t06",
        "afternoons",
        _week((1, 3, 5), ("14:00", "19:00")),
        ("math_e", "reading_e"),
        weekly_limit=480,
    ),
    TutorShape(
        "t07",
        "afternoons",
        _week((2, 3, 4, 5), ("14:00", "20:00")),
        ("calculus_u", "programming_u", "math_h"),
    ),
    TutorShape(
        "t08",
        "evenings",
        _week(MON_FRI, ("16:00", "17:30"), ("18:00", "20:00")),
        ("math_m", "english_m", "science_m"),
        daily_limit=180,
        required_break=15,
        preferred=("room4",),
    ),
    TutorShape(
        "t09",
        "evenings",
        _week((1, 2, 3, 4), ("16:00", "18:00"), ("18:30", "20:00")),
        ("physics_h", "chemistry_h", "math_h"),
        preferred=("room5",),
    ),
    TutorShape(
        "t10",
        "evenings",
        _week((2, 3, 4, 5), ("16:00", "20:00")),
        ("english_h", "english_m", "reading_e"),
        weekly_limit=720,
    ),
    TutorShape(
        "t11",
        "full_days",
        _week((1, 3, 5), ("09:00", "14:00"), ("15:00", "20:00")),
        ("math_e", "math_m", "science_m", "english_e"),
        daily_limit=360,
        weekly_limit=1500,
        required_break=15,
        preferred=("room1", "room4"),
    ),
    TutorShape(
        "t12",
        "full_days",
        _week((2, 3, 4), ("09:00", "20:00")),
        ("chemistry_h", "science_m", "programming_u"),
        daily_limit=300,
        joins_on=TUTOR_JOINS_ON,
    ),
)
"""The full profile's 12 tutors (#543 staff scope): archetypes 3/4/3/2."""

FULL: Final = Profile(
    name="full",
    families=(
        FamilyShape(3, 2),
        *(FamilyShape(2, 2) for _ in range(7)),
        FamilyShape(2, 2, enrolls_on=date(2026, 9, 14), registered=True),
        *(FamilyShape(1, 2) for _ in range(7)),
        *(FamilyShape(1, 1) for _ in range(8)),
        FamilyShape(1, 1, enrolls_on=date(2026, 10, 5)),
        FamilyShape(1, 1, enrolls_on=date(2026, 11, 9)),
        *(FamilyShape(1, 0, adult=True) for _ in range(4)),
    ),
    tutors=FULL_TUTORS,
    subjects=tuple(SUBJECT_LEVELS),
    classrooms=(
        ClassroomShape("room1", 4),
        ClassroomShape("room2", 6, WEEKDAY_CLASSROOM_WINDOWS),
        ClassroomShape("room3", 4, deactivated_on=ROOM_DEACTIVATED_ON),
        ClassroomShape("room4", 12),
        ClassroomShape("room5", 8),
    ),
    qualification_events=(
        QualificationEvent("t05", "programming_u", "rejected"),
        QualificationEvent("t06", "english_e", "revoked"),
        QualificationEvent("t09", "calculus_u", "pending"),
    ),
    exceptions=(
        ExceptionShape(
            "t01",
            "unavailable",
            date(2026, 9, 25),
            date(2026, 9, 25),
            None,
            None,
            0,
            date(2026, 9, 21),
        ),
        ExceptionShape(
            "t04",
            "unavailable",
            date(2026, 10, 7),
            date(2026, 10, 9),
            None,
            None,
            2,
            date(2026, 9, 30),
        ),
        ExceptionShape(
            "t05",
            "unavailable",
            date(2026, 10, 22),
            date(2026, 10, 22),
            "14:00",
            "17:00",
            1,
            date(2026, 10, 19),
        ),
        ExceptionShape(
            "t08",
            "unavailable",
            date(2026, 11, 6),
            date(2026, 11, 6),
            None,
            None,
            5,
            date(2026, 11, 2),
        ),
        ExceptionShape(
            "t06",
            "available",
            date(2026, 10, 15),
            date(2026, 10, 15),
            "15:00",
            "19:00",
            3,
            date(2026, 10, 12),
        ),
        ExceptionShape(
            "t07",
            "unavailable",
            date(2026, 11, 26),
            date(2026, 11, 27),
            None,
            None,
            4,
            date(2026, 11, 17),
        ),
        ExceptionShape(
            "t10",
            "available",
            date(2026, 11, 28),
            date(2026, 11, 28),
            "09:00",
            "13:00",
            6,
            date(2026, 11, 17),
        ),
    ),
    guardian_logins=12,
    teen_logins=2,
)
"""40 students (36 at setup; 2 on 14 Sep, 1 on 5 Oct, 1 on 9 Nov) in 30
families (21 with one student, 8 with two, 1 with three; 4 adults),
42 guardians (16 families with two, 10 with one; #543 D1) plus the 4
adults' paying parents (HF-01d), 30 guardian logins (every family's first
guardian; 12 use the portal), 12 tutors."""

SMALL: Final = Profile(
    name="small",
    families=(
        FamilyShape(2, 2),
        FamilyShape(2, 2),
        FamilyShape(1, 2),
        *(FamilyShape(1, 1) for _ in range(3)),
        FamilyShape(1, 1, enrolls_on=date(2026, 10, 5), registered=True),
        FamilyShape(1, 0, adult=True),
    ),
    tutors=(
        TutorShape(
            "t01",
            "mornings",
            _week(MON_FRI, ("09:00", "13:00")) + ((SATURDAY, "09:00", "13:00"),),
            ("math_e", "reading_e", "math_m", "calculus_u"),
            daily_limit=240,
            required_break=10,
            preferred=("room1",),
        ),
        TutorShape(
            "t02",
            "afternoons",
            _week(MON_FRI, ("14:00", "20:00")),
            ("math_e", "reading_e", "english_m", "physics_h", "calculus_u"),
        ),
        TutorShape(
            "t03",
            "evenings",
            _week(MON_FRI, ("16:00", "17:30"), ("18:00", "20:00")),
            ("math_m", "english_m", "physics_h"),
            daily_limit=180,
            weekly_limit=600,
            required_break=15,
            preferred=("room2",),
        ),
        TutorShape(
            "t04",
            "full_days",
            _week((2, 3, 4), ("09:00", "20:00")),
            ("physics_h", "math_m"),
            weekly_limit=720,
            joins_on=TUTOR_JOINS_ON,
        ),
    ),
    subjects=("math_e", "reading_e", "math_m", "english_m", "physics_h", "calculus_u"),
    classrooms=(
        ClassroomShape("room1", 4),
        ClassroomShape("room2", 6, WEEKDAY_CLASSROOM_WINDOWS),
        ClassroomShape("room3", 4, deactivated_on=ROOM_DEACTIVATED_ON),
    ),
    qualification_events=(
        QualificationEvent("t03", "calculus_u", "rejected"),
        QualificationEvent("t01", "english_m", "revoked"),
        QualificationEvent("t04", "english_m", "pending"),
    ),
    exceptions=(
        ExceptionShape(
            "t01",
            "unavailable",
            date(2026, 9, 25),
            date(2026, 9, 25),
            None,
            None,
            0,
            date(2026, 9, 21),
        ),
        ExceptionShape(
            "t03",
            "unavailable",
            date(2026, 10, 22),
            date(2026, 10, 22),
            "16:00",
            "17:30",
            1,
            date(2026, 10, 19),
        ),
        ExceptionShape(
            "t02",
            "available",
            date(2026, 10, 17),
            date(2026, 10, 17),
            "09:00",
            "13:00",
            6,
            date(2026, 10, 12),
        ),
        ExceptionShape(
            "t02",
            "unavailable",
            date(2026, 11, 26),
            date(2026, 11, 27),
            None,
            None,
            4,
            date(2026, 11, 17),
        ),
        ExceptionShape(
            "t03",
            "available",
            date(2026, 11, 28),
            date(2026, 11, 28),
            "09:00",
            "13:00",
            3,
            date(2026, 11, 17),
        ),
    ),
    guardian_logins=3,
    teen_logins=1,
)
"""10 students (9 at setup, 1 on 5 Oct) in 8 families, 10 guardians, 1
adult, 4 tutors (one per archetype; tutor 4 joins on 1 Oct)."""

PROFILES: Final[dict[str, Profile]] = {"full": FULL, "small": SMALL}

CLOSURES: Final[tuple[tuple[str, date], ...]] = (
    ("independence", date(2026, 9, 16)),
    ("day_of_the_dead", date(2026, 11, 2)),
    ("revolution", date(2026, 11, 16)),
)
"""The Mexican calendar closures inside the window (12 Dec is outside)."""

ACADEMIC_PERIOD_DATES: Final = (date(2026, 8, 24), date(2027, 7, 9))


# --- The plan -------------------------------------------------------------------


@dataclass(frozen=True)
class AccountPlan:
    """A login account: created (invited and redeemed) at ``created_at``."""

    username: str
    display_name: str
    role: str
    password: str
    created_at: datetime
    theme: str | None

    @property
    def privacy_accepted_at(self) -> datetime:
        """On or after version 1's publication (migration 0017)."""
        return max(PRIVACY_ACCEPTED_AT, self.created_at)


@dataclass(frozen=True)
class ContactPlan:
    name: str
    relationship: str
    phone: str


@dataclass(frozen=True)
class ConsentPlan:
    guardian: str
    method: Literal["in_app", "paper", "in_person"]
    at: datetime


@dataclass(frozen=True)
class StudentPlan:
    key: str
    given_names: str
    family_names: str
    birthdate: date
    level: str
    contacts: tuple[ContactPlan, ...]
    email: str | None
    account: AccountPlan | None
    consent: ConsentPlan | None
    """``student_login`` consent of the first guardian (every minor)."""

    @property
    def is_adult(self) -> bool:
        return age_on(self.birthdate, SIM_SETUP_DAY) >= 18


@dataclass(frozen=True)
class GuardianPlan:
    key: str
    given_names: str
    family_names: str
    relationship: str
    email: str
    account: AccountPlan | None
    registered: bool = False
    portal: bool = False
    """Uses the family portal from enrollment (HF-01's 12 [3] guardian
    logins): asks for changes and sessions and gives the minors' consent in
    the app. Since HF-01d every family's first guardian has a login (so
    receipts and reminders reach every family in the run's language), but
    the others only receive."""


@dataclass(frozen=True)
class FamilyPlan:
    key: str
    name: str
    enrolls_at: datetime
    guardians: tuple[GuardianPlan, ...]
    students: tuple[StudentPlan, ...]
    registered: bool


@dataclass(frozen=True)
class TutorPlan:
    key: str
    given_names: str
    family_names: str
    account: AccountPlan
    archetype: Archetype
    windows: tuple[Window, ...]
    subjects: tuple[str, ...]
    daily_limit: int | None
    weekly_limit: int | None
    required_break: int | None
    preferred: tuple[str, ...]
    experience: str
    joins_on: date

    @property
    def joins_at(self) -> datetime:
        return self.account.created_at


@dataclass(frozen=True)
class QualificationPlan:
    tutor: str
    subject: str
    status: Literal["rejected", "revoked", "pending"]
    requested_at: datetime
    decided_at: datetime | None
    """Rejected or revoked; ``None`` while pending."""
    reason: str | None


@dataclass(frozen=True)
class ExceptionPlan:
    tutor: str
    kind: Literal["unavailable", "available"]
    starts_on: date
    ends_on: date
    starts_at: str | None
    ends_at: str | None
    note: str
    created_at: datetime


@dataclass(frozen=True)
class SubjectPlan:
    key: str
    level: str
    name: str


@dataclass(frozen=True)
class ClassroomPlan:
    key: str
    name: str
    capacity: int
    windows: tuple[Window, ...]
    deactivated_at: datetime | None


@dataclass(frozen=True)
class ClosurePlan:
    key: str
    day: date
    name: str


@dataclass(frozen=True)
class DelegationPlan:
    permission: str
    granted_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class RegistrationPlan:
    """The pending self-registration (18 Nov): a user who isn't a guardian
    profile yet."""

    username: str
    display_name: str
    at: datetime


@dataclass(frozen=True)
class SimulationPlan:
    language: Language
    profile: ProfileName
    seed: int
    staff: tuple[AccountPlan, ...]
    """Owner, Administrator, Academic coordinator, Finance staff."""
    tutors: tuple[TutorPlan, ...]
    families: tuple[FamilyPlan, ...]
    subjects: tuple[SubjectPlan, ...]
    classrooms: tuple[ClassroomPlan, ...]
    closures: tuple[ClosurePlan, ...]
    academic_period: tuple[str, date, date]
    qualifications: tuple[QualificationPlan, ...]
    exceptions: tuple[ExceptionPlan, ...]
    delegations: tuple[DelegationPlan, ...]
    pending_registration: RegistrationPlan
    pending_tutor: RegistrationPlan
    """The tutor invited at :data:`SIM_NOW` ("joining in December")."""
    center_name: str
    address: str
    contact_email: str
    schedule: Timetable | None = None
    """HF-01b's timetable and outcomes (:mod:`.timetable`); always set by
    :func:`plan_simulation` (read it through :attr:`timetable`)."""

    coursework: Coursework | None = None
    """HF-01c's academic record (:mod:`.coursework`); always set by
    :func:`plan_simulation` (read it through :attr:`academics`)."""

    finances: Billing | None = None
    """HF-01d's billing plan (:mod:`.billing_plan`); always set by
    :func:`plan_simulation` (read it through :attr:`billing`)."""

    wages: Payroll | None = None
    """HF-01e's payroll plan (:mod:`.payroll_plan`); always set by
    :func:`plan_simulation` (read it through :attr:`payroll`)."""

    outgoings: Expenses | None = None
    """HF-01e's operating expenses (:mod:`.expenses_plan`); always set by
    :func:`plan_simulation` (read it through :attr:`expenses`)."""

    @property
    def payroll(self) -> Payroll:
        if self.wages is None:  # pragma: no cover - plan_simulation sets it
            raise RuntimeError("the plan has no payroll plan")
        return self.wages

    @property
    def expenses(self) -> Expenses:
        if self.outgoings is None:  # pragma: no cover - plan_simulation sets it
            raise RuntimeError("the plan has no expenses plan")
        return self.outgoings

    @property
    def billing(self) -> Billing:
        if self.finances is None:  # pragma: no cover - plan_simulation sets it
            raise RuntimeError("the plan has no billing plan")
        return self.finances

    @property
    def timetable(self) -> Timetable:
        if self.schedule is None:  # pragma: no cover - plan_simulation sets it
            raise RuntimeError("the plan has no timetable")
        return self.schedule

    @property
    def academics(self) -> Coursework:
        if self.coursework is None:  # pragma: no cover - plan_simulation sets it
            raise RuntimeError("the plan has no academic record")
        return self.coursework

    @property
    def owner(self) -> AccountPlan:
        return self.staff[0]

    @property
    def administrator(self) -> AccountPlan:
        return self.staff[1]

    @property
    def coordinator(self) -> AccountPlan:
        return self.staff[2]

    @property
    def finance(self) -> AccountPlan:
        return self.staff[3]

    @property
    def students(self) -> tuple[StudentPlan, ...]:
        return tuple(s for family in self.families for s in family.students)

    @property
    def guardians(self) -> tuple[GuardianPlan, ...]:
        return tuple(g for family in self.families for g in family.guardians)

    @property
    def accounts(self) -> tuple[AccountPlan, ...]:
        """Every login account (staff, tutors, guardians, students)."""
        return (
            *self.staff,
            *(t.account for t in self.tutors),
            *(g.account for g in self.guardians if g.account is not None),
            *(s.account for s in self.students if s.account is not None),
        )

    @property
    def registered_family(self) -> FamilyPlan:
        [family] = [f for f in self.families if f.registered]
        return family

    def tutor(self, key: str) -> TutorPlan:
        return next(t for t in self.tutors if t.key == key)


def age_on(birthdate: date, day: date) -> int:
    years = day.year - birthdate.year
    return years - ((day.month, day.day) < (birthdate.month, birthdate.day))


def make_faker(language: Language, seed: int) -> faker.Faker:
    """A Faker instance in the language's locale, seeded with ``seed``."""
    instance = faker.Faker(FAKER_LOCALES[language])
    instance.seed_instance(seed)
    return instance


_EN_NAME = re.compile(r"^[A-Za-z' -]+$")


def slug(text: str) -> str:
    """Lower-case ASCII letters and digits only (accents stripped)."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


class _Names:
    """Unique person names and usernames from one seeded Faker instance."""

    def __init__(self, language: Language, fake: faker.Faker) -> None:
        self.language = language
        self.fake = fake
        self.full_names: set[str] = set()
        self.usernames: set[str] = set(STAFF_USERNAMES[language].values())

    def _clean(self, value: str) -> bool:
        return self.language == "es" or bool(_EN_NAME.match(value))

    def given(self, female: bool) -> str:
        while True:
            value = (
                self.fake.first_name_female() if female else self.fake.first_name_male()
            )
            if self._clean(value):
                return value

    def surname(self) -> str:
        while True:
            value = self.fake.last_name()
            if self._clean(value):
                return value

    def person(self, female: bool, family_names: str) -> tuple[str, str]:
        """A given name not yet used with ``family_names``."""
        for _ in range(100):
            given = self.given(female)
            if f"{given} {family_names}" not in self.full_names:
                self.full_names.add(f"{given} {family_names}")
                return given, family_names
        raise RuntimeError("no unique name")  # pragma: no cover

    def username(self, given: str, family_names: str) -> str:
        base = f"{slug(given.split()[0])}.{slug(family_names.split()[0])}"[:60]
        candidate, n = base, 1
        while candidate in self.usernames:
            n += 1
            candidate = f"{base}{n}"
        self.usernames.add(candidate)
        return candidate


def plan_simulation(
    language: Language, profile: ProfileName | Profile = "full", seed: int = 1
) -> SimulationPlan:
    """The whole plan; pure (no clock, no database)."""
    shape = PROFILES[profile] if isinstance(profile, str) else profile
    # Deterministic synthetic data, not security: a seeded generator.
    rng = random.Random(seed)  # noqa: S311
    fake = make_faker(language, seed)
    texts = TextPool(language)
    names = _Names(language, fake)
    themes = _Cycle(THEMES)

    staff = tuple(
        _staff_account(language, role, names, themes)
        for role in ("owner", "administrator", "academic_coordinator", "finance_staff")
    )
    tutors = tuple(
        _tutor(shape_, names, themes, texts, index)
        for index, shape_ in enumerate(shape.tutors)
    )
    families = _families(language, shape, rng, names, themes, texts)
    families = _adult_payers(language, families, seed, names)
    subjects = tuple(
        SubjectPlan(key, SUBJECT_LEVELS[key], texts.mapping("subjects")[key])
        for key in shape.subjects
    )
    classrooms = tuple(
        ClassroomPlan(
            room.key,
            texts.mapping("classrooms")[room.key],
            room.capacity,
            room.windows,
            None
            if room.deactivated_on is None
            else local_instant(room.deactivated_on, "20:30"),
        )
        for room in shape.classrooms
    )
    closures = tuple(
        ClosurePlan(key, day, texts.mapping("closures")[key]) for key, day in CLOSURES
    )
    pending_given, pending_family = names.person(True, _surnames(language, names))
    tutor_given, tutor_family = names.person(False, _surnames(language, names))
    base = SimulationPlan(
        language=language,
        profile=shape.name,
        seed=seed,
        staff=staff,
        tutors=tutors,
        families=families,
        subjects=subjects,
        classrooms=classrooms,
        closures=closures,
        academic_period=(texts.text("academic_period"), *ACADEMIC_PERIOD_DATES),
        qualifications=tuple(
            _qualification(event, texts) for event in shape.qualification_events
        ),
        exceptions=tuple(
            ExceptionPlan(
                e.tutor,
                e.kind,
                e.starts_on,
                e.ends_on,
                e.starts_at,
                e.ends_at,
                texts.choices("exception_notes")[e.note],
                local_instant(e.created_on, "18:00"),
            )
            for e in shape.exceptions
        ),
        delegations=(
            DelegationPlan(
                PAYROLL_FINALIZE,
                local_instant(date(2026, 10, 12), "09:00"),
                local_instant(date(2026, 10, 16), "18:00"),
            ),
            DelegationPlan(
                FINANCE_REFUND_APPROVE,
                local_instant(date(2026, 11, 18), "18:00"),
                None,
            ),
        ),
        pending_registration=RegistrationPlan(
            names.username(pending_given, pending_family),
            f"{pending_given} {pending_family}",
            PENDING_REGISTRATION_AT,
        ),
        pending_tutor=RegistrationPlan(
            names.username(tutor_given, tutor_family),
            f"{tutor_given} {tutor_family}",
            SIM_NOW,
        ),
        center_name=texts.center_name,
        address=texts.text("address"),
        contact_email=texts.text("contact_email"),
    )
    from tests.factories.simulation.billing_plan import build_billing
    from tests.factories.simulation.coursework import build_coursework
    from tests.factories.simulation.expenses_plan import build_expenses
    from tests.factories.simulation.payroll_plan import build_payroll
    from tests.factories.simulation.timetable import build_timetable

    scheduled = replace(base, schedule=build_timetable(base, seed))
    taught = replace(scheduled, coursework=build_coursework(scheduled, seed))
    billed = replace(taught, finances=build_billing(taught, seed))
    paid = replace(billed, wages=build_payroll(billed))
    return replace(paid, outgoings=build_expenses(paid))


class _Cycle:
    def __init__(self, values: tuple[str | None, ...]) -> None:
        self.values = values
        self.n = 0

    def next(self) -> str | None:
        value = self.values[self.n % len(self.values)]
        self.n += 1
        return value


def _surnames(language: Language, names: _Names) -> str:
    if language == "es":
        return f"{names.surname()} {names.surname()}"
    return names.surname()


def _staff_account(
    language: Language, role: str, names: _Names, themes: _Cycle
) -> AccountPlan:
    given, family = names.person(names.fake.boolean(), _surnames(language, names))
    return AccountPlan(
        STAFF_USERNAMES[language][role],
        f"{given} {family}",
        role,
        STAFF_PASSWORDS[role],
        SIM_SETUP,
        themes.next(),
    )


def _tutor(
    shape: TutorShape, names: _Names, themes: _Cycle, texts: TextPool, index: int
) -> TutorPlan:
    given, family = names.person(names.fake.boolean(), _surnames(texts.language, names))
    joins_on = shape.joins_on or SIM_SETUP_DAY
    created_at = (
        SIM_SETUP if shape.joins_on is None else local_instant(joins_on, "09:00")
    )
    return TutorPlan(
        key=shape.key,
        given_names=given,
        family_names=family,
        account=AccountPlan(
            names.username(given, family),
            f"{given} {family}",
            "tutor",
            TUTOR_PASSWORD,
            created_at,
            themes.next(),
        ),
        archetype=shape.archetype,
        windows=shape.windows,
        subjects=shape.subjects,
        daily_limit=shape.daily_limit,
        weekly_limit=shape.weekly_limit,
        required_break=shape.required_break,
        preferred=shape.preferred,
        experience=texts.choices("professional_experience")[index],
        joins_on=joins_on,
    )


def _qualification(event: QualificationEvent, texts: TextPool) -> QualificationPlan:
    if event.status == "rejected":
        return QualificationPlan(
            event.tutor,
            event.subject,
            "rejected",
            local_instant(QUALIFICATION_REJECT_REQUESTED, "17:00"),
            local_instant(QUALIFICATION_REJECTED, "11:00"),
            texts.choices("rejection_reasons")[0],
        )
    if event.status == "revoked":
        return QualificationPlan(
            event.tutor,
            event.subject,
            "revoked",
            SIM_SETUP,
            local_instant(QUALIFICATION_REVOKED, "10:00"),
            texts.choices("revocation_reasons")[0],
        )
    return QualificationPlan(
        event.tutor,
        event.subject,
        "pending",
        local_instant(QUALIFICATION_PENDING_REQUESTED, "16:00"),
        None,
        None,
    )


def _families(
    language: Language,
    shape: Profile,
    rng: random.Random,
    names: _Names,
    themes: _Cycle,
    texts: TextPool,
) -> tuple[FamilyPlan, ...]:
    minors = sum(f.students for f in shape.families if not f.adult)
    per_level = minors // 3
    levels = [ELEMENTARY] * per_level + [MIDDLE_SCHOOL] * per_level
    levels += [HIGH_SCHOOL] * (minors - 2 * per_level)
    rng.shuffle(levels)
    phones = iter(range(100))
    relationships = texts.choices("contact_relationships")
    guardian_logins = shape.guardian_logins
    teen_logins = shape.teen_logins
    in_person = False
    families: list[FamilyPlan] = []
    # The registered family's guardian always has a login.
    guardian_logins -= 1
    for index, family_shape in enumerate(shape.families, start=1):
        enrolls_at = (
            SIM_SETUP
            if family_shape.enrolls_on is None
            else local_instant(family_shape.enrolls_on, "10:00")
        )
        if language == "es":
            paternal, maternal = names.surname(), names.surname()
            student_surnames = f"{paternal} {maternal}"
            mother_surnames = f"{maternal} {names.surname()}"
            father_surnames = f"{paternal} {names.surname()}"
            family_name = texts.text("family_name").format(surnames=student_surnames)
        else:
            surname = names.surname()
            student_surnames = mother_surnames = father_surnames = surname
            family_name = texts.text("family_name").format(surnames=surname)
        key = f"f{index:02d}"

        guardians: list[GuardianPlan] = []
        for g in range(family_shape.guardians):
            female = g == 0 if family_shape.guardians == 2 else rng.random() < 0.6
            relationship = "mother" if female else "father"
            if family_shape.guardians == 1 and rng.random() < 0.15:
                relationship = "grandparent"
            given, family = names.person(
                female, mother_surnames if female else father_surnames
            )
            registered = family_shape.registered and g == 0
            account = None
            portal = registered or (g == 0 and guardian_logins > 0)
            if portal:
                if not registered:
                    guardian_logins -= 1
                account = AccountPlan(
                    names.username(given, family),
                    f"{given} {family}",
                    "guardian",
                    GUARDIAN_PASSWORD,
                    REGISTRATION_APPROVED if registered else enrolls_at,
                    themes.next(),
                )
            elif g == 0:
                # HF-01d: every first guardian has a login (theme unset).
                account = AccountPlan(
                    names.username(given, family),
                    f"{given} {family}",
                    "guardian",
                    GUARDIAN_PASSWORD,
                    enrolls_at,
                    None,
                )
            guardians.append(
                GuardianPlan(
                    key=f"{key}g{g + 1}",
                    given_names=given,
                    family_names=family,
                    relationship=relationship,
                    email=_email(given, family, len(names.full_names)),
                    account=account,
                    registered=registered,
                    portal=portal,
                )
            )

        students: list[StudentPlan] = []
        for s in range(family_shape.students):
            level = UNIVERSITY if family_shape.adult else levels.pop()
            given, family = names.person(rng.random() < 0.5, student_surnames)
            birthdate = _birthdate(rng, level)
            contacts = tuple(
                ContactPlan(
                    _contact_name(language, names),
                    rng.choice(relationships),
                    f"555-01{next(phones):02d}",
                )
                for _ in range(1 if rng.random() < 0.7 else 2)
            )
            account = None
            if family_shape.adult or (
                level == HIGH_SCHOOL
                and teen_logins > 0
                and family_shape.enrolls_on is None
            ):
                if not family_shape.adult:
                    teen_logins -= 1
                account = AccountPlan(
                    names.username(given, family),
                    f"{given} {family}",
                    "student",
                    STUDENT_PASSWORD,
                    enrolls_at if family_shape.adult else TEEN_LOGIN_AT,
                    themes.next(),
                )
            consent = None
            if not family_shape.adult:
                first = guardians[0]
                if first.portal:
                    method: Literal["in_app", "paper", "in_person"] = "in_app"
                else:
                    method = "in_person" if in_person else "paper"
                    in_person = not in_person
                consent = ConsentPlan(
                    first.key, method, max(CONSENT_AT, enrolls_at + timedelta(hours=1))
                )
            students.append(
                StudentPlan(
                    key=f"{key}s{s + 1}",
                    given_names=given,
                    family_names=family,
                    birthdate=birthdate,
                    level=level,
                    contacts=contacts,
                    email=(
                        _email(given, family, len(names.full_names))
                        if family_shape.adult
                        else None
                    ),
                    account=account,
                    consent=consent,
                )
            )
        families.append(
            FamilyPlan(
                key=key,
                name=family_name,
                enrolls_at=enrolls_at,
                guardians=tuple(guardians),
                students=tuple(students),
                registered=family_shape.registered,
            )
        )
    return tuple(families)


ADULT_PAYER_STREAM: Final = 552_100
"""HF-01d: the adult students' paying parents come from their own Faker and
RNG streams, so every earlier name, username and date is unchanged."""


def _adult_payers(
    language: Language,
    families: tuple[FamilyPlan, ...],
    seed: int,
    names: _Names,
) -> tuple[FamilyPlan, ...]:
    """HF-01d: each adult student's family gets the parent who pays (a
    guardian with a login and no portal use), so receipts and reminders
    reach every family in the run's language. Their own names stream; the
    shared sets keep names and usernames unique."""
    # Deterministic synthetic data, not security: a seeded generator.
    rng = random.Random(seed + ADULT_PAYER_STREAM)  # noqa: S311
    payers = _Names(language, make_faker(language, seed + ADULT_PAYER_STREAM))
    payers.full_names = names.full_names
    payers.usernames = names.usernames
    result: list[FamilyPlan] = []
    for family in families:
        if family.guardians or not family.students:
            result.append(family)
            continue
        student = family.students[0]
        female = rng.random() < 0.5
        given, surnames = payers.person(female, student.family_names)
        payer = GuardianPlan(
            key=f"{family.key}g1",
            given_names=given,
            family_names=surnames,
            relationship="mother" if female else "father",
            email=_email(given, surnames, len(payers.full_names)),
            account=AccountPlan(
                payers.username(given, surnames),
                f"{given} {surnames}",
                "guardian",
                GUARDIAN_PASSWORD,
                family.enrolls_at,
                None,
            ),
        )
        result.append(replace(family, guardians=(payer,)))
    return tuple(result)


def _contact_name(language: Language, names: _Names) -> str:
    given = names.given(names.fake.boolean())
    return f"{given} {_surnames(language, names)}"


def _email(given: str, family: str, n: int) -> str:
    domain = RESERVED_EMAIL_DOMAINS[n % len(RESERVED_EMAIL_DOMAINS)]
    return f"{slug(given.split()[0])}.{slug(family.split()[0])}{n}@{domain}"


def _birthdate(rng: random.Random, level: str) -> date:
    """A birthdate whose age on :data:`SIM_SETUP_DAY` is within the level's
    range."""
    low, high = AGES[level]
    age = rng.randint(low, high)
    latest = SIM_SETUP_DAY.replace(year=SIM_SETUP_DAY.year - age)
    earliest = latest.replace(year=latest.year - 1) + timedelta(days=1)
    return earliest + timedelta(days=rng.randint(0, (latest - earliest).days))


# --- Plan helpers (D7b) -----------------------------------------------------------


def _minutes(hhmm: str) -> int:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return hours * 60 + minutes


def qualified_tutors(plan: SimulationPlan, subject: str, day: date) -> list[str]:
    """The tutors approved for ``subject`` on ``day``: the matrix, once a
    tutor has joined, plus a revoked qualification before its revocation."""
    keys = [t.key for t in plan.tutors if subject in t.subjects and t.joins_on <= day]
    for q in plan.qualifications:
        if (
            q.status == "revoked"
            and q.subject == subject
            and q.decided_at is not None
            and day < q.decided_at.date()
        ):
            keys.append(q.tutor)
    return sorted(set(keys))


def is_available(
    plan: SimulationPlan, tutor: str, day: date, start: str, end: str
) -> bool:
    """Whether ``tutor``'s weekly windows and exceptions cover ``start``-``end``
    (local) on ``day``."""
    lo, hi = _minutes(start), _minutes(end)
    exceptions = [
        e
        for e in plan.exceptions
        if e.tutor == tutor and e.starts_on <= day <= e.ends_on
    ]
    for e in exceptions:
        if e.kind == "unavailable":
            if e.starts_at is None or e.ends_at is None:
                return False
            if _minutes(e.starts_at) < hi and lo < _minutes(e.ends_at):
                return False
    for e in exceptions:
        if (
            e.kind == "available"
            and e.starts_at is not None
            and e.ends_at is not None
            and _minutes(e.starts_at) <= lo
            and hi <= _minutes(e.ends_at)
        ):
            return True
    return any(
        weekday == day.isoweekday() and _minutes(ws) <= lo and hi <= _minutes(we)
        for weekday, ws, we in plan.tutor(tutor).windows
    )


def available_tutors(
    plan: SimulationPlan, subject: str, day: date, start: str, end: str
) -> list[str]:
    """The distinct qualified tutors available for ``subject`` in the slot."""
    return [
        key
        for key in qualified_tutors(plan, subject, day)
        if is_available(plan, key, day, start, end)
    ]
