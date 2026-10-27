"""Money: MXN amounts with two places (ADR-0004, ISSUE-D2; spec §1, §16, §18.1).

Every billing and payroll amount is a :class:`Money`: an immutable value that
wraps one ``Decimal`` with exactly two places, in MXN (there is no currency
attribute: MXN only, spec §1). The rules (ADR-0004 and its M6-02
implementation notes):

- **Never from a float** (spec §16). ``float`` and ``bool`` are a
  :class:`TypeError` everywhere a number is taken.
- **Construction never rounds** (E1). ``Money(Decimal("1.234"))`` is a
  :class:`ValueError`. Rounding happens only in the named operations
  :meth:`Money.rounded`, :meth:`Money.multiply`, :meth:`Money.prorate`,
  :func:`allocate` and :func:`split`, so every rounding is a visible
  line-level step ("each line item is rounded half-up").
- **Half-up is half away from zero** (E2), like PostgreSQL ``round(x, 2)``:
  ``2.345 -> 2.35`` and ``-2.345 -> -2.35``. Each operation rounds **once**,
  from the exact value, on integer centavos, so no result depends on the
  ambient ``decimal`` context (its precision or rounding).
- **Strict operators** (E1). ``Money`` adds to, subtracts from and compares
  with ``Money`` only; ``* int`` is exact (quantities); ``* Decimal`` and ``/``
  are a :class:`TypeError` (use :meth:`Money.multiply`, :meth:`Money.prorate`
  or :func:`allocate`); ``sum()`` is a :class:`TypeError` (use
  :meth:`Money.total`); ``bool()`` is a :class:`TypeError` (use
  :meth:`Money.is_zero`).
- **Range** ``NUMERIC(14,2)``: ``|value| <= MONEY_MAX``, checked on every
  result (:class:`MoneyRangeError`).
- **JSON** (E4): a string only, matching :data:`MONEY_PATTERN` (the same
  pattern as ``apps/web/src/lib/money.ts``); output is always two places and
  never ``-0.00``. Field error codes: ``money_invalid``, ``money_precision``,
  ``money_out_of_range``. The sign and range of each field are the endpoint's.
- **Database** (E5): :class:`MoneyType` maps a ``NUMERIC(14,2)`` column and
  binds ``Money`` only; :func:`money_sum` sums a money expression as ``Money``.

This module is shared infrastructure (ADR-0002): it imports no domain module.
"""

import re
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any, Final, NoReturn, Self, final

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema
from sqlalchemy import ColumnElement, Dialect, Numeric, func, literal
from sqlalchemy.types import TypeDecorator

MONEY_MAX: Final = Decimal("999999999999.99")
"""The largest absolute amount, ``NUMERIC(14,2)`` (ADR-0004, E1 A5)."""

MONEY_PATTERN: Final = r"^-?\d{1,12}(\.\d{1,2})?$"
"""The money text pattern of the API and the web (ADR-0004, E4): up to 12
integer digits and up to 2 decimals. Equal to ``apps/web/src/lib/money.ts``."""

_MONEY_TEXT: Final = re.compile(MONEY_PATTERN, re.ASCII)
# Any plain decimal numeral (to tell precision and range errors apart, E4).
_NUMERIC_TEXT: Final = re.compile(r"-?(\d+)(?:\.(\d+))?", re.ASCII)
_MAX_CENTS: Final = 99_999_999_999_999
# A Decimal exponent (in centavos) above this cannot be in range.
_MAX_CENTS_EXPONENT: Final = 14

_FLOAT_RULE: Final = "money is never built from a float (ADR-0004, spec §16)"
_BOOL_RULE: Final = "a bool is not a number for money"

# Pydantic field error codes (E4) and their messages.
MONEY_INVALID: Final = "money_invalid"
MONEY_PRECISION: Final = "money_precision"
MONEY_OUT_OF_RANGE: Final = "money_out_of_range"
_MESSAGES: Final = {
    MONEY_INVALID: "Write an amount such as 1250.00.",
    MONEY_PRECISION: "At most two decimal places.",
    MONEY_OUT_OF_RANGE: "At most 12 digits before the decimal point.",
}


