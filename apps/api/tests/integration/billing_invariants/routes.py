"""HTTP helpers of the billing invariant suite (#103 D4, D8, E1, E2).

- :func:`portal_check` (D4, the #102 candidate): for each family's
  guardian, the summary's outstanding / credit / net equal the sums over
  that guardian's own ``…/charges``, ``…/payments`` and ``…/ledger``
  responses, through the real ASGI app. Credit counts the
  ``unallocated_amount`` of payments that aren't reversed (api.md
  ``PaymentItem``: "account credit only while not reversed").
- :class:`Staff`: an Owner or Finance user signed in (and reauthenticated)
  once per world copy; :func:`post_json` sends one ``POST`` with a fresh or
  given ``Idempotency-Key``.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.core.idempotency import IDEMPOTENCY_HEADER
from app.modules.billing import schemas as bschemas
from app.workflows import billing_corrections, billing_payments, packages, withdrawals
from app.workflows.package_schemas import PackagePurchase
from tests.integration import withdrawal_support
from tests.integration.auth_support import SetCookie, auth_client, signed_headers
from tests.integration.billing_invariants import checks
from tests.integration.billing_invariants import world as worlds
from tests.integration.billing_invariants.world import World
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import in_uow

PAGE = 100


def account_path(family_id: object, suffix: str = "") -> str:
    return f"/api/v1/families/{family_id}/billing-account{suffix}"


def new_key() -> str:
    return f"binv-{uuid.uuid4().hex}"


@dataclass(frozen=True)
class Staff:
    cookie: SetCookie

    @classmethod
    def signed_in(cls, client: TestClient, username: str, *, reauth: bool) -> Staff:
        cookie = sign_in(client, username)
        if reauth:
            reauthenticate(client, cookie)
        return cls(cookie)


def post_json(
    client: TestClient,
    staff: Staff,
    path: str,
    body: dict[str, Any],
    key: str | None = None,
) -> Response:
    headers = signed_headers(staff.cookie)
    headers[IDEMPOTENCY_HEADER] = key or new_key()
    client.cookies.clear()
    response: Response = client.post(path, json=body, headers=headers)
    return response


def _pages(client: TestClient, cookie: SetCookie, path: str) -> list[Any]:
    items: list[Any] = []
    for offset in range(0, 100 * PAGE, PAGE):
        response = get(client, cookie, path, limit=PAGE, offset=offset)
        assert response.status_code == 200, response.text
        body = response.json()
        items.extend(body["items"])
        if len(items) >= body["total"]:
            return items
    raise AssertionError(f"{path} never ended")


def _ledger(client: TestClient, cookie: SetCookie, path: str) -> list[Any]:
    rows: list[Any] = []
    cursor: str | None = None
    for _ in range(100):
        params: dict[str, Any] = {"limit": PAGE}
        if cursor is not None:
            params["cursor"] = cursor
        response = get(client, cookie, path, **params)
        assert response.status_code == 200, response.text
        rows.extend(response.json()["items"])
        cursor = response.json()["next_cursor"]
        if cursor is None:
            return rows
    raise AssertionError("the ledger never ended")


def _sum(values: Any) -> Decimal:
    return sum((Decimal(v) for v in values), Decimal("0"))


def portal_mismatches(client: TestClient, world: World) -> list[str]:
    """D4 for every family (one guardian login each)."""
    problems: list[str] = []
    for info in world.families.values():
        cookie = sign_in(client, info.guardian.username)
        summary = get(client, cookie, account_path(info.id)).json()
        charges = _pages(client, cookie, account_path(info.id, "/charges"))
        payments = _pages(client, cookie, account_path(info.id, "/payments"))
        ledger = _ledger(client, cookie, account_path(info.id, "/ledger"))
        outstanding = _sum(c["open"] for c in charges if c["status"] != "void")
        credit = _sum(p["unallocated_amount"] for p in payments if not p["is_reversed"])
        effects = _sum(e["balance_effect"] for e in ledger)
        got = (
            Decimal(summary["outstanding"]),
            Decimal(summary["account_credit"]),
            Decimal(summary["net_balance"]),
        )
        if got != (outstanding, credit, effects) or effects != outstanding - credit:
            problems.append(
                f"{info.label}: summary (outstanding, credit, net) = {got}; from the"
                f" guardian's lists = ({outstanding}, {credit}, {effects})"
            )
    return problems


def portal_check(url: str, world: World) -> None:
    """Call under the sequence's frozen clock."""
    with auth_client(url) as client:
        problems = portal_mismatches(client, world)
    assert not problems, "portal check (D4):\n" + "\n".join(problems)


@dataclass
class Builder:
    """Builds race and walk targets on a world copy through the real
    workflow entry points (owner as the actor; family ``F1``, student
    ``A1`` unless given)."""

    url: str
    world: worlds.World
    catalogue: checks.Catalogue = field(default_factory=checks.Catalogue)

    @property
    def family(self) -> worlds.FamilyInfo:
        return self.world.families["F1"]

    @property
    def student(self) -> uuid.UUID:
        return self.world.students["A1"]

    def run(self, call: Callable[[Any], Any]) -> Any:
        return in_uow(self.url, call, actor_id=self.world.owner.id)

    def charge(self, amount: str = "300.00") -> uuid.UUID:
        body = bschemas.ManualChargeCreate.model_validate(
            {
                "student_id": str(self.student),
                "description": "Material",
                "amount": amount,
                "service_date": worlds.TODAY.isoformat(),
                "due_date": worlds.TODAY.isoformat(),
            }
        )
        posted = self.run(
            lambda s: billing_corrections.post_manual_charge(s, self.family.id, body)
        )
        return uuid.UUID(str(posted.charge.id))

    def payment(
        self, amount: str, lines: list[tuple[uuid.UUID, str]] | None = None
    ) -> uuid.UUID:
        allocation: dict[str, Any] = {"mode": "none" if lines is None else "explicit"}
        if lines is not None:
            allocation["lines"] = [{"charge_id": str(c), "amount": a} for c, a in lines]
        body = bschemas.PaymentCreate.model_validate(
            {
                "amount": amount,
                "received_on": worlds.TODAY.isoformat(),
                "payment_method_code": "cash",
                "allocation": allocation,
            }
        )
        recorded = self.run(
            lambda s: billing_payments.record_payment(s, self.family.id, body)
        )
        return uuid.UUID(str(recorded.payment.id))

    def package(self, *, paid: bool = False) -> uuid.UUID:
        detail = self.run(
            lambda s: packages.purchase_package(
                s,
                PackagePurchase(
                    student_id=self.student, package_product_id=self.world.product
                ),
            )
        )
        package_id = uuid.UUID(str(detail.id))
        if paid:
            [charge] = run_sql(
                self.url,
                "SELECT id, amount FROM charges WHERE package_id = :p",
                {"p": package_id},
            )
            self.payment(str(charge.amount), [(charge.id, str(charge.amount))])
        return package_id

    def withdrawal_body(self, package_id: uuid.UUID, *, refund: bool) -> dict[str, Any]:
        found = self.run(
            lambda s: withdrawals.preview_withdrawal(
                s, "package", package_id, now=worlds.NOW.astimezone(UTC)
            )
        )
        return withdrawal_support.body(
            found.model_dump(mode="json"), refund_on=worlds.TODAY if refund else None
        )
