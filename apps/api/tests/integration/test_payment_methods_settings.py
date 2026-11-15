"""Activating and deactivating payment methods (#121 A; ADR-0006 E12,
ADR-0007 D10).

- A1: the list carries each method's ``version``.
- A2: ``PATCH /api/v1/billing/payment-methods/{code}``: ``settings.manage``
  only, and the check order ``401`` → ``csrf_failed`` →
  ``privacy_notice_required`` → ``permission_denied`` → ``422`` → ``404
  payment_method_not_found`` → ``reauth_required`` (only on a change) →
  ``409 version_conflict`` → ``409 payment_method_last_active``.
- A3: the stored value with the current version is a no-op (no
  reauthentication, no version bump, no event); a stale one is ``409``.
- A4: the last active method stays active, also with two concurrent
  deactivations (two sessions).
- A5: a deactivated method refuses new payments with the existing error,
  recorded payments keep it, and reactivating restores it.
- A6: one automatic ``update`` event per change (UUIDv5 ``record_id``).
- A7: no route creates, renames or deletes a method.

The three seeded methods are put back (active, version 1) after each test
with raw SQL (no audit event). The clock is frozen at noon on 2031-03-10
(center zone), so the payments of A5 are dated today.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import clock
from app.core.db import create_sessionmaker
from app.core.reauth import RecentAuthGuard
from app.core.uow import UnitOfWork
from app.main import build_app
from app.modules.billing import service as billing
from app.modules.billing.models import payment_method_record_id
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import payment_support as ps
from tests.integration.auth_support import (
    ORIGIN,
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
)
from tests.integration.catalog_support import get, patch
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, events

METHODS = "/api/v1/billing/payment-methods"
ACTION = "settings.payment_methods_or_time_zone"
SEEDED = ("cash", "other", "transfer")


def method_path(code: str) -> str:
    return f"{METHODS}/{code}"


def stored(url: str) -> dict[str, tuple[bool, int]]:
    """Each method's ``(is_active, version)``."""
    return {
        row.code: (row.is_active, row.version)
        for row in run_sql(
            url, "SELECT code, is_active, version FROM payment_methods ORDER BY code"
        )
    }


ALL_ACTIVE = {code: (True, 1) for code in SEEDED}


def method_events(url: str) -> list[Any]:
    return [
        event
        for code in SEEDED
        for event in events(
            url, record_type="payment_methods", record_id=payment_method_record_id(code)
        )
    ]


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as frozen_at:
        yield frozen_at


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def cookie(client: TestClient, owner: CreatedUser) -> Any:
    return sign_in(client, owner.username)


@pytest.fixture
def reauthed(client: TestClient, cookie: Any) -> Any:
    reauthenticate(client, cookie)
    return cookie


@pytest.fixture(autouse=True)
def restore_methods(module_seeded_url: str) -> Iterator[None]:
    assert stored(module_seeded_url) == ALL_ACTIVE
    yield
    run_sql(
        module_seeded_url, "UPDATE payment_methods SET is_active = true, version = 1"
    )


@pytest.fixture
def no_new_events(module_seeded_url: str) -> Iterator[None]:
    before = len(method_events(module_seeded_url))
    yield
    assert len(method_events(module_seeded_url)) == before


def deactivate(client: TestClient, cookie: Any, code: str, version: int = 1) -> Any:
    return patch(
        client, cookie, method_path(code), {"is_active": False, "version": version}
    )


# --- A1: the list --------------------------------------------------------------------


def test_the_list_carries_each_version(module_seeded_url, client, cookie):
    run_sql(
        module_seeded_url,
        "UPDATE payment_methods SET is_active = false, version = 4"
        " WHERE code = 'transfer'",
    )

    response = get(client, cookie, METHODS)

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"code": "cash", "is_active": True, "version": 1},
        {"code": "other", "is_active": True, "version": 1},
        {"code": "transfer", "is_active": False, "version": 4},
    ]


# --- A2, A6: a change ---------------------------------------------------------------


def test_deactivating_and_reactivating_with_reauthentication(
    module_seeded_url, client, owner, reauthed
):
    off = deactivate(client, reauthed, "other")

    assert off.status_code == 200, off.text
    assert off.json() == {"code": "other", "is_active": False, "version": 2}
    assert stored(module_seeded_url)["other"] == (False, 2)
    on = patch(
        client, reauthed, method_path("other"), {"is_active": True, "version": 2}
    )
    assert on.status_code == 200, on.text
    assert on.json() == {"code": "other", "is_active": True, "version": 3}
    listed = get(client, reauthed, METHODS).json()
    assert {"code": "other", "is_active": True, "version": 3} in listed
    # A6: one update event per change, record_id the code's UUIDv5.
    changes = events(
        module_seeded_url,
        record_type="payment_methods",
        record_id=payment_method_record_id("other"),
    )[-2:]
    assert [(e.action, e.actor_user_id) for e in changes] == [
        ("update", owner.id),
        ("update", owner.id),
    ]
    assert [e.changes for e in changes] == [
        {"is_active": {"old": True, "new": False}},
        {"is_active": {"old": False, "new": True}},
    ]
    assert changes[0].request_id == off.headers["x-request-id"]


