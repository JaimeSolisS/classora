"""The #88 idempotency pattern on both #96 ``POST`` routes (F3): a replay
returns the first body with ``Idempotent-Replayed: true`` and writes
nothing; the same key with another body is ``422 idempotency_key_reused``;
a missing or malformed key is ``400``; a failed request stores no key, so
a retry with it runs again; a concurrent duplicate runs exactly once."""

import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import REPLAYED_HEADER
from app.modules.billing import service as billing_service
from app.modules.identity.permissions import FINANCE_STAFF
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import payment_support as ps
from tests.integration.auth_support import auth_client, create_user
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of

TODAY = ps.TODAY


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(client: TestClient, module_seeded_url: str) -> Any:
    return sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )


Post = Callable[..., Response]


def _credit_world(url: str, client: TestClient, staff: Any) -> uuid.UUID:
    """A family with 300.00 of account credit and two open charges."""
    fam, [s1] = ps.family(url)
    ps.charges(url, (s1, "100.00", ps.YESTERDAY), (s1, "100.00", TODAY))
    response = ps.pay(client, staff, fam, ps.payment_body("300.00", "none"))
    assert response.status_code == 201, response.text
    return fam


ROUTES = {
    "payment": (ps.pay, lambda: ps.payment_body("150.00")),
    "credit": (ps.allocate, lambda: ps.credit_body(amount="150.00")),
}


def _world(url: str, client: TestClient, staff: Any, route: str) -> uuid.UUID:
    if route == "credit":
        return _credit_world(url, client, staff)
    fam, [s1] = ps.family(url)
    ps.charges(url, (s1, "100.00", ps.YESTERDAY), (s1, "100.00", TODAY))
    return fam


def _counts(url: str, fam: uuid.UUID) -> tuple[int, int]:
    account = ps.account_of(url, fam)
    if account is None:
        return 0, 0
    return len(ps.payments_of(url, account)), len(ps.allocations_of(url, account))


@pytest.mark.parametrize("route", ROUTES)
def test_a_replay_returns_the_first_response(module_seeded_url, client, staff, route):
    url = module_seeded_url
    post, make = ROUTES[route]
    fam = _world(url, client, staff, route)
    before = _counts(url, fam)
    key = ps.new_key()

    first = post(client, staff, fam, make(), key)
    after_first = _counts(url, fam)
    second = post(client, staff, fam, make(), key)

    assert first.status_code == second.status_code == 201, first.text
    assert second.json() == first.json()
    assert second.headers[REPLAYED_HEADER] == "true"
    assert REPLAYED_HEADER not in first.headers
    assert _counts(url, fam) == after_first != before


@pytest.mark.parametrize("route", ROUTES)
def test_a_reused_key_with_another_body_is_refused(
    module_seeded_url, client, staff, route
):
    url = module_seeded_url
    post, make = ROUTES[route]
    fam = _world(url, client, staff, route)
    key = ps.new_key()
    assert post(client, staff, fam, make(), key).status_code == 201
    counts = _counts(url, fam)
    other = {**make(), "allocation": {"mode": "explicit", "lines": []}}

    response = post(client, staff, fam, other, key)

    assert code_of(response) == (422, "idempotency_key_reused")
    assert _counts(url, fam) == counts


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("key", "code"),
    [(None, "idempotency_key_required"), ("short", "idempotency_key_invalid")],
)
def test_a_missing_or_malformed_key_is_400(
    module_seeded_url, client, staff, route, key, code
):
    url = module_seeded_url
    post, make = ROUTES[route]
    fam = _world(url, client, staff, route)
    counts = _counts(url, fam)

    response = post(client, staff, fam, make(), key)

    assert code_of(response) == (400, code)
    assert _counts(url, fam) == counts


def test_a_failed_request_stores_no_key_so_a_retry_runs_again(
    module_seeded_url, client, staff
):
    url = module_seeded_url
    fam, [s1] = ps.family(url)
    ps.charges(url, (s1, "100.00", TODAY))
    key = ps.new_key()
    body = ps.credit_body()

    failed = ps.allocate(client, staff, fam, body, key)
    assert code_of(failed) == (409, "nothing_to_allocate")
    paid = ps.pay(client, staff, fam, ps.payment_body("40.00", "none"))
    assert paid.status_code == 201

    retried = ps.allocate(client, staff, fam, body, key)

    assert retried.status_code == 201, retried.text
    assert REPLAYED_HEADER not in retried.headers
    assert retried.json()["allocated_total"] == "40.00"


def _hold_first_call(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[threading.Event, threading.Event]:
    """Make the first call of ``billing.service.<name>`` hold its
    transaction (after writing) until ``release`` is set."""
    entered, release = threading.Event(), threading.Event()
    original = getattr(billing_service, name)

    async def held(session: AsyncSession, request: Any) -> Any:
        result = await original(session, request)
        if not entered.is_set():
            entered.set()
            await anyio.to_thread.run_sync(release.wait, 10)
        return result

    monkeypatch.setattr(billing_service, name, held)
    return entered, release


@pytest.mark.parametrize(
    ("route", "primitive"),
    [("payment", "record_payment"), ("credit", "allocate_account_credit")],
)
def test_a_concurrent_duplicate_runs_once(
    module_seeded_url, client, staff, monkeypatch, route, primitive
):
    url = module_seeded_url
    post, make = ROUTES[route]
    fam = _world(url, client, staff, route)
    before = _counts(url, fam)
    key = ps.new_key()
    entered, release = _hold_first_call(monkeypatch, primitive)

    def second() -> Response:
        entered.wait(10)
        timer = threading.Timer(0.5, release.set)
        timer.start()
        return post(client, staff, fam, make(), key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(post, client, staff, fam, make(), key)
        second_future = pool.submit(second)
        responses = [first_future.result(30), second_future.result(30)]

    assert [r.status_code for r in responses] == [201, 201]
    assert sorted(r.headers.get(REPLAYED_HEADER, "") for r in responses) == [
        "",
        "true",
    ]
    assert responses[0].json() == responses[1].json()
    payments, allocations = _counts(url, fam)
    expected_payments = before[0] + (1 if route == "payment" else 0)
    assert payments == expected_payments
    assert allocations - before[1] == len(responses[0].json()["allocations"])
    ps.assert_invariants(url, ps.account_of(url, fam))
