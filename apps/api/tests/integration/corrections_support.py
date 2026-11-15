"""Helpers for the #100 correction tests (``test_billing_manual_*``,
``test_billing_payment_reversals``, ``test_billing_refunds``,
``test_billing_package_cancellation``, ``test_billing_corrections_*``).

The routes run with the clock frozen at :data:`payment_support.NOW` (noon on
2031-03-10, center zone), so the center's today is ``TODAY`` and a
reauthentication stays recent for the whole module (the window never
passes on a frozen clock).

- :func:`staff`: a user of ``roles``, signed in and reauthenticated.
- :func:`charge_post`, :func:`adjust`, :func:`reverse`, :func:`refund`,
  :func:`cancel`: the five ``POST`` routes with a fresh (or given)
  ``Idempotency-Key``; :func:`charge_get`, :func:`payment_get`: the reads.
- :func:`recorded_payment`: a payment through ``billing.record_payment``
  (with its receipt), committed.
- :func:`assert_invariants`: I1, I2, I3 (with "no refund on a reversed
  payment" and "no reversal of a refunded payment") and I8 over a whole
  account (#100 B6).
- :func:`snapshot`: every column of the rows a correction must leave alone.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import AllocationLine, AllocationRequest
from tests.integration import payment_support as ps
from tests.integration import receipt_support as rs
from tests.integration.auth_support import CreatedUser, SetCookie, create_user
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in

TODAY = ps.TODAY
PACKAGES = "/api/v1/billing/packages"


@dataclass(frozen=True)
class Staff:
    user: CreatedUser
    cookie: SetCookie


def staff(
    url: str,
    client: TestClient,
    roles: tuple[str, ...],
    *,
    delegations: list[str] | None = None,
    reauth: bool = True,
) -> Staff:
    user = create_user(url, roles=roles, delegations=delegations or [])
    cookie = sign_in(client, user.username)
    if reauth:
        reauthenticate(client, cookie)
    return Staff(user=user, cookie=cookie)


def charge_body(
    student: object, amount: str = "350.00", **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "student_id": str(student),
        "description": "Libro de lectura",
        "amount": amount,
        "service_date": TODAY.isoformat(),
        "due_date": TODAY.isoformat(),
    }
    body.update(extra)
    return body


def refund_body(amount: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "amount": amount,
        "refunded_on": TODAY.isoformat(),
        "payment_method_code": "transfer",
        "reference": "SPEI-900",
        "reason": "Saldo a favor devuelto",
    }
    body.update(extra)
    return body


def charge_post(
    client: TestClient,
    cookie: SetCookie,
    family: object,
    body: dict[str, Any],
    key: str | None = "new",
) -> Response:
    return ps._post(client, cookie, ps.account_path(family, "/charges"), body, key)


def adjust(
    client: TestClient,
    cookie: SetCookie,
    family: object,
    charge: object,
    amount: str,
    reason: str = "Error de captura",
    key: str | None = "new",
) -> Response:
    return ps._post(
        client,
        cookie,
        ps.account_path(family, f"/charges/{charge}/adjustments"),
        {"amount": amount, "reason": reason},
        key,
    )


def reverse(
    client: TestClient,
    cookie: SetCookie,
    family: object,
    payment: object,
    reason: str = "Transferencia rechazada",
    key: str | None = "new",
) -> Response:
    return ps._post(
        client,
        cookie,
        ps.account_path(family, f"/payments/{payment}/reversal"),
        {"reason": reason},
        key,
    )


def refund(
    client: TestClient,
    cookie: SetCookie,
    family: object,
    payment: object,
    body: dict[str, Any],
    key: str | None = "new",
) -> Response:
    return ps._post(
        client,
        cookie,
        ps.account_path(family, f"/payments/{payment}/refunds"),
        body,
        key,
    )


def cancel(
    client: TestClient,
    cookie: SetCookie,
    package: object,
    body: dict[str, Any] | None = None,
    key: str | None = "new",
) -> Response:
    return ps._post(
        client,
        cookie,
        f"{PACKAGES}/{package}/cancellation",
        body if body is not None else {"reason": "La familia se mudó"},
        key,
    )


def charge_get(
    client: TestClient, cookie: SetCookie, family: object, charge: object
) -> Response:
    return get(client, cookie, ps.account_path(family, f"/charges/{charge}"))


def payment_get(
    client: TestClient, cookie: SetCookie, family: object, payment: object
) -> Response:
    return get(client, cookie, ps.account_path(family, f"/payments/{payment}"))


def created(response: Response) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def recorded_payment(
    url: str,
    actor: uuid.UUID,
    account: uuid.UUID,
    amount: str,
    lines: list[tuple[uuid.UUID, str]] | None = None,
) -> uuid.UUID:
    """A payment through ``record_payment`` (``explicit`` lines, else
    ``none``), committed with its receipt."""
    allocation = (
        AllocationRequest(mode="none")
        if lines is None
        else AllocationRequest(
            mode="explicit",
            lines=tuple(AllocationLine(charge_id=c, amount=Money(a)) for c, a in lines),
        )
    )
    return rs.record(url, actor, rs.draft(account, amount, allocation)).payment_id


def new_account(
    url: str, students: int = 1
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    return rs.new_account(url, students)


def key_count(url: str, key: str) -> int:
    return len(
        run_sql(url, "SELECT id FROM idempotency_keys WHERE key = :k", {"k": key})
    )


_I1 = """
SELECT c.id FROM charges c WHERE c.billing_account_id = :a
  AND c.amount + (SELECT COALESCE(SUM(ca.amount), 0) FROM charge_adjustments ca
                  WHERE ca.charge_id = c.id) < 0
