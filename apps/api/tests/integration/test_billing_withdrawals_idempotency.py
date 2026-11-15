"""The #88 idempotency pattern on both withdrawal confirms (#101 F5, D2, D6):
a replay returns the first body with ``Idempotent-Replayed: true`` and one
set of rows; the same key with another body is ``422
idempotency_key_reused``; a missing or malformed key is ``400``; a ``409``
stores no key; a concurrent duplicate (two connections) runs once; and a
``403 reauth_required`` stores no key, so the same key and body run after
the reauthentication.

The clock is frozen at noon on 2031-03-10 (center zone) for the module."""

import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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
from tests.integration import withdrawal_support as ws
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of

TODAY = ps.TODAY
SOLD = date(2031, 3, 1)
ROUTES = ["credit", "refund"]


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


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    pks.reset(module_seeded_url)


class Target:
    """A paid package to withdraw and the route's bodies."""

    def __init__(self, url: str, client: TestClient, owner: cs.Staff, route: str):
        w = pks.world(url)
        self.package = pks.sell(url, w.student, pks.product(url), SOLD)
        [purchase] = run_sql(
            url, "SELECT * FROM charges WHERE package_id = :p", {"p": self.package}
        )
        cs.recorded_payment(
            url,
            owner.user.id,
            purchase.billing_account_id,
            "1000.00",
            [(purchase.id, "1000.00")],
        )
        self.url = url
        self.route = route
        found = ws.preview_of(client, owner.cookie, package_id=self.package)
        refund_on = TODAY if route == "refund" else None
        self.body = ws.body(found, refund_on=refund_on)
        self.other = ws.body(found, refund_on=refund_on, reason="Otro motivo")

    def post(
        self, client: TestClient, cookie: Any, body: dict[str, Any], key: str | None
    ) -> Response:
        send = ws.refund if self.route == "refund" else ws.credit
        return send(client, cookie, body, key)

    def count(self) -> int:
        return len(ws.withdrawal_rows(self.url, package_id=self.package))

    def refunds(self) -> int:
        return len(
            run_sql(
                self.url,
                "SELECT r.id FROM refunds r"
                " JOIN withdrawals w ON w.id = r.withdrawal_id"
                " WHERE w.package_id = :p",
                {"p": self.package},
            )
        )


@pytest.mark.parametrize("route", ROUTES)
def test_a_replay_returns_the_first_response(
    module_seeded_url: str, client: TestClient, owner: cs.Staff, route: str
) -> None:
    t = Target(module_seeded_url, client, owner, route)
    key = ps.new_key()

    first = t.post(client, owner.cookie, t.body, key)
    second = t.post(client, owner.cookie, t.body, key)

    assert first.status_code == second.status_code == 201, first.text
    assert second.json() == first.json()
    assert second.headers[REPLAYED_HEADER] == "true"
    assert REPLAYED_HEADER not in first.headers
    assert t.count() == 1
    assert t.refunds() == (1 if route == "refund" else 0)


@pytest.mark.parametrize("route", ROUTES)
def test_a_reused_key_with_another_body_is_refused(
    module_seeded_url: str, client: TestClient, owner: cs.Staff, route: str
) -> None:
    t = Target(module_seeded_url, client, owner, route)
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
    module_seeded_url: str,
    client: TestClient,
    owner: cs.Staff,
    route: str,
    key: str | None,
    code: str,
) -> None:
    t = Target(module_seeded_url, client, owner, route)

    response = t.post(client, owner.cookie, t.body, key)

    assert code_of(response) == (400, code)
    assert t.count() == 0


def test_a_409_stores_no_key(
    module_seeded_url: str, client: TestClient, owner: cs.Staff
) -> None:
    url = module_seeded_url
    t = Target(url, client, owner, "credit")
    key = ps.new_key()
    stale = dict(t.body, fingerprint="0" * 64)

    failed = t.post(client, owner.cookie, stale, key)
    assert code_of(failed) == (409, "withdrawal_preview_stale")
    assert cs.key_count(url, key) == 0
    retried = t.post(client, owner.cookie, t.body, key)

    assert retried.status_code == 201, retried.text
    assert REPLAYED_HEADER not in retried.headers
    assert cs.key_count(url, key) == 1


@pytest.mark.parametrize("route", ROUTES)
def test_reauth_required_stores_no_key_and_the_same_key_runs_after(
    module_seeded_url: str, client: TestClient, owner: cs.Staff, route: str
) -> None:
    url = module_seeded_url
    t = Target(url, client, owner, route)
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
    module_seeded_url: str,
    client: TestClient,
    owner: cs.Staff,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    t = Target(module_seeded_url, client, owner, route)
    key = ps.new_key()
    entered, release = _hold_first_call(monkeypatch, "insert_withdrawal")

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
    assert uuid.UUID(responses[0].json()["withdrawal"]["id"])
