"""The shared money type (M6-02 #87; ADR-0004 and its implementation notes)."""

import copy
import decimal
import pickle
import random
from collections.abc import Callable, Iterator
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import literal, select
from sqlalchemy.dialects import postgresql

from app.core.money import (
    MONEY_MAX,
    MONEY_PATTERN,
    Money,
    MoneyRangeError,
    MoneyType,
    allocate,
    money_sum,
    split,
)

M = Money
PG = postgresql.dialect()


def ms(*values: str) -> list[Money]:
    return [Money(v) for v in values]


# --- A1: immutability ---------------------------------------------------------


def test_money_is_immutable_and_slotted():
    value = M("1.00")
    with pytest.raises(AttributeError):
        value._cents = 5  # type: ignore[misc]
    with pytest.raises(AttributeError):
        value.amount = Decimal("2.00")  # type: ignore[misc]
    with pytest.raises(AttributeError):
        value.currency = "MXN"  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        del value._cents
    assert not hasattr(value, "__dict__")
    assert "__slots__" in vars(Money)
    assert not hasattr(value, "currency")
    assert value == M("1.00")


def test_copies_and_pickles_keep_the_value():
    value = M("12.34")
    assert copy.copy(value) is value
    assert copy.deepcopy(value) is value
    assert pickle.loads(pickle.dumps(value)) == value  # noqa: S301 - own data


# --- A2: never from a float, never from a bool --------------------------------


FLOATS: list[Any] = [1.5, 0.0, float("nan"), float("inf")]
NOT_NUMBERS: list[Any] = [*FLOATS, True, False]


@pytest.mark.parametrize("value", NOT_NUMBERS)
def test_float_and_bool_are_type_errors_everywhere(value):
    with pytest.raises(TypeError):
        Money(value)
    with pytest.raises(TypeError):
        Money.rounded(value)
    with pytest.raises(TypeError):
        M("1.00").multiply(value)
    with pytest.raises(TypeError):
        M("1.00").prorate(value, 2)
    with pytest.raises(TypeError):
        M("1.00").prorate(1, value)
    with pytest.raises(TypeError):
        M("1.00") * value
    with pytest.raises(TypeError):
        value * M("1.00")
    with pytest.raises(TypeError):
        allocate(M("1.00"), [1, value])
    with pytest.raises(TypeError):
        split(M("1.00"), value)


@pytest.mark.parametrize("value", FLOATS)
def test_the_float_message_names_the_rule(value):
    for call in (
        lambda: Money(value),
        lambda: Money.rounded(value),
        lambda: M("1.00").multiply(value),
        lambda: M("1.00") * value,
    ):
        with pytest.raises(TypeError, match="money is never built from a float"):
            call()


@pytest.mark.parametrize("value", [None, [1], b"1", object(), 1j])
def test_other_types_are_type_errors(value):
    with pytest.raises(TypeError):
        Money(value)


# --- A3: text -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        " 12.00",
        "12.00 ",
        "12.00\n",
        "+12",
        "1e3",
        "1,250.00",
        ".5",
        "12.",
        "NaN",
        "Infinity",
        "-",
        "--1",
        "1.234",
        "1.230",
        "1234567890123",
        "١٢",  # non-ASCII digits
        "1_000",
    ],
)
def test_rejected_text(text):
    with pytest.raises(ValueError):
        Money(text)


def test_too_many_integer_digits_is_a_range_error():
    with pytest.raises(MoneyRangeError):
        Money("1234567890123")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12", "12.00"),
        ("0.1", "0.10"),
        ("-0", "0.00"),
        ("-0.00", "0.00"),
        ("007", "7.00"),
        ("-12.5", "-12.50"),
        ("999999999999.99", "999999999999.99"),
        ("-999999999999.99", "-999999999999.99"),
    ],
)
def test_accepted_text(text, expected):
    assert str(Money(text)) == expected


