"""The package purchase (#94 C, L5, L8; G2, issue AC 1) through the real
app: one package, one ``package_purchase`` charge at today's price (due
date with the family's offset, #95 discounts with their snapshot), one
``grant``, the audit events; each C2 refusal; billing off allowed; and the
#88 idempotency pattern (replay, a reused key, a missing key, a concurrent
duplicate)."""

import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import IDEMPOTENCY_HEADER, REPLAYED_HEADER
from app.core.money import Money
from app.modules.billing import service as billing_service
from app.modules.identity.permissions import FINANCE_STAFF
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.billing import persist_discount
from tests.factories.families import persist_student
from tests.integration import package_support as ps
from tests.integration import session_charge_support as scs
from tests.integration.auth_support import (
    SetCookie,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ps.ZONE)
TODAY = date(2031, 3, 10)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(client: TestClient, module_seeded_url: str) -> SetCookie:
    return sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )


@pytest.fixture(autouse=True)
def _billing_off(module_seeded_url: str) -> None:
    ps.set_floor(module_seeded_url, None)


def new_key() -> str:
    return f"key-{uuid.uuid4().hex}"


def buy(
    client: TestClient,
    cookie: SetCookie,
    student: Any,
    product: Any,
    key: str | None = "new",
    **extra: Any,
) -> Response:
    headers = signed_headers(cookie)
    if key is not None:
        headers[IDEMPOTENCY_HEADER] = new_key() if key == "new" else key
    client.cookies.clear()
    return client.post(
        ps.PACKAGES,
        json={
            "student_id": str(student),
            "package_product_id": str(product),
            **extra,
        },
        headers=headers,
    )


def packages_of(url: str, student: Any) -> list[Any]:
    return run_sql(url, "SELECT * FROM packages WHERE student_id = :s", {"s": student})


def purchase_charges(url: str, student: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM charges WHERE kind = 'package_purchase' AND student_id = :s",
        {"s": student},
    )


def test_a_purchase_posts_the_package_the_charge_and_the_grant(
    client, staff, module_seeded_url
):
    url = module_seeded_url
    w = ps.world(url)
    product = ps.product(url, quantity=8, validity_days=60, subject=w.subject)
    ps.scs.add_version(
        url, ps.service_price_of(url, product), date(2031, 3, 5), "1200.00"
    )

    response = buy(client, staff, w.student, product)

    assert response.status_code == 201, response.text
    detail = response.json()
    assert (detail["purchased_on"], detail["expires_on"]) == (
        "2031-03-10",
        "2031-05-09",
    )
    assert (detail["units_granted"], detail["remaining_units"]) == (8, 8)
    assert (detail["unit"], detail["status"]) == ("sessions", "active")
    assert detail["student"]["id"] == str(w.student)
    assert detail["family"]["id"] == str(w.family)
    assert detail["product"]["id"] == str(product)
    assert detail["subject"]["id"] == str(w.subject)
    [package] = packages_of(url, w.student)
    assert str(package.id) == detail["id"]
    [charge] = purchase_charges(url, w.student)
    assert str(charge.id) == detail["purchase_charge_id"]
    assert charge.package_id == package.id
    assert Money(charge.amount) == Money("1200.00")  # the version in effect today
    assert charge.price_version_id == package.price_version_id
    assert (charge.service_date, charge.due_date) == (TODAY, TODAY)
    assert charge.education_level_id == w.level
    [grant] = [t for t in detail["transactions"]]
    assert (grant["kind"], grant["units"], grant["class_session_id"]) == (
        "grant",
        8,
        None,
    )
    created = events(url, record_type="packages", record_id=package.id)
    assert [e.action for e in created] == ["create"]
    assert created[0].actor_user_id is not None


