"""Per-course work after lock (M7-08d E1, E2; ADR-0017 E3, E11; #442 C, N1).

A late session of a cohort paid ``per_course`` is an anchor of the walk: the step
after every fixed step, at ``k = K + 1``, paid what :func:`walk_cohort` gives that
step. Later walks read it as a fixed step, ordered by its stored ``k``. A revision
of a per-course origin corrects only a change of kind, at the origin's ``k`` and
``N``.

The last tests are the #109 L8-style oracle: many random cohorts, with ``N``
going up and down, late sessions, anchors posted at random moments and periods
finalized oldest first, all through the real functions. Once everything is
approved, anchored and finalized, the lines and anchors of every cohort sum to its
rate exactly, to the centavo. A cancellation always leaves a later session: an
``N`` that drops after the cohort's last session is paid has no later line to
catch it up, which is #442's rule with or without anchors (H3). Every expected
amount elsewhere is worked by hand from ``rate x k / N``, half away from zero to
the centavo.
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest

from app.core.money import Money, Rate
from app.modules.payroll.calculation import (
    PER_COURSE,
    ApprovedSession,
    CohortStep,
    CohortWalk,
    CourseWalkError,
    FinalizedCourseLine,
    LineDraft,
    OriginSnapshot,
    PayBasis,
    course_anchor_draft,
    course_correction_draft,
    course_rate,
    in_walk_order,
    walk_cohort,
)
from app.modules.payroll.errors import (
    CompensationRateDeleteUnderFinalizedLineError,
)
from app.modules.payroll.schemas import RateInEffect, SessionPayRuleVersion
from tests.unit.test_payroll_calculation_pure import (
    LONG_AGO,
    basis,
    course,
    fixed,
    per_course,
    rule,
    terms_row,
)

FIRST = date(2026, 3, 2)
RATE = Rate("1000")


def paid(rate: str = "1000") -> RateInEffect:
    return RateInEffect(
        compensation_rate_id=uuid.uuid7(),
        rate_model=PER_COURSE,
        rate=Rate(rate),
        effective_from=LONG_AGO,
    )


def on(day: int) -> date:
    return FIRST + timedelta(days=day)


def anchor(
    pay: PayBasis, n: int, *steps: CohortStep, late: ApprovedSession
) -> LineDraft:
    draft = course_anchor_draft(pay, paid(), CohortWalk(FIRST, n, steps), late)
    assert draft is not None
    return draft


def lines_of(pay: PayBasis, n: int, *steps: CohortStep) -> list[LineDraft]:
    return walk_cohort(
        pay,
        paid(),
        CohortWalk(FIRST, n, in_walk_order(steps)),
        starts_on=on(-1000),
        ends_on=on(1000),
    )


def as_fixed(draft: LineDraft, rate: str = "1000") -> FinalizedCourseLine:
    assert draft.class_session_id is not None
    assert draft.cumulative_sessions is not None and draft.cohort_sessions is not None
    return FinalizedCourseLine(
        draft.class_session_id,
        draft.cumulative_sessions,
        draft.cohort_sessions,
        Rate(rate),
    )


# --- The walk order (A3) -----


def test_fixed_steps_walk_by_k_in_the_places_fixed_steps_hold():
    before = course(on(-7))
    late_anchor = fixed(2, 4)  # session 1, anchored after session 2 finalized
    line = fixed(1, 4)  # session 2
    after = course(on(20))

    ordered = in_walk_order([before, late_anchor, line, after])

    assert ordered == [before, line, late_anchor, after]


def test_an_anchor_feeds_the_peak_like_a_finalized_line():
    # Session 2 finalized at 1 of 4 (250.00); session 1 anchored at 2 of 4 (250.00).
    s3, s4 = course(on(14)), course(on(16))

    drafts = lines_of(basis(rates=[per_course()]), 4, fixed(2, 4), fixed(1, 4), s3, s4)

    assert [(d.cumulative_sessions, d.amount) for d in drafts] == [
        (3, Money("250.00")),
        (4, Money("250.00")),
    ]


def test_a_date_ordered_walk_would_pay_750_on_1000():
    """The mutant of the order: the anchor read by date, before the line with the
    lower ``k``, resets ``k`` to 1 after it."""
    s3, s4 = course(on(14)), course(on(16))
    pay = basis(rates=[per_course()])
    by_date = walk_cohort(
        pay,
        paid(),
        CohortWalk(FIRST, 4, [fixed(2, 4), fixed(1, 4), s3, s4]),
        starts_on=on(-1000),
        ends_on=on(1000),
    )
    assert Money.total(d.amount for d in by_date) == Money("250.00")


# --- The anchor's amount (E1, A2) -----


def test_the_first_late_session_with_nothing_fixed_is_one_share():
    late = course(on(0))
    draft = anchor(basis(rates=[per_course()]), 4, late=late)
    assert (draft.kind, draft.cumulative_sessions, draft.cohort_sessions) == (
        "session",
        1,
        4,
    )
    assert (draft.rate_model, draft.amount) == (PER_COURSE, Money("250.00"))


def test_k_is_one_after_the_highest_fixed_k_and_the_peak_is_their_maximum():
    late = course(on(0))
    draft = anchor(basis(rates=[per_course()]), 5, fixed(1, 4), fixed(2, 4), late=late)
    # K = 2, peak = 1000 x 2 / 4 = 500.00; 1000 x 3 / 5 = 600.00.
    assert (draft.cumulative_sessions, draft.amount) == (3, Money("100.00"))


def test_open_steps_later_in_time_dont_count_in_k():
    late = course(on(0))
    open_one = course(on(30))
    draft = anchor(basis(rates=[per_course()]), 4, fixed(1, 4), open_one, late=late)
    assert draft.cumulative_sessions == 2


def test_work_before_the_first_period_counts_in_k_and_in_the_peak():
    """Edge case: ``K`` is read from the walk, not only from stored rows."""
    before = course(on(-14))
    late = course(on(0))
    draft = anchor(basis(rates=[per_course()]), 4, before, late=late)
    # The walk paid nothing for 1 of 4, but its 250.00 is in cum.
    assert (draft.cumulative_sessions, draft.amount) == (2, Money("250.00"))


def test_a_floored_anchor_is_zero_and_the_job_writes_nothing():
    # 1 of 2 finalized (500.00), N now 6: 2 of 6 is 333.33, below the peak.
    late = course(on(0))
    draft = anchor(basis(rates=[per_course()]), 6, fixed(1, 2), late=late)
    assert (draft.cumulative_sessions, draft.amount) == (2, Money("0.00"))


def test_earlier_late_sessions_count_and_the_anchor_catches_their_shares_up():
    """All the remaining sessions are late. Session 2 alone floors at 0.00 (2 of 4
    is 500.00, already paid), so it writes nothing; session 3 counts it, pays 3 of
    4 minus the peak, and session 2 then takes the last ``k``."""
    pay = basis(rates=[per_course()])
    s2, s3 = course(on(2)), course(on(6))
    paid_first = fixed(1, 2)  # 500.00, then N grew to 4

    alone = course_anchor_draft(
        pay, paid(), CohortWalk(FIRST, 4, [paid_first], late=[s2, s3]), s2
    )
    assert alone is not None and alone.amount == Money("0.00")

    third = course_anchor_draft(
        pay, paid(), CohortWalk(FIRST, 4, [paid_first], late=[s2, s3]), s3
    )
    assert third is not None
    assert (third.cumulative_sessions, third.amount) == (3, Money("250.00"))

    second = course_anchor_draft(
        pay,
        paid(),
        CohortWalk(FIRST, 4, [paid_first, as_fixed(third)], late=[s2]),
        s2,
    )
    assert second is not None
    assert (second.cumulative_sessions, second.amount) == (4, Money("250.00"))


def test_k_is_stored_capped_at_n():
    """A session counted as waiting and anchored later can find ``K + 1 > N``:
    it stores ``N`` and pays only what the peak hasn't reached."""
    late = course(on(0))
    draft = anchor(basis(rates=[per_course()]), 4, fixed(4, 4), late=late)
    assert (draft.cumulative_sessions, draft.amount) == (4, Money("0.00"))


