"""Helpers of the #112 tests: the three payment routes with a fresh (or given)
``Idempotency-Key``, a period finalized by its routes, what was written as rows,
and the stored keys."""

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import CreatedUser, signed_headers
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff
from tests.integration.payroll_calculation_support import approved_lesson, paid_tutor
from tests.integration.payroll_workflow_support import locked, new_key
from tests.integration.test_attendance import lesson

PERIODS: Final = "/api/v1/payroll/periods"
PAYMENTS: Final = "/api/v1/payroll/payments"
NOW: Final = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS: Final = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
PAID_ON: Final = "2026-11-20"
CREATED: Final = (201, None)
DUPLICATE: Final = (409, "payroll_payment_duplicate")
EXCEEDS: Final = (409, "payroll_payment_exceeds_balance")
NOT_FINALIZED: Final = (409, "payroll_period_not_finalized")
ALREADY_REVERSED: Final = (409, "payroll_payment_already_reversed")
OWN_PAY: Final = (403, "payroll_own_compensation")
DENIED: Final = (403, "permission_denied")
REAUTH: Final = (403, "reauth_required")
NOT_FOUND: Final = (404, "not_found")
RECORDED_KEYS: Final = {"id", "payroll_period_id", "paid_state"}
REVERSED_KEYS: Final = {"id", "payroll_payment_id", "paid_state"}


def code(response: Response) -> tuple[int, str | None]:
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json().get("code")


def body(
    amount: str = "30.00",
    reference: str = "SPEI 1001",
    *,
    method: str = "transfer",
    paid_on: str = PAID_ON,
    note: str | None = None,
) -> dict[str, Any]:
    made: dict[str, Any] = {
        "paid_on": paid_on,
        "amount": amount,
        "method_code": method,
        "reference": reference,
    }
    if note is not None:
        made["note"] = note
    return made


def _post(
    client: TestClient, who: Staff, path: str, payload: Any, key: str | None
) -> Response:
    client.cookies.clear()
    headers = {**signed_headers(who.cookie), "Idempotency-Key": key or new_key()}
    return client.post(path, json=payload, headers=headers)


def pay(
    client: TestClient,
    who: Staff,
    period: Any,
    payload: dict[str, Any] | None = None,
    *,
    key: str | None = None,
) -> Response:
    """``POST …/periods/{id}/payments``."""
    return _post(client, who, f"{PERIODS}/{period}/payments", payload or body(), key)


def reverse(
    client: TestClient,
    who: Staff,
    payment: Any,
    reason: str = "Monto equivocado",
    *,
    key: str | None = None,
) -> Response:
    """``POST …/payments/{id}/reversal``."""
    return _post(client, who, f"{PAYMENTS}/{payment}/reversal", {"reason": reason}, key)


def listed(client: TestClient, who: Staff, period: Any) -> Response:
    client.cookies.clear()
    return client.get(
        f"{PERIODS}/{period}/payments", headers=signed_headers(who.cookie)
    )


def worked_period(
    url: str, client: TestClient, owner: Staff, tutor: CreatedUser
) -> uuid.UUID:
    """One approved 55-minute lesson at 120 an hour: an open period of 110.00."""
    period = paid_tutor(url, tutor.id)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    return period


def finalized_period(
    url: str, client: TestClient, owner: Staff, finance: Staff, tutor: CreatedUser
) -> uuid.UUID:
    """:func:`worked_period`, prepared by Finance and finalized by the Owner
    through the routes: ``total_amount`` 110.00."""
    period = worked_period(url, client, owner, tutor)
    locked(client, url, finance, owner, period)
    return period


def payments_of(url: str, period: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payroll_payments WHERE payroll_period_id = :p"
        " ORDER BY created_at, id",
        {"p": period},
    )


def reversals_of(url: str, payment: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payroll_payment_reversals WHERE payroll_payment_id = :p",
        {"p": payment},
    )


def paid_sum(url: str, period: Any) -> Any:
    """The sum of the period's payments without a reversal, by SQL."""
    [row] = run_sql(
        url,
        "SELECT COALESCE(sum(p.amount), 0) AS paid FROM payroll_payments p"
        " WHERE p.payroll_period_id = :p AND NOT EXISTS (SELECT 1 FROM"
        " payroll_payment_reversals r WHERE r.payroll_payment_id = p.id)",
        {"p": period},
    )
    return row.paid


def stored_keys(url: str, user_id: Any) -> list[Any]:
    """The idempotency keys this user claimed on the two payment routes."""
    return run_sql(
        url,
        "SELECT * FROM idempotency_keys WHERE user_id = :u AND (endpoint LIKE"
        " '%/payments' OR endpoint LIKE '%/reversal') ORDER BY created_at",
        {"u": user_id},
    )


def audit_rows(url: str, record_ids: list[Any]) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM audit_events WHERE record_id = ANY(:ids) ORDER BY id",
        {"ids": [uuid.UUID(str(i)) for i in record_ids]},
    )
