"""The payroll calendar (#106 C, F, J1): natural periods, the next period and
the terms refusals, as pure functions."""

from collections.abc import Sequence
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.modules.payroll.periods import (
    PeriodSpan,
    TermsVersion,
    natural_bounds,
    next_period,
    terms_refusal,
)
from app.modules.payroll.schemas import PayrollSettingsUpdate

pytestmark = pytest.mark.unit

ANCHOR = date(2026, 1, 5)
FLOOR = date(2026, 11, 4)
FAR = date(2040, 1, 1)
FREQUENCIES = ("weekly", "biweekly", "monthly")
INSIDE = "inside_generated_period"
BEFORE = "before_first_period"


def d(text: str) -> date:
    """``"11-04"`` is in 2026."""
    return date.fromisoformat(text if len(text) > 5 else f"2026-{text}")


def terms(*versions: tuple[str, str | None]) -> list[TermsVersion]:
    return [TermsVersion(d(on), frequency) for on, frequency in versions]


def span(starts: str, ends: str, frequency: str, first: bool = False) -> PeriodSpan:
    return PeriodSpan(d(starts), d(ends), frequency, first)


def chain(
    versions: Sequence[TermsVersion],
    *,
    previous: date | None = None,
    floor: date = FLOOR,
    today: date = FAR,
    limit: int = 500,
) -> list[PeriodSpan]:
    """Every period ``next_period`` opens, up to ``limit``."""
    found: list[PeriodSpan] = []
    last = previous
    while len(found) < limit:
        period = next_period(
            previous_ends_on=last,
            terms=versions,
            floor=floor,
            anchor=ANCHOR,
            today=today,
        )
        if period is None:
            break
        found.append(period)
        last = period.ends_on
    return found


# --- C3: the worked examples ---------------------------------------------------------


def test_weekly_from_a_wednesday_floor():
    assert chain(terms(("11-01", "weekly")), limit=2) == [
        span("11-04", "11-08", "weekly", True),
        span("11-09", "11-15", "weekly"),
    ]


def test_biweekly_from_a_wednesday_floor():
    assert chain(terms(("11-01", "biweekly")), limit=2) == [
        span("11-04", "11-08", "biweekly", True),
        span("11-09", "11-22", "biweekly"),
    ]


def test_a_biweekly_block_before_the_anchor():
    assert natural_bounds("biweekly", d("2026-01-04"), ANCHOR) == (
        d("2025-12-22"),
        d("2026-01-04"),
    )


def test_monthly_from_a_wednesday_floor():
    assert chain(terms(("11-01", "monthly")), limit=3) == [
        span("11-04", "11-30", "monthly", True),
        span("12-01", "12-31", "monthly"),
        span("2027-01-01", "2027-01-31", "monthly"),
    ]
    assert natural_bounds("monthly", d("2028-02-10"), ANCHOR) == (
        d("2028-02-01"),
        d("2028-02-29"),
    )


def test_a_stop_recorded_first_cuts_a_stub_and_nothing_follows():
    versions = terms(("10-01", "monthly"), ("11-20", None))

    assert chain(versions, floor=d("11-01")) == [
        span("11-01", "11-19", "monthly", True)
    ]


def test_a_change_recorded_first_cuts_a_stub():
    versions = terms(("10-01", "monthly"), ("11-15", "weekly"))

    assert chain(versions, floor=d("11-01"), limit=3) == [
        span("11-01", "11-14", "monthly", True),
        span("11-15", "11-15", "weekly"),
        span("11-16", "11-22", "weekly"),
    ]


def test_the_returning_period_is_one_long_period():
    versions = terms(("01-01", "monthly"), ("04-01", None), ("11-10", "weekly"))

    assert chain(versions, previous=d("03-31"), limit=1) == [
        span("04-01", "11-15", "weekly")
    ]


# --- Every day: natural_bounds -------------------------------------------------------


@pytest.mark.parametrize("frequency", FREQUENCIES)
@pytest.mark.parametrize("anchor", [ANCHOR, date(2026, 1, 12), date(2031, 6, 2)])
def test_every_day_is_in_its_natural_period(frequency, anchor):
    day = date(2024, 1, 1)
    block: tuple[date, date] | None = None
    while day <= date(2030, 12, 31):
        start, end = natural_bounds(frequency, day, anchor)
        assert start <= day <= end
        if frequency == "weekly":
            assert (start.isoweekday(), (end - start).days) == (1, 6)
        elif frequency == "biweekly":
            assert (start.isoweekday(), (end - start).days) == (1, 13)
            assert (start - anchor).days % 14 == 0
        else:
            assert start.day == 1
            assert (end + timedelta(days=1)).day == 1
            assert (start.year, start.month) == (end.year, end.month)
        if block is not None and day > block[1]:
            assert start == block[1] + timedelta(days=1)
        else:
            assert block is None or (start, end) == block
        block = (start, end)
        day += timedelta(days=1)


