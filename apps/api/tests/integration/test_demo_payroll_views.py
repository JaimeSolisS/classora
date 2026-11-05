"""What each demo account sees of the M7-demo payroll data (#499 E8): payroll
settings, tutors, periods, lines, unresolved work, payments, a tutor's own
earnings and the payroll side of the #117 reports (E10), through the API after
a whole run on the #299 frozen day."""

from decimal import Decimal
from typing import Any

import pytest

from tests.factories import demo
from tests.factories.demo_payroll import (
    BONUS_ITEM,
    MANUAL_REASON,
    PP2_REFERENCE,
    PP3_REFERENCE,
    REVERSAL_REASON,
)
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.demo_support import FROZEN_NOW, demo_env, migrate
from tests.integration.test_demo_schedule import signed_in

F = "2026-10-12"
T = "2026-11-04"
WEEKS = ["2026-10-12", "2026-10-19", "2026-10-26"]
PERIODS = {
    # (tutor, starts_on): (status, total, paid_total, paid_state)
    ("demo.tutor", "2026-11-02"): ("open", "20.00", None, None),
    ("demo.tutor", "2026-10-26"): ("finalized", "970.00", "0.00", "unpaid"),
    ("demo.tutor", "2026-10-19"): ("finalized", "630.00", "200.00", "partially_paid"),
    ("demo.tutor", "2026-10-12"): ("finalized", "70.00", "70.00", "paid"),
    ("demo.tutor2", "2026-11-02"): ("open", "0.00", None, None),
    ("demo.tutor2", "2026-10-26"): ("finalized", "3600.00", "0.00", "unpaid"),
    ("demo.tutor2", "2026-10-19"): ("finalized", "3600.00", "0.00", "unpaid"),
    ("demo.tutor2", "2026-10-12"): ("finalized", "2800.00", "0.00", "unpaid"),
}
"""E8 at ``FROZEN_NOW``. The open weeks hold only what was posted into them
(``demo.tutor``'s hours correction) until their next recalculation (engineer
D2)."""
CLASS_ROWS = [
    ("individual", "1180.00"),
    ("group", "0.00"),
    ("course", "4000.00"),
    ("unallocated", "6490.00"),
]
"""E10: the accrual payroll cost of ``[F, T]`` by class row: N1, H3, H2, H1 and
L1's late approval; the cohort's five classes; the salaries (6,000.00), the
bonuses (450.00), the deduction (−80.00) and the manual (120.00). 11,670.00 in
all (the groomed 11,910.00 less #299's P4, engineer D1)."""
ACCRUAL_PAYROLL = "11670.00"
CASH_PAYROLL = "270.00"
"""PP1 70.00 + PP2 200.00 (PP3 reversed)."""
PAYROLL_UNPAID = "11400.00"
OTHERS = ("demo.admin", "demo.coordinator", "demo.student", "demo.guardian")


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _users(url: str) -> dict[str, str]:
    return {
        r.username: str(r.id)
        for r in run_sql(
            url, "SELECT id, username FROM users WHERE username LIKE 'demo.%'"
        )
    }


def _periods(client: Any, cookie: Any, names: dict[str, str]) -> dict[Any, Any]:
    body = _ok(get(client, cookie, "/api/v1/payroll/periods", limit=100))
    assert body["total"] == len(PERIODS)
    return {(names[p["tutor_user_id"]], p["starts_on"]): p for p in body["items"]}


def _summary(periods: dict[Any, Any]) -> dict[Any, Any]:
    return {
        key: (p["status"], p["total"], p["paid_total"], p["paid_state"])
        for key, p in periods.items()
    }


