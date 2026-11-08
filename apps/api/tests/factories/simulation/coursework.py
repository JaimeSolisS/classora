"""The simulation's academic record (#551 HF-01c): pure, built by
:func:`build_coursework` from the people plan and HF-01b's timetable, so the
same arguments give the same record on any day (no clock, no database). Its
own RNG stream (``Random(seed + RNG_OFFSET)``) leaves HF-01's people and
HF-01b's timetable unchanged.

Everything is written on held classes (``completed``, never a substituted
one) by the class's tutor, who still teaches the student at every write
(:class:`Teaching`: the services' 30-day "current assignment" rule):

- **Notes:** about 1 in 4 completed classes, about one student who attended;
  about 60 % shared with the family, the rest internal; written (and shared)
  at the class end + 20 minutes. The text joins a topic of the class's
  subject, a progress sentence and a next step from the pool.
- **Evaluations:** three templates (diagnostic, monthly ongoing, course
  assessment) by the Academic coordinator at setup; a diagnostic for every
  student enrolled before 1 Oct on their first attended class (within two
  weeks of their first class day); a monthly evaluation on each student's
  last attended class of September and of October; the finished cohort's
  two course assessments and the running cohort's mid-course one (13 Nov).
  Each is created and finalized at the class end + 30 minutes: all final.
- **Learning plans** for about 60 % of the students: 4 completed (and a
  follow-up active plan for each), 2 discontinued (a reason in the
  description), the rest active. Milestones go ``in_progress`` and then
  ``done``; at least 2 active plans keep a milestone due before the anchor
  ``in_progress`` (overdue).
- **Homework:** one assignment per student every other week, set on an
  attended class, due a week later. Due before the anchor: graded on the
  due day (handed in by the student's login the day before, or recorded by
  the tutor) or missing (10 %). Due from the anchor on: pending, or
  submitted early on 17-18 Nov (at least 3).
- **Progress reports:** September and October monthly reports for every
  student with at least 4 completed classes in the month, by their main
  tutor that month, approved by the Academic coordinator (10 % sent back
  first); 2 on-demand reports; 2 weekly and 1 monthly report schedules
  created by the coordinator on 4 Sep, whose placeholders the in-process
  job step creates every Monday / 1st at 01:00 and the tutor fills and
  submits; the weekly reports for 9-15 Nov stay ``submitted`` (17 and 18
  Nov). No report covers a period ending after the anchor.

The result is a list of dated :class:`Step` values the ``academics``
module executes in order.
"""

import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Final, Literal

from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_SETUP_DAY,
    SIM_START,
    ZONE,
    local_instant,
)
from tests.factories.simulation.plan import SimulationPlan
from tests.factories.simulation.texts import TextPool
from tests.factories.simulation.timetable import ClassPlan, Timetable, monday

RNG_OFFSET: Final = 551_000
"""The academic record's RNG stream: ``Random(seed + RNG_OFFSET)``."""

NOTE_AFTER: Final = timedelta(minutes=20)
EVALUATION_AFTER: Final = timedelta(minutes=30)
HOMEWORK_AFTER: Final = timedelta(minutes=25)
NOTE_RATE: Final = 0.25
SHARED_RATE: Final = 0.6
PLAN_RATE: Final = 0.6
MISSING_RATE: Final = 0.10
CHANGES_RATE: Final = 0.10
COMPLETED_PLANS: Final = 4
DISCONTINUED_PLANS: Final = 2
OVERDUE_PLANS: Final = 2
EARLY_SUBMISSIONS: Final = 3
MONTHLY_MIN_CLASSES: Final = 4
ACTIVE_REACH: Final = timedelta(days=7)
"""An active plan's target is at least this long after the anchor."""
TUTOR_LOOKBACK: Final = timedelta(days=30)
"""``scheduling.tutor_assignments.TUTOR_ASSIGNMENT_LOOKBACK_DAYS``."""
EARLY_ENROLLMENT: Final = date(2026, 10, 1)
MONTHS: Final = (date(2026, 9, 1), date(2026, 10, 1))
MID_COURSE_DAY: Final = date(2026, 11, 13)
SCHEDULES_CREATED: Final = date(2026, 9, 4)
WEEKLY_FROM: Final = date(2026, 9, 7)
ON_DEMAND_PERIOD: Final = (date(2026, 9, 1), date(2026, 10, 15))
ON_DEMAND_WRITTEN: Final = date(2026, 10, 16)
ON_DEMAND_APPROVED: Final = date(2026, 10, 19)
PENDING_SUBMIT_DAYS: Final = (date(2026, 11, 17), date(2026, 11, 18))
TEMPLATE_KEYS: Final = ("diagnostic", "monthly", "course")
MAX_SCORE: Final = 10
HELD: Final = frozenset({"present", "late"})
CURRENT_STATES: Final = frozenset({"offered", "confirmed", "completed"})

