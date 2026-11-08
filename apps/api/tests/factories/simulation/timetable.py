"""The simulation's timetable and its outcomes (#550 HF-01b): pure, built by
:func:`build_timetable` from the people and staff plan, so the same
arguments give the same timetable on any day (no clock, no database).

**Timetable.** Every student has 1-3 weekly individual slots (``small``:
one), each a weekly series with a subject at the student's level, a tutor
qualified for it, a classroom or none and 60 or 90 minutes. Three [one]
class groups meet twice a week, and two course templates have one cohort
each: one that ran 8 Sep-22 Oct (Tue/Thu) and one from 28 Oct to 4 Dec
(Wed/Fri) that spans the anchor. Series are created at
:data:`~.anchor.SIM_SETUP`, at a later student's enrollment or at tutor 12's
join (1 Oct), end by :data:`~.anchor.SIM_HORIZON` (E9) and are materialized
through it at creation.

**Consistency (owner update 2026-11-07).** Every placement goes through
:class:`Calendar`: the tutor is approved for the subject on that date (the
matrix, joined, never the revoked or the pending one), inside their weekly
windows and exceptions (HF-01's and this issue's time off), within their
daily and weekly limits and required break, without a tutor, room or
student overlap, the room active, large enough and inside its windows, the
center open (operating hours, no closure), no student before their
enrollment. :func:`consistency_errors` re-checks the final timetable from
scratch (the A2 unit test).

**Outcomes.** Each class before :data:`~.anchor.SIM_ANCHOR` ends in a final
state drawn from the plan's own RNG (a separate stream, so HF-01's people
are unchanged): held and closed out, cancelled through a change request
(``on_time``, ``late`` or, from a tutor or as an exception, ``excused``) or
by the center, moved by an approved reschedule, held by a substitute after
the tutor's approved time off, or skipped for a closure (done by the
services). Make-ups, session requests, offers that are declined or expire,
rejected requests, corrections and closeouts sent back for changes are
dated events. On 17-18 Nov the pending items are created (requests, offers,
an availability change, a time off), so at :data:`~.anchor.SIM_NOW` every
later class is only ``confirmed``, ``offered`` or ``unassigned``.

**Payroll timing (HF-01e).** Two group classes are reviewed on the
Wednesday after their payroll fortnight is finalized (late approvals), and
a closeout correction of a class in a fortnight that ends before the anchor
is made on the Thursday after it (an hours correction), so payroll has work
after lock. The classes are picked without the RNG.

The result is a list of dated :data:`Event` values the ``schedule`` and
``courses`` modules execute in order.
"""

import random
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import cached_property
from typing import Final, Literal

from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_SETUP,
    SIM_START,
    ZONE,
    local_instant,
)
from tests.factories.simulation.plan import SimulationPlan, is_available
from tests.factories.simulation.texts import TextPool

type ClassKind = Literal["individual", "group", "course"]
type Status = Literal[
    "completed", "cancelled", "skipped_closure", "confirmed", "offered", "unassigned"
]
type Mark = Literal["present", "late", "absent"]

OPERATING_HOURS: Final[dict[int, tuple[int, int]]] = {
    **{day: (9 * 60, 20 * 60) for day in (1, 2, 3, 4, 5)},
    6: (9 * 60, 14 * 60),
}
"""Monday to Friday 09:00-20:00, Saturday 09:00-14:00 (HF-01 settings)."""
GRID: Final = 30
MINOR_EARLIEST: Final = 14 * 60
"""Minors are at school until 14:00 on weekdays."""
CLOSEOUT_AFTER: Final = timedelta(minutes=15)
APPROVE_AFTER: Final = timedelta(hours=2)
CHANGE_NOTICE: Final = timedelta(hours=24)
"""The policy's ``change_notice_hours`` (the merged default)."""
RESPONSE_HOURS: Final = timedelta(hours=48)
MAKEUP_WINDOW: Final = timedelta(days=30)
LAST_DECIDED_DAY: Final = SIM_ANCHOR - timedelta(days=1)
LATE_WINDOW: Final = (date(2026, 10, 27), date(2026, 11, 8))
"""Cancellations whose make-up window reaches past the anchor."""
LATE_WINDOW_CANCELLATIONS: Final = 3
RESIZE_AT: Final = local_instant(date(2026, 11, 12), "10:00")
"""When the Administrator shortens one class after the anchor (HF-01d)."""
RESIZE_FROM: Final = 90
RESIZE_TO: Final = 60
PENDING_DAYS: Final = (date(2026, 11, 17), date(2026, 11, 18))
PAY_FORTNIGHTS_FROM: Final = date(2026, 8, 31)
"""HF-01e: the Monday the payroll fortnights count from (the payroll start
and the biweekly anchor)."""
LATE_REVIEW_FORTNIGHTS: Final = (date(2026, 9, 14), date(2026, 10, 12))
"""HF-01e: one group class closed out in each of these fortnights is
reviewed only after the fortnight's payroll is finalized (a
``late_approval``)."""

RNG_OFFSET: Final = 550_000
"""The timetable's RNG stream: ``Random(seed + RNG_OFFSET)``."""


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def mins(value: str) -> int:
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes


def monday(day: date) -> date:
    return day - timedelta(days=day.isoweekday() - 1)


def fortnight_start(day: date) -> date:
    """The first day (a Monday) of the payroll fortnight holding ``day``
    (HF-01e: 14-day blocks from :data:`PAY_FORTNIGHTS_FROM`)."""
    return day - timedelta(days=(day - PAY_FORTNIGHTS_FROM).days % 14)


def fortnight_end(day: date) -> date:
    return fortnight_start(day) + timedelta(days=13)


def after_payroll(day: date, days: int, hhmm: str) -> datetime:
    """HF-01e: ``days`` days after the end of ``day``'s fortnight, at
    ``hhmm`` (3: the Wednesday after the Tuesday finalize, 4: the
    Thursday)."""
    return local_instant(fortnight_end(day) + timedelta(days=days), hhmm)


# --- Profile shapes ------------------------------------------------------------------


@dataclass(frozen=True)
class GroupShape:
    key: str
    subject: str
    size: int
    weekdays: tuple[int, int]


@dataclass(frozen=True)
class CourseShape:
    key: str
    subject: str
    session_count: int
    minutes: int
    modules: tuple[int, ...]
    """Planned sessions per module (they add up to ``session_count``)."""
    weekdays: tuple[int, ...]
    starts_on: date
    created: tuple[date, str] | None
    """``None``: created at :data:`SIM_SETUP`."""
    size: int


@dataclass(frozen=True)
class Volumes:
    """Per-profile targets and minimums."""

    slots: tuple[int, ...]
    """How many weekly individual slots each student gets, in turn."""
    joiner_slots: int
    groups: tuple[GroupShape, ...]
    courses: tuple[CourseShape, ...]
    cancel_rate: float
    reschedule_rate: float
    substitutions: int
    makeups: int
    approved_requests: int
    rejected_requests: int
    rejected_changes: int
    declined_offers: int
    expired_offers: int
    attendance_corrections: int
    closeout_corrections: int
    changes_requested_rate: float
    cancel_mix: tuple[tuple[str, int], ...]
    """Minimum cancellations per way (``on_time``, ``late``, ``tutor``,
    ``exception``, ``center``); the rest go to ``on_time``."""


FINISHED_COURSE: Final = "exam"
RUNNING_COURSE: Final = "physics"

FULL_VOLUMES: Final = Volumes(
    slots=(1,) * 14 + (2,) * 20 + (3,) * 6,
    joiner_slots=3,
    groups=(
        GroupShape("g1", "math_m", 5, (2, 4)),
        GroupShape("g2", "english_h", 6, (1, 3)),
        GroupShape("g3", "reading_e", 4, (2, 5)),
    ),
    courses=(
        CourseShape(
            FINISHED_COURSE,
            "math_m",
            14,
            90,
            (4, 4, 4, 2),
            (2, 4),
            date(2026, 9, 7),
            None,
            5,
        ),
        CourseShape(
            RUNNING_COURSE,
            "physics_h",
            12,
            60,
            (4, 4, 4),
            (3, 5),
            date(2026, 10, 26),
            (date(2026, 10, 12), "10:00"),
            6,
        ),
    ),
    cancel_rate=0.05,
    reschedule_rate=0.03,
    substitutions=8,
    makeups=6,
    approved_requests=6,
    rejected_requests=2,
    rejected_changes=2,
    declined_offers=3,
    expired_offers=2,
    attendance_corrections=3,
    closeout_corrections=2,
    changes_requested_rate=0.05,
    cancel_mix=(
        ("on_time", 2),
        ("late", 2),
        ("tutor", 2),
        ("exception", 2),
        ("center", 2),
    ),
)
SMALL_VOLUMES: Final = Volumes(
    slots=(1,) * 10,
    joiner_slots=1,
    groups=(GroupShape("g1", "math_m", 3, (2, 4)),),
    courses=(
        CourseShape(
            FINISHED_COURSE,
            "math_m",
            14,
            60,
            (4, 4, 4, 2),
            (1, 3),
            date(2026, 9, 7),
            None,
            3,
        ),
        CourseShape(
            RUNNING_COURSE,
            "physics_h",
            12,
            60,
            (4, 4, 4),
            (3, 5),
            date(2026, 10, 26),
            (date(2026, 10, 12), "10:00"),
            2,
        ),
    ),
    cancel_rate=0.05,
    reschedule_rate=0.03,
    substitutions=2,
    makeups=2,
    approved_requests=6,
    rejected_requests=2,
    rejected_changes=2,
    declined_offers=3,
    expired_offers=2,
    attendance_corrections=3,
    closeout_corrections=2,
    changes_requested_rate=0.05,
    cancel_mix=(
        ("on_time", 2),
        ("late", 1),
        ("tutor", 1),
        ("exception", 1),
        ("center", 1),
    ),
)
VOLUMES: Final[dict[str, Volumes]] = {"full": FULL_VOLUMES, "small": SMALL_VOLUMES}


