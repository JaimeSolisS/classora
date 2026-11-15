"""Helpers for the #96 payment tests (``test_billing_payments_*``).

The routes run with the clock frozen at :data:`NOW` (noon on 2031-03-10 in
the center's zone), so the center's today is :data:`TODAY`. Each test
builds its own families through :func:`family` and :func:`charges`.

- :func:`pay` / :func:`allocate`: the two ``POST`` routes with a fresh (or
  given) ``Idempotency-Key``.
- :func:`assert_invariants`: I2 and I3 over a whole account (#96 B5).
- :func:`payments_of`, :func:`allocations_of`, :func:`account_of`: the
  committed rows.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import IDEMPOTENCY_HEADER
from app.core.money import Money
from tests.factories import billing as factories
from tests.integration.auth_support import SetCookie, signed_headers
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

ZONE = ZoneInfo("America/Mexico_City")
TODAY = date(2031, 3, 10)
NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ZONE)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
MISSING = "00000000-0000-4000-8000-000000000096"


def account_path(family_id: object, suffix: str = "") -> str:
    return f"/api/v1/families/{family_id}/billing-account{suffix}"


def new_key() -> str:
    return f"key-{uuid.uuid4().hex}"


def payment_body(
    amount: str = "100.00",
    mode: str = "oldest_first",
    lines: list[tuple[object, str]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    allocation: dict[str, Any] = {"mode": mode}
    if lines is not None:
        allocation["lines"] = [
            {"charge_id": str(charge), "amount": amount_} for charge, amount_ in lines
        ]
    return {
        "amount": amount,
        "received_on": TODAY.isoformat(),
        "payment_method_code": "cash",
        "allocation": allocation,
        **extra,
    }


def credit_body(
    mode: str = "oldest_first",
    lines: list[tuple[object, str]] | None = None,
    amount: str | None = None,
) -> dict[str, Any]:
    body = payment_body(mode=mode, lines=lines)
    out: dict[str, Any] = {"allocation": body["allocation"]}
    if amount is not None:
        out["amount"] = amount
    return out


def _post(
    client: TestClient,
    cookie: SetCookie,
    path: str,
    body: dict[str, Any],
    key: str | None,
) -> Response:
    headers = signed_headers(cookie)
    if key is not None:
        headers[IDEMPOTENCY_HEADER] = new_key() if key == "new" else key
    client.cookies.clear()
    return client.post(path, json=body, headers=headers)


def pay(
    client: TestClient,
    cookie: SetCookie,
    family_id: object,
    body: dict[str, Any],
    key: str | None = "new",
) -> Response:
    return _post(client, cookie, account_path(family_id, "/payments"), body, key)


def allocate(
    client: TestClient,
    cookie: SetCookie,
    family_id: object,
    body: dict[str, Any],
    key: str | None = "new",
) -> Response:
    return _post(client, cookie, account_path(family_id, "/allocations"), body, key)


def family(url: str, students: int = 1) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A new family with ``students`` new students (no account yet)."""
    return in_uow(url, lambda s: factories.family_with_students(s, students))


def charges(
    url: str, *specs: tuple[uuid.UUID, str, date], service: date | None = None
) -> list[uuid.UUID]:
    """``manual`` charges ``(student, amount, due)`` posted in order (one
    transaction, so their ``posted_at`` differs only by the clock)."""

    async def _post_all(session: AsyncSession) -> list[uuid.UUID]:
        ids = []
        for student, amount, due in specs:
            overrides: dict[str, Any] = {"due": due}
            if service is not None:
                overrides["service_date"] = service
            posted = await factories.charge(session, student, amount, **overrides)
            ids.append(posted.charge_id)
        return ids

    return in_uow(url, _post_all)


def account_of(url: str, family_id: object) -> uuid.UUID | None:
    rows = run_sql(
        url, "SELECT id FROM billing_accounts WHERE family_id = :f", {"f": family_id}
    )
    return rows[0].id if rows else None


def payments_of(url: str, account_id: object) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payments WHERE billing_account_id = :a"
        " ORDER BY received_on, created_at, id",
        {"a": account_id},
    )


def allocations_of(url: str, account_id: object) -> list[Any]:
    """Every allocation of the account with ``valid``, oldest first."""
    return run_sql(
        url,
        "SELECT pa.*, NOT EXISTS (SELECT 1 FROM allocation_releases r"
        " WHERE r.allocation_id = pa.id) AS valid FROM payment_allocations pa"
        " WHERE pa.billing_account_id = :a ORDER BY pa.created_at, pa.id",
        {"a": account_id},
    )


def valid_allocated(url: str, charge_id: object) -> Money:
    [row] = run_sql(
        url,
        "SELECT COALESCE(SUM(pa.amount), 0) AS total FROM payment_allocations pa"
        " WHERE pa.charge_id = :c AND NOT EXISTS (SELECT 1 FROM"
        " allocation_releases r WHERE r.allocation_id = pa.id)",
        {"c": charge_id},
    )
    return Money(row.total)


_I2 = """
SELECT c.id FROM charges c
WHERE c.billing_account_id = :a
  AND (SELECT COALESCE(SUM(pa.amount), 0) FROM payment_allocations pa
       WHERE pa.charge_id = c.id AND NOT EXISTS
         (SELECT 1 FROM allocation_releases r WHERE r.allocation_id = pa.id))
    > c.amount + (SELECT COALESCE(SUM(ca.amount), 0) FROM charge_adjustments ca
                  WHERE ca.charge_id = c.id)
"""

_I3 = """
SELECT p.id FROM payments p
CROSS JOIN LATERAL (
  SELECT COALESCE(SUM(pa.amount), 0) AS allocated FROM payment_allocations pa
  WHERE pa.payment_id = p.id AND NOT EXISTS
    (SELECT 1 FROM allocation_releases r WHERE r.allocation_id = pa.id)) v
WHERE p.billing_account_id = :a
  AND (v.allocated + (SELECT COALESCE(SUM(f.amount), 0) FROM refunds f
                      WHERE f.payment_id = p.id) > p.amount
       OR (v.allocated > 0 AND EXISTS
             (SELECT 1 FROM payment_reversals x WHERE x.payment_id = p.id)))
"""


def assert_invariants(url: str, account_id: object) -> None:
    """I2 (per charge Σ valid allocations ≤ net) and I3 (per payment Σ
    valid allocations + Σ refunds ≤ amount; nothing valid on a reversed
    payment) over the whole account."""
    assert run_sql(url, _I2, {"a": account_id}) == [], "I2"
    assert run_sql(url, _I3, {"a": account_id}) == [], "I3"


def account_credit(url: str, account_id: object) -> Money:
    """The account credit computed independently in SQL (data-model §4.2)."""
    [row] = run_sql(
        url,
        "SELECT COALESCE(SUM(p.amount - COALESCE((SELECT SUM(pa.amount) FROM"
        " payment_allocations pa WHERE pa.payment_id = p.id AND NOT EXISTS"
        " (SELECT 1 FROM allocation_releases r WHERE r.allocation_id = pa.id)), 0)"
        " - COALESCE((SELECT SUM(f.amount) FROM refunds f WHERE f.payment_id ="
        " p.id), 0)), 0) AS credit FROM payments p WHERE p.billing_account_id = :a"
        " AND NOT EXISTS (SELECT 1 FROM payment_reversals x WHERE x.payment_id ="
        " p.id)",
        {"a": account_id},
    )
    return Money(row.credit)