type Outcome = Literal["active", "completed", "discontinued"]
type HomeworkOutcome = Literal["graded", "missing", "pending", "submitted"]
type MilestoneStatus = Literal["pending", "in_progress", "done"]
type Action = Literal[
    "template",
    "note",
    "evaluate",
    "plan_create",
    "milestone",
    "plan_close",
    "homework",
    "submit",
    "grade",
    "schedule",
    "placeholders",
    "report_write",
    "report_changes",
    "report_resubmit",
    "report_approve",
]


# --- The plan's values ----------------------------------------------------------------


@dataclass(frozen=True)
class TemplatePlan:
    key: str
    name: str
    kind: Literal["diagnostic", "ongoing"]
    description: str
    criteria: tuple[str, ...]


@dataclass(frozen=True)
class NotePlan:
    key: str
    cls: str
    student: str
    tutor: str
    subject: str
    body: str
    shared: bool
    at: datetime


@dataclass(frozen=True)
class EvaluationPlan:
    key: str
    template: str
    student: str
    tutor: str
    subject: str
    cls: str
    scores: tuple[int, ...]
    comments: tuple[str | None, ...]
    feedback: str
    at: datetime
    assessment: tuple[str, int] | None = None
    """``(cohort key, assessment index)`` of a course assessment."""


@dataclass(frozen=True)
class MilestonePlan:
    title: str
    activities: str
    due_on: date
    steps: tuple[tuple[datetime, MilestoneStatus], ...]

    @property
    def status(self) -> MilestoneStatus:
        return self.steps[-1][1] if self.steps else "pending"


@dataclass(frozen=True)
class ObjectivePlan:
    title: str
    milestones: tuple[MilestonePlan, ...]


@dataclass(frozen=True)
class LearningPlanPlan:
    key: str
    student: str
    subject: str
    tutor: str
    title: str
    description: str
    starts_on: date
    target_on: date
    objectives: tuple[ObjectivePlan, ...]
    created_at: datetime
    outcome: Outcome
    closed_at: datetime | None = None
    reason: str | None = None
    """A discontinued plan's reason (added to its description)."""

    @property
    def milestones(self) -> tuple[MilestonePlan, ...]:
        return tuple(m for o in self.objectives for m in o.milestones)


@dataclass(frozen=True)
class HomeworkPlan:
    key: str
    cls: str
    student: str
    tutor: str
    subject: str
    title: str
    instructions: str
    created_at: datetime
    due_on: date
    outcome: HomeworkOutcome
    submitted_at: datetime | None = None
    by_student: bool = False
    answer: str | None = None
    graded_at: datetime | None = None
    score: int | None = None
    feedback: str | None = None


@dataclass(frozen=True)
class SchedulePlan:
    key: str
    student: str
    tutor: str
    cadence: Literal["weekly", "monthly"]
    starts_on: date
    created_at: datetime


@dataclass(frozen=True)
class ReportPlan:
    key: str
    student: str
    tutor: str
    kind: Literal["weekly", "monthly", "on_demand"]
    period_start: date
    period_end: date
    narrative: dict[str, str]
    written_at: datetime
    """Created (or the placeholder filled) and submitted."""
    schedule: str | None = None
    changes_at: datetime | None = None
    changes_reason: str | None = None
    resubmitted_at: datetime | None = None
    approved_at: datetime | None = None

    @property
    def status(self) -> str:
        return "approved_for_family" if self.approved_at else "submitted"


@dataclass(frozen=True)
class Step:
    """One dated write of the ``academics`` module."""

    at: datetime
    action: Action
    key: str
    milestone: tuple[int, int] | None = None
    status: MilestoneStatus | None = None


