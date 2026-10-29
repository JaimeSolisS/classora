"""The #96 allocation planner and payment bodies, without a database (F6):
the oldest-first order (M2) and caps, the explicit checks (M3), ``none``,
the FIFO split of a credit allocation across payments (M4), the drafts'
rules, and the request bodies (modes, lines, M5 fields, blank → null)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.money import Money
from app.modules.billing.errors import (
    AllocationChargeNotFoundError,
    AllocationExceedsOpenError,
    InsufficientAccountCreditError,
)
from app.modules.billing.payments import plan_allocations, split_across_payments
from app.modules.billing.schemas import (
    AllocationLine,
    AllocationRequest,
    ChargeBalance,
    CreditAllocationCreate,
    CreditAllocationRequest,
    PaymentCreate,
    PaymentDraft,
    PlannedAllocation,
)

DAY = date(2031, 3, 10)
POSTED = datetime(2031, 3, 1, 12, 0, tzinfo=UTC)


def uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def balance(
    n: int,
    open_: str,
    *,
    due: date = DAY,
    service: date = DAY,
    posted: datetime = POSTED,
    net: str | None = None,
) -> ChargeBalance:
    net_amount = Money(net if net is not None else open_)
    open_amount = Money(open_)
    return ChargeBalance(
        charge_id=uid(n),
        student_id=uid(1000 + n),
        kind="manual",
        service_date=service,
        effective_due_date=due,
        amount=net_amount,
        adjustments_total=Money.zero(),
        net=net_amount,
        allocated=net_amount - open_amount,
        open=open_amount,
        status="pending",
        posted_at=posted,
    )


def oldest() -> AllocationRequest:
    return AllocationRequest(mode="oldest_first")


def explicit(*lines: tuple[int, str]) -> AllocationRequest:
    return AllocationRequest(
        mode="explicit",
        lines=tuple(
            AllocationLine(charge_id=uid(n), amount=Money(a)) for n, a in lines
        ),
    )


def plan(charges: list[ChargeBalance], available: str, request: Any) -> list[Any]:
    return [
        (p.charge_id.int, str(p.amount))
        for p in plan_allocations(charges, Money(available), request)
    ]


# --- oldest_first (M2) ---------------------------------------------------------------


def test_oldest_first_orders_by_due_date_then_service_date_posted_at_and_id():
    later = DAY + timedelta(days=1)
    charges = [
        balance(1, "10.00", due=later),
        balance(2, "10.00", due=DAY, service=later),
        balance(3, "10.00", due=DAY, service=DAY, posted=POSTED + timedelta(hours=1)),
        balance(5, "10.00", due=DAY, service=DAY),
        balance(4, "10.00", due=DAY, service=DAY),
        balance(6, "10.00", due=DAY - timedelta(days=30), service=later),
    ]

    assert [n for n, _ in plan(charges, "100.00", oldest())] == [6, 4, 5, 3, 2, 1]


def test_oldest_first_fills_each_up_to_its_open_and_stops_when_money_runs_out():
    charges = [
        balance(1, "100.00", due=DAY),
        balance(2, "50.00", due=DAY + timedelta(days=1)),
        balance(3, "70.00", due=DAY + timedelta(days=2)),
    ]

    assert plan(charges, "120.00", oldest()) == [(1, "100.00"), (2, "20.00")]
    assert plan(charges, "100.00", oldest()) == [(1, "100.00")]
    assert plan(charges, "500.00", oldest()) == [
        (1, "100.00"),
        (2, "50.00"),
        (3, "70.00"),
    ]


def test_oldest_first_skips_paid_void_and_zero_charges():
    charges = [
        balance(1, "0.00", net="80.00"),  # paid
        balance(2, "0.00", net="0.00"),  # void / zero
        balance(3, "40.00"),
    ]

    assert plan(charges, "100.00", oldest()) == [(3, "40.00")]
    assert plan([balance(1, "0.00")], "100.00", oldest()) == []


def test_none_allocates_nothing():
    assert plan([balance(1, "10.00")], "10.00", AllocationRequest(mode="none")) == []


# --- explicit (M3) -------------------------------------------------------------------


def test_explicit_keeps_request_order_and_amounts():
    charges = [balance(1, "100.00"), balance(2, "50.00")]

    assert plan(charges, "120.00", explicit((2, "50.00"), (1, "30.00"))) == [
        (2, "50.00"),
        (1, "30.00"),
    ]


def test_explicit_line_up_to_the_open_amount_is_accepted():
    assert plan([balance(1, "50.00")], "50.00", explicit((1, "50.00"))) == [
        (1, "50.00")
    ]


def test_explicit_line_above_open_is_refused_with_the_charge_and_its_open():
    charges = [balance(1, "100.00"), balance(2, "50.00")]

    with pytest.raises(AllocationExceedsOpenError) as raised:
        plan_allocations(charges, Money("200.00"), explicit((1, "10.00"), (2, "50.01")))

    assert raised.value.details == {"charge_id": str(uid(2)), "open": "50.00"}
    assert raised.value.status == 409


def test_explicit_unknown_charges_are_reported_per_line():
    with pytest.raises(AllocationChargeNotFoundError) as raised:
        plan_allocations(
            [balance(1, "100.00")],
            Money("100.00"),
            explicit((7, "1.00"), (1, "1.00"), (8, "1.00")),
        )

    assert raised.value.status == 422
    assert [e["path"] for e in raised.value.errors or []] == [
        ["body", "allocation", "lines", 0, "charge_id"],
        ["body", "allocation", "lines", 2, "charge_id"],
    ]


def test_explicit_lines_above_the_available_money_are_insufficient_credit():
    charges = [balance(1, "100.00"), balance(2, "100.00")]

    with pytest.raises(InsufficientAccountCreditError) as raised:
        plan_allocations(
            charges, Money("150.00"), explicit((1, "100.00"), (2, "50.01"))
        )

    assert raised.value.details == {"account_credit": "150.00"}


# --- the FIFO split (M4) -------------------------------------------------------------


def test_one_line_splits_across_payments_oldest_first():
    planned = [
        PlannedAllocation(charge_id=uid(1), amount=Money("250.00")),
        PlannedAllocation(charge_id=uid(2), amount=Money("30.00")),
    ]
    sources = [(uid(90), Money("100.00")), (uid(91), Money("200.00"))]

    rows = split_across_payments(planned, sources)

    assert [(p.int, c.charge_id.int, str(c.amount)) for p, c in rows] == [
        (90, 1, "100.00"),
        (91, 1, "150.00"),
        (91, 2, "30.00"),
    ]


def test_splitting_more_than_the_sources_is_a_programming_error():
    with pytest.raises(ValueError):
        split_across_payments(
            [PlannedAllocation(charge_id=uid(1), amount=Money("10.01"))],
            [(uid(90), Money("10.00"))],
        )


# --- drafts --------------------------------------------------------------------------


def draft(**overrides: Any) -> PaymentDraft:
    values: dict[str, Any] = {
        "billing_account_id": uid(1),
        "amount": Money("100.00"),
        "received_on": DAY,
        "payment_method_code": "cash",
        "allocation": oldest(),
    }
    values.update(overrides)
    return PaymentDraft(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": Money("0.00")},
        {"amount": Money("-1.00")},
        {"amount": "10.00"},
        {"note": " padded "},
        {"reference": ""},
        {"payer_name": "x" * 201},
        {"allocation": explicit((1, "100.01"))},
    ],
)
def test_a_payment_draft_refuses_rule_breaks(overrides):
    with pytest.raises(ValueError):
        draft(**overrides)


@pytest.mark.parametrize(
    ("mode", "lines"),
    [
        ("explicit", ()),
        ("oldest_first", (AllocationLine(charge_id=uid(1), amount=Money("1.00")),)),
        ("none", (AllocationLine(charge_id=uid(1), amount=Money("1.00")),)),
        (
            "explicit",
            (
                AllocationLine(charge_id=uid(1), amount=Money("1.00")),
                AllocationLine(charge_id=uid(1), amount=Money("2.00")),
            ),
        ),
        ("explicit", (AllocationLine(charge_id=uid(1), amount=Money("0.00")),)),
        ("sideways", ()),
    ],
)
def test_an_allocation_request_refuses_rule_breaks(mode, lines):
    with pytest.raises(ValueError):
        AllocationRequest(mode=mode, lines=lines)


def test_a_credit_request_has_no_none_and_amount_only_with_oldest_first():
    with pytest.raises(ValueError):
        CreditAllocationRequest(
            billing_account_id=uid(1), allocation=AllocationRequest(mode="none")
        )
    with pytest.raises(ValueError):
        CreditAllocationRequest(
            billing_account_id=uid(1),
            allocation=explicit((1, "1.00")),
            amount=Money("1.00"),
        )
    with pytest.raises(ValueError):
        CreditAllocationRequest(
            billing_account_id=uid(1), allocation=oldest(), amount=Money("0.00")
        )


# --- request bodies (B4, M5) ---------------------------------------------------------


def body(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "amount": "100.00",
        "received_on": "2031-03-10",
        "payment_method_code": "cash",
        "allocation": {"mode": "oldest_first"},
    }
    values.update(overrides)
    return values


def codes(model: Any, data: dict[str, Any]) -> list[tuple[tuple[Any, ...], str]]:
    with pytest.raises(ValidationError) as raised:
        model.model_validate(data)
    return [(e["loc"], e["type"]) for e in raised.value.errors()]


def line(n: int, amount: str) -> dict[str, str]:
    return {"charge_id": str(uid(n)), "amount": amount}


def test_a_valid_body_trims_text_and_turns_blank_into_null():
    parsed = PaymentCreate.model_validate(
        body(reference="  T-1  ", payer_name="   ", note="\tnota\n")
    )

    assert (parsed.reference, parsed.payer_name, parsed.note) == ("T-1", None, "nota")
    assert parsed.amount == Money("100.00")
    assert AllocationRequest.of(parsed.allocation) == oldest()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"amount": "0.00"}, [(("amount",), "amount_not_positive")]),
        ({"amount": "-5.00"}, [(("amount",), "amount_not_positive")]),
        ({"amount": "1.001"}, [(("amount",), "money_precision")]),
        ({"amount": "1000000000000.00"}, [(("amount",), "money_out_of_range")]),
        ({"received_on": "1999-12-31"}, [(("received_on",), "date_out_of_range")]),
        ({"reference": "x" * 101}, [(("reference",), "string_too_long")]),
        ({"payer_name": "x" * 201}, [(("payer_name",), "string_too_long")]),
        ({"note": "x" * 501}, [(("note",), "string_too_long")]),
        ({"payment_method_code": ""}, [(("payment_method_code",), "string_too_short")]),
        ({"extra": 1}, [(("extra",), "extra_forbidden")]),
        (
            {"allocation": {"mode": "explicit"}},
            [(("allocation", "lines"), "allocation_lines_required")],
        ),
        (
            {"allocation": {"mode": "explicit", "lines": []}},
            [(("allocation", "lines"), "allocation_lines_required")],
        ),
        (
            {"allocation": {"mode": "none", "lines": [line(1, "1.00")]}},
            [(("allocation", "lines"), "allocation_lines_not_allowed")],
        ),
        (
            {"allocation": {"mode": "oldest_first", "lines": [line(1, "1.00")]}},
            [(("allocation", "lines"), "allocation_lines_not_allowed")],
        ),
        (
            {
                "allocation": {
                    "mode": "explicit",
                    "lines": [line(1, "1.00"), line(1, "2.00")],
                }
            },
            [(("allocation", "lines"), "allocation_duplicate_charge")],
        ),
        (
            {"allocation": {"mode": "explicit", "lines": [line(1, "0.00")]}},
            [(("allocation", "lines", 0, "amount"), "amount_not_positive")],
        ),
        (
            {
                "allocation": {
                    "mode": "explicit",
                    "lines": [line(1, "60.00"), line(2, "40.01")],
                }
            },
            [(("allocation",), "allocation_exceeds_payment")],
        ),
        (
            {"allocation": {"mode": "sideways"}},
            [(("allocation", "mode"), "literal_error")],
        ),
    ],
)
def test_body_rules(overrides, expected):
    assert codes(PaymentCreate, body(**overrides)) == expected


def test_explicit_lines_may_add_up_to_exactly_the_payment():
    parsed = PaymentCreate.model_validate(
        body(
            allocation={
                "mode": "explicit",
                "lines": [line(1, "60.00"), line(2, "40.00")],
            }
        )
    )

    assert AllocationRequest.of(parsed.allocation).total == Money("100.00")


def test_at_most_200_lines():
    lines = [line(n, "0.01") for n in range(1, 202)]

    assert codes(
        PaymentCreate, body(allocation={"mode": "explicit", "lines": lines})
    ) == [(("allocation", "lines"), "too_long")]


def test_huge_lines_are_a_field_error_not_a_crash():
    lines = [line(n, "999999999999.99") for n in range(1, 4)]

    assert codes(
        PaymentCreate, body(allocation={"mode": "explicit", "lines": lines})
    ) == [(("allocation",), "allocation_exceeds_payment")]


def test_credit_body_rules():
    assert CreditAllocationCreate.model_validate(
        {"allocation": {"mode": "oldest_first"}, "amount": "10.00"}
    ).amount == Money("10.00")
    assert codes(
        CreditAllocationCreate,
        {"allocation": {"mode": "explicit", "lines": [line(1, "1.00")]}, "amount": "1"},
    ) == [(("amount",), "amount_not_allowed")]
    assert codes(CreditAllocationCreate, {"allocation": {"mode": "none"}}) == [
        (("allocation", "mode"), "literal_error")
    ]
    assert codes(
        CreditAllocationCreate,
        {"allocation": {"mode": "oldest_first"}, "amount": "0.00"},
    ) == [(("amount",), "amount_not_positive")]
