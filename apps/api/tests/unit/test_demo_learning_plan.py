"""The #354 demo plan (#354 G2, E1): a pure function of the center's today
on #299's ``P(k)`` and ``D(k)``, checked for the 18 days of #299 E1 (#353 F1)
together with #353's held classes. The service rules are constants here, so
the test does not depend on the generators' own values."""

from datetime import date, datetime, time, timedelta

import pytest

from tests.factories.demo_academics import DemoAcademicsPlan, plan_demo_academics
from tests.factories.demo_learning import DemoLearningPlan, plan_demo_learning
from tests.factories.demo_schedule import ENGLISH_SECONDARY, MATH_SECONDARY
from tests.unit.test_demo_schedule_plan import TODAYS

ASSIGNMENT_LOOKBACK_DAYS = 30
"""#67: a tutor teaches the students of their classes that ended in the last
30 days (or haven't ended)."""
DUE_MAX_DAYS = 365
"""#82 C2: a deadline at most 365 days after the center's today."""
ON_DEMAND_MAX_DAYS = 366
"""#83: an on-demand report covers at most 366 days."""


@pytest.fixture(params=TODAYS, ids=lambda d: f"{d:%a-%Y-%m-%d}")
def plans(request: pytest.FixtureRequest) -> tuple[DemoLearningPlan, DemoAcademicsPlan]:
    return plan_demo_learning(request.param), plan_demo_academics(request.param)


def _at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm))


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min)


def test_every_seed_instant_is_a_past_weekday_in_order(plans):
    plan, _ = plans
    for steps in (plan.plan_steps, plan.homework_steps):
        instants = [step.local for step in steps]
        assert instants == sorted(instants)
        assert len(set(instants)) == len(instants)
        for step in steps:
            assert step.day.isoweekday() <= 5, step
            assert step.local < _midnight(plan.today), step


def test_the_tutor_teaches_s_the_subject_at_every_seed_instant(plans):
    # H1 (Matemáticas) and H3 (Inglés) count from their creation until 30
    # days after they end; every seed instant is within that window.
    plan, academics = plans
    held = {h.key: h for h in academics.held}
    classes = {MATH_SECONDARY: held["H1"], ENGLISH_SECONDARY: held["H3"]}
    instants = [
        (step.local, plan.plan(step.plan).subject) for step in plan.plan_steps
    ] + [
        (step.local, plan.assignment(step.homework).subject)
        for step in plan.homework_steps
    ]
    for instant, subject in instants:
        taught = classes[subject]
        ends = _at(taught.day, taught.end)
        assert instant - timedelta(days=ASSIGNMENT_LOOKBACK_DAYS) < ends, instant
        assert academics.created_on < instant.date()


def test_the_homework_follows_its_classes_and_deadlines(plans):
    plan, academics = plans
    held = {h.key: h for h in academics.held}
    steps = {(s.homework, s.action): s for s in plan.homework_steps}
    for key in ("HW1", "HW2"):
        homework = plan.assignment(key)
        assert homework.held is not None
        created = steps[(key, "create")]
        submitted = steps[(key, "submit")]
        # Created after the linked class ended; the deadline from the
        # creation day to 365 days later; handed in before the deadline.
        linked = held[homework.held]
        assert _at(linked.day, linked.end) < created.local
        assert created.day <= homework.due_on
        assert homework.due_on <= created.day + timedelta(days=DUE_MAX_DAYS)
        assert created.local < submitted.local
        assert submitted.day <= homework.due_on
    graded = steps[("HW1", "grade")]
    assert steps[("HW1", "submit")].local < graded.local
    assert ("HW2", "grade") not in steps
    # HW3: on the real clock, due from T + 2 on.
    hw3 = plan.assignment("HW3")
    assert hw3.held is None
    assert plan.today < hw3.due_on <= plan.today + timedelta(days=DUE_MAX_DAYS)
    assert not any(s.homework == "HW3" for s in plan.homework_steps)


def test_only_inglés_homework_is_graded(plans):
    # E1: offers.spec step 9d's Matemáticas average is the spec's own grade.
    plan, _ = plans
    graded = [h for h in plan.homework if h.score is not None]
    assert [(h.key, h.subject) for h in graded] == [("HW1", ENGLISH_SECONDARY)]
    assert all(
        plan.assignment(s.homework).subject == ENGLISH_SECONDARY
        for s in plan.homework_steps
        if s.action == "grade"
    )


def test_lp1_has_exactly_one_overdue_milestone_and_lp2_none(plans):
    plan, _ = plans
    lp1, lp2 = plan.plan("LP1"), plan.plan("LP2")
    milestones = [m for o in lp1.objectives for m in o.milestones]
    overdue = [
        m.key for m in milestones if m.due_on < plan.today and m.status != "done"
    ]
    assert overdue == ["M2"]
    assert {m.key: m.status for m in milestones} == {
        "M1": "done",
        "M2": "pending",
        "M3": "in_progress",
        "M4": "pending",
    }
    assert lp1.starts_on < plan.today < lp1.target_on
    past_due = [
        m.key
        for o in lp2.objectives
        for m in o.milestones
        if m.due_on < plan.today and m.status == "pending"
    ]
    assert past_due == ["M3"]
    assert lp2.status == "completed"
    assert lp2.starts_on <= lp2.target_on < plan.today


def test_the_steps_set_each_final_status(plans):
    plan, _ = plans
    set_by_steps = {
        (s.plan, s.milestone): s.status
        for s in plan.plan_steps
        if s.action == "progress"
    }
    for planned in plan.plans:
        for objective in planned.objectives:
            for m in objective.milestones:
                assert set_by_steps.get((planned.key, m.key), "pending") == m.status
    actions = [(s.plan, s.action) for s in plan.plan_steps if s.action != "progress"]
    assert actions == [("LP2", "create"), ("LP1", "create"), ("LP2", "complete")]
    # Each plan is created on its start date, before its first progress step.
    for step in plan.plan_steps:
        planned = plan.plan(step.plan)
        if step.action == "create":
            assert step.day == planned.starts_on
        else:
            created = next(
                s for s in plan.plan_steps
                if s.plan == step.plan and s.action == "create"
            )  # fmt: skip
            assert created.local < step.local


def test_the_report_periods_and_the_schedule(plans):
    plan, _ = plans
    today = plan.today
    monday = today - timedelta(days=today.isoweekday() - 1)
    reports = {r.key: r for r in plan.reports}
    r1, r2, r3 = reports["R1"], reports["R2"], reports["R3"]
    # R1: on demand, at most 366 days, ending before T.
    assert r1.kind == "on_demand" and r1.period_end is not None
    assert r1.period_start <= r1.period_end < today
    assert (r1.period_end - r1.period_start).days + 1 <= ON_DEMAND_MAX_DAYS
    # R2: weekly from a Monday, its Sunday before T's week.
    assert r2.kind == "weekly" and r2.period_start.isoweekday() == 1
    assert r2.period_start + timedelta(days=6) < monday
    # R3: monthly from the 1st of T's month (never after T).
    assert r3.kind == "monthly" and r3.period_start == today.replace(day=1)
    # The schedule's first week is T's week, after R2's.
    assert plan.schedule_starts_on == monday
    assert r2.period_start + timedelta(days=6) < plan.schedule_starts_on <= today
    assert [r.status for r in plan.reports] == [
        "approved_for_family",
        "submitted",
        "draft",
    ]
