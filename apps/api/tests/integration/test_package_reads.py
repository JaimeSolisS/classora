"""The staff package reads (#94 D5, L9; G9) through the real app: the list
filters (student, family, status), the order, the pagination, the statement
bound, the detail with its unit movements, and ``404``."""

import uuid
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import OffsetParams
from app.modules.identity.permissions import FINANCE_STAFF
from app.workflows import packages
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import package_support as ps
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of

NOW = datetime(2031, 3, 20, 12, 0, tzinfo=ps.ZONE)
ITEM_KEYS = {
    "id",
    "student",
    "family",
    "product",
    "unit",
    "units_granted",
    "remaining_units",
    "subject",
    "purchased_on",
    "expires_on",
    "status",
    "purchase_charge_id",
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


def listed(client: TestClient, cookie: SetCookie, **params: Any) -> list[str]:
    response = get(client, cookie, ps.PACKAGES, limit=100, **params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_the_list_filters_order_and_statuses(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    brother = ps.sibling(url, w)
    later = ps.sell(url, w.student, ps.product(url, validity_days=60), date(2031, 3, 1))
    sooner = ps.sell(
        url, w.student, ps.product(url, validity_days=30), date(2031, 3, 5)
    )
    expired = ps.sell(
        url, w.student, ps.product(url, validity_days=10), date(2031, 3, 1)
    )
    empty = ps.sell(
        url, brother, ps.product(url, quantity=1, validity_days=20), date(2031, 3, 1)
    )
    ps.hook(url, ps.session_for(url, w, date(2031, 3, 12), student=brother))
    mine = [str(p) for p in (expired, sooner, later)]

    assert listed(client, staff, student_id=str(w.student)) == mine
    assert listed(client, staff, family_id=str(w.family)) == [
        str(expired),
        str(empty),
        str(sooner),
        str(later),
    ]
    assert listed(client, staff, family_id=str(w.family), status="active") == [
        str(sooner),
        str(later),
    ]
    assert listed(client, staff, family_id=str(w.family), status="expired") == [
        str(expired)
    ]
    assert listed(client, staff, family_id=str(w.family), status="exhausted") == [
        str(empty)
    ]
    both = get(
        client,
        staff,
        ps.PACKAGES,
        family_id=str(w.family),
        status=["exhausted", "expired"],
    )
    assert [i["id"] for i in both.json()["items"]] == [str(expired), str(empty)]
    page = get(
        client, staff, ps.PACKAGES, family_id=str(w.family), limit=2, offset=1
    ).json()
    assert (page["total"], [i["id"] for i in page["items"]]) == (
        4,
        [str(empty), str(sooner)],
    )
    item = page["items"][0]
    assert set(item) == ITEM_KEYS
    assert (item["remaining_units"], item["units_granted"]) == (0, 1)
    assert item["student"]["id"] == str(brother)
    assert item["family"]["id"] == str(w.family)
    assert item["subject"] is None
    assert item["purchase_charge_id"] is not None
    beyond = get(client, staff, ps.PACKAGES, family_id=str(w.family), offset=10)
    assert beyond.json()["total"] == 4
    assert code_of(get(client, staff, ps.PACKAGES, status="unknown")) == (
        422,
        "validation_error",
    )


def test_the_detail_lists_the_movements(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    package = ps.sell(
        url, w.student, ps.product(url, validity_days=10), date(2031, 3, 1)
    )
    session = ps.session_for(url, w, date(2031, 3, 5))
    ps.hook(url, session)
    ps.expire(url, date(2031, 3, 12))

    response = get(client, staff, f"{ps.PACKAGES}/{package}")

    assert response.status_code == 200, response.text
    detail = response.json()
    assert set(detail) == ITEM_KEYS | {"transactions"}
    assert detail["status"] == "expired"
    assert [(t["kind"], t["units"]) for t in detail["transactions"]] == [
        ("grant", 10),
        ("consume", -1),
        ("expire", -9),
    ]
    consume = detail["transactions"][1]
    assert consume["class_session_id"] == str(session)
    assert consume["session_starts_at"].startswith("2031-03-05T16:00:00")
    assert detail["transactions"][0]["session_starts_at"] is None
    assert code_of(get(client, staff, f"{ps.PACKAGES}/{uuid.uuid4()}")) == (
        404,
        "not_found",
    )


def test_the_list_is_bounded_in_statements(module_seeded_url):
    url = module_seeded_url
    w = ps.world(url)
    for _ in range(6):
        ps.sell(url, w.student, ps.product(url), date(2031, 3, 1))

    with frozen_clock(NOW):
        count, page = count_statements(
            url,
            lambda s: packages.list_packages(
                s,
                student_id=w.student,
                family_id=None,
                statuses=("active",),
                page=OffsetParams(limit=50, offset=0),
            ),
        )

    assert page.total == 6
    assert count <= 2, count  # the zone (center today) and the page