# --- A4: Decimal, int and Money -----------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1.230"), "1.23"),
        (Decimal("1E+3"), "1000.00"),
        (Decimal("-0"), "0.00"),
        (Decimal("0E-30"), "0.00"),
        (Decimal("12.5"), "12.50"),
        (7, "7.00"),
        (-7, "-7.00"),
        (M("4.4"), "4.40"),
    ],
)
def test_accepted_numbers(value, expected):
    assert str(Money(value)) == expected


@pytest.mark.parametrize(
    "value", [Decimal("1.234"), Decimal("0.001"), Decimal("1E-100000")]
)
def test_more_than_two_places_is_never_rounded(value):
    with pytest.raises(ValueError, match="never rounded"):
        Money(value)


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_non_finite_decimals_are_rejected(value):
    with pytest.raises(ValueError):
        Money(value)


def test_money_input_returns_an_equal_value():
    value = M("3.30")
    assert Money(value) == value


# --- A5: range ----------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: Money(10**12),
        lambda: Money(-(10**12)),
        lambda: Money(Decimal("1E+12")),
        lambda: Money(Decimal("1E+100000")),
        lambda: Money("999999999999.99") + Money("0.01"),
        lambda: Money("-999999999999.99") - Money("0.01"),
        lambda: Money("500000000000.00") * 2,
        lambda: 2 * Money("500000000000.00"),
        lambda: Money("999999999999.99").multiply(Decimal("1.001")),
        lambda: Money("999999999999.99").multiply(Decimal("1E+100000")),
        lambda: Money("999999999999.99").prorate(2, 1),
        lambda: Money.total([Money("999999999999.99"), Money("0.01")]),
        lambda: Money.rounded(Decimal("999999999999.995")),
        lambda: Money.rounded(10**12),
    ],
)
def test_out_of_range_results_raise(call):
    with pytest.raises(MoneyRangeError):
        call()


def test_range_error_is_a_value_error_and_the_limits_are_in_range():
    assert issubclass(MoneyRangeError, ValueError)
    assert Money(MONEY_MAX).amount == MONEY_MAX
    assert (-Money(MONEY_MAX)).amount == -MONEY_MAX
    assert Money.rounded(Decimal("999999999999.994")).amount == MONEY_MAX


# --- A6: normalization --------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        Money("-0.00"),
        Money("-0"),
        Money(Decimal("-0.00")),
        Money.rounded(Decimal("-0.004")),
        -Money.zero(),
        Money("1.00") - Money("1.00"),
        Money("-0.01").multiply(Decimal("0.1")),
    ],
)
def test_zero_is_never_negative(value):
    assert value == Money("0.00")
    assert str(value) == "0.00"
    assert value.amount.as_tuple().sign == 0


@pytest.mark.parametrize(
    "value",
    [Money("1.1"), Money(Decimal("1E+3")), Money(5), Money.rounded(Decimal("2.345"))],
)
def test_amount_always_has_two_places(value):
    assert value.amount.as_tuple().exponent == -2


def test_equal_values_have_equal_hashes_and_text():
    assert Money("1.1") == Money("1.10")
    assert hash(Money("1.1")) == hash(Money("1.10"))
    assert len({Money("1.1"), Money("1.10"), Money(Decimal("1.100"))}) == 1
    assert str(Money("1250")) == "1250.00"
    assert repr(Money("1250")) == "Money('1250.00')"
    assert Money.zero() == Money("0.00")


# --- A7: strict operators -----------------------------------------------------


OTHERS: list[Any] = [Decimal("1.00"), 1, 1.0]


@pytest.mark.parametrize("other", OTHERS)
def test_mixing_money_with_numbers_is_a_type_error(other):
    one = Money("1.00")
    for call in (
        lambda: one + other,
        lambda: other + one,
        lambda: one - other,
        lambda: other - one,
        lambda: one < other,
        lambda: one <= other,
        lambda: one > other,
        lambda: one >= other,
        lambda: other < one,
        lambda: other >= one,
    ):
        with pytest.raises(TypeError):
            call()
    assert (one == other) is False
    assert (other == one) is False
    assert one != other


