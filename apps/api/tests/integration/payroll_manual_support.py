"""Helpers of the M7-08c tests: the two routes with a fresh (or given)
``Idempotency-Key``, what they wrote as rows, and the stored keys."""

import uuid
from typing import Any, Final

from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.auth_support import signed_headers
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff
from tests.integration.payroll_workflow_support import new_key

TUTORS: Final = "/api/v1/payroll/tutors"
PERIODS: Final = "/api/v1/payroll/periods"
CREATED: Final = (201, None)
SETTLED: Final = (409, "payroll_work_settled")
NOT_UNRESOLVED: Final = (409, "payroll_work_not_unresolved")
BEFORE_FIRST: Final = (409, "payroll_work_before_first_period")
LINE_NOT_FINALIZED: Final = (409, "payroll_line_not_finalized")
NOT_NEGATIVE: Final = (409, "payroll_total_not_negative")
NEGATIVE: Final = (409, "payroll_negative_total")
NOT_FOUND: Final = (404, "not_found")
OWN_PAY: Final = (403, "payroll_own_compensation")
REAUTH: Final = (403, "reauth_required")
DENIED: Final = (403, "permission_denied")
REASON: Final = "Pago acordado con la tutora"
POSTED_KEYS: Final = {"id", "kind", "tutor_user_id", "target_period_id", "created_at"}
CARRIED_KEYS: Final = {"period_id", "target_period_id", "adjustment_ids"}


def manual(
    client: TestClient,
    who: Staff,
    tutor_user_id: Any,
    amount: str = "100.00",
    *,
    reason: str = REASON,
    key: str | None = None,
    **reference: Any,
) -> Response:
    """``POST …/adjustments``; ``reference`` is ``origin_line_id`` or
    ``class_session_id``."""
    body = {
        "amount": amount,
        "reason": reason,
        **{name: str(value) for name, value in reference.items()},
    }
    return send(client, who, f"{TUTORS}/{tutor_user_id}/adjustments", body, key=key)


def carry(
    client: TestClient, who: Staff, period: Any, *, key: str | None = None
) -> Response:
    """``POST …/carry-forward``, no body."""
    return send(client, who, f"{PERIODS}/{period}/carry-forward", None, key=key)


def send(
    client: TestClient,
    who: Staff,
    path: str,
    body: Any,
    *,
    key: str | None = None,
    keyed: bool = True,
) -> Response:
    client.cookies.clear()
    headers = signed_headers(who.cookie)
    if keyed:
        headers = {**headers, "Idempotency-Key": key or new_key()}
    return client.post(path, json=body, headers=headers)


def adjustments(url: str, tutor_user_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payroll_adjustments WHERE tutor_user_id = :t"
        " ORDER BY created_at, id",
        {"t": tutor_user_id},
    )


def adjustment_of(url: str, adjustment_id: Any) -> Any:
    [row] = run_sql(
        url, "SELECT * FROM payroll_adjustments WHERE id = :a", {"a": adjustment_id}
    )
    return row


def period_total(url: str, period: uuid.UUID) -> Any:
    """The sum of the period's lines, as ``GET …/lines`` adds them."""
    [row] = run_sql(
        url,
        "SELECT COALESCE(sum(amount), 0) AS total FROM payroll_lines"
        " WHERE payroll_period_id = :p",
        {"p": period},
    )
    return row.total


def stored_keys(url: str, user_id: Any) -> list[Any]:
    """The idempotency keys this user claimed on the two routes."""
    return run_sql(
        url,
        "SELECT * FROM idempotency_keys WHERE user_id = :u AND (endpoint LIKE"
        " '%/adjustments' OR endpoint LIKE '%/carry-forward') ORDER BY created_at",
        {"u": user_id},
    )


def audit_rows(url: str, record_ids: list[Any]) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM audit_events WHERE record_id = ANY(:ids) ORDER BY id",
        {"ids": [uuid.UUID(str(i)) for i in record_ids]},
    )
