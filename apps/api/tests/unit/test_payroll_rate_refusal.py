"""The rate refusal for ``course`` (#110 E5, QA D1, D2, D3), and for a delete
(M7-08d E3), against the pricing itself. The oracle asks the real lookups which
version prices a line, with and without the new (or the deleted) version, over
every small layout: the rule must refuse exactly when that answer changes. The
matrix through real routes is in
``tests/integration/test_payroll_workflow_rate_matrix.py``, and for deletes in
``tests/integration/test_payroll_rate_delete_refusal.py``."""

import itertools
import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from app.core.money import Rate
from app.modules.payroll.calculation import (
    COURSE,
    PER_COURSE,
    FinalizedPricedLine,
    PayBasis,
    RateVersion,
    _rate_in_effect,
    course_rate,
    repriced_by_delete,
    repriced_on,
)

DAY0 = date(2026, 3, 2)
MODELS = ("per_course", "per_hour", "per_session", None)
RATES = (Rate(100), Rate("100.0000"), Rate(200))
"""``100`` twice, as different text of one value: rates compare by value."""
KINDS = [(m, r) for m in MODELS if m is not None for r in (Rate(100), Rate(200))]
NEW = [*((m, r) for m in MODELS if m is not None for r in RATES), (None, None)]
DAYS = range(6)
SESSION = uuid.UUID(int=1)


def on(day: int) -> date:
    return DAY0 + timedelta(days=day)


def version(day: int, model: str | None, rate: Rate | None = None) -> RateVersion:
    return RateVersion(
        id=uuid.uuid4(),
        class_kind=COURSE,
        effective_from=on(day),
        rate_model=model,
        rate=None if model is None else (Rate(100) if rate is None else rate),
    )


def priced_by(rates: list[RateVersion], first: date, own: date) -> tuple | None:
    """The price of a line of this session, by the calculation's own lookups: the
    model and the rate, not the version's id (QA D4)."""
    basis = PayBasis(terms=(), rates=rates, rules=())
    paid = course_rate(basis, first)
    if paid is not None:
        return (PER_COURSE, paid.rate)
    ordinary = _rate_in_effect(basis, COURSE, own)
    if ordinary is None or ordinary.rate_model == PER_COURSE:
        return None
    return (ordinary.rate_model, ordinary.rate)


def until(rates: list[RateVersion], starts: date) -> date | None:
    later = [v.effective_from for v in rates if v.effective_from > starts]
    return min(later, default=None)


def layouts() -> list[tuple[int, int, list[RateVersion]]]:
    found = []
    for first, own in itertools.combinations_with_replacement(DAYS, 2):
        for count in (1, 2):
            for days in itertools.combinations(DAYS, count):
                for kinds in itertools.product([*KINDS, (None, None)], repeat=count):
                    rates = [
                        version(d, m, r) for d, (m, r) in zip(days, kinds, strict=True)
                    ]
                    found.append((first, own, rates))
    return found


def test_the_rule_refuses_exactly_what_changes_the_price():
    cells = 0
    for first, own, rates in layouts():
        before = priced_by(rates, on(first), on(own))
        if before is None:
            continue  # no such line: it can't have been finalized
        line = FinalizedPricedLine(SESSION, on(own), before[0], before[1])
        taken_days = {v.effective_from for v in rates}
        for day, (model, rate) in itertools.product(range(-1, 7), NEW):
            starts = on(day)
            if starts in taken_days:
                continue  # 409 compensation_rate_exists comes first
            after = priced_by([*rates, version(day, model, rate)], on(first), on(own))
            answer = repriced_on(
                line,
                first_session_date=on(first),
                new_model=model,
                new_rate=rate,
                effective_from=starts,
                until=until(rates, starts),
            )
            cells += 1
            assert (answer is not None) == (after != before), (
                first,
                own,
                [(v.effective_from, v.rate_model) for v in rates],
                day,
                model,
                rate,
            )
    assert cells > 100_000


def test_a_version_after_the_answer_clears_the_refusal():
    """D3: ``latest_finalized_on`` is the date to go past, unless that day is the
    next version's, when nothing in the window is free."""
    cleared = 0
    for first, own, rates in layouts():
        before = priced_by(rates, on(first), on(own))
        if before is None:
            continue
        line = FinalizedPricedLine(SESSION, on(own), before[0], before[1])
        for day, (model, rate) in itertools.product(range(-1, 7), NEW):
            starts = on(day)
            if any(v.effective_from == starts for v in rates):
                continue
            end = until(rates, starts)

            args: dict[str, Any] = {
                "first_session_date": on(first),
                "new_model": model,
                "new_rate": rate,
                "until": end,
            }
            latest = repriced_on(line, effective_from=starts, **args)
            if latest is None or latest + timedelta(days=1) == end:
                continue
            past = latest + timedelta(days=1)
            assert repriced_on(line, effective_from=past, **args) is None
            cleared += 1
    assert cleared > 10_000


FIRST = date(2026, 3, 2)
OWN = date(2026, 3, 20)


