"""The pure simulation plan (#543 C2, C3, D5, D7, D7b)."""

import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core import clock as app_clock
from tests.factories.base import RESERVED_EMAIL_DOMAINS
from tests.factories.simulation.anchor import (
    SIM_ANCHOR,
    SIM_HORIZON,
    SIM_SETUP,
    SIM_SETUP_DAY,
    SIM_START,
    ZONE,
)
from tests.factories.simulation.plan import (
    PRIVACY_ACCEPTED_AT,
    SUBJECT_LEVELS,
    UNIVERSITY,
    SimulationPlan,
    age_on,
    available_tutors,
    plan_simulation,
    qualified_tutors,
)

PROFILE_COUNTS = {
    # students, families, guardians, tutors, staff
    # HF-01d: + one paying parent per adult student's family.
    "full": (40, 30, 46, 12, 4),
    "small": (10, 8, 11, 4, 4),
}
LOGINS = {"full": (30, 6), "small": (8, 2)}
"""Guardian and student logins per profile (E6; HF-01d: every family's
first guardian has a login, so receipts and reminders reach every family
in the run's language)."""
PORTAL_GUARDIANS = {"full": 12, "small": 3}
"""HF-01's guardians who use the portal from enrollment (requests, in-app
consent)."""

SUGGESTION_TRIPLES = (
    ("math_h", date(2026, 11, 23), "16:00", "17:00", ["t04", "t09"]),
    ("math_m", date(2026, 11, 24), "16:00", "17:00", ["t04", "t08"]),
    ("english_m", date(2026, 11, 25), "18:00", "19:00", ["t05", "t08", "t10"]),
    ("science_m", date(2026, 11, 25), "10:00", "11:00", ["t11", "t12"]),
    ("calculus_u", date(2026, 11, 24), "18:00", "19:00", ["t07"]),
)
"""D7b: (subject, day, start, end) slots of the future window and the
qualified, available tutors the full profile gives (HF-05 can reuse them);
the last has exactly one (a late Calculus slot)."""

OPERATING = {day: ("09:00", "20:00") for day in range(1, 6)} | {6: ("09:00", "14:00")}


def plans() -> list[SimulationPlan]:
    return [
        plan_simulation(language, profile, 1)
        for language in ("es", "en")
        for profile in ("full", "small")
    ]


@pytest.fixture
def no_clock(monkeypatch):
    def _fail() -> datetime:
        raise AssertionError("the plan read the clock")

    monkeypatch.setattr(app_clock, "now", _fail)
    monkeypatch.setattr(app_clock, "_source", _fail)


def test_the_plan_is_pure_and_independent_of_the_run_date(clock, monkeypatch):
    results = []
    for frozen in (
        datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        datetime(2026, 12, 15, 12, 0, tzinfo=UTC),
    ):
        clock.set(frozen)
        with monkeypatch.context() as patch:

            def _fail() -> datetime:
                raise AssertionError("the plan read the clock")

            patch.setattr(app_clock, "now", _fail)
            results.append(plans())

    assert results[0] == results[1] == results[2]


def test_the_same_seed_gives_the_same_plan_and_another_seed_other_names(no_clock):
    assert plan_simulation("es", "small", 7) == plan_simulation("es", "small", 7)
    first = {s.given_names for s in plan_simulation("es", "full", 1).students}
    other = {s.given_names for s in plan_simulation("es", "full", 2).students}
    assert first != other