def test_decimal_factors_division_and_sum_are_type_errors():
    one = Money("1.00")
    for call in (
        lambda: one * Decimal("2"),
        lambda: Decimal("2") * one,
        lambda: one / 2,
        lambda: one / one,
        lambda: one // 2,
        lambda: one % 2,
        lambda: divmod(one, 2),
        lambda: 2 / one,
    ):
        with pytest.raises(TypeError):
            call()
    with pytest.raises(TypeError, match=r"Money\.total"):
        sum([Money("1.00"), Money("2.00")])
    with pytest.raises(TypeError, match="multiply"):
        one * Decimal("2")


def test_bool_points_to_is_zero():
    with pytest.raises(TypeError, match="is_zero"):
        bool(Money("0.00"))
    with pytest.raises(TypeError):
        if Money("1.00"):
            pass


def test_exact_arithmetic_and_comparisons():
    assert Money("1.10") + Money("2.25") == Money("3.35")
    assert Money("1.10") - Money("2.25") == Money("-1.15")
    assert -Money("1.10") == Money("-1.10")
    assert +Money("1.10") == Money("1.10")
    assert abs(Money("-1.10")) == Money("1.10")
    assert Money("1.10") * 3 == Money("3.30")
    assert 3 * Money("1.10") == Money("3.30")
    assert Money("1.10") * -2 == Money("-2.20")
    assert Money("1.00") < Money("1.01") <= Money("1.01") < Money("2")
    assert Money("2") > Money("1.99") >= Money("1.99")
    assert sorted(ms("3", "-1", "2")) == ms("-1", "2", "3")
    assert Money.total([]) == Money.zero()
    assert Money.total(iter(ms("1.10", "2.20", "-0.30"))) == Money("3.00")
    with pytest.raises(TypeError):
        Money.total([Money("1.00"), Decimal("1.00")])  # type: ignore[list-item]


def test_predicates():
    assert Money("0.00").is_zero()
    assert not Money("0.01").is_zero()
    assert Money("0.01").is_positive() and not Money("0.01").is_negative()
    assert Money("-0.01").is_negative() and not Money("-0.01").is_positive()
    assert not Money("0").is_positive() and not Money("0").is_negative()


# --- B1-B3: rounding vectors --------------------------------------------------

ROUNDED = [
    ("2.345", "2.35"),
    ("-2.345", "-2.35"),
    ("2.344999", "2.34"),
    ("0.005", "0.01"),
    ("-0.005", "-0.01"),
    ("-0.004", "0.00"),
    ("0.0049999999999999999999999999999", "0.00"),
    ("2.5E-1", "0.25"),
    ("1.5E+2", "150.00"),
    ("12.30000", "12.30"),
]
MULTIPLY = [
    ("199.99", "0.15", "30.00"),
    ("100.00", "0.125", "12.50"),
    ("0.10", "0.05", "0.01"),
    ("-199.99", "0.15", "-30.00"),
    ("-100.00", "0.125", "-12.50"),
    ("-0.10", "0.05", "-0.01"),
    ("199.99", "-0.15", "-30.00"),
    ("1250.00", "1", "1250.00"),
    ("1250.00", "0", "0.00"),
    ("0.01", "1E-100000", "0.00"),
    ("123456789.99", "0.333333333333333333333333333333", "41152263.33"),
]
PRORATE = [
    ("450.00", 90, 60, "675.00"),
    ("100.00", 1, 3, "33.33"),
    ("100.00", 2, 3, "66.67"),
    ("0.05", 1, 2, "0.03"),
    ("-0.05", 1, 2, "-0.03"),
    ("0.15", 1, 2, "0.08"),
    ("1000.00", 5, 8, "625.00"),
    ("1000.00", 0, 8, "0.00"),
    ("999999999999.99", 1, 1, "999999999999.99"),
]


def check_rounding_vectors() -> None:
    for value, expected in ROUNDED:
        assert str(Money.rounded(Decimal(value))) == expected, value
    assert str(Money.rounded(7)) == "7.00"
    assert str(Money.rounded(-7)) == "-7.00"
    for amount, factor, expected in MULTIPLY:
        assert str(Money(amount).multiply(Decimal(factor))) == expected, (
            amount,
            factor,
        )
    assert Money("1.25").multiply(3) == Money("3.75")
    for amount, numerator, denominator, expected in PRORATE:
        result = Money(amount).prorate(numerator, denominator)
        assert str(result) == expected, (amount, numerator, denominator)


