"""What each demo account sees of the M6-demo-b data (E7): payments and
receipts, the account credit, the cancellation credit, the changed due date,
the reversed payment and the payment reminders, through the API after a
whole run on the #299 frozen day."""

import uuid
from typing import Any

import pytest

from tests.factories import demo
from tests.factories.demo_billing_money import (
    DC_REASON,
    RV_REASON,
    DemoPaymentsGenerator,
    DemoReconciliationGenerator,
    DemoRemindersGenerator,
)
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.demo_support import (
    CHARGES_GENERATOR,
    FROZEN_NOW,
    demo_env,
    generators_through,
    migrate,
)
from tests.integration.test_demo_billing import _families
from tests.integration.test_demo_schedule import signed_in

REMINDER = "billing.payment_reminder"
STAFF = ("demo.finance", "demo.owner", "demo.admin")
NO_BILLING = ("demo.student", "demo.tutor", "demo.tutor2", "demo.coordinator")


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
    return (
        body["outstanding"],
        body["overdue"],
        body["account_credit"],
        body["net_balance"],
    )


def _payments(client: Any, cookie: Any, family_id: Any) -> list[dict[str, Any]]:
    body = _ok(get(client, cookie, f"{_account(family_id)}/payments", limit=100))
    items: list[dict[str, Any]] = body["items"]
    return items


