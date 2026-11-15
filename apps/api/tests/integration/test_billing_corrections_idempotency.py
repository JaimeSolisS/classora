"""The #88 idempotency pattern on the five #100 ``POST`` routes (F6, C7): a
replay returns the first body with ``Idempotent-Replayed: true`` and writes
nothing; the same key with another body is ``422 idempotency_key_reused``;
a missing or malformed key is ``400``; a ``409`` stores no key, so a retry
with it runs again; a concurrent duplicate runs exactly once; and a ``403
reauth_required`` stores no key, so the same key and body run after the
reauthentication.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import REPLAYED_HEADER
from app.modules.billing import service as billing_service
from app.modules.identity.permissions import OWNER
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration.auth_support import SetCookie, auth_client
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of

TODAY = ps.TODAY
SOLD = date(2031, 3, 1)
ROUTES = ["charge", "adjustment", "reversal", "refund", "cancellation"]
PRIMITIVES = {
    "charge": "post_manual_charge",
    "adjustment": "post_manual_adjustment",
    "reversal": "reverse_payment",
    "refund": "refund_payment",
    "cancellation": "cancel_package",
}


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> cs.Staff:
    return cs.staff(module_seeded_url, client, (OWNER,))


@dataclass(frozen=True)
class Target:
    """One route's world: how to post it, its body, another body, and how
    many rows it has written so far."""

    post: Callable[[TestClient, SetCookie, dict[str, Any], str | None], Response]
    body: dict[str, Any]
    other: dict[str, Any]
    count: Callable[[], int]


def _count(url: str, sql: str, **params: Any) -> Callable[[], int]:
    return lambda: len(run_sql(url, sql, params))


def target(url: str, owner: cs.Staff, route: str) -> Target:
    fam, account, [student] = cs.new_account(url)
    if route == "charge":
        return Target(
            post=lambda c, k, b, key: cs.charge_post(c, k, fam, b, key),
            body=cs.charge_body(student),
            other=cs.charge_body(student, "1.00"),
            count=_count(
                url, "SELECT id FROM charges WHERE billing_account_id = :a", a=account
            ),
        )
    if route == "adjustment":
        [charge] = ps.charges(url, (student, "100.00", TODAY))
        return Target(
            post=lambda c, k, b, key: ps._post(
                c, k, ps.account_path(fam, f"/charges/{charge}/adjustments"), b, key
            ),
            body={"amount": "-10.00", "reason": "Error"},
            other={"amount": "-11.00", "reason": "Error"},
            count=_count(
                url, "SELECT id FROM charge_adjustments WHERE charge_id = :c", c=charge
            ),
        )
    if route in ("reversal", "refund"):
        payment = cs.recorded_payment(url, owner.user.id, account, "100.00")
        if route == "reversal":
            return Target(
                post=lambda c, k, b, key: ps._post(
                    c, k, ps.account_path(fam, f"/payments/{payment}/reversal"), b, key
                ),
                body={"reason": "Rebotó"},
                other={"reason": "Otra razón"},
                count=_count(
                    url,
                    "SELECT id FROM payment_reversals WHERE payment_id = :p",
                    p=payment,
                ),
            )
        return Target(
            post=lambda c, k, b, key: cs.refund(c, k, fam, payment, b, key),
            body=cs.refund_body("10.00"),
            other=cs.refund_body("11.00"),
            count=_count(
                url, "SELECT id FROM refunds WHERE payment_id = :p", p=payment
            ),
        )
    pks.reset(url)
    w = pks.world(url)
    package = pks.sell(url, w.student, pks.product(url), SOLD)
    return Target(
        post=lambda c, k, b, key: cs.cancel(c, k, package, b, key),
        body={"reason": "Se mudó"},
        other={"reason": "Se mudó", "credit_amount": "0.00"},
        count=_count(
            url,
            "SELECT id FROM package_credit_transactions WHERE package_id = :p"
            " AND kind = 'reversal'",
            p=package,
        ),
    )


@pytest.mark.parametrize("route", ROUTES)
def test_a_replay_returns_the_first_response(module_seeded_url, client, owner, route):
    t = target(module_seeded_url, owner, route)
    key = ps.new_key()

    first = t.post(client, owner.cookie, t.body, key)
    second = t.post(client, owner.cookie, t.body, key)

    assert first.status_code == second.status_code == 201, first.text
    assert second.json() == first.json()
    assert second.headers[REPLAYED_HEADER] == "true"
    assert REPLAYED_HEADER not in first.headers
    assert t.count() == 1


@pytest.mark.parametrize("route", ROUTES)
def test_a_reused_key_with_another_body_is_refused(
    module_seeded_url, client, owner, route
):
    t = target(module_seeded_url, owner, route)
    key = ps.new_key()
    assert t.post(client, owner.cookie, t.body, key).status_code == 201

    response = t.post(client, owner.cookie, t.other, key)

    assert code_of(response) == (422, "idempotency_key_reused")
    assert t.count() == 1


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("key", "code"),
    [(None, "idempotency_key_required"), ("short", "idempotency_key_invalid")],
)
def test_a_missing_or_malformed_key_is_400(
    module_seeded_url, client, owner, route, key, code
):
    t = target(module_seeded_url, owner, route)

    response = t.post(client, owner.cookie, t.body, key)

    assert code_of(response) == (400, code)
    assert t.count() == 0


def test_a_409_stores_no_key_so_a_retry_runs_again(module_seeded_url, client, owner):
    url = module_seeded_url
    fam, account, _ = cs.new_account(url)
    payment = cs.recorded_payment(url, owner.user.id, account, "20.00")
    key = ps.new_key()
    body = cs.refund_body("30.00")

    failed = cs.refund(client, owner.cookie, fam, payment, body, key)
    assert code_of(failed) == (409, "refund_exceeds_remainder")
    assert cs.key_count(url, key) == 0
    # A second payment doesn't help (payment-scoped), so make the remainder
    # big enough by refunding against a bigger payment with the same key.
    bigger = cs.recorded_payment(url, owner.user.id, account, "50.00")
    retried = cs.refund(client, owner.cookie, fam, bigger, body, key)

    assert retried.status_code == 201, retried.text
    assert REPLAYED_HEADER not in retried.headers
    assert cs.key_count(url, key) == 1


@pytest.mark.parametrize("route", [r for r in ROUTES if r != "charge"])
def test_reauth_required_stores_no_key_and_the_same_key_runs_after(
    module_seeded_url, client, owner, route
):
    # A manual charge needs no reauthentication (R3).
    url = module_seeded_url
    t = target(url, owner, route)
    cookie = sign_in(client, owner.user.username)
    key = ps.new_key()

    refused = t.post(client, cookie, t.body, key)
    assert code_of(refused) == (403, "reauth_required")
    assert cs.key_count(url, key) == 0
    reauthenticate(client, cookie)
    retried = t.post(client, cookie, t.body, key)

    assert retried.status_code == 201, retried.text
    assert REPLAYED_HEADER not in retried.headers
    assert t.count() == 1


def _hold_first_call(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[threading.Event, threading.Event]:
    """Make the first call of ``billing.service.<name>`` hold its
    transaction (after writing) until ``release`` is set."""
    entered, release = threading.Event(), threading.Event()
    original = getattr(billing_service, name)

    async def held(session: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        result = await original(session, *args, **kwargs)
        if not entered.is_set():
            entered.set()
            await anyio.to_thread.run_sync(release.wait, 10)
        return result

    monkeypatch.setattr(billing_service, name, held)
    return entered, release


@pytest.mark.parametrize("route", ROUTES)
def test_a_concurrent_duplicate_runs_once(
    module_seeded_url, client, owner, monkeypatch, route
):
    t = target(module_seeded_url, owner, route)
    key = ps.new_key()
    entered, release = _hold_first_call(monkeypatch, PRIMITIVES[route])

    def second() -> Response:
        entered.wait(10)
        timer = threading.Timer(0.5, release.set)
        timer.start()
        return t.post(client, owner.cookie, t.body, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(t.post, client, owner.cookie, t.body, key)
        second_future = pool.submit(second)
        responses = [first_future.result(30), second_future.result(30)]

    assert [r.status_code for r in responses] == [201, 201], [r.text for r in responses]
    assert sorted(r.headers.get(REPLAYED_HEADER, "") for r in responses) == ["", "true"]
    assert responses[0].json() == responses[1].json()
    assert t.count() == 1
