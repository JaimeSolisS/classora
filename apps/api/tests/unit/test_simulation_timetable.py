"""The simulation's pure timetable and outcomes (#550 HF-01b A1-A3)."""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from functools import cache

import pytest

from app.core import clock as app_clock
from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_NOW,
    SIM_SETUP,
    ZONE,
)
from tests.factories.simulation.plan import SimulationPlan, plan_simulation
from tests.factories.simulation.timetable import (
    PENDING_DAYS,
    AnswerOffer,
    ChangeWeekly,
    CreateMakeup,
    DecideChange,
    DecideSessionRequest,
    DecideTimeOff,
    ExpireOffer,
    Hold,
    RequestChange,
    RequestSession,
    RequestTimeOff,
    Timetable,
    consistency_errors,
    mins,
    monday,
    week_starts,
)

PROFILES = ("full", "small")
LANGUAGES = ("es", "en")


@cache
def plan(language: str, profile: str) -> SimulationPlan:
    return plan_simulation(language, profile, 1)  # type: ignore[arg-type]


def timetable(language: str = "es", profile: str = "full") -> Timetable:
    return plan(language, profile).timetable


def local_date(at: datetime) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


def past_series_classes(t: Timetable) -> list:
    """The rate denominator: individual and group series classes before the
    anchor (closures included)."""
    return [
        c
        for c in t.classes
        if c.series is not None
        and c.kind in ("individual", "group")
        and c.placed.day < SIM_ANCHOR
    ]


# --- A1 -------------------------------------------------------------------------------


def test_the_timetable_is_pure_and_independent_of_the_run_date(clock, monkeypatch):
    results = []
    for frozen in (
        datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        datetime(2026, 12, 15, 12, 0, tzinfo=UTC),
    ):
        clock.set(frozen)
        with monkeypatch.context() as patch:

            def _fail() -> datetime:
                raise AssertionError("the timetable read the clock")

            patch.setattr(app_clock, "now", _fail)
            results.append([plan_simulation("es", p, 1).timetable for p in PROFILES])

    assert results[0] == results[1]
    assert results[0] == [timetable("es", p) for p in PROFILES]


def test_the_timetable_is_the_same_in_both_languages_apart_from_text():
    for profile in PROFILES:
        es, en = timetable("es", profile), timetable("en", profile)
        assert [(c.key, c.placed, c.final, c.status) for c in es.classes] == [
            (c.key, c.placed, c.final, c.status) for c in en.classes
        ]


# --- A2: the consistency MUST ---------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("profile", PROFILES)
def test_every_planned_class_respects_the_tutor_profiles_and_the_center(
    language, profile
):
    # consistency_errors names each class and the rule it breaks.
    assert consistency_errors(plan(language, profile)) == []


def test_the_checker_names_the_class_and_the_rule():
    from dataclasses import replace

    p = plan("es", "small")
    t = p.timetable
    victim = next(c for c in t.classes if c.status == "completed")
    other = next(
        c
        for c in t.classes
        if c.status == "completed"
        and c.final.tutor != victim.final.tutor
        and c.final.day != victim.final.day
    )
    moved = replace(victim, final=replace(victim.final, day=date(2026, 9, 16)))
    broken = replace(
        p,
        schedule=replace(
            t,
            classes=tuple(moved if c.key == victim.key else c for c in t.classes),
        ),
    )
    errors = consistency_errors(broken)
    assert any(e.startswith(victim.key) and "center_closed" in e for e in errors)
    assert other.key not in " ".join(errors)


def test_no_series_runs_past_the_horizon_and_every_class_is_inside_the_window():
    for language in LANGUAGES:
        for profile in PROFILES:
            t = timetable(language, profile)
            assert all(s.until <= SIM_HORIZON for s in t.series)
            assert all(
                date(2026, 9, 1) <= c.final.day <= SIM_HORIZON for c in t.classes
            )


def test_a_tutor_never_teaches_a_revoked_or_pending_subject_and_tutor_12_waits():
    for profile in PROFILES:
        p = plan("es", profile)
        joiner = next(t for t in p.tutors if t.joins_on > SIM_SETUP.date())
        events = {(q.tutor, q.subject): q for q in p.qualifications}
        for c in p.timetable.classes:
            tutor = c.final.tutor
            if tutor is None:
                continue
            assert (tutor, c.subject) not in events or events[
                (tutor, c.subject)
            ].status == "rejected", c.key
            if tutor == joiner.key:
                assert c.final.day >= date(2026, 10, 1), c.key


# --- A3: rates, volumes and load (full profile) ---------------------------------------