@pytest.mark.parametrize("model", MODELS)
def test_the_newest_date_comes_first_on_an_ordinary_line(model):
    line = FinalizedPricedLine(SESSION, OWN, "per_hour", Rate(100))

    answer = repriced_on(
        line,
        first_session_date=FIRST,
        new_model=model,
        new_rate=None if model is None else Rate(150),
        effective_from=FIRST,
        until=None,
    )

    assert answer == OWN


@pytest.mark.parametrize(
    ("model", "rate", "refused"),
    [
        ("per_course", Rate("1000"), False),
        ("per_course", Rate("1000.0000"), False),
        ("per_course", Rate("1000.0001"), True),
        ("per_course", Rate("999.9999"), True),
        ("per_hour", Rate("1000"), True),
        ("per_session", Rate("1000"), True),
        (None, None, True),
    ],
)
def test_only_the_lines_own_model_and_exact_rate_pass(model, rate, refused):
    """QA D4 ruling (a): exact value, any other model or rate keeps the rule."""
    line = FinalizedPricedLine(SESSION, OWN, "per_course", Rate(1000))

    answer = repriced_on(
        line,
        first_session_date=FIRST,
        new_model=model,
        new_rate=rate,
        effective_from=FIRST,
        until=None,
    )

    assert (answer is not None) is refused


# --- M7-08d E3: a delete runs the add's refusal -----

DELETE_KINDS = [
    ("per_course", Rate(100)),
    ("per_course", Rate(200)),
    ("per_hour", Rate(100)),
    ("per_hour", Rate(200)),
    ("per_session", Rate(100)),
    (None, None),
]


def delete_layouts() -> list[tuple[int, int, list[RateVersion]]]:
    """Up to three versions, so a deleted one can have a previous and a next."""
    found = []
    for first, own in itertools.combinations_with_replacement(DAYS, 2):
        for count in (1, 2, 3):
            for days in itertools.combinations(DAYS, count):
                for kinds in itertools.product(DELETE_KINDS, repeat=count):
                    found.append(
                        (
                            first,
                            own,
                            [
                                version(d, m, r)
                                for d, (m, r) in zip(days, kinds, strict=True)
                            ],
                        )
                    )
    return found


def pricing_version(
    rates: list[RateVersion], first: date, own: date, model: str
) -> RateVersion | None:
    """The version a line names: the first date's for a per-course line, its
    own date's for an ordinary one."""
    on_day = first if model == PER_COURSE else own
    earlier = [v for v in rates if v.effective_from <= on_day]
    return max(earlier, key=lambda v: v.effective_from, default=None)


def test_a_delete_refuses_exactly_what_changes_the_price():
    """Every deleted version of every small layout: refused exactly when the
    line's price without it differs. A version the line names is ``409
    compensation_rate_in_use`` first (its foreign key), so those cells are left
    out, as the route leaves them out of the check."""
    cells = refused = 0
    for first, own, rates in delete_layouts():
        before = priced_by(rates, on(first), on(own))
        if before is None:
            continue
        line = FinalizedPricedLine(SESSION, on(own), before[0], before[1])
        named = pricing_version(rates, on(first), on(own), before[0])
        ordered = sorted(rates, key=lambda v: v.effective_from)
        for index, deleted in enumerate(ordered):
            if deleted is named:
                continue
            rest = [v for v in ordered if v is not deleted]
            after = priced_by(rest, on(first), on(own))
            answer = repriced_by_delete(
                line,
                first_session_date=on(first),
                previous=ordered[index - 1] if index else None,
                deleted_from=deleted.effective_from,
                until=(
                    ordered[index + 1].effective_from
                    if index + 1 < len(ordered)
                    else None
                ),
            )
            cells += 1
            refused += answer is not None
            assert (answer is not None) == (after != before), (
                first,
                own,
                [(v.effective_from, v.rate_model, v.rate) for v in ordered],
                index,
            )
    assert cells > 100_000
    assert refused > 1000  # a version the line names is in_use, so most pass


def test_a_same_rate_previous_version_lets_the_delete_through():
    """#110 D4 for a delete: the window goes to a version of the line's own
    model and rate, so nothing changes."""
    line = FinalizedPricedLine(SESSION, OWN, "per_course", Rate(1000))
    previous = version(-10, "per_course", Rate("1000.0000"))

    assert (
        repriced_by_delete(
            line,
            first_session_date=FIRST,
            previous=previous,
            deleted_from=FIRST - timedelta(days=1),
            until=None,
        )
        is None
    )
    assert repriced_by_delete(
        line,
        first_session_date=FIRST,
        previous=version(-10, "per_course", Rate("1000.0001")),
        deleted_from=FIRST - timedelta(days=1),
        until=None,
    ) == (FIRST)


def test_deleting_the_first_version_gives_the_window_to_nothing():
    line = FinalizedPricedLine(SESSION, OWN, "per_hour", Rate(100))
    assert (
        repriced_by_delete(
            line,
            first_session_date=FIRST,
            previous=None,
            deleted_from=FIRST,
            until=None,
        )
        == OWN
    )
