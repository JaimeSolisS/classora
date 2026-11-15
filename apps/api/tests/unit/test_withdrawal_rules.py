"""The pure withdrawal rules (#101 F9; B1, B2, B7, S1, S4) and the confirm
body shapes (D2), plus ``package_status`` with ``withdrawn`` (D4) and the
#101 window and minutes of the session counts (B3)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.money import Money
from app.modules.billing.package_rules import PACKAGE_STATUSES, package_status
from app.modules.billing.withdrawals import (
    AllocationFact,
    ChargeAfter,
    canonical_json,
    fingerprint,
    plan_freed_cash,
    withdrawal_credit,
)
from app.modules.scheduling.enrollment_billing import CountedSlot, OwnerSchedule
from app.workflows.withdrawal_schemas import (
    WithdrawalCreditConfirm,
    WithdrawalRefundConfirm,
)

M = Money


# --- B1: the credit of one line ------------------------------------------------------


@pytest.mark.parametrize(
    ("net", "unused", "denominator", "policy", "credit"),
    [
        ("3000.00", 6, 10, "pro_rata", "1800.00"),  # 4 held of 10
        # Prorated at enrollment to 2,100.00 (7 of 10); then one session
        # cancelled by the center and 2 held: D = 6, U = 4.
        ("2100.00", 4, 6, "pro_rata", "1400.00"),
        ("100.00", 3, 7, "pro_rata", "42.86"),  # 42.857… rounds half away
        ("2500.00", 6, 10, "pro_rata", "1500.00"),  # a sessions package
        ("1000.00", 270, 600, "pro_rata", "450.00"),  # a minutes package
        ("3000.00", 0, 10, "fully_used", "0.00"),  # after the course ended
        ("3000.00", 0, 0, "nothing_delivered", "3000.00"),
        ("3000.00", 12, 10, "pro_rata", "3000.00"),  # U clamped to D
        ("3000.00", -1, 10, "fully_used", "0.00"),  # U clamped to 0
        ("0.00", 5, 10, "pro_rata", "0.00"),  # a net already credited to 0
    ],
)
def test_the_credit_is_net_times_unused_over_deliverable(
    net: str, unused: int, denominator: int, policy: str, credit: str
) -> None:
    rule = withdrawal_credit(M(net), unused, denominator)
    assert (rule.policy, str(rule.credit)) == (policy, credit)


def test_an_open_ended_fee_is_never_credited() -> None:
    rule = withdrawal_credit(M("2000.00"), 5, 0, open_ended=True)
    assert (rule.policy, rule.credit) == ("open_ended_fee", M.zero())


def test_the_center_cancelled_session_example() -> None:
    # 10-session fee of 3,000.00, one of the six future sessions cancelled by
    # the center: D = 9, U = 5.
    assert withdrawal_credit(M("3000.00"), 5, 9).credit == M("1666.67")


# --- B2: freed cash ------------------------------------------------------------------

T0 = datetime(2031, 3, 1, 12, tzinfo=UTC)


def _allocation(
    payment: uuid.UUID, amount: str, minutes: int, on: date
) -> AllocationFact:
    return AllocationFact(
        allocation_id=uuid.uuid7(),
        payment_id=payment,
        amount=M(amount),
        created_at=T0 + timedelta(minutes=minutes),
        received_on=on,
    )


def test_nothing_is_freed_while_the_new_net_covers_the_allocations() -> None:
    charge = uuid.uuid4()
    payment = uuid.uuid4()
    freed = plan_freed_cash(
        [
            ChargeAfter(
                charge_id=charge,
                new_net=M("1500.00"),
                allocations=[_allocation(payment, "1500.00", 0, date(2031, 3, 1))],
            )
        ]
    )
    assert freed.total == M.zero()
    assert freed.by_payment == {}


def test_the_newest_allocations_are_released_first_per_payment() -> None:
    charge = uuid.uuid4()
    older, newer = uuid.uuid4(), uuid.uuid4()
    freed = plan_freed_cash(
        [
            ChargeAfter(
                charge_id=charge,
                new_net=M("1200.00"),
                allocations=[
                    _allocation(older, "1500.00", 0, date(2031, 3, 1)),
                    _allocation(newer, "1500.00", 5, date(2031, 3, 2)),
                ],
            )
        ]
    )
    # Σ 3,000.00 − 1,200.00 = 1,800.00: all of the newer one, 300.00 of the
    # older one; payments listed oldest received first.
    assert freed.total == M("1800.00")
    assert list(freed.by_payment.items()) == [
        (older, M("300.00")),
        (newer, M("1500.00")),
    ]
    assert freed.by_charge == {charge: M("1800.00")}


def test_a_half_paid_fee_frees_only_the_excess() -> None:
    charge, payment = uuid.uuid4(), uuid.uuid4()
    freed = plan_freed_cash(
        [
            ChargeAfter(
                charge_id=charge,
                new_net=M("1200.00"),
                allocations=[_allocation(payment, "1500.00", 0, date(2031, 3, 1))],
            )
        ]
    )
    assert freed.by_payment == {payment: M("300.00")}


def test_freed_cash_sums_several_charges() -> None:
    payment = uuid.uuid4()
    lines = [
        ChargeAfter(
            charge_id=uuid.uuid4(),
            new_net=M.zero(),
            allocations=[_allocation(payment, "200.00", i, date(2031, 3, 1))],
        )
        for i in range(3)
    ]
    assert plan_freed_cash(lines).by_payment == {payment: M("600.00")}


# --- B7: the canonical form ----------------------------------------------------------


def test_the_canonical_form_sorts_keys_and_writes_money_as_strings() -> None:
    one = {"b": M("1.50"), "a": [uuid.UUID(int=1), date(2031, 1, 2)]}
    two = {"a": [uuid.UUID(int=1), date(2031, 1, 2)], "b": M("1.50")}
    assert canonical_json(one) == canonical_json(two)
    assert canonical_json(one) == (
        '{"a":["00000000-0000-0000-0000-000000000001","2031-01-02"],"b":"1.50"}'
    )
    assert fingerprint(one) == fingerprint(two)
    assert len(fingerprint(one)) == 64
    assert fingerprint(one) != fingerprint({**one, "b": M("1.51")})


# --- D4: package status --------------------------------------------------------------


def test_withdrawn_comes_right_after_cancelled() -> None:
    today = date(2031, 3, 10)
    assert "withdrawn" in PACKAGE_STATUSES
    assert (
        package_status(0, today, has_expire_row=False, today=today, withdrawn=True)
        == "withdrawn"
    )
    assert (
        package_status(
            0, today, has_expire_row=True, today=today, withdrawn=True, cancelled=True
        )
        == "cancelled"
    )
    assert (
        package_status(
            0,
            today - timedelta(days=1),
            has_expire_row=True,
            today=today,
            withdrawn=True,
        )
        == "withdrawn"
    )


# --- B3: the window and the minutes --------------------------------------------------


def test_the_counts_keep_92_results_and_add_minutes_and_a_window() -> None:
    start = datetime(2031, 3, 3, 22, tzinfo=UTC)  # 16:00 in Mexico City
    starts = [start + timedelta(days=7 * i) for i in range(6)]
    schedule = OwnerSchedule(
        starts=list(starts), slots=[CountedSlot(s, 60) for s in starts]
    )
    at = starts[1] + timedelta(hours=1)
    counts = schedule.counts(at)
    assert (counts.total, counts.remaining) == (6, 4)
    assert counts.remaining_minutes == 240
    bare = OwnerSchedule(starts=list(starts)).counts(at)
    assert (bare.total, bare.remaining, bare.first_start) == (6, 4, starts[0])
    window = (date(2031, 3, 10), date(2031, 3, 24))  # weeks 2-4
    inside = schedule.counts(at, local_window=window, zone="America/Mexico_City")
    assert (inside.total, inside.remaining, inside.remaining_minutes) == (3, 2, 120)
    with pytest.raises(ValueError, match="zone"):
        schedule.counts(at, local_window=window)


# --- D2: the confirm bodies ----------------------------------------------------------

FINGERPRINT = "a" * 64


def _body(**extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "subject": {"package_id": str(uuid.uuid4())},
        "reason": "  La familia se mudó  ",
        "fingerprint": FINGERPRINT,
    }
    body.update(extra)
    return body


def _codes(error: ValidationError) -> set[str]:
    return {e["type"] for e in error.errors()}


def test_the_body_trims_the_reason_and_takes_one_subject() -> None:
    parsed = WithdrawalCreditConfirm.model_validate(_body())
    assert parsed.reason == "La familia se mudó"
    assert parsed.subject.subject()[0] == "package"
    for subject in (
        {},
        {"package_id": str(uuid.uuid4()), "group_enrollment_id": str(uuid.uuid4())},
    ):
        with pytest.raises(ValidationError) as caught:
            WithdrawalCreditConfirm.model_validate(_body(subject=subject))
        assert "withdrawal_subject_invalid" in _codes(caught.value)


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"reason": "   "}, "string_too_short"),
        ({"reason": "x" * 501}, "string_too_long"),
        ({"fingerprint": "abc"}, "string_pattern_mismatch"),
        ({"unknown": 1}, "extra_forbidden"),
        (
            {
                "credit_overrides": [
                    {"charge_id": str(uuid.UUID(int=1)), "amount": "1.00"},
                    {"charge_id": str(uuid.UUID(int=1)), "amount": "2.00"},
                ]
            },
            "credit_override_duplicate",
        ),
        (
            {
                "credit_overrides": [
                    {"charge_id": str(uuid.UUID(int=1)), "amount": "-1.00"}
                ]
            },
            "amount_negative",
        ),
        (
            {"refund": {"refunded_on": "2031-03-10", "payment_method_code": "cash"}},
            "extra_forbidden",
        ),
    ],
)
def test_the_body_rules(extra: dict[str, object], code: str) -> None:
    with pytest.raises(ValidationError) as caught:
        WithdrawalCreditConfirm.model_validate(_body(**extra))
    assert code in _codes(caught.value)


def test_a_money_override_is_range_checked() -> None:
    with pytest.raises(ValidationError) as caught:
        WithdrawalCreditConfirm.model_validate(
            _body(
                credit_overrides=[
                    {"charge_id": str(uuid.UUID(int=1)), "amount": "1.001"}
                ]
            )
        )
    assert any(code.startswith("money") for code in _codes(caught.value))


def test_the_refund_body_needs_its_terms() -> None:
    with pytest.raises(ValidationError):
        WithdrawalRefundConfirm.model_validate(_body())
    parsed = WithdrawalRefundConfirm.model_validate(
        _body(
            refund={
                "refunded_on": "2031-03-10",
                "payment_method_code": "transfer",
                "reference": "  ",
            }
        )
    )
    assert parsed.refund.reference is None
    with pytest.raises(ValidationError) as caught:
        WithdrawalRefundConfirm.model_validate(
            _body(refund={"refunded_on": "1999-12-31", "payment_method_code": "cash"})
        )
    assert "date_out_of_range" in _codes(caught.value)