def test_no_terms_on_the_date_is_no_anchor():
    terms = [terms_row(), terms_row(on=on(0), frequency=None), terms_row(on=on(1))]
    late = course(on(0))
    assert (
        course_anchor_draft(
            basis(terms=terms, rates=[per_course()]),
            paid(),
            CohortWalk(FIRST, 4, []),
            late,
        )
        is None
    )


@pytest.mark.parametrize(("percent", "amount"), [("50", "125.00"), ("100", "250.00")])
def test_a_late_no_show_pays_its_rule(percent, amount):
    """A5: 50 % of 2 of 4 minus 50 % of 1 of 4, capped at the share; 100 % is the
    share."""
    rules = [rule("no_show", "percent", percent=percent), rule("makeup")]
    late = course(on(0), all_absent=True)
    draft = anchor(basis(rates=[per_course()], rules=rules), 4, fixed(1, 4), late=late)
    assert (draft.kind, draft.amount) == ("no_show", Money(amount))
    assert draft.session_pay_rule_id == rules[0].id


def test_a_late_makeup_pays_its_rule():
    """A5: a course session is never a makeup through the routes (#442 D3,
    ``makeup_target_not_individual``), so the kind is reachable only here."""
    rules = [rule("no_show"), rule("makeup", "percent", percent="100")]
    late = course(on(0), is_makeup=True)
    draft = anchor(basis(rates=[per_course()], rules=rules), 4, late=late)
    assert (draft.kind, draft.amount) == ("makeup", Money("250.00"))


