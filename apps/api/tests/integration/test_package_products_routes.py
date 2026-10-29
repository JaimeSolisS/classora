"""The package product routes (#94 B, L7; G1) through the real app: the
shapes, the B2 bounds, the fixed fields, deactivation, the versions through
the #89 rules (a version a package used is ``price_version_in_use``, and
the 0066 predicate refuses changing it), and the #89 catalog routes still
hiding ``package`` items."""

import uuid
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing import service as billing
from app.modules.billing.models import PackageProduct
from app.modules.identity.permissions import FINANCE_STAFF
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.scheduling import persist_subject
from tests.integration import package_support as ps
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ps.ZONE)
TODAY = date(2031, 3, 10)
INVALID = (422, "validation_error")
ITEM_KEYS = {
    "id",
    "name",
    "unit",
    "quantity",
    "validity_days",
    "subject",
    "is_active",
    "current_amount",
    "version",
}


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


@pytest.fixture(scope="module")
def subject(module_seeded_url: str) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        return (await persist_subject(session)).id

    return in_uow(module_seeded_url, _make)


def body(**values: Any) -> dict[str, Any]:
    return {
        "name": "10 clases",
        "unit": "sessions",
        "quantity": 10,
        "validity_days": 90,
        "subject_id": None,
        "amount": "2500.00",
        "effective_from": "2031-03-01",
        **values,
    }


def created(client: TestClient, cookie: SetCookie, **values: Any) -> dict[str, Any]:
    response = post(client, cookie, ps.PRODUCTS, body(**values))
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def field_codes(response: Response) -> set[tuple[str, str]]:
    problem = response.json()
    assert problem["code"] == "validation_error", problem
    return {(str(e["path"][-1]), e["code"]) for e in problem["errors"]}


def test_create_a_product(client, staff, module_seeded_url, subject):
    response = post(
        client, staff, ps.PRODUCTS, body(subject_id=str(subject), name="  Paquete A  ")
    )

    assert response.status_code == 201, response.text
    detail = response.json()
    assert set(detail) == ITEM_KEYS | {"versions"}
    assert detail["name"] == "Paquete A"
    assert (detail["unit"], detail["quantity"], detail["validity_days"]) == (
        "sessions",
        10,
        90,
    )
    assert detail["subject"]["id"] == str(subject)
    assert (detail["is_active"], detail["version"]) == (True, 1)
    assert detail["current_amount"] == "2500.00"
    [version] = detail["versions"]
    assert (version["effective_from"], version["amount"], version["status"]) == (
        "2031-03-01",
        "2500.00",
        "current",
    )
    [item] = run_sql(
        module_seeded_url,
        "SELECT sp.class_type, sp.pricing_model, sp.subject_id"
        " FROM package_products p JOIN service_prices sp"
        " ON sp.id = p.service_price_id WHERE p.id = :id",
        {"id": detail["id"]},
    )
    assert (item.class_type, item.pricing_model, item.subject_id) == (
        "individual",
        "package",
        subject,
    )
    assert len(events(module_seeded_url, record_type="package_products")) >= 1


def test_an_any_subject_minutes_product(client, staff):
    detail = created(client, staff, unit="minutes", quantity=600, validity_days=30)

    assert detail["subject"] is None
    assert (detail["unit"], detail["quantity"]) == ("minutes", 600)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"quantity": 0}, ("quantity", "quantity_out_of_range")),
        ({"quantity": 501}, ("quantity", "quantity_out_of_range")),
        ({"unit": "minutes", "quantity": 0}, ("quantity", "quantity_out_of_range")),
        (
            {"unit": "minutes", "quantity": 30015},
            ("quantity", "quantity_out_of_range"),
        ),
        ({"unit": "minutes", "quantity": 20}, ("quantity", "quantity_step")),
        ({"validity_days": 0}, ("validity_days", "validity_days_out_of_range")),
        ({"validity_days": 731}, ("validity_days", "validity_days_out_of_range")),
        ({"amount": "-1.00"}, ("amount", "amount_negative")),
        ({"amount": "1.001"}, ("amount", "money_precision")),
        ({"amount": "1000000000000.00"}, ("amount", "money_out_of_range")),
        ({"amount": None}, ("amount", "money_invalid")),
        ({"effective_from": "1999-12-31"}, ("effective_from", "date_out_of_range")),
        ({"name": "   "}, ("name", "string_too_short")),
        ({"name": "x" * 121}, ("name", "string_too_long")),
        ({"unit": "hours"}, ("unit", "literal_error")),
    ],
)
def test_the_body_bounds(client, staff, values, expected):
    response = post(client, staff, ps.PRODUCTS, body(**values))

    assert response.status_code == 422, response.text
    field, code = expected
    codes = field_codes(response)
    assert any(f == field and (c == code or code in c) for f, c in codes), codes