def check_allocation_vectors() -> None:
    for (amount, parts_or_weights), expected in SPLIT_AND_ALLOCATE:
        if isinstance(parts_or_weights, int):
            result = split(Money(amount), parts_or_weights)
        else:
            result = allocate(Money(amount), parts_or_weights)
        assert result == ms(*expected), (amount, parts_or_weights)


def test_rounding_vectors():
    check_rounding_vectors()


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("sNaN")],
)
def test_rounding_non_finite_is_a_value_error(value):
    with pytest.raises(ValueError):
        Money.rounded(value)
    with pytest.raises(ValueError):
        Money("1.00").multiply(value)


def test_rounding_other_types_are_type_errors():
    with pytest.raises(TypeError):
        Money.rounded("2.345")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money("1.00").multiply("2")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money("1.00").multiply(Money("2.00"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money("1.00").prorate(Decimal("1"), 2)  # type: ignore[arg-type]


@pytest.mark.parametrize(("numerator", "denominator"), [(1, 0), (1, -3), (-1, 3)])
def test_prorate_bounds(numerator, denominator):
    with pytest.raises(ValueError):
        Money("1.00").prorate(numerator, denominator)


def test_multiply_negative_mirrors_positive():
    rng = random.Random(87)  # noqa: S311 - seeded test data
    for _ in range(2000):
        amount = Money(Decimal(rng.randint(0, 10**11)).scaleb(-2))
        factor = Decimal(rng.randint(0, 10**6)).scaleb(-rng.randint(6, 10))  # 0-1
        assert (-amount).multiply(factor) == -amount.multiply(factor)


# --- B4: context independence -------------------------------------------------


@pytest.fixture(
    params=[
        {"rounding": ROUND_HALF_EVEN, "prec": 5},
        {"rounding": ROUND_DOWN},
    ],
    ids=["half-even-prec-5", "round-down"],
)
def hostile_context(request: pytest.FixtureRequest) -> Iterator[decimal.Context]:
    with decimal.localcontext(**request.param) as context:
        yield context


def test_results_do_not_depend_on_the_context(hostile_context):
    before = (hostile_context.prec, hostile_context.rounding)
    check_rounding_vectors()
    check_allocation_vectors()
    assert Money("999999999999.99") - Money("0.01") == Money("999999999999.98")
    assert str(Money("999999999999.99") - Money("0.01")) == "999999999999.98"
    assert str(Money(Decimal("123456789012.34"))) == "123456789012.34"
    assert Money("123456789012.34").amount == Decimal("123456789012.34")
    assert (decimal.getcontext().prec, decimal.getcontext().rounding) == before


def test_the_module_never_changes_the_context():
    context = decimal.getcontext()
    before = (context.prec, context.rounding, dict(context.traps))
    check_rounding_vectors()
    check_allocation_vectors()
    after = decimal.getcontext()
    assert after is context
    assert (after.prec, after.rounding, dict(after.traps)) == before


# --- C1-C2: allocation --------------------------------------------------------

SPLIT_AND_ALLOCATE: list[tuple[tuple[str, int | list[int]], list[str]]] = [
    (("10.00", 3), ["3.33", "3.34", "3.33"]),
    (("100.00", 6), ["16.67", "16.66", "16.67", "16.67", "16.66", "16.67"]),
    (("0.05", 2), ["0.03", "0.02"]),
    (("-0.05", 2), ["-0.03", "-0.02"]),
    (("0.01", 3), ["0.00", "0.01", "0.00"]),
    (("0.00", 4), ["0.00", "0.00", "0.00", "0.00"]),
    (("1234.56", 1), ["1234.56"]),
    (("-7.77", 1), ["-7.77"]),
    (("1000.00", [3, 0, 7]), ["300.00", "0.00", "700.00"]),
    (("0.10", [1, 1, 1]), ["0.03", "0.04", "0.03"]),
    (("0.05", [1] * 10), ["0.01", "0.00"] * 5),
]


def test_allocation_vectors():
    check_allocation_vectors()


@pytest.mark.parametrize(
    ("weights", "error"),
    [
        ([], ValueError),
        ([0, 0], ValueError),
        ([1, -1, 3], ValueError),
        ([1, True], TypeError),
        ([1, 1.0], TypeError),
        ([1, Decimal("1")], TypeError),
        ([1, "1"], TypeError),
        ("11", TypeError),
        ({1, 2}, TypeError),
        (5, TypeError),
    ],
)
def test_allocate_rejects_bad_weights(weights, error):
    with pytest.raises(error):
        allocate(Money("1.00"), weights)


def test_allocate_takes_money_and_any_int_sequence():
    with pytest.raises(TypeError):
        allocate(Decimal("1.00"), [1])  # type: ignore[arg-type]
    assert allocate(Money("1.00"), (1, 3)) == ms("0.25", "0.75")
    assert allocate(Money("1.00"), range(1, 3)) == ms("0.33", "0.67")


@pytest.mark.parametrize(("parts", "error"), [(0, ValueError), (-1, ValueError)])
def test_split_needs_at_least_one_part(parts, error):
    with pytest.raises(error):
        split(Money("1.00"), parts)


def test_split_rejects_non_int_parts():
    with pytest.raises(TypeError):
        split(Money("1.00"), Decimal("2"))  # type: ignore[arg-type]


# --- C3: allocation properties ------------------------------------------------


def random_cases(count: int = 2000) -> Iterator[tuple[Money, list[int]]]:
    rng = random.Random(87)  # noqa: S311 - seeded test data
    for _ in range(count):
        cents = rng.randint(-(10**11), 10**11)  # +-10^9 MXN
        weights = [
            rng.choice([0, rng.randint(0, 1000)]) for _ in range(rng.randint(1, 50))
        ]
        if sum(weights) == 0:
            weights[rng.randrange(len(weights))] = rng.randint(1, 1000)
        yield Money(Decimal(cents).scaleb(-2)), weights


def cents(value: Money) -> int:
    return int(value.amount.scaleb(2))


def test_allocation_properties():
    checked = 0
    for amount, weights in random_cases():
        parts = allocate(amount, weights)
        total = sum(weights)
        assert len(parts) == len(weights)
        assert Money.total(parts) == amount
        assert parts == allocate(amount, weights)
        assert allocate(-amount, weights) == [-p for p in parts]
        running = 0
        prefix = Money.zero()
        for part, weight in zip(parts, weights, strict=True):
            if weight == 0:
                assert part.is_zero()
            assert part.is_zero() or part.is_negative() == amount.is_negative()
            # |part * W - amount * w| < 0.01 * W, in centavos
            assert abs(cents(part) * total - cents(amount) * weight) < total
            running += weight
            prefix += part
            assert prefix == amount.prorate(running, total)
        checked += 1
    assert checked == 2000


def test_split_matches_allocate_with_unit_weights():
    rng = random.Random(87)  # noqa: S311 - seeded test data
    for _ in range(2000):
        amount = Money(Decimal(rng.randint(-(10**11), 10**11)).scaleb(-2))
        parts = rng.randint(1, 50)
        assert split(amount, parts) == allocate(amount, [1] * parts)


# --- D1-D4: Pydantic ----------------------------------------------------------


class Charge(BaseModel):
    amount: Money
    refund: Money | None = None


def codes(call: Callable[[], object]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        call()
    return [error["type"] for error in caught.value.errors()]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12.5", "12.50"), ("-3.5", "-3.50"), ("-0", "0.00"), ("1250", "1250.00")],
)
def test_json_input_is_a_string(text, expected):
    model = Charge.model_validate_json(f'{{"amount": "{text}"}}')
    assert model.amount == Money(expected)
    assert model.refund is None
    assert model.model_dump_json() == f'{{"amount":"{expected}","refund":null}}'


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ('{"amount": 12.5}', "money_invalid"),
        ('{"amount": 12}', "money_invalid"),
        ('{"amount": true}', "money_invalid"),
        ('{"amount": null}', "money_invalid"),
        ('{"amount": ["1"]}', "money_invalid"),
        ('{"amount": " 12.00"}', "money_invalid"),
        ('{"amount": "+12"}', "money_invalid"),
        ('{"amount": "1e3"}', "money_invalid"),
        ('{"amount": "1,250.00"}', "money_invalid"),
        ('{"amount": ".5"}', "money_invalid"),
        ('{"amount": "12."}', "money_invalid"),
        ('{"amount": "NaN"}', "money_invalid"),
        ('{"amount": ""}', "money_invalid"),
        ('{"amount": "1.234"}', "money_precision"),
        ('{"amount": "1.230"}', "money_precision"),
        ('{"amount": "1234567890123"}', "money_out_of_range"),
        ('{"amount": "-1234567890123.5"}', "money_out_of_range"),
        ('{"amount": "1234567890123.456"}', "money_precision"),  # precision wins
    ],
)
def test_json_input_errors(body, code):
    assert codes(lambda: Charge.model_validate_json(body)) == [code]