def test_what_each_account_sees(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    users = _users(migrated_url)
    names = {user_id: username for username, user_id in users.items()}
    tutor, tutor2 = users["demo.tutor"], users["demo.tutor2"]
    own = f"/api/v1/payroll/tutors/{tutor}"

    with auth_client(migrated_url) as client:
        # demo.finance.
        cookie = signed_in(client, "demo.finance")
        settings = _ok(get(client, cookie, "/api/v1/payroll/settings"))
        assert (settings["payroll_starts_on"], settings["start_locked"]) == (F, True)
        listed = _ok(get(client, cookie, "/api/v1/payroll/tutors", limit=100))
        assert {
            names[t["user_id"]]: (
                t["current_terms"]["pay_frequency"],
                sorted(t["current_rate_kinds"]),
            )
            for t in listed["items"]
        } == {
            "demo.tutor": ("weekly", ["group", "individual"]),
            "demo.tutor2": ("weekly", ["course"]),
        }
        compensation = _ok(get(client, cookie, f"{own}/compensation"))
        assert {r["status"] for r in compensation["rates"]} == {"current"}
        assert {t["status"] for t in compensation["terms"]} == {"current"}
        periods = _periods(client, cookie, names)
        staff = _summary(periods)
        assert staff == PERIODS
        wn = periods[("demo.tutor", WEEKS[2])]
        lines = _ok(get(client, cookie, f"/api/v1/payroll/periods/{wn['id']}/lines"))
        assert sorted(line["kind"] for line in lines["lines"]) == [
            "adjustment",
            "adjustment",
            "pay_item",
            "session",
            "session",
        ]
        adjustments = {
            line["adjustment_kind"]: line
            for line in lines["lines"]
            if line["kind"] == "adjustment"
        }
        assert adjustments["late_approval"]["adjustment_service_date"] == "2026-10-19"
        assert adjustments["manual"]["adjustment_reason"] == MANUAL_REASON
        [bonus] = [line for line in lines["lines"] if line["kind"] == "pay_item"]
        assert bonus["pay_item_description"] == BONUS_ITEM
        w2 = periods[("demo.tutor", WEEKS[1])]
        n1_week = _ok(get(client, cookie, f"/api/v1/payroll/periods/{w2['id']}/lines"))
        assert "no_show" in [line["kind"] for line in n1_week["lines"]]
        unresolved = _ok(get(client, cookie, "/api/v1/payroll/unresolved-work"))
        assert unresolved["items"] == []
        paid = _ok(get(client, cookie, f"/api/v1/payroll/periods/{w2['id']}/payments"))
        assert sorted(
            (
                p["reference"],
                p["amount"],
                None if p["reversal"] is None else p["reversal"]["reason"],
            )
            for p in paid
        ) == [
            (PP2_REFERENCE, "200.00", None),
            (PP3_REFERENCE, "100.00", REVERSAL_REASON),
        ]

        # E10: the payroll side of the reports.
        accrual = _ok(
            get(
                client,
                cookie,
                "/api/v1/reports/finance/profitability",
                basis="accrual",
                **{"from": F},
                to=T,
            )
        )
        rows = accrual["class_rows"]
        assert [(r["class_type"], r["direct_tutor_cost"]) for r in rows] == CLASS_ROWS
        assert accrual["direct_tutor_cost_total"] == ACCRUAL_PAYROLL
        cash = _ok(
            get(
                client,
                cookie,
                "/api/v1/reports/finance/profitability",
                basis="cash",
                **{"from": F},
                to=T,
            )
        )
        assert cash["direct_tutor_cost_total"] == CASH_PAYROLL
        balances = _ok(get(client, cookie, "/api/v1/reports/finance/balances"))
        assert balances["payables"]["payroll_unpaid"] == PAYROLL_UNPAID
        assert balances["payables"]["payroll_periods_unpaid"] == 5
        # #117 D4's payroll identity: Σ finalized − payroll cash = unpaid.
        finalized = sum(
            Decimal(p["total"]) for p in periods.values() if p["status"] == "finalized"
        )
        assert finalized - Decimal(CASH_PAYROLL) == Decimal(PAYROLL_UNPAID)
        assert finalized == Decimal(ACCRUAL_PAYROLL)

        # demo.owner: the same list and totals.
        cookie = signed_in(client, "demo.owner")
        assert _summary(_periods(client, cookie, names)) == staff

        # demo.tutor: its finalized weeks only; the reversed payment without
        # its reason.
        cookie = signed_in(client, "demo.tutor")
        earnings = _ok(get(client, cookie, f"{own}/earnings", limit=100))
        assert sorted(
            (e["starts_on"], e["total"], e["paid_state"], e["payslip"] is not None)
            for e in earnings["items"]
        ) == [
            ("2026-10-12", "70.00", "paid", True),
            ("2026-10-19", "630.00", "partially_paid", True),
            ("2026-10-26", "970.00", "unpaid", True),
        ]
        detail = _ok(get(client, cookie, f"{own}/earnings/{wn['id']}"))
        shown = sorted(
            (
                line["adjustment"]["kind"],
                line["adjustment"]["service_date"],
                line["adjustment"]["reason"],
            )
            for line in detail["lines"]
            if line["adjustment"] is not None
        )
        assert shown == [
            ("late_approval", "2026-10-19", None),
            ("manual", None, MANUAL_REASON),
        ]
        detail = _ok(get(client, cookie, f"{own}/earnings/{w2['id']}"))
        assert sorted(
            (p["reference"], p["reversed_on"]) for p in detail["payments"]
        ) == [(PP2_REFERENCE, None), (PP3_REFERENCE, T)]
        assert all("reason" not in p and "note" not in p for p in detail["payments"])
        other = f"/api/v1/payroll/tutors/{tutor2}/earnings"
        assert get(client, cookie, other).status_code == 404

        # demo.tutor2: its own weeks, all unpaid; demo.tutor's → 404.
        cookie = signed_in(client, "demo.tutor2")
        earnings = _ok(get(client, cookie, f"{other}", limit=100))
        assert sorted(
            (e["starts_on"], e["total"], e["paid_state"]) for e in earnings["items"]
        ) == [
            ("2026-10-12", "2800.00", "unpaid"),
            ("2026-10-19", "3600.00", "unpaid"),
            ("2026-10-26", "3600.00", "unpaid"),
        ]
        assert get(client, cookie, f"{own}/earnings").status_code == 404

        # Everyone else: demo.tutor's pay is invisible; no period list; no
        # reports.
        for username in OTHERS:
            cookie = signed_in(client, username)
            for path in (f"{own}/earnings", f"{own}/compensation"):
                assert get(client, cookie, path).status_code == 404, (username, path)
            response = get(client, cookie, "/api/v1/payroll/periods")
            assert response.status_code == 403, username
            if username == "demo.admin":
                for path in (
                    "/api/v1/reports/finance/balances",
                    f"/api/v1/reports/finance/profitability?basis=cash&from={F}&to={T}",
                ):
                    assert get(client, cookie, path).status_code == 403, path