# --- The plan's values ----------------------------------------------------------------


@dataclass(frozen=True)
class Placement:
    """Where and when a class is, and with whom (local date and times)."""

    day: date
    start: str
    minutes: int
    tutor: str | None
    room: str | None

    @property
    def lo(self) -> int:
        return mins(self.start)

    @property
    def hi(self) -> int:
        return self.lo + self.minutes

    @property
    def end(self) -> str:
        return hhmm(self.hi)

    @property
    def starts_at(self) -> datetime:
        return local_instant(self.day, self.start)

    @property
    def ends_at(self) -> datetime:
        return local_instant(self.day, self.end)


@dataclass(frozen=True)
class SeriesPlan:
    """A weekly series: an individual slot, a group's or a cohort's."""

    key: str
    kind: ClassKind
    subject: str
    tutor: str
    room: str | None
    weekdays: tuple[int, ...]
    start: str
    minutes: int
    starts_on: date
    until: date
    students: tuple[str, ...]
    created_at: datetime

    def dates(self) -> list[date]:
        """Every local date of the rule, closures included."""
        found: list[date] = []
        for weekday in self.weekdays:
            day = self.starts_on + timedelta(
                days=(weekday - self.starts_on.isoweekday()) % 7
            )
            while day <= self.until:
                found.append(day)
                day += timedelta(days=7)
        return sorted(found)


@dataclass(frozen=True)
class GroupPlan:
    key: str
    name: str
    max_students: int
    series: SeriesPlan


@dataclass(frozen=True)
class CoursePlan:
    key: str
    name: str
    description: str
    subject: str
    session_count: int
    minutes: int
    min_attendance_percent: int
    modules: tuple[tuple[str, int, str], ...]
    """Title, planned sessions, materials."""
    assessments: tuple[tuple[str, bool, int | None], ...]
    """Title, required, minimum score."""
    created_at: datetime


@dataclass(frozen=True)
class CohortPlan:
    key: str
    name: str
    course: str
    max_students: int
    series: SeriesPlan
    enrollments: tuple[tuple[str, datetime], ...]
    """Student and enrollment instant."""


@dataclass(frozen=True)
class ClassPlan:
    """One class (a series occurrence or a one-off session)."""

    key: str
    series: str | None
    kind: ClassKind
    subject: str
    students: tuple[str, ...]
    placed: Placement
    """As created (the series occurrence or the one-off session)."""
    status: Status
    final: Placement
    """Where and with whom it is held (moved or substituted), or ``placed``."""
    makeup_of: str | None = None
    rescheduled: bool = False
    substituted: bool = False
    attendance: tuple[tuple[str, Mark], ...] = ()
    created_at: datetime | None = None
    """One-off sessions only."""

    @property
    def occupies(self) -> bool:
        return self.status not in ("cancelled", "skipped_closure")


@dataclass(frozen=True)
class TimeOffPlan:
    """A tutor's ``unavailable`` exception over one class (``partial`` from
    its start to its end): approved, rejected or pending."""

    key: str
    cls: str
    tutor: str
    day: date
    start: str
    end: str
    note: str
    decision: Literal["approved", "rejected", "pending"]


@dataclass(frozen=True)
class ChangeRequestPlan:
    key: str
    cls: str
    requester: str
    """A username."""
    by: Literal["family", "tutor"]
    kind: Literal["cancellation", "reschedule"]
    created_at: datetime
    proposed: Placement | None
    decision: Literal["approved", "rejected", "pending"]
    exception: bool
    classification: Literal["on_time", "late", "excused"]


@dataclass(frozen=True)
class SessionRequestPlan:
    key: str
    requester: str
    student: str
    subject: str
    placement: Placement
    """The requested slot, without a tutor or room."""
    created_at: datetime
    decision: Literal["confirmed", "rejected", "pending"]


# --- Events ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateSeries:
    at: datetime
    series: SeriesPlan


@dataclass(frozen=True)
class CreateGroup:
    at: datetime
    group: GroupPlan


@dataclass(frozen=True)
class AcceptSeries:
    """The tutor accepts a series offer (an individual series, a group's or
    a cohort's: ``owner`` is the series, group or cohort key)."""

    at: datetime
    owner: str
    tutor: str


@dataclass(frozen=True)
class CreateCourse:
    at: datetime
    course: CoursePlan


@dataclass(frozen=True)
class CreateCohort:
    at: datetime
    cohort: CohortPlan


@dataclass(frozen=True)
class Enroll:
    at: datetime
    cohort: str
    student: str


@dataclass(frozen=True)
class Hold:
    """The tutor's attendance and closeout submit at the class's end + 15
    minutes."""

    at: datetime
    cls: str


@dataclass(frozen=True)
class ReviewCloseout:
    at: datetime
    cls: str
    approve: bool
    reason: str | None = None


@dataclass(frozen=True)
class ResubmitCloseout:
    at: datetime
    cls: str


@dataclass(frozen=True)
class CorrectAttendance:
    at: datetime
    cls: str
    student: str
    status: Mark
    reason: str


@dataclass(frozen=True)
class CorrectCloseout:
    at: datetime
    cls: str
    actual_minutes: int
    reason: str


@dataclass(frozen=True)
class RequestChange:
    at: datetime
    request: ChangeRequestPlan
    note: str


@dataclass(frozen=True)
class DecideChange:
    at: datetime
    request: str
    approve: bool
    exception: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class CenterCancel:
    at: datetime
    cls: str
    reason: str


@dataclass(frozen=True)
class RequestTimeOff:
    at: datetime
    time_off: TimeOffPlan


@dataclass(frozen=True)
class DecideTimeOff:
    at: datetime
    time_off: str
    approve: bool
    reason: str | None = None


@dataclass(frozen=True)
class Substitute:
    at: datetime
    cls: str
    substitute: str
    reason: str


@dataclass(frozen=True)
class AnswerOffer:
    """The tutor answers a session-scope offer (an assignment or a
    substitution) of ``cls``."""

    at: datetime
    cls: str
    tutor: str
    accept: bool
    note: str | None = None


@dataclass(frozen=True)
class ExpireOffer:
    """The offer-expiry step (the worker's, in-process) at its deadline."""

    at: datetime
    cls: str


@dataclass(frozen=True)
class Reoffer:
    """The coordinator offers an ``unassigned`` class to another tutor."""

    at: datetime
    cls: str
    tutor: str


@dataclass(frozen=True)
class RequestSession:
    at: datetime
    request: SessionRequestPlan
    note: str


@dataclass(frozen=True)
class DecideSessionRequest:
    """Confirm (offered to ``tutor``; the class is ``cls``) or reject."""

    at: datetime
    request: str
    cls: str | None
    tutor: str | None
    reason: str | None = None


@dataclass(frozen=True)
class CreateMakeup:
    """The Administrator creates a one-off make-up (``tutor`` ``None``:
    unassigned)."""

    at: datetime
    cls: str


@dataclass(frozen=True)
class Resize:
    """The Administrator shortens one confirmed class (a ``PATCH`` of the
    class, same start, new end; #552 HF-01d): its posted charge is repriced
    by the billing reconciliation step."""

    at: datetime
    cls: str
    minutes: int


@dataclass(frozen=True)
class ChangeWeekly:
    """A tutor asks to drop a weekly window that has classes: a pending
    availability change request."""

    at: datetime
    tutor: str
    weekday: int
    start: str
    end: str


type Event = (
    CreateSeries
    | CreateGroup
    | AcceptSeries
    | CreateCourse
    | CreateCohort
    | Enroll
    | Hold
    | ReviewCloseout
    | ResubmitCloseout
    | CorrectAttendance
    | CorrectCloseout
    | RequestChange
    | DecideChange
    | CenterCancel
    | RequestTimeOff
    | DecideTimeOff
    | Substitute
    | AnswerOffer
    | ExpireOffer
    | Reoffer
    | RequestSession
    | DecideSessionRequest
    | CreateMakeup
    | ChangeWeekly
    | Resize
)

COURSE_EVENTS: Final = (CreateCourse, CreateCohort, Enroll)
"""The events the ``courses`` module executes (the rest are ``schedule``'s;
a cohort's series offer is answered by ``courses`` right after it creates
the cohort)."""