def test_python_input_takes_money_or_text_only():
    assert Charge(amount=Money("1.00")).amount == Money("1.00")
    assert Charge(amount="2.5").amount == Money("2.50")  # type: ignore[arg-type]
    assert Charge(amount=Money("1"), refund=None).refund is None
    for value in (Decimal("1.00"), 1, 1.0, True):
        assert codes(lambda value=value: Charge(amount=value)) == ["money_invalid"]
    assert codes(lambda: Charge(amount="1.234")) == ["money_precision"]  # type: ignore[arg-type]


def test_output_is_text_in_json_and_money_in_python():
    model = Charge(amount=Money("-3.5"), refund=Money("0"))
    assert model.model_dump_json() == '{"amount":"-3.50","refund":"0.00"}'
    assert model.model_dump(mode="json") == {"amount": "-3.50", "refund": "0.00"}
    dumped = model.model_dump()
    assert type(dumped["amount"]) is Money
    assert dumped == {"amount": Money("-3.50"), "refund": Money("0.00")}


MONEY_SCHEMA = {
    "type": "string",
    "format": "decimal",
    "pattern": MONEY_PATTERN,
    "examples": ["1250.00"],
}


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_json_schema(mode):
    schema = Charge.model_json_schema(mode=mode)
    assert schema["properties"]["amount"] == MONEY_SCHEMA | {"title": "Amount"}
    assert MONEY_SCHEMA in schema["properties"]["refund"]["anyOf"]
    assert MONEY_PATTERN == r"^-?\d{1,12}(\.\d{1,2})?$"


