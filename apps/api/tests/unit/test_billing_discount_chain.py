"""Discount stacking (#95 B1, H4, H1 tests): the kind order on the running
remainder, one discount per kind, the remainder cap, one half-up rounding
per line, and the invariants over seeded random inputs."""

import decimal
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.money import Money
from app.modules.billing.discounts import (
    DiscountSpec,
    apply_discount_chain,
    select_discounts,
    total,
)
from app.modules.billing.schemas import ChargeDraft

T0 = datetime(2031, 1, 1, tzinfo=UTC)


def spec(
    kind: str,
    *,
    percent: str | None = None,
    fixed: str | None = None,
    created: int = 0,
    discount_id: uuid.UUID | None = None,
) -> DiscountSpec:
    return DiscountSpec(
        discount_id=discount_id or uuid.uuid4(),
        kind=kind,  # type: ignore[arg-type]
        name=f"{kind} {percent or fixed}",
        percent=None if percent is None else Decimal(percent),
        fixed_amount=None if fixed is None else Money(fixed),
        created_at=T0 + timedelta(minutes=created),
    )


def amounts(lines) -> list[str]:
    return [str(line.amount) for line in lines]


def test_the_kinds_apply_in_order_on_the_running_remainder():
    specs = [
        spec("promotion", percent="5"),
        spec("discount", fixed="100.00"),
        spec("sibling", percent="10"),
        spec("scholarship", percent="50"),
    ]

    lines = select_discounts(Money("350.00"), specs)

    assert [line.kind for line in lines] == [
        "scholarship",
        "sibling",
        "discount",
        "promotion",
    ]
    # 5 % of 57.50 = 2.875 -> 2.88 (half up).
    assert amounts(lines) == ["175.00", "17.50", "100.00", "2.88"]
    assert [line.position for line in lines] == [1, 2, 3, 4]
    assert str(Money("350.00") - total(lines)) == "54.62"


def test_one_discount_per_kind_the_one_that_takes_most():
    percent = spec("scholarship", percent="30")
    fixed = spec("scholarship", fixed="120.00")

    [line] = select_discounts(Money("350.00"), [percent, fixed])

    assert line.discount_id == fixed.discount_id
    assert line.amount == Money("120.00")


def test_a_tie_goes_to_the_earliest_then_the_lowest_id():
    low, high = sorted([uuid.uuid4(), uuid.uuid4()])
    later = spec("discount", percent="10", created=5, discount_id=low)
    earlier = spec("discount", fixed="10.00", created=1, discount_id=high)

    [line] = select_discounts(Money("100.00"), [later, earlier])
    assert line.discount_id == earlier.discount_id

    same_a = spec("discount", percent="10", created=1, discount_id=high)
    same_b = spec("discount", fixed="10.00", created=1, discount_id=low)
    [line] = select_discounts(Money("100.00"), [same_a, same_b])
    assert line.discount_id == low


def test_the_best_of_a_kind_is_measured_at_its_position():
    # On the list price 30 % (60.00) would beat 40.00; at its position (the
    # remainder 100.00 after the scholarship) it is 30.00, so 40.00 wins.
    lines = select_discounts(
        Money("200.00"),
        [
            spec("scholarship", percent="50"),
            spec("discount", percent="30"),
            spec("discount", fixed="40.00"),
        ],
    )

    assert amounts(lines) == ["100.00", "40.00"]


def test_a_fixed_line_is_capped_and_later_kinds_add_nothing():
    lines = select_discounts(
        Money("80.00"),
        [spec("discount", fixed="100.00"), spec("promotion", percent="10")],
    )

    assert amounts(lines) == ["80.00"]
    assert total(lines) == Money("80.00")


def test_a_full_scholarship_leaves_one_line_and_zero():
    lines = select_discounts(
        Money("350.00"),
        [spec("scholarship", percent="100"), spec("sibling", percent="10")],
    )

    assert amounts(lines) == ["350.00"]
    assert (Money("350.00") - total(lines)).is_zero()


@pytest.mark.parametrize(
    ("price", "percent", "expected"),
    [
        ("199.99", "15", "30.00"),
        ("0.10", "5", "0.01"),
        ("100.00", "33.3333", "33.33"),
        ("0.01", "49", "0.00"),
    ],
)
def test_one_half_up_rounding_per_line(price, percent, expected):
    lines = apply_discount_chain(Money(price), [spec("discount", percent=percent)])

    assert amounts(lines) == ([] if expected == "0.00" else [expected])