@dataclass(frozen=True)
class Timetable:
    series: tuple[SeriesPlan, ...]
    groups: tuple[GroupPlan, ...]
    courses: tuple[CoursePlan, ...]
    cohorts: tuple[CohortPlan, ...]
    classes: tuple[ClassPlan, ...]
    time_offs: tuple[TimeOffPlan, ...]
    change_requests: tuple[ChangeRequestPlan, ...]
    session_requests: tuple[SessionRequestPlan, ...]
    weekly_change: ChangeWeekly
    events: tuple[Event, ...]
    """Sorted by instant (stable)."""
    attendance_corrections: tuple[CorrectAttendance, ...]
    closeout_corrections: tuple[CorrectCloseout, ...]
    changes_requested: tuple[str, ...]
    declined: tuple[str, ...]
    expired: tuple[str, ...]
    resized: str = ""
    """The class :func:`_Builder.build_resize` shortens (HF-01d)."""
    late_reviews: tuple[str, ...] = ()
    """The group classes reviewed after their fortnight's payroll is
    finalized (HF-01e)."""

    def cls(self, key: str) -> ClassPlan:
        return self.by_key[key]

    @cached_property
    def by_key(self) -> dict[str, ClassPlan]:
        return {c.key: c for c in self.classes}


# --- The calendar: every consistency rule ---------------------------------------------


