"""Races of the payroll payments, on real connections (#112 B3, E3; data-model
§15). As in ``test_payroll_workflow_concurrency``: a waiting statement is
detected in ``pg_stat_activity``, never timed, every wait is capped, and the
first writer is paused **inside** its transaction, after its write and before
its commit (``overlap``). A second request has to wait on the period lock, then
reads what the first one committed.
"""

import uuid
from collections.abc import Awaitable, Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import (
    PayrollPaymentAlreadyReversedError,
    PayrollPaymentDuplicateError,
    PayrollPaymentExceedsBalanceError,
)
from app.modules.payroll.schemas import (
    PayrollPaymentCreate,
    PayrollPaymentRecorded,
    PayrollPaymentReversalCreate,
    PayrollPaymentReversed,
)
from app.workflows import payroll_payments
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.corrections_support import Staff, staff
from tests.integration.payroll_calculation_support import clear
from tests.integration.payroll_payment_support import (
    CREATED,
    NOW,
    body,
    code,
    finalized_period,
    paid_sum,
    pay,
    payments_of,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.test_attendance import principal_of
from tests.integration.test_payroll_workflow_concurrency import overlap

type Step = Any


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Iterator[None]:
    frozen.set(NOW)
    yield
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    set_floor(module_seeded_url, "10-01")
    yield
    clear(module_seeded_url)
    set_floor(module_seeded_url, None)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


@pytest.fixture
def period(module_seeded_url, client, owner, finance, tutor) -> uuid.UUID:
    """A finalized period of 110.00."""
    return finalized_period(module_seeded_url, client, owner, finance, tutor)


def paying(by: Staff, period: uuid.UUID, amount: str, reference: str) -> Step:
    principal = principal_of(by.user.id, FINANCE_STAFF)
    made = PayrollPaymentCreate(
        paid_on=date(2026, 11, 20),
        amount=Money(amount),
        method_code="transfer",
        reference=reference,
    )

    def call(session: AsyncSession) -> Awaitable[PayrollPaymentRecorded]:
        return payroll_payments.record_payment(session, principal, period, made)

    return call


def reversing(by: Staff, payment: Any) -> Step:
    principal = principal_of(by.user.id, OWNER)
    made = PayrollPaymentReversalCreate(reason="Monto equivocado")

    def call(session: AsyncSession) -> Awaitable[PayrollPaymentReversed]:
        return payroll_payments.reverse_payment(
            session, principal, uuid.UUID(str(payment)), made
        )

    return call


def test_e3_two_payments_over_the_total_one_is_refused(
    module_seeded_url, monkeypatch, finance, period
):
    url = module_seeded_url

    first, second = overlap(
        url,
        monkeypatch,
        payroll,
        "record_payment",
        paying(finance, period, "70.00", "SPEI 1"),
        paying(finance, period, "50.00", "SPEI 2"),
    )

    assert isinstance(first, PayrollPaymentRecorded)
    assert first.paid_state == "partially_paid"
    assert isinstance(second, PayrollPaymentExceedsBalanceError)
    assert second.details == {"remaining": "40.00"}
    assert [row.reference for row in payments_of(url, period)] == ["SPEI 1"]


def test_e3_two_identical_payments_one_is_a_duplicate(
    module_seeded_url, monkeypatch, finance, period
):
    url = module_seeded_url

    first, second = overlap(
        url,
        monkeypatch,
        payroll,
        "record_payment",
        paying(finance, period, "10.00", "SPEI 7"),
        paying(finance, period, "10.00", "spei 7"),
    )

    assert isinstance(first, PayrollPaymentRecorded)
    assert isinstance(second, PayrollPaymentDuplicateError)
    assert second.details == {"payment_id": str(first.id)}
    assert len(payments_of(url, period)) == 1


def test_e3_a_payment_waits_for_a_reversal_that_frees_the_total(
    module_seeded_url, client, monkeypatch, owner, finance, period
):
    url = module_seeded_url
    paid = pay(client, finance, period, body("110.00", "SPEI 1"))
    assert code(paid) == CREATED

    reversal, payment = overlap(
        url,
        monkeypatch,
        payroll,
        "reverse_payment",
        reversing(owner, paid.json()["id"]),
        paying(finance, period, "110.00", "SPEI 2"),
    )

    assert isinstance(reversal, PayrollPaymentReversed)
    assert reversal.paid_state == "unpaid"
    assert isinstance(payment, PayrollPaymentRecorded)
    assert payment.paid_state == "paid"
    assert paid_sum(url, period) == Money("110.00").amount


def test_e3_a_reversal_waits_for_a_payment(
    module_seeded_url, client, monkeypatch, owner, finance, period
):
    url = module_seeded_url
    paid = pay(client, finance, period, body("60.00", "SPEI 1"))
    assert code(paid) == CREATED

    payment, reversal = overlap(
        url,
        monkeypatch,
        payroll,
        "record_payment",
        paying(finance, period, "50.00", "SPEI 2"),
        reversing(owner, paid.json()["id"]),
    )

    assert isinstance(payment, PayrollPaymentRecorded)
    assert payment.paid_state == "paid"
    assert isinstance(reversal, PayrollPaymentReversed)
    # The reversal read the payment the first one committed.
    assert reversal.paid_state == "partially_paid"
    assert paid_sum(url, period) == Money("50.00").amount


def test_e3_two_reversals_of_one_payment(
    module_seeded_url, client, monkeypatch, owner, finance, period
):
    url = module_seeded_url
    paid = pay(client, finance, period, body("60.00", "SPEI 1"))

    first, second = overlap(
        url,
        monkeypatch,
        payroll,
        "reverse_payment",
        reversing(owner, paid.json()["id"]),
        reversing(owner, paid.json()["id"]),
    )

    assert isinstance(first, PayrollPaymentReversed)
    assert isinstance(second, PayrollPaymentAlreadyReversedError)
    assert paid_sum(url, period) == 0
