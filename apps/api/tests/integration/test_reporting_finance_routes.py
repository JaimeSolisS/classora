"""Financial report routes: access and the range rules (#117 B1-B3). Every
role is also in ``tests/authz/matrix.toml``; the figures are
``test_reporting_finance_reconciliation.py`` and
``tests/unit/test_reporting_recognition.py``."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
)
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import auth_client, signed_headers
from tests.integration.corrections_support import Staff, staff

PROFITABILITY = "/api/v1/reports/finance/profitability"
BALANCES = "/api/v1/reports/finance/balances"
NOW = datetime(2026, 12, 10, 18, 0, tzinfo=UTC)
"""Noon of 2026-12-10 in Mexico City: the center's today."""


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Iterator[None]:
    frozen.set(NOW)
    yield
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance_staff(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


def call(client: TestClient, who: Staff | None, path: str, **params: str) -> Any:
    client.cookies.clear()
    headers = {} if who is None else signed_headers(who.cookie)
    return client.get(path, params=params, headers=headers)


def report(
    client: TestClient, who: Staff, start: str, end: str, basis: str = "accrual"
):
    return call(client, who, PROFITABILITY, basis=basis, **{"from": start}, to=end)


def problem(response: Any) -> tuple[int, str, list[tuple[Any, ...]]]:
    body = response.json()
    return (
        response.status_code,
        body["code"],
        [(tuple(e["path"]), e["code"]) for e in body.get("errors") or []],
    )


def test_b1_owner_and_finance_read_both_routes(client, owner, finance_staff):
    for who in (owner, finance_staff):
        assert report(client, who, "2026-11-01", "2026-11-30").status_code == 200
        assert (
            report(client, who, "2026-11-01", "2026-11-30", "cash").status_code == 200
        )
        assert call(client, who, BALANCES).status_code == 200


def test_b1_administrator_with_payroll_finalize_and_coordinator_are_denied(
    module_seeded_url, client
):
    admin = staff(
        module_seeded_url, client, (ADMINISTRATOR,), delegations=["payroll.finalize"]
    )
    coordinator = staff(module_seeded_url, client, (ACADEMIC_COORDINATOR,))
    for who in (admin, coordinator):
        for response in (
            report(client, who, "2026-11-01", "2026-11-30"),
            call(client, who, BALANCES),
        ):
            assert response.status_code == 403
            assert response.json()["code"] == "permission_denied"
    assert call(client, None, BALANCES).status_code == 401


def test_b2_parameters_are_required_and_typed(client, owner):
    missing = call(
        client, owner, PROFITABILITY, **{"from": "2026-11-01"}, to="2026-11-30"
    )
    assert problem(missing)[:2] == (422, "validation_error")
    bad_basis = report(client, owner, "2026-11-01", "2026-11-30", "forecast")
    assert problem(bad_basis)[:2] == (422, "validation_error")
    bad_date = report(client, owner, "2026-11-31", "2026-11-30")
    assert problem(bad_date)[:2] == (422, "validation_error")
    no_dates = call(client, owner, PROFITABILITY, basis="cash")
    assert problem(no_dates)[:2] == (422, "validation_error")


INVALID = "report_range_invalid"


def test_b2_from_after_to(client, owner):
    assert report(client, owner, "2026-11-30", "2026-11-30").status_code == 200
    assert problem(report(client, owner, "2026-12-01", "2026-11-30")) == (
        422,
        INVALID,
        [(("query", "to"), INVALID)],
    )


def test_b2_to_after_today(client, owner):
    assert report(client, owner, "2026-12-01", "2026-12-10").status_code == 200
    assert problem(report(client, owner, "2026-12-01", "2026-12-11")) == (
        422,
        INVALID,
        [(("query", "to"), INVALID)],
    )


def test_b2_span_of_366_days(client, owner):
    # 2025-12-10 → 2026-12-10 is 365 days apart (366 dates): allowed.
    assert report(client, owner, "2025-12-10", "2026-12-10").status_code == 200
    assert problem(report(client, owner, "2025-12-09", "2026-12-10")) == (
        422,
        INVALID,
        [(("query", "from"), INVALID)],
    )


def test_b2_today_is_the_centers_not_utc(client, owner, frozen):
    # 05:30 UTC on Dec 1 is still Nov 30 in Mexico City.
    frozen.set(datetime(2026, 12, 1, 5, 30, tzinfo=UTC))
    assert report(client, owner, "2026-11-01", "2026-11-30").status_code == 200
    late = report(client, owner, "2026-11-01", "2026-12-01")
    assert problem(late) == (422, INVALID, [(("query", "to"), INVALID)])
    assert call(client, owner, BALANCES).json()["today"] == "2026-11-30"


FORBIDDEN_KEYS = {
    "description",
    "receipt_reference",
    "note",
    "reason",
    "reference",
    "template",
    "tutor",
    "student",
    "family",
    "payer_name",
}


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value)) if value else set()
    return set()


def test_b3_no_text_or_person_fields(client, owner):
    for response in (
        report(client, owner, "2026-11-01", "2026-11-30"),
        report(client, owner, "2026-11-01", "2026-11-30", "cash"),
        call(client, owner, BALANCES),
    ):
        assert response.status_code == 200
        assert not _keys(response.json()) & FORBIDDEN_KEYS
    body = report(client, owner, "2026-11-01", "2026-11-30").json()
    assert set(body) == {
        "basis",
        "from",
        "to",
        "today",
        "class_rows",
        "revenue_total",
        "direct_tutor_cost_total",
        "gross_profit",
        "operating_expenses",
        "net_result",
    }
    assert [r["class_type"] for r in body["class_rows"]] == [
        "individual",
        "group",
        "course",
        "unallocated",
    ]
    balances = call(client, owner, BALANCES).json()
    assert set(balances) == {"today", "receivables", "payables", "deferred_revenue"}