def test_the_rate_snapshot_is_the_first_dates_version():
    versions = [per_course("1000"), per_course("2000", on=on(1))]
    pay = basis(rates=versions)
    first = course_rate(pay, FIRST)
    assert first is not None
    draft = course_anchor_draft(pay, first, CohortWalk(FIRST, 4, []), course(on(7)))
    assert draft is not None
    assert (draft.rate, draft.compensation_rate_id) == (Rate("1000"), versions[0].id)


# --- Edge case: a fixed step beyond N -----


def test_after_a_fixed_step_k_beyond_n_is_floored_and_nothing_is_paid_back():
    # Anchored at 4 of 4 (1000.00); N is 3 now. The next step pays 0.00.
    following = course(on(20))
    [line] = lines_of(basis(rates=[per_course()]), 3, fixed(4, 4), following)
    assert (line.cumulative_sessions, line.amount) == (5, Money("0.00"))


def test_with_no_fixed_step_k_beyond_n_stays_a_bug():
    with pytest.raises(CourseWalkError):
        lines_of(basis(rates=[per_course()]), 1, course(on(1)), course(on(2)))


# --- E2: revisions of a per-course origin -----


def origin(
    kind: str = "session",
    amount: str = "250.00",
    k: int = 2,
    n: int = 4,
    rule_id: uuid.UUID | None = None,
) -> OriginSnapshot:
    return OriginSnapshot(
        PER_COURSE,
        RATE,
        uuid.uuid7(),
        kind=kind,
        line_amount=Money(amount),
        cumulative_sessions=k,
        cohort_sessions=n,
        session_pay_rule_id=rule_id,
    )


def half_rules() -> list[SessionPayRuleVersion]:
    return [rule("no_show", "percent", percent="50"), rule("makeup")]


def test_a_session_revised_to_all_absent_targets_the_rules_amount():
    rules = half_rules()
    draft = course_correction_draft(
        basis(rules=rules), origin(), course(on(0), all_absent=True)
    )
    assert draft is not None
    # 50 % of 2 of 4 (250.00) minus 50 % of 1 of 4 (125.00), capped at 250.00.
    assert (draft.kind, draft.amount) == ("no_show", Money("125.00"))
    assert (draft.cumulative_sessions, draft.cohort_sessions) == (2, 4)
    assert draft.session_pay_rule_id == rules[0].id


def test_back_to_the_origins_kind_targets_the_origins_amount():
    """A floored origin (0.00 at 2 of 6) stays 0.00: its share isn't recomputed."""
    draft = course_correction_draft(
        basis(rules=half_rules()), origin(amount="0.00", n=6), course(on(0))
    )
    assert draft is not None
    assert (draft.kind, draft.amount) == ("session", Money("0.00"))


def test_minutes_only_targets_the_origins_amount():
    draft = course_correction_draft(
        basis(rules=half_rules()), origin(amount="333.34", k=2, n=3), course(on(0))
    )
    assert draft is not None
    assert draft.amount == Money("333.34")


def test_a_no_show_origin_below_100_revised_to_taught_gets_the_formula_share():
    rules = half_rules()
    draft = course_correction_draft(
        basis(rules=rules),
        origin(kind="no_show", amount="125.00", rule_id=rules[0].id),
        course(on(0)),
    )
    assert draft is not None
    # max(0.00, 1000 x 2 / 4 - 1000 x 1 / 4)
    assert (draft.kind, draft.amount) == ("session", Money("250.00"))