def test_an_unknown_frequency_is_a_bug():
    with pytest.raises(ValueError, match="daily"):
        natural_bounds("daily", ANCHOR, ANCHOR)


# --- Chains --------------------------------------------------------------------------


def assert_contiguous(periods: Sequence[PeriodSpan]) -> None:
    assert periods[0].is_first_period
    assert not any(p.is_first_period for p in periods[1:])
    for period in periods:
        assert period.starts_on <= period.ends_on
    for before, after in zip(periods, periods[1:], strict=False):
        assert after.starts_on == before.ends_on + timedelta(days=1)


@pytest.mark.parametrize(
    "versions",
    [
        terms(("2025-01-01", "weekly")),
        terms(("2025-01-01", "biweekly")),
        terms(("2025-01-01", "monthly")),
        terms(("2025-01-01", "weekly"), ("11-18", "monthly"), ("2027-02-10", "weekly")),
        terms(("2025-01-01", "biweekly"), ("12-02", "weekly"), ("2027-03-03", None)),
        terms(("11-10", "monthly"), ("12-15", None), ("2027-02-17", "biweekly")),
        terms(("2025-01-01", "weekly"), ("11-11", None), ("11-13", "weekly")),
    ],
)
def test_a_chain_has_no_gap_and_no_overlap(versions):
    periods = chain(versions, today=d("2027-12-31"))

    assert_contiguous(periods)
    assert terms_refusal(versions, periods, FLOOR) is None


def test_a_chain_stays_inside_the_natural_calendar_when_it_has_no_change():
    for period in chain(terms(("2025-01-01", "biweekly")), limit=60)[1:]:
        assert natural_bounds("biweekly", period.starts_on, ANCHOR) == (
            period.starts_on,
            period.ends_on,
        )


# --- next_period: the edges ----------------------------------------------------------


def next_of(versions: Sequence[TermsVersion], **kwargs: object) -> PeriodSpan | None:
    arguments: dict[str, object] = {
        "previous_ends_on": None,
        "floor": FLOOR,
        "anchor": ANCHOR,
        "today": FAR,
        **kwargs,
    }
    return next_period(terms=versions, **arguments)  # type: ignore[arg-type]


def test_no_terms_opens_nothing():
    assert next_of([]) is None


def test_first_terms_in_the_future_wait_for_their_date():
    versions = terms(("11-20", "weekly"))

    assert next_of(versions, today=d("11-19")) is None
    assert next_of(versions, today=d("11-20")) == span("11-20", "11-22", "weekly", True)


def test_terms_stopped_before_the_floor_open_nothing():
    assert next_of(terms(("01-01", "weekly"), ("10-01", None))) is None


def test_a_floor_in_the_future_waits():
    assert next_of(terms(("01-01", "weekly")), today=d("11-03")) is None


def test_a_period_that_contains_today_opens_on_its_first_day():
    versions = terms(("01-01", "monthly"))

    assert next_of(versions, today=d("12-01"), previous_ends_on=d("11-30")) == span(
        "12-01", "12-31", "monthly"
    )
    assert next_of(versions, today=d("11-30"), previous_ends_on=d("11-30")) is None


def test_a_stop_on_the_next_periods_first_day_opens_nothing():
    versions = terms(("01-01", "monthly"), ("12-01", None))

    assert next_of(versions, previous_ends_on=d("11-30")) is None


def test_a_return_waits_for_its_covered_date():
    versions = terms(("01-01", "monthly"), ("04-01", None), ("11-10", "weekly"))

    assert next_of(versions, previous_ends_on=d("03-31"), today=d("11-09")) is None


def test_a_change_on_a_calendar_boundary_cuts_no_stub():
    versions = terms(("01-01", "monthly"), ("12-01", "weekly"))

    assert next_of(versions, previous_ends_on=d("10-31")) == span(
        "11-01", "11-30", "monthly"
    )


def test_a_salary_only_version_does_not_split():
    versions = terms(("01-01", "monthly"), ("11-15", "monthly"), ("11-20", "monthly"))

    assert next_of(versions, previous_ends_on=d("10-31")) == span(
        "11-01", "11-30", "monthly"
    )


def test_only_the_first_different_version_cuts():
    versions = terms(
        ("01-01", "monthly"), ("11-15", "monthly"), ("11-20", "weekly"), ("11-25", None)
    )

    assert next_of(versions, previous_ends_on=d("10-31")) == span(
        "11-01", "11-19", "monthly"
    )


def test_a_stub_followed_by_a_deleted_stop_leaves_two_stubs():
    # The stop cut 11-19. Once it is deleted, the next period reaches the natural
    # end, so one natural month has two periods, and #108 prorates both.
    assert next_of(terms(("01-01", "monthly")), previous_ends_on=d("11-19")) == span(
        "11-20", "11-30", "monthly"
    )


def test_the_order_of_the_versions_does_not_matter():
    versions = terms(("11-15", "weekly"), ("01-01", "monthly"))

    assert next_of(versions, previous_ends_on=d("10-31")) == span(
        "11-01", "11-14", "monthly"
    )


