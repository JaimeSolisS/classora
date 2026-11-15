"""Idempotent-route walk (#103 E1): every route of ``tests/authz/matrix.toml``
with ``idempotency = true`` whose path starts with ``/api/v1/billing/`` or
contains ``/billing-account/`` gets two **concurrent** requests with the
same ``Idempotency-Key`` and body through the real ASGI app (the #88
``ThreadPoolExecutor`` pattern). Exactly one business posting is written,
both responses have the same status and body (one of them replayed), and
the catalogue holds afterwards.

A route without an entry in :data:`CASES` fails the walk, naming it: a new
idempotent billing route adds a case here."""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.idempotency import REPLAYED_HEADER
from tests.authz.harness import load_matrix
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.billing_invariants import harness
from tests.integration.billing_invariants import world as worlds
from tests.integration.billing_invariants.routes import (
    Builder,
    Staff,
    account_path,
    new_key,
    post_json,
)
from tests.integration.conftest import run_sql

REPEATS = 3


def idempotent_billing_routes() -> list[str]:
    return sorted(
        f"{entry.method} {entry.path}"
        for entry in load_matrix()
        if entry.idempotency
        and (
            entry.path.startswith("/api/v1/billing/")
            or "/billing-account/" in entry.path
        )
    )


@dataclass(frozen=True)
class Prepared:
    path: str
    body: dict[str, Any]
    count: tuple[str, Any]
    """``(sql, id)``: counts the business rows the route writes."""


type Setup = Callable[["Builder"], Prepared]


def _package_purchase(w: Builder) -> Prepared:
    return Prepared(
        "/api/v1/billing/packages",
        {"student_id": str(w.student), "package_product_id": str(w.world.product)},
        ("SELECT COUNT(*) FROM packages WHERE student_id = :id", w.student),
    )


def _package_cancellation(w: Builder) -> Prepared:
    package_id = w.package()
    return Prepared(
        f"/api/v1/billing/packages/{package_id}/cancellation",
        {"reason": "La familia se mudó"},
        (
            "SELECT COUNT(*) FROM package_credit_transactions WHERE kind = 'reversal'"
            " AND package_id = :id",
            package_id,
        ),
    )


def _withdrawal(refund: bool) -> Setup:
    def setup(w: Builder) -> Prepared:
        package_id = w.package(paid=refund)
        return Prepared(
            "/api/v1/billing/withdrawals/" + ("refund" if refund else "account-credit"),
            w.withdrawal_body(package_id, refund=refund),
            ("SELECT COUNT(*) FROM withdrawals WHERE package_id = :id", package_id),
        )

    return setup


def _allocations(w: Builder) -> Prepared:
    w.charge("120.00")
    w.payment("80.00")
    return Prepared(
        account_path(w.family.id, "/allocations"),
        {"allocation": {"mode": "oldest_first"}},
        (
            "SELECT COUNT(*) FROM payment_allocations WHERE billing_account_id = :id",
            w.family.account,
        ),
    )


def _charges(w: Builder) -> Prepared:
    return Prepared(
        account_path(w.family.id, "/charges"),
        {
            "student_id": str(w.student),
            "description": "Libro",
            "amount": "75.00",
            "service_date": worlds.TODAY.isoformat(),
            "due_date": worlds.TODAY.isoformat(),
        },
        (
            "SELECT COUNT(*) FROM charges WHERE billing_account_id = :id",
            w.family.account,
        ),
    )


def _adjustments(w: Builder) -> Prepared:
    charge = w.charge()
    return Prepared(
        account_path(w.family.id, f"/charges/{charge}/adjustments"),
        {"amount": "-25.00", "reason": "Descuento acordado"},
        ("SELECT COUNT(*) FROM charge_adjustments WHERE charge_id = :id", charge),
    )


def _payments(w: Builder) -> Prepared:
    w.charge("90.00")
    return Prepared(
        account_path(w.family.id, "/payments"),
        {
            "amount": "60.00",
            "received_on": worlds.TODAY.isoformat(),
            "payment_method_code": "cash",
            "allocation": {"mode": "oldest_first"},
        },
        (
            "SELECT COUNT(*) FROM payments WHERE billing_account_id = :id",
            w.family.account,
        ),
    )