def test_a_no_show_origin_at_100_is_a_whole_share():
    rules = [rule("no_show", "percent", percent="100"), rule("makeup")]
    draft = course_correction_draft(
        basis(rules=rules),
        origin(kind="no_show", amount="0.00", n=6, rule_id=rules[0].id),
        course(on(0)),
    )
    assert draft is not None
    assert draft.amount == Money("0.00")


def test_a_floored_session_origin_revised_to_a_no_show_keeps_its_zero_share():
    """A ``session`` origin is a whole share even at 0.00 (2 of 6 under the
    peak): the no-show pays 0.00, not 50 % of a recomputed 166.67."""
    draft = course_correction_draft(
        basis(rules=half_rules()),
        origin(amount="0.00", n=6),
        course(on(0), all_absent=True),
    )
    assert draft is not None
    assert (draft.kind, draft.amount) == ("no_show", Money("0.00"))


def test_a_minutes_only_revision_of_a_floored_no_show_keeps_its_amount():
    """Same kind: the origin's amount, never the rule recomputed on the formula
    share (which would be 125.00 here)."""
    rules = half_rules()
    draft = course_correction_draft(
        basis(rules=rules),
        origin(kind="no_show", amount="0.00", rule_id=rules[0].id),
        course(on(0), all_absent=True),
    )
    assert draft is not None
    assert (draft.kind, draft.amount) == ("no_show", Money("0.00"))
    assert draft.session_pay_rule_id == rules[0].id


def test_an_hourly_origin_isnt_corrected_here():
    snapshot = OriginSnapshot("per_hour", Rate("120"), uuid.uuid7())
    assert course_correction_draft(basis(), snapshot, course(on(0))) is None


def test_the_delete_refusal_names_the_date():
    error = CompensationRateDeleteUnderFinalizedLineError(on(3))
    assert (error.status, error.code) == (
        409,
        "compensation_rate_delete_under_finalized_line",
    )
    assert error.details == {"latest_finalized_on": on(3).isoformat()}


# --- The oracle: random cohorts sum to their rate (A3) -----

PERIOD_DAYS = 4
SESSION_DAYS = 2


@dataclass
class Session:
    id: uuid.UUID
    day: int
    cancelled: bool = False
    approved: bool = False
    absent: bool = False


@dataclass
class Cohort:
    """One tutor's cohort, its periods (``PERIOD_DAYS`` each from day 0) and what
    they fixed. Sessions before day 0 are before the first period."""

    rate: Rate
    sessions: list[Session]
    finalized_through: int = -1
    """The last finalized period's index."""
    lines: dict[uuid.UUID, tuple[FinalizedCourseLine, Money]] = field(
        default_factory=dict
    )
    anchors: dict[uuid.UUID, tuple[FinalizedCourseLine, Money]] = field(
        default_factory=dict
    )
    n_at_first_fixed: int | None = None

    def pay(self) -> tuple[PayBasis, RateInEffect]:
        version = per_course(str(self.rate))
        pay = basis(
            rates=[version],
            rules=[rule("no_show", "percent", percent="100"), rule("makeup")],
        )
        found = course_rate(pay, FIRST)
        assert found is not None
        return pay, found

    def finalized(self, day: int) -> bool:
        return 0 <= day and day // PERIOD_DAYS <= self.finalized_through

    def work(self, session: Session) -> ApprovedSession:
        return course(
            on(session.day), class_session_id=session.id, all_absent=session.absent
        )

    def walk(self) -> CohortWalk:
        steps: list[CohortStep] = []
        for session in sorted(self.sessions, key=lambda s: (s.day, s.id)):
            if session.id in self.lines:
                steps.append(self.lines[session.id][0])
            elif session.id in self.anchors:
                steps.append(self.anchors[session.id][0])
            elif session.approved and not self.finalized(session.day):
                steps.append(self.work(session))
        return CohortWalk(
            FIRST,
            sum(1 for s in self.sessions if not s.cancelled),
            in_walk_order(steps),
            late=[self.work(s) for s in sorted(self.late(), key=lambda s: s.day)],
        )

    def fix(self, draft: LineDraft) -> tuple[FinalizedCourseLine, Money]:
        if self.n_at_first_fixed is None:
            self.n_at_first_fixed = draft.cohort_sessions
        return as_fixed(draft, str(self.rate)), draft.amount

    def finalize_next(self) -> None:
        index = self.finalized_through + 1
        pay, version = self.pay()
        for draft in walk_cohort(
            pay,
            version,
            self.walk(),
            starts_on=on(index * PERIOD_DAYS),
            ends_on=on(index * PERIOD_DAYS + PERIOD_DAYS - 1),
        ):
            assert draft.class_session_id is not None
            self.lines[draft.class_session_id] = self.fix(draft)
        self.finalized_through = index

    def late(self) -> list[Session]:
        return [
            s
            for s in self.sessions
            if s.approved
            and self.finalized(s.day)
            and s.id not in self.lines
            and s.id not in self.anchors
        ]

    def post(self, session: Session) -> bool:
        pay, version = self.pay()
        draft = course_anchor_draft(pay, version, self.walk(), self.work(session))
        if draft is None or draft.amount.is_zero():
            return False  # a floored 0.00 writes nothing and is read again
        self.anchors[session.id] = self.fix(draft)
        return True

    def total(self) -> Money:
        return Money.total(
            amount for _, amount in (*self.lines.values(), *self.anchors.values())
        )