def test_the_ambient_decimal_context_changes_nothing():
    specs = [spec("discount", percent="33.3333"), spec("promotion", percent="12.5")]
    expected = amounts(select_discounts(Money("999.99"), specs))
    with decimal.localcontext() as context:
        context.prec = 3
        context.rounding = decimal.ROUND_DOWN
        assert amounts(select_discounts(Money("999.99"), specs)) == expected


def test_nothing_to_apply():
    assert select_discounts(Money("0.00"), [spec("discount", percent="10")]) == ()
    assert select_discounts(Money("100.00"), []) == ()


def test_the_chain_keeps_the_given_order():
    promotion = spec("promotion", fixed="10.00")
    scholarship = spec("scholarship", percent="50")

    lines = apply_discount_chain(Money("100.00"), [promotion, scholarship])

    assert [line.discount_id for line in lines] == [
        promotion.discount_id,
        scholarship.discount_id,
    ]
    assert amounts(lines) == ["10.00", "45.00"]


def test_random_inputs_keep_the_invariants():
    rng = random.Random(95)  # noqa: S311 - seeded test data, not security
    kinds = ["scholarship", "sibling", "discount", "promotion"]
    for _ in range(500):
        price = Money(f"{rng.randint(0, 99999)}.{rng.randint(0, 99):02d}")
        specs = []
        for _ in range(rng.randint(0, 6)):
            if rng.random() < 0.5:
                percent = f"{rng.randint(1, 100)}.{rng.randint(0, 9999):04d}"
                if Decimal(percent) > 100:
                    percent = "100"
                specs.append(spec(rng.choice(kinds), percent=percent))
            else:
                fixed = f"{rng.randint(0, 2000)}.{rng.randint(1, 99):02d}"
                specs.append(spec(rng.choice(kinds), fixed=fixed))
        lines = select_discounts(price, specs)
        amount = price - total(lines)
        assert total(lines) + amount == price
        assert all(line.amount.is_positive() for line in lines)
        assert not amount.is_negative()
        assert len({line.kind for line in lines}) == len(lines)
        ChargeDraft(
            student_id=uuid.uuid4(),
            kind="session",
            class_session_id=uuid.uuid4(),
            scheduled_minutes=60,
            service_date=T0.date(),
            due_date_base=T0.date(),
            list_amount=price,
            discount_amount=total(lines),
            discounts=lines,
        )


# --- The draft's checks (#95 B5) -----------------------------------------------------


def _draft(**values):
    base = {
        "student_id": uuid.uuid4(),
        "kind": "session",
        "class_session_id": uuid.uuid4(),
        "scheduled_minutes": 60,
        "service_date": T0.date(),
        "due_date_base": T0.date(),
        "list_amount": Money("100.00"),
    }
    return ChargeDraft(**{**base, **values})


def test_the_draft_refuses_bad_pricing_before_any_sql():
    lines = apply_discount_chain(Money("100.00"), [spec("discount", percent="10")])

    with pytest.raises(ValueError, match="not both"):
        _draft(price_version_id=uuid.uuid4(), custom_price_id=uuid.uuid4())
    with pytest.raises(ValueError, match="add up"):
        _draft(discounts=lines, discount_amount=Money("9.00"))
    with pytest.raises(ValueError, match="numbered"):
        renumbered = (lines[0].__class__(**{**vars(lines[0]), "position": 2}),)
        _draft(discounts=renumbered, discount_amount=Money("10.00"))
    with pytest.raises(ValueError, match="manual"):
        ChargeDraft(
            student_id=uuid.uuid4(),
            kind="manual",
            description="Ajuste",
            service_date=T0.date(),
            due_date_base=T0.date(),
            list_amount=Money("100.00"),
            custom_price_id=uuid.uuid4(),
        )
    with pytest.raises(ValueError, match="manual"):
        ChargeDraft(
            student_id=uuid.uuid4(),
            kind="manual",
            description="Ajuste",
            service_date=T0.date(),
            due_date_base=T0.date(),
            list_amount=Money("100.00"),
            discount_amount=Money("10.00"),
            discounts=lines,
        )

    draft = _draft(discounts=lines, discount_amount=Money("10.00"))
    assert draft.amount == Money("90.00")
