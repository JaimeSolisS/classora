"""Who may preview, confirm and read withdrawals (#101 F7; S5, D1-D3).

The S5 holders, per route and subject:

- enrollment + account credit: Owner, Administrator (``schedule.manage``
  is checked in the handler, so Finance Staff get ``403
  permission_denied`` before anything is written);
- enrollment + refund: Owner, or an Administrator delegated
  ``finance.refund.approve``;
- package + account credit: Owner, Administrator, Finance Staff;
- package + refund: Owner, or a delegated Administrator.

Coordinators, tutors, students and guardians get ``403``; anonymous
``401``; each confirm needs a recent reauthentication (``403
reauth_required``, no key stored). The reads are staff-only and bounded.

The clock is frozen for the module on a series timeline (a Sunday noon)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_REFUND_APPROVE,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.workflows import withdrawals
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import enrollment_charge_support as ec
from tests.integration import package_support as pks
from tests.integration import withdrawal_support as ws
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.billing_ledger_support import count_statements
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.series_support import Timeline, timeline

TL: Timeline = timeline(2087)
"""A decade of its own (the module's database is shared by its tests)."""
SOLD = TL.now.date()


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(TL.now) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def users(module_seeded_url: str) -> dict[str, CreatedUser]:
    url = module_seeded_url
    made = {
        role: create_user(url, roles=(role,))
        for role in (
            OWNER,
            ADMINISTRATOR,
            FINANCE_STAFF,
            ACADEMIC_COORDINATOR,
            TUTOR,
            STUDENT,
            GUARDIAN,
        )
    }
    made["delegated"] = create_user(
        url, roles=(ADMINISTRATOR,), delegations=[FINANCE_REFUND_APPROVE]
    )
    return made


def cookie_of(client: TestClient, users: Any, name: str, *, reauth: bool = True) -> Any:
    cookie = sign_in(client, users[name].username)
    if reauth:
        reauthenticate(client, cookie)
    return cookie


def cohort(url: str, client: TestClient) -> ws.World:
    return ws.cohort_world(url, client, TL)


def paid_cohort(url: str, client: TestClient, users: Any) -> ws.World:
    world = cohort(url, client)
    [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
    cookie = cookie_of(client, users, OWNER)
    ws.pay(client, cookie, world.family, "3000.00", [(fee.id, "3000.00")], SOLD)
    return world


def package(url: str, *, paid_by: CreatedUser | None = None) -> uuid.UUID:
    w = pks.world(url)
    product = pks.product(url)
    sold = pks.sell(url, w.student, product, SOLD)
    if paid_by is not None:
        [purchase] = run_sql(
            url, "SELECT * FROM charges WHERE package_id = :p", {"p": sold}
        )
        cs.recorded_payment(
            url,
            paid_by.id,
            purchase.billing_account_id,
            "1000.00",
            [(purchase.id, "1000.00")],
        )
    return sold


def confirm(
    client: TestClient,
    cookie: Any,
    subject: dict[str, Any],
    route: str,
    key: str | None = "new",
) -> Any:
    found = ws.preview_of(client, cookie_of_owner(client), **subject)
    payload = ws.body(found, refund_on=SOLD if route == "refund" else None)
    send = ws.refund if route == "refund" else ws.credit
    return send(client, cookie, payload, key)


_OWNER_COOKIE: dict[str, Any] = {}


def cookie_of_owner(client: TestClient) -> Any:
    return _OWNER_COOKIE["cookie"]


@pytest.fixture(scope="module", autouse=True)
def _owner(client: TestClient, users: Any) -> None:
    _OWNER_COOKIE["cookie"] = cookie_of(client, users, OWNER)


# --- Packages ------------------------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, FINANCE_STAFF])
def test_billing_managers_settle_a_package_as_credit(
    module_seeded_url: str, client: TestClient, users: Any, role: str
) -> None:
    sold = package(module_seeded_url)
    cookie = cookie_of(client, users, role)
    response = confirm(client, cookie, {"package_id": sold}, "credit")
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("role", [OWNER, "delegated"])
def test_refund_approvers_refund_a_package(
    module_seeded_url: str, client: TestClient, users: Any, role: str
) -> None:
    sold = package(module_seeded_url, paid_by=users[OWNER])
    cookie = cookie_of(client, users, role)
    response = confirm(client, cookie, {"package_id": sold}, "refund")
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("role", [ADMINISTRATOR, FINANCE_STAFF])
def test_others_cant_refund_a_package(
    module_seeded_url: str, client: TestClient, users: Any, role: str
) -> None:
    sold = package(module_seeded_url, paid_by=users[OWNER])
    cookie = cookie_of(client, users, role)
    response = confirm(client, cookie, {"package_id": sold}, "refund")
    assert code_of(response) == (403, "permission_denied")


# --- Enrollments ---------------------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR])
def test_owners_and_administrators_settle_an_enrollment_as_credit(
    module_seeded_url: str, client: TestClient, users: Any, role: str
) -> None:
    world = cohort(module_seeded_url, client)
    cookie = cookie_of(client, users, role)
    response = confirm(client, cookie, world.subject_query, "credit")
    assert response.status_code == 201, response.text


def test_finance_staff_cant_withdraw_an_enrollment(
    module_seeded_url: str, client: TestClient, users: Any
) -> None:
    url = module_seeded_url
    world = cohort(url, client)
    cookie = cookie_of(client, users, FINANCE_STAFF)
    key = f"key-{uuid.uuid4().hex}"
    response = confirm(client, cookie, world.subject_query, "credit", key=key)
    assert code_of(response) == (403, "permission_denied")
    assert ws.withdrawal_rows(url, cohort_enrollment_id=world.enrollment_id) == []
    assert ws.key_rows(url, key) == 0
    [period] = run_sql(
        url,
        "SELECT withdrawn_at FROM cohort_enrollments WHERE id = :id",
        {"id": world.enrollment_id},
    )
    assert period.withdrawn_at is None


@pytest.mark.parametrize("role", [OWNER, "delegated"])
def test_refund_approvers_refund_an_enrollment(
    module_seeded_url: str, client: TestClient, users: Any, role: str
) -> None:
    world = paid_cohort(module_seeded_url, client, users)
    cookie = cookie_of(client, users, role)
    response = confirm(client, cookie, world.subject_query, "refund")
    assert response.status_code == 201, response.text
    assert response.json()["withdrawal"]["refund_amount"] == "3000.00"


def test_an_undelegated_administrator_cant_refund(
    module_seeded_url: str, client: TestClient, users: Any
) -> None:
    world = paid_cohort(module_seeded_url, client, users)
    cookie = cookie_of(client, users, ADMINISTRATOR)
    response = confirm(client, cookie, world.subject_query, "refund")
    assert code_of(response) == (403, "permission_denied")


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, TUTOR, STUDENT, GUARDIAN])
def test_everyone_else_gets_403(client: TestClient, users: Any, role: str) -> None:
    cookie = cookie_of(client, users, role)
    subject = {"package": {"package_id": str(uuid.uuid4())}}
    payload = {
        "subject": subject["package"],
        "reason": ws.REASON,
        "fingerprint": "a" * 64,
    }
    for response in (
        ws.preview(client, cookie, package_id=uuid.uuid4()),
        ws.credit(client, cookie, payload),
        ws.refund(client, cookie, payload),
        get(client, cookie, ws.WITHDRAWALS),
        get(client, cookie, f"{ws.WITHDRAWALS}/{uuid.uuid4()}"),
    ):
        assert code_of(response) == (403, "permission_denied"), response.url


