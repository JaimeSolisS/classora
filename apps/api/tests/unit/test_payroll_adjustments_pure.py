"""The pure rules of work after lock (#111 E2, E4, D4, G2).

E4 is one helper behind both the job (:func:`late_approval_draft`) and the
Unresolved list (:func:`unresolved_reason`): each case below is fed to both, so
a payment and a listing can't disagree (the #443 A1 pattern).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.money import Money, Rate
from app.modules.payroll.calculation import (
    ApprovedSession,
    OriginSnapshot,
    PayableCancellation,
    PayBasis,
    RateVersion,
    SalaryCover,
    TermsBasisVersion,
    UnresolvedCandidate,
    correction_draft,
    late_approval_draft,
    owed,
    unresolved_reason,
)
from app.modules.payroll.periods import TermsVersion, adjustment_period
from tests.unit.test_payroll_calculation_pure import rule, seeds

DAY = date(2026, 11, 4)
LONG_AGO = date(2000, 1, 1)
FINALIZED_AT = datetime(2026, 11, 10, 12, tzinfo=UTC)
BEFORE = FINALIZED_AT - timedelta(days=1)
AFTER = FINALIZED_AT + timedelta(seconds=1)
COVER = SalaryCover(date(2026, 11, 2), date(2026, 11, 8), FINALIZED_AT)


def terms(
    *,
    salary: str | None = "3000.00",
    frequency: str | None = "weekly",
    on: date = LONG_AGO,
) -> TermsBasisVersion:
    return TermsBasisVersion(
        id=uuid.uuid7(),
        effective_from=on,
        pay_frequency=frequency,
        salary=None if salary is None else Money(salary),
    )


def rate(
    created_at: datetime | None, *, on: date = LONG_AGO, value: str = "120"
) -> RateVersion:
    return RateVersion(
        id=uuid.uuid7(),
        class_kind="individual",
        effective_from=on,
        rate_model="per_hour",
        rate=Rate(value),
        created_at=created_at,
    )


def work(minutes: int = 60, *, absent: bool = False) -> ApprovedSession:
    return ApprovedSession(
        approval_id=uuid.uuid7(),
        class_session_id=uuid.uuid7(),
        class_kind="individual",
        service_date=DAY,
        approved_minutes=minutes,
        all_absent=absent,
        is_makeup=False,
    )


def candidate(item: ApprovedSession) -> UnresolvedCandidate:
    return UnresolvedCandidate(
        class_session_id=item.class_session_id,
        tutor_user_id=uuid.uuid7(),
        class_kind=item.class_kind,
        service_date=item.service_date,
        source="closeout",
    )


def both(basis: PayBasis) -> tuple[str | None, str | None]:
    """The job's amount (``None``: no draft) and the list's reason."""
    item = work()
    draft = late_approval_draft(basis, item)
    return (
        None if draft is None else str(draft.amount),
        unresolved_reason(basis, candidate(item), LONG_AGO),
    )


# (terms, rates, covers) -> (job amount, list reason)
E4_CASES = [
    pytest.param(
        [terms()],
        [rate(AFTER, on=DAY)],
        [COVER],
        ("0.00", None),
        id="rate-after-finalize-is-ignored",
    ),
    pytest.param(
        [terms()], [rate(BEFORE)], [COVER], ("120.00", None), id="rate-at-finalize-pays"
    ),
    pytest.param(
        [terms(), terms(salary=None, frequency=None, on=DAY)],
        [],
        [COVER],
        ("0.00", None),
        id="late-stop-stays-covered",
    ),
    pytest.param([terms()], [], [COVER], ("0.00", None), id="covered"),
    pytest.param(
        [terms()],
        [rate(AFTER, on=DAY)],
        [],
        ("120.00", None),
        id="no-cover-current-basis",
    ),
    pytest.param(
        [terms(salary=None)], [], [], (None, "no_rate"), id="no-cover-no-rate"
    ),
    pytest.param(
        [terms(salary=None)],
        [rate(AFTER, on=DAY)],
        [],
        ("120.00", None),
        id="no-cover-back-dated-rate-pays",
    ),
]


@pytest.mark.parametrize(("terms_rows", "rates", "covers", "expected"), E4_CASES)
def test_e4_the_job_and_the_list_judge_a_salaried_date_alike(
    terms_rows, rates, covers, expected
):
    basis = PayBasis(terms=terms_rows, rates=rates, rules=seeds(), salary_covers=covers)

    assert both(basis) == expected


def test_e4_a_cover_on_another_date_changes_nothing():
    basis = PayBasis(
        terms=[terms(salary=None)],
        rates=[rate(AFTER, on=DAY)],
        rules=seeds(),
        salary_covers=[
            SalaryCover(date(2026, 10, 1), date(2026, 10, 31), FINALIZED_AT)
        ],
    )

    assert both(basis) == ("120.00", None)


def test_a_late_cancellation_draft_uses_the_rule():
    basis = PayBasis(
        terms=[terms(salary=None)],
        rates=[rate(None)],
        rules=[rule("late_cancellation", "percent", percent="50"), *seeds()[1:]],
    )
    cancelled = PayableCancellation(uuid.uuid7(), "individual", DAY, 60, "late")

    draft = late_approval_draft(basis, cancelled)

    assert draft is not None
    assert (draft.kind, str(draft.amount)) == ("late_cancellation", "60.00")


def test_a_covered_cancellation_has_no_draft():
    basis = PayBasis(
        terms=[terms()],
        rates=[],
        rules=[rule("late_cancellation", "percent", percent="50"), *seeds()[1:]],
    )
    cancelled = PayableCancellation(uuid.uuid7(), "individual", DAY, 60, "late")

    assert late_approval_draft(basis, cancelled) is None


# --- E2: a correction is priced on the origin's snapshot -----


ORIGIN = OriginSnapshot("per_hour", Rate("120"), uuid.uuid7())


def test_e2_a_correction_ignores_the_current_rate_and_terms():
    basis = PayBasis(terms=[], rates=[rate(None, value="999")], rules=seeds())

    draft = correction_draft(basis, ORIGIN, work(75))

    assert draft is not None
    assert (str(draft.amount), draft.compensation_rate_id) == (
        "150.00",
        ORIGIN.compensation_rate_id,
    )


def test_e2_an_event_change_is_priced_with_its_rule():
    basis = PayBasis(
        terms=[],
        rates=[],
        rules=[*seeds()[:1], rule("no_show", "percent", percent="50"), seeds()[2]],
    )

    draft = correction_draft(basis, ORIGIN, work(60, absent=True))

    assert draft is not None
    assert (draft.kind, str(draft.amount)) == ("no_show", "60.00")
    assert draft.session_pay_rule_id is not None


@pytest.mark.parametrize(
    "origin",
    [
        OriginSnapshot(None, None, None),
        OriginSnapshot("per_course", Rate("1000"), uuid.uuid7()),
    ],
    ids=["covered", "per-course"],
)
def test_e2_a_covered_or_per_course_origin_has_no_correction(origin):
    assert correction_draft(PayBasis([], [], seeds()), origin, work()) is None


def test_owed_is_the_difference_and_never_zero():
    assert owed(Money("150.00"), [Money("180.00"), Money("-60.00")]) == Money("30.00")
    assert owed(Money("120.00"), [Money("180.00"), Money("-60.00")]) is None
    assert owed(Money("110.00"), []) == Money("110.00")


# --- D4: a leaver's period -----


def test_d4_a_leavers_period_runs_to_the_natural_end_of_the_last_frequency():
    span = adjustment_period(
        last_ends_on=date(2026, 11, 8),
        last_frequency="weekly",
        terms=[
            TermsVersion(LONG_AGO, "monthly"),
            TermsVersion(date(2026, 11, 1), "weekly"),
            TermsVersion(date(2026, 11, 9), None),
        ],
        anchor=date(2026, 1, 5),
    )

    assert (span.starts_on, span.ends_on, span.pay_frequency) == (
        date(2026, 11, 9),
        date(2026, 11, 15),
        "weekly",
    )
    assert not span.is_first_period


def test_d4_a_later_version_of_another_frequency_ends_it_first():
    span = adjustment_period(
        last_ends_on=date(2026, 10, 31),
        last_frequency="monthly",
        terms=[
            TermsVersion(LONG_AGO, "monthly"),
            TermsVersion(date(2026, 11, 1), None),
            TermsVersion(date(2026, 11, 20), "weekly"),
        ],
        anchor=date(2026, 1, 5),
    )

    assert (span.starts_on, span.ends_on, span.pay_frequency) == (
        date(2026, 11, 1),
        date(2026, 11, 19),
        "monthly",
    )