@dataclass(frozen=True)
class Coursework:
    templates: tuple[TemplatePlan, ...]
    notes: tuple[NotePlan, ...]
    evaluations: tuple[EvaluationPlan, ...]
    plans: tuple[LearningPlanPlan, ...]
    homework: tuple[HomeworkPlan, ...]
    schedules: tuple[SchedulePlan, ...]
    reports: tuple[ReportPlan, ...]
    ticks: tuple[datetime, ...]
    """The in-process placeholder job's runs (01:00 local on Mondays and
    the 1st of a month, from the schedules' creation)."""
    steps: tuple[Step, ...] = field(default=())
    """Sorted by instant (stable)."""

    def plan(self, key: str) -> LearningPlanPlan:
        return next(p for p in self.plans if p.key == key)


@dataclass(frozen=True)
class _Draft:
    """A homework assignment before its outcome is drawn."""

    created: datetime
    student: str
    cls: ClassPlan
    due: date


class CourseworkError(RuntimeError):
    """The timetable can't support a planned item: the plan has a bug."""


# --- Facts from the timetable ---------------------------------------------------------


def local_day(at: datetime) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


def attended(c: ClassPlan) -> tuple[str, ...]:
    return tuple(s for s, mark in c.attendance if mark in HELD)


def held_classes(timetable: Timetable) -> list[ClassPlan]:
    """Completed classes by their own tutor (no substitute), in time order."""
    return sorted(
        (
            c
            for c in timetable.classes
            if c.status == "completed" and not c.substituted and c.final.tutor
        ),
        key=lambda c: (c.final.starts_at, c.key),
    )


class Teaching:
    """The services' "current assignment" rule over the timetable: a tutor
    teaches a student (in a subject) at an instant when one of the tutor's
    held or scheduled classes with the student ended less than 30 days
    before or hasn't ended. Conservative: final placements, never a
    substituted, cancelled or skipped class."""

    def __init__(self, timetable: Timetable) -> None:
        self.ends: dict[tuple[str, str], list[tuple[datetime, str]]] = defaultdict(list)
        for c in timetable.classes:
            if c.status not in CURRENT_STATES or c.substituted or not c.final.tutor:
                continue
            for student in c.students:
                self.ends[(c.final.tutor, student)].append((c.final.ends_at, c.subject))

    def teaches(
        self, tutor: str, student: str, at: datetime, subject: str | None = None
    ) -> bool:
        since = at - TUTOR_LOOKBACK
        return any(
            end > since and (subject is None or found == subject)
            for end, found in self.ends[(tutor, student)]
        )


# --- The builder ----------------------------------------------------------------------


