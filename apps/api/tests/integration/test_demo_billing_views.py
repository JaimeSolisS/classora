"""What each demo account sees of the M6-demo billing data (E7), through the
API after a whole run on the #299 frozen day."""

from decimal import Decimal
from typing import Any

import pytest

from tests.factories import demo
from tests.factories.demo_billing import PK1_NAME, PK2_NAME, SIBLING_DISCOUNT
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.demo_support import FROZEN_NOW, demo_env, migrate
from tests.integration.test_demo_billing import _families, _labels
from tests.integration.test_demo_schedule import signed_in

FAMILY_READS = (
    "",
    "/charges",
    "/ledger",
    "/packages",
)
"""``/api/v1/families/{id}/billing-account`` and the reads under it (#102)."""


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _account(family_id: Any) -> str:
    return f"/api/v1/families/{family_id}/billing-account"


def _summary(client: Any, cookie: Any, family_id: Any) -> tuple[Any, ...]:
    body = _ok(get(client, cookie, _account(family_id)))
    return (body["outstanding"], body["overdue"], body["net_balance"])


def _packages(client: Any, cookie: Any, family_id: Any) -> list[tuple[Any, ...]]:
    body = _ok(get(client, cookie, f"{_account(family_id)}/packages", limit=100))
    return sorted(
        (p["product"]["name"], p["status"], p["remaining_units"], p["units_granted"])
        for p in body["items"]
    )


PACKAGES = [(PK2_NAME, "expired", 0, 4), (PK1_NAME, "active", 7, 8)]


def test_what_each_account_sees(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    families = {label: family for family, label in _families(migrated_url).items()}
    f1, f2, f3 = families["F1"], families["F2"], families["F3"]
    labels = _labels(migrated_url)
    s = next(sid for sid, label in labels.items() if label == "S")
    [cohort] = run_sql(
        migrated_url,
        "SELECT id FROM course_cohorts WHERE name = 'Álgebra intensiva, grupo 1'",
    )

    with auth_client(migrated_url) as client:
        # demo.finance: settings, prices, discounts, accounts, packages,
        # Unbilled and the tuition plan.
        cookie = signed_in(client, "demo.finance")
        settings = _ok(get(client, cookie, "/api/v1/billing/settings"))
        assert settings["billing_starts_on"] == "2026-10-16"
        prices = _ok(get(client, cookie, "/api/v1/billing/service-prices", limit=100))
        assert prices["total"] == 6
        [pr1] = [
            p
            for p in prices["items"]
            if p["class_type"] == "individual"
            and p["pricing_model"] == "per_session"
            and p["subject"]["name"] == "Matemáticas"
            and p["subject"]["education_level_name"] == "Secundaria"
        ]
        assert (pr1["current"]["amount"], pr1["next"]["amount"]) == ("350.00", "380.00")
        assert pr1["next"]["effective_from"] == "2026-12-01"
        discounts = _ok(get(client, cookie, "/api/v1/billing/discounts", limit=100))
        assert sorted(d["name"] for d in discounts["items"]) == [
            "Beca de apoyo",
            SIBLING_DISCOUNT,
        ]
        staff = {
            label: _summary(client, cookie, family)
            for label, family in families.items()
        }
        assert staff["F3"] == ("1600.00", "1600.00", "1600.00")
        assert Decimal(staff["F1"][1]) > 0
        assert staff["F2"][1] == "0.00"
        assert _packages(client, cookie, f1) == PACKAGES
        unbilled = _ok(
            get(client, cookie, "/api/v1/billing/unbilled-enrollments", limit=100)
        )
        assert (
            sorted(
                (i["kind"], i["reason"], (i["cohort"] or {}).get("id"))
                for i in unbilled["items"]
            )
            == [("cohort_enrollment", "no_price", str(cohort.id))] * 3
        )
        plans = _ok(get(client, cookie, "/api/v1/billing/tuition-plans", limit=100))
        [plan] = plans["items"]
        assert (plan["status"], plan["frequency"], plan["starts_on"]) == (
            "active",
            "monthly",
            "2026-10-16",
        )
        overdue = _ok(get(client, cookie, "/api/v1/billing/overdue-accounts"))
        assert {item["family"]["id"] for item in overdue["items"]} == {
            str(f1),
            str(f3),
        }

        # demo.owner and demo.admin: F1's summary equals demo.finance's.
        for username in ("demo.owner", "demo.admin"):
            cookie = signed_in(client, username)
            assert _summary(client, cookie, f1) == staff["F1"], username

        # demo.guardian: F1 only, the same totals; D1 on a charge; staff-only
        # fields null.
        cookie = signed_in(client, "demo.guardian")
        own = _ok(get(client, cookie, "/api/v1/families/own"))
        assert own["family"]["id"] == str(f1)
        assert _summary(client, cookie, f1) == staff["F1"]
        assert _packages(client, cookie, f1) == PACKAGES
        for path in FAMILY_READS:
            _ok(get(client, cookie, f"{_account(f1)}{path}"))
        charges = _ok(
            get(client, cookie, f"{_account(f1)}/charges", student_id=str(s), limit=100)
        )["items"]
        session = next(c for c in charges if c["kind"] == "session")
        assert session["posted_by_user_id"] is None
        detail = _ok(get(client, cookie, f"{_account(f1)}/charges/{session['id']}"))
        assert [(d["kind"], d["name"], d["percent"]) for d in detail["discounts"]] == [
            ("sibling", SIBLING_DISCOUNT, "10.0000")
        ]
        for family in (f2, f3):
            for path in FAMILY_READS:
                response = get(client, cookie, f"{_account(family)}{path}")
                assert response.status_code == 404, (family, path)

        # Students and staff without billing.view: F1's billing reads 404.
        for username in (
            "demo.student",
            "demo.tutor",
            "demo.tutor2",
            "demo.coordinator",
        ):
            cookie = signed_in(client, username)
            for path in FAMILY_READS:
                response = get(client, cookie, f"{_account(f1)}{path}")
                assert response.status_code == 404, (username, path)
            if username == "demo.student":
                assert _ok(get(client, cookie, "/api/v1/families/own")) == {
                    "family": None
                }
