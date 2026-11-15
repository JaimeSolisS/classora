"""Academic period and closure bodies (#38 B3, B4, C2): inclusive ranges,
the span limits, the YYYY-MM-DD form, null and unknown fields."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.catalog.schemas import (
    AcademicPeriodCreate,
    AcademicPeriodUpdate,
    ClosureCreate,
    ClosureUpdate,
    range_error,
)

PERIOD = {
    "name": " Ciclo 2026-2027 ",
    "period_type": "school_year",
    "starts_on": "2026-08-01",
    "ends_on": "2027-07-15",
}
CLOSURE = {
    "name": "Navidad",
    "closure_type": "holiday",
    "starts_on": "2026-12-25",
    "ends_on": "2026-12-25",
}


def _locations(error: ValidationError) -> set[str]:
    return {str(e["loc"][0]) for e in error.errors()}


def test_a_period_body_is_parsed_and_trimmed():
    body = AcademicPeriodCreate.model_validate(PERIOD)

    assert (body.name, body.period_type, body.starts_on, body.ends_on) == (
        "Ciclo 2026-2027",
        "school_year",
        date(2026, 8, 1),
        date(2027, 7, 15),
    )


@pytest.mark.parametrize(
    ("model", "base", "max_span"),
    [(AcademicPeriodCreate, PERIOD, 730), (ClosureCreate, CLOSURE, 366)],
)
def test_one_day_and_the_longest_span_are_accepted(model, base, max_span):
    one_day = model.model_validate(
        {**base, "starts_on": "2028-02-29", "ends_on": "2028-02-29"}
    )
    longest = model.model_validate(
        {
            **base,
            "starts_on": "2026-01-01",
            "ends_on": date.fromordinal(date(2026, 1, 1).toordinal() + max_span),
        }
    )

    assert one_day.starts_on == one_day.ends_on
    assert (longest.ends_on - longest.starts_on).days == max_span


@pytest.mark.parametrize(
    ("model", "base", "max_span"),
    [(AcademicPeriodCreate, PERIOD, 730), (ClosureCreate, CLOSURE, 366)],
)
def test_an_end_before_the_start_or_too_long_a_span_fails_on_ends_on(
    model, base, max_span
):
    too_long = date.fromordinal(date(2026, 1, 1).toordinal() + max_span + 1)
    for starts_on, ends_on in [
        ("2026-12-26", "2026-12-25"),
        ("2026-01-01", too_long.isoformat()),
    ]:
        with pytest.raises(ValidationError) as caught:
            model.model_validate({**base, "starts_on": starts_on, "ends_on": ends_on})
        assert _locations(caught.value) == {"ends_on"}


@pytest.mark.parametrize(
    "value",
    ["2026-8-1", "01/08/2026", "2026-08-01T00:00:00", "2026-02-30", 1767225600, ""],
)
@pytest.mark.parametrize("model", [AcademicPeriodCreate, ClosureCreate])
def test_only_real_yyyy_mm_dd_dates_are_accepted(model, value):
    base = PERIOD if model is AcademicPeriodCreate else CLOSURE

    with pytest.raises(ValidationError) as caught:
        model.model_validate({**base, "starts_on": value})
    assert "starts_on" in _locations(caught.value)


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (AcademicPeriodCreate, "period_type", "quarter"),
        (AcademicPeriodCreate, "period_type", "SCHOOL_YEAR"),
        (ClosureCreate, "closure_type", "bridge"),
        (AcademicPeriodCreate, "is_active", False),
        (ClosureCreate, "id", "x"),
    ],
)
def test_unknown_types_and_fields_are_rejected(model, field, value):
    base = PERIOD if model is AcademicPeriodCreate else CLOSURE

    with pytest.raises(ValidationError):
        model.model_validate({**base, field: value})


def test_a_period_update_can_t_change_the_type():
    with pytest.raises(ValidationError):
        AcademicPeriodUpdate.model_validate({"version": 1, "period_type": "term"})


@pytest.mark.parametrize("model", [AcademicPeriodUpdate, ClosureUpdate])
@pytest.mark.parametrize("field", ["name", "starts_on", "ends_on", "is_active"])
def test_update_fields_can_t_be_null(model, field):
    with pytest.raises(ValidationError):
        model.model_validate({"version": 1, field: None})


@pytest.mark.parametrize("model", [AcademicPeriodUpdate, ClosureUpdate])
def test_an_update_checks_the_range_only_when_both_dates_are_sent(model):
    alone: Any = model.model_validate({"version": 1, "ends_on": "2000-01-01"})

    assert alone.model_fields_set == {"version", "ends_on"}
    with pytest.raises(ValidationError) as caught:
        model.model_validate(
            {"version": 1, "starts_on": "2026-12-26", "ends_on": "2026-12-25"}
        )
    assert _locations(caught.value) == {"ends_on"}


@pytest.mark.parametrize("version", [0, None, "x"])
def test_an_update_needs_a_version(version):
    with pytest.raises(ValidationError):
        ClosureUpdate.model_validate({"version": version})


def test_range_error():
    assert range_error(date(2026, 1, 1), date(2026, 1, 1), 0) is None
    assert range_error(date(2026, 1, 2), date(2026, 1, 1), 10) is not None
    assert range_error(date(2026, 1, 1), date(2026, 1, 12), 10) is not None
    assert range_error(date(2026, 1, 1), date(2026, 1, 11), 10) is None