def test_the_past_outcomes_fall_within_the_target_rates():
    t = timetable()
    past = past_series_classes(t)
    total = len(past)
    rescheduled = sum(1 for c in past if c.rescheduled)
    completed = sum(1 for c in past if c.status == "completed" and not c.rescheduled)
    cancelled = sum(1 for c in past if c.status == "cancelled")

    assert 0.80 <= completed / total <= 0.90
    assert 0.03 <= cancelled / total <= 0.08
    assert 0.02 <= rescheduled / total <= 0.05


def test_each_cancellation_classification_appears_at_least_twice():
    t = timetable()
    approved = [
        r
        for r in t.change_requests
        if r.kind == "cancellation" and r.decision == "approved"
    ]
    classes = Counter(r.classification for r in approved)
    centre = sum(
        1
        for c in t.classes
        if c.status == "cancelled" and not any(r.cls == c.key for r in approved)
    )
    absences = sum(1 for c in t.classes for _, mark in c.attendance if mark == "absent")

    assert classes["on_time"] >= 2
    assert classes["late"] >= 2
    assert classes["excused"] + centre >= 2
    assert sum(1 for r in approved if r.exception) >= 2
    assert centre >= 2
    # no_show: the services classify only a request made after the start,
    # which they refuse; the no-shows are absences (engineer D2).
    assert absences >= 2


def test_substitutions_makeups_requests_and_offers_meet_their_minimums():
    for profile, subs, makeups in (("full", 8, 6), ("small", 2, 2)):
        t = timetable("es", profile)
        assert sum(c.substituted and c.status == "completed" for c in t.classes) >= subs
        assert (
            sum(
                1
                for c in t.classes
                if c.makeup_of is not None and c.status == "completed"
            )
            >= makeups
        )
        requests = Counter(r.decision for r in t.session_requests)
        past = [r for r in t.session_requests if r.created_at < SIM_NOW]
        assert sum(1 for r in past if r.decision == "confirmed") >= 6
        assert requests["rejected"] >= 2
        assert sum(1 for r in t.change_requests if r.decision == "rejected") >= 2
        assert len(t.declined) >= 3 and len(t.expired) >= 2
        offs = Counter(o.decision for o in t.time_offs)
        assert offs["approved"] >= 2 and offs["rejected"] >= 1
        assert len(t.attendance_corrections) >= 3
        assert len(t.closeout_corrections) >= 2
        assert len(t.changes_requested) >= 2


def test_the_weekly_volume_is_about_eighty_classes():
    t = timetable()
    weeks: Counter[date] = Counter(
        monday(c.placed.day) for c in t.classes if c.series is not None
    )
    for start in week_starts():
        assert 70 <= weeks[start] <= 90, start


def test_no_tutor_goes_over_a_limit_and_every_tutor_teaches_every_week():
    p = plan("es", "full")
    weekly: dict[tuple[str, date], int] = defaultdict(int)
    taught: set[tuple[str, date]] = set()
    for c in p.timetable.classes:
        if c.final.tutor is None:
            continue
        if c.series is not None:
            taught.add((c.placed.tutor or "", monday(c.placed.day)))
        if c.occupies:
            weekly[(c.final.tutor, monday(c.final.day))] += c.final.minutes
    for tutor in p.tutors:
        if tutor.weekly_limit is not None:
            assert all(
                minutes <= tutor.weekly_limit
                for (key, _), minutes in weekly.items()
                if key == tutor.key
            ), tutor.key
        if tutor.joins_on <= SIM_SETUP.date():
            for start in week_starts():
                assert (tutor.key, start) in taught, (tutor.key, start)


# --- Events: dated, ordered and final before the anchor -------------------------------


def test_events_are_in_time_order_and_history_ends_before_the_anchor():
    for profile in PROFILES:
        t = timetable("es", profile)
        instants = [e.at for e in t.events]
        assert instants == sorted(instants)
        assert all(SIM_SETUP <= at < SIM_NOW for at in instants)
        for event in t.events:
            if isinstance(event, Hold):
                c = t.cls(event.cls)
                assert event.at > c.final.ends_at and c.final.day < SIM_ANCHOR


def test_every_class_before_the_anchor_is_final_and_every_later_one_scheduled():
    for profile in PROFILES:
        t = timetable("es", profile)
        held = {e.cls for e in t.events if isinstance(e, Hold)}
        for c in t.classes:
            if c.final.day < SIM_ANCHOR and not (
                c.status == "cancelled" and c.placed.day >= SIM_ANCHOR
            ):
                assert c.status in ("completed", "cancelled", "skipped_closure"), c.key
            else:
                assert c.status in (
                    "confirmed",
                    "offered",
                    "unassigned",
                    "cancelled",
                ), c.key
            assert (c.key in held) == (c.status == "completed"), c.key


