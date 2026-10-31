"""The pay rate value type (M7-02 #105 A; ADR-0017 E1)."""

import copy
import decimal
import pickle
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import literal, select
from sqlalchemy.dialects import postgresql

from app.core.money import (
    RATE_MAX,
    RATE_PATTERN,
    Money,
    MoneyRangeError,
    Rate,
    RateRangeError,
    RateTextError,
    RateType,
)


class Body(BaseModel):
    rate: Rate


def code_of(error: ValidationError) -> str:
    return error.errors()[0]["type"]


# --- A1: construction ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("250.125"), "250.1250"),
        (Decimal("1.00000"), "1.0000"),
        (Decimal("1E+2"), "100.0000"),
        (7, "7.0000"),
        ("0", "0.0000"),
        ("-0.0000", "0.0000"),
        ("-1.5", "-1.5000"),
        ("9999999999.9999", "9999999999.9999"),
    ],
)
def test_a_rate_has_exactly_four_places(value, text):
    rate = Rate(value)
    assert str(rate) == text
    assert rate.amount.as_tuple().exponent == -4
    assert rate == Rate(rate)


@pytest.mark.parametrize("value", [1.5, True, False])
def test_a_float_or_bool_is_never_a_rate(value):
    with pytest.raises(TypeError):
        Rate(value)


def test_other_types_are_refused():
    with pytest.raises(TypeError):
        Rate(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Rate(Money("1.00"))  # type: ignore[arg-type]


def test_construction_never_rounds():
    with pytest.raises(ValueError, match="never rounded"):
        Rate(Decimal("1.23456"))
    with pytest.raises(RateTextError) as caught:
        Rate("1.23456")
    assert caught.value.code == "rate_precision"


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_a_non_finite_decimal_is_refused(value):
    with pytest.raises(ValueError, match="finite"):
        Rate(value)


@pytest.mark.parametrize(
    "value",
    [
        Decimal("10000000000"),
        Decimal("1E+1000000"),
        10_000_000_000,
        "10000000000",
        "-10000000000",
    ],
)
def test_the_range_is_numeric_14_4(value):
    with pytest.raises(RateRangeError):
        Rate(value)


def test_rate_max_is_the_limit():
    assert Rate(RATE_MAX).amount == RATE_MAX
    assert Rate(-RATE_MAX).amount == -RATE_MAX


def test_invalid_text_is_refused():
    for text in ("", "abc", "1,5", ".5", "1.", "+1", " 1", "1e3", "١"):
        with pytest.raises(RateTextError) as caught:
            Rate(text)
        assert caught.value.code == "rate_invalid"


# --- A1: strict operators, immutability ----------------------------------------------


def test_a_rate_is_immutable_and_slotted():
    rate = Rate("1.0000")
    with pytest.raises(AttributeError):
        rate._units = 5  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del rate._units
    with pytest.raises(AttributeError):
        rate.other = 1  # type: ignore[attr-defined]
    assert not hasattr(rate, "__dict__")


def test_copies_and_pickles_keep_the_value():
    rate = Rate("12.3456")
    assert copy.copy(rate) is rate
    assert copy.deepcopy(rate) is rate
    assert pickle.loads(pickle.dumps(rate)) == rate  # noqa: S301 - own data


def test_a_rate_has_no_arithmetic():
    rate = Rate("1.0000")
    for operation in (
        lambda: rate + rate,
        lambda: rate - rate,
        lambda: rate * 2,
        lambda: 2 * rate,
        lambda: rate * Decimal("2"),
        lambda: rate / 2,
        lambda: sum([rate]),
        lambda: Money("1.00") * rate,
        lambda: Money("1.00") + rate,
        lambda: -rate,
    ):
        with pytest.raises(TypeError):
            operation()


def test_bool_of_a_rate_raises():
    with pytest.raises(TypeError):
        bool(Rate("1.0000"))


def test_a_rate_compares_with_a_rate_only():
    assert Rate("1.0000") == Rate(1)
    assert Rate("1.0000") < Rate("1.0001") <= Rate("1.0001")
    assert Rate("2") > Rate("1") >= Rate("1")
    assert Rate("1") != Decimal("1.0000")
    assert Rate("1") != 1
    assert Rate("1") != Money("1.00")
    assert len({Rate("1"), Rate("1.0")}) == 1
    for other in (1, Decimal("1"), Money("1.00")):
        with pytest.raises(TypeError):
            Rate("1") < other  # type: ignore[operator]  # noqa: B015
        with pytest.raises(TypeError):
            Rate("1") >= other  # type: ignore[operator]  # noqa: B015


def test_predicates_and_text():
    assert Rate("0").is_zero()
    assert not Rate("0.0001").is_zero()
    assert Rate("-0.0001").is_negative()
    assert not Rate("0").is_negative()
    assert repr(Rate("1.5")) == "Rate('1.5000')"


# --- A2: to_money --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "numerator", "denominator", "expected"),
    [
        ("250.1250", 90, 60, "375.19"),  # 375.1875
        ("250.0000", 1, 1, "250.00"),
        ("0.0050", 1, 1, "0.01"),  # exactly half a centavo
        ("0.0049", 1, 1, "0.00"),
        ("-0.0050", 1, 1, "-0.01"),  # half away from zero
        ("-0.0049", 1, 1, "0.00"),
        ("100.0000", 1, 3, "33.33"),
        ("100.0000", 2, 3, "66.67"),
        ("0.0100", 1, 2, "0.01"),  # 0.005
        ("100.0000", 0, 60, "0.00"),
    ],
)
def test_to_money_rounds_half_away_from_zero_once(
    rate, numerator, denominator, expected
):
    assert Rate(rate).to_money(numerator, denominator) == Money(expected)