class MoneyRangeError(ValueError):
    """An amount outside ``NUMERIC(14,2)``: ``|value| > MONEY_MAX``
    (ADR-0004, E1 A5)."""


class MoneyTextError(ValueError):
    """Money text that is not a valid amount (ADR-0004, E4). ``code`` is the
    field error code: ``money_invalid`` or ``money_precision``."""

    def __init__(self, code: str) -> None:
        super().__init__(_MESSAGES[code])
        self.code = code


# --- Integer helpers (context-free, E2) ---------------------------------------


def _reject_number_type(value: object, what: str) -> None:
    if isinstance(value, bool):
        raise TypeError(f"{what}: {_BOOL_RULE}")
    if isinstance(value, float):
        raise TypeError(f"{what}: {_FLOAT_RULE}")


def _checked_int(value: object, what: str) -> int:
    _reject_number_type(value, what)
    if not isinstance(value, int):
        raise TypeError(f"{what} must be an int, not {type(value).__name__}")
    return value


def _power_of_ten(exponent: int) -> int:
    """``10**exponent`` as an ``int`` (``exponent >= 0``)."""
    result: int = 10**exponent
    return result


def _round_half_up(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` rounded half away from zero (``denominator > 0``)."""
    quotient, remainder = divmod(abs(numerator), denominator)
    if 2 * remainder >= denominator:
        quotient += 1
    return -quotient if numerator < 0 else quotient


def _decimal_parts(value: Decimal) -> tuple[int, int]:
    """A finite ``Decimal`` as ``(signed coefficient, exponent)``, exactly."""
    if not value.is_finite():
        raise ValueError("money needs a finite number")
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)  # noqa: S101 - finite, so an int
    coefficient = int("".join(map(str, digits)))
    return (-coefficient if sign else coefficient), exponent


def _scaled_to_cents(coefficient: int, exponent: int) -> int:
    """``coefficient * 10**exponent`` centavos rounded half away from zero."""
    if coefficient == 0:
        return 0
    if exponent >= 0:
        if exponent > _MAX_CENTS_EXPONENT:
            raise MoneyRangeError("the amount is outside NUMERIC(14,2)")
        return coefficient * _power_of_ten(exponent)
    shift = -exponent
    if shift > len(str(abs(coefficient))) + 1:
        return 0  # less than a tenth of a centavo
    return _round_half_up(coefficient, _power_of_ten(shift))


def _exact_cents(value: Decimal) -> int:
    """A ``Decimal`` with at most two places (numerically) as centavos."""
    coefficient, exponent = _decimal_parts(value)
    if coefficient == 0:
        return 0
    exponent += 2
    if exponent < 0:
        divisor = _power_of_ten(-exponent)
        if coefficient % divisor:
            raise ValueError(
                "money has at most two decimal places and is never rounded"
                " on construction; use Money.rounded()"
            )
        return coefficient // divisor
    if exponent > _MAX_CENTS_EXPONENT:
        raise MoneyRangeError("the amount is outside NUMERIC(14,2)")
    return coefficient * _power_of_ten(exponent)


def _text_problem(text: str) -> str | None:
    """The field error code of money text, or ``None`` when it is valid (E4).

    ``money_precision`` wins over ``money_out_of_range``.
    """
    if _MONEY_TEXT.fullmatch(text):
        return None
    numeral = _NUMERIC_TEXT.fullmatch(text)
    if numeral is None:
        return MONEY_INVALID
    integer, fraction = numeral.groups()
    if fraction is not None and len(fraction) > 2:
        return MONEY_PRECISION
    if len(integer) > 12:
        return MONEY_OUT_OF_RANGE
    return MONEY_INVALID


def _text_cents(text: str) -> int:
    problem = _text_problem(text)
    if problem == MONEY_OUT_OF_RANGE:
        raise MoneyRangeError(_MESSAGES[problem])
    if problem is not None:
        raise MoneyTextError(problem)
    negative = text.startswith("-")
    integer, _, fraction = text.lstrip("-").partition(".")
    cents = int(integer) * 100 + int(fraction.ljust(2, "0"))
    return -cents if negative else cents


# --- Money --------------------------------------------------------------------


@final
class Money:
    """An immutable MXN amount with exactly two places (ADR-0004; E1).

    ``Money(value)`` takes a ``Money``, a ``Decimal`` (finite, at most two
    places numerically), an ``int`` or a ``str`` matching
    :data:`MONEY_PATTERN`. It never rounds: use :meth:`rounded` for that. A
    ``float`` or ``bool`` is a :class:`TypeError`.
    """

    __slots__ = ("_amount", "_cents")
    _cents: int
    _amount: Decimal

    def __new__(cls, value: Money | Decimal | int | str) -> Self:
        _reject_number_type(value, "Money()")
        if isinstance(value, Money):
            return cls._from_cents(value._cents)
        if isinstance(value, int):
            return cls._from_cents(value * 100)
        if isinstance(value, Decimal):
            return cls._from_cents(_exact_cents(value))
        if isinstance(value, str):
            return cls._from_cents(_text_cents(value))
        raise TypeError(
            f"Money() takes Money, Decimal, int or str, not {type(value).__name__}"
        )

    @classmethod
    def _from_cents(cls, cents: int) -> Self:
        if abs(cents) > _MAX_CENTS:
            raise MoneyRangeError(
                "the amount is outside NUMERIC(14,2) (|amount| > MONEY_MAX)"
            )
        instance = object.__new__(cls)
        units, hundredths = divmod(abs(cents), 100)
        sign = "-" if cents < 0 else ""
        object.__setattr__(instance, "_cents", cents)
        # Decimal(str) is exact whatever the context; never "-0.00" (A6).
        object.__setattr__(
            instance, "_amount", Decimal(f"{sign}{units}.{hundredths:02d}")
        )
        return instance

    @classmethod
    def zero(cls) -> Self:
        """``0.00`` (ADR-0004)."""
        return cls._from_cents(0)

    @classmethod
    def rounded(cls, value: Decimal | int) -> Self:
        """``value`` rounded half away from zero to the centavo (ADR-0004; E2).

        ``2.345 -> 2.35``, ``-2.345 -> -2.35``, ``-0.004 -> 0.00``.
        """
        _reject_number_type(value, "Money.rounded()")
        if isinstance(value, int):
            return cls._from_cents(value * 100)
        if not isinstance(value, Decimal):
            raise TypeError(
                f"Money.rounded() takes a Decimal or an int, not {type(value).__name__}"
            )
        coefficient, exponent = _decimal_parts(value)
        return cls._from_cents(_scaled_to_cents(coefficient, exponent + 2))

    @classmethod
    def total(cls, items: Iterable[Money]) -> Self:
        """The exact sum of ``items`` (only ``Money``); empty gives ``0.00``
        (ADR-0004 "totals are sums of rounded lines"; E1)."""
        cents = 0
        for item in items:
            if not isinstance(item, Money):
                raise TypeError(
                    f"Money.total() sums Money only, not {type(item).__name__}"
                )
            cents += item._cents
        return cls._from_cents(cents)

    @property
    def amount(self) -> Decimal:
        """The ``Decimal`` value: exponent ``-2``, never ``-0.00`` (ADR-0004; E1 A6)."""
        return self._amount

    # --- Rounding operations (E2) ---

    def multiply(self, factor: Decimal | int) -> Money:
        """``amount * factor`` computed exactly, then rounded once half away
        from zero (ADR-0004; E2). For percentages and rates (``Decimal``)."""
        _reject_number_type(factor, "Money.multiply()")
        if isinstance(factor, int):
            return Money._from_cents(self._cents * factor)
        if not isinstance(factor, Decimal):
            raise TypeError(
                "Money.multiply() takes a Decimal or an int,"
                f" not {type(factor).__name__}"
            )
        coefficient, exponent = _decimal_parts(factor)
        return Money._from_cents(_scaled_to_cents(self._cents * coefficient, exponent))

    def prorate(self, numerator: int, denominator: int) -> Money:
        """``amount * numerator / denominator`` rounded once half away from
        zero, on integer centavos (ADR-0004; E2, E3).

        ``denominator > 0`` and ``numerator >= 0`` (it may exceed the
        denominator, as for a longer session).
        """
        numerator = _checked_int(numerator, "Money.prorate() numerator")
        denominator = _checked_int(denominator, "Money.prorate() denominator")
        if denominator <= 0:
            raise ValueError("Money.prorate() needs a denominator > 0")
        if numerator < 0:
            raise ValueError("Money.prorate() needs a numerator >= 0")
        return Money._from_cents(_round_half_up(self._cents * numerator, denominator))

    # --- Predicates ---

    def is_zero(self) -> bool:
        """``True`` for ``0.00`` (ADR-0004; E1: ``bool(Money)`` raises)."""
        return self._cents == 0

    def is_negative(self) -> bool:
        """``True`` below ``0.00`` (ADR-0004; E1)."""
        return self._cents < 0

    def is_positive(self) -> bool:
        """``True`` above ``0.00`` (ADR-0004; E1)."""
        return self._cents > 0

    # --- Exact arithmetic (E1) ---

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money._from_cents(self._cents + other._cents)

    def __radd__(self, other: object) -> NoReturn:
        raise TypeError(
            "Money adds to Money only; to sum Money use Money.total(), not sum()"
        )

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money._from_cents(self._cents - other._cents)

    def __rsub__(self, other: object) -> NoReturn:
        raise TypeError("Money subtracts Money only")

    def __neg__(self) -> Money:
        return Money._from_cents(-self._cents)

    def __pos__(self) -> Money:
        return self

    def __abs__(self) -> Money:
        return Money._from_cents(abs(self._cents))

    def __mul__(self, other: object) -> Money:
        _reject_number_type(other, "Money *")
        if isinstance(other, int):
            return Money._from_cents(self._cents * other)
        raise TypeError(
            "Money * takes an int quantity only;"
            " use Money.multiply() for a Decimal factor"
        )

    __rmul__ = __mul__

    def __truediv__(self, other: object) -> NoReturn:
        raise TypeError("Money is never divided; use Money.prorate() or allocate()")

    __floordiv__ = __truediv__
    __mod__ = __truediv__
    __divmod__ = __truediv__

    def __bool__(self) -> NoReturn:
        raise TypeError(
            "bool(Money) is ambiguous; use is_zero(), is_positive() or is_negative()"
        )

    # --- Comparisons (Money only, E1) ---

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._cents == other._cents

    def __hash__(self) -> int:
        return hash((Money, self._cents))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._cents < other._cents

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._cents <= other._cents

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._cents > other._cents

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._cents >= other._cents

    # --- Immutability and text ---

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("Money is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError("Money is immutable")

    def __reduce__(self) -> tuple[type[Money], tuple[str]]:
        return (Money, (str(self),))

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self

    def __str__(self) -> str:
        return str(self._amount)

    def __repr__(self) -> str:
        return f"Money('{self._amount}')"

    # --- Pydantic (E4) ---

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """``Money`` as a Pydantic field type (E4, D1-D3): JSON input is a
        string only; Python input is a ``Money`` or a string; JSON output is
        the two-place string; ``model_dump()`` keeps the ``Money``."""
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_plain_validator_function(_validate_json),
            python_schema=core_schema.no_info_plain_validator_function(
                _validate_python
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=core_schema.str_schema(), when_used="json"
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """The OpenAPI schema of money (ADR-0004 ``format: decimal``; E4 D4)."""
        return {
            "type": "string",
            "format": "decimal",
            "pattern": MONEY_PATTERN,
            "examples": ["1250.00"],
        }


def _field_error(code: str) -> PydanticCustomError:
    return PydanticCustomError(code, _MESSAGES[code])


def _validate_json(value: Any) -> Money:
    if not isinstance(value, str):
        raise _field_error(MONEY_INVALID)
    problem = _text_problem(value)
    if problem is not None:
        raise _field_error(problem)
    return Money(value)


def _validate_python(value: Any) -> Money:
    if isinstance(value, Money):
        return value
    return _validate_json(value)


# --- Allocation (E3) ----------------------------------------------------------


def allocate(amount: Money, weights: Sequence[int]) -> list[Money]:
    """``amount`` split by non-negative ``int`` weights, by cumulative
    rounding (ADR-0004; E3).

    Part *i* is ``R(A * C_i / W) - R(A * C_(i-1) / W)``, where ``C_i`` is the
    running weight total, ``W`` the weight total and ``R`` half away from zero
    to the centavo. The parts sum exactly to ``amount``, each is within one
    centavo of its exact share, a zero weight gets ``0.00``, every part has
    the amount's sign, and the first *k* parts sum to
    ``amount.prorate(C_k, W)`` (the prefix property: recognizing "cumulative
    now - cumulative before" on each consumption leaves the remainder to the
    last one, #86 E7).
    """
    if not isinstance(amount, Money):
        raise TypeError(f"allocate() splits Money, not {type(amount).__name__}")
    if isinstance(weights, str | bytes) or not isinstance(weights, Sequence):
        raise TypeError("allocate() weights are a sequence of int")
    checked = [_checked_int(weight, "allocate() weight") for weight in weights]
    if not checked:
        raise ValueError("allocate() needs at least one weight")
    if any(weight < 0 for weight in checked):
        raise ValueError("allocate() weights are >= 0")
    total = sum(checked)
    if total == 0:
        raise ValueError("allocate() needs a positive weight total")
    parts = []
    running = 0
    before = 0
    for weight in checked:
        running += weight
        now = _round_half_up(amount._cents * running, total)
        parts.append(Money._from_cents(now - before))
        before = now
    return parts


def split(amount: Money, parts: int) -> list[Money]:
    """``amount`` in ``parts`` equal shares that sum exactly:
    ``allocate(amount, [1] * parts)`` (ADR-0004; E3)."""
    parts = _checked_int(parts, "split() parts")
    if parts < 1:
        raise ValueError("split() needs parts >= 1")
    return allocate(amount, [1] * parts)


# --- Database (E5) ------------------------------------------------------------


class MoneyType(TypeDecorator[Money]):
    """A ``NUMERIC(14,2)`` money column (ADR-0004; E5).

    It binds only a ``Money`` (or ``None``): a ``Decimal``, ``int``, ``float``
    or ``str`` is a :class:`TypeError`. Results come back as ``Money`` (or
    ``None``).
    """

    impl = Numeric(14, 2, asdecimal=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Decimal | None:
        return _bind_value(value)

    def process_literal_param(self, value: Any, dialect: Dialect) -> str:
        bound = _bind_value(value)
        return "NULL" if bound is None else str(bound)

    def process_result_value(self, value: Any, dialect: Dialect) -> Money | None:
        if value is None:
            return None
        if isinstance(value, float):  # pragma: no cover - asdecimal=True
            raise TypeError(_FLOAT_RULE)
        return Money(value if isinstance(value, Decimal | int) else Decimal(value))


def _bind_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Money):
        raise TypeError(
            f"a money column binds Money only, not {type(value).__name__}"
            " (ADR-0004, E5)"
        )
    return value.amount


def money_sum(expression: Any) -> ColumnElement[Money]:
    """``coalesce(sum(expression), 0)`` typed as :class:`MoneyType`, so a SQL
    balance comes back as ``Money`` (``0.00`` when no row matches;
    ADR-0004; E5)."""
    return func.coalesce(
        func.sum(expression), literal(Money.zero(), MoneyType()), type_=MoneyType()
    )