@pytest.mark.parametrize(
    ("unit", "quantity"),
    [("sessions", 1), ("sessions", 500), ("minutes", 15), ("minutes", 30000)],
)
def test_the_quantity_limits_are_accepted(client, staff, unit, quantity):
    assert created(client, staff, unit=unit, quantity=quantity)["quantity"] == quantity


def test_unknown_and_inactive_subjects(client, staff, module_seeded_url):
    unknown = post(client, staff, ps.PRODUCTS, body(subject_id=str(uuid.uuid4())))
    assert field_codes(unknown) == {("subject_id", "subject_unknown")}

    async def _inactive(session: AsyncSession) -> uuid.UUID:
        subject = await persist_subject(session)
        subject.is_active = False
        await session.flush()
        return subject.id

    inactive = in_uow(module_seeded_url, _inactive)
    response = post(client, staff, ps.PRODUCTS, body(subject_id=str(inactive)))
    assert field_codes(response) == {("subject_id", "subject_inactive")}


def test_list_filters_and_order(client, staff, module_seeded_url, subject):
    prefix = uuid.uuid4().hex[:6]
    names = [f"Lista {prefix} {c}" for c in "ba"]
    second = created(client, staff, name=names[0], subject_id=str(subject))
    first = created(client, staff, name=names[1], subject_id=str(subject))
    patch(
        client,
        staff,
        f"{ps.PRODUCTS}/{second['id']}",
        {"version": 1, "is_active": False},
    )

    everything = get(client, staff, ps.PRODUCTS, subject_id=str(subject), limit=100)
    assert everything.status_code == 200, everything.text
    listed = [i["id"] for i in everything.json()["items"]]
    assert listed.index(first["id"]) < listed.index(second["id"])
    assert set(everything.json()["items"][0]) == ITEM_KEYS
    active = get(
        client, staff, ps.PRODUCTS, subject_id=str(subject), is_active="true", limit=100
    ).json()["items"]
    assert first["id"] in [i["id"] for i in active]
    assert second["id"] not in [i["id"] for i in active]


def test_detail_and_404(client, staff):
    detail = created(client, staff)

    found = get(client, staff, f"{ps.PRODUCTS}/{detail['id']}")
    assert found.status_code == 200
    assert found.json() == detail
    missing = get(client, staff, f"{ps.PRODUCTS}/{uuid.uuid4()}")
    assert code_of(missing) == (404, "not_found")


def test_rename_and_deactivate(client, staff):
    detail = created(client, staff)
    path = f"{ps.PRODUCTS}/{detail['id']}"

    renamed = patch(client, staff, path, {"version": 1, "name": " Nuevo nombre "})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["name"], renamed.json()["version"]) == ("Nuevo nombre", 2)
    off = patch(client, staff, path, {"version": 2, "is_active": False})
    assert (off.json()["is_active"], off.json()["version"]) == (False, 3)
    same = patch(client, staff, path, {"version": 3, "is_active": False})
    assert same.json()["version"] == 3  # nothing changed, no bump
    stale = patch(client, staff, path, {"version": 1, "name": "Otro"})
    assert code_of(stale) == (409, "version_conflict")
    missing = patch(client, staff, f"{ps.PRODUCTS}/{uuid.uuid4()}", {"version": 1})
    assert code_of(missing) == (404, "not_found")