def test_an_administrator_deactivates_a_method(module_seeded_url, client):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)
    reauthenticate(client, admin_cookie)

    response = deactivate(client, admin_cookie, "transfer")

    assert response.status_code == 200, response.text
    assert stored(module_seeded_url)["transfer"] == (False, 2)


# --- A2: the check order -------------------------------------------------------------


def test_anonymous_is_401(client, no_new_events):
    client.cookies.clear()
    response = client.patch(
        method_path("other"), json={"is_active": False, "version": 1}
    )

    assert code_of(response) == (401, "authentication_required")


def test_without_the_csrf_token_is_403(
    module_seeded_url, client, cookie, no_new_events
):
    client.cookies.clear()
    response = client.patch(
        method_path("other"),
        json={"is_active": False, "version": 1},
        headers={**ORIGIN, **cookie_header(cookie)},
    )

    assert code_of(response) == (403, "csrf_failed")
    assert stored(module_seeded_url) == ALL_ACTIVE


def test_the_privacy_notice_comes_before_the_permission(
    module_seeded_url, client, no_new_events
):
    pending = create_user(
        module_seeded_url, roles=(OWNER,), privacy_notice_accepted=False
    )

    response = deactivate(client, sign_in(client, pending.username), "other")

    assert code_of(response) == (403, "privacy_notice_required")


@pytest.mark.parametrize(
    "role", [FINANCE_STAFF, ACADEMIC_COORDINATOR, TUTOR, STUDENT, GUARDIAN]
)
def test_only_owners_and_administrators_change_a_method(
    module_seeded_url, client, role, no_new_events
):
    person = create_user(module_seeded_url, roles=(role,))
    other = sign_in(client, person.username)
    reauthenticate(client, other)

    responses = [
        deactivate(client, other, "other"),
        patch(client, other, method_path("other"), {"is_active": True, "version": 1}),
        patch(client, other, method_path("nope"), {"is_active": True, "version": 1}),
        patch(client, other, method_path("other"), {"version": 0}),
    ]

    assert [code_of(r) for r in responses] == [(403, "permission_denied")] * 4
    assert stored(module_seeded_url) == ALL_ACTIVE


INVALID_BODIES: dict[str, dict[str, Any]] = {
    "empty": {},
    "no_version": {"is_active": False},
    "no_is_active": {"version": 1},
    "version_zero": {"is_active": False, "version": 0},
    "null_is_active": {"is_active": None, "version": 1},
    "not_a_boolean": {"is_active": "maybe", "version": 1},
    "unknown_field": {"is_active": False, "version": 1, "label": "Tarjeta"},
    "rename": {"code": "card", "version": 1},
}


@pytest.mark.parametrize("code", ["other", "nope"])
@pytest.mark.parametrize("body", INVALID_BODIES.values(), ids=INVALID_BODIES.keys())
def test_invalid_bodies_are_422_before_the_method_is_looked_up(
    module_seeded_url, client, cookie, code, body, no_new_events
):
    response = patch(client, cookie, method_path(code), body)

    assert code_of(response) == (422, "validation_error"), response.text
    assert stored(module_seeded_url) == ALL_ACTIVE


def test_an_unknown_method_is_404_before_reauthentication(
    client, cookie, no_new_events
):
    responses = [
        deactivate(client, cookie, "card"),
        patch(client, cookie, method_path("card"), {"is_active": True, "version": 9}),
    ]

    assert [code_of(r) for r in responses] == [(404, "payment_method_not_found")] * 2


def test_a_change_without_reauthentication_is_403(
    module_seeded_url, client, cookie, no_new_events
):
    run_sql(
        module_seeded_url,
        "UPDATE payment_methods SET is_active = false WHERE code = 'other'",
    )
    try:
        responses = [
            deactivate(client, cookie, "cash"),
            # Reauthentication comes before the version check.
            deactivate(client, cookie, "cash", version=7),
            patch(
                client, cookie, method_path("other"), {"is_active": True, "version": 1}
            ),
        ]
    finally:
        run_sql(
            module_seeded_url,
            "UPDATE payment_methods SET is_active = true WHERE code = 'other'",
        )

    assert [code_of(r) for r in responses] == [(403, "reauth_required")] * 3
    assert stored(module_seeded_url) == ALL_ACTIVE