# --- E1: MoneyType without a database -----------------------------------------


def test_money_type_binds_money_only():
    column = MoneyType()
    process = column.bind_processor(PG)
    bind = column.process_bind_param
    assert bind(Money("1250.00"), PG) == Decimal("1250.00")
    assert bind(None, PG) is None
    for value in (Decimal("1.00"), 1, 1.0, "1.00", True):
        with pytest.raises(TypeError, match="binds Money only"):
            bind(value, PG)
        if process is not None:
            with pytest.raises(TypeError):
                process(value)


def test_money_type_results_are_money():
    column = MoneyType()
    assert column.process_result_value(Decimal("1250.00"), PG) == Money("1250.00")
    assert type(column.process_result_value(Decimal("0"), PG)) is Money
    assert column.process_result_value(None, PG) is None
    assert column.cache_ok is True
    impl = column.impl_instance
    assert (impl.precision, impl.scale, impl.asdecimal) == (14, 2, True)


def test_money_type_literals():
    statement = select(literal(Money("1250.00"), MoneyType()))
    rendered = str(
        statement.compile(dialect=PG, compile_kwargs={"literal_binds": True})
    )
    assert "1250.00" in rendered
    total = select(money_sum(literal(Money("-3.50"), MoneyType())))
    rendered = str(total.compile(dialect=PG, compile_kwargs={"literal_binds": True}))
    assert rendered.startswith("SELECT coalesce(sum(-3.50), 0.00)")
    assert isinstance(money_sum(literal(Money("1"), MoneyType())).type, MoneyType)