"""
_REVERSED_REFUNDED = """
SELECT p.id FROM payments p WHERE p.billing_account_id = :a
  AND EXISTS (SELECT 1 FROM payment_reversals x WHERE x.payment_id = p.id)
  AND EXISTS (SELECT 1 FROM refunds f WHERE f.payment_id = p.id)
"""
_I8 = """
SELECT k.id FROM packages k WHERE k.billing_account_id = :a
  AND ((SELECT COALESCE(SUM(t.units), 0) FROM package_credit_transactions t
        WHERE t.package_id = k.id) < 0
    OR (EXISTS (SELECT 1 FROM package_credit_transactions t
                WHERE t.package_id = k.id AND t.kind = 'reversal')
        AND (SELECT COALESCE(SUM(t.units), 0) FROM package_credit_transactions t
             WHERE t.package_id = k.id) <> 0))
"""


def assert_invariants(url: str, account: object) -> None:
    """#100 B6 over a whole account: I1, I2, I3 (#96's helper), a payment
    never both reversed and refunded, and I8 (remaining ≥ 0; 0 after a
    cancellation)."""
    assert run_sql(url, _I1, {"a": account}) == [], "I1"
    ps.assert_invariants(url, account)
    assert run_sql(url, _REVERSED_REFUNDED, {"a": account}) == [], "I3 reversal"
    assert run_sql(url, _I8, {"a": account}) == [], "I8"


def snapshot(url: str, account: object) -> dict[str, list[dict[str, Any]]]:
    """Every column of the account's charges, payments, allocations,
    receipts and package rows, by id: a correction adds rows, never changes
    these (E2)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for table, where in (
        ("charges", "billing_account_id = :a"),
        ("payments", "billing_account_id = :a"),
        ("payment_allocations", "billing_account_id = :a"),
        ("receipts", "billing_account_id = :a"),
        ("packages", "billing_account_id = :a"),
        (
            "package_credit_transactions",
            "package_id IN (SELECT id FROM packages WHERE billing_account_id = :a)",
        ),
        (
            "charge_adjustments",
            "billing_account_id = :a",
        ),
    ):
        rows = run_sql(
            url,
            f"SELECT * FROM {table} WHERE {where} ORDER BY id",  # noqa: S608
            {"a": account},
        )
        out[table] = [dict(row._mapping) for row in rows]
    return out


def unchanged(before: dict[str, list[dict[str, Any]]], after: dict[str, Any]) -> None:
    """Every row of ``before`` is in ``after`` with the same columns."""
    for table, rows in before.items():
        later = {row["id"]: row for row in after[table]}
        for row in rows:
            assert later.get(row["id"]) == row, (table, row["id"])


def balances(url: str, family: object) -> Any:
    from tests.integration.lifecycle_support import in_uow

    return in_uow(url, lambda s: billing.family_balance(s, family, today=TODAY))