RATES = ("1000", "999.99", "1234.5678", "100.0050", "0.0100", "7")


def random_cohort(rng: random.Random, before_first: int) -> Cohort:
    count = rng.randint(1, 7)
    sessions = [
        Session(uuid.uuid4(), day=(index - before_first) * SESSION_DAYS)
        for index in range(count + before_first)
    ]
    return Cohort(Rate(rng.choice(RATES)), sessions)


def run(rng: random.Random, cohort: Cohort) -> None:
    """Random moves, then everything approved, anchored and finalized."""
    for session in cohort.sessions:
        if session.day < 0:
            session.approved = True  # taught and approved before payroll began
    for _ in range(rng.randint(0, 14)):
        move = rng.random()
        unapproved = [s for s in cohort.sessions if not (s.approved or s.cancelled)]
        if move < 0.35 and unapproved:
            chosen = rng.choice(unapproved)
            chosen.approved, chosen.absent = True, rng.random() < 0.2
        elif move < 0.55:
            cohort.finalize_next()
        elif move < 0.7 and cohort.late():
            cohort.post(rng.choice(cohort.late()))
        elif move < 0.85:  # N up
            last = max(s.day for s in cohort.sessions)
            cohort.sessions.append(Session(uuid.uuid4(), day=last + SESSION_DAYS))
        else:  # N down: a session not yet taught, outside the finalized periods
            last = max(s.day for s in cohort.sessions if not s.cancelled)
            future = [
                s for s in unapproved if not cohort.finalized(s.day) and s.day < last
            ]
            if future:
                rng.choice(future).cancelled = True
    for session in cohort.sessions:
        if not session.cancelled:
            session.approved = True
    last_period = max(s.day for s in cohort.sessions) // PERIOD_DAYS
    while cohort.finalized_through < last_period:
        if cohort.late() and rng.random() < 0.5:
            cohort.post(rng.choice(cohort.late()))
        else:
            cohort.finalize_next()
    for _ in range(len(cohort.sessions)):
        if not any([cohort.post(s) for s in cohort.late()]):
            break


def test_random_cohorts_sum_to_their_rate_exactly():
    rng = random.Random(464)  # noqa: S311 - a fixed seed, not a secret
    mismatches = []
    anchored = 0
    for case in range(4000):
        cohort = random_cohort(rng, before_first=0)
        run(rng, cohort)
        anchored += len(cohort.anchors)
        taught = any(not s.cancelled for s in cohort.sessions)
        if cohort.total() != (cohort.rate.to_money() if taught else Money(0)):
            mismatches.append((case, str(cohort.rate), str(cohort.total())))
    assert mismatches == []
    assert anchored > 1000


def test_random_cohorts_with_work_before_the_first_period_leave_the_item_exact():
    """#109 E4's figure: the walk pays all but ``rate x k0 / N`` at the first
    fixed step, which a one-time item pays (never an anchor, A7)."""
    rng = random.Random(109)  # noqa: S311 - a fixed seed, not a secret
    mismatches = []
    for case in range(2000):
        cohort = random_cohort(rng, before_first=rng.randint(1, 2))
        run(rng, cohort)
        before = [s for s in cohort.sessions if s.day < 0]
        assert not any(s.id in cohort.anchors for s in before)
        if cohort.n_at_first_fixed is None:
            continue  # nothing after the first period was ever paid
        item = cohort.rate.to_money(len(before), cohort.n_at_first_fixed)
        if Money.total([cohort.total(), item]) != cohort.rate.to_money():
            mismatches.append((case, str(cohort.rate), str(cohort.total()), str(item)))
    assert mismatches == []