@pytest.mark.parametrize("plan", plans(), ids=lambda p: f"{p.language}-{p.profile}")
def test_counts_and_people_invariants(plan):
    students, families, guardians, tutors, staff = PROFILE_COUNTS[plan.profile]
    assert len(plan.students) == students
    assert len(plan.families) == families
    assert len(plan.guardians) == guardians
    assert len(plan.tutors) == tutors
    assert len(plan.staff) == staff
    guardian_logins, student_logins = LOGINS[plan.profile]
    assert sum(g.account is not None for g in plan.guardians) == guardian_logins
    assert sum(g.portal for g in plan.guardians) == PORTAL_GUARDIANS[plan.profile]
    assert all(f.guardians[0].account is not None for f in plan.families)
    assert sum(s.account is not None for s in plan.students) == student_logins

    usernames = [a.username for a in plan.accounts]
    usernames += [plan.pending_registration.username, plan.pending_tutor.username]
    assert len(usernames) == len(set(usernames))
    assert all(re.fullmatch(r"[a-z0-9._-]{3,64}", name) for name in usernames)

    emails = [g.email for g in plan.guardians] + [
        s.email for s in plan.students if s.email is not None
    ]
    assert emails and all(
        email.rsplit("@", 1)[1] in RESERVED_EMAIL_DOMAINS for email in emails
    )
    for family in plan.families:
        for student in family.students:
            if student.is_adult:
                assert student.level == UNIVERSITY
                assert age_on(student.birthdate, SIM_SETUP_DAY) >= 18
                # HF-01d: the paying parent only (a login, no portal use).
                assert student.consent is None
                assert [g.portal for g in family.guardians] == [False]
            else:
                assert family.guardians, "every minor has a guardian"
                assert student.consent is not None
                assert student.consent.guardian == family.guardians[0].key
                assert student.consent.at >= PRIVACY_ACCEPTED_AT
            assert student.contacts
            assert all(re.fullmatch(r"555-01\d\d", c.phone) for c in student.contacts)
    levels = {SUBJECT_LEVELS[s.key] for s in plan.subjects}
    assert {s.level for s in plan.students} <= levels
    adults = sum(s.is_adult for s in plan.students)
    assert adults == (4 if plan.profile == "full" else 1)

    for family in plan.families:
        if family.enrolls_at != SIM_SETUP:
            day = family.enrolls_at.astimezone(ZONE).date()
            assert SIM_START <= day < SIM_ANCHOR
    for tutor in plan.tutors:
        if tutor.account.created_at != SIM_SETUP:
            assert SIM_START <= tutor.joins_on < SIM_ANCHOR
    for account in plan.accounts:
        assert account.privacy_accepted_at >= datetime(2026, 10, 18, tzinfo=UTC)


def test_full_profile_enrollment_schedule_and_levels():
    plan = plan_simulation("es", "full", 1)
    later = Counter(
        f.enrolls_at.astimezone(ZONE).date()
        for f in plan.families
        for _ in f.students
        if f.enrolls_at != SIM_SETUP
    )
    assert later == {date(2026, 9, 14): 2, date(2026, 10, 5): 1, date(2026, 11, 9): 1}
    shapes = Counter(len(f.students) for f in plan.families)
    assert shapes == {1: 21, 2: 8, 3: 1}
    levels = Counter(s.level for s in plan.students)
    assert levels == {
        "elementary": 12,
        "middle_school": 12,
        "high_school": 12,
        "university": 4,
    }
    assert plan.registered_family.enrolls_at.date() == date(2026, 9, 14)


def test_spanish_names_have_two_last_names_and_english_ones_are_plain():
    es = plan_simulation("es", "full", 1)
    en = plan_simulation("en", "full", 1)
    for person in (*es.students, *es.guardians):
        assert len(person.family_names.split()) >= 2
    for person in (*en.students, *en.guardians):
        full = f"{person.given_names} {person.family_names}"
        assert re.fullmatch(r"[A-Za-z' -]+", full), full
    es_names = {
        f"{p.given_names} {p.family_names}" for p in (*es.students, *es.guardians)
    }
    en_names = {
        f"{p.given_names} {p.family_names}" for p in (*en.students, *en.guardians)
    }
    assert not es_names & en_names


@pytest.mark.parametrize("plan", plans(), ids=lambda p: f"{p.language}-{p.profile}")
def test_every_subject_has_two_qualified_tutors_on_every_day(plan):
    day = SIM_START
    while day <= SIM_HORIZON:
        for subject in plan.subjects:
            assert len(qualified_tutors(plan, subject.key, day)) >= 2, (subject, day)
        day += timedelta(days=1)


