"""Ledger constraints by direct insert (#90 E2; I4-I7; data-model §3).

One test per rule: each ``ck_charges_*`` CHECK, the I7 equation, the sign
and range checks, the adjustment ``source_key`` rule, the release cause,
one reversal per payment and one release per allocation, and the composite
foreign keys that keep an allocation, a reversal, a refund and an
adjustment in their charge's or payment's account (and student).
PostgreSQL checks CHECKs in name order, so a row that breaks two rules is
reported under the first name.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.factories.scheduling import persist_class_session
from tests.integration.billing_ledger_support import (
    LedgerRows,
    fails_with,
    ledger_rows,
)
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

CHECK = "23514"
UNIQUE = "23505"
FOREIGN_KEY = "23503"
DAY = date(2031, 3, 10)
POSTED_AT = datetime(2031, 3, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def rows(module_seeded_url: str) -> LedgerRows:
    return ledger_rows(module_seeded_url)


@pytest.fixture(scope="module")
def session_id(module_seeded_url: str) -> uuid.UUID:
    async def _make(session: Any) -> uuid.UUID:
        return (await persist_class_session(session)).id

    return in_uow(module_seeded_url, _make)


CHARGE_COLUMNS = (
    "billing_account_id",
    "student_id",
    "kind",
    "class_session_id",
    "group_enrollment_id",
    "cohort_enrollment_id",
    "tuition_plan_id",
    "package_id",
    "service_date",
    "service_period_end",
    "scheduled_minutes",
    "list_amount",
    "discount_amount",
    "amount",
    "due_date",
    "description",
    "posted_at",
)
CHARGE_INSERT = (
    f"INSERT INTO charges ({', '.join(CHARGE_COLUMNS)}) VALUES"  # noqa: S608
    f" ({', '.join(':' + c for c in CHARGE_COLUMNS)})"
)


def _charge(rows: LedgerRows, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "billing_account_id": rows.account,
        "student_id": rows.student,
        "kind": "manual",
        "class_session_id": None,
        "group_enrollment_id": None,
        "cohort_enrollment_id": None,
        "tuition_plan_id": None,
        "package_id": None,
        "service_date": DAY,
        "service_period_end": None,
        "scheduled_minutes": None,
        "list_amount": Decimal("100.00"),
        "discount_amount": Decimal("0.00"),
        "amount": Decimal("100.00"),
        "due_date": DAY,
        "description": "Cargo",
        "posted_at": POSTED_AT,
    }
    values.update(overrides)
    return values


def test_a_valid_manual_charge_row_is_accepted(module_seeded_url, rows):
    run_sql(module_seeded_url, CHARGE_INSERT, _charge(rows))


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        pytest.param({"kind": "refund"}, "ck_charges_kind", id="unknown-kind"),
        pytest.param(
            {"kind": "package_purchase", "description": None},
            "ck_charges_one_source",
            id="purchase-without-package",
        ),
        pytest.param(
            {"package_id": "PACKAGE"},
            "ck_charges_one_source",
            id="package-on-manual",
        ),
        pytest.param(
            {
                "kind": "package_purchase",
                "description": None,
                "package_id": "PACKAGE",
                "tuition_plan_id": "PLAN",
            },
            "ck_charges_one_source",
            id="purchase-with-a-plan",
        ),
        pytest.param(
            {"kind": "tuition_period", "description": None},
            "ck_charges_one_source",
            id="tuition-without-plan",
        ),
        pytest.param(
            {"service_period_end": date(2031, 3, 31)},
            "ck_charges_one_source",
            id="period-end-on-manual",
        ),
        pytest.param(
            {"kind": "session", "description": None, "scheduled_minutes": 60},
            "ck_charges_one_source",
            id="no-source",
        ),
        pytest.param(
            {"kind": "session", "class_session_id": "SESSION", "description": None},
            "ck_charges_one_source",
            id="session-without-minutes",
        ),
        pytest.param(
            {"scheduled_minutes": 60}, "ck_charges_one_source", id="minutes-on-manual"
        ),
        pytest.param(
            {"description": None}, "ck_charges_one_source", id="manual-no-description"
        ),
        pytest.param(
            {"kind": "session", "class_session_id": "SESSION", "scheduled_minutes": 60},
            "ck_charges_one_source",
            id="description-on-session",
        ),
        pytest.param(
            {"description": " Cargo"}, "ck_charges_description_valid", id="untrimmed"
        ),
        pytest.param({"description": ""}, "ck_charges_description_valid", id="empty"),
        pytest.param(
            {
                "kind": "tuition_period",
                "description": None,
                "tuition_plan_id": "PLAN",
                "service_period_end": date(2031, 3, 9),
            },
            "ck_charges_service_period_end_valid",
            id="period-end-before-start",
        ),
        pytest.param(
            {
                "kind": "tuition_period",
                "description": None,
                "tuition_plan_id": "PLAN",
            },
            "ck_charges_one_source",
            id="tuition-without-period-end",
        ),
        pytest.param(
            {
                "kind": "session",
                "class_session_id": "SESSION",
                "description": None,
                "scheduled_minutes": 60,
                "tuition_plan_id": "PLAN",
            },
            "ck_charges_one_source",
            id="tuition-plan-on-session",
        ),
        pytest.param(
            {
                "kind": "session",
                "class_session_id": "SESSION",
                "description": None,
                "scheduled_minutes": 14,
            },
            "ck_charges_scheduled_minutes_range",
            id="minutes-14",
        ),
        pytest.param(
            {
                "kind": "session",
                "class_session_id": "SESSION",
                "description": None,
                "scheduled_minutes": 481,
            },
            "ck_charges_scheduled_minutes_range",
            id="minutes-481",
        ),
        pytest.param(
            {"amount": Decimal("90.00")}, "ck_charges_amount_equation", id="i7"
        ),
        pytest.param(
            {"list_amount": Decimal("-1.00"), "amount": Decimal("-1.00")},
            "ck_charges_amount_non_negative",
            id="negative-amount",
        ),
        pytest.param(
            {"discount_amount": Decimal("-1.00"), "amount": Decimal("101.00")},
            "ck_charges_discount_amount_non_negative",
            id="negative-discount",
        ),
        pytest.param(
            {"discount_amount": Decimal("150.00"), "amount": Decimal("-50.00")},
            "ck_charges_amount_non_negative",
            id="discount-above-list",
        ),
    ],
)
def test_charge_checks(module_seeded_url, rows, session_id, overrides, constraint):
    # "PLAN" and "PACKAGE" are made-up ids: the CHECKs fire before the
    # composite foreign keys (#93 A5, #94 A5).
    values = {
        k: session_id
        if v == "SESSION"
        else (uuid.uuid4() if v in ("PLAN", "PACKAGE") else v)
        for k, v in _charge(rows, **overrides).items()
    }
    message = fails_with(module_seeded_url, CHARGE_INSERT, CHECK, **values)
    assert constraint in message


def test_a_charge_with_two_sources_breaks_the_one_source_check(module_seeded_url, rows):
    # The CHECK runs before the foreign keys, so made-up ids reach it.
    values = _charge(
        rows,
        kind="group_enrollment",
        description=None,
        group_enrollment_id=uuid.uuid4(),
        cohort_enrollment_id=uuid.uuid4(),
    )
    message = fails_with(module_seeded_url, CHARGE_INSERT, CHECK, **values)
    assert "ck_charges_one_source" in message


PRORATION_INSERT = (
    f"INSERT INTO charges ({', '.join(CHARGE_COLUMNS)}, proration_remaining,"  # noqa: S608
    f" proration_total) VALUES ({', '.join(':' + c for c in CHARGE_COLUMNS)},"
    " :remaining, :total)"
)


@pytest.mark.parametrize(
    ("kind", "remaining", "total"),
    [
        pytest.param("manual", 3, 10, id="manual"),
        pytest.param("session", 3, 10, id="session"),
        pytest.param("group_enrollment", 11, 10, id="remaining-above-total"),
        pytest.param("group_enrollment", 0, 0, id="total-zero"),
        pytest.param("cohort_enrollment", -1, 10, id="negative"),
        pytest.param("group_enrollment", 3, None, id="only-remaining"),
        pytest.param("cohort_enrollment", None, 10, id="only-total"),
    ],
)
def test_the_proration_check(
    module_seeded_url, rows, session_id, kind, remaining, total
):
    # #92 A2, J8: both NULL, or an enrollment fee with 0 <= r <= t, t >= 1.
    # The CHECKs run before the foreign keys (made-up period ids reach it)
    # and in name order (ck_charges_one_source passes first).
    source: dict[str, Any] = {"description": None}
    if kind == "manual":
        source = {}
    elif kind == "session":
        source.update(class_session_id=session_id, scheduled_minutes=60)
    else:
        source[f"{kind}_id"] = uuid.uuid4()
    values = _charge(rows, kind=kind, **source)
    message = fails_with(
        module_seeded_url,
        PRORATION_INSERT,
        CHECK,
        **values,
        remaining=remaining,
        total=total,
    )
    assert "ck_charges_proration" in message


def test_the_session_natural_key_is_unique(module_seeded_url, rows, session_id):
    url = module_seeded_url
    values = _charge(
        rows,
        kind="session",
        class_session_id=session_id,
        scheduled_minutes=60,
        description=None,
    )
    run_sql(url, CHARGE_INSERT, values)
    message = fails_with(url, CHARGE_INSERT, UNIQUE, **values)
    assert "uq_charges_session_student" in message


@pytest.mark.parametrize(
    ("offset", "ok"), [(-1, False), (0, True), (60, True), (61, False)]
)
def test_due_offset_days_range(module_seeded_url, rows, offset, ok):
    sql = "UPDATE billing_accounts SET due_offset_days = :d WHERE id = :id"
    if ok:
        run_sql(module_seeded_url, sql, {"d": offset, "id": rows.other_account})
    else:
        message = fails_with(
            module_seeded_url, sql, CHECK, d=offset, id=rows.other_account
        )
        assert "ck_billing_accounts_due_offset_days_range" in message


def test_one_account_per_family(module_seeded_url, rows):
    message = fails_with(
        module_seeded_url,
        "INSERT INTO billing_accounts (family_id) VALUES (:f)",
        UNIQUE,
        f=rows.family,
    )
    assert "uq_billing_accounts_family_id" in message


def test_a_payment_method_code_is_lower_snake_case(module_seeded_url):
    message = fails_with(
        module_seeded_url,
        "INSERT INTO payment_methods (code) VALUES ('Tarjeta')",
        CHECK,
    )
    assert "ck_payment_methods_code_format" in message


ADJUSTMENT_INSERT = (
    "INSERT INTO charge_adjustments (charge_id, billing_account_id, student_id,"
    " kind, amount, reason, source_key, posted_at) VALUES (:charge, :account,"
    " :student, :kind, :amount, :reason, :key, '2031-03-10T12:00:00+00:00')"
)


def _adjustment(rows: LedgerRows, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "charge": rows.charge,
        "account": rows.account,
        "student": rows.student,
        "kind": "cancellation_credit",
        "amount": Decimal("-10.00"),
        "reason": None,
        "key": f"test:{uuid.uuid4()}",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        pytest.param({"amount": Decimal("0.00")}, "amount_non_zero", id="zero"),
        pytest.param({"key": None}, "source_key_required", id="generated-no-key"),
        pytest.param(
            {"kind": "manual", "key": None},
            "manual_reason_required",
            id="manual-no-reason",
        ),
        pytest.param({"kind": "refund"}, "ck_charge_adjustments_kind", id="kind"),
        pytest.param({"reason": "  "}, "reason_valid", id="blank-reason"),
    ],
)
def test_adjustment_checks(module_seeded_url, rows, overrides, constraint):
    message = fails_with(
        module_seeded_url, ADJUSTMENT_INSERT, CHECK, **_adjustment(rows, **overrides)
    )
    assert constraint in message


def test_a_manual_adjustment_needs_no_source_key(module_seeded_url, rows):
    run_sql(
        module_seeded_url,
        ADJUSTMENT_INSERT,
        _adjustment(rows, kind="manual", key=None, reason="Corrección"),
    )


def test_a_source_key_is_used_once(module_seeded_url, rows):
    values = _adjustment(rows)
    run_sql(module_seeded_url, ADJUSTMENT_INSERT, values)
    message = fails_with(module_seeded_url, ADJUSTMENT_INSERT, UNIQUE, **values)
    assert "uq_charge_adjustments_source_key" in message


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"account": "OTHER_ACCOUNT"}, id="other-account"),
        pytest.param({"student": "OTHER_STUDENT"}, id="other-student"),
    ],
)
def test_an_adjustment_stays_on_its_charges_account_and_student(
    module_seeded_url, rows, overrides
):
    values = _adjustment(rows)
    for key, value in overrides.items():
        values[key] = (
            rows.other_account if value == "OTHER_ACCOUNT" else rows.other_student
        )
    message = fails_with(module_seeded_url, ADJUSTMENT_INSERT, FOREIGN_KEY, **values)
    assert "fk_charge_adjustments_charge_id_charges" in message


PAYMENT_INSERT = (
    "INSERT INTO payments (billing_account_id, amount, received_on,"
    " payment_method_code, recorded_by_user_id, reference)"
    " VALUES (:account, :amount, '2031-03-10', :method, :user, :reference)"
)


@pytest.mark.parametrize(
    ("overrides", "state", "constraint"),
    [
        pytest.param({"amount": Decimal("0.00")}, CHECK, "amount_positive", id="zero"),
        pytest.param({"reference": " x"}, CHECK, "reference_valid", id="reference"),
        pytest.param(
            {"method": "card"}, FOREIGN_KEY, "payment_method_code", id="unknown-method"
        ),
    ],
)
def test_payment_checks(module_seeded_url, rows, overrides, state, constraint):
    values = {
        "account": rows.account,
        "amount": Decimal("10.00"),
        "method": "cash",
        "user": rows.user,
        "reference": None,
        **overrides,
    }
    message = fails_with(module_seeded_url, PAYMENT_INSERT, state, **values)
    assert constraint in message


ALLOCATION_INSERT = (
    "INSERT INTO payment_allocations (payment_id, charge_id, billing_account_id,"
    " amount) VALUES (:payment, :charge, :account, :amount)"
)


def test_an_allocation_amount_is_positive(module_seeded_url, rows):
    message = fails_with(
        module_seeded_url,
        ALLOCATION_INSERT,
        CHECK,
        payment=rows.payment,
        charge=rows.charge,
        account=rows.account,
        amount=Decimal("0.00"),
    )
    assert "ck_payment_allocations_amount_positive" in message


@pytest.mark.parametrize(
    ("payment", "charge", "account", "constraint"),
    [
        pytest.param(
            "payment",
            "other_charge",
            "account",
            "fk_payment_allocations_charge_id_charges",
            id="charge-of-another-account",
        ),
        pytest.param(
            "other_payment",
            "charge",
            "account",
            "fk_payment_allocations_payment_id_payments",
            id="payment-of-another-account",
        ),
    ],
)
def test_an_allocation_its_payment_and_charge_share_one_account(
    module_seeded_url, rows, payment, charge, account, constraint
):
    message = fails_with(
        module_seeded_url,
        ALLOCATION_INSERT,
        FOREIGN_KEY,
        payment=getattr(rows, payment),
        charge=getattr(rows, charge),
        account=getattr(rows, account),
        amount=Decimal("1.00"),
    )
    assert constraint in message


REFUND_INSERT = (
    "INSERT INTO refunds (payment_id, billing_account_id, amount, refunded_on,"
    " payment_method_code, reason, refunded_by_user_id) VALUES (:payment,"
    " :account, :amount, '2031-03-10', 'cash', 'Devolución', :user)"
)


def test_a_refund_amount_is_positive(module_seeded_url, rows):
    message = fails_with(
        module_seeded_url,
        REFUND_INSERT,
        CHECK,
        payment=rows.payment,
        account=rows.account,
        amount=Decimal("0.00"),
        user=rows.user,
    )
    assert "ck_refunds_amount_positive" in message


def test_a_refund_stays_in_its_payments_account(module_seeded_url, rows):
    message = fails_with(
        module_seeded_url,
        REFUND_INSERT,
        FOREIGN_KEY,
        payment=rows.payment,
        account=rows.other_account,
        amount=Decimal("1.00"),
        user=rows.user,
    )
    assert "fk_refunds_payment_id_payments" in message


REVERSAL_INSERT = (
    "INSERT INTO payment_reversals (payment_id, billing_account_id, reason,"
    " reversed_by_user_id) VALUES (:payment, :account, 'Rebotado', :user)"
)


def test_a_payment_is_reversed_at_most_once(module_seeded_url, rows):
    message = fails_with(
        module_seeded_url,
        REVERSAL_INSERT,
        UNIQUE,
        payment=rows.reversed_payment,
        account=rows.account,
        user=rows.user,
    )
    assert "uq_payment_reversals_payment_id" in message


def test_a_reversal_stays_in_its_payments_account(module_seeded_url, rows):
    message = fails_with(
        module_seeded_url,
        REVERSAL_INSERT,
        FOREIGN_KEY,
        payment=rows.payment,
        account=rows.other_account,
        user=rows.user,
    )
    assert "fk_payment_reversals_payment_id_payments" in message


RELEASE_INSERT = (
    "INSERT INTO allocation_releases (allocation_id, payment_reversal_id,"
    " charge_adjustment_id) VALUES (:allocation, :reversal, :adjustment)"
)


@pytest.mark.parametrize(
    ("reversal", "adjustment"), [(False, False), (True, True)], ids=["none", "both"]
)
def test_a_release_has_exactly_one_cause(module_seeded_url, rows, reversal, adjustment):
    message = fails_with(
        module_seeded_url,
        RELEASE_INSERT,
        CHECK,
        allocation=rows.allocation,
        reversal=rows.reversal if reversal else None,
        adjustment=rows.adjustment if adjustment else None,
    )
    assert "ck_allocation_releases_one_cause" in message


def test_an_allocation_is_released_at_most_once(module_seeded_url, rows):
    url = module_seeded_url
    [released] = run_sql(
        url,
        "SELECT allocation_id FROM allocation_releases WHERE id = :id",
        {"id": rows.release},
    )
    message = fails_with(
        url,
        RELEASE_INSERT,
        UNIQUE,
        allocation=released.allocation_id,
        reversal=None,
        adjustment=rows.adjustment,
    )
    assert "uq_allocation_releases_allocation_id" in message