def test_to_money_rounds_once_from_the_exact_value():
    # 0.5033. Rounding the rate first to 1.01 would give 0.51.
    assert Rate("1.0066").to_money(1, 2) == Money("0.50")
    assert Rate("1.0066").to_money() == Money("1.01")


@pytest.mark.parametrize(
    "context",
    [
        decimal.Context(prec=2, rounding=ROUND_DOWN),
        decimal.Context(prec=50, rounding=ROUND_HALF_EVEN),
    ],
)
def test_to_money_does_not_depend_on_the_decimal_context(context):
    with decimal.localcontext(context):
        assert Rate("250.1250").to_money(90, 60) == Money("375.19")
        assert str(Rate("250.1250")) == "250.1250"


def test_to_money_stays_in_the_money_range():
    assert Rate("9999999999.9999").to_money() == Money("10000000000.00")
    with pytest.raises(MoneyRangeError):
        Rate("9999999999.9999").to_money(1000, 1)


@pytest.mark.parametrize(
    ("numerator", "denominator", "error"),
    [
        (1, 0, ValueError),
        (1, -1, ValueError),
        (-1, 1, ValueError),
        (1.5, 1, TypeError),
        (1, 1.5, TypeError),
        (True, 1, TypeError),
        (1, Decimal("1"), TypeError),
    ],
)
def test_to_money_arguments(numerator, denominator, error):
    with pytest.raises(error):
        Rate("1").to_money(numerator, denominator)


# --- A3: JSON and the column ---------------------------------------------------------


def test_the_pattern():
    assert RATE_PATTERN == r"^-?\d{1,10}(\.\d{1,4})?$"


def test_json_is_a_string_with_four_places():
    body = Body.model_validate_json('{"rate": "250.125"}')
    assert body.rate == Rate("250.1250")
    assert body.model_dump_json() == '{"rate":"250.1250"}'
    assert body.model_dump()["rate"] == Rate("250.125")
    assert Body.model_validate({"rate": Rate("1")}).rate == Rate("1")
    assert Body.model_validate({"rate": "1"}).rate == Rate("1")


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ('{"rate": 250}', "rate_invalid"),
        ('{"rate": 250.5}', "rate_invalid"),
        ('{"rate": true}', "rate_invalid"),
        ('{"rate": "abc"}', "rate_invalid"),
        ('{"rate": "1.23456"}', "rate_precision"),
        ('{"rate": "12345678901"}', "rate_out_of_range"),
        ('{"rate": "12345678901.23456"}', "rate_precision"),
    ],
)
def test_json_field_codes(raw, code):
    with pytest.raises(ValidationError) as caught:
        Body.model_validate_json(raw)
    assert code_of(caught.value) == code


def test_the_openapi_schema():
    schema = Body.model_json_schema()["properties"]["rate"]
    assert schema["type"] == "string"
    assert schema["format"] == "decimal"
    assert schema["pattern"] == RATE_PATTERN


def test_the_column_binds_a_rate_only():
    column = RateType()
    dialect = postgresql.dialect()
    assert column.process_bind_param(Rate("1.5"), dialect) == Decimal("1.5000")
    assert column.process_bind_param(None, dialect) is None
    bad: list[Any] = [Decimal("1"), 1, 1.5, "1", Money("1.00")]
    for value in bad:
        with pytest.raises(TypeError):
            column.process_bind_param(value, dialect)


def test_the_column_reads_a_rate():
    column = RateType()
    dialect = postgresql.dialect()
    assert column.process_result_value(Decimal("1.5000"), dialect) == Rate("1.5")
    assert column.process_result_value(None, dialect) is None


def test_a_rate_literal_renders():
    statement = select(literal(Rate("1.5"), RateType()))
    text = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "1.5000" in text