def _refunds(w: Builder) -> Prepared:
    payment = w.payment("50.00")
    return Prepared(
        account_path(w.family.id, f"/payments/{payment}/refunds"),
        {
            "amount": "20.00",
            "refunded_on": worlds.TODAY.isoformat(),
            "payment_method_code": "transfer",
            "reference": "SPEI-1",
            "reason": "Saldo a favor",
        },
        ("SELECT COUNT(*) FROM refunds WHERE payment_id = :id", payment),
    )


def _reversal(w: Builder) -> Prepared:
    charge = w.charge("40.00")
    payment = w.payment("40.00", [(charge, "40.00")])
    return Prepared(
        account_path(w.family.id, f"/payments/{payment}/reversal"),
        {"reason": "Cheque rechazado"},
        ("SELECT COUNT(*) FROM payment_reversals WHERE payment_id = :id", payment),
    )


CASES: dict[str, Setup] = {
    "POST /api/v1/billing/packages": _package_purchase,
    "POST /api/v1/billing/packages/{package_id}/cancellation": _package_cancellation,
    "POST /api/v1/billing/withdrawals/account-credit": _withdrawal(refund=False),
    "POST /api/v1/billing/withdrawals/refund": _withdrawal(refund=True),
    "POST /api/v1/families/{family_id}/billing-account/allocations": _allocations,
    "POST /api/v1/families/{family_id}/billing-account/charges": _charges,
    "POST /api/v1/families/{family_id}/billing-account/charges/{charge_id}"
    "/adjustments": (_adjustments),
    "POST /api/v1/families/{family_id}/billing-account/payments": _payments,
    "POST /api/v1/families/{family_id}/billing-account/payments/{payment_id}/refunds": (
        _refunds
    ),
    "POST /api/v1/families/{family_id}/billing-account/payments/{payment_id}"
    "/reversal": (_reversal),
}


@pytest.fixture(scope="module")
def walk(
    database_url: str, world_template: worlds.Template
) -> Iterator[tuple[Builder, TestClient, Staff]]:
    url = worlds.clone_database(database_url, world_template.url)
    try:
        with frozen_clock(worlds.NOW.astimezone(UTC)), auth_client(url) as client:
            owner = Staff.signed_in(
                client, world_template.world.owner.username, reauth=True
            )
            yield Builder(url, world_template.world), client, owner
    finally:
        worlds.drop_database(database_url, url)


def test_billing_invariant_every_idempotent_billing_route_has_a_case() -> None:
    routes = idempotent_billing_routes()
    assert routes, "the matrix lists no idempotent billing route"
    missing = [route for route in routes if route not in CASES]
    assert missing == [], f"idempotent billing routes without a #103 E1 case: {missing}"
    assert sorted(CASES) == routes, "a case for a route that is no longer idempotent"


def _count(url: str, count: tuple[str, Any]) -> int:
    sql, row_id = count
    [row] = run_sql(url, sql, {"id": row_id})
    return int(row[0])


@pytest.mark.parametrize("repeat", range(REPEATS))
@pytest.mark.parametrize("route", idempotent_billing_routes())
def test_billing_invariant_concurrent_duplicate_posts_once(
    walk: tuple[Builder, TestClient, Staff], route: str, repeat: int
) -> None:
    w, client, owner = walk
    setup = CASES.get(route)
    assert setup is not None, f"no #103 E1 case for {route}"
    prepared = setup(w)
    before = _count(w.url, prepared.count)
    key = new_key()
    start = threading.Barrier(2)

    def send() -> Response:
        start.wait(10)
        return post_json(client, owner, prepared.path, prepared.body, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send), pool.submit(send)]
        first, second = (f.result(60) for f in futures)

    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json() == second.json()
    replayed = sorted(r.headers.get(REPLAYED_HEADER, "") for r in (first, second))
    assert replayed == ["", "true"], replayed
    assert _count(w.url, prepared.count) == before + 1, route
    harness.assert_catalogue(w.url, worlds.TODAY, w.catalogue, what=f"after {route}")
