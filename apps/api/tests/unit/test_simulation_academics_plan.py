"""The simulation's pure academic record (#551 HF-01c A1, A2, and the plan
side of B1-B6)."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from functools import cache

import pytest

from app.core import clock as app_clock
from tests.factories.simulation.anchor import SIM_ANCHOR, SIM_NOW, SIM_START, ZONE
from tests.factories.simulation.coursework import (
    EARLY_ENROLLMENT,
    MID_COURSE_DAY,
    NOTE_AFTER,
    PENDING_SUBMIT_DAYS,
    Coursework,
    Teaching,
    attended,
)
from tests.factories.simulation.plan import SimulationPlan, plan_simulation
from tests.factories.simulation.texts import POOLS, TextPool
from tests.factories.simulation.timetable import monday

PROFILES = ("full", "small")
LANGUAGES = ("es", "en")
ACCENTED = set("áéíóúñ¿¡ÁÉÍÓÚÑ")


@cache
def plan(language: str = "es", profile: str = "full") -> SimulationPlan:
    return plan_simulation(language, profile, 1)  # type: ignore[arg-type]


def work(language: str = "es", profile: str = "full") -> Coursework:
    return plan(language, profile).academics


def local_date(at: datetime) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


def enrolled_on(p: SimulationPlan) -> dict[str, date]:
    return {s.key: local_date(f.enrolls_at) for f in p.families for s in f.students}


# --- A1: pure, independent of the run date --------------------------------------------


def test_the_academic_record_is_pure_and_independent_of_the_run_date(
    clock, monkeypatch
):
    results = []
    for frozen in (
        datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        datetime(2026, 12, 15, 12, 0, tzinfo=UTC),
    ):
        clock.set(frozen)
        with monkeypatch.context() as patch:

            def _fail() -> datetime:
                raise AssertionError("the academic record read the clock")

            patch.setattr(app_clock, "now", _fail)
            results.append([plan_simulation("es", p, 1).academics for p in PROFILES])

    assert results[0] == results[1]
    assert results[0] == [work("es", p) for p in PROFILES]


def test_the_record_is_the_same_in_both_languages_apart_from_text():
    for profile in PROFILES:
        es, en = work("es", profile), work("en", profile)
        assert [(s.at, s.action, s.key, s.milestone, s.status) for s in es.steps] == [
            (s.at, s.action, s.key, s.milestone, s.status) for s in en.steps
        ]


def test_steps_are_in_time_order_and_all_before_the_anchor():
    for profile in PROFILES:
        steps = work("es", profile).steps
        assert [s.at for s in steps] == sorted(s.at for s in steps)
        assert all(s.at < SIM_NOW for s in steps)
        assert {s.action for s in steps} == {
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
        }


# --- A2: every item is about a class or student the tutor taught ----------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_every_item_is_on_a_held_class_of_its_tutor_in_its_subject(profile):
    p = plan("es", profile)
    w = p.academics
    t = p.timetable
    for item in (*w.notes, *w.evaluations, *w.homework):
        c = t.cls(item.cls)
        assert c.status == "completed" and not c.substituted, item.key
        assert c.final.tutor == item.tutor, item.key
        assert c.subject == item.subject, item.key
        assert item.student in attended(c), item.key
        assert c.final.ends_at < (
            item.at if hasattr(item, "at") else item.created_at  # type: ignore[union-attr]
        ), item.key


@pytest.mark.parametrize("profile", PROFILES)
def test_the_tutor_teaches_the_student_at_every_write(profile):
    p = plan("es", profile)
    w = p.academics
    teaching = Teaching(p.timetable)
    for lp in w.plans:
        instants = [lp.created_at, *(at for m in lp.milestones for at, _ in m.steps)]
        if lp.closed_at is not None:
            instants.append(lp.closed_at)
        for at in instants:
            assert teaching.teaches(lp.tutor, lp.student, at, lp.subject), lp.key
    for h in w.homework:
        for at in (h.created_at, h.graded_at):
            if at is not None:
                assert teaching.teaches(h.tutor, h.student, at, h.subject), h.key
    for r in w.reports:
        for at in (r.written_at, r.changes_at, r.resubmitted_at):
            if at is not None:
                assert teaching.teaches(r.tutor, r.student, at), r.key
        taught = [
            c
            for c in p.timetable.classes
            if c.status == "completed"
            and c.final.tutor == r.tutor
            and r.student in c.students
            and c.final.ends_at < r.written_at
        ]
        assert taught, r.key
    for s in w.schedules:
        assert teaching.teaches(s.tutor, s.student, s.created_at), s.key


# --- B1: evaluations ------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_evaluations_are_planned_per_template(profile):
    p = plan("es", profile)
    w = p.academics
    enrolled = enrolled_on(p)
    by = Counter(e.template for e in w.evaluations)
    diagnostics = {e.student: e for e in w.evaluations if e.template == "diagnostic"}
    early = {s for s, day in enrolled.items() if day < EARLY_ENROLLMENT}
    assert set(diagnostics) == early
    for student, e in diagnostics.items():
        first_day = max(enrolled[student], SIM_START)
        assert local_date(e.at) - first_day <= timedelta(days=15), student
    assert by["monthly"] >= len(p.students)
    months = Counter(
        (e.student, local_date(e.at).month)
        for e in w.evaluations
        if e.template == "monthly"
    )
    assert set(months.values()) == {1}
    assert {month for _, month in months} == {9, 10}
    course = [e for e in w.evaluations if e.template == "course"]
    assert {e.assessment[0] for e in course if e.assessment} == {
        c.key for c in p.timetable.cohorts
    }
    running = [e for e in course if local_date(e.at) > date(2026, 11, 1)]
    assert running and {local_date(e.at) for e in running} <= {MID_COURSE_DAY}
    assert all(e.at < SIM_NOW for e in w.evaluations)
    assert len(w.templates) == 3


# --- B2: learning plans ---------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_learning_plans_meet_the_minimums(profile):
    w = work("es", profile)
    outcomes = Counter(lp.outcome for lp in w.plans)
    assert outcomes["completed"] >= 4
    assert outcomes["discontinued"] >= 2
    assert outcomes["active"] >= 2
    for lp in w.plans:
        statuses = [m.status for m in lp.milestones]
        if lp.outcome == "completed":
            assert set(statuses) == {"done"}, lp.key
            assert lp.closed_at is not None and lp.closed_at < SIM_NOW
        if lp.outcome == "discontinued":
            assert lp.reason is not None and lp.closed_at is not None
        if lp.outcome == "active":
            # QA #551 D1: an active plan spans the anchor.
            assert lp.target_on >= SIM_ANCHOR, lp.key
            assert any(
                m.status != "done" and m.due_on >= SIM_ANCHOR for m in lp.milestones
            ), lp.key
            for m in lp.milestones:
                if m.due_on >= SIM_ANCHOR:
                    assert m.status in ("pending", "in_progress"), lp.key
    overdue = [
        lp
        for lp in w.plans
        if lp.outcome == "active"
        and any(
            m.due_on < SIM_ANCHOR and m.status == "in_progress" for m in lp.milestones
        )
    ]
    assert len(overdue) >= 2
    # One active plan per student and subject.
    active = Counter(
        (lp.student, lp.subject) for lp in w.plans if lp.outcome == "active"
    )
    assert set(active.values()) == {1}


def test_about_sixty_percent_of_the_students_have_a_plan():
    p = plan("es", "full")
    students = {lp.student for lp in p.academics.plans}
    assert 0.5 <= len(students) / len(p.students) <= 0.7


# --- B3: homework ---------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_homework_states_follow_the_anchor(profile):
    w = work("es", profile)
    past = [h for h in w.homework if h.due_on < SIM_ANCHOR]
    future = [h for h in w.homework if h.due_on >= SIM_ANCHOR]
    assert {h.outcome for h in past} == {"graded", "missing"}
    assert {h.outcome for h in future} <= {"pending", "submitted"}
    assert sum(h.outcome == "submitted" for h in future) >= 3
    missing = sum(h.outcome == "missing" for h in past) / len(past)
    assert 0.05 <= missing <= 0.15
    for h in w.homework:
        assert h.due_on == local_date(h.created_at) + timedelta(days=7)
        if h.submitted_at is not None:
            assert h.created_at < h.submitted_at < SIM_NOW
        if h.outcome == "submitted":
            assert local_date(h.submitted_at) in PENDING_SUBMIT_DAYS  # type: ignore[arg-type]
        if h.graded_at is not None:
            assert h.submitted_at is not None and h.submitted_at < h.graded_at < SIM_NOW


def test_about_one_assignment_per_student_every_two_weeks():
    p = plan("es", "full")
    per_student = Counter(h.student for h in p.academics.homework)
    weeks = (monday(SIM_ANCHOR) - monday(SIM_START)).days // 7 + 1
    early = [s.key for s in p.students if enrolled_on(p)[s.key] < SIM_START]
    # At most one every other week; closures, cancellations and absences
    # skip some.
    assert all(1 <= per_student[s] <= (weeks + 1) // 2 for s in early)
    average = sum(per_student[s] for s in early) / len(early)
    assert weeks / 2 - 2 <= average <= weeks / 2


# --- B4: progress reports -------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_progress_reports_cover_september_october_and_the_pending_week(profile):
    p = plan("es", profile)
    w = p.academics
    assert all(r.period_end < SIM_ANCHOR for r in w.reports)
    monthly = [r for r in w.reports if r.kind == "monthly"]
    assert {r.period_start for r in monthly} == {date(2026, 9, 1), date(2026, 10, 1)}
    assert all(r.status == "approved_for_family" for r in monthly)
    sent_back = sum(r.changes_at is not None for r in monthly)
    assert 1 <= sent_back <= max(1, round(0.15 * len(monthly)))
    for month in (9, 10):
        completed = Counter(
            s
            for c in p.timetable.classes
            if c.status == "completed" and c.final.day.month == month
            for s in c.students
        )
        due = {s for s, n in completed.items() if n >= 4}
        assert {r.student for r in monthly if r.period_start.month == month} == due
    assert sum(r.kind == "on_demand" for r in w.reports) >= 2
    waiting = [r for r in w.reports if r.status == "submitted"]
    assert len(waiting) >= 2
    assert {r.period_end for r in waiting} == {date(2026, 11, 15)}
    assert {local_date(r.written_at) for r in waiting} <= set(PENDING_SUBMIT_DAYS)
    assert Counter(s.cadence for s in w.schedules) == {"weekly": 2, "monthly": 1}
    assert all(local_date(s.created_at).month == 9 for s in w.schedules)
    for r in w.reports:
        if r.schedule is not None:
            tick = r.period_end + timedelta(days=1)
            assert any(local_date(t) == tick for t in w.ticks), r.key
    assert all(local_date(t) < SIM_ANCHOR for t in w.ticks)


# --- B5: notes ------------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_notes_are_a_quarter_of_the_classes_and_mostly_shared(profile):
    p = plan("es", profile)
    w = p.academics
    held = [c for c in p.timetable.classes if c.status == "completed"]
    assert 0.15 <= len(w.notes) / len(held) <= 0.35
    assert 0.45 <= sum(n.shared for n in w.notes) / len(w.notes) <= 0.75
    for n in w.notes:
        assert n.at == p.timetable.cls(n.cls).final.ends_at + NOTE_AFTER


# --- B6: language ---------------------------------------------------------------------


def _note_bodies(pool: TextPool, subject: str) -> set[str]:
    return {
        f"{topic}: {progress} {nxt}"
        for topic in pool.lists("closeout_topics")[subject]
        for progress in pool.choices("note_progress")
        for nxt in pool.choices("note_next_steps")
    }


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_text_comes_from_the_run_languages_pool(language):
    p = plan(language, "small")
    w = p.academics
    pool = TextPool(language)
    subjects = {s.key: s.name for s in p.subjects}
    for n in w.notes:
        assert n.body in _note_bodies(pool, n.subject)
    for t in w.templates:
        assert t.name == pool.mapping("evaluation_templates")[t.key]
        assert list(t.criteria) == pool.lists("evaluation_criteria")[t.key]
    for e in w.evaluations:
        assert e.feedback in pool.choices("evaluation_feedback")
        assert {c for c in e.comments if c} <= set(pool.choices("evaluation_comments"))
    for lp in w.plans:
        titles = {
            t.format(subject=subjects[lp.subject]) for t in pool.choices("plan_titles")
        }
        assert lp.title in titles
        assert lp.description in pool.choices("plan_descriptions")
        assert {o.title for o in lp.objectives} <= set(pool.choices("plan_objectives"))
        assert {m.title for m in lp.milestones} <= set(pool.choices("plan_milestones"))
        if lp.reason:
            assert lp.reason in pool.choices("plan_discontinue_reasons")
    for h in w.homework:
        titles = {
            t.format(topic=topic)
            for t in pool.choices("homework_titles")
            for topic in pool.lists("closeout_topics")[h.subject]
        }
        assert h.title in titles
        assert h.instructions in pool.choices("homework_instructions")
        assert h.feedback is None or h.feedback in pool.choices("homework_feedback")
        assert h.answer is None or h.answer in pool.choices("homework_answers")
    keys = {
        "summary": "report_summaries",
        "strengths": "report_strengths",
        "areas_for_improvement": "report_improvements",
        "next_steps": "report_next_steps",
    }
    for r in w.reports:
        for field, key in keys.items():
            assert r.narrative[field] in pool.choices(key)
        assert r.changes_reason is None or r.changes_reason in pool.choices(
            "report_change_reasons"
        )
    if language == "en":
        texts = [n.body for n in w.notes] + [lp.title for lp in w.plans]
        texts += [h.title for h in w.homework]
        assert not any(ACCENTED & set(value) for value in texts)


def test_the_new_pool_keys_exist_in_both_languages():
    for key in (
        "note_progress",
        "evaluation_criteria",
        "plan_titles",
        "report_summaries",
    ):
        assert key in POOLS["es"] and key in POOLS["en"]


def test_other_seeds_build_a_record_too():
    # Absences and cancellations differ per seed: a diagnostic falls on the
    # first attended class, and a student without a suitable class for a
    # learning plan is skipped (seeds 1-20 of both profiles were checked).
    for seed in range(2, 15):
        w = plan_simulation("es", "small", seed).academics
        assert Counter(lp.outcome for lp in w.plans)["completed"] == 4
        assert w.steps and all(s.at < SIM_NOW for s in w.steps)