@pytest.mark.parametrize("plan", plans(), ids=lambda p: f"{p.language}-{p.profile}")
def test_tutor_profiles_are_complete_and_within_operating_hours(plan):
    texts = [t.experience for t in plan.tutors]
    assert len(set(texts)) == len(texts)
    for tutor in plan.tutors:
        assert 2 <= len(tutor.subjects) <= 5
        assert tutor.windows
        per_day = Counter(day for day, _, _ in tutor.windows)
        assert all(1 <= n <= 3 for n in per_day.values())
        for day, start, end in tutor.windows:
            opens, closes = OPERATING[day]
            assert opens <= start < end <= closes
        assert "room3" not in tutor.preferred
    assert {t.archetype for t in plan.tutors} == {
        "mornings",
        "afternoons",
        "evenings",
        "full_days",
    }
    assert any(
        all(v is None for v in (t.daily_limit, t.weekly_limit, t.required_break))
        for t in plan.tutors
    ), "at least one tutor is unlimited"
    statuses = Counter(q.status for q in plan.qualifications)
    assert statuses == {"rejected": 1, "revoked": 1, "pending": 1}
    [revoked] = [q for q in plan.qualifications if q.status == "revoked"]
    assert revoked.decided_at is not None
    assert revoked.decided_at.astimezone(ZONE).date() == date(2026, 10, 20)
    assert revoked.subject not in plan.tutor(revoked.tutor).subjects


def test_full_profile_matrix_archetypes_limits_and_exceptions():
    plan = plan_simulation("en", "full", 1)
    tutors = plan.tutors
    assert Counter(t.archetype for t in tutors) == {
        "mornings": 3,
        "afternoons": 4,
        "evenings": 3,
        "full_days": 2,
    }
    assert 2.5 <= sum(len(t.subjects) for t in tutors) / len(tutors) <= 3.5

    wide = [
        s.key
        for s in plan.subjects
        if len(holders := [t for t in tutors if s.key in t.subjects]) >= 3
        and len({t.archetype for t in holders}) >= 2
    ]
    assert len(wide) >= 4
    levels_of = [{SUBJECT_LEVELS[s] for s in t.subjects} for t in tutors]
    generalists = [
        t
        for t, lv in zip(tutors, levels_of, strict=True)
        if len(t.subjects) == 5 and len(lv) == 2
    ]
    specialists = [
        t
        for t, lv in zip(tutors, levels_of, strict=True)
        if len(t.subjects) == 2 and len(lv) == 1
    ]
    assert len(generalists) >= 2 and len(specialists) >= 2

    split = [
        t
        for t in tutors
        if any(n >= 2 for n in Counter(d for d, _, _ in t.windows).values())
    ]
    assert len(split) >= 2
    assert sum(t.daily_limit is not None for t in tutors) >= 4
    assert sum(t.weekly_limit is not None for t in tutors) >= 3
    assert sum(t.required_break is not None for t in tutors) >= 4
    assert sum(bool(t.preferred) for t in tutors) >= 6
    assert all(len(t.preferred) <= 2 for t in tutors)

    anchor = datetime(2026, 11, 19, tzinfo=UTC).date()
    past = [e for e in plan.exceptions if e.ends_on < anchor]
    future = [e for e in plan.exceptions if e.starts_on >= anchor]
    past_off = [e for e in past if e.kind == "unavailable"]
    assert len(past_off) >= 4
    assert any(e.starts_at is not None for e in past_off)
    assert any(e.kind == "available" for e in past)
    assert {e.kind for e in future} == {"unavailable", "available"}
    assert all(
        e.created_at.astimezone(ZONE).date() < e.starts_on for e in plan.exceptions
    )


@pytest.mark.parametrize(
    ("subject", "day", "start", "end", "expected"), SUGGESTION_TRIPLES
)
def test_suggestion_ready_slots(subject, day, start, end, expected):
    for language in ("es", "en"):
        plan = plan_simulation(language, "full", 1)
        assert available_tutors(plan, subject, day, start, end) == expected
    assert day >= SIM_ANCHOR


def test_one_late_slot_has_a_single_qualified_match():
    plan = plan_simulation("es", "full", 1)
    many = [t for *_, t in SUGGESTION_TRIPLES if len(t) >= 2]
    single = [t for *_, t in SUGGESTION_TRIPLES if len(t) == 1]
    assert len(many) >= 3 and len(single) == 1
    # Calculus: the only tutor available in the evening on every weekday.
    for offset in range(5):
        day = date(2026, 11, 23) + timedelta(days=offset)
        evening = available_tutors(plan, "calculus_u", day, "18:00", "19:00")
        assert len(evening) <= 1
