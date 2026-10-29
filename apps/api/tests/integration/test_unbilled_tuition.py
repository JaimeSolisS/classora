"""The unbilled tuition periods read (#93 G1, H9): both reasons, filters,
order, pagination, billing off, the excluded states, the statement bound,
and list ⇔ job consistency."""

from collections.abc import Iterator
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import OffsetParams
from app.modules.identity.permissions import FINANCE_STAFF
from app.workflows import tuition_charges
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import tuition_support as ts
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow

NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ts.MX)
TODAY = date(2031, 3, 10)
FLOOR = date(2031, 1, 1)


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
def _reset(module_seeded_url: str, frozen: FrozenClock) -> None:
    ts.reset(module_seeded_url)


def listed(url: str, **filters):
    return in_uow(
        url,
        lambda s: tuition_charges.list_unbilled_tuition_periods(
            s, page=filters.pop("page", OffsetParams(limit=50, offset=0)), **filters
        ),
    )


def test_both_reasons_and_the_excluded_states(client, staff, module_seeded_url):
    url = module_seeded_url
    unpriced = ts.world(url, amount=None)
    no_price_plan = ts.plan(url, unpriced, starts_on=date(2031, 2, 10))
    moved = ts.world(url)
    moved_plan = ts.plan(url, moved, starts_on=date(2031, 3, 1))
    ts.move_to_new_family(url, moved.student)
    posted = ts.world(url)
    ts.plan(url, posted, starts_on=date(2031, 3, 1))
    paused = ts.world(url, amount=None)
    paused_plan = ts.plan(url, paused, starts_on=date(2031, 3, 1))
    ts.pause(url, paused_plan, date(2031, 3, 1), None)
    ended = ts.world(url, amount=None)
    ts.plan(url, ended, starts_on=date(2030, 12, 1), ends_on=date(2030, 12, 31))
    before_floor = ts.world(url, amount=None)
    ts.plan(url, before_floor, starts_on=date(2030, 12, 15), ends_on=date(2031, 1, 14))
    future = ts.world(url, amount=None)
    ts.plan(url, future, starts_on=date(2031, 4, 1))
    assert listed(url).items == []  # billing off
    ts.set_floor(url, FLOOR)
    ts.run(url, ts.local(date(2031, 3, 1)))  # posts the 1 Mar period of `posted`

    page = listed(url)

    found = [(i.tuition_plan_id, i.period_start, i.reason) for i in page.items]
    assert found == [
        (no_price_plan, date(2031, 2, 10), "no_price"),
        (moved_plan, date(2031, 3, 1), "family_changed"),
        (no_price_plan, date(2031, 3, 10), "no_price"),
    ]
    item = page.items[1]
    assert item.family is not None and item.family.id != moved.family
    assert item.frequency == "monthly"
    assert item.subject.id == moved.subject and item.subject.name
    assert item.group is None and item.period_end == date(2031, 3, 31)
    assert page.total == 3

    # The job skips exactly these, with the same reasons.
    run = ts.run(url, NOW)
    assert (run.no_price, run.family_changed, run.posted) == (2, 1, 0)


def test_filters_order_pagination_and_the_route(client, staff, module_seeded_url):
    url = module_seeded_url
    ts.set_floor(url, FLOOR)
    worlds = [ts.world(url, amount=None) for _ in range(3)]
    for w in worlds:
        ts.plan(url, w, starts_on=date(2031, 3, 1))
    moved = ts.world(url)
    ts.plan(url, moved, starts_on=date(2031, 2, 20))
    ts.move_to_new_family(url, moved.student)

    assert [i.reason for i in listed(url, reasons=["family_changed"]).items] == [
        "family_changed"
    ]
    only = listed(url, student_id=worlds[1].student).items
    assert [i.student.id for i in only] == [worlds[1].student]
    first = listed(url, page=OffsetParams(limit=2, offset=0))
    rest = listed(url, page=OffsetParams(limit=2, offset=2))
    assert first.total == rest.total == 4
    ordered = [*first.items, *rest.items]
    assert ordered[0].period_start == date(2031, 2, 20)
    march = [i for i in ordered if i.period_start == date(2031, 3, 1)]
    names = [i.student.name.casefold() for i in march]
    assert names == sorted(names)

    response = get(client, staff, ts.UNBILLED, reason=["no_price"], limit=50)
    assert response.status_code == 200, response.text
    assert {row["reason"] for row in response.json()["items"]} == {"no_price"}
    assert response.json()["total"] == 3
    assert (
        code_of(get(client, staff, ts.UNBILLED, reason="no_billing_account"))[0] == 422
    )


def test_the_statement_bound(module_seeded_url):
    url = module_seeded_url
    ts.set_floor(url, FLOOR)
    for _ in range(12):
        ts.plan(url, ts.world(url, amount=None), starts_on=date(2031, 2, 1))
    for _ in range(3):
        w = ts.world(url)
        ts.plan(url, w, starts_on=date(2031, 2, 1))
        ts.move_to_new_family(url, w.student)

    count, page = count_statements(
        url,
        lambda s: tuition_charges.list_unbilled_tuition_periods(
            s, page=OffsetParams(limit=100, offset=0)
        ),
    )

    assert page.total == 30  # Feb and Mar of 15 plans
    assert count <= 12, count