class _Builder:
    def __init__(self, plan: SimulationPlan, seed: int) -> None:
        self.plan = plan
        # Deterministic synthetic data, not security: a seeded generator.
        self.rng = random.Random(seed + RNG_OFFSET)  # noqa: S311
        self.texts = TextPool(plan.language)
        self.timetable = plan.timetable
        self.held = held_classes(self.timetable)
        self.teaching = Teaching(self.timetable)
        self.students = {s.key: s for s in plan.students}
        self.enrolled = {
            s.key: family.enrolls_at
            for family in plan.families
            for s in family.students
        }
        self.by_student: dict[str, list[ClassPlan]] = defaultdict(list)
        for c in self.held:
            for student in c.students:
                self.by_student[student].append(c)
        self.completed: dict[str, list[date]] = defaultdict(list)
        for c in self.timetable.classes:
            if c.status == "completed":
                for student in c.students:
                    self.completed[student].append(c.final.day)
        self.subject_names = {s.key: s.name for s in plan.subjects}

    # Small helpers -------------------------------------------------------------------

    def pick(self, key: str) -> str:
        return self.rng.choice(self.texts.choices(key))

    def require(
        self, tutor: str, student: str, at: datetime, subject: str | None
    ) -> None:
        if not self.teaching.teaches(tutor, student, at, subject):
            raise CourseworkError(f"{tutor} doesn't teach {student} at {at}")

    def attended_by(self, student: str) -> list[ClassPlan]:
        return [c for c in self.by_student[student] if student in attended(c)]

    # Templates -----------------------------------------------------------------------

    def templates(self) -> tuple[TemplatePlan, ...]:
        names = self.texts.mapping("evaluation_templates")
        descriptions = self.texts.mapping("evaluation_template_descriptions")
        criteria = self.texts.lists("evaluation_criteria")
        return tuple(
            TemplatePlan(
                key,
                names[key],
                "diagnostic" if key == "diagnostic" else "ongoing",
                descriptions[key],
                tuple(criteria[key]),
            )
            for key in TEMPLATE_KEYS
        )

    # Notes ---------------------------------------------------------------------------

    def notes(self) -> tuple[NotePlan, ...]:
        found: list[NotePlan] = []
        topics = self.texts.lists("closeout_topics")
        for c in self.held:
            present = attended(c)
            if not present or self.rng.random() >= NOTE_RATE:
                continue
            student = self.rng.choice(present)
            assert c.final.tutor is not None
            body = " ".join(
                (
                    f"{self.rng.choice(topics[c.subject])}:",
                    self.pick("note_progress"),
                    self.pick("note_next_steps"),
                )
            )
            at = c.final.ends_at + NOTE_AFTER
            self.require(c.final.tutor, student, at, c.subject)
            found.append(
                NotePlan(
                    f"n{len(found) + 1:04d}",
                    c.key,
                    student,
                    c.final.tutor,
                    c.subject,
                    body,
                    self.rng.random() < SHARED_RATE,
                    at,
                )
            )
        return tuple(found)

    # Evaluations ---------------------------------------------------------------------

    def _evaluation(
        self,
        template: str,
        c: ClassPlan,
        student: str,
        index: int,
        assessment: tuple[str, int] | None = None,
    ) -> EvaluationPlan:
        assert c.final.tutor is not None
        count = len(self.texts.lists("evaluation_criteria")[template])
        scores = tuple(self.rng.randint(5, MAX_SCORE) for _ in range(count))
        comments = tuple(
            self.pick("evaluation_comments") if self.rng.random() < 0.4 else None
            for _ in range(count)
        )
        at = c.final.ends_at + EVALUATION_AFTER
        self.require(c.final.tutor, student, at, c.subject)
        return EvaluationPlan(
            f"e{index:04d}",
            template,
            student,
            c.final.tutor,
            c.subject,
            c.key,
            scores,
            comments,
            self.pick("evaluation_feedback"),
            at,
            assessment,
        )

    def evaluations(self) -> tuple[EvaluationPlan, ...]:
        found: list[EvaluationPlan] = []

        def add(
            template: str,
            c: ClassPlan,
            student: str,
            assessment: tuple[str, int] | None = None,
        ) -> None:
            found.append(
                self._evaluation(template, c, student, len(found) + 1, assessment)
            )

        for student in sorted(self.students):
            classes = self.attended_by(student)
            enrolled = local_day(self.enrolled[student])
            if enrolled < EARLY_ENROLLMENT and classes:
                # Their first attended class: within two weeks of their first
                # class day for seed 1 (the unit test); a later one when
                # absences and cancellations leave no earlier class.
                add("diagnostic", classes[0], student)
            for month in MONTHS:
                in_month = [
                    c
                    for c in classes
                    if (c.final.day.year, c.final.day.month)
                    == (month.year, month.month)
                ]
                if in_month:
                    add("monthly", in_month[-1], student)
        for cohort in self.timetable.cohorts:
            series = cohort.series.key
            classes = [c for c in self.held if c.series == series]
            if not classes:
                continue
            if classes[-1].final.day < SIM_ANCHOR - timedelta(days=14):
                # The finished cohort: one assessment midway, one at the end.
                chosen = [(classes[len(classes) // 2], 0), (classes[-1], 1)]
            else:
                before = [c for c in classes if c.final.day <= MID_COURSE_DAY]
                chosen = [(before[-1], 0)]
            for c, index in chosen:
                for student in attended(c):
                    add("course", c, student, (cohort.key, index))
        return tuple(found)

    # Learning plans ------------------------------------------------------------------

    def _plan_class(self, student: str, after: date) -> ClassPlan:
        """The student's first attended individual class from ``after`` whose
        tutor teaches them in that subject through the horizon."""
        for c in self.attended_by(student):
            if c.kind != "individual" or c.final.day < after:
                continue
            assert c.final.tutor is not None
            late = local_instant(SIM_ANCHOR + timedelta(days=7), "12:00")
            if self.teaching.teaches(c.final.tutor, student, late, c.subject):
                return c
        raise CourseworkError(f"{student}: no individual class for a plan")

    def _learning_plan(
        self,
        key: str,
        student: str,
        outcome: Outcome,
        after: date,
        overdue: bool = False,
    ) -> LearningPlanPlan:
        c = self._plan_class(student, after)
        assert c.final.tutor is not None
        tutor, subject = c.final.tutor, c.subject
        start = c.final.day
        created = c.final.ends_at + timedelta(hours=1)
        span = 9 if outcome == "completed" else 14
        if outcome == "active":
            # An active plan spans the anchor (QA #551 D1): its last
            # milestone, and so its target, falls a week or more after it.
            reach = (SIM_ANCHOR + ACTIVE_REACH - start).days
            span = max(span, -(-reach // 4))
        objectives_pool = self.rng.sample(self.texts.choices("plan_objectives"), 2)
        milestone_pool = self.rng.sample(self.texts.choices("plan_milestones"), 4)
        dues = [start + timedelta(days=span * (n + 1)) for n in range(4)]
        last_before = max(
            (n for n, due in enumerate(dues) if due < SIM_ANCHOR), default=None
        )
        closed: datetime | None = None
        reason: str | None = None
        if outcome == "completed":
            closed = local_instant(dues[-1] + timedelta(days=1), "18:30")
        elif outcome == "discontinued":
            closed = local_instant(dues[1] + timedelta(days=3), "18:30")
            reason = self.pick("plan_discontinue_reasons")
        milestones: list[MilestonePlan] = []
        for n, due in enumerate(dues):
            opened = created if n == 0 else local_instant(dues[n - 1], "18:10")
            steps: list[tuple[datetime, MilestoneStatus]] = []
            if closed is not None and opened >= closed:
                pass  # never started before the plan closed
            elif opened < local_instant(SIM_ANCHOR, "00:00"):
                steps.append((opened + timedelta(minutes=5), "in_progress"))
                done_at = local_instant(due, "18:00")
                keep_open = overdue and n == last_before
                if (
                    due < SIM_ANCHOR
                    and not keep_open
                    and (closed is None or done_at < closed)
                ):
                    steps.append((done_at, "done"))
            milestones.append(
                MilestonePlan(
                    milestone_pool[n],
                    self.pick("plan_activities"),
                    due,
                    tuple(steps),
                )
            )
        for at, _ in (s for m in milestones for s in m.steps):
            self.require(tutor, student, at, subject)
        if closed is not None:
            self.require(tutor, student, closed, subject)
        title = self.pick("plan_titles").format(subject=self.subject_names[subject])
        return LearningPlanPlan(
            key,
            student,
            subject,
            tutor,
            title,
            self.pick("plan_descriptions"),
            start,
            dues[-1],
            (
                ObjectivePlan(objectives_pool[0], tuple(milestones[:2])),
                ObjectivePlan(objectives_pool[1], tuple(milestones[2:])),
            ),
            created,
            outcome,
            closed,
            reason,
        )

    def plans(self) -> tuple[LearningPlanPlan, ...]:
        eligible = sorted(
            s
            for s in self.students
            if local_day(self.enrolled[s]) < EARLY_ENROLLMENT
            and any(c.kind == "individual" for c in self.attended_by(s))
        )
        count = max(
            COMPLETED_PLANS + DISCONTINUED_PLANS,
            round(PLAN_RATE * len(self.students)),
        )
        chosen = self.rng.sample(eligible, count)
        # A student without a suitable individual class (some seeds) is
        # skipped for the next eligible one, so every seed builds.
        candidates = chosen + [s for s in eligible if s not in chosen]
        found: list[LearningPlanPlan] = []
        n = 0
        for student in candidates:
            if n == count:
                break
            try:
                found += self._plans_for(student, n)
            except CourseworkError:
                continue
            n += 1
        if n < count:  # pragma: no cover - the profiles have enough students
            raise CourseworkError(f"only {n} students for {count} learning plans")
        return tuple(found)

    def _plans_for(self, student: str, n: int) -> list[LearningPlanPlan]:
        """The ``n``-th chosen student's plans: completed (and its active
        follow-up), discontinued, or active."""
        key = f"p{n + 1:02d}"
        if n < COMPLETED_PLANS:
            done = self._learning_plan(key, student, "completed", WEEKLY_FROM)
            assert done.closed_at is not None
            follow = self._learning_plan(
                f"{key}b",
                student,
                "active",
                local_day(done.closed_at) + timedelta(days=1),
                overdue=n <= OVERDUE_PLANS,
            )
            return [done, follow]
        if n < COMPLETED_PLANS + DISCONTINUED_PLANS:
            return [self._learning_plan(key, student, "discontinued", WEEKLY_FROM)]
        after = WEEKLY_FROM + timedelta(days=7 * (n % 6))
        return [self._learning_plan(key, student, "active", after)]

    # Homework ------------------------------------------------------------------------

    def homework(self) -> tuple[HomeworkPlan, ...]:
        topics = self.texts.lists("closeout_topics")
        drafts: list[_Draft] = []
        for index, student in enumerate(sorted(self.students)):
            weeks: dict[date, ClassPlan] = {}
            for c in self.attended_by(student):
                weeks.setdefault(monday(c.final.day), c)
            for week, c in sorted(weeks.items()):
                if ((week - monday(SIM_START)).days // 7) % 2 != index % 2:
                    continue
                drafts.append(
                    _Draft(
                        c.final.ends_at + HOMEWORK_AFTER,
                        student,
                        c,
                        c.final.day + timedelta(days=7),
                    )
                )
        past = [d for d in drafts if d.due < SIM_ANCHOR]
        future = [d for d in drafts if d.due >= SIM_ANCHOR]
        missing = {
            id(d) for d in self.rng.sample(past, round(MISSING_RATE * len(past)))
        }
        early = {
            id(d)
            for d in self.rng.sample(future, min(len(future), EARLY_SUBMISSIONS + 1))
        }
        found: list[HomeworkPlan] = []
        for d in sorted(drafts, key=lambda d: (d.created, d.student)):
            c, student, created, due = d.cls, d.student, d.created, d.due
            assert c.final.tutor is not None
            tutor = c.final.tutor
            account = self.students[student].account
            title = self.pick("homework_titles").format(
                topic=self.rng.choice(topics[c.subject])
            )
            base = HomeworkPlan(
                key=f"h{len(found) + 1:04d}",
                cls=c.key,
                student=student,
                tutor=tutor,
                subject=c.subject,
                title=title,
                instructions=self.pick("homework_instructions"),
                created_at=created,
                due_on=due,
                outcome="pending",
            )
            self.require(tutor, student, created, c.subject)
            if due < SIM_ANCHOR:
                if id(d) in missing:
                    found.append(replace(base, outcome="missing"))
                    continue
                by_student = account is not None and local_instant(
                    due - timedelta(days=1), "18:00"
                ) > account.created_at + timedelta(days=1)
                submitted = (
                    local_instant(due - timedelta(days=1), "18:00")
                    if by_student
                    else local_instant(due, "18:00")
                )
                graded = local_instant(due, "20:00")
                self.require(tutor, student, graded, c.subject)
                found.append(
                    replace(
                        base,
                        outcome="graded",
                        submitted_at=submitted,
                        by_student=by_student,
                        answer=self.pick("homework_answers") if by_student else None,
                        graded_at=graded,
                        score=self.rng.randint(6, MAX_SCORE),
                        feedback=self.pick("homework_feedback"),
                    )
                )
            elif id(d) in early:
                day = max(
                    local_day(created) + timedelta(days=1), PENDING_SUBMIT_DAYS[0]
                )
                day = min(day, PENDING_SUBMIT_DAYS[1])
                # Handed in that evening, or an hour after a late class.
                submitted = max(
                    local_instant(day, "19:00"), created + timedelta(hours=1)
                )
                if local_day(submitted) >= SIM_ANCHOR:  # pragma: no cover
                    raise CourseworkError(f"{base.key}: submitted after the anchor")
                by_student = account is not None and submitted > (
                    account.created_at + timedelta(days=1)
                )
                found.append(
                    replace(
                        base,
                        outcome="submitted",
                        submitted_at=submitted,
                        by_student=by_student,
                        answer=self.pick("homework_answers") if by_student else None,
                    )
                )
            else:
                found.append(base)
        return tuple(found)

    # Progress reports ----------------------------------------------------------------

    def _narrative(self) -> dict[str, str]:
        return {
            "summary": self.pick("report_summaries"),
            "strengths": self.pick("report_strengths"),
            "areas_for_improvement": self.pick("report_improvements"),
            "next_steps": self.pick("report_next_steps"),
        }

    def _main_tutor(
        self, student: str, start: date, end: date
    ) -> tuple[str, int] | None:
        counts = Counter(
            c.final.tutor
            for c in self.by_student[student]
            if start <= c.final.day <= end and c.final.tutor is not None
        )
        if not counts:
            return None
        tutor, _ = min(counts.items(), key=lambda item: (-item[1], item[0]))
        return tutor, sum(start <= day <= end for day in self.completed[student])

    def _individual_series(self) -> list[tuple[str, str]]:
        """``(student, tutor)`` of the individual series created at setup
        that run through the horizon, by student."""
        found = []
        for s in self.timetable.series:
            if (
                s.kind == "individual"
                and local_day(s.created_at) <= SIM_SETUP_DAY
                and s.until >= SIM_ANCHOR + timedelta(days=7)
            ):
                found.append((s.students[0], s.tutor))
        return sorted(set(found))

    def schedules(self) -> tuple[SchedulePlan, ...]:
        candidates = self._individual_series()
        monthly = next(
            (
                (student, tutor)
                for student, tutor in candidates
                if all(
                    (main := self._main_tutor(student, m, _month_end(m))) is not None
                    and main[0] == tutor
                    and main[1] >= MONTHLY_MIN_CLASSES
                    for m in MONTHS
                )
            ),
            None,
        )
        if monthly is None:
            # No student's main tutor is their individual tutor in both
            # months: the schedule's author writes that student's reports.
            monthly = next(
                (student, tutor)
                for student, tutor in candidates
                if all(
                    (main := self._main_tutor(student, m, _month_end(m))) is not None
                    and main[1] >= MONTHLY_MIN_CLASSES
                    for m in MONTHS
                )
            )
        weekly = [c for c in candidates if c[0] != monthly[0]]
        weekly = [weekly[0], next(c for c in weekly if c[0] != weekly[0][0])]
        created = local_instant(SCHEDULES_CREATED, "10:00")
        found = [
            SchedulePlan(
                f"w{n + 1}",
                student,
                tutor,
                "weekly",
                WEEKLY_FROM,
                created + timedelta(minutes=5 * n),
            )
            for n, (student, tutor) in enumerate(weekly)
        ]
        found.append(
            SchedulePlan(
                "m1",
                monthly[0],
                monthly[1],
                "monthly",
                MONTHS[0],
                created + timedelta(minutes=10),
            )
        )
        for s in found:
            self.require(s.tutor, s.student, s.created_at, None)
        return tuple(found)

    def reports(
        self, schedules: Sequence[SchedulePlan]
    ) -> tuple[tuple[ReportPlan, ...], tuple[datetime, ...]]:
        found: list[ReportPlan] = []
        reviewed: list[int] = []
        # Weekly placeholders: Mondays from the second week.
        ticks: set[datetime] = set()
        last_week = monday(SIM_ANCHOR) - timedelta(days=7)
        for n, schedule in enumerate(s for s in schedules if s.cadence == "weekly"):
            week = schedule.starts_on
            while week <= last_week:
                tick = local_instant(week + timedelta(days=7), "01:00")
                ticks.add(tick)
                if week == last_week:
                    written = local_instant(PENDING_SUBMIT_DAYS[n % 2], "18:00")
                    approved = None
                else:
                    written = local_instant(week + timedelta(days=8), "18:00")
                    approved = local_instant(week + timedelta(days=9), "10:00")
                found.append(
                    ReportPlan(
                        f"r{len(found) + 1:03d}",
                        schedule.student,
                        schedule.tutor,
                        "weekly",
                        week,
                        week + timedelta(days=6),
                        self._narrative(),
                        written,
                        schedule=schedule.key,
                        approved_at=approved,
                    )
                )
                week += timedelta(days=7)
        monthly = next(s for s in schedules if s.cadence == "monthly")
        for month in MONTHS:
            end = _month_end(month)
            next_month = end + timedelta(days=1)
            ticks.add(local_instant(next_month, "01:00"))
            first_day = 1 if month.month == 9 else 3
            for index, student in enumerate(sorted(self.students)):
                main = self._main_tutor(student, month, end)
                if main is None or main[1] < MONTHLY_MIN_CLASSES:
                    continue
                scheduled = student == monthly.student
                tutor = monthly.tutor if scheduled else main[0]
                day = next_month.replace(day=first_day + index % 3)
                written = local_instant(day, "18:00")
                reviewed.append(len(found))
                found.append(
                    ReportPlan(
                        f"r{len(found) + 1:03d}",
                        student,
                        tutor,
                        "monthly",
                        month,
                        end,
                        self._narrative(),
                        written,
                        schedule=monthly.key if scheduled else None,
                    )
                )
        start, end = ON_DEMAND_PERIOD
        on_demand = 0
        for student in sorted(self.students):
            main = self._main_tutor(student, start, end)
            if main is None or main[1] < MONTHLY_MIN_CLASSES:
                continue
            found.append(
                ReportPlan(
                    f"r{len(found) + 1:03d}",
                    student,
                    main[0],
                    "on_demand",
                    start,
                    end,
                    self._narrative(),
                    local_instant(ON_DEMAND_WRITTEN, "18:00"),
                    approved_at=local_instant(ON_DEMAND_APPROVED, "10:00"),
                )
            )
            on_demand += 1
            if on_demand == 2:
                break
        # Monthly reviews: 10 % sent back first.
        sent_back = set(self.rng.sample(reviewed, round(CHANGES_RATE * len(reviewed))))
        for index in reviewed:
            r = found[index]
            day = local_day(r.written_at)
            if index in sent_back:
                found[index] = replace(
                    r,
                    changes_at=local_instant(day + timedelta(days=1), "10:00"),
                    changes_reason=self.pick("report_change_reasons"),
                    resubmitted_at=local_instant(day + timedelta(days=2), "18:00"),
                    approved_at=local_instant(day + timedelta(days=3), "10:00"),
                )
            else:
                found[index] = replace(
                    r, approved_at=local_instant(day + timedelta(days=1), "10:00")
                )
        for r in found:
            for at in (r.written_at, r.changes_at, r.resubmitted_at):
                if at is not None:
                    self.require(r.tutor, r.student, at, None)
        return tuple(found), tuple(sorted(ticks))

    # Steps ---------------------------------------------------------------------------

    def build(self) -> Coursework:
        schedules = self.schedules()
        reports, ticks = self.reports(schedules)
        work = Coursework(
            templates=self.templates(),
            notes=self.notes(),
            evaluations=self.evaluations(),
            plans=self.plans(),
            homework=self.homework(),
            schedules=schedules,
            reports=reports,
            ticks=ticks,
        )
        return replace(work, steps=tuple(_steps(work)))


def _month_end(start: date) -> date:
    following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


TEMPLATES_AT: Final = local_instant(SIM_SETUP_DAY, "12:00")
"""The coordinator creates the evaluation templates at setup."""


def _steps(work: Coursework) -> Iterable[Step]:
    found: list[Step] = [Step(TEMPLATES_AT, "template", t.key) for t in work.templates]
    found += [Step(n.at, "note", n.key) for n in work.notes]
    found += [Step(e.at, "evaluate", e.key) for e in work.evaluations]
    for p in work.plans:
        found.append(Step(p.created_at, "plan_create", p.key))
        for o, objective in enumerate(work.plan(p.key).objectives):
            for m, milestone in enumerate(objective.milestones):
                found += [
                    Step(at, "milestone", p.key, (o, m), status)
                    for at, status in milestone.steps
                ]
        if p.closed_at is not None:
            found.append(Step(p.closed_at, "plan_close", p.key))
    for h in work.homework:
        found.append(Step(h.created_at, "homework", h.key))
        if h.submitted_at is not None:
            found.append(Step(h.submitted_at, "submit", h.key))
        if h.graded_at is not None:
            found.append(Step(h.graded_at, "grade", h.key))
    found += [Step(s.created_at, "schedule", s.key) for s in work.schedules]
    found += [Step(at, "placeholders", "") for at in work.ticks]
    for r in work.reports:
        found.append(Step(r.written_at, "report_write", r.key))
        if r.changes_at is not None:
            found.append(Step(r.changes_at, "report_changes", r.key))
        if r.resubmitted_at is not None:
            found.append(Step(r.resubmitted_at, "report_resubmit", r.key))
        if r.approved_at is not None:
            found.append(Step(r.approved_at, "report_approve", r.key))
    return sorted(found, key=lambda s: s.at)


def build_coursework(plan: SimulationPlan, seed: int) -> Coursework:
    """The academic record for ``plan`` (pure; see the module docstring)."""
    return _Builder(plan, seed).build()
