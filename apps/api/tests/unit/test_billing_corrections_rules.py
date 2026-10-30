"""The #100 corrections without a database (F10): the request bodies (signs,
zero, reason trimming, dates, ``extra="forbid"``), the drafts, the pro-rata
cancellation credit and ``package_status`` with ``cancelled``."""

import uuid
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.models import ADJUSTMENT_KINDS
from app.modules.billing.package_rules import PACKAGE_STATUSES, package_status
from app.modules.billing.schemas import (
    AdjustmentDraft,
    ManualAdjustmentCreate,
    ManualChargeCreate,
    ManualChargeDraft,
    PackageCancellationCreate,
    PaymentReversalCreate,
    RefundCreate,
    RefundDraft,
)

TODAY = date(2031, 3, 10)


def codes(error: ValidationError) -> list[tuple[Any, str]]:
    return [(e["loc"], e["type"]) for e in error.errors()]


def charge_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "student_id": str(uuid.uuid4()),
        "description": "  Libro de lectura  ",
        "amount": "350.00",
        "service_date": "2031-03-10",
        "due_date": "2031-03-01",
    }
    body.update(overrides)
    return body


# --- Bodies ---------------------------------------------------------------------------


def test_a_manual_charge_body_trims_and_allows_any_date_order():
    body = ManualChargeCreate.model_validate(charge_body())

    assert body.description == "Libro de lectura"
    assert body.amount == Money("350.00")
    assert body.due_date < body.service_date


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"amount": "0.00"}, (("amount",), "amount_not_positive")),
        ({"amount": "-1.00"}, (("amount",), "amount_not_positive")),
        ({"amount": "1.001"}, (("amount",), "money_precision")),
        ({"description": "   "}, (("description",), "string_too_short")),
        ({"description": "x" * 201}, (("description",), "string_too_long")),
        ({"service_date": "1999-12-31"}, (("service_date",), "date_out_of_range")),
        ({"due_date": "1999-12-31"}, (("due_date",), "date_out_of_range")),
        ({"extra": 1}, (("extra",), "extra_forbidden")),
    ],
)
def test_a_manual_charge_body_refuses(override, expected):
    with pytest.raises(ValidationError) as caught:
        ManualChargeCreate.model_validate(charge_body(**override))

    assert codes(caught.value) == [expected]


@pytest.mark.parametrize("amount", ["-25.00", "25.00"])
def test_a_manual_adjustment_takes_both_signs(amount):
    body = ManualAdjustmentCreate.model_validate(
        {"amount": amount, "reason": " Error de captura "}
    )

    assert body.amount == Money(amount)
    assert body.reason == "Error de captura"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"amount": "0.00", "reason": "x"}, (("amount",), "amount_zero")),
        ({"amount": "-0.00", "reason": "x"}, (("amount",), "amount_zero")),
        ({"amount": "5.00", "reason": "  "}, (("reason",), "string_too_short")),
        ({"amount": "5.00", "reason": "x" * 501}, (("reason",), "string_too_long")),
        ({"amount": "5.00"}, (("reason",), "missing")),
        (
            {"amount": "5.00", "reason": "x", "kind": "manual"},
            (("kind",), "extra_forbidden"),
        ),
    ],
)
def test_a_manual_adjustment_body_refuses(body, expected):
    with pytest.raises(ValidationError) as caught:
        ManualAdjustmentCreate.model_validate(body)

    assert codes(caught.value) == [expected]


def test_a_reversal_needs_a_trimmed_reason():
    assert PaymentReversalCreate.model_validate({"reason": " Rebotó "}).reason == (
        "Rebotó"
    )
    with pytest.raises(ValidationError) as caught:
        PaymentReversalCreate.model_validate({"reason": ""})
    assert codes(caught.value) == [(("reason",), "string_too_short")]


def refund_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "amount": "40.00",
        "refunded_on": "2031-03-10",
        "payment_method_code": "transfer",
        "reference": "  SPEI-1  ",
        "reason": "Saldo a favor",
    }
    body.update(overrides)
    return body


