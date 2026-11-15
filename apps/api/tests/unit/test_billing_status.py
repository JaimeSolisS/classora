"""Ledger core pure parts (#90 D1, C3, C4, C6; data-model §4.2): the
charge status function, the draft rules and the error classes."""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from app.core.errors import AppError
from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft, ChargeDraft

TODAY = date(2031, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)


def m(value: str) -> Money:
    return Money(value)


@pytest.mark.parametrize(
    ("net", "allocated", "due", "expected"),
    [
        pytest.param("0.00", "0.00", YESTERDAY, "void", id="void"),
        pytest.param("100.00", "100.00", YESTERDAY, "paid", id="paid-even-if-late"),
        pytest.param("100.00", "99.99", YESTERDAY, "overdue", id="open-0.01-late"),
        pytest.param("100.00", "0.00", YESTERDAY, "overdue", id="overdue-unpaid"),
        pytest.param("100.00", "50.00", TODAY, "partially_paid", id="due-today"),
        pytest.param("100.00", "99.99", TODAY, "partially_paid", id="open-0.01"),
        pytest.param("100.00", "0.00", TODAY, "pending", id="pending-due-today"),
        pytest.param(
            "0.01", "0.00", TODAY + timedelta(days=1), "pending", id="pending-0.01"
        ),
        pytest.param("0.00", "0.00", TODAY, "void", id="released-to-void"),
    ],
)
def test_charge_status(net, allocated, due, expected):
    assert billing.charge_status(m(net), m(allocated), due, TODAY) == expected


def _charge(**overrides: Any) -> ChargeDraft:
    values: dict[str, Any] = {
        "student_id": uuid.uuid4(),
        "kind": "session",
        "class_session_id": uuid.uuid4(),
        "scheduled_minutes": 60,
        "service_date": TODAY,
        "list_amount": m("300.00"),
        "due_date_base": TODAY,
    }
    values.update(overrides)
    return ChargeDraft(**values)


def test_a_valid_charge_draft_computes_its_amount():
    draft = _charge(discount_amount=m("30.00"))
    assert draft.amount == m("270.00")
    assert draft.source_id == draft.class_session_id
    assert (
        _charge(
            kind="manual",
            class_session_id=None,
            scheduled_minutes=None,
            description="Libro",
        ).source_id
        is None
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"kind": "tuition_period"}, id="kind-not-yet"),
        pytest.param({"class_session_id": None}, id="no-source"),
        pytest.param({"group_enrollment_id": uuid.uuid4()}, id="two-sources"),
        pytest.param(
            {"kind": "group_enrollment", "scheduled_minutes": None},
            id="wrong-source",
        ),
        pytest.param({"scheduled_minutes": None}, id="session-without-minutes"),
        pytest.param({"scheduled_minutes": 14}, id="minutes-14"),
        pytest.param({"scheduled_minutes": 481}, id="minutes-481"),
        pytest.param({"description": "x"}, id="description-on-session"),
        pytest.param(
            {"kind": "manual", "class_session_id": None, "scheduled_minutes": None},
            id="manual-without-description",
        ),
        pytest.param(
            {
                "kind": "manual",
                "class_session_id": None,
                "scheduled_minutes": None,
                "description": " Libro",
            },
            id="untrimmed-description",
        ),
        pytest.param(
            {
                "kind": "manual",
                "class_session_id": None,
                "scheduled_minutes": None,
                "description": "x" * 201,
            },
            id="long-description",
        ),
        pytest.param({"list_amount": m("-1.00")}, id="negative-list"),
        pytest.param({"discount_amount": m("-1.00")}, id="negative-discount"),
        pytest.param({"discount_amount": m("300.01")}, id="discount-above-list"),
        pytest.param({"list_amount": "300.00"}, id="not-money"),
    ],
)
def test_a_bad_charge_draft_is_refused(overrides):
    with pytest.raises(ValueError):
        _charge(**overrides)


def _adjustment(**overrides: Any) -> AdjustmentDraft:
    values: dict[str, Any] = {
        "charge_id": uuid.uuid4(),
        "kind": "cancellation_credit",
        "amount": m("-10.00"),
        "source_key": "cancellation_credit:x",
    }
    values.update(overrides)
    return AdjustmentDraft(**values)


def test_valid_adjustment_drafts():
    assert _adjustment().amount == m("-10.00")
    assert _adjustment(kind="manual", source_key=None, reason="Ajuste").reason == (
        "Ajuste"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"kind": "refund"}, id="kind"),
        pytest.param({"amount": m("0.00")}, id="zero"),
        pytest.param({"source_key": None}, id="generated-without-key"),
        pytest.param({"source_key": ""}, id="empty-key"),
        pytest.param({"source_key": "k" * 201}, id="long-key"),
        pytest.param({"kind": "manual", "source_key": None}, id="manual-no-reason"),
        pytest.param({"reason": "  "}, id="blank-reason"),
        pytest.param({"reason": "r" * 501}, id="long-reason"),
        pytest.param({"amount": "-10.00"}, id="not-money"),
    ],
)
def test_a_bad_adjustment_draft_is_refused(overrides):
    with pytest.raises(ValueError):
        _adjustment(**overrides)


def test_error_classes():
    charge_id = uuid.uuid4()
    error = billing.ChargeNetNegativeError(charge_id, m("30.00"), m("-40.00"))
    assert (error.status, error.code, error.details) == (
        409,
        "charge_net_negative",
        {"charge_id": str(charge_id), "net": "30.00", "amount": "-40.00"},
    )
    student_id = uuid.uuid4()
    missing = billing.StudentHasNoFamilyError(student_id)
    assert missing.student_id == student_id
    assert not isinstance(missing, AppError)