def test_pending_items_are_created_on_17_or_18_november():
    for profile in PROFILES:
        t = timetable("es", profile)
        pending_requests = [r for r in t.session_requests if r.decision == "pending"]
        pending_changes = [r for r in t.change_requests if r.decision == "pending"]
        pending_off = [o for o in t.time_offs if o.decision == "pending"]
        decided_offs = {e.time_off for e in t.events if isinstance(e, DecideTimeOff)}
        decided_changes = {e.request for e in t.events if isinstance(e, DecideChange)}
        decided_requests = {
            e.request for e in t.events if isinstance(e, DecideSessionRequest)
        }

        assert len(pending_requests) >= 3 and len(pending_changes) >= 3
        assert len(pending_off) >= 1
        assert all(local_date(r.created_at) in PENDING_DAYS for r in pending_requests)
        assert all(local_date(r.created_at) in PENDING_DAYS for r in pending_changes)
        assert not {r.key for r in pending_requests} & decided_requests
        assert not {r.key for r in pending_changes} & decided_changes
        assert not {o.key for o in pending_off} & decided_offs
        for event in t.events:
            if (
                isinstance(event, RequestTimeOff)
                and event.time_off.decision == "pending"
            ):
                assert local_date(event.at) in PENDING_DAYS
            if isinstance(event, ChangeWeekly):
                assert local_date(event.at) in PENDING_DAYS
            if isinstance(event, RequestChange | RequestSession):
                pass

        statuses = Counter(c.status for c in t.classes if c.placed.day >= SIM_ANCHOR)
        assert statuses["offered"] >= 3 and statuses["unassigned"] >= 2
        offered_at = {
            e.cls: e.at
            for e in t.events
            if isinstance(e, DecideSessionRequest | CreateMakeup)
            and getattr(e, "cls", None) is not None
        }
        answered = {e.cls for e in t.events if isinstance(e, AnswerOffer | ExpireOffer)}
        for c in t.classes:
            if c.status == "offered":
                assert local_date(offered_at[c.key]) in PENDING_DAYS
                assert c.key not in answered


def test_one_offs_respect_the_policy_windows():
    """Make-ups within 30 days of the cancelled class; time off, requests and
    cancellations decided before the class starts."""
    t = timetable()
    for c in t.classes:
        if c.makeup_of is not None:
            original = t.cls(c.makeup_of)
            assert original.status == "cancelled"
            assert c.placed.starts_at < original.placed.starts_at + timedelta(days=30)
    for event in t.events:
        if isinstance(event, DecideChange):
            request = next(r for r in t.change_requests if r.key == event.request)
            assert event.at < t.cls(request.cls).placed.starts_at
            assert event.at > request.created_at
        if isinstance(event, DecideTimeOff):
            off = next(o for o in t.time_offs if o.key == event.time_off)
            assert event.at < t.cls(off.cls).placed.starts_at


def test_minors_have_weekday_classes_after_school_and_windows_are_grid_aligned():
    p = plan("es", "full")
    adults = {s.key for s in p.students if s.is_adult}
    for c in p.timetable.classes:
        assert mins(c.final.start) % 30 == 0, c.key
        if c.final.day.isoweekday() <= 5 and not set(c.students) <= adults:
            assert mins(c.final.start) >= 14 * 60, c.key


def test_no_event_involves_a_tutor_before_they_join_or_a_family_before_it_enrolls():
    from tests.factories.simulation.timetable import (
        Reoffer,
        Substitute,
    )

    for profile in PROFILES:
        p = plan("es", profile)
        t = p.timetable
        joins = {tutor.key: tutor.joins_at for tutor in p.tutors}
        enrolled = {s.key: f.enrolls_at for f in p.families for s in f.students}
        for event in t.events:
            tutors: list[str] = []
            if isinstance(event, AnswerOffer | Reoffer):
                tutors = [event.tutor]
            elif isinstance(event, Substitute):
                tutors = [event.substitute]
            elif isinstance(event, DecideSessionRequest) and event.tutor:
                tutors = [event.tutor]
            elif isinstance(event, CreateMakeup):
                tutor = t.cls(event.cls).placed.tutor
                tutors = [tutor] if tutor else []
            assert all(joins[k] < event.at for k in tutors), event
            if isinstance(event, RequestChange):
                cls = t.cls(event.request.cls)
                assert all(enrolled[s] < event.at for s in cls.students), event
            if isinstance(event, RequestSession):
                assert enrolled[event.request.student] < event.at, event
