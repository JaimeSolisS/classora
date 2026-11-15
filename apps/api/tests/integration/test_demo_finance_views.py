"""What each demo account sees of the M7-demo-b data (#501 F8, B1, C1): the
expenses, a reversed payment, a generated expense, the recurring templates,
the categories and vendors, and the cohort's price, through the API after a
whole run on the #299 frozen day."""

from collections import Counter
from typing import Any

import pytest

from tests.factories import demo
from tests.factories.demo_academics import COHORT_NAME
from tests.factories.demo_finance import (
    FURNITURE,
    STATIONERY,
    TL_NAME,
    VENDORS,
    X5_REVERSAL_REASON,
)
from tests.integration.auth_support import auth_client, cookie_header
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.demo_support import FROZEN_NOW, demo_env, migrate
from tests.integration.test_demo_schedule import signed_in

STATUSES = {"unpaid": 4, "overdue": 3, "partially_paid": 1, "paid": 8, "void": 2}
"""The 18 expenses at ``FROZEN_NOW`` (#501 "Expected figures")."""
TEMPLATES = {
    "Renta del local": ("active", "2026-12-01"),
    "Internet y telefonía": ("active", "2026-11-30"),
    "Limpieza semanal": ("paused", "2026-11-09"),
    "Impresión de volantes de inscripción": ("ended", "2026-10-26"),
    "Mantenimiento anual de climas": ("active", "2027-10-12"),
}
"""D1: status and ``next_period_start`` (TI clamped to 11-30)."""
CATEGORIES = {
    "Renta": ("rent", True),
    "Servicios": ("utilities", True),
    STATIONERY: (None, True),
    "Equipo": ("equipment", True),
    "Mantenimiento": ("maintenance", True),
    FURNITURE: (None, False),
}
FINANCE_LISTS = (
    "/api/v1/finance/expenses",
    "/api/v1/finance/expense-templates",
    "/api/v1/finance/expense-categories",
    "/api/v1/finance/vendors",
)
NO_FINANCE = (
    "demo.coordinator",
    "demo.tutor",
    "demo.tutor2",
    "demo.student",
    "demo.guardian",
)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _categories(client: Any, cookie: Any, language: str) -> dict[str, Any]:
    client.cookies.clear()
    response = client.get(
        "/api/v1/finance/expense-categories",
        headers={**cookie_header(cookie), "Accept-Language": language},
    )
    return {c["name"]: (c["system_key"], c["is_active"]) for c in _ok(response)}


def _vendors(client: Any, cookie: Any) -> dict[str, bool]:
    body = _ok(get(client, cookie, "/api/v1/finance/vendors", limit=100))
    assert body["total"] == len(VENDORS)
    return {v["name"]: v["is_active"] for v in body["items"]}


def _expenses(client: Any, cookie: Any, **params: Any) -> list[dict[str, Any]]:
    body = _ok(get(client, cookie, "/api/v1/finance/expenses", limit=100, **params))
    assert body["total"] == len(body["items"])
    return list(body["items"])


def _staff_view(client: Any, cookie: Any) -> dict[str, Any]:
    """What ``demo.finance`` and ``demo.owner`` see (the same)."""
    items = _expenses(client, cookie)
    assert len(items) == 18
    assert Counter(x["status"] for x in items) == STATUSES

    # X5: two payments, the first reversed with its reason.
    [x5] = [
        x for x in items if x["description"] == "Instalación de punto de acceso WiFi"
    ]
    detail = _ok(get(client, cookie, f"/api/v1/finance/expenses/{x5['id']}"))
    payments = sorted(detail["payments"], key=lambda p: p["paid_on"])
    assert [
        (
            p["paid_on"],
            p["reference"],
            None if p["reversal"] is None else p["reversal"]["reason"],
            None
            if p["reversal"] is None
            else p["reversal"]["reversed_by"]["display_name"],
        )
        for p in payments
    ] == [
        ("2026-10-20", "COL-DEMO-0001", X5_REVERSAL_REASON, "Demo Owner"),
        ("2026-10-22", "COL-DEMO-0001", None, None),
    ]
    assert detail["status"] == "paid"

    # A generated expense: no creator, its template.
    templates = _ok(get(client, cookie, "/api/v1/finance/expense-templates"))
    assert {t["name"]: (t["status"], t["next_period_start"]) for t in templates} == (
        TEMPLATES
    )
    [tl] = [t for t in templates if t["name"] == TL_NAME]
    generated = _expenses(client, cookie, template_id=tl["id"])
    assert len(generated) == 4
    assert {x["template"]["name"] for x in generated} == {TL_NAME}
    first = _ok(get(client, cookie, f"/api/v1/finance/expenses/{generated[0]['id']}"))
    assert first["created_by"] is None
    assert first["template"] == {"id": tl["id"], "name": TL_NAME}

    categories = _categories(client, cookie, "es")
    assert categories == CATEGORIES
    assert _categories(client, cookie, "en") == CATEGORIES  # C1: es and en
    vendors = _vendors(client, cookie)
    assert vendors == {v.name: v.key != "V4" for v in VENDORS}
    return {"categories": categories, "vendors": vendors}


def test_what_each_account_sees(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    [cohort] = run_sql(
        migrated_url,
        "SELECT id FROM course_cohorts WHERE name = :n",
        {"n": COHORT_NAME},
    )

    with auth_client(migrated_url) as client:
        # demo.finance.
        cookie = signed_in(client, "demo.finance")
        seen = _staff_view(client, cookie)
        # B1: seven catalog items, PR7 scoped to #353's cohort.
        prices = _ok(get(client, cookie, "/api/v1/billing/service-prices", limit=100))
        assert prices["total"] == 7
        assert [
            (p["class_type"], p["pricing_model"], p["current"]["amount"])
            for p in prices["items"]
            if p["cohort"] is not None and p["cohort"]["id"] == str(cohort.id)
        ] == [("course", "full_course", "1800.00")]

        # B2, E1: Unbilled is empty once the finished cohort is priced.
        unbilled = _ok(
            get(client, cookie, "/api/v1/billing/unbilled-enrollments", limit=100)
        )
        assert unbilled["items"] == []

        # demo.owner: the same.
        cookie = signed_in(client, "demo.owner")
        assert _staff_view(client, cookie) == seen

        # demo.admin: categories and vendors (expense.configure) only.
        cookie = signed_in(client, "demo.admin")
        assert _categories(client, cookie, "es") == CATEGORIES
        assert _vendors(client, cookie) == seen["vendors"]
        for path in FINANCE_LISTS[:2]:
            assert get(client, cookie, path).status_code == 403, path

        # Everyone else: no finance list.
        for username in NO_FINANCE:
            cookie = signed_in(client, username)
            for path in FINANCE_LISTS:
                assert get(client, cookie, path).status_code == 403, (username, path)
