"""Overdue accounts across families (#97 E1, E2; I4; N7 (a)).

``GET /api/v1/billing/overdue-accounts``: the aging buckets at the
30/31, 60/61 and 90/91 boundaries, ``min_days_overdue``, partially paid
charges (their open part), account credit shown but not netted, the
effective-date effects, ``family_status``, the order, the totals and
pagination, ``last_reminded_at``, the bounded statements and access.

The list reads every account of the database, so the tests on exact
totals and order use a fresh database (``seeded_url``); the others look up
their own account in a module database.
"""

import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.pagination import OffsetParams
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    TUTOR,
)
from app.workflows import billing_overdue
from tests.conftest import frozen_clock
from tests.factories import billing as factories
from tests.integration import due_date_support as dd
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.billing_ledger_support import count_statements
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import in_uow

TODAY = dd.TODAY
NOON = dd.local(TODAY, 12)
PATH = "/api/v1/billing/overdue-accounts"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def listing(
    url: str,
    *,
    min_days: int = 1,
    status: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> Any:
    """The workflow's page with the clock at noon on :data:`TODAY`."""

    async def _read(session: Any) -> Any:
        return await billing_overdue.list_overdue_accounts(
            session,
            min_days_overdue=min_days,
            family_status=status,  # type: ignore[arg-type]
            page=OffsetParams(limit=limit, offset=offset),
        )

    with frozen_clock(NOON):
        return in_uow(url, _read)


def row_of(page: Any, account: uuid.UUID) -> Any:
    found = [item for item in page.items if item.account_id == account]
    return found[0] if found else None


def ago(n: int) -> date:
    return TODAY - dd.days(n)


# --- Buckets and rules ---------------------------------------------------------------


def test_buckets_at_their_boundaries(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    for n, amount in ((30, "1.00"), (31, "2.00"), (60, "4.00"), (61, "8.00")):
        dd.charge(url, w.student, amount, due=ago(n))
    for n, amount in ((90, "16.00"), (91, "32.00")):
        dd.charge(url, w.student, amount, due=ago(n))
    dd.charge(url, w.student, "64.00", due=TODAY)  # due today: not overdue

    row = row_of(listing(url), w.account)

    assert row.overdue_charges == 6
    assert str(row.overdue_total) == "63.00"
    assert (
        str(row.buckets.days_1_30),
        str(row.buckets.days_31_60),
        str(row.buckets.days_61_90),
        str(row.buckets.days_over_90),
    ) == ("1.00", "6.00", "24.00", "32.00")
    assert row.oldest_due_date == ago(91)
    assert row.days_overdue == 91
    assert str(row.outstanding) == "127.00"
    assert row.family.id == w.family


def test_min_days_overdue_counts_only_late_enough_charges(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    recent = dd.world(url)
    dd.charge(url, w.student, "10.00", due=ago(5))
    dd.charge(url, w.student, "20.00", due=ago(45))
    dd.charge(url, recent.student, "10.00", due=ago(5))

    page = listing(url, min_days=31)

    row = row_of(page, w.account)
    assert (str(row.overdue_total), row.overdue_charges, row.days_overdue) == (
        "20.00",
        1,
        45,
    )
    assert str(row.buckets.days_1_30) == "0.00"
    assert row_of(page, recent.account) is None
    assert row_of(listing(url), recent.account) is not None


def test_partial_payments_count_their_open_part_and_credit_is_not_netted(
    module_seeded_url,
):
    url = module_seeded_url
    w = dd.world(url)
    late = dd.charge(url, w.student, "100.00", due=ago(10))
    dd.pay(url, w, late, "30.00")
    dd.charge(url, w.student, "40.00", due=TODAY + dd.days(5))  # pending

    async def _credit(session: Any) -> None:
        await factories.payment(session, w.account, "50.00", recorded_by=w.actor)

    in_uow(url, _credit)

    row = row_of(listing(url), w.account)

    assert str(row.overdue_total) == "70.00"
    assert str(row.outstanding) == "110.00"
    assert str(row.account_credit) == "50.00"
    assert str(row.net_balance) == "60.00"


def test_settled_and_not_yet_due_accounts_are_absent(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    paid = dd.charge(url, w.student, due=ago(10))
    dd.pay(url, w, paid, "100.00")
    voided = dd.charge(url, w.student, due=ago(10))
    dd.credit(url, voided, "-100.00")
    dd.charge(url, w.student, due=TODAY)
    dd.charge(url, w.student, "0.00", due=ago(10))

    assert row_of(listing(url), w.account) is None


def test_the_effective_due_date_decides(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    later = dd.world(url)
    moved_out = dd.charge(url, w.student, due=ago(20))
    moved_in = dd.charge(url, later.student, due=TODAY + dd.days(20))

    dd.change_due(url, w, moved_out, TODAY + dd.days(1))
    dd.change_due(url, later, moved_in, ago(40))

    page = listing(url)
    assert row_of(page, w.account) is None
    row = row_of(page, later.account)
    assert (row.oldest_due_date, row.days_overdue) == (ago(40), 40)
    assert str(row.buckets.days_31_60) == "100.00"


def test_last_reminded_at_is_the_latest_reminder(module_seeded_url):
    url = module_seeded_url
    w = dd.world(url)
    charge = dd.charge(url, w.student, due=ago(3))
    assert row_of(listing(url), w.account).last_reminded_at is None
    run_sql(
        url,
        "INSERT INTO charge_reminders (charge_id, stage, due_date, sent_at)"
        " VALUES (:c, 'due_soon', :d, :at), (:c, 'overdue', :d, :later)",
        {
            "c": charge,
            "d": ago(3),
            "at": dd.local(ago(5)),
            "later": dd.local(ago(2)),
        },
    )

    assert row_of(listing(url), w.account).last_reminded_at == dd.local(ago(2))


# --- Order, filters, totals (a fresh database) ---------------------------------------


def test_order_status_filter_totals_and_pages(seeded_url):
    url = seeded_url
    small = dd.world(url, name="Alfa")
    large = dd.world(url, name="Beta")
    tie = dd.world(url, name="Gama")
    gone = dd.world(url, name="Delta", family_active=False)
    dd.charge(url, small.student, "100.00", due=ago(40))
    dd.charge(url, large.student, "200.00", due=ago(40))
    dd.charge(url, tie.student, "100.00", due=ago(40))
    dd.charge(url, gone.student, "50.00", due=ago(100))

    page = listing(url)

    assert [i.family.name for i in page.items] == ["Delta", "Beta", "Alfa", "Gama"]
    assert page.total == 4
    assert page.totals.accounts == 4
    assert str(page.totals.overdue_total) == "450.00"
    assert str(page.totals.buckets.days_31_60) == "400.00"
    assert str(page.totals.buckets.days_over_90) == "50.00"
    assert page.today == TODAY
    active = listing(url, status="active")
    assert [i.family.name for i in active.items] == ["Beta", "Alfa", "Gama"]
    assert str(active.totals.overdue_total) == "400.00"
    inactive = listing(url, status="inactive")
    assert [i.family.is_active for i in inactive.items] == [False]
    second = listing(url, limit=2, offset=2)
    assert [i.family.name for i in second.items] == ["Alfa", "Gama"]
    assert (second.total, second.totals.accounts) == (4, 4)
    beyond = listing(url, limit=2, offset=10)
    assert beyond.items == []
    assert (beyond.total, str(beyond.totals.overdue_total)) == (4, "450.00")
    empty = listing(url, min_days=3000)
    assert (empty.items, empty.total, str(empty.totals.overdue_total)) == (
        [],
        0,
        "0.00",
    )


def test_the_statements_are_bounded(seeded_url):
    url = seeded_url
    w = dd.world(url)
    dd.charge(url, w.student, due=ago(5))

    async def _read(session: Any) -> Any:
        return await billing_overdue.list_overdue_accounts(
            session,
            min_days_overdue=1,
            family_status="all",
            page=OffsetParams(limit=50, offset=0),
        )

    with frozen_clock(NOON):
        few, _ = count_statements(url, _read)
    for _ in range(5):
        other = dd.world(url, students=2)
        for student in other.students:
            dd.charge(url, student, due=ago(35))
            dd.charge(url, student, due=ago(95))
    with frozen_clock(NOON):
        many, page = count_statements(url, _read)

    assert many == few <= 3
    assert page.total == 6


# --- Access and the route ------------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, ADMINISTRATOR, FINANCE_STAFF])
def test_billing_view_roles_read_the_route(module_seeded_url, client, role):
    url = module_seeded_url
    user = create_user(url, roles=(role,))
    w = dd.world(url)
    dd.charge(url, w.student, "75.00", due=ago(12))

    with frozen_clock(NOON):
        response = get(
            client, sign_in(client, user.username), PATH, limit=100, min_days_overdue=12
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["today"] == TODAY.isoformat()
    [item] = [i for i in body["items"] if i["account_id"] == str(w.account)]
    assert item["overdue_total"] == "75.00"
    assert item["buckets"] == {
        "days_1_30": "75.00",
        "days_31_60": "0.00",
        "days_61_90": "0.00",
        "days_over_90": "0.00",
    }
    assert item["family"]["id"] == str(w.family)
    assert set(body["totals"]) == {"accounts", "overdue_total", "buckets"}


@pytest.mark.parametrize("role", [ACADEMIC_COORDINATOR, TUTOR, GUARDIAN])
def test_other_roles_get_403(module_seeded_url, client, role):
    user: CreatedUser = create_user(module_seeded_url, roles=(role,))

    response = get(client, sign_in(client, user.username), PATH)

    assert response.status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        {"min_days_overdue": 0},
        {"min_days_overdue": 3651},
        {"family_status": "archived"},
        {"limit": 0},
    ],
)
def test_bad_queries_are_422(module_seeded_url, client, query):
    user = create_user(module_seeded_url, roles=(OWNER,))

    response = get(client, sign_in(client, user.username), PATH, **query)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