def test_a_stale_version_is_409_after_reauthentication(
    module_seeded_url, client, reauthed, no_new_events
):
    response = deactivate(client, reauthed, "cash", version=2)

    assert code_of(response) == (409, "version_conflict")
    assert stored(module_seeded_url) == ALL_ACTIVE


# --- A3: a no-op ---------------------------------------------------------------------


def test_sending_the_stored_value_is_a_no_op_without_reauthentication(
    module_seeded_url, client, cookie, no_new_events
):
    response = patch(
        client, cookie, method_path("other"), {"is_active": True, "version": 1}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"code": "other", "is_active": True, "version": 1}
    assert stored(module_seeded_url) == ALL_ACTIVE


def test_a_no_op_with_a_stale_version_is_409(
    module_seeded_url, client, cookie, no_new_events
):
    response = patch(
        client, cookie, method_path("other"), {"is_active": True, "version": 3}
    )

    assert code_of(response) == (409, "version_conflict")


# --- A4: the last active method ------------------------------------------------------


def test_the_last_active_method_cant_be_deactivated(
    module_seeded_url, client, reauthed
):
    assert deactivate(client, reauthed, "other").status_code == 200
    assert deactivate(client, reauthed, "transfer").status_code == 200
    before = len(method_events(module_seeded_url))

    stale = deactivate(client, reauthed, "cash", version=5)
    last = deactivate(client, reauthed, "cash")

    # The version is checked before the last-active rule.
    assert code_of(stale) == (409, "version_conflict")
    assert code_of(last) == (409, "payment_method_last_active")
    assert stored(module_seeded_url) == {
        "cash": (True, 1),
        "other": (False, 2),
        "transfer": (False, 2),
    }
    assert len(method_events(module_seeded_url)) == before
    # Another method active again: cash can go.
    on = patch(
        client, reauthed, method_path("other"), {"is_active": True, "version": 2}
    )
    assert on.status_code == 200, on.text
    assert deactivate(client, reauthed, "cash").status_code == 200


HOLD_SECONDS = 0.5


def test_two_concurrent_deactivations_leave_one_method_active(module_seeded_url):
    run_sql(
        module_seeded_url,
        "UPDATE payment_methods SET is_active = false WHERE code = 'other'",
    )

    async def deactivate_in(engine: Any, code: str, hold: asyncio.Event | None) -> str:
        guard = RecentAuthGuard(ACTION, clock.now())
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                await billing.set_payment_method_active(
                    uow.session, code, is_active=False, version=1, guard=guard
                )
                if hold is not None:
                    hold.set()
                    await asyncio.sleep(HOLD_SECONDS)
        except billing.PaymentMethodLastActiveError:
            return "payment_method_last_active"
        finally:
            if hold is not None:
                hold.set()
        return "ok"

    async def run() -> list[str]:
        engine_a = short_lived_engine(module_seeded_url)
        engine_b = short_lived_engine(module_seeded_url)
        locked = asyncio.Event()

        async def second() -> str:
            await locked.wait()
            return await deactivate_in(engine_b, "transfer", None)

        try:
            return list(
                await asyncio.gather(deactivate_in(engine_a, "cash", locked), second())
            )
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

    assert asyncio.run(run()) == ["ok", "payment_method_last_active"]
    assert stored(module_seeded_url) == {
        "cash": (False, 2),
        "other": (False, 1),
        "transfer": (True, 1),
    }


# --- A5: the effect ------------------------------------------------------------------


def test_a_deactivated_method_refuses_new_payments_until_reactivated(
    module_seeded_url, client, reauthed
):
    url = module_seeded_url
    fam, _ = ps.family(url)
    body = ps.payment_body("40.00", "none", payment_method_code="other")
    earlier = ps.pay(client, reauthed, fam, body)
    assert earlier.status_code == 201, earlier.text

    assert deactivate(client, reauthed, "other").status_code == 200
    refused = ps.pay(client, reauthed, fam, body)
    # A recorded payment keeps its method.
    listed = get(client, reauthed, ps.account_path(fam, "/payments")).json()["items"]
    on = patch(
        client, reauthed, method_path("other"), {"is_active": True, "version": 2}
    )
    accepted = ps.pay(client, reauthed, fam, body)

    assert code_of(refused) == (422, "payment_method_unavailable")
    assert [e["path"] for e in refused.json()["errors"]] == [
        ["body", "payment_method_code"]
    ]
    assert [p["payment_method_code"] for p in listed] == ["other"]
    assert on.status_code == 200, on.text
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["payment"]["payment_method_code"] == "other"


# --- A7: no create, rename or delete -------------------------------------------------


def test_only_the_list_and_the_activation_route_exist():
    paths = build_app().openapi()["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(METHODS)
        for method in operations
    }

    assert routes == {("GET", METHODS), ("PATCH", f"{METHODS}/{{code}}")}