def test_anonymous_is_401_everywhere(client: TestClient) -> None:
    client.cookies.clear()
    for response in (
        client.get(ws.PREVIEW, params={"package_id": str(uuid.uuid4())}),
        client.post(ws.CREDIT, json={}),
        client.post(ws.REFUND, json={}),
        client.get(ws.WITHDRAWALS),
        client.get(f"{ws.WITHDRAWALS}/{uuid.uuid4()}"),
    ):
        assert response.status_code == 401, response.url


@pytest.mark.parametrize(("route", "role"), [("credit", OWNER), ("refund", OWNER)])
def test_each_confirm_needs_a_recent_reauthentication(
    module_seeded_url: str, client: TestClient, users: Any, route: str, role: str
) -> None:
    url = module_seeded_url
    sold = package(url, paid_by=users[OWNER])
    cookie = cookie_of(client, users, role, reauth=False)
    key = f"key-{uuid.uuid4().hex}"
    response = confirm(client, cookie, {"package_id": sold}, route, key=key)
    assert code_of(response) == (403, "reauth_required")
    assert ws.key_rows(url, key) == 0
    reauthenticate(client, cookie)
    retried = confirm(client, cookie, {"package_id": sold}, route, key=key)
    assert retried.status_code == 201, retried.text


# --- Reads (D3) ----------------------------------------------------------------------


def test_staff_read_withdrawals_with_bounded_statements(
    module_seeded_url: str, client: TestClient, users: Any
) -> None:
    url = module_seeded_url
    sold = package(url)
    created = confirm(client, cookie_of_owner(client), {"package_id": sold}, "credit")
    withdrawal = created.json()["withdrawal"]
    cookie = cookie_of(client, users, FINANCE_STAFF)
    page = get(client, cookie, ws.WITHDRAWALS, package_id=str(sold)).json()
    assert [item["id"] for item in page["items"]] == [withdrawal["id"]]
    assert page["total"] == 1
    detail = get(client, cookie, f"{ws.WITHDRAWALS}/{withdrawal['id']}").json()
    assert detail["adjustment_ids"] == withdrawal["adjustment_ids"]
    assert detail["preview"]["outcome"] == "account_credit"
    assert detail["preview"]["fingerprint"]
    family_page = get(
        client, cookie, ws.WITHDRAWALS, family_id=withdrawal["family_id"]
    ).json()
    assert [item["id"] for item in family_page["items"]] == [withdrawal["id"]]
    missing = get(client, cookie, f"{ws.WITHDRAWALS}/{uuid.uuid4()}")
    assert code_of(missing) == (404, "not_found")

    count, _ = count_statements(
        url,
        lambda s: withdrawals.get_withdrawal(s, uuid.UUID(withdrawal["id"])),
    )
    assert count <= 4
    count, listed = count_statements(
        url,
        lambda s: withdrawals.list_withdrawals(
            s,
            family_id=None,
            student_id=None,
            group_enrollment_id=None,
            cohort_enrollment_id=None,
            package_id=None,
            limit=50,
            offset=0,
        ),
    )
    assert count <= 4
    assert listed.total >= 1