@pytest.mark.parametrize(
    "field",
    [
        {"unit": "minutes"},
        {"quantity": 5},
        {"validity_days": 10},
        {"subject_id": None},
    ],
)
def test_the_fixed_fields_are_refused(client, staff, field):
    detail = created(client, staff)

    response = patch(
        client, staff, f"{ps.PRODUCTS}/{detail['id']}", {"version": 1, **field}
    )

    assert code_of(response) == INVALID
    [key] = field
    assert field_codes(response) == {(key, "extra_forbidden")}


def test_versions_follow_the_catalog_rules(client, staff):
    detail = created(client, staff, effective_from="2031-03-01")
    path = f"{ps.PRODUCTS}/{detail['id']}/versions"

    past = post(client, staff, path, {"effective_from": "2031-03-05", "amount": "1"})
    assert code_of(past) == (422, "price_effective_from_invalid")
    added = post(client, staff, path, {"effective_from": "2031-04-01", "amount": None})
    assert added.status_code == 201, added.text
    versions = added.json()["versions"]
    assert [(v["effective_from"], v["status"]) for v in versions] == [
        ("2031-04-01", "upcoming"),
        ("2031-03-01", "current"),
    ]
    current_id = versions[1]["id"]
    in_effect = delete(client, staff, f"{path}/{current_id}")
    assert code_of(in_effect) == (409, "price_version_in_effect")
    removed = delete(client, staff, f"{path}/{versions[0]['id']}")
    assert removed.status_code == 204
    assert code_of(delete(client, staff, f"{path}/{uuid.uuid4()}")) == (
        404,
        "not_found",
    )


def test_a_version_a_package_used_is_in_use(client, staff, module_seeded_url):
    url = module_seeded_url
    detail = created(client, staff, effective_from="2031-03-01")
    path = f"{ps.PRODUCTS}/{detail['id']}/versions"
    upcoming = post(
        client, staff, path, {"effective_from": "2031-05-01", "amount": "9"}
    )
    version_id = upcoming.json()["versions"][0]["id"]
    w = ps.world(url)

    async def _package(session: AsyncSession) -> None:
        product = await session.get(PackageProduct, uuid.UUID(detail["id"]))
        assert product is not None
        account = await billing.ensure_billing_account(session, w.family)
        await billing.insert_package(
            session,
            billing_account_id=account,
            student_id=w.student,
            product=product,
            price_version_id=uuid.UUID(version_id),
            purchased_on=TODAY,
        )

    in_uow(url, _package)

    response = delete(client, staff, f"{path}/{version_id}")

    assert code_of(response) == (409, "price_version_in_use")
    with pytest.raises(DBAPIError) as refused:
        run_sql(
            url,
            "UPDATE price_versions SET amount = 1 WHERE id = :v",
            {"v": version_id},
        )
    assert getattr(refused.value.orig, "sqlstate", None) == "42501"


def test_the_catalog_routes_still_hide_package_items(client, staff, module_seeded_url):
    detail = created(client, staff)
    item = ps.service_price_of(module_seeded_url, uuid.UUID(detail["id"]))

    listed = get(client, staff, "/api/v1/billing/service-prices", limit=100)
    assert str(item) not in [i["id"] for i in listed.json()["items"]]
    assert code_of(get(client, staff, f"/api/v1/billing/service-prices/{item}")) == (
        404,
        "not_found",
    )
    added = post(
        client,
        staff,
        f"/api/v1/billing/service-prices/{item}/versions",
        {"effective_from": "2031-06-01", "amount": "1.00"},
    )
    assert code_of(added) == (404, "not_found")


def test_a_catalog_item_is_not_a_product_version(client, staff, module_seeded_url):
    detail = created(client, staff)
    other = created(client, staff)
    other_version = other["versions"][0]["id"]

    response = delete(
        client, staff, f"{ps.PRODUCTS}/{detail['id']}/versions/{other_version}"
    )

    assert code_of(response) == (404, "not_found")