def _notifications(client: Any, cookie: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    for _ in range(20):
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        body = _ok(get(client, cookie, "/api/v1/notifications", **params))
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("too many notification pages")


def _reminders(client: Any, cookie: Any) -> list[tuple[Any, ...]]:
    return sorted(
        (n["params"]["kind"], n["params"]["family_id"], n["params"]["count"])
        for n in _notifications(client, cookie)
        if n["notification_type"] == REMINDER
    )


def test_what_each_account_sees(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = [
        *generators_through(CHARGES_GENERATOR),
        DemoReconciliationGenerator(),
        payments := DemoPaymentsGenerator(),
        reminders := DemoRemindersGenerator(),
    ]
    assert demo.main([], generators=generators) == 0
    recorded = payments.recorded
    assert recorded is not None
    assert reminders.dc is not None
    families = {label: family for family, label in _families(migrated_url).items()}
    f1, f2, f3 = families["F1"], families["F2"], families["F3"]
    [cx] = run_sql(
        migrated_url,
        "SELECT c.id FROM charges c JOIN charge_adjustments a ON a.charge_id = c.id"
        " WHERE a.kind = 'cancellation_credit'",
    )
    receipts = {
        r.payment_id: (str(r.id), r.number)
        for r in run_sql(migrated_url, "SELECT id, payment_id, number FROM receipts")
    }

    with auth_client(migrated_url) as client:
        # demo.finance.
        cookie = signed_in(client, "demo.finance")
        listed = _payments(client, cookie, f1)
        assert [p["id"] for p in listed] == [str(recorded.py2), str(recorded.py1)]
        for p in listed:
            receipt = receipts[uuid.UUID(p["id"])]
            assert (p["receipt"]["id"], p["receipt"]["number"]) == receipt
            assert p["recorded_by_user_id"] is not None
            assert "note" in p
        assert [(p["amount"], p["unallocated_amount"]) for p in listed] == [
            ("500.00", "500.00"),
            ("1500.00", "0.00"),
        ]
        staff = _summary(client, cookie, f1)
        assert staff == ("11271.00", "4077.00", "500.00", "10771.00")
        detail = _ok(get(client, cookie, f"{_account(f1)}/charges/{cx.id}"))
        assert [
            (a["kind"], a["amount"], a["reason"]) for a in detail["adjustments"]
        ] == [
            ("cancellation_credit", "-315.00", None)  # a job's credit has no reason
        ]
        assert (detail["net"], detail["open"]) == ("0.00", "0.00")
        changes = _ok(
            get(
                client,
                cookie,
                f"{_account(f1)}/charges/{reminders.dc}/due-date-changes",
            )
        )
        assert [(c["due_date"], c["reason"]) for c in changes] == [
            ("2026-11-06", DC_REASON)
        ]
        overdue = _ok(get(client, cookie, "/api/v1/billing/overdue-accounts"))
        assert sorted(
            (i["family"]["id"], i["overdue_total"]) for i in overdue["items"]
        ) == sorted([(str(f1), "4077.00"), (str(f2), "250.00"), (str(f3), "1600.00")])
        account = _ok(get(client, cookie, _account(f2)))
        assert account["due_offset_days"] == 5
        f2_staff = _summary(client, cookie, f2)
        assert f2_staff == ("2639.50", "250.00", "0.00", "2639.50")
        mc = _ok(get(client, cookie, f"{_account(f2)}/charges/{recorded.mc}"))
        assert (mc["kind"], mc["open"], mc["status"], mc["due_date"]) == (
            "manual",
            "250.00",
            "overdue",
            "2026-11-03",
        )
        py3 = _ok(get(client, cookie, f"{_account(f2)}/payments/{recorded.py3}"))
        assert py3["is_reversed"] is True
        assert py3["reversal"]["reason"] == RV_REASON
        assert [a["is_valid"] for a in py3["allocations"]] == [False]
        assert _reminders(client, cookie) == []

        # demo.owner and demo.admin: the same summaries.
        for username in STAFF[1:]:
            cookie = signed_in(client, username)
            assert _summary(client, cookie, f1) == staff, username
            assert _summary(client, cookie, f2) == f2_staff, username
            assert _reminders(client, cookie) == [], username

        # demo.guardian: F1 only, the staff-only fields null.
        cookie = signed_in(client, "demo.guardian")
        assert _ok(get(client, cookie, "/api/v1/families/own"))["family"]["id"] == str(
            f1
        )
        own = _payments(client, cookie, f1)
        assert [p["id"] for p in own] == [str(recorded.py2), str(recorded.py1)]
        for p in own:
            assert (p["receipt"]["id"], p["receipt"]["number"]) == receipts[
                uuid.UUID(p["id"])
            ]
            assert (p["note"], p["recorded_by_user_id"]) == (None, None)
        py1 = _ok(get(client, cookie, f"{_account(f1)}/payments/{recorded.py1}"))
        assert len(py1["allocations"]) == 3
        assert {a["created_by_user_id"] for a in py1["allocations"]} == {None}
        assert _summary(client, cookie, f1) == staff
        charges = _ok(get(client, cookie, f"{_account(f1)}/charges", limit=100))
        [dc] = [c for c in charges["items"] if c["id"] == str(reminders.dc)]
        assert (dc["due_date"], dc["original_due_date"]) == ("2026-11-06", "2026-10-26")
        detail = _ok(get(client, cookie, f"{_account(f1)}/charges/{cx.id}"))
        assert [(a["kind"], a["reason"]) for a in detail["adjustments"]] == [
            ("cancellation_credit", None)
        ]
        assert _reminders(client, cookie) == [
            ("due_soon", str(f1), 4),
            ("overdue", str(f1), 6),
        ]
        py3_receipt = receipts[recorded.py3][0]
        for path in (
            f"{_account(f2)}/payments",
            f"{_account(f2)}/payments/{recorded.py3}",
            f"{_account(f2)}/receipts/{py3_receipt}/pdf",
            f"{_account(f1)}/receipts/{py3_receipt}/pdf",
        ):
            assert get(client, cookie, path).status_code == 404, path

        # Students and staff without billing.view.
        py1_receipt = receipts[recorded.py1][0]
        for username in NO_BILLING:
            cookie = signed_in(client, username)
            for path in (
                f"{_account(f1)}/payments",
                f"{_account(f1)}/payments/{recorded.py1}",
                f"{_account(f1)}/receipts/{py1_receipt}/pdf",
            ):
                response = get(client, cookie, path)
                assert response.status_code == 404, (username, path)
            assert _reminders(client, cookie) == [], username
