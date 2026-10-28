"""Discount stacking and eligibility, pure (#95 B1; H2-H4; no I/O).

**Stacking (H4).** A charge's discounts are computed from its base price
(``list_amount``: the custom price if one applies, otherwise the catalog
price; :mod:`app.modules.billing.pricing`):

1. **At most one discount per kind.** Among the eligible discounts of one
   kind, the one that takes the most off **at its position** wins (ties: the
   earliest ``created_at``, then the lowest ``id``).
2. **Kinds apply in a fixed order** (:data:`KIND_ORDER`): ``scholarship`` →
   ``sibling`` → ``discount`` → ``promotion``, from the most specific
   (granted to this student) to the most general (open to everyone).
3. Each applies to the **running remainder** after the previous ones:

   - a percentage line is ``remainder.multiply(percent / 100)``
     (:meth:`~app.core.money.Money.multiply`: computed exactly and rounded
     once, half away from zero, independent of the ambient ``decimal``
     context; ADR-0004, #87 E2);
   - a fixed line is ``min(amount, remainder)`` (a fixed amount is per
     charge);
   - a line is never more than the remainder, so the result is never
     negative, and a line that comes to ``0.00`` is not recorded.

4. ``discount_amount`` = Σ lines; ``amount = list_amount − discount_amount``
   (I7). Percentages on the remainder can never total more than 100 %.

:func:`select_discounts` makes the per-kind choice and then calls
:func:`apply_discount_chain`, which applies given specs **in the given
order** and never reselects: #99 reuses it with a posted charge's snapshot
to reprice a duration change (#86 E2, "same discount snapshot").

**Eligibility (H2, H3).** :class:`DiscountEligibility` is the ``eligibility``
JSON object (every key optional; absent or ``null`` = any): ``charge_kinds``,
``class_types``, ``subject_ids`` (the charge's subject),
``education_level_ids`` (the **student's** level at posting; a student with
no level matches only when the key is absent) and ``min_family_students``
(only for ``sibling``: the family has at least that many **active**
students). :func:`matches` checks every criterion against
:class:`ChargeFacts`; the audience and the date window are checked by the
query (:func:`app.modules.billing.pricing.discounts_for`).
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError

from app.core.money import Money

type DiscountKind = Literal["scholarship", "sibling", "discount", "promotion"]
type DiscountAudience = Literal["all", "assigned"]
type CriteriaChargeKind = Literal[
    "session",
    "group_enrollment",
    "cohort_enrollment",
    "tuition_period",
    "package_purchase",
]
"""The charge kinds a discount may target (never ``manual``)."""
type CriteriaClassType = Literal["individual", "group", "course"]

KIND_ORDER: Final[tuple[DiscountKind, ...]] = (
    "scholarship",
    "sibling",
    "discount",
    "promotion",
)
"""The order the kinds apply in (H4)."""
CRITERIA_CHARGE_KINDS: Final[tuple[CriteriaChargeKind, ...]] = (
    "session",
    "group_enrollment",
    "cohort_enrollment",
    "tuition_period",
    "package_purchase",
)
CRITERIA_CLASS_TYPES: Final[tuple[CriteriaClassType, ...]] = (
    "individual",
    "group",
    "course",
)
IDS_MAX: Final = 50
"""At most this many subject or level ids per criterion."""
MIN_FAMILY_STUDENTS_MIN: Final = 2
MIN_FAMILY_STUDENTS_MAX: Final = 20

_HUNDREDTH_EXPONENT: Final = -2


@dataclass(frozen=True)
class DiscountSpec:
    """A discount as the stacking sees it: exactly one of ``percent`` and
    ``fixed_amount``."""

    discount_id: uuid.UUID
    kind: DiscountKind
    name: str
    percent: Decimal | None
    fixed_amount: Money | None
    created_at: datetime


@dataclass(frozen=True)
class DiscountLine:
    """One applied discount: the spec's snapshot, its ``position`` (1…n) and
    the amount it took off (always ``> 0.00``)."""

    discount_id: uuid.UUID
    position: int
    kind: DiscountKind
    name: str
    percent: Decimal | None
    fixed_amount: Money | None
    amount: Money


@dataclass(frozen=True)
class ChargeFacts:
    """What the criteria are checked against (H2, H9: as at posting).
    ``family_active_students`` is ``None`` when it wasn't read (no sibling
    candidate, or no family)."""

    charge_kind: str
    class_type: str
    subject_id: uuid.UUID
    education_level_id: uuid.UUID | None
    family_active_students: int | None = None


def _percent_factor(percent: Decimal) -> Decimal:
    """``percent / 100`` exactly (no context rounding)."""
    sign, digits, exponent = percent.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("a percent is a finite Decimal")
    return Decimal((sign, digits, exponent + _HUNDREDTH_EXPONENT))


def line_amount(remainder: Money, spec: DiscountSpec) -> Money:
    """What ``spec`` takes off ``remainder``: a percentage of it (one
    half-up rounding), or the fixed amount capped at it."""
    if spec.percent is not None:
        taken = remainder.multiply(_percent_factor(spec.percent))
    elif spec.fixed_amount is not None:
        taken = spec.fixed_amount
    else:
        raise ValueError("a discount has a percent or a fixed amount")
    return taken if taken <= remainder else remainder


def apply_discount_chain(
    list_amount: Money, specs: Sequence[DiscountSpec]
) -> tuple[DiscountLine, ...]:
    """Apply ``specs`` in the given order on the running remainder; drop the
    ``0.00`` lines and number the others 1…n. No selection."""
    remainder = list_amount
    lines: list[DiscountLine] = []
    for spec in specs:
        taken = line_amount(remainder, spec)
        if not taken.is_positive():
            continue
        remainder = remainder - taken
        lines.append(
            DiscountLine(
                discount_id=spec.discount_id,
                position=len(lines) + 1,
                kind=spec.kind,
                name=spec.name,
                percent=spec.percent,
                fixed_amount=spec.fixed_amount,
                amount=taken,
            )
        )
    return tuple(lines)


def select_discounts(
    list_amount: Money, eligible: Iterable[DiscountSpec]
) -> tuple[DiscountLine, ...]:
    """The H4 result for the ``eligible`` discounts: per kind, in
    :data:`KIND_ORDER`, the candidate with the largest line at that
    position (ties: earliest ``created_at``, then ``id``), then
    :func:`apply_discount_chain`."""
    by_kind: dict[str, list[DiscountSpec]] = {}
    for spec in eligible:
        by_kind.setdefault(spec.kind, []).append(spec)
    remainder = list_amount
    chosen: list[DiscountSpec] = []
    for kind in KIND_ORDER:
        candidates = by_kind.get(kind)
        if not candidates:
            continue
        best = min(
            candidates,
            key=lambda spec: (
                -line_amount(remainder, spec).amount,
                spec.created_at,
                spec.discount_id,
            ),
        )
        chosen.append(best)
        remainder = remainder - line_amount(remainder, best)
    return apply_discount_chain(list_amount, chosen)


def total(lines: Iterable[DiscountLine]) -> Money:
    """Σ line amounts (``discount_amount``)."""
    return Money.total(line.amount for line in lines)


# --- Eligibility (H2) ----------------------------------------------------------------


def _ids(value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    if value is None:
        return None
    if not value:
        raise PydanticCustomError(
            "criteria_empty_list", "Leave the criterion out to mean any."
        )
    unique = sorted(set(value))
    if len(unique) > IDS_MAX:
        raise PydanticCustomError(
            "criteria_too_many", "Choose at most 50 for one criterion."
        )
    return unique


def _choices[T: str](value: list[T] | None, order: tuple[T, ...]) -> list[T] | None:
    if value is None:
        return None
    if not value:
        raise PydanticCustomError(
            "criteria_empty_list", "Leave the criterion out to mean any."
        )
    return [choice for choice in order if choice in value]


class DiscountEligibility(BaseModel):
    """The ``eligibility`` criteria of a discount (H2). Every key is
    optional; absent or ``null`` means any. An empty list is refused
    (``criteria_empty_list``); lists are stored sorted and without
    duplicates; at most 50 ids (``criteria_too_many``);
    ``min_family_students`` 2–20 (``min_family_students_out_of_range``) and
    only for ``sibling`` (checked with the kind)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    charge_kinds: list[CriteriaChargeKind] | None = None
    class_types: list[CriteriaClassType] | None = None
    subject_ids: list[uuid.UUID] | None = None
    education_level_ids: list[uuid.UUID] | None = None
    min_family_students: int | None = None

    @field_validator("charge_kinds")
    @classmethod
    def _charge_kinds(
        cls, value: list[CriteriaChargeKind] | None
    ) -> list[CriteriaChargeKind] | None:
        return _choices(value, CRITERIA_CHARGE_KINDS)

    @field_validator("class_types")
    @classmethod
    def _class_types(
        cls, value: list[CriteriaClassType] | None
    ) -> list[CriteriaClassType] | None:
        return _choices(value, CRITERIA_CLASS_TYPES)

    @field_validator("subject_ids", "education_level_ids")
    @classmethod
    def _id_lists(cls, value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        return _ids(value)

    @field_validator("min_family_students")
    @classmethod
    def _threshold(cls, value: int | None) -> int | None:
        if value is not None and not (
            MIN_FAMILY_STUDENTS_MIN <= value <= MIN_FAMILY_STUDENTS_MAX
        ):
            raise PydanticCustomError(
                "min_family_students_out_of_range", "Choose 2 to 20 students."
            )
        return value

    def stored(self) -> dict[str, object]:
        """The canonical JSON object stored in ``discounts.eligibility``
        (absent keys left out)."""
        return self.model_dump(mode="json", exclude_none=True)


def matches(criteria: DiscountEligibility, facts: ChargeFacts) -> bool:
    """Whether every criterion holds for ``facts`` (H2, H3). A
    ``min_family_students`` criterion needs ``family_active_students``
    (``None`` = no family: never matches)."""
    if criteria.charge_kinds is not None and facts.charge_kind not in (
        criteria.charge_kinds
    ):
        return False
    if criteria.class_types is not None and facts.class_type not in (
        criteria.class_types
    ):
        return False
    if criteria.subject_ids is not None and facts.subject_id not in (
        criteria.subject_ids
    ):
        return False
    if criteria.education_level_ids is not None and (
        facts.education_level_id is None
        or facts.education_level_id not in criteria.education_level_ids
    ):
        return False
    if criteria.min_family_students is not None:
        count = facts.family_active_students
        if count is None or count < criteria.min_family_students:
            return False
    return True