@dataclass
class Calendar:
    """The occupied time of tutors, rooms and students, by local date, and
    the checks every placement passes (module docstring).

    While the plan is built, a cancelled, moved or reassigned class keeps
    its first booking too: the services see the world in time order, and
    that time was taken when later steps were planned, so it is never
    reused (conservative). :func:`consistency_errors` books final
    placements only."""

    plan: SimulationPlan
    tutor: dict[tuple[str, date], list[tuple[int, int, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    room: dict[tuple[str, date], list[tuple[int, int, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    student: dict[tuple[str, date], list[tuple[int, int, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    time_off: dict[tuple[str, date], list[tuple[int, int]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def __post_init__(self) -> None:
        self.closures = {c.day for c in self.plan.closures}
        self.tutors = {t.key: t for t in self.plan.tutors}
        self.rooms = {r.key: r for r in self.plan.classrooms}
        self.enrolled = {
            s.key: f.enrolls_at for f in self.plan.families for s in f.students
        }
        self._available: dict[tuple[str, date, int, int], bool] = {}

    # -- the rules --

    def available(self, tutor: str, day: date, lo: int, hi: int) -> bool:
        """HF-01's windows and exceptions (:func:`.plan.is_available`),
        memoized."""
        key = (tutor, day, lo, hi)
        found = self._available.get(key)
        if found is None:
            found = is_available(self.plan, tutor, day, hhmm(lo), hhmm(hi))
            self._available[key] = found
        return found

    def center_error(self, day: date, lo: int, hi: int) -> str | None:
        if day in self.closures:
            return "center_closed"
        hours = OPERATING_HOURS.get(day.isoweekday())
        if hours is None or lo < hours[0] or hi > hours[1]:
            return "outside_operating_hours"
        return None

    def tutor_error(
        self, tutor: str, subject: str, day: date, lo: int, hi: int, ignore: str = ""
    ) -> str | None:
        shape = self.tutors[tutor]
        if subject not in shape.subjects:
            return "tutor_not_qualified"
        if day < shape.joins_on or local_instant(day, hhmm(lo)) <= shape.joins_at:
            return "tutor_not_joined"
        if not self.available(tutor, day, lo, hi):
            return "tutor_unavailable"
        for olo, ohi in self.time_off[(tutor, day)]:
            if olo < hi and lo < ohi:
                return "tutor_time_off"
        busy = [b for b in self.tutor[(tutor, day)] if b[2] != ignore]
        gap = shape.required_break or 0
        for olo, ohi, _ in busy:
            if olo < hi and lo < ohi:
                return "tutor_overlap"
            if not (hi + gap <= olo or ohi + gap <= lo):
                return "tutor_break"
        minutes = hi - lo
        if shape.daily_limit is not None:
            if sum(h - lw for lw, h, _ in busy) + minutes > shape.daily_limit:
                return "tutor_daily_limit"
        if shape.weekly_limit is not None:
            start = monday(day)
            week = sum(
                h - lw
                for offset in range(7)
                for lw, h, key in self.tutor[(tutor, start + timedelta(days=offset))]
                if key != ignore
            )
            if week + minutes > shape.weekly_limit:
                return "tutor_weekly_limit"
        return None

    def room_error(
        self, room: str, size: int, day: date, lo: int, hi: int, ignore: str = ""
    ) -> str | None:
        shape = self.rooms[room]
        if shape.deactivated_at is not None:
            return "room_inactive"
        if shape.capacity < size:
            return "room_capacity"
        if shape.windows and not any(
            weekday == day.isoweekday() and mins(s) <= lo and hi <= mins(e)
            for weekday, s, e in shape.windows
        ):
            return "room_unavailable"
        for olo, ohi, key in self.room[(room, day)]:
            if key != ignore and olo < hi and lo < ohi:
                return "room_overlap"
        return None

    def student_error(
        self, student: str, day: date, lo: int, hi: int, ignore: str = ""
    ) -> str | None:
        if local_instant(day, hhmm(lo)) <= self.enrolled[student]:
            return "student_not_enrolled"
        for olo, ohi, key in self.student[(student, day)]:
            if key != ignore and olo < hi and lo < ohi:
                return "student_overlap"
        return None

    def error(
        self,
        subject: str,
        students: Sequence[str],
        where: Placement,
        ignore: str = "",
    ) -> str | None:
        """The first rule ``where`` breaks, or ``None``."""
        lo, hi = where.lo, where.hi
        found = self.center_error(where.day, lo, hi)
        if found is None and where.tutor is not None:
            found = self.tutor_error(where.tutor, subject, where.day, lo, hi, ignore)
        if found is None and where.room is not None:
            found = self.room_error(
                where.room, len(students), where.day, lo, hi, ignore
            )
        for student in students:
            if found is None:
                found = self.student_error(student, where.day, lo, hi, ignore)
        return found

    # -- bookings --

    def book(self, key: str, students: Sequence[str], where: Placement) -> None:
        item = (where.lo, where.hi, key)
        if where.tutor is not None:
            self.tutor[(where.tutor, where.day)].append(item)
        if where.room is not None:
            self.room[(where.room, where.day)].append(item)
        for student in students:
            self.student[(student, where.day)].append(item)

    def minutes_in_week(self, tutor: str, day: date) -> int:
        start = monday(day)
        return sum(
            hi - lo
            for offset in range(7)
            for lo, hi, _ in self.tutor[(tutor, start + timedelta(days=offset))]
        )


# --- Students -------------------------------------------------------------------------


@dataclass(frozen=True)
class StudentFacts:
    key: str
    level: str
    adult: bool
    enrolls_at: datetime
    requester: str | None
    """The login that acts for the student (an adult's own, a minor's first
    guardian with a login), or ``None``."""


def student_facts(plan: SimulationPlan) -> list[StudentFacts]:
    facts: list[StudentFacts] = []
    for family in plan.families:
        guardian = next(
            (
                g.account.username
                for g in family.guardians
                if g.portal and g.account is not None
            ),
            None,
        )
        for student in family.students:
            own = student.account.username if student.account is not None else None
            facts.append(
                StudentFacts(
                    key=student.key,
                    level=student.level,
                    adult=student.is_adult,
                    enrolls_at=family.enrolls_at,
                    requester=own if student.is_adult else guardian,
                )
            )
    return facts


def _level_subjects(plan: SimulationPlan, level: str) -> list[str]:
    return [s.key for s in plan.subjects if s.level == level]


# --- The builder ----------------------------------------------------------------------


class TimetableError(RuntimeError):
    """The plan can't place something: the profile's shapes have a bug."""


class _Builder:
    def __init__(self, plan: SimulationPlan, seed: int, attempt: int = 0) -> None:
        self.plan = plan
        # Deterministic synthetic data, not security: a seeded generator.
        self.rng = random.Random(seed + RNG_OFFSET + 1000 * attempt)  # noqa: S311
        self.texts = TextPool(plan.language)
        self.volumes = VOLUMES[plan.profile]
        self.cal = Calendar(plan)
        self.students = {s.key: s for s in student_facts(plan)}
        self.accounts = {t.key: t.account.username for t in plan.tutors}
        self.series: list[SeriesPlan] = []
        self.groups: list[GroupPlan] = []
        self.courses: list[CoursePlan] = []
        self.cohorts: list[CohortPlan] = []
        self.classes: dict[str, ClassPlan] = {}
        self.events: list[Event] = []
        self.time_offs: list[TimeOffPlan] = []
        self.change_requests: list[ChangeRequestPlan] = []
        self.session_requests: list[SessionRequestPlan] = []
        self.load: dict[str, int] = {t.key: 0 for t in plan.tutors}
        self.weekly_days: dict[str, set[int]] = defaultdict(set)
        self.individual_days: dict[str, set[int]] = defaultdict(set)
        self.used: set[str] = set()
        """Classes already given an outcome."""
        self.n = 0
        self.attendance_corrections: list[CorrectAttendance] = []
        self.closeout_corrections: list[CorrectCloseout] = []
        self.changes_requested: list[str] = []
        self.late_reviews: list[str] = []
        self.declined: list[str] = []
        self.expired: list[str] = []
        self.pool_index: dict[str, int] = defaultdict(int)

    # -- helpers --

    def text(self, key: str) -> str:
        """The next value of a pool list, in turn."""
        values = self.texts.choices(key)
        value = values[self.pool_index[key] % len(values)]
        self.pool_index[key] += 1
        return value

    def next_key(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n:04d}"

    def tutors_for(self, subject: str, day: date) -> list[str]:
        keys = [
            t.key
            for t in self.plan.tutors
            # Offers and substitutions come days before the class: a tutor
            # who joins later is a candidate a week after joining.
            if subject in t.subjects
            and (
                t.joins_on <= SIM_SETUP.date() or t.joins_on + timedelta(days=7) <= day
            )
        ]
        self.rng.shuffle(keys)
        return sorted(keys, key=lambda k: self.load[k])

    def rooms_for(self, tutor: str, size: int, required: bool) -> list[str | None]:
        preferred = list(self.cal.tutors[tutor].preferred)
        others = sorted(
            (r.key for r in self.plan.classrooms if r.key not in preferred),
            key=lambda k: (self.cal.rooms[k].capacity, k),
        )
        rooms: list[str | None] = [
            r
            for r in preferred + others
            if self.cal.rooms[r].deactivated_at is None
            and self.cal.rooms[r].capacity >= size
        ]
        if not required:
            rooms.append(None)
        return rooms

    def starts(self, weekday: int, minutes: int, earliest: int) -> list[int]:
        """The grid starts of a ``minutes`` class on ``weekday`` from
        ``earliest`` (a school-day limit: Saturdays start at opening)."""
        lo, hi = OPERATING_HOURS[weekday]
        if weekday == 6:
            earliest = 0
        return [m for m in range(max(lo, earliest), hi - minutes + 1, GRID)]

    def first_on_or_after(self, day: date, weekday: int) -> date:
        return day + timedelta(days=(weekday - day.isoweekday()) % 7)

    # -- series --

    def _series_fits(
        self, subject: str, students: Sequence[str], series: SeriesPlan
    ) -> bool:
        for day in series.dates():
            if day in self.cal.closures:
                continue
            where = Placement(
                day, series.start, series.minutes, series.tutor, series.room
            )
            if self.cal.error(subject, students, where) is not None:
                return False
        return True

    def _add_series(self, series: SeriesPlan) -> None:
        self.series.append(series)
        for n, day in enumerate(series.dates()):
            where = Placement(
                day, series.start, series.minutes, series.tutor, series.room
            )
            key = f"{series.key}#{n + 1:02d}"
            closed = day in self.cal.closures
            status: Status = (
                "skipped_closure"
                if closed
                else ("completed" if day < SIM_ANCHOR else "confirmed")
            )
            self.classes[key] = ClassPlan(
                key=key,
                series=series.key,
                kind=series.kind,
                subject=series.subject,
                students=series.students,
                placed=where,
                status=status,
                final=where,
            )
            if not closed:
                self.cal.book(key, series.students, where)
                self.load[series.tutor] += series.minutes

    def find_series(
        self,
        key: str,
        kind: ClassKind,
        subject: str,
        students: Sequence[str],
        weekday_sets: Sequence[tuple[int, ...]],
        minutes_options: Sequence[int],
        from_day: date,
        until: date,
        created_at: datetime,
        earliest: int,
        room_required: bool,
        tutors: Sequence[str] | None = None,
    ) -> SeriesPlan | None:
        """The first series (tutor by load, then weekdays, length, start and
        room) whose every date passes the calendar."""
        for tutor in (
            tutors if tutors is not None else self.tutors_for(subject, from_day)
        ):
            for weekdays in weekday_sets:
                first = min(self.first_on_or_after(from_day, w) for w in weekdays)
                for minutes in minutes_options:
                    starts = self.starts(weekdays[0], minutes, earliest)
                    self.rng.shuffle(starts)
                    for start in starts:
                        series = SeriesPlan(
                            key=key,
                            kind=kind,
                            subject=subject,
                            tutor=tutor,
                            room=None,
                            weekdays=tuple(sorted(weekdays)),
                            start=hhmm(start),
                            minutes=minutes,
                            starts_on=first,
                            until=until,
                            students=tuple(students),
                            created_at=created_at,
                        )
                        # The tutor and the students first, then a room.
                        if not self._series_fits(subject, students, series):
                            continue
                        for room in self.rooms_for(tutor, len(students), room_required):
                            placed = replace(series, room=room)
                            if room is None or self._series_fits(
                                subject, students, placed
                            ):
                                return placed
        return None

    # -- the timetable --

    def build_groups(self) -> None:
        taken: set[str] = set()
        for shape in self.volumes.groups:
            level = next(s.level for s in self.plan.subjects if s.key == shape.subject)
            pool = [
                s.key
                for s in self.students.values()
                if s.level == level and s.enrolls_at == SIM_SETUP and s.key not in taken
            ]
            size = min(shape.size, len(pool))
            if size < 2:
                raise TimetableError(f"{shape.key}: not enough students")
            students = sorted(self.rng.sample(pool, size))
            taken.update(students)
            series = self.find_series(
                f"group:{shape.key}",
                "group",
                shape.subject,
                students,
                [shape.weekdays],
                (60, 90),
                SIM_START,
                SIM_HORIZON,
                SIM_SETUP,
                MINOR_EARLIEST,
                room_required=True,
            )
            if series is None:
                raise TimetableError(f"{shape.key}: no slot")
            self._add_series(series)
            for student in students:
                self.weekly_days[student].update(series.weekdays)
            self.groups.append(
                GroupPlan(
                    key=shape.key,
                    name=self.texts.mapping("group_names")[shape.key],
                    max_students=size + 2,
                    series=series,
                )
            )
            self.events.append(CreateGroup(SIM_SETUP, self.groups[-1]))
            self.events.append(
                AcceptSeries(SIM_SETUP + timedelta(minutes=30), shape.key, series.tutor)
            )

    def build_courses(self) -> None:
        for shape in self.volumes.courses:
            created_at = (
                SIM_SETUP if shape.created is None else local_instant(*shape.created)
            )
            course = CoursePlan(
                key=shape.key,
                name=self.texts.mapping("course_names")[shape.key],
                description=self.texts.mapping("course_descriptions")[shape.key],
                subject=shape.subject,
                session_count=shape.session_count,
                minutes=shape.minutes,
                min_attendance_percent=80,
                modules=tuple(
                    zip(
                        self.texts.lists("course_modules")[shape.key],
                        shape.modules,
                        self.texts.lists("course_materials")[shape.key],
                        strict=True,
                    )
                ),
                assessments=tuple(
                    (title, index == 0, 70 if index == 0 else None)
                    for index, title in enumerate(
                        self.texts.lists("course_assessments")[shape.key]
                    )
                ),
                created_at=created_at,
            )
            self.courses.append(course)
            self.events.append(CreateCourse(created_at, course))
            level = next(s.level for s in self.plan.subjects if s.key == shape.subject)
            first_class = local_instant(
                self.first_on_or_after(shape.starts_on, shape.weekdays[0]), "09:00"
            )
            pool = [
                s.key
                for s in self.students.values()
                if s.level == level and s.enrolls_at < created_at + timedelta(days=1)
            ]
            pool = [k for k in pool if self.students[k].enrolls_at < first_class]
            size = min(shape.size, len(pool))
            if size < 2:
                raise TimetableError(f"{shape.key}: not enough students")
            pool.sort()
            # Try student sets until one fits (times avoid their groups).
            for _ in range(40):
                students = sorted(self.rng.sample(pool, size))
                series = self._course_series(shape, students, created_at)
                if series is not None:
                    break
            else:
                raise TimetableError(f"{shape.key}: no slot")
            self._add_series(series)
            enrollments = tuple(
                (
                    student,
                    created_at
                    + timedelta(minutes=10)
                    + (
                        timedelta(days=index % 4, hours=1)
                        if shape.created
                        else timedelta()
                    ),
                )
                for index, student in enumerate(students)
            )
            cohort = CohortPlan(
                key=shape.key,
                name=self.texts.mapping("cohort_names")[shape.key],
                course=shape.key,
                max_students=size + 4,
                series=series,
                enrollments=enrollments,
            )
            self.cohorts.append(cohort)
            self.events.append(CreateCohort(created_at + timedelta(minutes=5), cohort))
            for student, at in enrollments:
                self.events.append(Enroll(at, shape.key, student))

    def _course_series(
        self, shape: CourseShape, students: Sequence[str], created_at: datetime
    ) -> SeriesPlan | None:
        from_day = shape.starts_on
        dates: list[date] = []
        day = from_day
        while len(dates) < shape.session_count:
            if day.isoweekday() in shape.weekdays and day not in self.cal.closures:
                dates.append(day)
            day += timedelta(days=1)
        until = dates[-1]
        if until > SIM_HORIZON:
            raise TimetableError(f"{shape.key}: ends after the horizon")
        return self.find_series(
            f"cohort:{shape.key}",
            "course",
            shape.subject,
            students,
            [shape.weekdays],
            (shape.minutes,),
            from_day,
            until,
            created_at,
            MINOR_EARLIEST,
            room_required=True,
        )

    def build_individual(self) -> None:
        facts = list(self.students.values())
        counts = list(self.volumes.slots)
        self.rng.shuffle(counts)
        missing = 0
        taken: dict[str, list[str]] = defaultdict(list)
        for student, count in zip(facts, counts, strict=True):
            subjects = _level_subjects(self.plan, student.level)
            self.rng.shuffle(subjects)
            for subject in subjects:
                if len(taken[student.key]) < count and self.individual_slot(
                    student, subject, joiner=False
                ):
                    taken[student.key].append(subject)
            if not taken[student.key]:
                raise TimetableError(f"{student.key}: no slot")
            missing += count - len(taken[student.key])
        # A slot that didn't fit goes to another student (same total).
        for student in facts:
            if missing == 0:
                break
            for subject in _level_subjects(self.plan, student.level):
                if (
                    missing
                    and len(taken[student.key]) < 3
                    and subject not in taken[student.key]
                    and self.individual_slot(student, subject, joiner=False)
                ):
                    taken[student.key].append(subject)
                    missing -= 1
        if missing:
            raise TimetableError(f"{missing} individual slots don't fit")

    def individual_slot(
        self, student: StudentFacts, subject: str, joiner: bool
    ) -> bool:
        created_at = (
            local_instant(date(2026, 10, 1), "10:00")
            if joiner
            else (
                SIM_SETUP
                if student.enrolls_at == SIM_SETUP
                else student.enrolls_at + timedelta(hours=1)
            )
        )
        from_day = (
            SIM_START
            if created_at == SIM_SETUP
            else (created_at + timedelta(days=2)).astimezone(ZONE).date()
        )
        # Preferably one class a day: first the days free of any class, then
        # the days with only a group or course class.
        free = [d for d in (1, 2, 3, 4, 5, 6) if d not in self.weekly_days[student.key]]
        shared = [
            d
            for d in (1, 2, 3, 4, 5, 6)
            if d in self.weekly_days[student.key]
            and d not in self.individual_days[student.key]
        ]
        self.rng.shuffle(free)
        self.rng.shuffle(shared)
        weekdays = free + shared
        minutes = [60, 90]
        self.rng.shuffle(minutes)
        tutors = None
        if joiner:
            tutors = [t.key for t in self.plan.tutors if t.joins_on > SIM_SETUP.date()]
        key = self.next_key("s")
        series = self.find_series(
            key,
            "individual",
            subject,
            [student.key],
            [(w,) for w in weekdays],
            minutes,
            from_day,
            SIM_HORIZON,
            created_at,
            0 if student.adult else MINOR_EARLIEST,
            room_required=False,
            tutors=tutors,
        )
        if series is None:
            return False
        self._add_series(series)
        self.weekly_days[student.key].update(series.weekdays)
        self.individual_days[student.key].update(series.weekdays)
        self.events.append(CreateSeries(created_at, series))
        self.events.append(
            AcceptSeries(created_at + timedelta(minutes=30), key, series.tutor)
        )
        return True

    def build_joiner(self) -> None:
        joiners = [t for t in self.plan.tutors if t.joins_on > SIM_SETUP.date()]
        added = 0
        for tutor in joiners:
            candidates = [
                (s, subject)
                for s in self.students.values()
                for subject in tutor.subjects
                if subject in _level_subjects(self.plan, s.level)
                and len(self.individual_days[s.key]) < 3
            ]
            self.rng.shuffle(candidates)
            taken: set[str] = set()
            for student, subject in candidates:
                if added >= self.volumes.joiner_slots:
                    return
                if student.key in taken or any(
                    c.kind == "individual"
                    and c.subject == subject
                    and student.key in c.students
                    for c in self.classes.values()
                ):
                    continue
                if self.individual_slot(student, subject, joiner=True):
                    taken.add(student.key)
                    added += 1
        if added < self.volumes.joiner_slots:
            raise TimetableError("the joining tutor has too few slots")

    # -- outcomes --

    def past(
        self, first: date = date(2026, 9, 8), last: date = date(2026, 11, 13)
    ) -> list[ClassPlan]:
        """Individual series classes before the anchor with no outcome yet,
        shuffled."""
        found = [
            c
            for c in self.classes.values()
            if c.kind == "individual"
            and c.series is not None
            and c.status == "completed"
            and c.key not in self.used
            and first <= c.placed.day <= last
            # Requests about it come days before: after the enrollment.
            and all(
                c.placed.starts_at >= self.students[s].enrolls_at + timedelta(days=6)
                for s in c.students
            )
        ]
        found.sort(key=lambda c: c.key)
        self.rng.shuffle(found)
        return found

    def update(self, key: str, **changes: object) -> ClassPlan:
        updated = replace(self.classes[key], **changes)  # type: ignore[arg-type]
        self.classes[key] = updated
        return updated

    def cancel_way_counts(self, total: int) -> list[str]:
        ways: list[str] = []
        for way, minimum in self.volumes.cancel_mix:
            ways.extend([way] * minimum)
        while len(ways) < total:
            ways.append(
                self.rng.choice(["on_time", "on_time", "late", "tutor", "center"])
            )
        return ways

    def build_cancellations(self, total: int) -> list[ClassPlan]:
        cancelled: list[ClassPlan] = []
        ways = self.cancel_way_counts(total)
        late_window = 0
        for way in ways:
            # Three make-up-eligible cancellations late enough for a make-up
            # after the anchor (build_pending).
            if way != "late" and late_window < LATE_WINDOW_CANCELLATIONS:
                late_window += 1
                pool = self.past(*LATE_WINDOW)
            else:
                pool = self.past()
            for c in pool:
                if c.key in self.used:
                    continue
                student = self.students[c.students[0]]
                if (
                    way in ("on_time", "late", "exception")
                    and student.requester is None
                ):
                    continue
                cancelled.append(self.cancel(c, way))
                break
            else:
                raise TimetableError(f"no class to cancel ({way})")
        return cancelled

    def cancel(self, c: ClassPlan, way: str) -> ClassPlan:
        self.used.add(c.key)
        start = c.placed.starts_at
        if way == "center":
            at = start - timedelta(days=1, hours=5)
            self.events.append(
                CenterCancel(at, c.key, self.text("center_cancellation_reasons"))
            )
            return self.update(c.key, status="cancelled")
        student = self.students[c.students[0]]
        if way == "tutor":
            requester, by = self.accounts[c.placed.tutor or ""], "tutor"
            created = start - timedelta(days=2, hours=-3)
            note = self.text("tutor_cancellation_notes")
        else:
            assert student.requester is not None
            requester, by = student.requester, "family"
            note = self.text("family_cancellation_notes")
            if way == "on_time":
                created = start - timedelta(days=3, hours=-2)
            else:
                day_start = local_instant(c.placed.day, "07:00")
                created = max(start - timedelta(hours=6), day_start)
        classification = (
            "excused"
            if by == "tutor" or way == "exception"
            else ("on_time" if start - created >= CHANGE_NOTICE else "late")
        )
        decided = (
            created + timedelta(hours=14)
            if start - created > timedelta(hours=20)
            else created + timedelta(minutes=45)
        )
        request = ChangeRequestPlan(
            key=self.next_key("cr"),
            cls=c.key,
            requester=requester,
            by=by,  # type: ignore[arg-type]
            kind="cancellation",
            created_at=created,
            proposed=None,
            decision="approved",
            exception=way == "exception",
            classification=classification,  # type: ignore[arg-type]
        )
        self.change_requests.append(request)
        self.events.append(RequestChange(created, request, note))
        self.events.append(
            DecideChange(
                decided,
                request.key,
                approve=True,
                exception=way == "exception",
                reason=self.text("exception_reasons") if way == "exception" else None,
            )
        )
        return self.update(c.key, status="cancelled")

    def cancelled_at(self, key: str) -> datetime:
        """When the cancellation is decided."""
        for event in self.events:
            if isinstance(event, CenterCancel) and event.cls == key:
                return event.at
            if isinstance(event, DecideChange):
                request = next(
                    r for r in self.change_requests if r.key == event.request
                )
                if request.cls == key:
                    return event.at
        raise TimetableError(key)

    def makeup_allowed(self, key: str) -> bool:
        request = next((r for r in self.change_requests if r.cls == key), None)
        if request is None:
            return True  # a center cancellation: excused
        return request.classification in ("on_time", "excused")

    def build_reschedules(self, total: int) -> None:
        done = 0
        for c in self.past():
            if done >= total:
                return
            if c.key in self.used:
                continue
            student = self.students[c.students[0]]
            by = (
                "family" if student.requester is not None and done % 3 != 2 else "tutor"
            )
            created = c.placed.starts_at - timedelta(days=4, hours=-2)
            target = self.find_move(c, created)
            if target is None:
                continue
            self.used.add(c.key)
            self.cal.book(c.key, c.students, target)
            requester = (
                student.requester
                if by == "family" and student.requester is not None
                else self.accounts[c.placed.tutor or ""]
            )
            request = ChangeRequestPlan(
                key=self.next_key("cr"),
                cls=c.key,
                requester=requester,
                by=by,  # type: ignore[arg-type]
                kind="reschedule",
                created_at=created,
                proposed=target,
                decision="approved",
                exception=False,
                classification="on_time" if by == "family" else "excused",
            )
            self.change_requests.append(request)
            self.events.append(
                RequestChange(created, request, self.text("reschedule_notes"))
            )
            self.events.append(
                DecideChange(created + timedelta(hours=14), request.key, approve=True)
            )
            self.update(c.key, final=target, rescheduled=True)
            done += 1
        if done < total:
            raise TimetableError("too few reschedules")

    def find_move(self, c: ClassPlan, created: datetime) -> Placement | None:
        """A valid new slot for ``c`` with its own tutor, no room, within a few
        days, at least a day after ``created`` and before the anchor."""
        offsets = [0, 1, -1, 2, -2, 3]
        for offset in offsets:
            day = c.placed.day + timedelta(days=offset)
            if day >= SIM_ANCHOR or day.isoweekday() not in OPERATING_HOURS:
                continue
            earliest = (
                0
                if self.students[c.students[0]].adult or day.isoweekday() == 6
                else MINOR_EARLIEST
            )
            for start in self.starts(day.isoweekday(), c.placed.minutes, earliest):
                where = Placement(
                    day, hhmm(start), c.placed.minutes, c.placed.tutor, None
                )
                if where.starts_at == c.placed.starts_at:
                    continue
                if where.starts_at < created + timedelta(days=1, hours=2):
                    continue
                if self.cal.error(c.subject, c.students, where, ignore=c.key) is None:
                    return where
        return None

    def build_substitutions(self, total: int) -> None:
        done = 0
        for c in self.past():
            if done >= total:
                break
            if c.key in self.used:
                continue
            substitute = self.find_substitute(c)
            if substitute is None:
                continue
            self.used.add(c.key)
            tutor = c.placed.tutor or ""
            start = c.placed.starts_at
            off = TimeOffPlan(
                key=self.next_key("to"),
                cls=c.key,
                tutor=tutor,
                day=c.placed.day,
                start=c.placed.start,
                end=c.placed.end,
                note=self.text("time_off_notes"),
                decision="approved",
            )
            self.time_offs.append(off)
            self.cal.time_off[(tutor, c.placed.day)].append((c.placed.lo, c.placed.hi))
            final = replace(c.placed, tutor=substitute)
            self.cal.book(c.key, c.students, final)
            asked = start - timedelta(days=4, hours=-1)
            self.events.append(RequestTimeOff(asked, off))
            decided = start - timedelta(days=3)
            decided = local_instant(decided.astimezone(ZONE).date(), "09:00")
            self.events.append(DecideTimeOff(decided, off.key, approve=True))
            self.events.append(
                Substitute(
                    decided + timedelta(hours=1),
                    c.key,
                    substitute,
                    self.text("substitution_reasons"),
                )
            )
            self.events.append(
                AnswerOffer(
                    decided + timedelta(hours=3), c.key, substitute, accept=True
                )
            )
            self.update(c.key, final=final, substituted=True)
            done += 1
        if done < total:
            raise TimetableError("too few substitutions")
        # One time off rejected: the class stays with its tutor.
        for c in self.past():
            if c.key in self.used:
                continue
            self.used.add(c.key)
            off = TimeOffPlan(
                key=self.next_key("to"),
                cls=c.key,
                tutor=c.placed.tutor or "",
                day=c.placed.day,
                start=c.placed.start,
                end=c.placed.end,
                note=self.text("time_off_notes"),
                decision="rejected",
            )
            self.time_offs.append(off)
            asked = c.placed.starts_at - timedelta(days=4, hours=-1)
            decided = local_instant(
                (c.placed.starts_at - timedelta(days=3)).astimezone(ZONE).date(),
                "09:00",
            )
            self.events.append(RequestTimeOff(asked, off))
            self.events.append(
                DecideTimeOff(
                    decided,
                    off.key,
                    approve=False,
                    reason=self.text("availability_rejection_reasons"),
                )
            )
            return
        raise TimetableError("no class for the rejected time off")

    def find_substitute(self, c: ClassPlan) -> str | None:
        for tutor in self.tutors_for(c.subject, c.placed.day):
            if tutor == c.placed.tutor:
                continue
            where = replace(c.placed, tutor=tutor)
            if self.cal.error(c.subject, c.students, where, ignore=c.key) is None:
                return tutor
        return None

    def build_rejected_changes(self, total: int) -> None:
        done = 0
        for c in self.past():
            if done >= total:
                return
            student = self.students[c.students[0]]
            if c.key in self.used or student.requester is None:
                continue
            self.used.add(c.key)
            created = c.placed.starts_at - timedelta(days=3, hours=-2)
            kind: Literal["cancellation", "reschedule"] = (
                "cancellation" if done % 2 == 0 else "reschedule"
            )
            proposed = None
            if kind == "reschedule":
                proposed = self.find_move(c, created)
                if proposed is None:
                    kind = "cancellation"
            request = ChangeRequestPlan(
                key=self.next_key("cr"),
                cls=c.key,
                requester=student.requester,
                by="family",
                kind=kind,
                created_at=created,
                proposed=proposed,
                decision="rejected",
                exception=False,
                classification="on_time",
            )
            self.change_requests.append(request)
            note = self.text(
                "family_cancellation_notes"
                if kind == "cancellation"
                else "reschedule_notes"
            )
            self.events.append(RequestChange(created, request, note))
            self.events.append(
                DecideChange(
                    created + timedelta(hours=14),
                    request.key,
                    approve=False,
                    reason=self.text("change_rejection_reasons"),
                )
            )
            done += 1
        if done < total:
            raise TimetableError("too few rejected changes")

    # -- one-off sessions --

    def find_one_off(
        self,
        student: StudentFacts,
        subject: str,
        days: Iterable[date],
        tutors: Sequence[str] | None = None,
        exclude: Sequence[str] = (),
        minutes: int = 60,
        needed: int = 1,
    ) -> Placement | None:
        """The first free slot (day, then tutor by load, then start) for a
        one-off class, with no room; with ``needed`` 2, only a slot where a
        second tutor is free too (an offer declined or left to expire)."""
        for day in days:
            if day.isoweekday() not in OPERATING_HOURS or day in self.cal.closures:
                continue
            earliest = 0 if student.adult or day.isoweekday() == 6 else MINOR_EARLIEST
            starts = self.starts(day.isoweekday(), minutes, earliest)
            order = [
                t
                for t in (
                    tutors if tutors is not None else self.tutors_for(subject, day)
                )
                if t not in exclude
            ]
            for tutor in order:
                for start in starts:
                    where = Placement(day, hhmm(start), minutes, tutor, None)
                    if self.cal.error(subject, [student.key], where) is not None:
                        continue
                    others = sum(
                        1
                        for other in self.tutors_for(subject, day)
                        if other != tutor
                        and self.cal.error(
                            subject, [student.key], replace(where, tutor=other)
                        )
                        is None
                    )
                    if others + 1 >= needed:
                        return where
        return None

    def other_tutor(self, c: ClassPlan, exclude: Sequence[str]) -> str | None:
        for tutor in self.tutors_for(c.subject, c.placed.day):
            if tutor in exclude:
                continue
            where = replace(c.placed, tutor=tutor)
            if self.cal.error(c.subject, c.students, where, ignore=c.key) is None:
                return tutor
        return None

    def add_one_off(
        self,
        key: str,
        student: str,
        subject: str,
        where: Placement,
        status: Status,
        created_at: datetime,
        makeup_of: str | None = None,
    ) -> ClassPlan:
        c = ClassPlan(
            key=key,
            series=None,
            kind="individual",
            subject=subject,
            students=(student,),
            placed=where,
            status=status,
            final=where,
            makeup_of=makeup_of,
            created_at=created_at,
        )
        self.classes[key] = c
        self.cal.book(key, c.students, where)
        return c

    def assign_answer(self, c: ClassPlan, offered_at: datetime, way: str) -> ClassPlan:
        """After an offer to ``c.placed.tutor`` at ``offered_at``: accepted,
        declined then re-offered, or left to expire then re-offered."""
        tutor = c.placed.tutor or ""
        if way == "accept":
            self.events.append(
                AnswerOffer(offered_at + timedelta(hours=3), c.key, tutor, True)
            )
            return c
        other = self.other_tutor(c, exclude=[tutor])
        if other is None:
            raise TimetableError(f"{c.key}: no second tutor")
        if way == "decline":
            self.declined.append(c.key)
            at = offered_at + timedelta(hours=3)
            self.events.append(
                AnswerOffer(at, c.key, tutor, False, self.text("offer_decline_notes"))
            )
        else:
            self.expired.append(c.key)
            at = min(offered_at + RESPONSE_HOURS, c.placed.starts_at) + timedelta(
                minutes=5
            )
            self.events.append(ExpireOffer(at, c.key))
        final = replace(c.placed, tutor=other)
        self.cal.book(c.key, c.students, final)
        self.events.append(Reoffer(at + timedelta(hours=1), c.key, other))
        self.events.append(AnswerOffer(at + timedelta(hours=3), c.key, other, True))
        return self.update(c.key, final=final)

    def build_session_requests(self) -> None:
        requesters = [s for s in self.students.values() if s.requester is not None]
        requesters.sort(key=lambda s: s.key)
        self.rng.shuffle(requesters)
        ways = ["decline"] * self.volumes.declined_offers + [
            "expire"
        ] * self.volumes.expired_offers
        made = 0
        index = 0
        day = date(2026, 9, 21)
        while made < self.volumes.approved_requests + self.volumes.rejected_requests:
            if index > 500:
                raise TimetableError("too few session requests")
            student = requesters[index % len(requesters)]
            index += 1
            subject = self.rng.choice(_level_subjects(self.plan, student.level))
            confirm = made < self.volumes.approved_requests
            target_day = day + timedelta(days=(made * 7) % 49)
            days = [target_day + timedelta(days=k) for k in range(5)]
            way = ways[0] if ways and confirm else "accept"
            # A declined or expired offer needs a second tutor.
            where = self.find_one_off(
                student, subject, days, needed=1 if way == "accept" else 2
            )
            if where is None or where.day >= SIM_ANCHOR:
                continue
            created = where.starts_at - timedelta(days=5)
            created = local_instant(created.astimezone(ZONE).date(), "19:00")
            if created <= student.enrolls_at:
                continue
            decided = local_instant(
                (created + timedelta(days=1)).astimezone(ZONE).date(), "10:00"
            )
            key = self.next_key("sr")
            request = SessionRequestPlan(
                key=key,
                requester=student.requester or "",
                student=student.key,
                subject=subject,
                placement=replace(where, tutor=None, room=None),
                created_at=created,
                decision="confirmed" if confirm else "rejected",
            )
            self.session_requests.append(request)
            self.events.append(
                RequestSession(created, request, self.text("session_request_notes"))
            )
            if not confirm:
                self.events.append(
                    DecideSessionRequest(
                        decided,
                        key,
                        None,
                        None,
                        reason=self.text("session_request_rejection_reasons"),
                    )
                )
                made += 1
                continue
            cls_key = f"{key}:class"
            c = self.add_one_off(
                cls_key, student.key, subject, where, "completed", decided
            )
            self.events.append(DecideSessionRequest(decided, key, cls_key, where.tutor))
            if way != "accept":
                ways.pop(0)
            self.assign_answer(c, decided, way)
            made += 1

    def build_makeups(self, cancelled: Sequence[ClassPlan]) -> list[ClassPlan]:
        """The completed make-ups; returns the allowed cancellations left."""
        allowed = [c for c in cancelled if self.makeup_allowed(c.key)]
        early = [c for c in allowed if c.placed.day < LATE_WINDOW[0]]
        early.sort(key=lambda c: c.placed.starts_at)
        made = 0
        for c in early:
            if made >= self.volumes.makeups:
                break
            decided = self.cancelled_at(c.key)
            created = local_instant(
                (decided + timedelta(days=1)).astimezone(ZONE).date(), "10:00"
            )
            if created < c.placed.starts_at - timedelta(days=6):
                created = local_instant(c.placed.day + timedelta(days=1), "10:00")
            student = self.students[c.students[0]]
            first = (created + timedelta(days=2)).astimezone(ZONE).date()
            last = min(
                (c.placed.starts_at + MAKEUP_WINDOW).astimezone(ZONE).date()
                - timedelta(days=1),
                LAST_DECIDED_DAY - timedelta(days=1),
            )
            days = [first + timedelta(days=k) for k in range((last - first).days + 1)]
            tutors = [c.placed.tutor or ""] + [
                t for t in self.tutors_for(c.subject, first) if t != c.placed.tutor
            ]
            where = self.find_one_off(
                student, c.subject, days, tutors=tutors, minutes=c.placed.minutes
            )
            if where is None:
                continue
            key = f"{c.key}:makeup"
            m = self.add_one_off(
                key, student.key, c.subject, where, "completed", created, c.key
            )
            self.events.append(CreateMakeup(created, key))
            self.assign_answer(m, created, "accept")
            made += 1
        if made < self.volumes.makeups:
            raise TimetableError("too few make-ups")
        return [c for c in allowed if f"{c.key}:makeup" not in self.classes]

    # -- closeouts and corrections --

    def build_holds(self) -> None:
        marks: list[Mark] = ["present", "late", "absent"]
        weights = [88, 6, 6]
        completed = sorted(
            (c for c in self.classes.values() if c.status == "completed"),
            key=lambda c: (c.final.starts_at, c.key),
        )
        for c in completed:
            attendance = tuple(
                (student, self.rng.choices(marks, weights)[0]) for student in c.students
            )
            self.update(c.key, attendance=attendance)
            end = c.final.ends_at
            self.events.append(Hold(end + CLOSEOUT_AFTER, c.key))
        candidates = [
            c
            for c in completed
            if c.final.day <= date(2026, 11, 13) and c.series is not None
        ]
        candidates.sort(key=lambda c: c.key)
        self.rng.shuffle(candidates)
        n_changes = max(2, round(len(completed) * self.volumes.changes_requested_rate))
        picked = candidates[:n_changes]
        for c in picked:
            self.changes_requested.append(c.key)
        rest = candidates[n_changes:]
        corrected = {
            c.key
            for c in rest[
                : self.volumes.attendance_corrections
                + self.volumes.closeout_corrections
            ]
        }
        self.late_reviews = self.pick_late_reviews(completed, corrected)
        for c in completed:
            end = c.final.ends_at
            if c.key in self.late_reviews:
                self.events.append(
                    ReviewCloseout(
                        after_payroll(c.final.day, 3, "12:00"), c.key, approve=True
                    )
                )
            elif c.key in self.changes_requested:
                self.events.append(
                    ReviewCloseout(
                        end + APPROVE_AFTER,
                        c.key,
                        approve=False,
                        reason=self.text("closeout_change_reasons"),
                    )
                )
                next_day = (end + timedelta(days=1)).astimezone(ZONE).date()
                self.events.append(
                    ResubmitCloseout(local_instant(next_day, "10:00"), c.key)
                )
                self.events.append(
                    ReviewCloseout(
                        local_instant(next_day, "12:00"), c.key, approve=True
                    )
                )
            else:
                self.events.append(
                    ReviewCloseout(end + APPROVE_AFTER, c.key, approve=True)
                )
        for c in rest[: self.volumes.attendance_corrections]:
            student, mark = self.classes[c.key].attendance[0]
            status: Mark = "late" if mark != "late" else "present"
            at = local_instant(c.final.day + timedelta(days=2), "11:00")
            event = CorrectAttendance(
                at, c.key, student, status, self.text("attendance_correction_reasons")
            )
            self.attendance_corrections.append(event)
            self.events.append(event)
        start = self.volumes.attendance_corrections
        for c in rest[start : start + self.volumes.closeout_corrections]:
            at = local_instant(c.final.day + timedelta(days=3), "11:30")
            if fortnight_end(c.final.day) + timedelta(days=4) < SIM_ANCHOR:
                # HF-01e: found after the fortnight's payroll is finalized,
                # so payroll posts it as an ``hours_correction``.
                at = max(at, after_payroll(c.final.day, 4, "11:30"))
            event = CorrectCloseout(
                at,
                c.key,
                c.final.minutes - 10,
                self.text("closeout_correction_reasons"),
            )
            self.closeout_corrections.append(event)
            self.events.append(event)

    def pick_late_reviews(
        self, completed: Sequence[ClassPlan], excluded: set[str]
    ) -> list[str]:
        """HF-01e: in each of :data:`LATE_REVIEW_FORTNIGHTS`, the last held
        group class (no send-back, no correction, its own tutor) is reviewed
        on the Wednesday after the fortnight, once payroll has finalized it.
        Group classes consume no package unit, so billing doesn't move. No
        RNG: every other plan value is unchanged."""
        picked: list[str] = []
        for first in LATE_REVIEW_FORTNIGHTS:
            found = [
                c
                for c in completed
                if c.kind == "group"
                and fortnight_start(c.final.day) == first
                and c.key not in excluded
                and c.key not in self.changes_requested
                and not c.substituted
            ]
            if not found:
                raise TimetableError(f"no group class to review late in {first}")
            picked.append(max(found, key=lambda c: (c.final.starts_at, c.key)).key)
        return picked

    def build_resize(self) -> str:
        """HF-01d: the first confirmed 90-minute series class from 23 Nov
        that no other outcome uses is shortened to 60 minutes on 12 Nov (a
        ``PATCH``: same start, new end; no RNG). Shortening frees time, so
        every rule still holds."""
        found = sorted(
            (
                c
                for c in self.classes.values()
                if c.kind == "individual"
                and c.series is not None
                and c.status == "confirmed"
                and c.key not in self.used
                and c.makeup_of is None
                and c.final == c.placed
                and c.placed.minutes == RESIZE_FROM
                and c.placed.day >= date(2026, 11, 23)
            ),
            key=lambda c: (c.placed.starts_at, c.key),
        )
        if not found:
            raise TimetableError("no class to shorten")
        c = found[0]
        self.used.add(c.key)
        self.update(c.key, final=replace(c.final, minutes=RESIZE_TO))
        self.events.append(Resize(RESIZE_AT, c.key, RESIZE_TO))
        return c.key

    # -- at the anchor --

    def future(self) -> list[ClassPlan]:
        found = [
            c
            for c in self.classes.values()
            if c.kind == "individual"
            and c.series is not None
            and c.status == "confirmed"
            and c.key not in self.used
            and date(2026, 11, 23) <= c.placed.day <= date(2026, 12, 4)
        ]
        found.sort(key=lambda c: c.key)
        self.rng.shuffle(found)
        return found

    def build_pending(self, leftovers: Sequence[ClassPlan]) -> ChangeWeekly:
        day1, day2 = PENDING_DAYS
        # Change requests: a family cancellation, a family reschedule and a
        # tutor cancellation.
        made = 0
        plans = [
            ("family", "cancellation"),
            ("family", "reschedule"),
            ("tutor", "cancellation"),
        ]
        busy_tutors: set[str] = set()
        for c in self.future():
            if made >= len(plans):
                break
            by, kind = plans[made]
            student = self.students[c.students[0]]
            if by == "family" and student.requester is None:
                continue
            created = local_instant(
                day1 if made < 2 else day2, ("18:00", "20:00", "11:00")[made]
            )
            proposed = None
            if kind == "reschedule":
                proposed = self.find_future_move(c, created)
                if proposed is None:
                    continue
            self.used.add(c.key)
            request = ChangeRequestPlan(
                key=self.next_key("cr"),
                cls=c.key,
                requester=student.requester
                if by == "family"
                else self.accounts[c.placed.tutor or ""],  # type: ignore[arg-type]
                by=by,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                created_at=created,
                proposed=proposed,
                decision="pending",
                exception=False,
                classification="on_time" if by == "family" else "excused",
            )
            self.change_requests.append(request)
            note = self.text(
                "reschedule_notes"
                if kind == "reschedule"
                else (
                    "family_cancellation_notes"
                    if by == "family"
                    else "tutor_cancellation_notes"
                )
            )
            self.events.append(RequestChange(created, request, note))
            made += 1
        if made < len(plans):
            raise TimetableError("too few pending change requests")

        # A pending time off over a future class.
        for c in self.future():
            if (c.placed.tutor or "") in busy_tutors:
                continue
            self.used.add(c.key)
            busy_tutors.add(c.placed.tutor or "")
            off = TimeOffPlan(
                key=self.next_key("to"),
                cls=c.key,
                tutor=c.placed.tutor or "",
                day=c.placed.day,
                start=c.placed.start,
                end=c.placed.end,
                note=self.text("time_off_notes"),
                decision="pending",
            )
            self.time_offs.append(off)
            self.events.append(RequestTimeOff(local_instant(day1, "16:00"), off))
            break
        else:
            raise TimetableError("no class for the pending time off")

        # A pending weekly change: a tutor drops a window with classes.
        weekly = None
        for c in self.future():
            tutor = c.placed.tutor or ""
            if tutor in busy_tutors:
                continue
            shape = self.cal.tutors[tutor]
            window = next(
                (
                    w
                    for w in shape.windows
                    if w[0] == c.placed.day.isoweekday()
                    and mins(w[1]) <= c.placed.lo
                    and c.placed.hi <= mins(w[2])
                ),
                None,
            )
            if window is None:
                continue
            busy_tutors.add(tutor)
            weekly = ChangeWeekly(local_instant(day2, "12:00"), tutor, *window)
            self.events.append(weekly)
            break
        if weekly is None:
            raise TimetableError("no tutor for the pending weekly change")

        # Session requests: two confirmed with pending offers, three pending.
        requesters = sorted(
            (s for s in self.students.values() if s.requester is not None),
            key=lambda s: s.key,
        )
        self.rng.shuffle(requesters)
        confirmed = 0
        pending = 0
        for student in requesters:
            if confirmed >= 2 and pending >= 3:
                break
            subject = self.rng.choice(_level_subjects(self.plan, student.level))
            days = [date(2026, 11, 23) + timedelta(days=k) for k in range(10)]
            self.rng.shuffle(days)
            where = self.find_one_off(student, subject, days)
            if where is None:
                continue
            confirm = confirmed < 2
            created = local_instant(day1, "09:00" if confirm else "19:30")
            if not confirm:
                created = local_instant(day1 if pending == 0 else day2, "19:30")
            key = self.next_key("sr")
            request = SessionRequestPlan(
                key=key,
                requester=student.requester or "",
                student=student.key,
                subject=subject,
                placement=replace(where, tutor=None, room=None),
                created_at=created,
                decision="confirmed" if confirm else "pending",
            )
            self.session_requests.append(request)
            self.events.append(
                RequestSession(created, request, self.text("session_request_notes"))
            )
            if confirm:
                cls_key = f"{key}:class"
                decided = local_instant(day2, "10:00")
                self.add_one_off(
                    cls_key, student.key, subject, where, "offered", decided
                )
                self.events.append(
                    DecideSessionRequest(decided, key, cls_key, where.tutor)
                )
                confirmed += 1
            else:
                pending += 1
        if confirmed < 2 or pending < 3:
            raise TimetableError("too few pending session requests")

        # Make-ups after the anchor: one offered, two unassigned.
        made = 0
        for c in sorted(leftovers, key=lambda c: c.placed.starts_at, reverse=True):
            if made >= 3:
                break
            if c.placed.starts_at + MAKEUP_WINDOW < local_instant(
                date(2026, 11, 26), "00:00"
            ):
                continue
            student = self.students[c.students[0]]
            last = (c.placed.starts_at + MAKEUP_WINDOW).astimezone(
                ZONE
            ).date() - timedelta(days=1)
            days = [
                date(2026, 11, 20) + timedelta(days=k)
                for k in range((last - date(2026, 11, 20)).days + 1)
            ]
            offered = made == 0
            where = self.find_one_off(
                student, c.subject, days, minutes=c.placed.minutes
            )
            if where is None:
                continue
            if not offered:
                where = replace(where, tutor=None)
            created = local_instant(day2 if offered else day1, "13:00")
            key = f"{c.key}:makeup"
            self.add_one_off(
                key,
                student.key,
                c.subject,
                where,
                "offered" if offered else "unassigned",
                created,
                c.key,
            )
            self.events.append(CreateMakeup(created, key))
            made += 1
        if made < 3:
            raise TimetableError("too few future make-ups")
        return weekly

    def find_future_move(self, c: ClassPlan, created: datetime) -> Placement | None:
        for offset in (1, 2, -1, 3):
            day = c.placed.day + timedelta(days=offset)
            if day.isoweekday() not in OPERATING_HOURS or day > SIM_HORIZON:
                continue
            earliest = (
                0
                if self.students[c.students[0]].adult or day.isoweekday() == 6
                else MINOR_EARLIEST
            )
            for start in self.starts(day.isoweekday(), c.placed.minutes, earliest):
                where = Placement(
                    day, hhmm(start), c.placed.minutes, c.placed.tutor, None
                )
                if where.starts_at < created + timedelta(days=2):
                    continue
                if self.cal.error(c.subject, c.students, where, ignore=c.key) is None:
                    return where
        return None

    # -- all --

    def build(self) -> Timetable:
        self.build_groups()
        self.build_courses()
        self.build_individual()
        self.build_joiner()
        past = [
            c
            for c in self.classes.values()
            if c.kind in ("individual", "group") and c.placed.day < SIM_ANCHOR
        ]
        total = len(past)
        cancelled = self.build_cancellations(round(total * self.volumes.cancel_rate))
        self.build_reschedules(round(total * self.volumes.reschedule_rate))
        self.build_substitutions(self.volumes.substitutions)
        self.build_rejected_changes(self.volumes.rejected_changes)
        self.build_session_requests()
        leftovers = self.build_makeups(cancelled)
        weekly = self.build_pending(leftovers)
        resized = self.build_resize()
        self.build_holds()
        events = sorted(enumerate(self.events), key=lambda item: (item[1].at, item[0]))
        return Timetable(
            series=tuple(self.series),
            groups=tuple(self.groups),
            courses=tuple(self.courses),
            cohorts=tuple(self.cohorts),
            classes=tuple(sorted(self.classes.values(), key=lambda c: c.key)),
            time_offs=tuple(self.time_offs),
            change_requests=tuple(self.change_requests),
            session_requests=tuple(self.session_requests),
            weekly_change=weekly,
            events=tuple(event for _, event in events),
            attendance_corrections=tuple(self.attendance_corrections),
            closeout_corrections=tuple(self.closeout_corrections),
            changes_requested=tuple(self.changes_requested),
            declined=tuple(self.declined),
            expired=tuple(self.expired),
            resized=resized,
            late_reviews=tuple(self.late_reviews),
        )


MAX_ATTEMPTS: Final = 20


def build_timetable(plan: SimulationPlan, seed: int) -> Timetable:
    """The timetable and its outcomes for ``plan`` (pure). The greedy
    builder can paint itself into a corner for some seeds; it then starts
    again with the next RNG stream (deterministic: the same seed always
    takes the same attempts)."""
    error: TimetableError | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _Builder(plan, seed, attempt).build()
        except TimetableError as exc:
            error = exc
    raise TimetableError(f"no timetable after {MAX_ATTEMPTS} attempts: {error}")


# --- The A2 check ---------------------------------------------------------------------


def consistency_errors(plan: SimulationPlan) -> list[str]:
    """Every rule broken by the final timetable, as ``"<class>: <rule>"``
    (empty when consistent). Rebuilds the calendar from scratch: every
    occupying class at its final placement, with the approved time off."""
    timetable = plan.timetable
    cal = Calendar(plan)
    for off in timetable.time_offs:
        if off.decision == "approved":
            cal.time_off[(off.tutor, off.day)].append((mins(off.start), mins(off.end)))
    errors: list[str] = []
    for c in sorted(timetable.classes, key=lambda c: (c.final.starts_at, c.key)):
        if not c.occupies:
            if c.status == "cancelled":
                found = cal.center_error(c.placed.day, c.placed.lo, c.placed.hi)
                if found is None and c.placed.tutor is not None:
                    shape = cal.tutors[c.placed.tutor]
                    if c.subject not in shape.subjects:
                        found = "tutor_not_qualified"
                if found is not None:
                    errors.append(f"{c.key}: {found}")
            continue
        found = cal.error(c.subject, c.students, c.final)
        if found is not None:
            errors.append(f"{c.key} {c.final}: {found}")
        cal.book(c.key, c.students, c.final)
    return errors


def week_starts() -> Iterator[date]:
    """The Mondays of the full weeks from 7 Sep to the horizon's week."""
    day = date(2026, 9, 7)
    while day <= SIM_HORIZON:
        yield day
        day += timedelta(days=7)