def test_a_refund_body_trims_and_blanks_the_reference():
    assert RefundCreate.model_validate(refund_body()).reference == "SPEI-1"
    assert RefundCreate.model_validate(refund_body(reference="   ")).reference is None
    assert RefundCreate.model_validate(refund_body(reference=None)).reference is None


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"amount": "0.00"}, (("amount",), "amount_not_positive")),
        ({"amount": "-5.00"}, (("amount",), "amount_not_positive")),
        ({"refunded_on": "1999-12-31"}, (("refunded_on",), "date_out_of_range")),
        ({"reference": "x" * 101}, (("reference",), "string_too_long")),
        ({"payment_method_code": ""}, (("payment_method_code",), "string_too_short")),
        ({"reason": " "}, (("reason",), "string_too_short")),
        ({"withdrawal_id": None}, (("withdrawal_id",), "extra_forbidden")),
    ],
)
def test_a_refund_body_refuses(override, expected):
    with pytest.raises(ValidationError) as caught:
        RefundCreate.model_validate(refund_body(**override))

    assert codes(caught.value)[0][1] == expected[1]
    assert codes(caught.value)[0][0][0] == expected[0][0]


def test_a_package_cancellation_body():
    plain = PackageCancellationCreate.model_validate({"reason": "Se mudó"})
    zero = PackageCancellationCreate.model_validate(
        {"reason": "Se mudó", "credit_amount": "0.00"}
    )

    assert plain.credit_amount is None
    assert zero.credit_amount == Money.zero()
    with pytest.raises(ValidationError) as caught:
        PackageCancellationCreate.model_validate(
            {"reason": "Se mudó", "credit_amount": "-1.00"}
        )
    assert codes(caught.value) == [(("credit_amount",), "amount_negative")]


# --- Drafts ---------------------------------------------------------------------------


def test_the_drafts_refuse_what_the_bodies_refuse():
    student, family = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ValueError, match="above zero"):
        ManualChargeDraft(
            family_id=family,
            student_id=student,
            description="Libro",
            amount=Money.zero(),
            service_date=TODAY,
            due_date=TODAY,
        )
    with pytest.raises(ValueError, match="trimmed"):
        ManualChargeDraft(
            family_id=family,
            student_id=student,
            description=" Libro",
            amount=Money("1.00"),
            service_date=TODAY,
            due_date=TODAY,
        )
    with pytest.raises(ValueError, match="above zero"):
        RefundDraft(
            payment_id=uuid.uuid4(),
            amount=Money("-1.00"),
            refunded_on=TODAY,
            payment_method_code="cash",
            reason="x",
        )
    with pytest.raises(ValueError, match="trimmed"):
        RefundDraft(
            payment_id=uuid.uuid4(),
            amount=Money("1.00"),
            refunded_on=TODAY,
            payment_method_code="cash",
            reason="",
        )


def test_a_package_cancellation_adjustment_needs_a_key_and_a_reason():
    assert "package_cancellation" in ADJUSTMENT_KINDS
    with pytest.raises(ValueError, match="needs a reason"):
        AdjustmentDraft(
            charge_id=uuid.uuid4(),
            kind="package_cancellation",
            amount=Money("-10.00"),
            source_key="package_cancellation:x",
        )
    with pytest.raises(ValueError, match="source_key"):
        AdjustmentDraft(
            charge_id=uuid.uuid4(),
            kind="package_cancellation",
            amount=Money("-10.00"),
            reason="Se mudó",
        )
    AdjustmentDraft(
        charge_id=uuid.uuid4(),
        kind="package_cancellation",
        amount=Money("-10.00"),
        reason="Se mudó",
        source_key="package_cancellation:x",
    )


# --- The pro-rata credit and the status (R6, B7) --------------------------------------


@pytest.mark.parametrize(
    ("net", "remaining", "granted", "credit"),
    [
        ("1000.00", 10, 10, "1000.00"),
        ("1000.00", 6, 10, "600.00"),
        ("1000.00", 1, 3, "333.33"),
        ("1000.00", 2, 3, "666.67"),
        ("0.01", 1, 2, "0.01"),  # half away from zero
        ("0.00", 5, 10, "0.00"),
        ("1500.00", 450, 600, "1125.00"),  # a minutes package
    ],
)
def test_the_default_credit_is_net_times_remaining_over_granted(
    net, remaining, granted, credit
):
    assert billing.package_cancellation_credit(Money(net), remaining, granted) == (
        Money(credit)
    )


def test_cancelled_wins_over_every_other_status():
    assert "cancelled" in PACKAGE_STATUSES
    for remaining, expires_on, has_expire_row in (
        (0, TODAY, False),
        (5, date(2031, 3, 1), False),
        (5, TODAY, True),
        (5, TODAY, False),
    ):
        assert (
            package_status(
                remaining,
                expires_on,
                has_expire_row=has_expire_row,
                today=TODAY,
                cancelled=True,
            )
            == "cancelled"
        )
    assert package_status(0, TODAY, has_expire_row=False, today=TODAY) == "exhausted"
    assert package_status(5, TODAY, has_expire_row=False, today=TODAY) == "active"