def test_the_due_date_adds_the_familys_offset(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    scs.set_offset(url, w.family, 7)

    response = buy(client, staff, w.student, ps.product(url))

    [charge] = purchase_charges(url, w.student)
    assert response.status_code == 201
    assert charge.due_date == TODAY + timedelta(days=7)


def test_a_discount_applies_with_its_snapshot(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)

    async def _discount(session: AsyncSession) -> uuid.UUID:
        from tests.factories.billing import assign_discount

        discount = await persist_discount(
            session,
            audience="assigned",
            percent="10",
            eligibility={"charge_kinds": ["package_purchase"]},
        )
        await assign_discount(session, discount.id, student_id=w.student)
        return discount.id

    discount = in_uow(url, _discount)

    response = buy(client, staff, w.student, ps.product(url, amount="1000.00"))

    assert response.status_code == 201, response.text
    [charge] = purchase_charges(url, w.student)
    assert (Money(charge.list_amount), Money(charge.discount_amount)) == (
        Money("1000.00"),
        Money("100.00"),
    )
    [line] = run_sql(
        url, "SELECT * FROM charge_discounts WHERE charge_id = :c", {"c": charge.id}
    )
    assert (line.discount_id, Money(line.amount)) == (discount, Money("100.00"))


def test_a_zero_price_posts_a_zero_charge_and_grants(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)

    response = buy(client, staff, w.student, ps.product(url, amount="0.00"))

    assert response.status_code == 201, response.text
    [charge] = purchase_charges(url, w.student)
    assert Money(charge.amount) == Money.zero()
    assert response.json()["remaining_units"] == 10


def test_billing_off_or_a_later_floor_doesnt_block_a_purchase(
    client, staff, module_seeded_url
):
    url = module_seeded_url
    w = ps.world(url)
    ps.set_floor(url, date(2032, 1, 1))

    response = buy(client, staff, w.student, ps.product(url))

    assert response.status_code == 201, response.text


def test_a_student_without_a_family_is_refused(client, staff, module_seeded_url):
    url = module_seeded_url

    async def _orphan(session: AsyncSession) -> uuid.UUID:
        return (await persist_student(session, contacts=0)).id

    orphan = in_uow(url, _orphan)

    response = buy(client, staff, orphan, ps.product(url))

    assert code_of(response) == (409, "student_has_no_family")
    assert packages_of(url, orphan) == []


def test_an_inactive_product_is_refused(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    product = ps.product(url)
    run_sql(
        url,
        "UPDATE package_products SET is_active = false WHERE id = :p",
        {"p": product},
    )

    assert code_of(buy(client, staff, w.student, product)) == (
        409,
        "package_product_inactive",
    )


@pytest.mark.parametrize("case", ["no_version_yet", "not_offered"])
def test_a_product_without_a_price_today_is_not_offered(
    client, staff, module_seeded_url, case
):
    url = module_seeded_url
    w = ps.world(url)
    if case == "no_version_yet":
        product = ps.product(url, price_from=date(2031, 4, 1))
    else:
        product = ps.product(url)
        ps.scs.add_version(url, ps.service_price_of(url, product), TODAY, None)

    response = buy(client, staff, w.student, product)

    assert code_of(response) == (409, "package_not_offered")
    assert packages_of(url, w.student) == []
    assert purchase_charges(url, w.student) == []


def test_unknown_product_and_student_are_422(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)

    product = buy(client, staff, w.student, uuid.uuid4())
    student = buy(client, staff, uuid.uuid4(), ps.product(url))

    assert code_of(product) == (422, "validation_error")
    assert product.json()["errors"][0]["code"] == "package_product_unknown"
    assert product.json()["errors"][0]["path"] == ["body", "package_product_id"]
    assert student.json()["errors"][0]["code"] == "student_unknown"
    assert student.json()["errors"][0]["path"] == ["body", "student_id"]


def test_an_unknown_body_field_is_422(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)

    response = buy(client, staff, w.student, ps.product(url), purchased_on="2031-01-01")

    assert code_of(response) == (422, "validation_error")


def test_a_replay_returns_the_first_response(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    product = ps.product(url)
    key = new_key()

    first = buy(client, staff, w.student, product, key)
    second = buy(client, staff, w.student, product, key)

    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers[REPLAYED_HEADER] == "true"
    assert REPLAYED_HEADER not in first.headers
    assert len(packages_of(url, w.student)) == 1
    assert len(purchase_charges(url, w.student)) == 1


def test_a_reused_key_with_another_body_is_refused(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    key = new_key()
    assert buy(client, staff, w.student, ps.product(url), key).status_code == 201

    other = buy(client, staff, w.student, ps.product(url), key)

    assert code_of(other) == (422, "idempotency_key_reused")
    assert len(packages_of(url, w.student)) == 1


def test_a_missing_key_is_400(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)

    response = buy(client, staff, w.student, ps.product(url), key=None)

    assert code_of(response) == (400, "idempotency_key_required")
    assert packages_of(url, w.student) == []


def test_a_concurrent_duplicate_runs_once(
    client, staff, module_seeded_url, monkeypatch
):
    url = module_seeded_url
    w = ps.world(url)
    product = ps.product(url)
    key = new_key()
    entered, release = threading.Event(), threading.Event()
    grant = billing_service.grant_package

    async def held_grant(session: AsyncSession, package: Any) -> uuid.UUID:
        result = await grant(session, package)
        if not entered.is_set():
            entered.set()
            import anyio

            await anyio.to_thread.run_sync(release.wait, 10)
        return result

    monkeypatch.setattr(billing_service, "grant_package", held_grant)

    def second() -> Response:
        entered.wait(10)
        timer = threading.Timer(0.5, release.set)
        timer.start()
        return buy(client, staff, w.student, product, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(buy, client, staff, w.student, product, key)
        second_future = pool.submit(second)
        responses = [first_future.result(30), second_future.result(30)]

    assert sorted(r.status_code for r in responses) == [201, 201]
    assert sorted(r.headers.get(REPLAYED_HEADER, "") for r in responses) == [
        "",
        "true",
    ]
    assert responses[0].json() == responses[1].json()
    assert len(packages_of(url, w.student)) == 1
    assert len(purchase_charges(url, w.student)) == 1