# --- F6: the refusals, on the pure function ------------------------------------------

MONTH = [span("11-01", "11-30", "monthly", True)]


@pytest.mark.parametrize(
    ("after", "periods", "floor", "refusal"),
    [
        # A stop, its deletion, and a salary-only version.
        (terms(("11-01", "monthly"), ("11-15", None)), MONTH, "11-01", None),
        (terms(("11-01", "monthly")), MONTH, "11-01", None),
        (terms(("11-01", "monthly"), ("11-15", "monthly")), MONTH, "11-01", None),
        # A change inside, and the same change after the period.
        (terms(("11-01", "monthly"), ("11-15", "weekly")), MONTH, "11-01", INSIDE),
        (terms(("11-01", "monthly"), ("12-01", "weekly")), MONTH, "11-01", None),
        # Returns.
        (
            terms(("11-01", "monthly"), ("11-15", None), ("11-25", "monthly")),
            MONTH,
            "11-01",
            None,
        ),
        (
            terms(("11-01", "monthly"), ("11-15", None), ("11-25", "weekly")),
            MONTH,
            "11-01",
            INSIDE,
        ),
        # Deleting the stop exposes monthly inside a weekly period.
        (
            terms(("11-01", "monthly"), ("12-07", "weekly")),
            [*MONTH, span("12-01", "12-13", "weekly")],
            "11-01",
            INSIDE,
        ),
        # Back-dated before the first period.
        (
            terms(("10-01", "weekly"), ("11-05", "weekly")),
            [span("11-05", "11-08", "weekly", True)],
            "10-01",
            BEFORE,
        ),
        (
            terms(("09-01", "weekly"), ("11-05", "weekly")),
            [span("11-05", "11-08", "weekly", True)],
            "10-15",
            BEFORE,
        ),
        # Before the floor is fine: that work is never paid (ADR-0017 E2).
        (
            terms(("08-01", "weekly"), ("09-01", "weekly")),
            [span("11-05", "11-08", "weekly", True)],
            "11-05",
            None,
        ),
        # The way out of no_pay_terms: a same-frequency version in a gap.
        (
            terms(("11-01", "monthly"), ("11-15", None), ("11-20", "monthly")),
            MONTH,
            "11-01",
            None,
        ),
        # No periods: nothing applies.
        (terms(("11-01", "monthly"), ("11-15", "weekly")), [], "11-01", None),
    ],
)
def test_the_refusals(after, periods, floor, refusal):
    assert terms_refusal(after, periods, d(floor)) == refusal


def test_a_stop_inside_the_window_before_the_first_period_is_not_covered():
    versions = terms(("09-01", "weekly"), ("10-01", None), ("11-05", "weekly"))

    assert (
        terms_refusal(versions, [span("11-05", "11-08", "weekly", True)], d("10-15"))
        is None
    )


def test_a_version_on_a_periods_last_day_counts_and_the_next_day_does_not():
    assert (
        terms_refusal(terms(("11-01", "monthly"), ("11-30", "weekly")), MONTH, FLOOR)
        == INSIDE
    )
    assert (
        terms_refusal(terms(("11-01", "monthly"), ("12-01", "weekly")), MONTH, FLOOR)
        is None
    )


def test_the_day_before_the_first_period_counts():
    versions = terms(("11-04", "weekly"), ("11-05", "weekly"))

    assert (
        terms_refusal(versions, [span("11-05", "11-08", "weekly", True)], d("10-01"))
        == BEFORE
    )


# --- B2: the settings body -----------------------------------------------------------


def body(**overrides: object) -> dict[str, object]:
    return {
        "version": 1,
        "payroll_starts_on": "2026-11-04",
        "biweekly_anchor_on": "2026-01-05",
        "salary_proration_basis": "weekdays",
        **overrides,
    }


def codes(payload: dict[str, object]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        PayrollSettingsUpdate.model_validate(payload)
    return [error["type"] for error in caught.value.errors()]


def test_a_valid_body_and_a_null_floor():
    assert PayrollSettingsUpdate.model_validate(body()).payroll_starts_on == FLOOR
    assert (
        PayrollSettingsUpdate.model_validate(
            body(payroll_starts_on=None)
        ).payroll_starts_on
        is None
    )


def test_the_refused_bodies():
    assert codes(body(biweekly_anchor_on="2026-01-06")) == ["anchor_not_monday"]
    assert codes(body(biweekly_anchor_on="1999-12-27")) == ["date_out_of_range"]
    assert codes(body(payroll_starts_on="1999-12-31")) == ["date_out_of_range"]
    assert codes(body(payroll_starts_on="2100-01-01")) == ["date_out_of_range"]
    assert codes(body(salary_proration_basis="hours")) == ["literal_error"]
    assert codes(body(extra=1)) == ["extra_forbidden"]
    payload = body()
    del payload["biweekly_anchor_on"]
    assert codes(payload) == ["missing"]
